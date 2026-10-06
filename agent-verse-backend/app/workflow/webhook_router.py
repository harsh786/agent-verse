"""Public workflow webhook endpoint — external systems POST here to fire a run.

Authentication is the HMAC token in the path (no tenant API key), so this router
is exempt from TenantMiddleware (its ``/wf-hooks/`` prefix is in _BYPASS_PREFIXES).
The token both identifies the workflow and proves the caller holds the URL that
``publish`` issued.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.observability.logging import get_logger
from app.workflow.runner import WorkflowValidationError
from app.workflow.trigger_extract import extract_triggers
from app.workflow.webhook_tokens import verify_webhook_token_versioned

_log = get_logger(__name__)
router = APIRouter(prefix="/wf-hooks", tags=["workflow-webhooks"])

# Largest accepted webhook body (matches the runner's trigger payload limit).
MAX_WEBHOOK_BODY_BYTES = 1_048_576
# Headers senders use to identify a delivery (retries repeat the same value).
_DELIVERY_ID_HEADERS = (
    "idempotency-key",
    "x-delivery-id",
    "x-request-id",
    "x-github-delivery",
    "x-webhook-id",
)


async def _read_capped_body(request: Request, cap: int) -> bytes:
    """The request body, or 413 once it exceeds ``cap`` bytes (streamed, so an
    oversized body is never buffered whole)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise HTTPException(status_code=413, detail=f"Webhook body exceeds {cap} bytes")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > cap:
            raise HTTPException(status_code=413, detail=f"Webhook body exceeds {cap} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _delivery_key(request: Request) -> str | None:
    for header in _DELIVERY_ID_HEADERS:
        value = (request.headers.get(header) or "").strip()
        if value:
            return f"webhook:{value[:200]}"
    return None


async def _enforce_rate_limit(
    request: Request, runner: Any, *, tenant_id: str, workflow_id: str
) -> None:
    """429 once this workflow's webhook exceeds its plan's per-minute limit.

    One sliding window per (tenant, workflow) in the app's rate-limit Redis,
    shared by every replica (``SlidingWindowRateLimiter``); a leaked URL can no
    longer launch unlimited billed runs. While Redis is unreachable each replica
    enforces its share of the limit (the tenancy middleware's degraded budget)
    — never fail open. (This imported the per-process ``RateLimiter`` that
    RATE-02 deleted, so every delivery answered 500.)"""
    from app.tenancy.context import PLAN_LIMITS, PlanTier
    from app.tenancy.middleware import _check_rate_limit_with_fallback
    from app.tenancy.rate_limiter import SlidingWindowRateLimiter
    from app.tenancy.store import TenantScopedStore

    plan = "free"
    tier_of = getattr(runner, "_get_plan_tier", None)
    if tier_of is not None:
        try:
            plan = str(await tier_of(tenant_id))
        except Exception:
            plan = "free"
    try:
        limit = PLAN_LIMITS[PlanTier(plan)].requests_per_minute
    except (KeyError, ValueError):
        limit = PLAN_LIMITS[PlanTier.FREE].requests_per_minute
    redis = getattr(request.app.state, "_rate_limiter_redis", None)
    allowed: bool | None = None
    if redis is not None:
        try:
            limiter = SlidingWindowRateLimiter(
                store=TenantScopedStore(redis=redis, tenant_id=tenant_id)
            )
            allowed, _, _ = await limiter.check_and_record(f"wfhook:{workflow_id}", limit=limit)
        except Exception as exc:
            _log.warning("webhook_rate_limit_redis_unavailable", error=str(exc)[:200])
    if allowed is None:
        allowed = await _check_rate_limit_with_fallback(
            f"wfhook:{tenant_id}:{workflow_id}", None, rpm_limit=limit
        )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Webhook rate limit exceeded ({limit} deliveries per minute)",
            headers={"Retry-After": "60"},
        )


async def _verify_declared_auth(
    request: Request,
    triggers: list[dict[str, Any]],
    raw: bytes,
    *,
    svc: Any,
    tenant_id: str,
) -> None:
    """Enforce ``trigger.webhook.auth: hmac`` (B2-9).

    The DSL accepted ``auth: hmac`` + ``hmac_secret`` but nothing read them, so
    an unsigned or forged delivery started a run. Any webhook/api trigger that
    declares hmac requires an HMAC-SHA256 signature of the body
    (``X-AgentVerse-Signature`` / ``X-Signature``; with ``X-Webhook-Timestamp``
    over ``"{ts}.{body}"``, replay-windowed). Declared without a secret it fails
    closed. ``bearer`` / ``none`` keep the path token as the credential.

    The stored secret is vault-encrypted (B2-OPEN-1) and opened only here; one
    that cannot be opened (vault key missing / rotated away) refuses the
    delivery with a retryable 503 — never a check against a blank secret.
    """
    from app.triggers.webhooks.ingress import check_signature
    from app.workflow.webhook_secrets import open_secret

    for trig in triggers:
        if trig.get("type") not in ("webhook", "api"):
            continue
        cfg = trig.get("webhook") if isinstance(trig.get("webhook"), dict) else trig
        if str((cfg or {}).get("auth") or "").lower() != "hmac":
            continue
        stored = (cfg or {}).get("hmac_secret") or ""
        opener = getattr(svc, "open_webhook_secret", None)
        try:
            secret = (
                await opener(tenant_id, stored) if opener is not None else open_secret(stored)
            )
        except Exception as exc:
            _log.error("workflow_webhook_secret_unreadable", error=type(exc).__name__)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Webhook secret could not be read; retry later",
            ) from exc
        if not secret:
            _log.warning("workflow_webhook_hmac_without_secret")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Webhook declares HMAC auth but has no secret configured",
            )
        check_signature(request.headers, raw, [secret])
        return


