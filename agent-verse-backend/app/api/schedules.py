"""Schedules API — CRUD for trigger schedules, NL creation, webhooks, and SSE events."""

from __future__ import annotations

import asyncio
import json as _json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.tenancy.context import TenantContext
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.nl_scheduler import NLScheduler
from app.triggers.store import ScheduleStore

# Four routers covering different URL prefixes defined in this module.
router = APIRouter(prefix="/schedules", tags=["schedules"])
nl_router = APIRouter(prefix="/nl", tags=["schedules"])
webhooks_router = APIRouter(prefix="/webhooks", tags=["schedules"])
events_router = APIRouter(tags=["schedules"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateScheduleRequest(BaseModel):
    trigger_type: str = "once"
    cron_expr: str = ""
    interval_seconds: int = 0
    endpoint: str = ""
    goal_template: str = ""
    agent_id: str = ""
    name: str = ""


class NLScheduleRequest(BaseModel):
    command: str
    agent_id: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


def _schedule_store(request: Request) -> ScheduleStore:
    return request.app.state.schedule_store  # type: ignore[no-any-return]


async def _create_with_quota(
    store: ScheduleStore,
    tenant_ctx: TenantContext,
    *,
    goal_id: str,
    spec: TriggerSpec,
    agent_id: str,
    goal_template: str,
) -> str:
    """``create_async`` with PLAN_MAX_TRIGGERS enforced (counted in Postgres when
    DB-backed). The quota existed but no create path ever checked it."""
    from app.triggers.quota import TriggerQuotaExceeded

    try:
        return await store.create_async(
            goal_id=goal_id,
            spec=spec,
            tenant_ctx=tenant_ctx,
            agent_id=agent_id,
            goal_template=goal_template,
            quota_plan=str(getattr(tenant_ctx, "plan", "free") or "free"),
        )
    except TriggerQuotaExceeded as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


def _nl_scheduler(request: Request) -> NLScheduler:
    return request.app.state.nl_scheduler  # type: ignore[no-any-return]


def _token_map(request: Request) -> dict[str, str]:
    """Lazy {webhook_token: schedule_id} map stored on app.state."""
    if not hasattr(request.app.state, "_webhook_tokens"):
        request.app.state._webhook_tokens = {}
    return request.app.state._webhook_tokens  # type: ignore[no-any-return]


def _validate_agent_id(
    request: Request,
    agent_id: str,
    *,
    tenant_ctx: TenantContext,
) -> None:
    if not agent_id:
        return

    agent_store: Any | None = getattr(request.app.state, "agent_store", None)
    if agent_store is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Agent store unavailable",
        )

    agent = agent_store.get(agent_id, tenant_ctx=tenant_ctx)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )


def _spec_to_dict(spec: TriggerSpec) -> dict[str, Any]:
    return {
        "trigger_type": spec.trigger_type,
        "cron_expression": spec.cron_expression,
        "timezone": spec.timezone,
        "interval_seconds": spec.interval_seconds,
        "webhook_token": spec.webhook_token,
        "event_channel": spec.event_channel,
        "fire_at_iso": spec.fire_at_iso,
        "description": spec.description,
    }


def _record_to_dict(rec: dict[str, Any]) -> dict[str, Any]:
    out = dict(rec)
    out.setdefault("agent_id", "")
    out.setdefault("goal_template", out.get("goal_id", ""))
    if isinstance(out.get("spec"), TriggerSpec):
        out["spec"] = _spec_to_dict(out["spec"])
    return out


# ---------------------------------------------------------------------------
# Schedule CRUD
# ---------------------------------------------------------------------------


