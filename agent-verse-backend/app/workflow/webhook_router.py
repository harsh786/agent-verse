"""Public workflow webhook endpoint — external systems POST here to fire a run.

Authentication is the HMAC token in the path (no tenant API key), so this router
is exempt from TenantMiddleware (its ``/wf-hooks/`` prefix is in _BYPASS_PREFIXES).
The token both identifies the workflow and proves the caller holds the URL that
``publish`` issued.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.observability.logging import get_logger
from app.workflow.trigger_extract import extract_triggers
from app.workflow.webhook_tokens import verify_webhook_token

_log = get_logger(__name__)
router = APIRouter(prefix="/wf-hooks", tags=["workflow-webhooks"])


@router.post("/{token}")
async def fire_workflow_webhook(token: str, request: Request) -> dict[str, Any]:
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
    run_id = await runner.run(
        workflow_id=workflow_id,
        tenant_id=tenant_id,
        inputs=inputs,
        trigger_type="webhook",
        trigger_payload=inputs,
    )
    _log.info("workflow_webhook_fired", workflow_id=workflow_id, run_id=run_id)
    return {"status": "accepted", "run_id": run_id, "workflow_id": workflow_id}
