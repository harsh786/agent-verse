"""Linked chat identities (TRG-36): who a Slack user acts as in AgentVerse.

* ``POST   /channels/identities/link-codes`` — the CALLER (its API key) asks for a
  one-time code; ``/agentverse link <code>`` in a bound Slack workspace links
  that Slack user to the caller.
* ``GET    /channels/identities`` — the tenant's links (active + outstanding codes).
* ``DELETE /channels/identities/{id}`` — unlink.

A Slack user then acts with the linked key's LIVE roles: approving HITL needs
``governance:approve``, submitting goals ``goals:write``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/channels/identities", tags=["channels"])

_SUPPORTED = ("slack",)


def _caller(request: Request) -> tuple[str, str]:
    ctx = getattr(request.state, "tenant", None)
    tenant_id = str(getattr(ctx, "tenant_id", "") or "")
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return tenant_id, str(getattr(ctx, "api_key_id", "") or "")


def _own_only(request: Request, principal_id: str) -> str | None:
    """Non-admins see and remove only their own links."""
    from app.tenancy.rbac import has_role

    ctx = getattr(request.state, "tenant", None)
    return None if ctx is not None and has_role(ctx, "admin") else principal_id


def _tenant_db(request: Request) -> Any:
    from app.api.channels.ingestion import _tenant_db as tenant_db

    return tenant_db(request)


@router.post("/link-codes")
async def create_link_code(request: Request) -> dict[str, Any]:
    from app.integrations.slack.identity import (
        PrincipalNotLinkableError,
        SlackIdentityStoreUnavailableError,
        issue_link_code,
    )

    tenant_id, principal_id = _caller(request)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    channel_type = str((body or {}).get("channel_type") or "slack")
    if channel_type not in _SUPPORTED:
        raise HTTPException(status_code=422, detail="channel_type must be 'slack'")
    try:
        issued = await issue_link_code(
            tenant_db=_tenant_db(request), tenant_id=tenant_id, principal_id=principal_id
        )
    except SlackIdentityStoreUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except PrincipalNotLinkableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return issued.to_response()


@router.get("")
async def list_identities(request: Request) -> list[dict[str, Any]]:
    from app.integrations.slack.identity import SlackIdentityStoreUnavailableError, list_links

    tenant_id, principal_id = _caller(request)
    try:
        return await list_links(
            tenant_db=_tenant_db(request),
            tenant_id=tenant_id,
            principal_id=_own_only(request, principal_id),
        )
    except SlackIdentityStoreUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


@router.delete("/{link_id}")
async def delete_identity(link_id: str, request: Request) -> dict[str, Any]:
    from app.integrations.slack.identity import SlackIdentityStoreUnavailableError, delete_link

    tenant_id, principal_id = _caller(request)
    try:
        deleted = await delete_link(
            tenant_db=_tenant_db(request),
            tenant_id=tenant_id,
            link_id=link_id,
            principal_id=_own_only(request, principal_id),
        )
    except SlackIdentityStoreUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    if not deleted:
        raise HTTPException(status_code=404, detail="Identity link not found")
    return {"id": link_id, "deleted": True}