@router.get("")
async def list_schedules(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    tenant_ctx: TenantContext = _require_tenant(request)
    store = _schedule_store(request)
    records = store.list_all(tenant_ctx=tenant_ctx)
    return [_record_to_dict(r) for r in records[offset : offset + limit]]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_schedule(request: Request, body: CreateScheduleRequest) -> dict[str, Any]:
    tenant_ctx: TenantContext = _require_tenant(request)
    store = _schedule_store(request)
    token_map = _token_map(request)

    try:
        ttype = TriggerType(body.trigger_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown trigger_type: {body.trigger_type}",
        ) from None

    if ttype == TriggerType.CRON:
        from app.triggers.models import validate_cron

        try:
            validate_cron(body.cron_expr, str(getattr(tenant_ctx, "plan", "free") or "free"))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

    _validate_agent_id(request, body.agent_id, tenant_ctx=tenant_ctx)

    webhook_token = ""
    if ttype == TriggerType.WEBHOOK:
        webhook_token = secrets.token_hex(16)

    spec = TriggerSpec(
        trigger_type=ttype,
        cron_expression=body.cron_expr,
        interval_seconds=body.interval_seconds,
        webhook_token=webhook_token,
        description=body.name or body.goal_template,
    )

    goal_id = body.goal_template or body.agent_id or "unset"
    schedule_id = await _create_with_quota(
        store,
        tenant_ctx,
        goal_id=goal_id,
        spec=spec,
        agent_id=body.agent_id,
        goal_template=body.goal_template,
    )

    if webhook_token:
        token_map[webhook_token] = schedule_id

    record = store.get(schedule_id, tenant_ctx=tenant_ctx) or {}
    return _record_to_dict(record)


def _analytics_trigger_type(rec: dict[str, Any]) -> str:
    """Trigger type of a store record; ``spec`` is a TriggerSpec, not a dict.

    (The analytics handler used ``(rec["spec"] or {}).get(...)``, which only
    never crashed because the route was unreachable — see below.)
    """
    explicit = rec.get("trigger_type")
    if explicit:
        return str(explicit)
    spec = rec.get("spec")
    if isinstance(spec, dict):
        ttype = spec.get("trigger_type")
    else:
        ttype = getattr(spec, "trigger_type", "")
    return str(getattr(ttype, "value", ttype) or "unknown")


# Declared BEFORE ``/{schedule_id}``: FastAPI matches routes in order, so the
# parameterized route used to swallow GET /schedules/analytics (→ 404).
@router.get("/analytics")
async def get_schedule_analytics(request: Request) -> dict[str, Any]:
    """Aggregate analytics across all schedules for this tenant.

    Returns counts by status and trigger type, plus a simple 7-day
    firing cadence derived from last_fired_at timestamps.
    """
    tenant_ctx: TenantContext = _require_tenant(request)
    store = _schedule_store(request)
    records = store.list_all(tenant_ctx=tenant_ctx)

    total = len(records)
    active = sum(1 for r in records if not r.get("paused", False))
    paused = total - active

    by_type: dict[str, int] = {}
    for r in records:
        ttype = _analytics_trigger_type(r)
        by_type[ttype] = by_type.get(ttype, 0) + 1

    # Build a rough 7-day cadence histogram using last_fired_at
    now = datetime.now(UTC)
    fired_by_day: dict[str, int] = {}
    for i in range(7):
        day_str = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        fired_by_day[day_str] = 0

    for r in records:
        lf = r.get("last_fired_at")
        if lf:
            try:
                dt = datetime.fromisoformat(str(lf).replace("Z", "+00:00"))
                day_str = dt.strftime("%Y-%m-%d")
                if day_str in fired_by_day:
                    fired_by_day[day_str] += 1
            except (ValueError, TypeError):
                pass

    return {
        "total": total,
        "active": active,
        "paused": paused,
        "by_trigger_type": by_type,
        "fired_last_7_days": fired_by_day,
        "schedules_summary": [
            {
                "schedule_id": r.get("schedule_id", ""),
                "goal_template": r.get("goal_template", ""),
                "trigger_type": _analytics_trigger_type(r),
                "status": "paused" if r.get("paused") else "active",
                "last_fired_at": r.get("last_fired_at"),
                "next_run_at": r.get("next_run_at"),
            }
            for r in records[:20]  # cap at 20 for response size
        ],
    }


@router.get("/{schedule_id}")
async def get_schedule(request: Request, schedule_id: str) -> dict[str, Any]:
    tenant_ctx: TenantContext = _require_tenant(request)
    store = _schedule_store(request)
    rec = store.get(schedule_id, tenant_ctx=tenant_ctx)
    if rec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule {schedule_id} not found",
        )
    return _record_to_dict(rec)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(request: Request, schedule_id: str) -> None:
    tenant_ctx: TenantContext = _require_tenant(request)
    store = _schedule_store(request)
    removed = await store.delete_async(schedule_id, tenant_ctx=tenant_ctx)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule {schedule_id} not found",
        )


