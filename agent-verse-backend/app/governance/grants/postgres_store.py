"""Postgres-backed grant store — persists grants with tenant RLS.

Same interface as ``InMemoryGrantStore`` so it drops into the enforcer/executor
unchanged. Every statement runs inside ``sqlalchemy_rls_context`` so the
``agent_grants`` RLS policy scopes rows to the tenant.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text

from app.db.rls import sqlalchemy_rls_context
from app.governance.grants.models import Grant

_COLS = (
    "grant_id, tenant_id, grantor, grantee_agent_id, scopes, not_before, expires_at, "
    "max_cost_usd, revoked, parent_grant_id, metadata"
)


def _row_to_grant(row: Any) -> Grant:
    scopes = row["scopes"]
    if isinstance(scopes, str):
        scopes = json.loads(scopes)
    meta = row["metadata"]
    if isinstance(meta, str):
        meta = json.loads(meta)
    return Grant(
        grant_id=str(row["grant_id"]),
        tenant_id=str(row["tenant_id"]),
        grantor=str(row["grantor"]),
        grantee_agent_id=str(row["grantee_agent_id"]),
        scopes=tuple(scopes or ()),
        not_before=row["not_before"],
        expires_at=row["expires_at"],
        max_cost_usd=row["max_cost_usd"],
        revoked=bool(row["revoked"]),
        parent_grant_id=row["parent_grant_id"],
        metadata=dict(meta or {}),
    )


class PostgresGrantStore:
    def __init__(self, session_factory: Any) -> None:
        self._sf = session_factory

    async def issue(self, grant: Grant) -> Grant:
        async with self._sf() as session, session.begin(), sqlalchemy_rls_context(
            session, grant.tenant_id
        ):
            # Idempotent: first writer wins (ON CONFLICT DO NOTHING), then read back.
            await session.execute(
                text(
                    f"INSERT INTO agent_grants ({_COLS}) VALUES "
                    "(:grant_id, :tenant_id, :grantor, :grantee_agent_id, "
                    "CAST(:scopes AS jsonb), :not_before, :expires_at, :max_cost_usd, "
                    ":revoked, :parent_grant_id, CAST(:metadata AS jsonb)) "
                    "ON CONFLICT (grant_id) DO NOTHING"
                ),
                {
                    "grant_id": grant.grant_id,
                    "tenant_id": grant.tenant_id,
                    "grantor": grant.grantor,
                    "grantee_agent_id": grant.grantee_agent_id,
                    "scopes": json.dumps(list(grant.scopes)),
                    "not_before": grant.not_before,
                    "expires_at": grant.expires_at,
                    "max_cost_usd": grant.max_cost_usd,
                    "revoked": grant.revoked,
                    "parent_grant_id": grant.parent_grant_id,
                    "metadata": json.dumps(grant.metadata),
                },
            )
        stored = await self.get(grant.tenant_id, grant.grant_id)
        return stored or grant

    async def get(self, tenant_id: str, grant_id: str) -> Grant | None:
        async with self._sf() as session, session.begin(), sqlalchemy_rls_context(
            session, tenant_id
        ):
            row = (
                await session.execute(
                    text(f"SELECT {_COLS} FROM agent_grants WHERE grant_id = :gid"),
                    {"gid": grant_id},
                )
            ).mappings().one_or_none()
        return _row_to_grant(row) if row else None

    async def revoke(self, tenant_id: str, grant_id: str) -> Grant | None:
        async with self._sf() as session, session.begin(), sqlalchemy_rls_context(
            session, tenant_id
        ):
            await session.execute(
                text("UPDATE agent_grants SET revoked = TRUE WHERE grant_id = :gid"),
                {"gid": grant_id},
            )
        return await self.get(tenant_id, grant_id)

    async def list_for_agent(self, tenant_id: str, agent_id: str) -> tuple[Grant, ...]:
        async with self._sf() as session, session.begin(), sqlalchemy_rls_context(
            session, tenant_id
        ):
            rows = (
                await session.execute(
                    text(
                        f"SELECT {_COLS} FROM agent_grants WHERE grantee_agent_id = :aid"
                    ),
                    {"aid": agent_id},
                )
            ).mappings().all()
        return tuple(_row_to_grant(r) for r in rows)

    async def active_for_agent(
        self, tenant_id: str, agent_id: str, *, now: datetime
    ) -> tuple[Grant, ...]:
        grants = await self.list_for_agent(tenant_id, agent_id)
        return tuple(g for g in grants if g.is_active(now))


__all__ = ["PostgresGrantStore"]
