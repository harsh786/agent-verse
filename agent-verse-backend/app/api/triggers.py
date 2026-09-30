"""Triggers API — CRUD, lifecycle control, simulation, events, and DLQ."""

from __future__ import annotations

import contextlib
from typing import Any

import structlog
from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from app.tenancy.context import TenantContext
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.simulation import get_sample_payload

router = APIRouter(prefix="/triggers", tags=["triggers"])
logger = structlog.get_logger(__name__)


# ── Dependency helpers ────────────────────────────────────────────────────────


def _require_tenant(request: Request) -> TenantContext:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return ctx  # type: ignore[return-value]


def _require_trigger_permission(tenant_ctx: Any, operation: str) -> str:
    """The caller's trigger-matrix role; 403 when it may not ``operation``."""
    from app.triggers.rbac import TriggerPermissionDenied, check_permission, trigger_role

    role = trigger_role(tenant_ctx)
    try:
        check_permission(role, operation)
    except TriggerPermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return role


def _get_store(request: Request) -> Any:
    return getattr(request.app.state, "schedule_store", None)


def _get_dispatcher(request: Request) -> Any:
    return getattr(request.app.state, "trigger_dispatcher", None)


def _get_db(request: Request) -> Any:
    # WT-2/G5: app.state.db is never set; the DB session factory lives here.
    return getattr(request.app.state, "db_session_factory", None)


def _has_async(store: Any, name: str) -> bool:
    import inspect

    return inspect.iscoroutinefunction(getattr(store, name, None))


def _strict(fn: Any) -> dict[str, Any]:
    """``{"strict": True}`` when the store method supports it (test doubles may not)."""
    import inspect

    try:
        return {"strict": True} if "strict" in inspect.signature(fn).parameters else {}
    except (TypeError, ValueError):
        return {}


async def _durable_read(awaitable: Any) -> Any:
    """TRG-29: a DB outage is a 503 (as on /schedules), never an answer from this
    replica's possibly stale per-process cache."""
    from app.triggers.store import ScheduleStoreUnavailableError

    try:
        return await awaitable
    except ScheduleStoreUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Trigger store unavailable; retry") from exc


async def _store_get(store: Any, schedule_id: str, tenant_ctx: TenantContext) -> Any:
    """DB read-through lookup (the in-memory ``get`` only knows this replica)."""
    if store is None:
        return None
    if _has_async(store, "get_async"):
        return await _durable_read(
            store.get_async(schedule_id, tenant_ctx=tenant_ctx, **_strict(store.get_async))
        )
    return store.get(schedule_id, tenant_ctx=tenant_ctx)


# The webhook token in ``/triggers/webhooks/{type}/{token}`` is the credential a
# third party presents (it selects the tenant pre-auth), so it must not be
# guessable: shorter tokens never authenticate and are refused on create.
_MIN_WEBHOOK_TOKEN_LEN = 32


def _push_webhook_types() -> frozenset[str]:
    from app.triggers.dispatch_map import WEBHOOK_TYPE_MAP

    return frozenset({*WEBHOOK_TYPE_MAP.values(), TriggerType.WEBHOOK.value})


async def _webhook_tenant_ctx(request: Request, tenant_id: str) -> TenantContext:
    """Least-privilege context for a token-authenticated webhook delivery: the
    token owner's tenant and plan, no roles (nothing here is scope-gated).

    TRG-06: the plan is read from the tenant record; it used to come from this
    replica's in-memory tenant dict, which is empty for tenants created on
    another replica (so their deliveries ran as FREE).
    """
    from app.tenancy.plan_resolver import resolve_tenant_plan

    state = request.app.state
    plan = await resolve_tenant_plan(
        tenant_id,
        tenant_service=getattr(state, "tenant_service", None),
        db_factory=getattr(state, "db_session_factory", None),
    )
    return TenantContext(tenant_id=tenant_id, plan=plan, api_key_id="webhook-token", roles=())


# ── Request / Response models ─────────────────────────────────────────────────


class TriggerSpecRequest(BaseModel):
    trigger_type: str
    name: str | None = None
    description: str | None = None
    cron_expression: str | None = None
    interval_seconds: int | None = None
    run_at: str | None = None
    watch_goal_id: str | None = None
    watch_agent_id: str | None = None
    score_threshold: float | None = None
    condition_cel: str | None = None
    webhook_secret: str | None = None
    mqtt_topic: str | None = None
    mqtt_broker_url: str | None = None
    geofence_action: str | None = None
    max_firings: int | None = None
    enabled: bool = True

    model_config = {"extra": "allow"}


class CreateTriggerRequest(BaseModel):
    spec: TriggerSpecRequest
    goal_id: str = ""
    agent_id: str = ""
    # Optional: a trigger may simply reference an agent (agent_id) and run that
    # agent's own goal on fire, so an explicit goal template is not required.
    goal_template: str = Field(default="")

    @model_validator(mode="after")
    def _require_goal_or_agent(self) -> CreateTriggerRequest:
        """A trigger must have something concrete to run on fire, in priority order:
        a bound ``goal_id`` (re-run a specific existing goal — avoids the noise of a
        free-text template matching many goals), a ``goal_template`` (NL, with
        ``{{payload.*}}`` interpolation), or a referenced ``agent_id`` (whose own
        goal runs). None of the three → nothing to fire (422)."""
        if (
            not (self.goal_id or "").strip()
            and not (self.goal_template or "").strip()
            and not (self.agent_id or "").strip()
        ):
            raise ValueError("Provide a goal_id, a goal_template, or reference an agent_id")
        return self


