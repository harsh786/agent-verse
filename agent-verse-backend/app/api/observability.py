"""Observability API — real-time logs, SSE log stream, and structured metrics."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from app.tenancy.context import TenantContext

router = APIRouter(prefix="/observability", tags=["observability"])

_obs_log = logging.getLogger("observability")


def _require_tenant(request: Request) -> TenantContext:
    ctx: TenantContext | None = getattr(request.state, "tenant", None)
    if ctx is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="Unauthorized")
    return ctx


# ── Structured log store ──────────────────────────────────────────────────────


class StructuredLogStore:
    """Redis Streams-backed structured log store.

    Falls back to an in-memory ring buffer when Redis is unavailable.
    Supports both push (emit) and pull (query / stream) APIs.

    Redis key layout: ``av:logs:{tenant_id}``  (one Stream per tenant).
    MAXLEN is approximate (``~`` trimming) to keep overhead low.
    """

    STREAM_KEY = "av:logs:{tenant_id}"
    MAX_STREAM_LEN = 10_000  # entries per tenant stream

    def __init__(self) -> None:
        self._redis: Any = None
        # In-memory ring buffer: tenant_id → list[dict]
        self._memory_buffer: dict[str, list[dict[str, Any]]] = {}
        self._MAX_MEMORY = 500

    def set_redis(self, redis: Any) -> None:
        """Wire a real (or fake) Redis client. Called from main.py lifespan."""
        self._redis = redis

    async def emit(
        self,
        tenant_id: str,
        level: str,
        message: str,
        source: str = "",
        goal_id: str = "",
        **kwargs: Any,
    ) -> None:
        """Write a structured log entry. Called from goal_service, agent loop, etc."""
        ts = int(time.time() * 1000)
        entry: dict[str, Any] = {
            "id": f"{ts}-0",
            "timestamp": datetime.now(UTC).isoformat(),
            "level": level.lower(),
            "message": message,
            "source": source,
            "goal_id": goal_id,
            **{k: str(v) for k, v in kwargs.items()},
        }

        if self._redis is not None:
            try:
                key = self.STREAM_KEY.format(tenant_id=tenant_id)
                # Redis Streams require string values; drop empty strings to save space
                fields = {k: v for k, v in entry.items() if v}
                await self._redis.xadd(
                    key, fields, maxlen=self.MAX_STREAM_LEN, approximate=True
                )
                return
            except Exception as exc:
                _obs_log.debug("Redis log emit failed: %s", exc)

        # In-memory fallback
        buf = self._memory_buffer.setdefault(tenant_id, [])
        buf.append(entry)
        if len(buf) > self._MAX_MEMORY:
            self._memory_buffer[tenant_id] = buf[-self._MAX_MEMORY :]

    async def query(
        self,
        tenant_id: str,
        limit: int = 50,
        level: str | None = None,
        since_ts: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch log entries for a tenant (newest-first)."""
        if self._redis is not None:
            try:
                key = self.STREAM_KEY.format(tenant_id=tenant_id)
                # xrevrange returns newest-first; fetch 2x so level filter still yields `limit`
                raw = await self._redis.xrevrange(key, count=limit * 2)
                logs: list[dict[str, Any]] = []
                for _stream_id, fields in raw:
                    if len(logs) >= limit:
                        break
                    log = {
                        (k.decode() if isinstance(k, bytes) else k): (
                            v.decode() if isinstance(v, bytes) else v
                        )
                        for k, v in fields.items()
                    }
                    if level and log.get("level") != level.lower():
                        continue
                    logs.append(log)
                return logs
            except Exception as exc:
                _obs_log.debug("Redis log query failed: %s", exc)

        # In-memory fallback
        buf = list(reversed(self._memory_buffer.get(tenant_id, [])))
        if level:
            buf = [e for e in buf if e.get("level") == level.lower()]
        return buf[:limit]

    async def stream_new_since(
        self, tenant_id: str, last_id: str = "$"
    ) -> list[dict[str, Any]]:
        """Non-blocking read of entries newer than *last_id* (for SSE).

        Uses ``XREAD BLOCK 2000`` so the coroutine yields control every 2 s
        at most, allowing ``is_disconnected`` checks to run between polls.
        Returns an empty list when no entries arrive within the block window
        or when Redis is unavailable.
        """
        if self._redis is not None:
            try:
                key = self.STREAM_KEY.format(tenant_id=tenant_id)
                # block=2000 ms — returns None (not []) on timeout in some clients
                raw = await self._redis.xread({key: last_id}, count=50, block=2000)
                logs: list[dict[str, Any]] = []
                if raw:
                    for _key, msgs in raw:
                        for stream_id, fields in msgs:
                            log = {
                                (k.decode() if isinstance(k, bytes) else k): (
                                    v.decode() if isinstance(v, bytes) else v
                                )
                                for k, v in fields.items()
                            }
                            log["_stream_id"] = (
                                stream_id.decode()
                                if isinstance(stream_id, bytes)
                                else stream_id
                            )
                            logs.append(log)
                return logs
            except Exception as exc:
                _obs_log.debug("Redis log stream failed: %s", exc)
        return []


