"""Per-agent credential management API."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api._deps import require_owned_agent

# Every route acts on the path's agent, so ownership is checked once for all.
router = APIRouter(
    prefix="/agents/{agent_id}/keys",
    tags=["agent-identity"],
    dependencies=[Depends(require_owned_agent)],
)


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
async def get_agent_manifest(
    agent_id: str, request: Request, agent: dict = Depends(require_owned_agent)
) -> dict:
    """Return the signed capability manifest for one of the caller's agents.

    It used to sign a manifest for ANY agent id — falling back to a bare
    ``{"id": agent_id}`` when the agent was not the caller's — which made the
    platform a signing oracle for agents the caller does not own.
    """
    from app.auth.agent_manifest import (
        ManifestSigningNotConfiguredError,
        build_manifest,
        sign_manifest,
    )

    manifest = build_manifest(agent, request.state.tenant)
    try:
        return sign_manifest(manifest)
    except ManifestSigningNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
