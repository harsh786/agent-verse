"""Tenant-scoped generative-agent read model."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

router = APIRouter(prefix="/api/v1/coordination/sessions", tags=["coordination-generative"])


def _tenant(request: Request) -> str:
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")
    return str(tenant.tenant_id)


@router.get("/{session_id}/generative", operation_id="get_generative_agent_state")
async def get_generative_state(request: Request, session_id: str) -> dict[str, Any]:
    records = await request.app.state.generative_repository.list_session(
        _tenant(request), session_id
    )
    return {"items": [item.model_dump(mode="json") for item in records]}


__all__ = ["router"]