# Module-level singleton — wired to Redis in main.py lifespan
log_store = StructuredLogStore()


# ── Log listing ───────────────────────────────────────────────────────────────


@router.get("/logs")
async def list_logs(
    request: Request,
    limit: int = Query(default=50, le=500),
    level: str | None = Query(default=None),
    since: str | None = Query(default=None),
) -> dict[str, Any]:
    """Return recent log entries from the Redis Streams store (falls back to goal events)."""
    tenant = _require_tenant(request)

    # ── Primary path: Redis-backed structured log store ───────────────────────
    logs: list[dict[str, Any]] = await log_store.query(
        tenant.tenant_id, limit=limit, level=level, since_ts=since
    )
    source = "redis_stream" if log_store._redis is not None else "memory"

    # ── Legacy fallback: derive from goal events when store is empty ──────────
    if not logs:
        goal_svc = getattr(request.app.state, "goal_service", None)
        if goal_svc is not None:
            try:
                resp = await goal_svc.list_goals(tenant_ctx=tenant)
                goals = resp.get("goals", []) if isinstance(resp, dict) else (resp or [])

                for goal in goals[:20]:  # last 20 goals
                    goal_id = goal.get("id") or goal.get("goal_id", "")
                    try:
                        events = await goal_svc.get_events(
                            goal_id=goal_id, tenant_ctx=tenant
                        )
                        for evt in events[-10:]:  # last 10 events per goal
                            evt_type = evt.get("type", "")
                            level_val = (
                                "error"
                                if "fail" in evt_type
                                else ("warning" if "cancel" in evt_type else "info")
                            )
                            if level and level_val != level:
                                continue
                            logs.append(
                                {
                                    "id": f"{goal_id}_{evt.get('ts', '')}",
                                    "timestamp": evt.get("ts")
                                    or datetime.now(UTC).isoformat(),
                                    "level": level_val,
                                    "message": _evt_to_message(evt),
                                    "source": evt_type,
                                    "goal_id": goal_id,
                                }
                            )
                    except Exception:
                        pass
            except Exception:
                pass
        source = "goal_events"

    # ── since filter ──────────────────────────────────────────────────────────
    if since:
        try:
            since_dt = datetime.fromisoformat(since.rstrip("Z")).replace(tzinfo=UTC)
            logs = [
                log
                for log in logs
                if datetime.fromisoformat(
                    log.get("timestamp", "").rstrip("Z")
                ).replace(tzinfo=UTC)
                >= since_dt
            ]
        except Exception:
            pass

    logs.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return {"logs": logs[:limit], "total": len(logs), "source": source}


def _evt_to_message(evt: dict[str, Any]) -> str:
    etype = evt.get("type", "unknown")
    messages: dict[str, str] = {
        "goal_complete": "Goal completed successfully",
        "goal_failed": f"Goal failed: {evt.get('reason', '')}",
        "goal_cancelled": "Goal was cancelled",
        "plan_ready": f"Plan ready with {len(evt.get('steps', []))} steps",
        "step_started": f"Started step: {str(evt.get('step', ''))[:80]}",
        "step_complete": f"Completed step: {str(evt.get('step', ''))[:60]}",
        "tool_call_complete": f"Tool call: {evt.get('tool_name', evt.get('name', 'unknown'))}",
        "tool_call_failed": (
            f"Tool failed: {evt.get('tool_name', 'unknown')} — {evt.get('error', '')}"
        ),
        "goal_started": "Goal execution started",
        "approval_required": "Human approval required",
        "approval_granted": "Human approval granted",
    }
    return messages.get(etype, f"Event: {etype}")


# ── SSE log stream ────────────────────────────────────────────────────────────


