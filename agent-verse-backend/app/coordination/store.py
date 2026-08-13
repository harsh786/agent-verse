"""PostgreSQL-canonical transactional coordination repository."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.coordination.state_machines import transition_session
from app.db.models.coordination import COORDINATION_TABLES
from app.db.rls import sqlalchemy_rls_context
from app.tenancy.context import TenantContext


class OptimisticConflictError(RuntimeError):
    """The supplied expected version is stale."""


class CoordinationSessionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    tenant_id: str
    state: str
    next_sequence: int
    version: int


class AcceptedTransition(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str
    session_id: str
    sequence: int
    state: str
    version: int
    idempotency_key: str


class CoordinationStore:
    """Commit domain state, event, and outbox intent in one RLS transaction."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create_session(
        self,
        tenant_ctx: TenantContext,
        *,
        civilization_id: str,
        goal_id: str,
        policy_snapshot: dict[str, Any],
        budget_snapshot: dict[str, Any],
    ) -> CoordinationSessionRecord:
        session_id = uuid.uuid4().hex
        table = COORDINATION_TABLES["coordination_sessions"]
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_ctx.tenant_id),
        ):
            await db.execute(
                insert(table).values(
                    id=session_id,
                    tenant_id=tenant_ctx.tenant_id,
                    civilization_id=civilization_id,
                    goal_id=goal_id,
                    state="pending",
                    policy_snapshot=policy_snapshot,
                    budget_snapshot=budget_snapshot,
                    deadline=None,
                    cancellation_requested_at=None,
                    next_sequence=1,
                    version=1,
                )
            )
        return CoordinationSessionRecord(
            session_id=session_id,
            tenant_id=tenant_ctx.tenant_id,
            state="pending",
            next_sequence=1,
            version=1,
        )

    async def get_session(
        self,
        tenant_ctx: TenantContext,
        *,
        session_id: str,
    ) -> CoordinationSessionRecord:
        table = COORDINATION_TABLES["coordination_sessions"]
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_ctx.tenant_id),
        ):
            row = (
                await db.execute(
                    select(
                        table.c.id,
                        table.c.tenant_id,
                        table.c.state,
                        table.c.next_sequence,
                        table.c.version,
                    ).where(table.c.id == session_id)
                )
            ).one_or_none()
        if row is None:
            raise KeyError(f"coordination session not found: {session_id}")
        return CoordinationSessionRecord(
            session_id=str(row.id),
            tenant_id=str(row.tenant_id),
            state=str(row.state),
            next_sequence=int(row.next_sequence),
            version=int(row.version),
        )

    async def transition_session(
        self,
        tenant_ctx: TenantContext,
        *,
        session_id: str,
        expected_version: int,
        target_state: str,
        idempotency_key: str,
    ) -> AcceptedTransition:
        event_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{tenant_ctx.tenant_id}:{session_id}:{idempotency_key}",
        ).hex
        sessions = COORDINATION_TABLES["coordination_sessions"]
        events = COORDINATION_TABLES["coordination_events"]
        outbox = COORDINATION_TABLES["coordination_outbox"]

        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_ctx.tenant_id),
        ):
            replay = (
                await db.execute(select(events.c.payload).where(events.c.id == event_id))
            ).scalar_one_or_none()
            if replay is not None:
                return AcceptedTransition.model_validate(replay["accepted"])

            row = (
                await db.execute(
                    select(
                        sessions.c.state,
                        sessions.c.next_sequence,
                        sessions.c.version,
                    )
                    .where(sessions.c.id == session_id)
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                raise KeyError(f"coordination session not found: {session_id}")
            if row.version != expected_version:
                raise OptimisticConflictError(
                    f"expected version {expected_version}, found {row.version}"
                )
            new_state = transition_session(row.state, target_state)
            sequence = int(row.next_sequence)
            new_version = int(row.version) + 1
            accepted = AcceptedTransition(
                event_id=event_id,
                session_id=session_id,
                sequence=sequence,
                state=new_state,
                version=new_version,
                idempotency_key=idempotency_key,
            )
            occurred_at = datetime.now(UTC)
            payload = {
                "kind": "session_transition",
                "from_state": row.state,
                "to_state": new_state,
                "accepted": accepted.model_dump(),
            }
            await db.execute(
                update(sessions)
                .where(
                    sessions.c.id == session_id,
                    sessions.c.version == expected_version,
                )
                .values(
                    state=new_state,
                    next_sequence=sequence + 1,
                    version=new_version,
                    updated_at=occurred_at,
                )
            )
            await db.execute(
                insert(events).values(
                    id=event_id,
                    tenant_id=tenant_ctx.tenant_id,
                    session_id=session_id,
                    sequence=sequence,
                    schema_version=1,
                    event_type="session.state_changed",
                    occurred_at=occurred_at,
                    correlation_id=session_id,
                    causation_id="",
                    idempotency_key=idempotency_key,
                    classification="internal",
                    expires_at=None,
                    payload=payload,
                    version=1,
                )
            )
            envelope = {
                "event_id": event_id,
                "tenant_id": tenant_ctx.tenant_id,
                "session_id": session_id,
                "sequence": sequence,
                "schema_version": 1,
                "event_type": "session.state_changed",
                "occurred_at": occurred_at.isoformat(),
                "correlation_id": session_id,
                "causation_id": None,
                "idempotency_key": idempotency_key,
                "classification": "internal",
                "payload": payload,
            }
            await db.execute(
                insert(outbox).values(
                    id=uuid.uuid4().hex,
                    event_id=event_id,
                    tenant_id=tenant_ctx.tenant_id,
                    session_id=session_id,
                    stream=f"coord:{tenant_ctx.tenant_id}:{session_id}",
                    payload=envelope,
                    state="pending",
                    attempt_count=0,
                    available_at=occurred_at,
                    claim_owner="",
                    claimed_at=None,
                    published_at=None,
                    last_error="",
                    version=1,
                )
            )
            return accepted


class InMemoryCoordinationStore:
    """Explicit development/test double with the same tenant and CAS semantics."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], CoordinationSessionRecord] = {}
        self._accepted: dict[tuple[str, str, str], AcceptedTransition] = {}
        self._lock = asyncio.Lock()

    async def create_session(
        self,
        tenant_ctx: TenantContext,
        *,
        civilization_id: str,
        goal_id: str,
        policy_snapshot: dict[str, Any],
        budget_snapshot: dict[str, Any],
    ) -> CoordinationSessionRecord:
        del civilization_id, goal_id, policy_snapshot, budget_snapshot
        record = CoordinationSessionRecord(
            session_id=uuid.uuid4().hex,
            tenant_id=tenant_ctx.tenant_id,
            state="pending",
            next_sequence=1,
            version=1,
        )
        async with self._lock:
            self._sessions[(tenant_ctx.tenant_id, record.session_id)] = record
        return record

    async def get_session(
        self,
        tenant_ctx: TenantContext,
        *,
        session_id: str,
    ) -> CoordinationSessionRecord:
        async with self._lock:
            record = self._sessions.get((tenant_ctx.tenant_id, session_id))
        if record is None:
            raise KeyError(f"coordination session not found: {session_id}")
        return record

    async def contains(self, tenant_id: str, session_id: str) -> bool:
        async with self._lock:
            return (tenant_id, session_id) in self._sessions

    async def transition_session(
        self,
        tenant_ctx: TenantContext,
        *,
        session_id: str,
        expected_version: int,
        target_state: str,
        idempotency_key: str,
    ) -> AcceptedTransition:
        key = (tenant_ctx.tenant_id, session_id)
        replay_key = (*key, idempotency_key)
        async with self._lock:
            if replay_key in self._accepted:
                return self._accepted[replay_key]
            current = self._sessions.get(key)
            if current is None:
                raise KeyError(f"coordination session not found: {session_id}")
            if current.version != expected_version:
                raise OptimisticConflictError(
                    f"expected version {expected_version}, found {current.version}"
                )
            state = transition_session(current.state, target_state)
            accepted = AcceptedTransition(
                event_id=uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{tenant_ctx.tenant_id}:{session_id}:{idempotency_key}",
                ).hex,
                session_id=session_id,
                sequence=current.next_sequence,
                state=state,
                version=current.version + 1,
                idempotency_key=idempotency_key,
            )
            self._sessions[key] = CoordinationSessionRecord(
                session_id=session_id,
                tenant_id=tenant_ctx.tenant_id,
                state=state,
                next_sequence=current.next_sequence + 1,
                version=current.version + 1,
            )
            self._accepted[replay_key] = accepted
            return accepted


class PostgresReplayRepository:
    """Sequence-indexed event reader used when Redis is absent or behind."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def page(
        self,
        *,
        tenant_id: str,
        session_id: str,
        after_sequence: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        events = COORDINATION_TABLES["coordination_events"]
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            rows = (
                await db.execute(
                    select(
                        events.c.tenant_id,
                        events.c.session_id,
                        events.c.sequence,
                        events.c.id.label("event_id"),
                        events.c.schema_version,
                        events.c.event_type,
                        events.c.occurred_at,
                        events.c.correlation_id,
                        events.c.causation_id,
                        events.c.classification,
                        events.c.payload,
                    )
                    .where(
                        events.c.session_id == session_id,
                        events.c.sequence > after_sequence,
                    )
                    .order_by(events.c.sequence)
                    .limit(limit)
                )
            ).mappings()
            return [dict(row) for row in rows]


__all__ = [
    "AcceptedTransition",
    "CoordinationSessionRecord",
    "CoordinationStore",
    "InMemoryCoordinationStore",
    "OptimisticConflictError",
    "PostgresReplayRepository",
]
