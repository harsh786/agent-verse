"""Schedules API — CRUD for trigger schedules, NL creation, webhooks, and SSE events."""

from __future__ import annotations

import asyncio
import json as _json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
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
async def list_schedules(request: Request) -> list[dict[str, Any]]:
    tenant_ctx: TenantContext = _require_tenant(request)
    store = _schedule_store(request)
    return [_record_to_dict(r) for r in store.list_all(tenant_ctx=tenant_ctx)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_schedule(
    request: Request, body: CreateScheduleRequest
) -> dict[str, Any]:
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
    schedule_id = await store.create_async(
        goal_id=goal_id,
        spec=spec,
        tenant_ctx=tenant_ctx,
        agent_id=body.agent_id,
        goal_template=body.goal_template,
    )

    if webhook_token:
        token_map[webhook_token] = schedule_id

    record = store.get(schedule_id, tenant_ctx=tenant_ctx) or {}
    return _record_to_dict(record)


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
    goal_svc = request.app.state.goal_service
    result = await goal_svc.submit_goal(
        goal=goal_text, priority="normal", dry_run=False,
        tenant_ctx=tenant, agent_id=rec.get("agent_id") or None,
    )
    return {"fired": True, "schedule_id": schedule_id, "goal_id": result["goal_id"]}


# ---------------------------------------------------------------------------
# NL schedule creation
# ---------------------------------------------------------------------------

@nl_router.post("/schedule", status_code=status.HTTP_201_CREATED)
async def nl_create_schedule(
    request: Request, body: NLScheduleRequest
) -> list[dict[str, Any]]:
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

        schedule_id = await store.create_async(
            goal_id=body.command,
            spec=spec,
            tenant_ctx=tenant_ctx,
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

@webhooks_router.post("/{token}")
async def webhook_trigger(request: Request, token: str) -> dict[str, Any]:
    """Receive an inbound webhook and fire the associated schedule."""
    _require_tenant(request)
    token_map = _token_map(request)
    schedule_id = token_map.get(token)
    if schedule_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown webhook token",
        )
    return {"status": "ok", "schedule_id": schedule_id}


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
    context: str = ""       # optional additional context


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
        ttype = r.get("trigger_type") or (r.get("spec") or {}).get("trigger_type") or "unknown"
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
                "trigger_type": r.get("trigger_type") or (r.get("spec") or {}).get("trigger_type") or "unknown",
                "status": "paused" if r.get("paused") else "active",
                "last_fired_at": r.get("last_fired_at"),
                "next_run_at": r.get("next_run_at"),
            }
            for r in records[:20]   # cap at 20 for response size
        ],
    }


@router.post("/suggest")
async def suggest_schedule(
    request: Request, body: SuggestScheduleRequest
) -> dict[str, Any]:
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

    from app.providers.base import CompletionRequest, Message  # noqa: PLC0415
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
                max_tokens=600,
            )
        )
        raw = resp.content.strip().lstrip("```json").lstrip("```").rstrip("```")
        data = _json.loads(raw)
        suggestions = data.get("suggestions", [])
    except Exception:  # noqa: BLE001
        suggestions = []

    return {
        "suggestions": suggestions,
        "goal": body.goal_description,
        "llm_powered": True,
    }
