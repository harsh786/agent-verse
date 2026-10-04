"""Grant storage. In-memory implementation; Postgres repo is the follow-up.

Mirrors the coordination InMemory*/Postgres* repository pattern already used
across the codebase — the in-memory store is a real, tested implementation
(not a stub) behind a Protocol the enforcer depends on.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Protocol

from app.governance.grants.models import Grant


class GrantStore(Protocol):
    async def issue(self, grant: Grant) -> Grant: ...
    async def get(self, tenant_id: str, grant_id: str) -> Grant | None: ...
    async def revoke(self, tenant_id: str, grant_id: str) -> Grant | None: ...
    async def list_for_agent(self, tenant_id: str, agent_id: str) -> tuple[Grant, ...]: ...
    async def record_spend(
        self, tenant_id: str, grant_id: str, cost_usd: float
    ) -> float: ...


class InMemoryGrantStore:
    """Tenant-scoped in-memory grant store (idempotent issue by grant_id)."""

    def __init__(self) -> None:
        self._grants: dict[tuple[str, str], Grant] = {}
        self._lock = asyncio.Lock()

    async def issue(self, grant: Grant) -> Grant:
        async with self._lock:
            key = (grant.tenant_id, grant.grant_id)
            existing = self._grants.get(key)
            if existing is not None:
                return existing  # idempotent
            self._grants[key] = grant
            return grant

    async def get(self, tenant_id: str, grant_id: str) -> Grant | None:
        return self._grants.get((tenant_id, grant_id))

    async def revoke(self, tenant_id: str, grant_id: str) -> Grant | None:
        """Revoke a grant **and every grant delegated from it, transitively**.

        Delegation is the mechanism by which a supervisor hands narrowed
        authority to sub-agents; revocation that stopped at the named row left
        every one of those sub-agents holding the revoked agent's scopes until
        their own expiry, which is exactly the situation revocation exists to
        end. The cascade walks ``parent_grant_id`` downwards only — revoking a
        child never touches its parent.
        """
        async with self._lock:
            key = (tenant_id, grant_id)
            grant = self._grants.get(key)
            if grant is None:
                return None

            children: dict[str, list[str]] = {}
            for (tid, gid), g in self._grants.items():
                if tid == tenant_id and g.parent_grant_id:
                    children.setdefault(g.parent_grant_id, []).append(gid)

            to_revoke: list[str] = []
            frontier = [grant_id]
            seen = {grant_id}
            while frontier:
                current = frontier.pop()
                to_revoke.append(current)
                for child_id in children.get(current, []):
                    if child_id not in seen:  # cycles cannot happen, but be safe
                        seen.add(child_id)
                        frontier.append(child_id)

            for gid in to_revoke:
                existing = self._grants.get((tenant_id, gid))
                if existing is not None:
                    self._grants[(tenant_id, gid)] = Grant(
                        **{**existing.__dict__, "revoked": True}
                    )
            return self._grants[key]

    async def record_spend(self, tenant_id: str, grant_id: str, cost_usd: float) -> float:
        """Add ``cost_usd`` to the grant's cumulative spend; return the new total."""
        async with self._lock:
            key = (tenant_id, grant_id)
            grant = self._grants.get(key)
            if grant is None:
                return 0.0
            total = float(grant.spent_usd) + float(cost_usd)
            self._grants[key] = Grant(**{**grant.__dict__, "spent_usd": total})
            return total

    async def list_for_agent(self, tenant_id: str, agent_id: str) -> tuple[Grant, ...]:
        return tuple(
            g
            for (t, _gid), g in self._grants.items()
            if t == tenant_id and g.grantee_agent_id == agent_id
        )

    async def active_for_agent(
        self, tenant_id: str, agent_id: str, *, now: datetime
    ) -> tuple[Grant, ...]:
        return tuple(
            g
            for (t, _gid), g in self._grants.items()
            if t == tenant_id and g.grantee_agent_id == agent_id and g.is_active(now)
        )

    async def has_any_for_agent(self, tenant_id: str, agent_id: str) -> bool:
        return any(
            t == tenant_id and g.grantee_agent_id == agent_id
            for (t, _gid), g in self._grants.items()
        )


__all__ = ["GrantStore", "InMemoryGrantStore"]
