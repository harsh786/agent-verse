"""Fail-closed tenant-scoped civilization membership authorization."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from app.db.models.agent import Agent
from app.db.models.civilization import CivilizationAgent
from app.db.models.coordination import COORDINATION_TABLES
from app.db.rls import sqlalchemy_rls_context


class DatabaseHandoffMembership:
    def __init__(self, session_factory: Callable[[], Any | None]) -> None:
        self._session_factory = session_factory

    async def active_member(self, tenant_id: str, civilization_id: str, agent_id: str) -> bool:
        factory = self._session_factory()
        if factory is None:
            return False
        async with (
            factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            value = await session.scalar(
                select(CivilizationAgent.id).where(
                    CivilizationAgent.tenant_id == tenant_id,
                    CivilizationAgent.civilization_id == civilization_id,
                    CivilizationAgent.agent_id == agent_id,
                    CivilizationAgent.status == "active",
                )
            )
            return value is not None

    async def connector_allowlist(
        self, tenant_id: str, civilization_id: str, agent_id: str
    ) -> frozenset[str]:
        if not await self.active_member(tenant_id, civilization_id, agent_id):
            return frozenset()
        factory = self._session_factory()
        if factory is None:
            return frozenset()
        async with (
            factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            connectors = await session.scalar(
                select(Agent.connector_ids).where(
                    Agent.tenant_id == tenant_id,
                    Agent.id == agent_id,
                    Agent.is_active.is_(True),
                    Agent.is_archived.is_(False),
                )
            )
            return frozenset(str(item) for item in (connectors or ()))


class DatabaseSessionAuthorizer:
    def __init__(self, session_factory: Callable[[], Any | None]) -> None:
        self._session_factory = session_factory

    async def __call__(self, tenant_id: str, session_id: str) -> bool:
        factory = self._session_factory()
        if factory is None:
            return False
        sessions = COORDINATION_TABLES["coordination_sessions"]
        async with (
            factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            value = await session.scalar(select(sessions.c.id).where(sessions.c.id == session_id))
            return value is not None


class InMemorySessionAuthorizer:
    def __init__(self, store: Any) -> None:
        self._store = store

    async def __call__(self, tenant_id: str, session_id: str) -> bool:
        return bool(await self._store.contains(tenant_id, session_id))


__all__ = [
    "DatabaseHandoffMembership",
    "DatabaseSessionAuthorizer",
    "InMemorySessionAuthorizer",
]
