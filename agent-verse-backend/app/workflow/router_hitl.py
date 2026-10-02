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
from app.workflow.hitl_extension import (
    ApprovalAlreadyDecidedError,
    ApprovalNotPendingError,
    ApprovalPersistenceError,
)

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


def _caller(request: Request) -> Any:
    # Prefer the per-request tenant set by TenantMiddleware; fall back to the
    # app-state context (used in some test harnesses). Nothing sets
    # app.state.tenant_context in production, so the per-request path is normal.
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        tenant = getattr(request.app.state, "tenant_context", None)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Tenant context not resolved")
    return tenant


def _tenant_id(request: Request) -> str:
    return str(_caller(request).tenant_id)


def _user_id(request: Request) -> str:
    """The calling principal (its API key id — SSO users map to their key).

    Old bug: this read a process-global ``app.state.current_user_id`` that
    nothing set, so every caller was "anonymous" and no decision could be
    attributed to (or restricted to) a real reviewer.
    """
    return str(getattr(_caller(request), "api_key_id", "") or "anonymous")


def _roles(request: Request) -> frozenset[str]:
    """The caller's roles, expanded through the RBAC hierarchy."""
    from app.tenancy.context import PlanTier, TenantContext
    from app.tenancy.rbac import effective_roles

    raw = tuple(getattr(_caller(request), "roles", ()) or ())
    return effective_roles(
        TenantContext(tenant_id="", plan=PlanTier.FREE, api_key_id="", roles=raw)
    )


def _authorize(request: Request, req: Any, verb: str) -> None:
    """403 unless the caller may act on approval ``req``; audit admin overrides."""
    from app.workflow.hitl_extension import authorize_reviewer

    principal = _user_id(request)
    auth = authorize_reviewer(req, principal, _roles(request))
    if not auth.allowed:
        _log.info(
            "hitl_action_denied",
            request_id=req.request_id,
            verb=verb,
            principal=principal,
            reason=auth.reason,
        )
        raise HTTPException(
            status_code=403, detail=f"Not your approval to {verb}: {auth.reason}"
        )
    if auth.override:
        _audit_override(request, req, verb, principal, auth.reason)


def _audit_override(request: Request, req: Any, verb: str, principal: str, reason: str) -> None:
    _log.warning(
        "hitl_admin_override",
        request_id=req.request_id,
        verb=verb,
        principal=principal,
        reason=reason,
    )
    audit_log = getattr(request.app.state, "audit_log", None)
    if audit_log is None:
        return
    try:
        from app.governance.audit import AuditEvent
        from app.governance.permissions import ActionLevel

        audit_log.record(
            AuditEvent(
                goal_id=str(req.run_id or req.request_id),
                tool_name="workflow.approval.admin_override",
                action_level=ActionLevel.ALLOW_LOG,
                outcome=verb,
                step_id=str(req.step_id or ""),
                approver=principal,
                api_key_id=principal,
                note=f"request_id={req.request_id}; {reason}",
            ),
            tenant_ctx=_caller(request),
        )
    except Exception as exc:  # auditing must never break the call path
        _log.warning("hitl_admin_override_audit_failed", error=str(exc))


async def _load_for_action(request: Request, request_id: str, verb: str) -> Any:
    req = await _svc(request).get_request(request_id, _tenant_id(request))
    if req is None:
        raise HTTPException(status_code=404, detail="Approval request not found")
    _authorize(request, req, verb)
    return req


async def _visible_pending(request: Request, *, priority: str | None = None) -> list[Any]:
    """Pending approvals the caller may act on (an admin sees the whole tenant)."""
    from app.workflow.hitl_extension import authorize_reviewer

    principal, roles = _user_id(request), _roles(request)
    items, _ = await _svc(request).list_pending(
        tenant_id=_tenant_id(request), priority=priority, page=1, per_page=100_000
    )
    return [r for r in items if authorize_reviewer(r, principal, roles).allowed]