@router.post("/{schedule_id}/pause")
async def pause_schedule(request: Request, schedule_id: str) -> dict[str, Any]:
    tenant_ctx: TenantContext = _require_tenant(request)
    store = _schedule_store(request)
    ok = store.pause(schedule_id, tenant_ctx=tenant_ctx)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule {schedule_id} not found",
        )
    return {"schedule_id": schedule_id, "paused": True}


@router.post("/{schedule_id}/resume")
async def resume_schedule(request: Request, schedule_id: str) -> dict[str, Any]:
    tenant_ctx: TenantContext = _require_tenant(request)
    store = _schedule_store(request)
    ok = store.resume(schedule_id, tenant_ctx=tenant_ctx)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule {schedule_id} not found",
        )
    return {"schedule_id": schedule_id, "paused": False}


@router.post("/{schedule_id}/fire", status_code=202)
async def fire_schedule_now(request: Request, schedule_id: str) -> dict[str, Any]:
    """Manually fire a REST or webhook schedule."""
    tenant = _require_tenant(request)
    store = _schedule_store(request)
    rec = store.get(schedule_id, tenant_ctx=tenant)
    if rec is None:
        raise HTTPException(404, f"Schedule {schedule_id} not found")
    spec = rec.get("spec")
    trigger_type_val = spec.trigger_type.value if spec is not None else ""
    if trigger_type_val not in {"rest", "webhook"}:
        raise HTTPException(400, "Only REST and webhook schedules can be manually fired")
    goal_text = rec.get("goal_template") or rec.get("goal_id") or "Execute scheduled task"
    dispatcher = getattr(request.app.state, "trigger_dispatcher", None)
    if dispatcher is not None:
        # Through the dispatcher so the manual fire is governed AND recorded in
        # trigger_events (which GET /schedules/{id}/history reads). The fire id
        # makes each manual fire a distinct firing rather than a replay.
        import uuid as _uuid

        from app.api.triggers import _spec_for_dispatch

        event = await dispatcher.dispatch(
            _spec_for_dispatch(rec),
            {"manual_fire_id": _uuid.uuid4().hex, "source": "manual"},
            tenant,
        )
        skip_reason = getattr(event, "skip_reason", None)
        if skip_reason:
            raise HTTPException(409, f"Schedule fire suppressed: {skip_reason}")
        if not getattr(event, "goal_created", False):
            raise HTTPException(502, "Goal could not be enqueued (recorded in the trigger DLQ)")
        return {"fired": True, "schedule_id": schedule_id, "goal_id": event.goal_id}
    goal_svc = request.app.state.goal_service
    result = await goal_svc.submit_goal(
        goal=goal_text,
        priority="normal",
        dry_run=False,
        tenant_ctx=tenant,
        agent_id=rec.get("agent_id") or None,
    )
    return {"fired": True, "schedule_id": schedule_id, "goal_id": result["goal_id"]}


# ---------------------------------------------------------------------------
# NL schedule creation
# ---------------------------------------------------------------------------