class SimulateRequest(BaseModel):
    payload: dict[str, Any] | None = None


class FireRequest(BaseModel):
    payload: dict[str, Any] | None = None
    # Identity of the firing for time-based triggers: a replay of the same tick
    # (same scheduled_fire_time) is deduplicated. Without it each call is an
    # independent manual fire.
    scheduled_fire_time: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_spec(req: TriggerSpecRequest) -> TriggerSpec:
    try:
        tt = TriggerType(req.trigger_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown trigger_type: {req.trigger_type!r}",
        ) from exc

    import dataclasses as _dc

    valid_fields = {f.name for f in _dc.fields(TriggerSpec)}

    kwargs: dict[str, Any] = {"trigger_type": tt}
    # Map known request fields to their TriggerSpec equivalents
    field_map = {
        "description": "description",
        "cron_expression": "cron_expression",
        "interval_seconds": "interval_seconds",
        "run_at": "fire_at_iso",
        "watch_goal_id": "watch_goal_id",
        "watch_agent_id": "watch_agent_id",
        "score_threshold": "score_threshold",
        "condition_cel": "condition_expression",
        "webhook_secret": "webhook_signature_secret",
        "mqtt_topic": "mqtt_topic",
        "mqtt_broker_url": "mqtt_broker_url",
        "geofence_action": "geofence_action",
    }
    for req_field, spec_field in field_map.items():
        val = getattr(req, req_field, None)
        if val is not None and spec_field in valid_fields:
            kwargs[spec_field] = val

    # Extra fields from model_config extra=allow — only add if they're valid spec fields
    for key, val in (req.model_extra or {}).items():
        if val is not None and key in valid_fields:
            kwargs[key] = val

    return TriggerSpec(**kwargs)


def _serialize_record(rec: dict[str, Any]) -> dict[str, Any]:
    spec: TriggerSpec | None = rec.get("spec")
    out = {
        "schedule_id": rec["schedule_id"],
        "goal_id": rec.get("goal_id", ""),
        "agent_id": rec.get("agent_id", ""),
        "goal_template": rec.get("goal_template", ""),
        "paused": rec.get("paused", False),
    }
    # Surface lifecycle timestamps so the UI can show when a trigger was created
    # and when it will next / last fire. Values may be datetime (DB-hydrated) or
    # already-ISO strings; normalise to ISO for the JSON response.
    for _ts in ("created_at", "next_fire_at", "last_fired_at"):
        _val = rec.get(_ts)
        if _val is not None:
            out[_ts] = _val.isoformat() if hasattr(_val, "isoformat") else _val
    if spec is not None:
        import dataclasses

        # The signing secret is write-only: it used to be echoed on every GET /
        # list / PATCH response. It is returned exactly once, by rotate-secret.
        out["spec"] = {
            k: (v.value if hasattr(v, "value") else v)
            for k, v in dataclasses.asdict(spec).items()
            if v is not None and k not in _WRITE_ONLY_SPEC_FIELDS
        }
        out["spec"]["has_webhook_signature_secret"] = bool(
            getattr(spec, "webhook_signature_secret", "")
        )
    return out


_WRITE_ONLY_SPEC_FIELDS = frozenset({"webhook_signature_secret"})


def _spec_for_dispatch(rec: dict[str, Any]) -> TriggerSpec:
    """Return the trigger's spec enriched with the record-level agent/goal refs.

    ``agent_id`` and ``goal_template`` are persisted on the trigger *record*
    (via ``store.create``), not on the embedded ``TriggerSpec``. The dispatcher,
    however, resolves the goal text and the routed agent from the spec — so when
    firing (or simulating) we must fold those record-level references onto the
    spec. Without this, a trigger that merely references an agent (no goal
    template) fires a generic default goal with no agent routing.
    """
    spec: TriggerSpec = rec["spec"]
    # Only fill from the record when the spec doesn't already carry the value,
    # so an explicit spec-level field still wins. Use getattr/setattr defensively
    # so a non-dataclass stand-in (tests) or a spec missing a field is tolerated.
    if not (getattr(spec, "goal_template", "") or "").strip():
        with contextlib.suppress(Exception):
            spec.goal_template = rec.get("goal_template", "") or ""
    # The record's agent is the agent to RUN — never the goal-event source filter
    # (``watch_agent_id``), see ``bind_refs_to_spec``.
    if not (getattr(spec, "agent_id", "") or "").strip():
        with contextlib.suppress(Exception):
            spec.agent_id = rec.get("agent_id", "") or ""  # type: ignore[attr-defined]
    # trigger_id drives the dispatcher's per-trigger idempotency key; without the
    # real schedule id every manual fire dedups as "unknown" against other
    # triggers. Bind it here so the fire/simulate paths match the beat path.
    if not (getattr(spec, "trigger_id", "") or "").strip():
        with contextlib.suppress(Exception):
            spec.trigger_id = rec.get("schedule_id", "") or ""
    return spec


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("", response_model=list[dict[str, Any]])
async def list_triggers(request: Request) -> list[dict[str, Any]]:
    """List all triggers for the authenticated tenant."""
    tenant_ctx = _require_tenant(request)
    store = _get_store(request)
    if store is None:
        return []
    if _has_async(store, "list_all_async"):
        records = await _durable_read(
            store.list_all_async(tenant_ctx=tenant_ctx, **_strict(store.list_all_async))
        )
    else:
        records = store.list_all(tenant_ctx=tenant_ctx)
    return [_serialize_record(r) for r in records]


