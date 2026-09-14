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
        async with self._lock:
            key = (tenant_id, grant_id)
            grant = self._grants.get(key)
            if grant is None:
                return None
            revoked = Grant(**{**grant.__dict__, "revoked": True})
            self._grants[key] = revoked
            return revoked

    async def list_for_agent(self, tenant_id: str, agent_id: str) -> tuple[Grant, ...]:
        return tuple(
            g
            for (t, _gid), g in self._grants.items()
            if t == tenant_id and g.grantee_agent_id == agent_id
        )

    async def active_for_agent(
        self, tenant_id: str, agent_id: str, *, now: datetime
    ) -> tuple[Grant, ...]:
        grants = await self.list_for_agent(tenant_id, agent_id)
        return tuple(g for g in grants if g.is_active(now))


__all__ = ["GrantStore", "InMemoryGrantStore"]
