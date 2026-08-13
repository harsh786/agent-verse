"""Compare-and-set handoff repository with command idempotency."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from sqlalchemy import insert, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.coordination.handoffs.models import HandoffRecord, HandoffState, HandoffTransition
from app.coordination.handoffs.state_machine import transition_handoff
from app.coordination.store import OptimisticConflictError
from app.db.models.coordination import COORDINATION_TABLES
from app.db.rls import sqlalchemy_rls_context


class InMemoryHandoffRepository:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], HandoffRecord] = {}
        self._commands: dict[tuple[str, str, str], HandoffTransition] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def create(self, record: HandoffRecord) -> tuple[HandoffRecord, bool]:
        key = (record.tenant_id, record.handoff_id)
        async with self._locks.setdefault(key, asyncio.Lock()):
            existing = self._records.get(key)
            if existing is not None:
                if existing.idempotency_key == record.idempotency_key:
                    return existing, True
                raise OptimisticConflictError("handoff already exists")
            self._records[key] = record
            return record, False

    async def get(self, tenant_id: str, handoff_id: str) -> HandoffRecord | None:
        return self._records.get((tenant_id, handoff_id))

    async def transition(
        self,
        tenant_id: str,
        handoff_id: str,
        *,
        target: HandoffState,
        expected_version: int,
        idempotency_key: str,
        result_reference: str | None = None,
    ) -> tuple[HandoffRecord, HandoffTransition, bool]:
        command_key = (tenant_id, handoff_id, idempotency_key)
        prior = self._commands.get(command_key)
        if prior is not None:
            replay_record = self._records[(tenant_id, handoff_id)]
            return replay_record, prior, True
        key = (tenant_id, handoff_id)
        async with self._locks.setdefault(key, asyncio.Lock()):
            candidate = self._records.get(key)
            if candidate is None:
                raise KeyError(handoff_id)
            record = candidate
            if record.version != expected_version:
                raise OptimisticConflictError(
                    f"expected version {expected_version}, found {record.version}"
                )
            next_state = transition_handoff(record.state, target)
            now = datetime.now(UTC)
            updated = record.model_copy(
                update={
                    "state": next_state,
                    "version": record.version + 1,
                    "updated_at": now,
                    "result_reference": result_reference or record.result_reference,
                }
            )
            event = HandoffTransition(
                handoff_id=handoff_id,
                from_state=record.state,
                to_state=next_state,
                version=updated.version,
                idempotency_key=idempotency_key,
                event_type=f"handoff.{next_state.value}.v1",
                occurred_at=now,
            )
            self._records[key] = updated
            self._commands[command_key] = event
            return updated, event, False


class HandoffRepository(Protocol):
    async def create(self, record: HandoffRecord) -> tuple[HandoffRecord, bool]: ...

    async def get(self, tenant_id: str, handoff_id: str) -> HandoffRecord | None: ...

    async def transition(
        self,
        tenant_id: str,
        handoff_id: str,
        *,
        target: HandoffState,
        expected_version: int,
        idempotency_key: str,
        result_reference: str | None = None,
    ) -> tuple[HandoffRecord, HandoffTransition, bool]: ...


def _record_from_row(row: Any) -> HandoffRecord:
    snapshot = dict(row.target_membership_snapshot or {})
    return HandoffRecord(
        handoff_id=str(row.id),
        tenant_id=str(row.tenant_id),
        session_id=str(row.session_id),
        civilization_id=str(row.civilization_id),
        source_agent_id=str(row.source_agent_id),
        target_agent_id=str(row.target_agent_id),
        task_summary=str(snapshot.get("task_summary", "handoff")),
        context_message_ids=tuple(snapshot.get("context_message_ids", ())),
        artifact_refs=tuple(snapshot.get("artifact_refs", ())),
        connector_allowlist=frozenset(row.connector_allowlist or ()),
        classification=str(row.classification),
        remaining_budget_usd=float(snapshot.get("remaining_budget_usd", 0.0)),
        deadline=row.deadline,
        acceptance_token_digest=str(row.acceptance_token_digest),
        state=str(row.state),
        result_reference=row.result_reference,
        version=int(row.version),
        idempotency_key=str(row.idempotency_key),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresHandoffRepository:
    """RLS-scoped handoff state, event, and outbox writes in one transaction."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    @staticmethod
    async def _allocate_event(
        db: AsyncSession,
        *,
        tenant_id: str,
        session_id: str,
        event_id: str,
        event_type: str,
        idempotency_key: str,
        classification: str,
        payload: dict[str, Any],
        occurred_at: datetime,
    ) -> int:
        sessions = COORDINATION_TABLES["coordination_sessions"]
        events = COORDINATION_TABLES["coordination_events"]
        outbox = COORDINATION_TABLES["coordination_outbox"]
        session = (
            await db.execute(
                select(sessions.c.next_sequence)
                .where(sessions.c.id == session_id)
                .with_for_update()
            )
        ).one_or_none()
        if session is None:
            raise KeyError(f"coordination session not found: {session_id}")
        sequence = int(session.next_sequence)
        await db.execute(
            update(sessions)
            .where(sessions.c.id == session_id)
            .values(next_sequence=sequence + 1, updated_at=occurred_at)
        )
        envelope = {
            "event_id": event_id,
            "tenant_id": tenant_id,
            "session_id": session_id,
            "sequence": sequence,
            "schema_version": 1,
            "event_type": event_type,
            "occurred_at": occurred_at.isoformat(),
            "correlation_id": session_id,
            "causation_id": None,
            "idempotency_key": idempotency_key,
            "classification": classification,
            "payload": payload,
        }
        await db.execute(
            insert(events).values(
                id=event_id,
                tenant_id=tenant_id,
                session_id=session_id,
                sequence=sequence,
                schema_version=1,
                event_type=event_type,
                occurred_at=occurred_at,
                correlation_id=session_id,
                causation_id="",
                idempotency_key=idempotency_key,
                classification=classification,
                expires_at=None,
                payload=payload,
                version=1,
            )
        )
        await db.execute(
            insert(outbox).values(
                id=uuid.uuid5(uuid.NAMESPACE_URL, f"outbox:{event_id}").hex,
                event_id=event_id,
                tenant_id=tenant_id,
                session_id=session_id,
                stream=f"coord:{tenant_id}:{session_id}",
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
        return sequence

    async def create(self, record: HandoffRecord) -> tuple[HandoffRecord, bool]:
        handoffs = COORDINATION_TABLES["handoffs"]
        event_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"handoff-request:{record.tenant_id}:{record.session_id}:{record.idempotency_key}",
        ).hex
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, record.tenant_id),
        ):
            prior = (
                await db.execute(
                    select(handoffs).where(
                        handoffs.c.session_id == record.session_id,
                        handoffs.c.idempotency_key == record.idempotency_key,
                    )
                )
            ).mappings().one_or_none()
            if prior is not None:
                return _record_from_row(prior), True
            snapshot = {
                "task_summary": record.task_summary,
                "context_message_ids": list(record.context_message_ids),
                "artifact_refs": list(record.artifact_refs),
                "remaining_budget_usd": record.remaining_budget_usd,
            }
            await db.execute(
                insert(handoffs).values(
                    id=record.handoff_id,
                    tenant_id=record.tenant_id,
                    session_id=record.session_id,
                    source_agent_id=record.source_agent_id,
                    target_agent_id=record.target_agent_id,
                    state=record.state.value,
                    civilization_id=record.civilization_id,
                    target_membership_snapshot=snapshot,
                    connector_allowlist=list(record.connector_allowlist),
                    classification=record.classification.value,
                    result_reference=record.result_reference,
                    acceptance_token_digest=record.acceptance_token_digest,
                    transition_audit=[],
                    schema_version=1,
                    idempotency_key=record.idempotency_key,
                    deadline=record.deadline,
                    version=record.version,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
            )
            await self._allocate_event(
                db,
                tenant_id=record.tenant_id,
                session_id=record.session_id,
                event_id=event_id,
                event_type="handoff.requested.v1",
                idempotency_key=record.idempotency_key,
                classification=record.classification.value,
                payload={"handoff_id": record.handoff_id, "version": record.version},
                occurred_at=record.created_at,
            )
            return record, False

    async def get(self, tenant_id: str, handoff_id: str) -> HandoffRecord | None:
        handoffs = COORDINATION_TABLES["handoffs"]
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            row = (
                await db.execute(select(handoffs).where(handoffs.c.id == handoff_id))
            ).mappings().one_or_none()
            return _record_from_row(row) if row is not None else None

    async def transition(
        self,
        tenant_id: str,
        handoff_id: str,
        *,
        target: HandoffState,
        expected_version: int,
        idempotency_key: str,
        result_reference: str | None = None,
    ) -> tuple[HandoffRecord, HandoffTransition, bool]:
        handoffs = COORDINATION_TABLES["handoffs"]
        events = COORDINATION_TABLES["coordination_events"]
        event_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"handoff-transition:{tenant_id}:{handoff_id}:{idempotency_key}",
        ).hex
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            prior_event = (
                await db.execute(select(events.c.payload).where(events.c.id == event_id))
            ).scalar_one_or_none()
            if prior_event is not None:
                row = (
                    await db.execute(select(handoffs).where(handoffs.c.id == handoff_id))
                ).mappings().one()
                record = _record_from_row(row)
                audit = prior_event["transition"]
                event = HandoffTransition.model_validate(audit)
                return record, event, True
            locked_row = (
                await db.execute(
                    select(handoffs)
                    .where(handoffs.c.id == handoff_id)
                    .with_for_update()
                )
            ).mappings().one_or_none()
            if locked_row is None:
                raise KeyError(handoff_id)
            record = _record_from_row(locked_row)
            if record.version != expected_version:
                raise OptimisticConflictError(
                    f"expected version {expected_version}, found {record.version}"
                )
            next_state = transition_handoff(record.state, target)
            now = datetime.now(UTC)
            updated = record.model_copy(
                update={
                    "state": next_state,
                    "version": record.version + 1,
                    "updated_at": now,
                    "result_reference": result_reference or record.result_reference,
                }
            )
            event = HandoffTransition(
                handoff_id=handoff_id,
                from_state=record.state,
                to_state=next_state,
                version=updated.version,
                idempotency_key=idempotency_key,
                event_type=f"handoff.{next_state.value}.v1",
                occurred_at=now,
            )
            audit = list(locked_row.transition_audit or ())
            audit.append(event.model_dump(mode="json"))
            result = await db.execute(
                update(handoffs)
                .where(
                    handoffs.c.id == handoff_id,
                    handoffs.c.version == expected_version,
                )
                .values(
                    state=next_state.value,
                    result_reference=updated.result_reference,
                    transition_audit=audit,
                    version=updated.version,
                    updated_at=now,
                )
            )
            if cast(CursorResult[Any], result).rowcount != 1:
                raise OptimisticConflictError("handoff version changed concurrently")
            await self._allocate_event(
                db,
                tenant_id=tenant_id,
                session_id=record.session_id,
                event_id=event_id,
                event_type=event.event_type,
                idempotency_key=idempotency_key,
                classification=record.classification.value,
                payload={
                    "handoff_id": handoff_id,
                    "version": updated.version,
                    "transition": event.model_dump(mode="json"),
                },
                occurred_at=now,
            )
            return updated, event, False


__all__ = [
    "HandoffRepository",
    "InMemoryHandoffRepository",
    "PostgresHandoffRepository",
]
