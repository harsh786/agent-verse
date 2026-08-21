"""Approval inbox API router.

Endpoints:
  GET    /approvals                    List pending + recent for current user
  GET    /approvals/stats              Inbox stats (pending count, avg resolution)
  GET    /approvals/{id}               Full detail with rich context
  POST   /approvals/{id}/decide        Submit decision {action, note, form_data}
  POST   /approvals/{id}/delegate      Delegate to {user_id}
  POST   /approvals/{id}/escalate      Manual escalate
  POST   /approvals/bulk-decide        Bulk approve/reject {request_ids, action}
  GET    /approvals/magic/{token}      Process magic link from email/Slack
  POST   /approvals/delegate-all       "On leave" → delegate all pending to user
  GET    /approvals/stream             SSE stream of inbox events
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from app.observability.logging import get_logger

_log = get_logger(__name__)

router = APIRouter(prefix="/approvals", tags=["workflow-hitl"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _svc(request: Request) -> Any:
    svc = getattr(request.app.state, "hitl_workflow_gateway", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="HITL gateway not available")
    return svc


def _tenant_id(request: Request) -> str:
    return request.app.state.tenant_context.tenant_id


def _user_id(request: Request) -> str:
    return getattr(request.app.state, "current_user_id", "anonymous")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class DecideRequest(BaseModel):
    action: str = Field(..., description="approve | reject | custom action id")
    note: str = ""
    form_data: dict[str, Any] = Field(default_factory=dict)


class DelegateRequest(BaseModel):
    to_user_id: str
    note: str = ""


class BulkDecideRequest(BaseModel):
    request_ids: list[str]
    action: str
    note: str = ""


class DelegateAllRequest(BaseModel):
    to_user_id: str
    note: str = ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def list_approvals(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    priority: str | None = Query(None),
) -> dict[str, Any]:
    """List pending + recent approval requests for the current user."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    user_id = _user_id(request)
    items, total = await svc.list_pending(
        tenant_id=tenant_id,
        assigned_to=user_id,
        priority=priority,
        page=page,
        per_page=per_page,
    )
    return {
        "items": [r.__dict__ for r in items],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/stats")
async def approval_stats(request: Request) -> dict[str, Any]:
    """Inbox statistics: pending count, avg resolution time."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    return await svc.get_stats(tenant_id=tenant_id)


@router.get("/{request_id}")
async def get_approval(request_id: str, request: Request) -> dict[str, Any]:
    svc = _svc(request)
    req = await svc.get_request(request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Approval request not found")
    return req.__dict__


@router.post("/{request_id}/decide", status_code=status.HTTP_200_OK)
async def decide_approval(request_id: str, body: DecideRequest, request: Request) -> dict[str, Any]:
    """Submit a reviewer decision."""
    svc = _svc(request)
    user_id = _user_id(request)
    try:
        req = await svc.decide(
            request_id=request_id,
            action=body.action,
            actor_id=user_id,
            note=body.note,
            form_data=body.form_data or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return req.__dict__


@router.post("/{request_id}/delegate", status_code=status.HTTP_200_OK)
async def delegate_approval(
    request_id: str, body: DelegateRequest, request: Request
) -> dict[str, Any]:
    svc = _svc(request)
    user_id = _user_id(request)
    try:
        req = await svc.delegate(
            request_id=request_id,
            from_user=user_id,
            to_user=body.to_user_id,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return req.__dict__


@router.post("/{request_id}/escalate", status_code=status.HTTP_200_OK)
async def escalate_approval(request_id: str, request: Request) -> dict[str, Any]:
    svc = _svc(request)
    user_id = _user_id(request)
    try:
        req = await svc.escalate(request_id=request_id, actor_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return req.__dict__


@router.post("/bulk-decide", status_code=status.HTTP_200_OK)
async def bulk_decide(body: BulkDecideRequest, request: Request) -> dict[str, Any]:
    """Bulk approve or reject multiple requests."""
    svc = _svc(request)
    user_id = _user_id(request)
    results = await svc.bulk_decide(
        request_ids=body.request_ids,
        action=body.action,
        actor_id=user_id,
        note=body.note,
    )
    return {
        "decided": len(results),
        "request_ids": [r.request_id for r in results],
    }


@router.get("/magic/{token}")
async def magic_link_decide(
    token: str,
    action: str = Query(...),
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Process a single-use magic link from email or Slack."""
    svc = _svc(request)
    payload = await svc.consume_magic_link(token)
    if payload is None:
        raise HTTPException(status_code=410, detail="Magic link expired or already used")

    request_id = payload.get("request_id", "")
    user_id = "magic_link"
    try:
        req = await svc.decide(
            request_id=request_id,
            action=action,
            actor_id=user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"request_id": req.request_id, "action": action, "status": req.status}


@router.post("/delegate-all", status_code=status.HTTP_200_OK)
async def delegate_all(body: DelegateAllRequest, request: Request) -> dict[str, Any]:
    """Delegate all pending requests from current user to another (e.g., on leave)."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    user_id = _user_id(request)

    pending, _ = await svc.list_pending(
        tenant_id=tenant_id, assigned_to=user_id, page=1, per_page=1000
    )
    count = 0
    for req in pending:
        try:
            await svc.delegate(
                request_id=req.request_id,
                from_user=user_id,
                to_user=body.to_user_id,
                note=body.note,
            )
            count += 1
        except Exception:
            pass
    return {"delegated": count, "to_user_id": body.to_user_id}


@router.get("/stream")
async def stream_approvals(request: Request) -> StreamingResponse:
    """SSE stream of new approval inbox events for the current user."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    user_id = _user_id(request)

    async def event_gen() -> AsyncGenerator[str, None]:
        import asyncio
        import json as _json

        # Poll every 5 seconds for new pending requests
        seen: set[str] = set()
        for _ in range(60):  # max 5 min stream
            await asyncio.sleep(5)
            items, _ = await svc.list_pending(
                tenant_id=tenant_id, assigned_to=user_id, page=1, per_page=50
            )
            for item in items:
                if item.request_id not in seen:
                    seen.add(item.request_id)
                    event_data = {
                        "event": "new_request",
                        "request_id": item.request_id,
                        "priority": item.priority,
                    }
                    yield f"data: {_json.dumps(event_data)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
