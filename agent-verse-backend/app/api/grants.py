"""Grantex grant issuance API — issue / list / revoke agent tool-grants.

Lets a human/org administer the scoped, time-limited, revocable authority an agent
holds. Tenant-scoped via the request's tenant context; grants are enforced at the
executor tool gate (see app/governance/grants/enforcer.py).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator

from app.governance.grants import Grant
from app.governance.grants.delegation import DelegationError, mint_delegation
from app.observability.logging import get_logger
from app.tenancy.rbac import require_role

logger = get_logger(__name__)

router = APIRouter(prefix="/grants", tags=["grants"])


def _tenant_id(request: Request) -> str:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return str(ctx.tenant_id)


def _store(request: Request) -> Any:
    store = getattr(request.app.state, "grant_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="grant store unavailable")
    return store


async def _audit(request: Request, tenant_id: str, event: dict[str, Any]) -> bool:
    """Append to the tamper-evident audit chain; return whether it was recorded.

    ``PersistentAuditChain.append`` retries a concurrent (tenant_id, seq) PK
    collision itself; a final failure used to vanish inside
    ``suppress(Exception)``, silently dropping the grant's audit record. It is now
    logged at error level and surfaced as ``audit_recorded: false`` on the
    response (the grant mutation itself has already committed, so failing the
    request would misreport it as not having happened).
    """
    chain = getattr(request.app.state, "audit_chain", None)
    if chain is None:
        return False
    try:
        await chain.append(tenant_id, event)
    except Exception as exc:
        logger.error(
            "grant_audit_append_failed",
            tenant_id=tenant_id,
            audit_event=event.get("event"),
            grant_id=event.get("grant_id"),
            error=str(exc)[:200],
        )
        return False
    return True


# Per-grant spend cap ceiling. ``max_cost_usd`` was unbounded (and could be
# negative / NaN-like huge values), so a cap could silently be "no cap".
MAX_GRANT_COST_USD = 10_000.0


class IssueGrantRequest(BaseModel):
    grantee_agent_id: str = Field(min_length=1, max_length=200)
    scopes: list[str] = Field(min_length=1)
    ttl_seconds: int = Field(gt=0, le=60 * 60 * 24 * 365)
    grantor: str = "api"
    max_cost_usd: float | None = Field(default=None, ge=0, le=MAX_GRANT_COST_USD)
    not_before_seconds: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _window_is_not_empty(self) -> IssueGrantRequest:
        if self.not_before_seconds >= self.ttl_seconds:
            raise ValueError("not_before_seconds must be less than ttl_seconds")
        return self


async def _require_grantee(request: Request, agent_id: str) -> None:
    """404 unless *agent_id* is an agent of the caller's tenant.

    The grantee was never checked: a grant could be minted for an agent id that
    does not exist (and would silently attach to any agent later created with
    that id) or that belongs to another tenant.
    """
    store = getattr(request.app.state, "agent_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="agent store unavailable")
    ctx = request.state.tenant
    getter = getattr(store, "get_async", None)
    agent = (
        await getter(agent_id, tenant_ctx=ctx)
        if getter is not None
        else store.get(agent_id, tenant_ctx=ctx)
    )
    if agent is None:
        raise HTTPException(status_code=404, detail=f"grantee agent {agent_id!r} not found")


def _to_dict(g: Grant) -> dict[str, Any]:
    return {
        "grant_id": g.grant_id,
        "tenant_id": g.tenant_id,
        "grantor": g.grantor,
        "grantee_agent_id": g.grantee_agent_id,
        "scopes": list(g.scopes),
        "not_before": g.not_before.isoformat(),
        "expires_at": g.expires_at.isoformat(),
        "max_cost_usd": g.max_cost_usd,
        "spent_usd": g.spent_usd,
        "revoked": g.revoked,
        "parent_grant_id": g.parent_grant_id,
    }


def _principal(request: Request) -> str:
    ctx = getattr(request.state, "tenant", None)
    key_id = str(getattr(ctx, "api_key_id", "") or "")
    user_id = str(getattr(ctx, "user_id", "") or "")
    return f"user:{user_id}" if user_id else f"key:{key_id}"


@router.post("", status_code=status.HTTP_201_CREATED)
async def issue_grant(
    body: IssueGrantRequest,
    request: Request,
    # Minting authority for agents is an admin action. There was no role or scope
    # check at all, so any key — a viewer's included — could issue a "*" grant
    # with no cost cap to any agent, defeating grant enforcement entirely.
    _rbac: None = Depends(require_role("admin")),
) -> dict[str, Any]:
    tenant_id = _tenant_id(request)
    await _require_grantee(request, body.grantee_agent_id)
    now = datetime.now(UTC)
    grant = Grant(
        grant_id=uuid.uuid4().hex,
        tenant_id=tenant_id,
        # The grantor is the authenticated principal, never a request field (it
        # was body.grantor: an audit trail anyone could forge).
        grantor=_principal(request),
        grantee_agent_id=body.grantee_agent_id,
        scopes=tuple(body.scopes),
        not_before=now + timedelta(seconds=body.not_before_seconds),
        expires_at=now + timedelta(seconds=body.ttl_seconds),
        max_cost_usd=body.max_cost_usd,
    )
    stored = await _store(request).issue(grant)
    audit_recorded = await _audit(
        request,
        tenant_id,
        {
            "event": "grant_issued",
            "grant_id": stored.grant_id,
            "grantee_agent_id": stored.grantee_agent_id,
            "scopes": list(stored.scopes),
            "grantor": stored.grantor,
        },
    )
    return {**_to_dict(stored), "audit_recorded": audit_recorded}


@router.get("")
async def list_grants(request: Request, agent_id: str) -> dict[str, Any]:
    tenant_id = _tenant_id(request)
    grants = await _store(request).list_for_agent(tenant_id, agent_id)
    return {"grants": [_to_dict(g) for g in grants]}


@router.get("/{grant_id}")
async def get_grant(grant_id: str, request: Request) -> dict[str, Any]:
    """One grant of the caller's tenant (including ``spent_usd``), or 404."""
    tenant_id = _tenant_id(request)
    grant = await _store(request).get(tenant_id, grant_id)
    if grant is None:
        raise HTTPException(status_code=404, detail="grant not found")
    return _to_dict(grant)


