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

    async def create_link(self, link: IdentityLink) -> None:
        async with self._sf() as s, s.begin(), sqlalchemy_rls_context(s, link.tenant_id):
            await s.execute(
                text(
                    "INSERT INTO identity_links "
                    "(tenant_id, channel, channel_user_id, principal_id, created_at) "
                    "VALUES (:t, :c, :u, :p, :ca) "
                    "ON CONFLICT (tenant_id, channel, channel_user_id) DO NOTHING"
                ),
                {
                    "t": link.tenant_id,
                    "c": link.channel,
                    "u": link.channel_user_id,
                    "p": link.principal_id,
                    "ca": link.created_at,
                },
            )

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