@router.post("", response_model=dict[str, Any], status_code=201)
async def create_trigger(request: Request, body: CreateTriggerRequest) -> dict[str, Any]:
    """Create a new trigger."""
    tenant_ctx = _require_tenant(request)
    store = _get_store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="Trigger store unavailable")

    spec = _build_spec(body.spec)

    # Push-webhook triggers: the path token now authenticates third-party
    # delivery on its own, so it must be strong. Client-chosen tokens were
    # accepted at any length ("abc") and none was issued when omitted.
    if spec.trigger_type.value in _push_webhook_types():
        if not spec.webhook_token:
            import secrets

            spec.webhook_token = secrets.token_urlsafe(32)
        elif not _MIN_WEBHOOK_TOKEN_LEN <= len(spec.webhook_token) <= 64:  # column is 64
            raise HTTPException(
                status_code=422,
                detail=f"webhook_token must be {_MIN_WEBHOOK_TOKEN_LEN}-64 characters",
            )

    # Reject trigger types that have no runtime dispatch path — a tenant must not
    # be able to register a trigger that could never fire (2.W-10).
    from app.triggers.dispatch_map import is_supported, unsupported_reason

    if not is_supported(spec.trigger_type):
        raise HTTPException(status_code=422, detail=unsupported_reason(spec.trigger_type))

    # Fail fast on a misconfigured spec (missing/invalid type-specific fields) so a
    # trigger that could never fire correctly is never persisted.
    from app.triggers.validation import validate_spec

    try:
        validate_spec(spec, plan=str(getattr(tenant_ctx, "plan", "free") or "free"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Durable create + PLAN_MAX_TRIGGERS. The quota enforcer existed but was
    # never called, and ``store.create`` persisted fire-and-forget (a DB failure
    # still answered 201 with a trigger no other replica or restart would see).
    # ``create_async`` counts the tenant's rows in Postgres inside the INSERT's
    # transaction, so the cap holds across replicas.
    from app.triggers.quota import TriggerQuotaExceeded

    plan = str(getattr(tenant_ctx, "plan", "free") or "free")
    try:
        if _has_async(store, "create_async"):
            schedule_id = await store.create_async(
                spec=spec,
                tenant_ctx=tenant_ctx,
                goal_id=body.goal_id,
                agent_id=body.agent_id,
                goal_template=body.goal_template,
                quota_plan=plan,
            )
        else:
            schedule_id = store.create(
                spec=spec,
                tenant_ctx=tenant_ctx,
                goal_id=body.goal_id,
                agent_id=body.agent_id,
                goal_template=body.goal_template,
            )
    except TriggerQuotaExceeded as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    # Read back through the durable store (the cache is only this replica's).
    rec = await _store_get(store, schedule_id, tenant_ctx)
    if rec is None:
        raise HTTPException(status_code=500, detail="Failed to retrieve created trigger")
    return _serialize_record(rec)


# ── Event emission (declared BEFORE /{schedule_id} to avoid shadowing) ───────


@router.post("/events/{event_channel}", status_code=202)
async def emit_trigger_event(
    event_channel: str, request: Request, body: dict[str, Any] = Body(default_factory=dict)
) -> dict[str, Any]:
    """Publish a custom event that fires this tenant's matching EVENT triggers.

    The authenticated tenant is stamped onto the event server-side, so a tenant
    can only fire its own EVENT triggers.
    """
    tenant_ctx = _require_tenant(request)
    redis = getattr(request.app.state, "trigger_event_redis", None)
    if redis is None:
        raise HTTPException(status_code=503, detail="Event bus unavailable")
    from app.triggers.bus import TriggerBusPublishError
    from app.triggers.consumers.event import publish_trigger_event

    try:
        await publish_trigger_event(
            redis, event_channel=event_channel, tenant_id=tenant_ctx.tenant_id, payload=body
        )
    except TriggerBusPublishError as exc:
        # Not durably on the trigger stream (TRG-18): never a 202 for a lost event.
        logger.warning("trigger_event_publish_failed", channel=event_channel, error=str(exc))
        raise HTTPException(status_code=503, detail="Event bus unavailable") from exc
    return {"published": True, "event_channel": event_channel}


# ── DLQ routes (must be declared BEFORE /{schedule_id} to avoid shadowing) ───


@router.get("/dlq", response_model=list[dict[str, Any]])
async def list_dlq(request: Request) -> list[dict[str, Any]]:
    """Return DLQ entries for the tenant."""
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        return []
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    # RLS context is required, not optional: trigger_dlq is FORCE-protected, so
    # without app.tenant_id every row is filtered out under the app's own role.
    async with (
        db() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
    ):
        rows = await session.execute(
            text(
                "SELECT id, trigger_id, failure_type, error_message, retry_count, "
                "next_retry_at, created_at, resolved_at FROM trigger_dlq "
                "WHERE tenant_id = :tid ORDER BY created_at DESC LIMIT 100"
            ),
            {"tid": tenant_ctx.tenant_id},
        )
        return [dict(r._mapping) for r in rows]


@router.post("/dlq/{dlq_id}/retry", status_code=202)
async def retry_dlq_entry(dlq_id: str, request: Request) -> dict[str, Any]:
    """Re-queue a DLQ entry for another delivery attempt.

    Records the attempt on the entry itself (``retry_count`` + ``next_retry_at``
    from :data:`~app.triggers.dlq.RETRY_DELAYS`) and re-dispatches the stored
    ``raw_payload`` through the live trigger dispatcher. The UPDATE is scoped by
    ``tenant_id`` *and* runs under RLS, so another tenant's entry is invisible
    and answers 404 rather than reporting a retry that never happened.
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context
    from app.triggers.dlq import RETRY_DELAYS

    tenant_ctx = _require_tenant(request)
    role = _require_trigger_permission(tenant_ctx, "fire")  # a retry re-fires
    db = _get_db(request)
    if db is None:
        raise HTTPException(status_code=503, detail="DLQ storage unavailable")

    async with (
        db() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
    ):
        row = (
            await session.execute(
                text(
                    "SELECT trigger_id, raw_payload, retry_count FROM trigger_dlq "
                    "WHERE id = :id AND tenant_id = :tid AND resolved_at IS NULL "
                    "FOR UPDATE"
                ),
                {"id": dlq_id, "tid": tenant_ctx.tenant_id},
            )
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="DLQ entry not found")

        trigger_id = str(row[0])
        raw_payload = row[1] if isinstance(row[1], dict) else {}
        attempt = int(row[2] or 0) + 1
        # Exponential backoff; attempts past the table stay on the last delay.
        delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)) - 1]
        next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)

        await session.execute(
            text(
                "UPDATE trigger_dlq SET retry_count = :n, next_retry_at = :next "
                "WHERE id = :id AND tenant_id = :tid"
            ),
            {"n": attempt, "next": next_retry_at, "id": dlq_id, "tid": tenant_ctx.tenant_id},
        )

    # Re-dispatch with the dispatcher's real signature (spec, payload, tenant_ctx).
    # The old call passed tenant_id=/payload= keywords, raised a TypeError that
    # contextlib.suppress swallowed, and answered "queued" without ever firing.
    # A per-attempt message_id gives the retry its own idempotency key so the
    # durable dedup gate does not drop it as a replay of the original firing.
    dispatched = False
    status_str = "scheduled"  # attempt recorded; nothing to re-fire right now
    skip_reason: str | None = None
    goal_id: str | None = None
    dispatcher = _get_dispatcher(request)
    store = _get_store(request)
    rec = await _store_get(store, trigger_id, tenant_ctx) if store is not None else None
    if dispatcher is not None and rec is not None:
        try:
            event = await dispatcher.dispatch(
                _spec_for_dispatch(rec),
                raw_payload,
                tenant_ctx,
                caller_role=role,
                message_id=f"dlq-retry:{dlq_id}:{attempt}",
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"DLQ retry recorded but re-dispatch failed: {str(exc)[:200]}",
            ) from exc
        skip_reason = getattr(event, "skip_reason", None)
        goal_id = getattr(event, "goal_id", None)
        dispatched = bool(getattr(event, "goal_created", False))
        status_str = "skipped" if skip_reason else ("dispatched" if dispatched else "failed")

    return {
        "status": status_str,
        "dlq_id": dlq_id,
        "trigger_id": trigger_id,
        "retry_count": attempt,
        "next_retry_at": next_retry_at.isoformat(),
        "dispatched": dispatched,
        "goal_id": goal_id,
        "skip_reason": skip_reason,
    }


# ── Per-trigger routes ────────────────────────────────────────────────────────


@router.get("/{schedule_id}", response_model=dict[str, Any])
async def get_trigger(schedule_id: str, request: Request) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    store = _get_store(request)
    rec = await _store_get(store, schedule_id, tenant_ctx)
    if rec is None:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return _serialize_record(rec)


@router.delete("/{schedule_id}", status_code=204)
async def delete_trigger(schedule_id: str, request: Request) -> None:
    tenant_ctx = _require_tenant(request)
    store = _get_store(request)
    if store is None:
        raise HTTPException(status_code=404, detail="Trigger not found")
    # Durable delete (the sync ``delete`` removed the DB row fire-and-forget).
    if _has_async(store, "delete_async"):
        deleted = await store.delete_async(schedule_id, tenant_ctx=tenant_ctx)
    else:
        deleted = store.delete(schedule_id, tenant_ctx=tenant_ctx)
    if not deleted:
        raise HTTPException(status_code=404, detail="Trigger not found")


@router.post("/{schedule_id}/pause", response_model=dict[str, Any])
async def pause_trigger(schedule_id: str, request: Request) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    store = _get_store(request)
    rec = await _set_paused(store, schedule_id, tenant_ctx, paused=True)
    if rec is None:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return _serialize_record(rec)


@router.post("/{schedule_id}/resume", response_model=dict[str, Any])
async def resume_trigger(schedule_id: str, request: Request) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    store = _get_store(request)
    # Resume used to set ``rec["paused"] = False`` on this process's copy only:
    # Redis (read by the beat) and Postgres stayed paused, so the trigger never
    # fired again and a restart reloaded it as paused. Persist like pause does.
    rec = await _set_paused(store, schedule_id, tenant_ctx, paused=False)
    if rec is None:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return _serialize_record(rec)


async def _set_paused(
    store: Any, schedule_id: str, tenant_ctx: TenantContext, *, paused: bool
) -> dict[str, Any] | None:
    if store is None:
        return None
    if _has_async(store, "set_paused_async"):
        rec: dict[str, Any] | None = await store.set_paused_async(
            schedule_id, paused=paused, tenant_ctx=tenant_ctx
        )
        return rec
    method = store.pause if paused else store.resume
    if not method(schedule_id, tenant_ctx=tenant_ctx):
        return None
    got: dict[str, Any] | None = store.get(schedule_id, tenant_ctx=tenant_ctx)
    return got


@router.post("/{schedule_id}/simulate", response_model=dict[str, Any])
async def simulate_trigger(
    schedule_id: str, request: Request, body: SimulateRequest
) -> dict[str, Any]:
    """Simulate a trigger firing without actually creating a goal."""
    tenant_ctx = _require_tenant(request)
    store = _get_store(request)
    rec = await _store_get(store, schedule_id, tenant_ctx)
    if rec is None:
        raise HTTPException(status_code=404, detail="Trigger not found")

    spec = _spec_for_dispatch(rec)
    sample = body.payload or get_sample_payload(spec.trigger_type)

    dispatcher = _get_dispatcher(request)
    if dispatcher is not None:
        result = await dispatcher.dispatch(spec, sample, tenant_ctx, simulation=True)
        import dataclasses

        if dataclasses.is_dataclass(result) and not isinstance(result, type):
            return dataclasses.asdict(result)
        return vars(result)

    return {
        "trigger_type": spec.trigger_type.value,
        "sample_payload": sample,
        "would_fire": True,
        "simulated": True,
    }


@router.post("/{schedule_id}/fire", response_model=dict[str, Any])
async def fire_trigger_now(schedule_id: str, request: Request, body: FireRequest) -> dict[str, Any]:
    """Fire a trigger immediately, creating a real goal."""
    tenant_ctx = _require_tenant(request)
    role = _require_trigger_permission(tenant_ctx, "fire")
    store = _get_store(request)
    rec = await _store_get(store, schedule_id, tenant_ctx)
    if rec is None:
        raise HTTPException(status_code=404, detail="Trigger not found")
    if rec.get("paused"):
        raise HTTPException(status_code=409, detail="Trigger is paused")

    spec = _spec_for_dispatch(rec)
    sample = body.payload or get_sample_payload(spec.trigger_type)

    dispatcher = _get_dispatcher(request)
    if dispatcher is None:
        raise HTTPException(status_code=503, detail="Dispatcher unavailable")

    event = await dispatcher.dispatch(
        spec,
        sample,
        tenant_ctx,
        caller_role=role,
        scheduled_fire_time=getattr(body, "scheduled_fire_time", None),
    )
    return {
        "goal_id": getattr(event, "goal_id", None),  # WT-2/G3: field is goal_id
        "goal_created": getattr(event, "goal_created", None),
        "skip_reason": getattr(event, "skip_reason", None),
        "fired_at": getattr(event, "fired_at", None),
    }


@router.get("/{schedule_id}/events", response_model=list[dict[str, Any]])
async def list_trigger_events(
    schedule_id: str, request: Request, limit: int = 50
) -> list[dict[str, Any]]:
    """Return recent trigger events from the audit table, scoped to the tenant.

    The ``tenant_id`` predicate is load-bearing, not belt-and-braces: this query
    used to filter on ``trigger_id`` alone with no RLS context, so any
    authenticated caller who knew another tenant's trigger id read that tenant's
    firing history — including the raw inbound webhook ``payload``. Both the
    explicit predicate and the RLS GUC are now in place.
    """
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        return []
    limit = max(1, min(limit, 500))

    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with (
        db() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
    ):
        rows = await session.execute(
            text(
                "SELECT id AS event_id, trigger_id, trigger_type, idempotency_key, "
                "payload, goal_id, goal_created, skip_reason, fired_at "
                "FROM trigger_events "
                "WHERE trigger_id = :tid AND tenant_id = :tenant "
                "ORDER BY fired_at DESC LIMIT :lim"
            ),
            {"tid": schedule_id, "tenant": tenant_ctx.tenant_id, "lim": limit},
        )
        return [dict(r._mapping) for r in rows]


# ── PATCH (partial update) ────────────────────────────────────────────────────


class UpdateTriggerRequest(BaseModel):
    goal_template: str | None = None
    paused: bool | None = None
    spec: TriggerSpecRequest | None = None


@router.patch("/{schedule_id}", response_model=dict[str, Any])
async def update_trigger(
    schedule_id: str, request: Request, body: UpdateTriggerRequest
) -> dict[str, Any]:
    """Partially update a trigger (goal_template, paused, or spec fields)."""
    tenant_ctx = _require_tenant(request)
    store = _get_store(request)
    rec = await _store_get(store, schedule_id, tenant_ctx)
    if rec is None:
        raise HTTPException(status_code=404, detail="Trigger not found")

    new_spec: TriggerSpec | None = None
    if body.spec is not None:
        new_spec = _build_spec(body.spec)
        from app.triggers.dispatch_map import is_supported, unsupported_reason

        if not is_supported(new_spec.trigger_type):
            raise HTTPException(status_code=422, detail=unsupported_reason(new_spec.trigger_type))
        # Validate the replacement spec the same way create does — an update must
        # not be able to persist a misconfigured trigger either.
        from app.triggers.validation import validate_spec

        try:
            validate_spec(new_spec, plan=str(getattr(tenant_ctx, "plan", "free") or "free"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # PATCH used to assign onto this process's record only — nothing reached
    # Redis (the beat kept the old cron/template) or Postgres (a restart or any
    # other replica reverted the edit). ``update_async`` persists all three.
    if _has_async(store, "update_async"):
        updated = await store.update_async(
            schedule_id,
            tenant_ctx=tenant_ctx,
            goal_template=body.goal_template,
            paused=body.paused,
            spec=new_spec,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Trigger not found")
        return _serialize_record(updated)

    if body.goal_template is not None:
        rec["goal_template"] = body.goal_template
    if body.paused is not None:
        rec["paused"] = body.paused
    if new_spec is not None:
        rec["spec"] = new_spec
    return _serialize_record(rec)


# ── Rotate secret ─────────────────────────────────────────────────────────────


@router.post("/{schedule_id}/rotate-secret", response_model=dict[str, Any])
async def rotate_secret(schedule_id: str, request: Request) -> dict[str, Any]:
    """Initiate webhook secret rotation (dual-secret grace period)."""
    tenant_ctx = _require_tenant(request)
    store = _get_store(request)
    rec = await _store_get(store, schedule_id, tenant_ctx)
    if rec is None:
        raise HTTPException(status_code=404, detail="Trigger not found")

    from app.triggers.webhooks.rotation import WebhookSecretRotation

    grace_period_seconds = 300
    new_secret = WebhookSecretRotation().generate_secret()
    # The new secret used to be set on this process's spec only (the store's
    # persistence was never called), so other replicas and every restart kept
    # verifying with the OLD secret — or with none. It is now stored encrypted,
    # with the previous secret honoured for the grace window, and is returned
    # exactly once, here.
    if _has_async(store, "update_secret_async"):
        if not await store.update_secret_async(
            schedule_id,
            new_secret=new_secret,
            tenant_id=tenant_ctx.tenant_id,
            grace_period_seconds=grace_period_seconds,
        ):
            raise HTTPException(status_code=404, detail="Trigger not found")
    else:
        spec = rec.get("spec")
        if spec is not None:
            spec.webhook_signature_secret = new_secret
    return {
        "trigger_id": schedule_id,
        "new_secret": new_secret,
        "grace_period_seconds": grace_period_seconds,
        "status": "rotation_started",
    }


# ── Validate condition (CEL) ──────────────────────────────────────────────────


class ValidateConditionRequest(BaseModel):
    expression: str
    test_payload: dict[str, Any] = {}


@router.post("/validate-condition", response_model=dict[str, Any])
async def validate_condition(body: ValidateConditionRequest) -> dict[str, Any]:
    """Validate a CEL condition expression and evaluate it against a test payload."""
    from app.triggers.condition.evaluator import CELEvaluator

    evaluator = CELEvaluator()
    try:
        result = evaluator.evaluate(body.expression, body.test_payload)
        return {"valid": True, "error_message": None, "evaluated_to": result}
    except Exception as exc:
        return {"valid": False, "error_message": str(exc), "evaluated_to": None}


# ── Typed webhook ingest ──────────────────────────────────────────────────────


async def _mark_sns_confirmed(store: Any, records: list[dict[str, Any]], caller: Any) -> None:
    """Record ``sns_subscription_confirmed_at`` so the UI can show the SNS
    subscription as confirmed (TRG-25). Best effort: the confirmation itself
    already succeeded at AWS."""
    import dataclasses
    from datetime import UTC, datetime

    update = getattr(store, "update_async", None)
    if update is None:
        return
    confirmed_at = datetime.now(UTC).isoformat()
    for rec in records:
        spec = rec.get("spec")
        schedule_id = str(rec.get("schedule_id") or "")
        if not schedule_id or not dataclasses.is_dataclass(spec) or isinstance(spec, type):
            continue
        try:
            await update(
                schedule_id,
                tenant_ctx=caller,
                spec=dataclasses.replace(spec, sns_subscription_confirmed_at=confirmed_at),
            )
        except Exception as exc:
            logger.warning(
                "sns_confirmed_state_not_saved", schedule_id=schedule_id, error=str(exc)[:200]
            )


@router.post("/webhooks/{webhook_type}/{token}")
async def receive_typed_webhook(webhook_type: str, token: str, request: Request) -> Any:
    """Unified typed webhook endpoint — routes GitHub, Stripe, Jira, etc."""
    body_bytes = await request.body()
    try:
        body = __import__("json").loads(body_bytes)
    except Exception:
        body = {}

    # Determine signature header per type
    sig_header_map = {
        "github": "x-hub-signature-256",
        "stripe": "stripe-signature",
        "jira": "x-hub-signature",
        "pagerduty": "x-pagerduty-signature",
        "linear": "linear-signature",
        "sentry": "sentry-hook-signature",
        # TRG-24: the headers Slack and Teams actually sign with.
        "slack": "x-slack-signature",
        "teams": "authorization",
        # TRG-28: Grafana's configured signature header default, Atlassian Data
        # Center's X-Hub-Signature, Salesforce's x-signature.
        "grafana": "x-grafana-alerting-signature",
        "confluence": "x-hub-signature",
        "salesforce": "x-signature",
    }
    sig_header = request.headers.get(sig_header_map.get(webhook_type, "x-signature"), "")

    # Verify signature if store has a matching trigger with a secret
    store = _get_store(request)
    dispatcher = _get_dispatcher(request)
    if store is None or dispatcher is None:
        # Used to answer "accepted" while doing nothing — the sender then never
        # retried a delivery that was dropped.
        raise HTTPException(status_code=503, detail="Webhook triggers are unavailable")

    from app.triggers.webhooks.verifier import WebhookSignatureVerifier

    verifier = WebhookSignatureVerifier()

    # Map webhook_type → TriggerType value (single source of truth: dispatch_map)
    from app.triggers.dispatch_map import WEBHOOK_TYPE_MAP

    trigger_type = WEBHOOK_TYPE_MAP.get(webhook_type, "webhook")

    # Enrich payload with parsed data
    from app.triggers.webhooks import parsers as _parsers

    parse_map = {
        "github": lambda: _parsers.GitHubWebhookPayload.parse(dict(request.headers), body).__dict__,
        "stripe": lambda: _parsers.StripeWebhookPayload.parse(body).__dict__,
        "jira": lambda: _parsers.JiraWebhookPayload.parse(body).__dict__,
        "slack": lambda: _parsers.SlackEventPayload.parse(body).__dict__,
        "pagerduty": lambda: _parsers.PagerDutyWebhookPayload.parse(body).__dict__,
        "linear": lambda: _parsers.LinearWebhookPayload.parse(body).__dict__,
    }
    parse_fn = parse_map.get(webhook_type)
    enriched: dict = {**body, "webhook_type": webhook_type}
    if parse_fn:
        try:
            parsed = parse_fn()
            enriched.update({k: v for k, v in parsed.items() if k != "raw"})
        except Exception:
            pass

    # Tenant = the owner of the path TOKEN. Originally the tenant came from an
    # ``X-Tenant-ID`` header (cross-tenant firing); then from the caller's API key
    # — which GitHub/Stripe/Jira/... can never send, so real deliveries always
    # got 401 (the route was not even past TenantMiddleware). The token is the
    # credential a third party holds, so it alone selects the tenant: resolved
    # pre-auth (constant-time; DB fallback via the maintenance session), after
    # which everything runs as that tenant. Any API-key tenant or header is
    # ignored. A trigger with a signing secret still requires a valid signature.
    import hmac

    if len(token) < _MIN_WEBHOOK_TOKEN_LEN:
        raise HTTPException(status_code=404, detail="No trigger matches this webhook token")
    from app.triggers.store import ScheduleStoreUnavailableError

    finder = getattr(store, "find_tenant_by_webhook_token", None)
    try:
        tenant_id = (
            await finder(
                token, system_db=getattr(request.app.state, "system_db_session_factory", None)
            )
            if finder is not None
            else None
        )
    except ScheduleStoreUnavailableError as exc:
        # TRG-27: an outage is a retryable 503, never a (permanent) 404.
        raise HTTPException(status_code=503, detail="Webhook triggers unavailable; retry") from exc
    if not tenant_id:
        raise HTTPException(status_code=404, detail="No trigger matches this webhook token")
    caller = await _webhook_tenant_ctx(request, tenant_id)

    # TRG-25: CloudWatch alarms arrive through SNS. Every SNS message is signed
    # by AWS; the SubscriptionConfirmation must be confirmed (or no alarm is ever
    # delivered) and a Notification wraps the alarm JSON in ``Message``.
    from app.triggers.webhooks import sns as _sns

    sns_type = str(body.get("Type")) if webhook_type == "cloudwatch" and _sns.is_sns_message(
        body
    ) else ""
    if sns_type:
        try:
            await _sns.verify_sns_message(body)
        except _sns.SNSVerificationError as exc:
            raise HTTPException(status_code=401, detail=f"Invalid SNS message: {exc}") from exc
        if sns_type == "Notification":
            enriched = {**_sns.notification_payload(body), "webhook_type": webhook_type}
    sns_handshakes: list[dict[str, Any]] = []

    # TRG-26: Salesforce outbound messages are SOAP XML (they parsed to {}) and
    # are redelivered until acknowledged with a SOAP <Ack>true</Ack>.
    from app.triggers.webhooks import salesforce as _sf

    sf_message_id: str | None = None
    if webhook_type == "salesforce" and _sf.looks_like_xml(body_bytes):
        try:
            sf_payload = _sf.parse_outbound_message(body_bytes)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid outbound message: {exc}") from exc
        enriched = {**sf_payload, "webhook_type": webhook_type}
        sf_message_id = _sf.message_id(sf_payload) or None

    try:
        triggers = await store.find_by_type_async(trigger_type, tenant_id=tenant_id, strict=True)
    except ScheduleStoreUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Webhook triggers unavailable; retry") from exc
    matched = 0
    failed = 0
    for trigger in triggers:
        # Bind the record-level goal_template / agent refs onto the spec (as the
        # manual fire and simulate paths do) so an inbound webhook renders the
        # tenant's goal template instead of the generic "Trigger fired: <type>".
        if isinstance(trigger, dict) and "spec" in trigger:
            spec = _spec_for_dispatch(trigger)
        else:
            spec = trigger.get("spec", trigger) if isinstance(trigger, dict) else trigger
        stored_token = str(getattr(spec, "webhook_token", "") or "")
        if not stored_token or not hmac.compare_digest(stored_token.encode(), token.encode()):
            continue
        secret = getattr(spec, "webhook_signature_secret", "") or ""
        if secret:
            # rotate-secret promised a dual-secret grace period, but only the
            # new secret was ever checked. The previous one is now accepted
            # until its (persisted) grace deadline.
            candidates = [secret]
            prev = str(trigger.get("previous_webhook_secret", "") or "") if isinstance(
                trigger, dict
            ) else ""
            grace_until = float(trigger.get("secret_grace_until", 0) or 0) if isinstance(
                trigger, dict
            ) else 0.0
            import time as _time

            if prev and _time.time() < grace_until:
                candidates.append(prev)
            verified = False
            if sig_header:
                for candidate in candidates:
                    if await verifier.verify_for_type(
                        webhook_type,
                        body_bytes,
                        sig_header,
                        candidate,
                        headers=request.headers,
                    ):
                        verified = True
                        break
            if not verified:
                raise HTTPException(status_code=401, detail="Invalid webhook signature")
        if (
            webhook_type == "slack"
            and isinstance(body, dict)
            and body.get("type") == "url_verification"
        ):
            # TRG-24: Slack saves an Events API URL only after it echoes the
            # (signed) challenge; it is a handshake, not an event to fire on.
            return {"challenge": str(body.get("challenge", ""))}
        matched += 1
        if sns_type and sns_type != "Notification":
            # A subscription handshake, not an alarm: nothing to fire.
            if isinstance(trigger, dict):
                sns_handshakes.append(trigger)
            continue
        try:
            if sns_type:
                # SNS redelivers until it gets a 2xx: dedup on its MessageId.
                await dispatcher.dispatch(
                    spec, enriched, caller, message_id=str(body.get("MessageId") or "")
                )
            elif sf_message_id:
                # Salesforce redelivers the same notification ids until acked.
                await dispatcher.dispatch(spec, enriched, caller, message_id=sf_message_id)
            else:
                await dispatcher.dispatch(spec, enriched, caller)
        except Exception as exc:
            # Was suppressed and still answered "accepted", so the platform never
            # redelivered an event that fired nothing.
            failed += 1
            logger.error(
                "typed_webhook_dispatch_failed",
                webhook_type=webhook_type,
                tenant_id=tenant_id,
                error=str(exc)[:200],
            )

    if not matched:
        raise HTTPException(status_code=404, detail="No trigger matches this webhook token")
    if sns_type == "SubscriptionConfirmation":
        try:
            await _sns.confirm_subscription(body)
        except _sns.SNSVerificationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            # 5xx so SNS retries the confirmation.
            raise HTTPException(
                status_code=502, detail="Could not confirm the SNS subscription; retry"
            ) from exc
        await _mark_sns_confirmed(store, sns_handshakes, caller)
        return {"status": "subscription_confirmed", "webhook_type": webhook_type}
    if sns_type == "UnsubscribeConfirmation":
        return {"status": "unsubscribe_acknowledged", "webhook_type": webhook_type}
    if failed == matched:
        raise HTTPException(status_code=503, detail="Webhook could not be dispatched; retry")
    if webhook_type == "salesforce" and "notifications" in enriched:
        from fastapi.responses import Response as _Response

        return _Response(content=_sf.ACK_XML, media_type="text/xml")
    return {
        "status": "accepted",
        "webhook_type": webhook_type,
        "dispatched": matched - failed,
        "failed": failed,
    }
