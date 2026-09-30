"""Public workflow webhook endpoint — external systems POST here to fire a run.

Authentication is the HMAC token in the path (no tenant API key), so this router
is exempt from TenantMiddleware (its ``/wf-hooks/`` prefix is in _BYPASS_PREFIXES).
The token both identifies the workflow and proves the caller holds the URL that
``publish`` issued.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.observability.logging import get_logger
from app.workflow.trigger_extract import extract_triggers
from app.workflow.webhook_tokens import verify_webhook_token

_log = get_logger(__name__)
router = APIRouter(prefix="/wf-hooks", tags=["workflow-webhooks"])


@router.post("/{token}")
async def fire_workflow_webhook(token: str, request: Request) -> Any:
    """Fire a run for the workflow the signed token points at.

    The JSON body (if any) becomes the run ``inputs``, so a webhook can carry a
    payload the workflow's steps reference via ``{{inputs.*}}``.
    """
    resolved = verify_webhook_token(token)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook token"
        )
    tenant_id, workflow_id = resolved

    try:
        body = await request.json()
    except Exception:
        body = {}
    inputs: dict[str, Any] = body if isinstance(body, dict) else {"payload": body}

    runner = getattr(request.app.state, "workflow_runner", None)
    svc = getattr(request.app.state, "workflow_service", None)
    if runner is None or svc is None:
        raise HTTPException(status_code=503, detail="Workflow engine not available")

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
        )
    except Exception as exc:
        return await _dead_letter(
            runner,
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            token=token,
            payload=inputs,
            exc=exc,
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
    try:
        event_id = await recorder(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            token_fingerprint=hashlib.sha256(token.encode()).hexdigest()[:16],
            payload=payload,
            error=error,
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
