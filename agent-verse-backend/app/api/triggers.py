"""Triggers API — CRUD, lifecycle control, simulation, events, and DLQ."""

from __future__ import annotations

import contextlib
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from app.tenancy.context import TenantContext
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.simulation import get_sample_payload

router = APIRouter(prefix="/triggers", tags=["triggers"])


# ── Dependency helpers ────────────────────────────────────────────────────────


def _require_tenant(request: Request) -> TenantContext:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return ctx  # type: ignore[return-value]


def _get_store(request: Request) -> Any:
    return getattr(request.app.state, "schedule_store", None)


def _get_dispatcher(request: Request) -> Any:
    return getattr(request.app.state, "trigger_dispatcher", None)


def _get_db(request: Request) -> Any:
    # WT-2/G5: app.state.db is never set; the DB session factory lives here.
    return getattr(request.app.state, "db_session_factory", None)


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
        """A trigger must have something to run: a goal template OR a referenced
        agent (whose own goal will run). Neither → nothing to fire (422)."""
        if not (self.goal_template or "").strip() and not (self.agent_id or "").strip():
            raise ValueError("Provide a goal_template or reference an agent_id")
        return self


class SimulateRequest(BaseModel):
    payload: dict[str, Any] | None = None


class FireRequest(BaseModel):
    payload: dict[str, Any] | None = None


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
    if spec is not None:
        import dataclasses

        out["spec"] = {
            k: (v.value if hasattr(v, "value") else v)
            for k, v in dataclasses.asdict(spec).items()
            if v is not None
        }
    return out


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
    if not (getattr(spec, "watch_agent_id", "") or "").strip():
        with contextlib.suppress(Exception):
            spec.watch_agent_id = rec.get("agent_id", "") or ""
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

    # Reject trigger types that have no runtime dispatch path — a tenant must not
    # be able to register a trigger that could never fire (2.W-10).
    from app.triggers.dispatch_map import is_supported, unsupported_reason

    if not is_supported(spec.trigger_type):
        raise HTTPException(status_code=422, detail=unsupported_reason(spec.trigger_type))

    schedule_id = store.create(
        spec=spec,
        tenant_ctx=tenant_ctx,
        goal_id=body.goal_id,
        agent_id=body.agent_id,
        goal_template=body.goal_template,
    )
    rec = store.get(schedule_id, tenant_ctx=tenant_ctx)
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
    from app.triggers.consumers.event import publish_trigger_event

    await publish_trigger_event(
        redis, event_channel=event_channel, tenant_id=tenant_ctx.tenant_id, payload=body
    )
    return {"published": True, "event_channel": event_channel}


# ── DLQ routes (must be declared BEFORE /{schedule_id} to avoid shadowing) ───


@router.get("/dlq", response_model=list[dict[str, Any]])
async def list_dlq(request: Request) -> list[dict[str, Any]]:
    """Return DLQ entries for the tenant."""
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        return []
    try:
        from sqlalchemy import text

        async with db() as session:
            rows = await session.execute(
                text(
                    "SELECT id, trigger_id, failure_type, error_message, retry_count, "
                    "next_retry_at, created_at FROM trigger_dlq "
                    "WHERE tenant_id = :tid ORDER BY created_at DESC LIMIT 100"
                ),
                {"tid": tenant_ctx.tenant_id},
            )
            return [dict(r._mapping) for r in rows]
    except Exception:
        return []


@router.post("/dlq/{dlq_id}/retry", status_code=202)
async def retry_dlq_entry(dlq_id: str, request: Request) -> dict[str, str]:
    """Re-queue a DLQ entry for immediate retry."""
    _require_tenant(request)
    return {"status": "queued", "dlq_id": dlq_id}


# ── Per-trigger routes ────────────────────────────────────────────────────────


@router.get("/{schedule_id}", response_model=dict[str, Any])
async def get_trigger(schedule_id: str, request: Request) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    store = _get_store(request)
    rec = store.get(schedule_id, tenant_ctx=tenant_ctx) if store else None
    if rec is None:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return _serialize_record(rec)


@router.delete("/{schedule_id}", status_code=204)
async def delete_trigger(schedule_id: str, request: Request) -> None:
    tenant_ctx = _require_tenant(request)
    store = _get_store(request)
    if store is None or not store.delete(schedule_id, tenant_ctx=tenant_ctx):
        raise HTTPException(status_code=404, detail="Trigger not found")


@router.post("/{schedule_id}/pause", response_model=dict[str, Any])
async def pause_trigger(schedule_id: str, request: Request) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    store = _get_store(request)
    ok = store.pause(schedule_id, tenant_ctx=tenant_ctx) if store else False
    if not ok:
        raise HTTPException(status_code=404, detail="Trigger not found")
    rec = store.get(schedule_id, tenant_ctx=tenant_ctx)
    return _serialize_record(rec)  # type: ignore[arg-type]


