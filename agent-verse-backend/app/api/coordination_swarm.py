"""Tenant-scoped swarm topology read model."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

router = APIRouter(prefix="/api/v1/coordination/sessions", tags=["coordination-swarm"])


def _tenant(request: Request) -> str:
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")
    return str(tenant.tenant_id)


@router.get("/{session_id}/swarm", operation_id="get_swarm_topology")
async def get_swarm_topology(request: Request, session_id: str) -> dict[str, Any]:
    records = await request.app.state.swarm_repository.list_session(_tenant(request), session_id)
    return {"nodes": [item.model_dump(mode="json") for item in records], "edges": []}


__all__ = ["router"]