@router.post("/{token}")
async def fire_workflow_webhook(token: str, request: Request) -> Any:
    """Fire a run for the workflow the signed token points at.

    The JSON body (if any) becomes the run ``inputs``, so a webhook can carry a
    payload the workflow's steps reference via ``{{inputs.*}}``.
    """
    resolved = verify_webhook_token_versioned(token)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook token"
        )
    tenant_id, workflow_id, token_version = resolved

    # Size cap before parsing anything (a leaked URL must not be a way to push
    # arbitrarily large bodies into run inputs).
    raw = await _read_capped_body(request, MAX_WEBHOOK_BODY_BYTES)
    try:
        body = json.loads(raw) if raw.strip() else {}
    except ValueError:
        body = {}
    inputs: dict[str, Any] = body if isinstance(body, dict) else {"payload": body}

    runner = getattr(request.app.state, "workflow_runner", None)
    svc = getattr(request.app.state, "workflow_service", None)
    if runner is None or svc is None:
        raise HTTPException(status_code=503, detail="Workflow engine not available")

    # Rotation: a token minted before the latest rotation is revoked.
    try:
        current_version = await svc.webhook_token_version(tenant_id, workflow_id)
    except Exception as exc:
        _log.error("workflow_webhook_version_check_failed", error=str(exc))
        raise HTTPException(status_code=503, detail="Webhook token could not be checked") from exc
    if token_version != current_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Webhook token has been revoked"
        )

    wf = await svc.get(tenant_id=tenant_id, workflow_id=workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if wf.get("status") != "published":
        raise HTTPException(status_code=409, detail="Workflow is not published")
    # A workflow is externally HTTP-triggerable when any declared trigger (in
    # either the builder's plural ``triggers`` list or the DSL's singular
    # ``trigger``) is of type ``webhook`` or ``api``; schedule/event/file_drop
    # triggers are not fired through this endpoint.
    triggers = extract_triggers(wf.get("definition"))
    if not any(t.get("type") in ("webhook", "api") for t in triggers):
        raise HTTPException(status_code=400, detail="Workflow has no webhook/api trigger")

    await _verify_declared_auth(request, triggers, raw, svc=svc, tenant_id=tenant_id)

    await _enforce_rate_limit(request, runner, tenant_id=tenant_id, workflow_id=workflow_id)

    # Sender retries carry the same delivery id: dedupe on it through the run
    # store's (tenant, workflow, idempotency_key) unique index — across replicas.
    idempotency_key = _delivery_key(request)
    extra: dict[str, Any] = {}
    if idempotency_key and getattr(runner, "_run_store", None) is not None:
        extra["idempotency_key"] = idempotency_key

    # Fire the run tagged as webhook-triggered (not the generic "api" default),
    # passing the body as both inputs and the raw trigger payload so a
    # ``trigger_transform`` can map webhook fields into inputs if configured.
    try:
        run_id = await runner.run(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            inputs=inputs,
            trigger_type="webhook",
            trigger_payload=inputs,
            **extra,
        )
    except WorkflowValidationError as exc:
        # The payload itself is unacceptable: retrying it can never succeed.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        return await _dead_letter(
            runner,
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            token=token,
            payload=inputs,
            exc=exc,
            idempotency_key=extra.get("idempotency_key"),
        )
    _log.info("workflow_webhook_fired", workflow_id=workflow_id, run_id=run_id)
    return {"status": "accepted", "run_id": run_id, "workflow_id": workflow_id}


async def _dead_letter(
    runner: Any,
    *,
    tenant_id: str,
    workflow_id: str,
    token: str,
    payload: dict[str, Any],
    exc: BaseException,
    idempotency_key: str | None = None,
) -> JSONResponse:
    """Keep a delivery whose run could not start, for the retry task.

    Old bug: nothing wrote the dead-letter table, so such a delivery was lost.
    If it cannot be stored either, answer 503 so the sender retries it.
    """
    import hashlib

    error = str(exc) or type(exc).__name__
    _log.error("workflow_webhook_run_start_failed", workflow_id=workflow_id, error=error)
    run_store = getattr(runner, "_run_store", None)
    recorder = getattr(run_store, "record_webhook_failure", None)
    if recorder is None:
        raise HTTPException(status_code=503, detail="Workflow run could not be started")
    # The delivery's dedup key rides along so the retry is idempotent with a
    # sender redelivery of the same delivery (B2-OPEN-2).
    identity = {"idempotency_key": idempotency_key} if idempotency_key else {}
    try:
        event_id = await recorder(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            token_fingerprint=hashlib.sha256(token.encode()).hexdigest()[:16],
            payload=payload,
            error=error,
            **identity,
        )
    except Exception as dlq_exc:
        _log.error("workflow_webhook_dead_letter_failed", error=str(dlq_exc))
        raise HTTPException(status_code=503, detail="Workflow run could not be started") from exc
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "queued_for_retry",
            "delivery_id": event_id,
            "workflow_id": workflow_id,
        },
    )
