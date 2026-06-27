"""RPA API endpoints."""
from __future__ import annotations

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


@router.get("/tools")
async def list_rpa_tools() -> list[dict[str, Any]]:
    """Return built-in RPA tool metadata for agent clients."""
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
