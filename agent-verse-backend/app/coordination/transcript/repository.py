"""Concurrency-safe append and replay for canonical transcript messages."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.coordination.contracts import Classification
from app.coordination.transcript.models import TranscriptMessage
from app.db.models.coordination import COORDINATION_TABLES
from app.db.rls import sqlalchemy_rls_context


class InMemoryTranscriptRepository:
    def __init__(self) -> None:
        self._messages: dict[tuple[str, str], list[TranscriptMessage]] = {}
        self._idempotency: dict[tuple[str, str, str], TranscriptMessage] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def append(self, message: TranscriptMessage) -> TranscriptMessage:
        session_key = (message.tenant_id, message.session_id)
        command_key = (*session_key, message.idempotency_key)
        prior = self._idempotency.get(command_key)
        if prior is not None:
            return prior
        async with self._locks.setdefault(session_key, asyncio.Lock()):
            prior = self._idempotency.get(command_key)
            if prior is not None:
                return prior
            messages = self._messages.setdefault(session_key, [])
            expected = len(messages) + 1
            stored = message.model_copy(update={"sequence": expected})
            messages.append(stored)
            self._idempotency[command_key] = stored
            return stored

    async def page(
        self,
        tenant_id: str,
        session_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[TranscriptMessage, ...]:
        bounded = max(1, min(limit, 500))
        return tuple(
            item
            for item in self._messages.get((tenant_id, session_id), ())
            if item.sequence > after_sequence
        )[:bounded]


class TranscriptRepository(Protocol):
    async def append(self, message: TranscriptMessage) -> TranscriptMessage: ...

    async def page(
        self,
        tenant_id: str,
        session_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[TranscriptMessage, ...]: ...


def _message_from_row(row: Any) -> TranscriptMessage:
    content = dict(row.content or {})
    return TranscriptMessage(
        message_id=str(row.id),
        tenant_id=str(row.tenant_id),
        session_id=str(row.session_id),
        sequence=int(row.sequence),
        sender_agent_id=str(row.sender_agent_id),
        recipient_agent_ids=tuple(row.recipient_agent_ids or ()),
        message_type=str(row.message_type),
        safe_content=content.get("safe_content"),
        artifact_reference=row.artifact_reference,
        classification=Classification(str(row.classification)),
        trust_label=str(row.trust_label),
        provenance_chain=tuple(row.provenance or ()),
        source_digest=str(row.source_digest),
        clearance_decision=str(row.clearance_decision),
        compacts_from_sequence=row.compacts_from_sequence,
        compacts_to_sequence=row.compacts_to_sequence,
        idempotency_key=str(row.idempotency_key),
        created_at=row.created_at,
    )


class PostgresTranscriptRepository:
    """Append canonical messages and replay events atomically under RLS."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def append(self, message: TranscriptMessage) -> TranscriptMessage:
        messages = COORDINATION_TABLES["context_messages"]
        sessions = COORDINATION_TABLES["coordination_sessions"]
        events = COORDINATION_TABLES["coordination_events"]
        outbox = COORDINATION_TABLES["coordination_outbox"]
        event_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"transcript:{message.tenant_id}:{message.session_id}:{message.idempotency_key}",
        ).hex
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, message.tenant_id),
        ):
            session = (
                await db.execute(
                    select(sessions.c.next_sequence)
                    .where(sessions.c.id == message.session_id)
                    .with_for_update()
                )
            ).one_or_none()
            if session is None:
                raise KeyError(f"coordination session not found: {message.session_id}")
            prior = (
                (
                    await db.execute(
                        select(messages).where(
                            messages.c.session_id == message.session_id,
                            messages.c.idempotency_key == message.idempotency_key,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if prior is not None:
                return _message_from_row(prior)
            sequence = int(session.next_sequence)
            now = datetime.now(UTC)
            stored = message.model_copy(update={"sequence": sequence, "created_at": now})
            await db.execute(
                update(sessions)
                .where(sessions.c.id == message.session_id)
                .values(next_sequence=sequence + 1, updated_at=now)
            )
            await db.execute(
                insert(messages).values(
                    id=stored.message_id,
                    tenant_id=stored.tenant_id,
                    session_id=stored.session_id,
                    sequence=stored.sequence,
                    sender_agent_id=stored.sender_agent_id,
                    recipient_agent_ids=list(stored.recipient_agent_ids),
                    message_type=stored.message_type,
                    content={"safe_content": stored.safe_content},
                    provenance=list(stored.provenance_chain),
                    classification=stored.classification.value,
                    idempotency_key=stored.idempotency_key,
                    expires_at=None,
                    encrypted_content_reference=None,
                    artifact_reference=stored.artifact_reference,
                    clearance_decision=stored.clearance_decision,
                    taint_chain=[stored.trust_label],
                    source_digest=stored.source_digest,
                    compacts_from_sequence=stored.compacts_from_sequence,
                    compacts_to_sequence=stored.compacts_to_sequence,
                    trust_label=stored.trust_label,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            payload = {
                "kind": "transcript_message",
                "message": stored.model_dump(mode="json"),
            }
            event_type = (
                "transcript.compacted.v1"
                if stored.message_type == "summary"
                else "transcript.message_appended.v1"
            )
            await db.execute(
                insert(events).values(
                    id=event_id,
                    tenant_id=stored.tenant_id,
                    session_id=stored.session_id,
                    sequence=sequence,
                    schema_version=1,
                    event_type=event_type,
                    occurred_at=now,
                    correlation_id=stored.session_id,
                    causation_id="",
                    idempotency_key=stored.idempotency_key,
                    classification=stored.classification.value,
                    expires_at=None,
                    payload=payload,
                    version=1,
                )
            )
            envelope = {
                "event_id": event_id,
                "tenant_id": stored.tenant_id,
                "session_id": stored.session_id,
                "sequence": sequence,
                "schema_version": 1,
                "event_type": event_type,
                "occurred_at": now.isoformat(),
                "correlation_id": stored.session_id,
                "causation_id": None,
                "idempotency_key": stored.idempotency_key,
                "classification": stored.classification.value,
                "payload": payload,
            }
            await db.execute(
                insert(outbox).values(
                    id=uuid.uuid5(uuid.NAMESPACE_URL, f"outbox:{event_id}").hex,
                    event_id=event_id,
                    tenant_id=stored.tenant_id,
                    session_id=stored.session_id,
                    stream=f"coord:{stored.tenant_id}:{stored.session_id}",
                    payload=envelope,
                    state="pending",
                    attempt_count=0,
                    available_at=now,
                    claim_owner="",
                    claimed_at=None,
                    published_at=None,
                    last_error="",
                    version=1,
                )
            )
            return stored

    async def page(
        self,
        tenant_id: str,
        session_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[TranscriptMessage, ...]:
        messages = COORDINATION_TABLES["context_messages"]
        bounded = max(1, min(limit, 500))
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            rows = (
                await db.execute(
                    select(messages)
                    .where(
                        messages.c.session_id == session_id,
                        messages.c.sequence > after_sequence,
                    )
                    .order_by(messages.c.sequence)
                    .limit(bounded)
                )
            ).mappings()
            return tuple(_message_from_row(row) for row in rows)


__all__ = [
    "InMemoryTranscriptRepository",
    "PostgresTranscriptRepository",
    "TranscriptRepository",
]