@router.post("/{grant_id}/revoke")
async def revoke_grant(
    grant_id: str,
    request: Request,
    _rbac: None = Depends(require_role("admin")),
) -> dict[str, Any]:
    tenant_id = _tenant_id(request)
    revoked = await _store(request).revoke(tenant_id, grant_id)
    if revoked is None:
        raise HTTPException(status_code=404, detail="grant not found")
    audit_recorded = await _audit(
        request, tenant_id, {"event": "grant_revoked", "grant_id": grant_id}
    )
    return {**_to_dict(revoked), "audit_recorded": audit_recorded}


# Ancestor walks stop here (a cycle or runaway chain cannot loop the request).
_MAX_CHAIN_DEPTH = 32


class DelegateGrantRequest(BaseModel):
    """A narrowed child of an existing grant (a03-F055-02).

    Every field may only NARROW the parent: omitted scopes / window / cap
    inherit the parent's (the cap defaults to the parent's REMAINING budget, as
    the automatic sub-agent delegation does); anything wider is a 400.
    """

    grantee_agent_id: str = Field(min_length=1, max_length=200)
    scopes: list[str] | None = Field(default=None, min_length=1)
    ttl_seconds: int | None = Field(default=None, gt=0, le=60 * 60 * 24 * 365)
    max_cost_usd: float | None = Field(default=None, ge=0, le=MAX_GRANT_COST_USD)


@router.post("/{grant_id}/delegate", status_code=status.HTTP_201_CREATED)
async def delegate_grant(
    grant_id: str,
    body: DelegateGrantRequest,
    request: Request,
    # Minting authority is an admin action, like issuing a grant.
    _rbac: None = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Delegate a narrowed copy of *grant_id* to another agent of the tenant.

    Delegation used to happen only inside the sub-agent spawn path
    (``delegate_active_grants``); an administrator could not create one or see
    the result. The child records ``parent_grant_id``: revoking the parent
    revokes it, and its spend counts against the parent's budget.
    """
    tenant_id = _tenant_id(request)
    store = _store(request)
    parent = await store.get(tenant_id, grant_id)
    if parent is None:
        raise HTTPException(status_code=404, detail="grant not found")
    await _require_grantee(request, body.grantee_agent_id)
    now = datetime.now(UTC)
    expires_at = (
        now + timedelta(seconds=body.ttl_seconds)
        if body.ttl_seconds is not None
        else parent.expires_at
    )
    max_cost = body.max_cost_usd
    if max_cost is None and parent.max_cost_usd is not None:
        max_cost = max(0.0, parent.max_cost_usd - parent.spent_usd)
    try:
        child = mint_delegation(
            parent,
            grant_id=uuid.uuid4().hex,
            grantee_agent_id=body.grantee_agent_id,
            scopes=tuple(body.scopes) if body.scopes else parent.scopes,
            expires_at=expires_at,
            not_before=max(now, parent.not_before),
            max_cost_usd=max_cost,
            now=now,
        )
    except DelegationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # The grantor names who delegated: the parent's agent, through this admin.
    child = Grant(**{**child.__dict__, "grantor": f"{child.grantor} via {_principal(request)}"})
    stored = await store.issue(child)
    audit_recorded = await _audit(
        request,
        tenant_id,
        {
            "event": "grant_delegated",
            "grant_id": stored.grant_id,
            "parent_grant_id": parent.grant_id,
            "grantee_agent_id": stored.grantee_agent_id,
            "scopes": list(stored.scopes),
            "grantor": stored.grantor,
        },
    )
    return {**_to_dict(stored), "audit_recorded": audit_recorded}


@router.get("/{grant_id}/chain")
async def get_grant_chain(grant_id: str, request: Request) -> dict[str, Any]:
    """The grant, its ancestors (parent first, up to the root) and its direct delegations."""
    tenant_id = _tenant_id(request)
    store = _store(request)
    grant = await store.get(tenant_id, grant_id)
    if grant is None:
        raise HTTPException(status_code=404, detail="grant not found")
    ancestors: list[Grant] = []
    seen = {grant.grant_id}
    parent_id = grant.parent_grant_id
    while parent_id and parent_id not in seen and len(ancestors) < _MAX_CHAIN_DEPTH:
        seen.add(parent_id)
        parent = await store.get(tenant_id, parent_id)
        if parent is None:
            break
        ancestors.append(parent)
        parent_id = parent.parent_grant_id
    lister = getattr(store, "list_children", None)
    children = await lister(tenant_id, grant_id) if lister is not None else ()
    return {
        "grant": _to_dict(grant),
        "ancestors": [_to_dict(g) for g in ancestors],
        "delegations": [_to_dict(g) for g in children],
    }
