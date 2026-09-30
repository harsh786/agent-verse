"""Tenant-scoped swarm topology read model."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.coordination.pattern_runs.service import public_record, swarm_topology

router = APIRouter(prefix="/api/v1/coordination/sessions", tags=["coordination-swarm"])


def _tenant(request: Request) -> str:
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")
    return str(tenant.tenant_id)


@router.get("/{session_id}/swarm", operation_id="get_swarm_topology")
async def get_swarm_topology(request: Request, session_id: str) -> dict[str, Any]:
    """Claims (nodes) and accepted gossip deliveries (edges) written by swarm runs."""
    records = await request.app.state.swarm_repository.list_session(_tenant(request), session_id)
    topology = swarm_topology(records)
    return {
        **topology,
        "runs": [public_record("decentralized_swarm", item) for item in records],
    }


__all__ = ["router"]