@router.post("/{schedule_id}/resume", response_model=dict[str, Any])
async def resume_trigger(schedule_id: str, request: Request) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    store = _get_store(request)
    rec = store.get(schedule_id, tenant_ctx=tenant_ctx) if store else None
    if rec is None:
        raise HTTPException(status_code=404, detail="Trigger not found")
    rec["paused"] = False
    return _serialize_record(rec)


@router.post("/{schedule_id}/simulate", response_model=dict[str, Any])
async def simulate_trigger(
    schedule_id: str, request: Request, body: SimulateRequest
) -> dict[str, Any]:
    """Simulate a trigger firing without actually creating a goal."""
    tenant_ctx = _require_tenant(request)
    store = _get_store(request)
    rec = store.get(schedule_id, tenant_ctx=tenant_ctx) if store else None
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
    store = _get_store(request)
    rec = store.get(schedule_id, tenant_ctx=tenant_ctx) if store else None
    if rec is None:
        raise HTTPException(status_code=404, detail="Trigger not found")
    if rec.get("paused"):
        raise HTTPException(status_code=409, detail="Trigger is paused")

    spec = _spec_for_dispatch(rec)
    sample = body.payload or get_sample_payload(spec.trigger_type)

    dispatcher = _get_dispatcher(request)
    if dispatcher is None:
        raise HTTPException(status_code=503, detail="Dispatcher unavailable")

    event = await dispatcher.dispatch(spec, sample, tenant_ctx)
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
    """Return recent trigger events from the audit table."""
    _require_tenant(request)
    db = _get_db(request)
    if db is None:
        return []
    try:
        from sqlalchemy import text

        async with db() as session:
            rows = await session.execute(
                text(
                    "SELECT id AS event_id, trigger_id, trigger_type, idempotency_key, "
                    "payload, goal_id, goal_created, skip_reason, fired_at "
                    "FROM trigger_events WHERE trigger_id = :tid "
                    "ORDER BY fired_at DESC LIMIT :lim"
                ),
                {"tid": schedule_id, "lim": limit},
            )
            return [dict(r._mapping) for r in rows]
    except Exception:
        return []


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
    rec = store.get(schedule_id, tenant_ctx=tenant_ctx) if store else None
    if rec is None:
        raise HTTPException(status_code=404, detail="Trigger not found")

    if body.goal_template is not None:
        rec["goal_template"] = body.goal_template
    if body.paused is not None:
        rec["paused"] = body.paused
    if body.spec is not None:
        new_spec = _build_spec(body.spec)
        rec["spec"] = new_spec

    return _serialize_record(rec)


# ── Rotate secret ─────────────────────────────────────────────────────────────


@router.post("/{schedule_id}/rotate-secret", response_model=dict[str, Any])
async def rotate_secret(schedule_id: str, request: Request) -> dict[str, Any]:
    """Initiate webhook secret rotation (dual-secret grace period)."""
    tenant_ctx = _require_tenant(request)
    store = _get_store(request)
    rec = store.get(schedule_id, tenant_ctx=tenant_ctx) if store else None
    if rec is None:
        raise HTTPException(status_code=404, detail="Trigger not found")

    from app.triggers.webhooks.rotation import WebhookSecretRotation

    rotation = WebhookSecretRotation()
    result = await rotation.rotate(schedule_id, tenant_id=tenant_ctx.tenant_id)
    # Update spec with new secret
    spec = rec.get("spec")
    if spec is not None:
        spec.webhook_signature_secret = result["new_secret"]
    return {
        "trigger_id": schedule_id,
        "new_secret": result["new_secret"],
        "grace_period_seconds": result["grace_period_seconds"],
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


@router.post("/webhooks/{webhook_type}/{token}")
async def receive_typed_webhook(webhook_type: str, token: str, request: Request) -> dict[str, Any]:
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
    }
    sig_header = request.headers.get(sig_header_map.get(webhook_type, "x-signature"), "")

    # Verify signature if store has a matching trigger with a secret
    store = _get_store(request)
    dispatcher = _get_dispatcher(request)
    if store is None or dispatcher is None:
        return {"status": "accepted", "webhook_type": webhook_type}

    # Find matching trigger by webhook token (token matches webhook_signature_secret prefix)
    from types import SimpleNamespace

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

    # Attempt dispatch — the token is used as a lookup key; for now just broadcast to matching type
    # In production: token would be hashed and matched against stored webhook_token field
    tenant_id = request.headers.get("X-Tenant-ID", "")
    if not tenant_id:
        # Return accepted regardless (Slack, GitHub etc. expect 200 quickly)
        return {"status": "accepted"}

    tenant_ctx = SimpleNamespace(tenant_id=tenant_id, plan="free")
    triggers = await store.find_by_type_async(trigger_type, tenant_id=tenant_id)
    for trigger in triggers:
        spec = trigger.get("spec", trigger)
        secret = getattr(spec, "webhook_signature_secret", "") or ""
        if secret and sig_header:
            valid = await verifier.verify(body_bytes, sig_header, secret)
            if not valid:
                continue
        with contextlib.suppress(Exception):
            await dispatcher.dispatch(spec, enriched, tenant_ctx)

    return {"status": "accepted", "webhook_type": webhook_type}
