"""RPA API endpoints."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.rpa.tools import RPA_TOOLS

router = APIRouter(prefix="/rpa", tags=["rpa"])


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


def _executor(request: Request) -> Any:
    return getattr(request.app.state, "rpa_executor", None)


def _session_store(request: Request) -> Any:
    return getattr(request.app.state, "rpa_session_store", None)


async def _refuse_if_on_another_replica(request: Request, session_id: str, tenant_id: str) -> None:
    """409 when the session's live browser is held by a different API replica.

    Browser pages are per-process; the shared registry says where one lives.
    Pretending it exists here (a fresh blank browser, or a vague 404) is worse.
    """
    manager = getattr(request.app.state, "rpa_session_manager", None)
    live_elsewhere = getattr(manager, "live_elsewhere", None)
    if live_elsewhere is None:
        return
    owner = await live_elsewhere(session_id, tenant_id)
    if owner:
        from app.rpa.session_manager import SessionOnAnotherReplicaError

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(SessionOnAnotherReplicaError(session_id, owner)),
        )


@router.get("/tools")
async def list_rpa_tools(request: Request) -> list[dict[str, Any]]:
    """Return built-in RPA tool metadata for agent clients.

    Security: requires a valid tenant API key — tool metadata is not public
    information and could aid reconnaissance of automation capabilities.
    """
    _require_tenant(request)
    return [dict(tool) for tool in RPA_TOOLS]


class RPAExecuteRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = {}
    session_id: str | None = None


@router.post("/execute", status_code=200)
async def execute_rpa_tool(request: Request, body: RPAExecuteRequest) -> dict[str, Any]:
    """Execute an RPA tool command."""
    tenant = _require_tenant(request)

    # Validate tool exists
    valid_tools = {t["name"] for t in RPA_TOOLS}
    if body.tool_name not in valid_tools:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown RPA tool: {body.tool_name}. Valid: {sorted(valid_tools)}",
        )

    if body.session_id:
        await _refuse_if_on_another_replica(request, body.session_id, tenant.tenant_id)

    executor = _executor(request)
    if executor is None:
        # Late import to avoid circular deps at startup
        from app.rpa.executor import RPAExecutor

        executor = RPAExecutor()
        # Cache on app.state for subsequent requests
        request.app.state.rpa_executor = executor

    result = await executor.execute(
        tool_name=body.tool_name,
        arguments=body.arguments,
        session_id=body.session_id,
        tenant_id=tenant.tenant_id,
    )

    if getattr(result, "error_code", None) == "browser_session_limit":
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "browser_session_limit",
                "message": "Browser session limit reached; close an active session first.",
                **(result.error_detail or {}),
            },
            headers={"Retry-After": "10"},
        )

    return {
        "success": result.success,
        "output": result.output,
        "artifact_url": result.artifact_url,
        "artifact_name": result.artifact_name,
        "duration_ms": round(result.duration_ms, 2),
        "error": result.error,
        "tool_name": body.tool_name,
        "session_id": body.session_id,
    }


class RPAReportRequest(BaseModel):
    url: str
    selectors: list[str] | None = None
    title: str | None = None
    goal_id: str = "rpa-report"


@router.post("/report", status_code=201)
async def generate_rpa_report(request: Request, body: RPAReportRequest) -> dict[str, Any]:
    """Scrape a URL and return a structured, provenance-tagged PDF report.

    Runs an ``open_url → extract_text → screenshot`` sequence through the RPA
    executor (WS-5), assembles a :class:`ScrapeReport`, renders it to a real PDF
    (fpdf2), and persists it via the RPA artifact store. The PDF is returned
    inline (base64) for immediate download and referenced by its stored ``uri``.
    """
    tenant = _require_tenant(request)

    # SSRF guard: the URL is fetched server-side by the RPA executor, so reject
    # internal/private/link-local/metadata targets before any navigation.
    from app.net.ssrf_guard import SSRFError, assert_public_url

    try:
        assert_public_url(body.url, context="rpa_report")
    except SSRFError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Blocked URL: {exc}"
        ) from exc

    executor = _executor(request)
    if executor is None:
        from app.rpa.executor import RPAExecutor

        executor = RPAExecutor()
        request.app.state.rpa_executor = executor

    from app.rpa.artifacts import get_artifact_store
    from app.rpa.report import build_and_store_report_pdf, render_report_pdf, run_scrape_report

    report, _results = await run_scrape_report(
        executor,
        url=body.url,
        tenant_id=tenant.tenant_id,
        goal_id=body.goal_id,
        selectors=body.selectors,
        title=body.title,
    )

    store = getattr(request.app.state, "rpa_artifact_store", None) or get_artifact_store()
    artifact = await build_and_store_report_pdf(
        report,
        artifact_store=store,
        goal_id=body.goal_id,
        name=f"{body.goal_id}-report.pdf",
    )
    pdf_bytes = render_report_pdf(report)

    return {
        "source_url": report.source_url,
        "title": report.title,
        "scraped_at": report.scraped_at,
        "sections": len(report.sections),
        "screenshots": len(report.screenshots),
        "artifact_id": getattr(artifact, "artifact_id", None),
        "artifact_name": getattr(artifact, "name", None),
        "artifact_uri": getattr(artifact, "uri", None),
        "size_bytes": getattr(artifact, "size_bytes", len(pdf_bytes)),
        "pdf_base64": base64.b64encode(pdf_bytes).decode(),
    }


@router.get("/sessions")
async def list_sessions(request: Request) -> list[dict[str, Any]]:
    """List active RPA sessions for the tenant."""
    tenant = _require_tenant(request)
    store = _session_store(request)
    if store is None:
        from app.rpa.session import RPASessionStore

        store = RPASessionStore()
        request.app.state.rpa_session_store = store

    sessions = await store.list_active(tenant_id=tenant.tenant_id)
    return [
        {
            "session_id": s.session_id,
            "status": s.status,
            "created_at": s.created_at,
            "last_used_at": s.last_used_at,
        }
        for s in sessions
    ]


@router.post("/sessions", status_code=201)
async def create_session(request: Request) -> dict[str, Any]:
    """Create a new RPA session."""
    tenant = _require_tenant(request)
    store = _session_store(request)
    if store is None:
        from app.rpa.session import RPASessionStore

        store = RPASessionStore()
        request.app.state.rpa_session_store = store

    session = await store.create(tenant_id=tenant.tenant_id)
    return {
        "session_id": session.session_id,
        "status": session.status,
        "created_at": session.created_at,
    }


@router.delete("/sessions/{session_id}", status_code=204)
async def close_session(request: Request, session_id: str) -> None:
    """Close an RPA session."""
    tenant = _require_tenant(request)
    store = _session_store(request)
    if store is None:
        return

    ok = await store.close(session_id, tenant_id=tenant.tenant_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")


@router.get("/sessions/{session_id}/screenshot")
async def get_session_screenshot(request: Request, session_id: str) -> dict[str, Any]:
    """Take a read-only screenshot of the current viewport without recording an action."""
    tenant = _require_tenant(request)
    session_manager = getattr(request.app.state, "rpa_session_manager", None)
    if session_manager is None:
        raise HTTPException(503, "RPA session manager not available")
    page = (
        session_manager.get_page(session_id, tenant_id=tenant.tenant_id)
        if hasattr(session_manager, "get_page")
        else None
    )
    if page is None:
        await _refuse_if_on_another_replica(request, session_id, tenant.tenant_id)
        raise HTTPException(404, "Session not found or browser not active")
    try:
        screenshot_bytes = await page.screenshot(type="jpeg", quality=60, full_page=False)
        return {
            "session_id": session_id,
            "screenshot_data_uri": f"data:image/jpeg;base64,{base64.b64encode(screenshot_bytes).decode()}",  # noqa: E501
            "url": page.url,
            "timestamp": datetime.now(UTC).isoformat(),
        }
    except Exception as exc:
        raise HTTPException(500, f"Screenshot failed: {exc}") from exc


@router.get("/sessions/{session_id}/current-view")
async def get_current_view(request: Request, session_id: str) -> dict[str, Any]:
    """Read-only viewport snapshot — does NOT create action log entry."""
    tenant = _require_tenant(request)
    session_manager = getattr(request.app.state, "rpa_session_manager", None)
    if session_manager is None:
        raise HTTPException(503, "RPA not available")
    page = (
        session_manager.get_page(session_id, tenant_id=tenant.tenant_id)
        if hasattr(session_manager, "get_page")
        else None
    )
    if page is None:
        await _refuse_if_on_another_replica(request, session_id, tenant.tenant_id)
        raise HTTPException(404, "Session not found or browser not active")
    try:
        screenshot_bytes = await page.screenshot(type="jpeg", quality=60, full_page=False)
        return {
            "session_id": session_id,
            "screenshot_data_uri": f"data:image/jpeg;base64,{base64.b64encode(screenshot_bytes).decode()}",  # noqa: E501
            "url": page.url,
            "timestamp": datetime.now(UTC).isoformat(),
        }
    except Exception as exc:
        raise HTTPException(500, f"Screenshot failed: {exc}") from exc


# P1.2: Human takeover endpoint
class TakeoverRequest(BaseModel):
    reason: str = "Operator requested assistance"


@router.post("/sessions/{session_id}/takeover")
async def request_human_takeover(
    request: Request, session_id: str, body: TakeoverRequest
) -> dict[str, Any]:
    """Ask a human operator to take over an RPA session.

    Raises a tenant-scoped HITL approval — listed in the Approvals inbox, and
    alerted to the tenant's notification channels by the gateway — and returns
    503 when it cannot be delivered. (It used to set a Redis key nothing read
    and always claim the operator "has been notified".)
    """
    tenant = _require_tenant(request)
    store = _session_store(request)
    if store is None:
        raise HTTPException(503, "Session store not available")

    session = await store.get(session_id, tenant_id=tenant.tenant_id)
    if session is None:
        raise HTTPException(404, f"RPA session {session_id} not found")

    gateway = getattr(request.app.state, "hitl_gateway", None)
    if gateway is None or not hasattr(gateway, "request_approval_async"):
        raise HTTPException(
            503, "Takeover cannot be delivered: no human-approval channel is configured"
        )

    from app.governance.hitl import HITLDeliveryError

    reason = (body.reason or "").strip()[:500] or "Operator requested assistance"
    try:
        request_id = await gateway.request_approval_async(
            goal_id=f"rpa-session:{session_id}"[:64],
            action=f"RPA human takeover for session {session_id}: {reason}",
            risk_level="high",
            tenant_ctx=tenant,
            require_persisted=True,
        )
    except HITLDeliveryError as exc:
        raise HTTPException(
            503, "Takeover cannot be delivered: the approval request could not be saved"
        ) from exc

    # Channels the gateway's notification dispatch targets (fire-and-forget).
    notifier = getattr(gateway, "_notification_service", None)
    channels = 0
    if notifier is not None and hasattr(notifier, "get_channels"):
        try:
            channels = len(notifier.get_channels(tenant.tenant_id))
        except Exception:
            channels = 0

    frontend_url = str(
        getattr(getattr(request.app.state, "settings", None), "frontend_url", "")
        or "http://localhost:5173"
    ).rstrip("/")
    message = f"Takeover request {request_id} is waiting in the Approvals inbox"
    message += (
        f"; an alert was queued for {channels} notification channel(s)."
        if channels
        else "; no notification channels are configured for this tenant."
    )
    return {
        "session_id": session_id,
        "status": "awaiting_human",
        "reason": reason,
        "approval_request_id": request_id,
        "notification_channels": channels,
        # Both are real frontend routes (App.tsx): the RPA live console and the
        # approvals inbox. There is no per-session live route to deep-link.
        "live_url": f"{frontend_url}/rpa/live",
        "approvals_url": f"{frontend_url}/approvals",
        "message": message,
    }