def _item(request: Request, req: Any) -> dict[str, Any]:
    """An approval plus whether THIS caller may act on it (drives the inbox's
    buttons) and whether doing so would be an audited admin override."""
    from app.workflow.hitl_extension import authorize_reviewer

    auth = authorize_reviewer(req, _user_id(request), _roles(request))
    return {**req.__dict__, "can_decide": auth.allowed, "requires_override": auth.override}


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
    """List the pending approvals the caller may act on: assigned to them, to
    one of their roles, or unassigned for an approver (an admin sees all).

    Each item carries ``can_decide`` / ``requires_override`` for the caller.
    Old bug: this filtered on ``assigned_to == "anonymous"`` for every caller,
    so role-assigned and unassigned approvals never reached anyone's inbox."""
    visible = await _visible_pending(request, priority=priority)
    start = (page - 1) * per_page
    return {
        "items": [_item(request, r) for r in visible[start : start + per_page]],
        "total": len(visible),
        "page": page,
        "per_page": per_page,
    }


@router.get("/stats")
async def approval_stats(request: Request) -> dict[str, Any]:
    """Inbox statistics: pending count, avg resolution time."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    return await svc.get_stats(tenant_id=tenant_id)


# Poll cadence / lifetime of GET /approvals/stream (module-level so tests can
# shorten them). The client reconnects after [DONE].
_STREAM_POLL_SECONDS = 5.0
_STREAM_MAX_POLLS = 60  # ~5 min per connection


# NOTE: declared BEFORE GET /{request_id}. Declared after it (as it used to be),
# "/approvals/stream" matched the detail route as request_id="stream" and every
# connection got 404 "Approval request not found".
@router.get("/stream")
async def stream_approvals(request: Request) -> StreamingResponse:
    """SSE stream of new approval inbox events for the current user.

    EventSource cannot send headers: authenticate with ``?token=`` from
    ``GET /tenants/stream-token`` (accepted on ``.../stream`` GETs only). Each
    pending request the caller may act on (same visibility as ``GET
    /approvals``) is announced once as ``new_request``; the first poll runs
    immediately so the current inbox arrives without a delay."""
    _svc(request)  # 503 up front when the gateway is missing

    async def event_gen() -> AsyncGenerator[str, None]:
        import asyncio
        import json as _json

        seen: set[str] = set()
        for poll in range(_STREAM_MAX_POLLS):
            if poll:
                await asyncio.sleep(_STREAM_POLL_SECONDS)
            if await request.is_disconnected():
                return
            items = await _visible_pending(request)
            for item in items:
                if item.request_id not in seen:
                    seen.add(item.request_id)
                    event_data = {
                        "event": "new_request",
                        "kind": "workflow",
                        "request_id": item.request_id,
                        "priority": item.priority,
                        "workflow_id": item.workflow_id,
                        "workflow_name": item.workflow_name,
                        "run_id": item.run_id,
                        "step_id": item.step_id,
                        "step_name": item.step_name,
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


@router.get("/{request_id}")
async def get_approval(request_id: str, request: Request) -> dict[str, Any]:
    svc = _svc(request)
    req = await svc.get_request(request_id, _tenant_id(request))
    if req is None:
        raise HTTPException(status_code=404, detail="Approval request not found")
    return _item(request, req)


@router.post("/{request_id}/decide", status_code=status.HTTP_200_OK)
async def decide_approval(request_id: str, body: DecideRequest, request: Request) -> dict[str, Any]:
    """Submit a reviewer decision (only the assignee, a member of the assigned
    role, or an admin — whose override is audited)."""
    svc = _svc(request)
    await _load_for_action(request, request_id, "decide")
    try:
        req = await svc.decide(
            request_id=request_id,
            action=body.action,
            actor_id=_user_id(request),
            note=body.note,
            form_data=body.form_data or None,
            tenant_id=_tenant_id(request),
        )
    except ApprovalAlreadyDecidedError as exc:
        raise _conflict(exc) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _audit_decision(request, req, body.action)
    return req.__dict__


def _audit_decision(request: Request, req: Any, action: str) -> None:
    """WF-AUDIT: every reviewer decision is a tenant-scoped audit row."""
    from app.workflow.audit_middleware import record_workflow_action

    record_workflow_action(
        request,
        "approval_decided",
        workflow_id=str(req.workflow_id or req.run_id or req.request_id),
        outcome=action,
        approver=_user_id(request),
        step_id=str(req.step_id or ""),
        note=f"request_id={req.request_id}; run_id={req.run_id}",
    )


def _conflict(exc: ApprovalAlreadyDecidedError) -> HTTPException:
    """409 carrying the recorded decision, so the loser sees who decided."""
    req = exc.request
    return HTTPException(
        status_code=409,
        detail={
            "message": str(exc),
            "status": req.status,
            "action_taken": req.action_taken,
            "reviewed_by": req.reviewed_by,
            "reviewed_at": req.reviewed_at,
        },
    )


@router.post("/{request_id}/delegate", status_code=status.HTTP_200_OK)
async def delegate_approval(
    request_id: str, body: DelegateRequest, request: Request
) -> dict[str, Any]:
    svc = _svc(request)
    await _load_for_action(request, request_id, "delegate")
    try:
        req = await svc.delegate(
            request_id=request_id,
            from_user=_user_id(request),
            to_user=body.to_user_id,
            note=body.note,
            tenant_id=_tenant_id(request),
        )
    except ApprovalPersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return req.__dict__


@router.post("/{request_id}/escalate", status_code=status.HTTP_200_OK)
async def escalate_approval(request_id: str, request: Request) -> dict[str, Any]:
    svc = _svc(request)
    await _load_for_action(request, request_id, "escalate")
    try:
        req = await svc.escalate(
            request_id=request_id, actor_id=_user_id(request), tenant_id=_tenant_id(request)
        )
    except ApprovalPersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ApprovalNotPendingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return req.__dict__


@router.post("/bulk-decide", status_code=status.HTTP_200_OK)
async def bulk_decide(body: BulkDecideRequest, request: Request) -> dict[str, Any]:
    """Bulk approve or reject. Requests the caller may not decide are skipped
    and reported in ``denied``; unknown ids in ``not_found``."""
    svc = _svc(request)
    tenant_id = _tenant_id(request)
    allowed: list[str] = []
    denied: list[str] = []
    not_found: list[str] = []
    for rid in body.request_ids:
        req = await svc.get_request(rid, tenant_id)
        if req is None:
            not_found.append(rid)
            continue
        try:
            _authorize(request, req, "decide")
        except HTTPException:
            denied.append(rid)
            continue
        allowed.append(rid)
    results = (
        await svc.bulk_decide(
            request_ids=allowed,
            action=body.action,
            actor_id=_user_id(request),
            note=body.note,
            tenant_id=tenant_id,
        )
        if allowed
        else []
    )
    for decided in results:
        _audit_decision(request, decided, body.action)
    return {
        "decided": len(results),
        "request_ids": [r.request_id for r in results],
        "denied": denied,
        "not_found": not_found,
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
    # A valid token always names the approval it was minted for; anything else
    # (including the old no-Redis {"valid": True} fallback shape) is rejected.
    if not payload or not payload.get("request_id"):
        raise HTTPException(status_code=410, detail="Magic link expired or already used")

    # The decision is the one the token was minted for — the ?action= query is
    # only a confirmation. Old bug: the query value was used verbatim, so an
    # "approve" link could be edited into any other decision.
    bound_action = str(payload.get("action") or "")
    if action != bound_action:
        raise HTTPException(status_code=400, detail="Magic link action mismatch")

    request_id = str(payload["request_id"])
    user_id = "magic_link"
    try:
        req = await svc.decide(
            request_id=request_id,
            action=bound_action,
            actor_id=user_id,
            # Resolve from the durable, RLS-scoped store of the owning tenant.
            tenant_id=payload.get("tenant_id") or None,
        )
    except ApprovalAlreadyDecidedError as exc:
        raise _conflict(exc) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"request_id": req.request_id, "action": bound_action, "status": req.status}


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
                tenant_id=tenant_id,
            )
            count += 1
        except Exception:
            pass
    return {"delegated": count, "to_user_id": body.to_user_id}
