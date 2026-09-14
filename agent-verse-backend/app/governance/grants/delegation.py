"""Delegation — a sub-agent grant that can only NARROW its parent's authority.

When a supervisor spawns a sub-agent, it mints a delegated grant referencing the
parent. Delegation may restrict scope, shorten the window, and lower the cost cap
— never widen any of them. Widening is rejected, so authority strictly decreases
down a multi-agent chain (the Grantex delegation-chain guarantee).
"""

from __future__ import annotations

from datetime import datetime

from app.governance.grants.models import Grant, scope_matches


class DelegationError(ValueError):
    """Raised when a delegation would widen the parent grant's authority."""


def mint_delegation(
    parent: Grant,
    *,
    grant_id: str,
    grantee_agent_id: str,
    scopes: tuple[str, ...],
    expires_at: datetime,
    not_before: datetime | None = None,
    max_cost_usd: float | None = None,
    now: datetime,
) -> Grant:
    """Create a child grant that narrows ``parent``. Raises on any widening."""
    if not parent.is_active(now):
        raise DelegationError("cannot delegate from an inactive (expired/revoked) grant")
    if not scopes:
        raise DelegationError("delegated grant must request at least one scope")

    # Scope narrowing: every child scope must be covered by the parent's scopes.
    for scope in scopes:
        if not scope_matches(parent.scopes, scope):
            raise DelegationError(f"scope '{scope}' is not within parent scopes {parent.scopes}")

    # Window may not extend beyond the parent.
    _nb = not_before or parent.not_before
    if _nb < parent.not_before:
        raise DelegationError("delegated not_before precedes parent")
    if expires_at > parent.expires_at:
        raise DelegationError("delegated expiry extends beyond parent")

    # Cost cap may only tighten. If the parent is capped, the child must be capped
    # at or below it; an uncapped child under a capped parent is a widening.
    child_cap = max_cost_usd
    if parent.max_cost_usd is not None and (
        child_cap is None or child_cap > parent.max_cost_usd
    ):
        raise DelegationError("delegated cost cap exceeds parent")

    return Grant(
        grant_id=grant_id,
        tenant_id=parent.tenant_id,
        grantor=f"agent:{parent.grantee_agent_id}",
        grantee_agent_id=grantee_agent_id,
        scopes=scopes,
        not_before=_nb,
        expires_at=expires_at,
        max_cost_usd=child_cap,
        parent_grant_id=parent.grant_id,
    )


__all__ = ["DelegationError", "mint_delegation"]
