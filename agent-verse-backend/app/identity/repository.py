"""PostgresIdentityStore — durable principals + identity links (Phase 3).

Backs :class:`app.identity.service.IdentityService` with the ``principals`` /
``identity_links`` tables (migration 0131) so unified cross-channel identity
survives restarts and spans workers. Every query runs inside an RLS tenant context
AND filters by ``tenant_id`` explicitly (defence-in-depth).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import sqlalchemy_rls_context
from app.identity.models import IdentityLink, Principal


class PostgresIdentityStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get_principal(self, principal_id: str, tenant_id: str) -> Principal | None:
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text("SELECT * FROM principals WHERE id = :id AND tenant_id = :t"),
                    {"id": principal_id, "t": tenant_id},
                )
            ).mappings().one_or_none()
            return self._principal(row) if row else None

    async def create_principal(self, principal: Principal) -> None:
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, principal.tenant_id):
            await s.execute(
                text(
                    "INSERT INTO principals (id, tenant_id, kind, display_name, created_at) "
                    "VALUES (:id, :t, :kind, :dn, :ca) ON CONFLICT (id) DO NOTHING"
                ),
                {
                    "id": principal.id,
                    "t": principal.tenant_id,
                    "kind": principal.kind,
                    "dn": principal.display_name,
                    "ca": principal.created_at,
                },
            )

    async def get_link(
        self, tenant_id: str, channel: str, channel_user_id: str
    ) -> IdentityLink | None:
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            row = (
                await s.execute(
                    text(
                        "SELECT * FROM identity_links WHERE tenant_id = :t "
                        "AND channel = :c AND channel_user_id = :u"
                    ),
                    {"t": tenant_id, "c": channel, "u": channel_user_id},
                )
            ).mappings().one_or_none()
            return self._link(row) if row else None

    async def create_link(self, link: IdentityLink) -> IdentityLink:
        """Insert *link* unless the identity is already bound; return the binding
        that is in the table (the existing one when another request won)."""
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, link.tenant_id):
            won = (
                await s.execute(
                    text(
                        "INSERT INTO identity_links "
                        "(tenant_id, channel, channel_user_id, principal_id, created_at) "
                        "VALUES (:t, :c, :u, :p, :ca) "
                        "ON CONFLICT (tenant_id, channel, channel_user_id) DO NOTHING "
                        "RETURNING principal_id"
                    ),
                    {
                        "t": link.tenant_id,
                        "c": link.channel,
                        "u": link.channel_user_id,
                        "p": link.principal_id,
                        "ca": link.created_at,
                    },
                )
            ).scalar_one_or_none()
            if won is not None:
                return link
            row = (
                await s.execute(
                    text(
                        "SELECT * FROM identity_links WHERE tenant_id = :t "
                        "AND channel = :c AND channel_user_id = :u"
                    ),
                    {"t": link.tenant_id, "c": link.channel, "u": link.channel_user_id},
                )
            ).mappings().one()
            return self._link(row)

    async def claim_link(self, principal: Principal, link: IdentityLink) -> Principal:
        """First contact: create *principal* bound to *link* — or, when a concurrent
        request bound the identity first, return THAT principal (a10-F249-02).

        One transaction. The link insert is ``ON CONFLICT DO NOTHING RETURNING``;
        when it loses, the bound principal is read back in a NEW statement (READ
        COMMITTED: it sees the winner's committed rows, which the insert's own
        snapshot does not) and this request's principal is deleted again, so no
        orphan is left. A link whose principal no longer exists is re-pointed,
        conditionally on the value just read, so a live binding is never displaced.
        """
        t = principal.tenant_id
        key = {"t": t, "c": link.channel, "u": link.channel_user_id}
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, t):
            await s.execute(
                text(
                    "INSERT INTO principals (id, tenant_id, kind, display_name, created_at) "
                    "VALUES (:id, :t, :kind, :dn, :ca) ON CONFLICT (id) DO NOTHING"
                ),
                {
                    "id": principal.id,
                    "t": t,
                    "kind": principal.kind,
                    "dn": principal.display_name,
                    "ca": principal.created_at,
                },
            )
            won = (
                await s.execute(
                    text(
                        "INSERT INTO identity_links "
                        "(tenant_id, channel, channel_user_id, principal_id, created_at) "
                        "VALUES (:t, :c, :u, :p, :ca) "
                        "ON CONFLICT (tenant_id, channel, channel_user_id) DO NOTHING "
                        "RETURNING principal_id"
                    ),
                    {**key, "p": principal.id, "ca": link.created_at},
                )
            ).scalar_one_or_none()
            if won is not None:
                return principal
            row = (
                await s.execute(
                    text(
                        "SELECT l.principal_id AS bound_id, p.id, p.tenant_id, p.kind, "
                        "p.display_name, p.created_at FROM identity_links l "
                        "LEFT JOIN principals p "
                        "ON p.id = l.principal_id AND p.tenant_id = l.tenant_id "
                        "WHERE l.tenant_id = :t AND l.channel = :c AND l.channel_user_id = :u"
                    ),
                    key,
                )
            ).mappings().one()
            if row["id"] is not None:
                await s.execute(
                    text("DELETE FROM principals WHERE id = :id AND tenant_id = :t"),
                    {"id": principal.id, "t": t},
                )
                return self._principal(row)
            # Dangling link (its principal was deleted): re-point it to ours, only
            # if it still names the principal we saw missing.
            repointed = (
                await s.execute(
                    text(
                        "UPDATE identity_links SET principal_id = :p, created_at = :ca "
                        "WHERE tenant_id = :t AND channel = :c AND channel_user_id = :u "
                        "AND principal_id = :old RETURNING principal_id"
                    ),
                    {**key, "p": principal.id, "ca": link.created_at, "old": row["bound_id"]},
                )
            ).scalar_one_or_none()
            if repointed is None:
                raise RuntimeError(
                    "identity link changed concurrently while re-pointing a dangling link"
                )
            return principal

    async def links_for_principal(
        self, principal_id: str, tenant_id: str
    ) -> list[IdentityLink]:
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
            rows = (
                await s.execute(
                    text(
                        "SELECT * FROM identity_links "
                        "WHERE tenant_id = :t AND principal_id = :p ORDER BY created_at"
                    ),
                    {"t": tenant_id, "p": principal_id},
                )
            ).mappings().all()
            return [self._link(r) for r in rows]

    @staticmethod
    def _principal(row: Any) -> Principal:
        return Principal(
            id=str(row["id"]),
            tenant_id=str(row["tenant_id"]),
            kind=str(row["kind"]),
            display_name=row["display_name"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _link(row: Any) -> IdentityLink:
        return IdentityLink(
            tenant_id=str(row["tenant_id"]),
            channel=str(row["channel"]),
            channel_user_id=str(row["channel_user_id"]),
            principal_id=str(row["principal_id"]),
            created_at=row["created_at"] or datetime.now(),
        )