@router.get("/logs/stream")
async def stream_logs(request: Request) -> StreamingResponse:
    """SSE stream of real-time log entries via Redis XREAD (event-driven)."""
    tenant = _require_tenant(request)

    async def event_generator() -> Any:
        yield 'data: {"type": "connected"}\n\n'

        # Send the 20 most-recent historical entries first
        recent = await log_store.query(tenant.tenant_id, limit=20)
        for log_entry in reversed(recent):  # oldest-first for the initial burst
            if await request.is_disconnected():
                return
            yield f"data: {json.dumps(log_entry)}\n\n"

        # Stream new entries via Redis XREAD (truly event-driven)
        last_id = "$"  # read only entries that arrive *after* the initial snapshot

        while True:
            if await request.is_disconnected():
                break

            if log_store._redis is not None:
                # stream_new_since blocks up to 2 s then returns (empty on timeout)
                new_logs = await log_store.stream_new_since(tenant.tenant_id, last_id)
                for log_entry in new_logs:
                    if await request.is_disconnected():
                        return
                    last_id = log_entry.pop("_stream_id", last_id)
                    yield f"data: {json.dumps(log_entry)}\n\n"
                # No heartbeat needed when Redis is available — XREAD itself polls
            else:
                # No Redis: poll goal events and emit heartbeat with backoff
                goal_svc = getattr(request.app.state, "goal_service", None)
                if goal_svc is not None:
                    # Reuse the in-memory log buffer (populated via emit() calls)
                    new_logs = await log_store.stream_new_since(tenant.tenant_id, last_id)
                    for log_entry in new_logs:
                        if await request.is_disconnected():
                            return
                        last_id = log_entry.pop("_stream_id", last_id)
                        yield f"data: {json.dumps(log_entry)}\n\n"

                yield 'data: {"type": "heartbeat"}\n\n'
                await asyncio.sleep(5)

    from app.core.config import get_settings as _get_settings

    _settings = _get_settings()
    cors_origin = _settings.cors_origins[0] if _settings.cors_origins else "*"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": cors_origin,
            "X-Accel-Buffering": "no",
        },
    )


# ── Structured metrics ────────────────────────────────────────────────────────


