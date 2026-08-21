"""Tenant-scoped optimistic state repository used by pattern-owned read models."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.coordination.store import OptimisticConflictError
from app.db.models.coordination import COORDINATION_TABLES
from app.db.rls import sqlalchemy_rls_context


class PatternRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    session_id: str
    execution_id: str
    state: dict[str, Any]
    version: int = Field(gt=0)
    idempotency_key: str


class InMemoryPatternStateRepository:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], PatternRecord] = {}
        self._commands: dict[tuple[str, str], PatternRecord] = {}
        self._lock = asyncio.Lock()

    async def save(self, record: PatternRecord, *, expected_version: int) -> PatternRecord:
        command = (record.tenant_id, record.idempotency_key)
        async with self._lock:
            if command in self._commands:
                return self._commands[command]
            key = (record.tenant_id, record.session_id, record.execution_id)
            prior = self._records.get(key)
            current = prior.version if prior is not None else 0
            if current != expected_version or record.version != current + 1:
                raise OptimisticConflictError(
                    f"expected version {expected_version}, found {current}"
                )
            self._records[key] = record
            self._commands[command] = record
            return record

    async def get(self, tenant_id: str, session_id: str, execution_id: str) -> PatternRecord | None:
        return self._records.get((tenant_id, session_id, execution_id))

    async def list_session(self, tenant_id: str, session_id: str) -> tuple[PatternRecord, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._records.values()
                    if item.tenant_id == tenant_id and item.session_id == session_id
                ),
                key=lambda item: item.execution_id,
            )
        )


class PostgresPatternStateRepository:
    """Immutable checkpoint-backed state repository for distributed patterns."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    @staticmethod
    def _command_id(record: PatternRecord) -> str:
        return uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{record.tenant_id}:{record.idempotency_key}",
        ).hex

    @staticmethod
    def _decode(row: Any) -> PatternRecord:
        state = json.loads(str(row.state_reference))
        return PatternRecord(
            tenant_id=str(row.tenant_id),
            session_id=str(row.session_id),
            execution_id=str(row.execution_id),
            state=state["state"],
            version=int(row.sequence),
            idempotency_key=str(state["idempotency_key"]),
        )

    async def save(self, record: PatternRecord, *, expected_version: int) -> PatternRecord:
        table = COORDINATION_TABLES["strategy_checkpoints"]
        command_id = self._command_id(record)
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, record.tenant_id),
        ):
            replay = (
                (await db.execute(select(table).where(table.c.id == command_id)))
                .mappings()
                .one_or_none()
            )
            if replay is not None:
                return self._decode(replay)
            latest = (
                (
                    await db.execute(
                        select(table)
                        .where(
                            table.c.session_id == record.session_id,
                            table.c.execution_id == record.execution_id,
                        )
                        .order_by(table.c.sequence.desc())
                        .limit(1)
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            current = int(latest.sequence) if latest is not None else 0
            if current != expected_version or record.version != current + 1:
                raise OptimisticConflictError(
                    f"expected version {expected_version}, found {current}"
                )
            await db.execute(
                insert(table).values(
                    id=command_id,
                    tenant_id=record.tenant_id,
                    session_id=record.session_id,
                    execution_id=record.execution_id,
                    sequence=record.version,
                    state_reference=json.dumps(
                        {
                            "idempotency_key": record.idempotency_key,
                            "state": record.state,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    version=record.version,
                )
            )
        return record

    async def get(self, tenant_id: str, session_id: str, execution_id: str) -> PatternRecord | None:
        records = await self._list(tenant_id, session_id, execution_id=execution_id)
        return records[0] if records else None

    async def list_session(self, tenant_id: str, session_id: str) -> tuple[PatternRecord, ...]:
        records = await self._list(tenant_id, session_id)
        latest: dict[str, PatternRecord] = {}
        for record in records:
            latest.setdefault(record.execution_id, record)
        return tuple(latest[key] for key in sorted(latest))

    async def _list(
        self,
        tenant_id: str,
        session_id: str,
        *,
        execution_id: str | None = None,
    ) -> list[PatternRecord]:
        table = COORDINATION_TABLES["strategy_checkpoints"]
        query = select(table).where(table.c.session_id == session_id)
        if execution_id is not None:
            query = query.where(table.c.execution_id == execution_id)
        query = query.order_by(table.c.execution_id, table.c.sequence.desc())
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            rows = (await db.execute(query)).mappings().all()
        return [self._decode(row) for row in rows]


__all__ = [
    "InMemoryPatternStateRepository",
    "PatternRecord",
    "PostgresPatternStateRepository",
]
