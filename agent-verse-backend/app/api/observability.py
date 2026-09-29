"""Observability API — tenant logs (list + SSE), structured metrics, time-series,
and the per-goal run trace.

Fail-closed contract: nothing here fabricates data.

* ``/logs`` serves the tenant log store fed by the structlog pipeline
  (``app/observability/logging.py::feed_tenant_log_store``); a store failure is a
  503, never an empty list.
* ``/metrics`` and ``/timeseries`` are computed in Postgres, under the tenant's
  RLS context, from real columns: goal duration is ``completed_at - created_at``
  and cost/tokens come from the durable ``goal_cost_breakdowns`` ledger (``goals``
  has no duration or cost column). No database -> 501; database error -> 503.
* ``/goals/{id}/trace`` is served from the process-local span timeline only where
  that timeline is authoritative (single process, goals executed in-process);
  otherwise 501 rather than an empty or partial trace.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, extract, func, select
from sqlalchemy.sql.elements import ColumnElement

from app.db.models.goal import Goal
from app.db.models.runtime_records import GoalCostBreakdownRow
from app.observability.log_store import StructuredLogStore, log_store
from app.tenancy.context import TenantContext

__all__ = ["StructuredLogStore", "log_store", "router"]

router = APIRouter(prefix="/observability", tags=["observability"])

_obs_log = logging.getLogger("observability")

_goals = Goal.__table__
_costs = GoalCostBreakdownRow.__table__

_BUCKETS = frozenset({"minute", "hour", "day"})
_TERMINAL = ("complete", "failed", "cancelled")


def _require_tenant(request: Request) -> TenantContext:
    ctx: TenantContext | None = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return ctx


def _parse_ts(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"'{name}' must be an ISO-8601 timestamp"
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# ── Log listing ───────────────────────────────────────────────────────────────


@router.get("/logs")
async def list_logs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    level: str | None = Query(default=None),
    since: str | None = Query(default=None),
) -> dict[str, Any]:
    """Recent tenant log entries (newest first) from the tenant log store.

    ``source`` is ``redis_stream`` (shared across replicas) or ``memory``
    (process-local: only this replica's lines).
    """
    tenant = _require_tenant(request)
    since_dt = _parse_ts(since, "since")
    try:
        logs = await log_store.query(tenant.tenant_id, limit=limit, level=level)
    except Exception as exc:
        _obs_log.warning("observability_log_query_failed: %s", exc)
        raise HTTPException(status_code=503, detail="Log store unavailable") from exc

    if since_dt is not None:
        kept: list[dict[str, Any]] = []
        for log in logs:
            ts = _entry_ts(log)
            if ts is not None and ts >= since_dt:
                kept.append(log)
        logs = kept

    logs.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
    return {"logs": logs[:limit], "total": len(logs), "source": log_store.backend}


def _entry_ts(log: dict[str, Any]) -> datetime | None:
    try:
        ts = datetime.fromisoformat(str(log.get("timestamp", "")).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


# ── SSE log stream ────────────────────────────────────────────────────────────


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@router.get("/logs/stream")
async def stream_logs(request: Request) -> StreamingResponse:
    """SSE stream of tenant log entries (Redis ``XREAD`` tailing when wired).

    Backend errors are surfaced as ``{"type": "error"}`` events rather than a
    silently idle stream.
    """
    tenant = _require_tenant(request)

    async def event_generator() -> Any:
        yield _sse({"type": "connected"})

        try:
            recent = await log_store.query(tenant.tenant_id, limit=20)
        except Exception as exc:
            _obs_log.warning("observability_log_stream_query_failed: %s", exc)
            yield _sse({"type": "error", "detail": "Log store unavailable"})
            return
        for log_entry in reversed(recent):  # oldest-first for the initial burst
            if await request.is_disconnected():
                return
            yield _sse(log_entry)

        last_id = "$"  # only entries that arrive after the snapshot
        while True:
            if await request.is_disconnected():
                break

            if log_store.backend == "redis_stream":
                try:
                    # Blocks up to 2 s in XREAD; [] on timeout.
                    new_logs = await log_store.stream_new_since(tenant.tenant_id, last_id)
                except Exception as exc:
                    _obs_log.warning("observability_log_stream_read_failed: %s", exc)
                    yield _sse({"type": "error", "detail": "Log store unavailable"})
                    await asyncio.sleep(2)
                    continue
                for log_entry in new_logs:
                    if await request.is_disconnected():
                        return
                    last_id = log_entry.pop("_stream_id", last_id)
                    yield _sse(log_entry)
                # A fast-returning (empty) read must not busy-loop the generator.
                if not new_logs:
                    await asyncio.sleep(0.5)
            else:
                # Process-local memory store: no tailing primitive — heartbeat only.
                yield _sse({"type": "heartbeat"})
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


# ── SQL (SQLAlchemy Core over the ORM tables: a missing column fails to build) ─


def _duration_s() -> ColumnElement[Any]:
    """Goal wall-clock duration in seconds (NULL until the goal is terminal)."""
    return extract("epoch", _goals.c.completed_at - _goals.c.created_at)


def _bucket_expr(bucket: str, column: Any) -> ColumnElement[Any]:
    if bucket not in _BUCKETS:
        raise ValueError(f"unsupported bucket {bucket!r}")
    return func.date_trunc(bucket, column).label("bucket")


def _tenant_uuid(tenant_id: str) -> uuid.UUID | None:
    """``goal_cost_breakdowns.tenant_id`` is a UUID; non-UUID tenants own no rows."""
    try:
        return uuid.UUID(str(tenant_id))
    except (ValueError, TypeError, AttributeError):
        return None


def build_goal_summary_stmt(tenant_id: str, since: datetime) -> Select[Any]:
    """Goal count, completion counts and duration percentiles since *since*.

    ``PERCENTILE_CONT`` ignores NULLs, so only finished goals (``completed_at``
    set) contribute to the percentiles.
    """
    dur = _duration_s()
    return select(
        func.count().label("total"),
        func.count().filter(_goals.c.status == "complete").label("completed"),
        func.count().filter(_goals.c.status.in_(_TERMINAL)).label("finished"),
        func.percentile_cont(0.50).within_group(dur).label("p50_s"),
        func.percentile_cont(0.95).within_group(dur).label("p95_s"),
        func.percentile_cont(0.99).within_group(dur).label("p99_s"),
    ).where(_goals.c.tenant_id == tenant_id, _goals.c.created_at >= since)


def build_token_usage_stmt(tenant_id: str, since: datetime) -> Select[Any]:
    """Tokens per model from the durable per-(goal, role, model) cost ledger."""
    tokens = func.sum(_costs.c.input_tokens + _costs.c.output_tokens)
    return (
        select(_costs.c.model.label("model"), tokens.label("tokens"))
        .where(_costs.c.tenant_id == _tenant_uuid(tenant_id), _costs.c.updated_at >= since)
        .group_by(_costs.c.model)
        .order_by(tokens.desc())
        .limit(10)
    )


def build_goals_timeseries_stmt(
    tenant_id: str, since: datetime, until: datetime, bucket: str
) -> Select[Any]:
    b = _bucket_expr(bucket, _goals.c.created_at)
    return (
        select(
            b,
            func.count().label("total"),
            func.count().filter(_goals.c.status == "complete").label("success"),
            func.count().filter(_goals.c.status == "failed").label("failed"),
        )
        .where(
            _goals.c.tenant_id == tenant_id,
            _goals.c.created_at >= since,
            _goals.c.created_at <= until,
        )
        .group_by(b)
        .order_by(b)
    )


def build_latency_timeseries_stmt(
    tenant_id: str, since: datetime, until: datetime, bucket: str
) -> Select[Any]:
    dur = _duration_s()
    b = _bucket_expr(bucket, _goals.c.created_at)
    return (
        select(
            b,
            func.percentile_cont(0.50).within_group(dur).label("p50_s"),
            func.percentile_cont(0.95).within_group(dur).label("p95_s"),
        )
        .where(
            _goals.c.tenant_id == tenant_id,
            _goals.c.created_at >= since,
            _goals.c.created_at <= until,
            _goals.c.completed_at.is_not(None),
        )
        .group_by(b)
        .order_by(b)
    )


def build_cost_timeseries_stmt(
    tenant_id: str, since: datetime, until: datetime, bucket: str
) -> Select[Any]:
    """Spend per bucket from the durable cost ledger, bucketed by when the
    (goal, role, model) spend was first recorded."""
    b = _bucket_expr(bucket, _costs.c.first_recorded_at)
    return (
        select(b, func.sum(_costs.c.cost_usd).label("total_cost"))
        .where(
            _costs.c.tenant_id == _tenant_uuid(tenant_id),
            _costs.c.first_recorded_at >= since,
            _costs.c.first_recorded_at <= until,
        )
        .group_by(b)
        .order_by(b)
    )


def _require_db(request: Request) -> Any:
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        raise HTTPException(
            status_code=501,
            detail=(
                "Observability metrics are computed in Postgres and are not available "
                "in in-memory mode (no database configured)"
            ),
        )
    return db


def _ms(seconds: Any) -> int | None:
    return None if seconds is None else round(float(seconds) * 1000)


# ── Structured metrics ────────────────────────────────────────────────────────


@router.get("/metrics")
async def get_structured_metrics(
    request: Request,
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
) -> dict[str, Any]:
    """Goal count, success rate, duration percentiles and token usage by model.

    Window: goals created since ``since`` (default: last 24 h); ``until`` is
    validated but the window always ends now. Percentiles and success rate are
    ``null`` when no goal in the window has finished.
    """
    tenant = _require_tenant(request)
    since_dt = _parse_ts(since, "since") or datetime.now(UTC) - timedelta(hours=24)
    _parse_ts(until, "until")
    db = _require_db(request)
    tid = tenant.tenant_id

    from app.db.rls import sqlalchemy_rls_context

    try:
        async with db() as session, session.begin(), sqlalchemy_rls_context(session, tid):
            row = (await session.execute(build_goal_summary_stmt(tid, since_dt))).mappings().one()
            token_rows: Any = []
            if _tenant_uuid(tid) is not None:
                token_stmt = build_token_usage_stmt(tid, since_dt - timedelta(days=30))
                token_rows = (await session.execute(token_stmt)).mappings().all()
    except Exception as exc:
        _obs_log.warning("observability_metrics_query_failed: %s", exc)
        raise HTTPException(
            status_code=503, detail="Observability metrics unavailable: database query failed"
        ) from exc

    finished = int(row["finished"] or 0)
    p50, p95, p99 = _ms(row["p50_s"]), _ms(row["p95_s"]), _ms(row["p99_s"])
    return {
        "total_goals": int(row["total"] or 0),
        "success_rate": round(int(row["completed"] or 0) / finished, 3) if finished else None,
        "latency_percentiles": {"p50": p50, "p95": p95, "p99": p99},
        "goal_duration_percentiles": (
            [
                {"percentile": "p50", "ms": p50},
                {"percentile": "p95", "ms": p95},
                {"percentile": "p99", "ms": p99},
            ]
            if p50 is not None
            else []
        ),
        # Labelled by model: the cost ledger records (role, model), not provider.
        "token_usage_by_provider": [
            {"label": str(r["model"] or "unknown"), "value": int(r["tokens"] or 0)}
            for r in token_rows
        ],
        "since": since_dt.isoformat(),
        "source": "postgres",
    }


# ── Time-series ───────────────────────────────────────────────────────────────


@router.get("/timeseries")
async def get_timeseries(
    request: Request,
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    bucket: str = Query(default="hour", pattern="^(minute|hour|day)$"),
) -> dict[str, Any]:
    """Goals, cost and duration percentiles bucketed by time (default: last 24 h)."""
    tenant = _require_tenant(request)
    until_dt = _parse_ts(until, "until") or datetime.now(UTC)
    since_dt = _parse_ts(since, "since") or until_dt - timedelta(hours=24)
    if since_dt > until_dt:
        raise HTTPException(status_code=422, detail="'since' must not be after 'until'")
    db = _require_db(request)
    tid = tenant.tenant_id

    from app.db.rls import sqlalchemy_rls_context

    try:
        async with db() as session, session.begin(), sqlalchemy_rls_context(session, tid):
            goals_stmt = build_goals_timeseries_stmt(tid, since_dt, until_dt, bucket)
            goal_rows = (await session.execute(goals_stmt)).mappings().all()
            lat_stmt = build_latency_timeseries_stmt(tid, since_dt, until_dt, bucket)
            lat_rows = (await session.execute(lat_stmt)).mappings().all()
            cost_rows: Any = []
            if _tenant_uuid(tid) is not None:
                cost_stmt = build_cost_timeseries_stmt(tid, since_dt, until_dt, bucket)
                cost_rows = (await session.execute(cost_stmt)).mappings().all()
    except Exception as exc:
        _obs_log.warning("observability_timeseries_query_failed: %s", exc)
        raise HTTPException(
            status_code=503, detail="Observability time-series unavailable: database query failed"
        ) from exc

    return {
        "goals_per_hour": [
            {
                "ts": r["bucket"].isoformat(),
                "count": int(r["total"] or 0),
                "success": int(r["success"] or 0),
                "failed": int(r["failed"] or 0),
            }
            for r in goal_rows
        ],
        "cost_per_hour": [
            {"ts": r["bucket"].isoformat(), "cost_usd": round(float(r["total_cost"] or 0), 4)}
            for r in cost_rows
        ],
        "avg_latency_per_hour": [
            {"ts": r["bucket"].isoformat(), "p50_ms": _ms(r["p50_s"]), "p95_ms": _ms(r["p95_s"])}
            for r in lat_rows
        ],
    }


# ── Per-goal run trace ────────────────────────────────────────────────────────


def _timeline_is_authoritative(request: Request) -> bool:
    """The span timeline is process-local. It is the whole truth only when this
    process is the only one that could have run the goal: no Postgres (in-memory,
    single-process deployment) and no out-of-process goal queue (Celery)."""
    state = request.app.state
    if getattr(state, "db_session_factory", None) is not None:
        return False
    goal_svc = getattr(state, "goal_service", None)
    if goal_svc is None:
        return True
    return getattr(goal_svc, "_db", None) is None and getattr(goal_svc, "_task_queue", None) is None


@router.get("/goals/{goal_id}/trace")
async def get_goal_trace(goal_id: str, request: Request) -> dict[str, Any]:
    """Per-goal execution timeline for the Run Inspector.

    Returns the goal's captured steps — LangGraph nodes, LLM generations
    (model/tokens/cost/latency/role) and tool calls — each carrying its
    trace_id/span_id for deep-linking into Jaeger/Langfuse, plus a cost/token
    summary. Tenant-scoped (a tenant only sees its own goals' traces).

    The timeline is captured in-process by ``RunTimelineSpanProcessor``; when goals
    may run in another process or replica this answers 501 instead of serving an
    empty or partial trace. Durable per-goal cost/token totals remain available
    from ``GET /goals/{id}/cost-metrics``; full traces from the OTLP backend.
    """
    tenant = _require_tenant(request)
    if not _timeline_is_authoritative(request):
        raise HTTPException(
            status_code=501,
            detail=(
                "Per-goal run traces are captured per process/replica, and goals in this "
                "deployment may run on other replicas or Celery workers, so this replica's "
                "view would be incomplete. Use the OTLP trace backend (Jaeger/Langfuse) for "
                "full traces and GET /goals/{id}/cost-metrics for durable cost/token totals."
            ),
        )
    from app.observability.tracing import get_run_timeline_store

    entries = get_run_timeline_store().get(tenant.tenant_id, goal_id)
    total_cost = sum(float(e.get("cost_usd") or 0.0) for e in entries)
    total_in = sum(int(e.get("input_tokens") or 0) for e in entries)
    total_out = sum(int(e.get("output_tokens") or 0) for e in entries)
    generations = sum(1 for e in entries if str(e.get("name", "")).startswith("gen_ai"))
    return {
        "goal_id": goal_id,
        "scope": "process",
        "entries": entries,
        "summary": {
            "steps": len(entries),
            "generations": generations,
            "total_cost_usd": round(total_cost, 6),
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
        },
    }