@nl_router.post("/schedule", status_code=status.HTTP_201_CREATED)
async def nl_create_schedule(request: Request, body: NLScheduleRequest) -> list[dict[str, Any]]:
    """Parse a NL schedule description and create one or more schedule records."""
    tenant_ctx: TenantContext = _require_tenant(request)
    store = _schedule_store(request)
    nl = _nl_scheduler(request)
    token_map = _token_map(request)

    _validate_agent_id(request, body.agent_id, tenant_ctx=tenant_ctx)

    specs = await nl.parse(body.command)
    created: list[dict[str, Any]] = []

    for spec in specs:
        webhook_token = ""
        if spec.trigger_type == TriggerType.WEBHOOK:
            webhook_token = secrets.token_hex(16)
            spec.webhook_token = webhook_token

        schedule_id = await _create_with_quota(
            store,
            tenant_ctx,
            goal_id=body.command,
            spec=spec,
            agent_id=body.agent_id,
            goal_template=body.command,
        )

        if webhook_token:
            token_map[webhook_token] = schedule_id

        rec = store.get(schedule_id, tenant_ctx=tenant_ctx) or {}
        created.append(_record_to_dict(rec))

    return created


# ---------------------------------------------------------------------------
# Webhook trigger
# ---------------------------------------------------------------------------


@webhooks_router.post("/alerts/{trigger_type}")
async def receive_alert_webhook(
    trigger_type: str,
    schedule_id: str,
    request: Request,
) -> dict[str, Any]:
    """Receive incoming alert webhooks (Alertmanager / Datadog / PagerDuty).

    Stores the JSON payload in Redis under ``alert_payload:{type}:{schedule_id}``
    with a 5-minute TTL so ``fire_due_schedules`` picks it up on the next tick.
    """
    tenant_ctx = _require_tenant(request)
    valid_types = ("alertmanager", "datadog", "pagerduty")
    if trigger_type not in valid_types:
        raise HTTPException(400, f"Unknown trigger type. Must be one of: {valid_types}")
    # The schedule must be the caller's, and the cache key is tenant-scoped: any
    # tenant used to be able to inject alert context into another tenant's
    # alert-triggered goal by naming its schedule_id.
    store = getattr(request.app.state, "schedule_store", None)
    if store is None or not store.get(schedule_id, tenant_ctx=tenant_ctx):
        raise HTTPException(404, "Schedule not found")

    pools = getattr(request.app.state, "pools", None)
    redis = getattr(pools, "redis", None) if pools else None
    if redis is None:
        raise HTTPException(503, "Redis not available for alert ingestion")

    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {"data": payload}
        cache_key = f"alert_payload:{tenant_ctx.tenant_id}:{trigger_type}:{schedule_id}"
        await redis.set(cache_key, _json.dumps(payload), ex=300)
        return {"status": "queued", "trigger_type": trigger_type, "schedule_id": schedule_id}
    except Exception as exc:
        raise HTTPException(500, f"Failed to queue alert: {exc}") from exc


