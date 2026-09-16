"""Per-agent credential management API."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/agents/{agent_id}/keys", tags=["agent-identity"])


class AgentKeyCreateRequest(BaseModel):
    name: str
    allowed_tools: list[str] | None = None
    denied_tools: list[str] = []
    allowed_connectors: list[str] | None = None
    expires_in_days: int | None = None


@router.post("")
async def create_agent_key(
    agent_id: str,
    body: AgentKeyCreateRequest,
    request: Request,
) -> dict:
    """Create a new API key scoped to this agent."""
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Auth required")

    from app.auth.agent_credentials import _agent_credential_store

    expires_at = None
    if body.expires_in_days:
        expires_at = time.time() + body.expires_in_days * 86400

    result = await _agent_credential_store.create_key_async(
        agent_id=agent_id,
        tenant_id=tenant_ctx.tenant_id,
        name=body.name,
        allowed_tools=body.allowed_tools,
        denied_tools=body.denied_tools,
        allowed_connectors=body.allowed_connectors,
        expires_at=expires_at,
        created_by=tenant_ctx.api_key_id,
    )
    return result


@router.get("")
async def list_agent_keys(agent_id: str, request: Request) -> dict:
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Auth required")
    from app.auth.agent_credentials import _agent_credential_store

    keys = await _agent_credential_store.list_for_agent_async(agent_id, tenant_ctx.tenant_id)
    return {"keys": keys, "agent_id": agent_id}


@router.delete("/{key_id}")
async def revoke_agent_key(agent_id: str, key_id: str, request: Request) -> dict:
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Auth required")
    from app.auth.agent_credentials import _agent_credential_store

    revoked = await _agent_credential_store.revoke_async(key_id, agent_id, tenant_ctx.tenant_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"key_id": key_id, "status": "revoked"}


@router.get("/manifest")
async def get_agent_manifest(agent_id: str, request: Request) -> dict:
    """Return signed capability manifest for this agent."""
    tenant_ctx = getattr(request.state, "tenant", None)
    app_state = request.app.state
    agent_store = getattr(app_state, "agent_store", None)

    agent: dict = {}
    if agent_store is not None:
        try:
            agent_dict = await agent_store.get_agent(agent_id, tenant_ctx=tenant_ctx)
            if agent_dict:
                agent = agent_dict
        except Exception:
            pass

    from app.auth.agent_manifest import build_manifest, sign_manifest

    manifest = build_manifest(agent or {"id": agent_id}, tenant_ctx)
    return sign_manifest(manifest)
