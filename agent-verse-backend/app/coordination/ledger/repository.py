"""Concurrency-safe append-only progress-ledger repository."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.coordination.ledger.models import LedgerRevision
from app.coordination.store import OptimisticConflictError
from app.db.models.coordination import COORDINATION_TABLES
from app.db.rls import sqlalchemy_rls_context


class InMemoryProgressLedgerRepository:
    def __init__(self) -> None:
        self._revisions: dict[tuple[str, str], list[LedgerRevision]] = {}
        self._commands: dict[tuple[str, str, str], LedgerRevision] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def append(
        self,
        revision: LedgerRevision,
        *,
        expected_predecessor_version: int,
    ) -> LedgerRevision:
        key = (revision.tenant_id, revision.session_id)
        command = (*key, revision.idempotency_key)
        async with self._locks.setdefault(key, asyncio.Lock()):
            prior = self._commands.get(command)
            if prior is not None:
                return prior
            history = self._revisions.setdefault(key, [])
            current = history[-1].version if history else 0
            if current != expected_predecessor_version:
                raise OptimisticConflictError(
                    f"expected predecessor {expected_predecessor_version}, found {current}"
                )
            if revision.version != current + 1:
                raise ValueError("ledger revision version must be contiguous")
            accepted = revision.model_copy(
                update={"predecessor_version": current or None}
            )
            history.append(accepted)
            self._commands[command] = accepted
            return accepted

    async def current(self, tenant_id: str, session_id: str) -> LedgerRevision | None:
        history = self._revisions.get((tenant_id, session_id), ())
        return history[-1] if history else None

    async def revisions(
        self,
        tenant_id: str,
        session_id: str,
        *,
        after_version: int = 0,
        limit: int = 100,
    ) -> tuple[LedgerRevision, ...]:
        return tuple(
            item
            for item in self._revisions.get((tenant_id, session_id), ())
            if item.version > after_version
        )[: max(1, min(limit, 500))]


def _revision_from_row(row: Any) -> LedgerRevision:
    return LedgerRevision.model_validate(row.state)


class PostgresProgressLedgerRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def append(
        self,
        revision: LedgerRevision,
        *,
        expected_predecessor_version: int,
    ) -> LedgerRevision:
        table = COORDINATION_TABLES["progress_ledger_revisions"]
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, revision.tenant_id),
        ):
            current_row = (
                await db.execute(
                    select(table)
                    .where(table.c.session_id == revision.session_id)
                    .order_by(table.c.version.desc())
                    .limit(1)
                    .with_for_update()
                )
            ).mappings().one_or_none()
            if current_row is not None:
                current = _revision_from_row(current_row)
                if current.idempotency_key == revision.idempotency_key:
                    return current
                current_version = current.version
            else:
                current_version = 0
            if current_version != expected_predecessor_version:
                raise OptimisticConflictError(
                    f"expected predecessor {expected_predecessor_version}, "
                    f"found {current_version}"
                )
            if revision.version != current_version + 1:
                raise ValueError("ledger revision version must be contiguous")
            accepted = revision.model_copy(
                update={"predecessor_version": current_version or None}
            )
            await db.execute(
                insert(table).values(
                    id=uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"ledger:{revision.tenant_id}:{revision.session_id}:{revision.version}",
                    ).hex,
                    tenant_id=accepted.tenant_id,
                    session_id=accepted.session_id,
                    objective=accepted.objective,
                    state=accepted.model_dump(mode="json"),
                    version=accepted.version,
                    created_at=accepted.created_at,
                    updated_at=accepted.created_at,
                )
            )
            return accepted

    async def current(self, tenant_id: str, session_id: str) -> LedgerRevision | None:
        table = COORDINATION_TABLES["progress_ledger_revisions"]
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            row = (
                await db.execute(
                    select(table)
                    .where(table.c.session_id == session_id)
                    .order_by(table.c.version.desc())
                    .limit(1)
                )
            ).mappings().one_or_none()
            return _revision_from_row(row) if row is not None else None

    async def revisions(
        self,
        tenant_id: str,
        session_id: str,
        *,
        after_version: int = 0,
        limit: int = 100,
    ) -> tuple[LedgerRevision, ...]:
        table = COORDINATION_TABLES["progress_ledger_revisions"]
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            rows = (
                await db.execute(
                    select(table)
                    .where(
                        table.c.session_id == session_id,
                        table.c.version > after_version,
                    )
                    .order_by(table.c.version)
                    .limit(max(1, min(limit, 500)))
                )
            ).mappings()
            return tuple(_revision_from_row(row) for row in rows)


__all__ = [
    "InMemoryProgressLedgerRepository",
    "PostgresProgressLedgerRepository",
]
