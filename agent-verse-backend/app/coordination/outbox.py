"""Transactional outbox delivery, retry, and dead-letter policy."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.coordination import COORDINATION_TABLES
from app.db.rls import sqlalchemy_rls_context


class OutboxRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: str
    event_id: str
    tenant_id: str
    session_id: str
    envelope: dict[str, Any]
    attempt_count: int = Field(ge=0)


class OutboxRepository(Protocol):
    async def claim(self, *, owner: str, limit: int) -> list[OutboxRecord]: ...

    async def mark_published(self, record_id: str, message_id: str) -> None: ...

    async def mark_retry(self, record_id: str, attempt: int, delay: float, error: str) -> None: ...

    async def dead_letter(self, record_id: str, error: str) -> None: ...


class StreamPublisher(Protocol):
    async def publish(self, tenant_id: str, session_id: str, envelope: dict[str, Any]) -> str: ...


def retry_delay_seconds(
    event_id: str,
    attempt: int,
    *,
    base_seconds: float = 1,
    max_seconds: float = 300,
) -> float:
    """Return deterministic exponential backoff with bounded 1x-2x jitter."""
    if attempt <= 0 or base_seconds <= 0 or max_seconds <= 0:
        raise ValueError("attempt and delay bounds must be positive")
    digest = hashlib.sha256(f"{event_id}:{attempt}".encode()).digest()
    fraction = int.from_bytes(digest[:8], "big") / (2**64 - 1)
    exponential = base_seconds * (2 ** (attempt - 1))
    return float(min(max_seconds, exponential * (1 + fraction)))


class OutboxDelivery:
    """Publish claimed rows and persist the outcome after transport acceptance."""

    def __init__(
        self,
        repository: OutboxRepository,
        streams: StreamPublisher,
        *,
        max_attempts: int = 8,
    ) -> None:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        self._repository = repository
        self._streams = streams
        self._max_attempts = max_attempts

    async def deliver(self, *, owner: str, limit: int) -> int:
        if not owner or limit <= 0:
            raise ValueError("owner and positive limit are required")
        records = await self._repository.claim(owner=owner, limit=limit)
        delivered = 0
        for record in records:
            try:
                message_id = await self._streams.publish(
                    record.tenant_id,
                    record.session_id,
                    record.envelope,
                )
            except Exception as exc:
                attempt = record.attempt_count + 1
                error = f"{type(exc).__name__}: {exc}"
                if attempt >= self._max_attempts:
                    await self._repository.dead_letter(record.record_id, error)
                else:
                    await self._repository.mark_retry(
                        record.record_id,
                        attempt,
                        retry_delay_seconds(record.event_id, attempt),
                        error,
                    )
                continue
            await self._repository.mark_published(record.record_id, message_id)
            delivered += 1
        return delivered


class PostgresOutboxRepository:
    """Claim rows with SKIP LOCKED and persist retry/dead-letter outcomes."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        tenant_id: str,
        claim_ttl_seconds: int = 60,
    ) -> None:
        self._sessions = session_factory
        self._tenant_id = tenant_id
        self._claim_ttl = timedelta(seconds=claim_ttl_seconds)

    async def claim(self, *, owner: str, limit: int) -> list[OutboxRecord]:
        table = COORDINATION_TABLES["coordination_outbox"]
        now = datetime.now(UTC)
        stale_before = now - self._claim_ttl
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, self._tenant_id),
        ):
            rows = (
                (
                    await db.execute(
                        select(table)
                        .where(
                            # Defence-in-depth: scope to this repository's tenant
                            # EXPLICITLY rather than relying solely on RLS. RLS is
                            # bypassed by superuser/BYPASSRLS roles, so a claim on
                            # such a connection would otherwise drain OTHER tenants'
                            # pending events into this worker.
                            table.c.tenant_id == self._tenant_id,
                            or_(
                                and_(table.c.state == "pending", table.c.available_at <= now),
                                and_(
                                    table.c.state == "claimed",
                                    table.c.claimed_at <= stale_before,
                                ),
                            ),
                        )
                        .order_by(table.c.available_at, table.c.created_at)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                )
                .mappings()
                .all()
            )
            if rows:
                await db.execute(
                    update(table)
                    .where(table.c.id.in_([row["id"] for row in rows]))
                    .values(
                        state="claimed",
                        claim_owner=owner,
                        claimed_at=now,
                        updated_at=now,
                    )
                )
            return [
                OutboxRecord(
                    record_id=row["id"],
                    event_id=row["event_id"],
                    tenant_id=row["tenant_id"],
                    session_id=row["session_id"],
                    envelope=row["payload"],
                    attempt_count=row["attempt_count"],
                )
                for row in rows
            ]

    async def mark_published(self, record_id: str, message_id: str) -> None:
        del message_id
        await self._update(
            record_id,
            state="published",
            published_at=datetime.now(UTC),
            claim_owner="",
        )

    async def mark_retry(self, record_id: str, attempt: int, delay: float, error: str) -> None:
        await self._update(
            record_id,
            state="pending",
            attempt_count=attempt,
            available_at=datetime.now(UTC) + timedelta(seconds=delay),
            claim_owner="",
            claimed_at=None,
            last_error=error,
        )

    async def dead_letter(self, record_id: str, error: str) -> None:
        outbox = COORDINATION_TABLES["coordination_outbox"]
        dead = COORDINATION_TABLES["coordination_dead_letters"]
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, self._tenant_id),
        ):
            row = (
                (await db.execute(select(outbox).where(outbox.c.id == record_id).with_for_update()))
                .mappings()
                .one()
            )
            await db.execute(
                insert(dead).values(
                    id=hashlib.sha256(f"dead:{record_id}".encode()).hexdigest()[:32],
                    tenant_id=self._tenant_id,
                    event_id=row["event_id"],
                    session_id=row["session_id"],
                    payload=row["payload"],
                    replay_status="pending_operator_review",
                    version=1,
                )
            )
            await db.execute(
                update(outbox)
                .where(outbox.c.id == record_id)
                .values(state="dead_letter", last_error=error, updated_at=datetime.now(UTC))
            )

    async def _update(self, record_id: str, **values: Any) -> None:
        table = COORDINATION_TABLES["coordination_outbox"]
        values["updated_at"] = datetime.now(UTC)
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, self._tenant_id),
        ):
            await db.execute(update(table).where(table.c.id == record_id).values(**values))


__all__ = [
    "OutboxDelivery",
    "OutboxRecord",
    "OutboxRepository",
    "PostgresOutboxRepository",
    "StreamPublisher",
    "retry_delay_seconds",
]