@webhooks_router.post("/{token}", status_code=status.HTTP_202_ACCEPTED)
async def webhook_trigger(request: Request, token: str) -> dict[str, Any]:
    """Receive an inbound webhook and fire the caller's matching webhook trigger.

    This used to be a stub: it looked ``token`` up in a process-local
    ``{token: schedule_id}`` dict — global across tenants, empty after a restart
    or on another replica — and returned ``{"status": "ok"}`` without dispatching
    anything, so every webhook was silently dropped while the sender was told it
    succeeded. It now resolves the AUTHENTICATED tenant's own ``webhook`` trigger
    from the ScheduleStore and runs it through the real ``TriggerDispatcher``
    (the path ``POST /triggers/webhooks/webhook/{token}`` uses), enforcing the
    trigger's signing secret when it has one.
    """
    import hmac

    from app.api.triggers import _spec_for_dispatch
    from app.triggers.webhooks.verifier import WebhookSignatureVerifier

    tenant_ctx: TenantContext = _require_tenant(request)
    store = getattr(request.app.state, "schedule_store", None)
    if store is None:
        raise HTTPException(503, "Schedule store unavailable")

    rec: dict[str, Any] | None = None
    for candidate in store.list_all(tenant_ctx=tenant_ctx):
        spec = candidate.get("spec")
        if spec is None or getattr(spec, "trigger_type", None) != TriggerType.WEBHOOK:
            continue
        stored = str(getattr(spec, "webhook_token", "") or "")
        if stored and hmac.compare_digest(stored, token):
            rec = candidate
            break
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown webhook token")
    if rec.get("paused"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Trigger is paused")
    dispatcher = getattr(request.app.state, "trigger_dispatcher", None)
    if dispatcher is None:  # never report success for a webhook nothing can run
        raise HTTPException(503, "Trigger dispatcher unavailable")

    body_bytes = await request.body()
    spec = _spec_for_dispatch(rec)
    secret = str(getattr(spec, "webhook_signature_secret", "") or "")
    if secret:
        signature = request.headers.get("x-signature", "")
        if not await WebhookSignatureVerifier().verify(body_bytes, signature, secret):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload: dict[str, Any] = {}
    if body_bytes:
        try:
            parsed = _json.loads(body_bytes)
        except ValueError:
            raise HTTPException(422, "Webhook body must be JSON") from None
        payload = parsed if isinstance(parsed, dict) else {"data": parsed}

    event = await dispatcher.dispatch(spec, payload, tenant_ctx)
    return {
        "schedule_id": rec.get("schedule_id"),
        "goal_id": getattr(event, "goal_id", None),
        "goal_created": getattr(event, "goal_created", None),
        "skip_reason": getattr(event, "skip_reason", None),
        "fired_at": getattr(event, "fired_at", None),
    }


# ---------------------------------------------------------------------------
# SSE real-time events stream
# ---------------------------------------------------------------------------


@events_router.get("/events")
async def events_stream(request: Request) -> StreamingResponse:
    """Platform-wide SSE. Uses Redis pub/sub when available, heartbeats as fallback."""
    tenant = _require_tenant(request)

    async def generator() -> Any:
        # Try Redis pub/sub first
        pools = getattr(request.app.state, "pools", None)
        redis_client = getattr(pools, "redis", None) if pools else None

        if redis_client is not None:
            try:
                pubsub = redis_client.pubsub()
                channel = f"platform_events:{tenant.tenant_id}"
                await pubsub.subscribe(channel)
                # Initial heartbeat
                yield 'data: {"type":"heartbeat"}\n\n'
                try:
                    async for message in pubsub.listen():
                        if await request.is_disconnected():
                            break
                        if message.get("type") == "message":
                            yield f"data: {message['data']}\n\n"
                finally:
                    await pubsub.unsubscribe(channel)
                    await pubsub.aclose()
                return
            except Exception:
                pass  # Fall back to heartbeat

        # Fallback: heartbeat only
        while True:
            if await request.is_disconnected():
                break
            yield 'data: {"type":"heartbeat"}\n\n'
            await asyncio.sleep(30)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Analytics & Intelligence endpoints
# ---------------------------------------------------------------------------


class SuggestScheduleRequest(BaseModel):
    goal_description: str
    context: str = ""  # optional additional context


@router.post("/suggest")
async def suggest_schedule(request: Request, body: SuggestScheduleRequest) -> dict[str, Any]:
    """Use the LLM to suggest optimal schedule configurations for a goal.

    Returns 3 ranked suggestions with rationale, trigger type, and
    example cron/interval values.
    """
    _require_tenant(request)
    provider = getattr(request.app.state, "llm_provider", None)
    if provider is None:
        # Fallback: return template suggestions without LLM
        return {
            "suggestions": [
                {
                    "rank": 1,
                    "title": "Daily at 9 AM",
                    "trigger_type": "cron",
                    "cron_expr": "0 9 * * *",
                    "interval_seconds": None,
                    "rationale": "Good for daily recurring tasks during business hours.",
                    "use_case": "Reports, summaries, digests",
                },
                {
                    "rank": 2,
                    "title": "Every 4 hours",
                    "trigger_type": "interval",
                    "cron_expr": None,
                    "interval_seconds": 14400,
                    "rationale": "Ideal for monitoring and alerting tasks.",
                    "use_case": "Health checks, metrics collection",
                },
                {
                    "rank": 3,
                    "title": "Weekly Monday 8 AM",
                    "trigger_type": "cron",
                    "cron_expr": "0 8 * * 1",
                    "interval_seconds": None,
                    "rationale": "Low frequency for strategic or planning tasks.",
                    "use_case": "Weekly planning, team summaries",
                },
            ],
            "goal": body.goal_description,
            "llm_powered": False,
        }

    from app.providers.base import CompletionRequest, Message

    system_prompt = (
        "You are a scheduling expert for AI automation systems. "
        "Given a goal description, suggest 3 optimal schedule configurations. "
        "Respond with JSON only:\n"
        '{"suggestions": [{"rank": 1, "title": "...", "trigger_type": "cron|interval", '
        '"cron_expr": "..." or null, "interval_seconds": number or null, '
        '"rationale": "...", "use_case": "..."}]}'
    )
    user_msg = f"Goal: {body.goal_description}\nContext: {body.context or 'none'}"
    try:
        resp = await provider.complete(
            CompletionRequest(
                messages=[
                    Message(role="system", content=system_prompt),
                    Message(role="user", content=user_msg),
                ],
                model="",
                max_tokens=600,
            )
        )
        raw = (
            resp.content.strip()
            .removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )
        data = _json.loads(raw)
        suggestions = data.get("suggestions", [])
    except Exception:
        suggestions = []

    return {
        "suggestions": suggestions,
        "goal": body.goal_description,
        "llm_powered": True,
    }


# ---------------------------------------------------------------------------
# Schedule Run History
# ---------------------------------------------------------------------------


@router.get("/{schedule_id}/history")
async def get_schedule_history(
    schedule_id: str,
    request: Request,
    limit: int = Query(default=20, le=100),
) -> dict:
    """Get execution history for a schedule, from the ``trigger_events`` audit log.

    This used to filter ``goals`` on ``execution_context->>'schedule_id'`` — a
    key nothing ever writes — so it was always empty, and a bare
    ``except: pass`` hid every error. Every fire (beat, manual, webhook) goes
    through the TriggerDispatcher, which records one ``trigger_events`` row per
    firing *including suppressed ones* (skip_reason), keyed by the schedule id;
    the goal's live status comes from a join on ``goals``.
    """
    tenant = _require_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        return {"runs": [], "total": 0, "schedule_id": schedule_id}

    import logging

    from sqlalchemy import text as _t

    from app.db.rls import sqlalchemy_rls_context

    try:
        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant.tenant_id),
        ):
            rows = (
                await session.execute(
                    _t(
                        """
                        SELECT te.id, te.goal_id, te.goal_created, te.skip_reason,
                               te.fired_at, g.status, g.created_at, g.completed_at,
                               g.error_message
                        FROM trigger_events te
                        LEFT JOIN goals g
                               ON g.id = te.goal_id AND g.tenant_id = te.tenant_id
                        WHERE te.tenant_id = :tid AND te.trigger_id = :sid
                        ORDER BY te.fired_at DESC
                        LIMIT :limit
                        """
                    ),
                    {"tid": tenant.tenant_id, "sid": schedule_id, "limit": limit},
                )
            ).fetchall()
    except Exception as exc:
        logging.getLogger(__name__).error(
            "schedule_history_query_failed schedule=%s: %s", schedule_id, exc
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Schedule history unavailable",
        ) from exc

    runs = []
    for row in rows:
        event_id, goal_id, goal_created, skip_reason, fired_at = row[0:5]
        goal_status, goal_created_at, goal_completed_at, goal_error = row[5:9]
        if skip_reason:
            run_status = "skipped"
        elif not goal_created:
            run_status = "failed"  # dispatch failed before a goal existed (see DLQ)
        elif goal_status == "complete":
            run_status = "success"
        else:
            run_status = goal_status or "dispatched"
        duration_ms = None
        if goal_created_at is not None and goal_completed_at is not None:
            duration_ms = int((goal_completed_at - goal_created_at).total_seconds() * 1000)
        runs.append(
            {
                "run_id": str(event_id),
                "goal_id": str(goal_id) if goal_id else None,
                "status": run_status,
                "skip_reason": skip_reason,
                "started_at": fired_at.isoformat() if fired_at else None,
                "duration_ms": duration_ms,
                "error": goal_error or None,
            }
        )
    return {"runs": runs, "total": len(runs), "schedule_id": schedule_id}
