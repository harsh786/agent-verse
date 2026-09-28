"""Grantex grant issuance API — issue / list / revoke agent tool-grants.

Lets a human/org administer the scoped, time-limited, revocable authority an agent
holds. Tenant-scoped via the request's tenant context; grants are enforced at the
executor tool gate (see app/governance/grants/enforcer.py).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.governance.grants import Grant
from app.observability.logging import get_logger

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


class IssueGrantRequest(BaseModel):
    grantee_agent_id: str
    scopes: list[str] = Field(min_length=1)
    ttl_seconds: int = Field(gt=0, le=60 * 60 * 24 * 365)
    grantor: str = "api"
    max_cost_usd: float | None = None
    not_before_seconds: int = 0


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
        "revoked": g.revoked,
        "parent_grant_id": g.parent_grant_id,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def issue_grant(body: IssueGrantRequest, request: Request) -> dict[str, Any]:
    tenant_id = _tenant_id(request)
    now = datetime.now(UTC)
    grant = Grant(
        grant_id=uuid.uuid4().hex,
        tenant_id=tenant_id,
        grantor=body.grantor,
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


@router.post("/{grant_id}/revoke")
async def revoke_grant(grant_id: str, request: Request) -> dict[str, Any]:
    tenant_id = _tenant_id(request)
    revoked = await _store(request).revoke(tenant_id, grant_id)
    if revoked is None:
        raise HTTPException(status_code=404, detail="grant not found")
    audit_recorded = await _audit(
        request, tenant_id, {"event": "grant_revoked", "grant_id": grant_id}
    )
    return {**_to_dict(revoked), "audit_recorded": audit_recorded}
