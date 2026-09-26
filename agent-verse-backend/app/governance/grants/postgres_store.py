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
    "max_cost_usd, spent_usd, revoked, parent_grant_id, metadata"
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
        spent_usd=float(row["spent_usd"] or 0.0),
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
                    ":spent_usd, :revoked, :parent_grant_id, CAST(:metadata AS jsonb)) "
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
                    "spent_usd": grant.spent_usd,
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
                    text(
                        f"SELECT {_COLS} FROM agent_grants "
                        "WHERE grant_id = :gid AND tenant_id = :tid"
                    ),
                    {"gid": grant_id, "tid": tenant_id},
                )
            ).mappings().one_or_none()
        return _row_to_grant(row) if row else None

    async def revoke(self, tenant_id: str, grant_id: str) -> Grant | None:
        """Revoke a grant **and every grant delegated from it, transitively**.

        A single-row UPDATE left every sub-agent that the revoked agent had
        delegated to holding the same scopes until their own expiry — the exact
        situation revocation exists to end. One recursive statement so every
        replica sees the whole chain revoked atomically; the walk follows
        ``parent_grant_id`` downwards only, so revoking a child never touches its
        parent.
        """
        async with self._sf() as session, session.begin(), sqlalchemy_rls_context(
            session, tenant_id
        ):
            await session.execute(
                text(
                    """
                    WITH RECURSIVE chain AS (
                        SELECT grant_id FROM agent_grants
                         WHERE grant_id = :gid AND tenant_id = :tid
                        UNION
                        SELECT g.grant_id FROM agent_grants g
                          JOIN chain c ON g.parent_grant_id = c.grant_id
                         WHERE g.tenant_id = :tid
                    )
                    UPDATE agent_grants SET revoked = TRUE
                     WHERE tenant_id = :tid
                       AND grant_id IN (SELECT grant_id FROM chain)
                    """
                ),
                {"gid": grant_id, "tid": tenant_id},
            )
        return await self.get(tenant_id, grant_id)

    async def record_spend(self, tenant_id: str, grant_id: str, cost_usd: float) -> float:
        """Atomically add ``cost_usd`` to the grant's cumulative spend.

        The increment happens in SQL rather than read-modify-write so concurrent
        executors on different replicas cannot lose each other's charges — which
        would let an agent overrun its budget by exactly the amount that went
        missing.
        """
        async with self._sf() as session, session.begin(), sqlalchemy_rls_context(
            session, tenant_id
        ):
            total = (
                await session.execute(
                    text(
                        "UPDATE agent_grants "
                        "SET spent_usd = spent_usd + :cost "
                        "WHERE grant_id = :gid AND tenant_id = :tid "
                        "RETURNING spent_usd"
                    ),
                    {"cost": cost_usd, "gid": grant_id, "tid": tenant_id},
                )
            ).scalar_one_or_none()
        return float(total or 0.0)

    async def list_for_agent(self, tenant_id: str, agent_id: str) -> tuple[Grant, ...]:
        async with self._sf() as session, session.begin(), sqlalchemy_rls_context(
            session, tenant_id
        ):
            rows = (
                await session.execute(
                    text(
                        f"SELECT {_COLS} FROM agent_grants "
                        "WHERE grantee_agent_id = :aid AND tenant_id = :tid"
                    ),
                    {"aid": agent_id, "tid": tenant_id},
                )
            ).mappings().all()
        return tuple(_row_to_grant(r) for r in rows)

    async def active_for_agent(
        self, tenant_id: str, agent_id: str, *, now: datetime
    ) -> tuple[Grant, ...]:
        grants = await self.list_for_agent(tenant_id, agent_id)
        return tuple(g for g in grants if g.is_active(now))


__all__ = ["PostgresGrantStore"]