@router.get("/metrics")
async def get_structured_metrics(request: Request) -> dict[str, Any]:
    """Return structured observability metrics including DB-backed latency percentiles."""
    tenant = _require_tenant(request)
    goal_svc = getattr(request.app.state, "goal_service", None)

    result: dict[str, Any] = {
        "latency_percentiles": {"p50": 0, "p95": 0, "p99": 0},
        "goal_duration_percentiles": [],
        "token_usage_by_provider": [],
        "success_rate": 0.0,
        "total_goals": 0,
    }

    if goal_svc is None:
        return result

    # ── Primary path: DB-backed percentiles (accurate across replicas) ─────────
    db = getattr(goal_svc, "_db", None)
    if db is not None:
        try:
            from sqlalchemy import text as _t

            async with db() as session:
                await session.execute(
                    _t("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant.tenant_id}
                )
                row = (
                    await session.execute(
                        _t("""
                            SELECT
                                COUNT(*) AS total,
                                SUM(CASE WHEN status = 'complete' THEN 1 ELSE 0 END)::float
                                    / NULLIF(COUNT(*), 0) AS success_rate,
                                PERCENTILE_CONT(0.50) WITHIN GROUP (
                                    ORDER BY COALESCE(duration_s, 0)
                                ) * 1000 AS p50_ms,
                                PERCENTILE_CONT(0.95) WITHIN GROUP (
                                    ORDER BY COALESCE(duration_s, 0)
                                ) * 1000 AS p95_ms,
                                PERCENTILE_CONT(0.99) WITHIN GROUP (
                                    ORDER BY COALESCE(duration_s, 0)
                                ) * 1000 AS p99_ms
                            FROM goals
                            WHERE tenant_id = :tid
                              AND created_at >= NOW() - INTERVAL '24 hours'
                        """),
                        {"tid": tenant.tenant_id},
                    )
                ).fetchone()

                if row and row[0]:
                    result["total_goals"] = int(row[0])
                    result["success_rate"] = round(float(row[1] or 0), 3)
                    p50 = round(float(row[2] or 0))
                    p95 = round(float(row[3] or 0))
                    p99 = round(float(row[4] or 0))
                    result["latency_percentiles"] = {"p50": p50, "p95": p95, "p99": p99}
                    result["goal_duration_percentiles"] = [
                        {"percentile": "p50", "ms": p50},
                        {"percentile": "p95", "ms": p95},
                        {"percentile": "p99", "ms": p99},
                    ]
        except Exception:
            pass

    # ── Fallback: in-memory durations (single-replica, lost on restart) ────────
    if result["total_goals"] == 0:
        try:
            metrics_data = await goal_svc.get_metrics(tenant_ctx=tenant)
            result["total_goals"] = metrics_data.get("total_goals", 0)
            result["success_rate"] = metrics_data.get("success_rate", 0.0)

            durations: list[float] = getattr(goal_svc, "_goal_durations", {}).get(
                tenant.tenant_id, []
            )
            if durations:
                sorted_d = sorted(durations)
                n = len(sorted_d)
                p50 = round(sorted_d[int(n * 0.5)] * 1000)
                p95 = round(sorted_d[min(int(n * 0.95), n - 1)] * 1000)
                p99 = round(sorted_d[min(int(n * 0.99), n - 1)] * 1000)
                result["latency_percentiles"] = {"p50": p50, "p95": p95, "p99": p99}
                result["goal_duration_percentiles"] = [
                    {"percentile": "p50", "ms": p50},
                    {"percentile": "p95", "ms": p95},
                    {"percentile": "p99", "ms": p99},
                ]
        except Exception:
            pass

    # ── Token usage by provider (DB query, unchanged) ─────────────────────────
    if not result["token_usage_by_provider"] and db is not None:
        try:
            from sqlalchemy import text as _t

            async with db() as session:
                rows = (
                    await session.execute(
                        _t("""
                            SELECT
                                COALESCE(
                                    execution_context->>'provider', 'unknown'
                                ) AS provider,
                                SUM(COALESCE(
                                    (execution_context->>'tokens_used')::integer, 0
                                )) AS tokens
                            FROM goals
                            WHERE tenant_id = :tid
                              AND created_at >= NOW() - INTERVAL '30 days'
                            GROUP BY provider
                            ORDER BY tokens DESC
                            LIMIT 10
                        """),
                        {"tid": tenant.tenant_id},
                    )
                ).fetchall()
                if rows:
                    result["token_usage_by_provider"] = [
                        {"label": r[0], "value": int(r[1] or 0)} for r in rows
                    ]
        except Exception:
            pass

    # Fetch token usage from in-memory metrics when DB is absent
    if not result["token_usage_by_provider"]:
        try:
            token_by_provider = (
                getattr(goal_svc, "_metrics_cache", {})
                .get(tenant.tenant_id, {})
                .get("token_usage_by_provider", [])
            )
            if token_by_provider:
                result["token_usage_by_provider"] = token_by_provider
        except Exception:
            pass

    return result


# ── Time-series ───────────────────────────────────────────────────────────────


@router.get("/timeseries")
async def get_timeseries(
    request: Request,
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    bucket: str = Query(default="hour", pattern="^(minute|hour|day)$"),
) -> dict[str, Any]:
    """Return time-series data for goals, cost, and latency bucketed by time."""
    tenant = _require_tenant(request)
    goal_svc = getattr(request.app.state, "goal_service", None)

    result: dict[str, Any] = {
        "goals_per_hour": [],
        "cost_per_hour": [],
        "avg_latency_per_hour": [],
    }

    if goal_svc is None:
        return result

    db = getattr(goal_svc, "_db", None)
    if db is None:
        # In-memory mode: synthesise from in-memory goal records
        import datetime as _dt

        now = _dt.datetime.now(_dt.UTC)
        buckets: dict[str, dict[str, Any]] = {}

        for record in goal_svc._goals.values():
            if record.tenant_id != tenant.tenant_id:
                continue
            try:
                created = _dt.datetime.fromisoformat(
                    record.created_at.rstrip("Z")
                ).replace(tzinfo=_dt.UTC)
            except Exception:
                continue

            if since:
                try:
                    since_dt = _dt.datetime.fromisoformat(since.rstrip("Z")).replace(
                        tzinfo=_dt.UTC
                    )
                    if created < since_dt:
                        continue
                except Exception:
                    pass

            if until:
                try:
                    until_dt = _dt.datetime.fromisoformat(until.rstrip("Z")).replace(
                        tzinfo=_dt.UTC
                    )
                    if created > until_dt:
                        continue
                except Exception:
                    pass

            # Bucket key depends on granularity
            if bucket == "minute":
                bucket_key = created.strftime("%Y-%m-%dT%H:%M:00Z")
            elif bucket == "day":
                bucket_key = created.strftime("%Y-%m-%dT00:00:00Z")
            else:
                bucket_key = created.strftime("%Y-%m-%dT%H:00:00Z")

            if bucket_key not in buckets:
                buckets[bucket_key] = {
                    "count": 0,
                    "success": 0,
                    "failed": 0,
                    "cost": 0.0,
                }

            buckets[bucket_key]["count"] += 1
            status_val = getattr(getattr(record, "status", None), "value", None) or str(
                getattr(record, "status", "")
            )
            if status_val == "complete":
                buckets[bucket_key]["success"] += 1
            elif status_val == "failed":
                buckets[bucket_key]["failed"] += 1
            cost = getattr(record, "cost_usd", 0.0) or 0.0
            buckets[bucket_key]["cost"] += float(cost)

        for ts, data in sorted(buckets.items()):
            result["goals_per_hour"].append(
                {
                    "ts": ts,
                    "count": data["count"],
                    "success": data["success"],
                    "failed": data["failed"],
                }
            )
            result["cost_per_hour"].append(
                {"ts": ts, "cost_usd": round(data["cost"], 4)}
            )

        return result

    # DB-backed path
    try:
        import datetime as _dt

        from sqlalchemy import text as _t

        trunc = {"minute": "minute", "hour": "hour", "day": "day"}.get(bucket, "hour")
        now = _dt.datetime.now(_dt.UTC)
        since_dt = (
            _dt.datetime.fromisoformat(since.rstrip("Z")).replace(tzinfo=_dt.UTC)
            if since
            else now - _dt.timedelta(hours=24)
        )
        until_dt = (
            _dt.datetime.fromisoformat(until.rstrip("Z")).replace(tzinfo=_dt.UTC)
            if until
            else now
        )

        async with db() as session:
            await session.execute(
                _t("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant.tenant_id}
            )

            # Goals per time bucket
            goals_rows = (
                await session.execute(
                    _t(f"""
                        SELECT
                            date_trunc('{trunc}', created_at) AS bucket,
                            COUNT(*) AS total,
                            SUM(CASE WHEN status = 'complete' THEN 1 ELSE 0 END) AS success,
                            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
                        FROM goals
                        WHERE tenant_id = :tid
                          AND created_at BETWEEN :since AND :until
                        GROUP BY bucket
                        ORDER BY bucket
                    """),
                    {"tid": tenant.tenant_id, "since": since_dt, "until": until_dt},
                )
            ).fetchall()

            result["goals_per_hour"] = [
                {
                    "ts": r[0].isoformat(),
                    "count": int(r[1]),
                    "success": int(r[2] or 0),
                    "failed": int(r[3] or 0),
                }
                for r in goals_rows
            ]

            # Cost per time bucket
            cost_rows = (
                await session.execute(
                    _t(f"""
                        SELECT
                            date_trunc('{trunc}', created_at) AS bucket,
                            SUM(COALESCE(cost_usd, 0)) AS total_cost
                        FROM goals
                        WHERE tenant_id = :tid
                          AND created_at BETWEEN :since AND :until
                          AND cost_usd IS NOT NULL
                        GROUP BY bucket
                        ORDER BY bucket
                    """),
                    {"tid": tenant.tenant_id, "since": since_dt, "until": until_dt},
                )
            ).fetchall()

            result["cost_per_hour"] = [
                {"ts": r[0].isoformat(), "cost_usd": round(float(r[1] or 0), 4)}
                for r in cost_rows
            ]

            # Latency (p50 / p95) per time bucket
            lat_rows = (
                await session.execute(
                    _t(f"""
                        SELECT
                            date_trunc('{trunc}', created_at) AS bucket,
                            PERCENTILE_CONT(0.5) WITHIN GROUP (
                                ORDER BY COALESCE(duration_s, 0)
                            ) * 1000 AS p50_ms,
                            PERCENTILE_CONT(0.95) WITHIN GROUP (
                                ORDER BY COALESCE(duration_s, 0)
                            ) * 1000 AS p95_ms
                        FROM goals
                        WHERE tenant_id = :tid
                          AND created_at BETWEEN :since AND :until
                          AND duration_s IS NOT NULL
                        GROUP BY bucket
                        ORDER BY bucket
                    """),
                    {"tid": tenant.tenant_id, "since": since_dt, "until": until_dt},
                )
            ).fetchall()

            result["avg_latency_per_hour"] = [
                {
                    "ts": r[0].isoformat(),
                    "p50_ms": round(float(r[1] or 0)),
                    "p95_ms": round(float(r[2] or 0)),
                }
                for r in lat_rows
            ]
    except Exception:
        pass  # Return empty arrays on any error

    return result
