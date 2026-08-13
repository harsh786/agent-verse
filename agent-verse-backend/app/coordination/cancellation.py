"""Persisted cancellation propagation and checkpoint-compatible resume."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.coordination import COORDINATION_TABLES
from app.db.rls import sqlalchemy_rls_context


class IncompatibleCheckpointError(RuntimeError):
    """The latest checkpoint cannot be resumed by the selected adapter."""


class ResumeRejectedError(RuntimeError):
    """The session state cannot be resumed."""


class CancellationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    session_id: str
    reason: str
    cancelled_children: tuple[str, ...]
    invalidated_work_items: tuple[str, ...]


class ResumeResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    session_id: str
    checkpoint_reference: str
    attempt: int


class CancellationRepository(Protocol):
    async def cancel(
        self, tenant_id: str, session_id: str, *, reason: str
    ) -> tuple[CancellationResult, bool]: ...

    async def resume(
        self,
        tenant_id: str,
        session_id: str,
        *,
        adapter_version: str,
        state_schema_version: int,
    ) -> ResumeResult: ...


@dataclass
class _Session:
    state: str = "active"
    attempt: int = 1
    children: list[str] = field(default_factory=list)
    leases: dict[str, int] = field(default_factory=dict)
    checkpoint: tuple[str, int, str] | None = None
    cancellation: CancellationResult | None = None


class InMemoryCancellationRepository:
    """Atomic test double matching the durable cancellation repository contract."""

    def __init__(self, *, on_persist: Callable[[], None] | None = None) -> None:
        self._sessions: dict[tuple[str, str], _Session] = {}
        self._on_persist = on_persist

    def _session(self, tenant_id: str, session_id: str) -> _Session:
        return self._sessions.setdefault((tenant_id, session_id), _Session())

    def add_child(self, tenant_id: str, session_id: str, child_id: str) -> None:
        self._session(tenant_id, session_id).children.append(child_id)

    def add_lease(
        self,
        tenant_id: str,
        session_id: str,
        work_item_id: str,
        *,
        fencing_token: int,
    ) -> None:
        self._session(tenant_id, session_id).leases[work_item_id] = fencing_token

    def lease_token(self, tenant_id: str, session_id: str, work_item_id: str) -> int:
        return self._session(tenant_id, session_id).leases[work_item_id]

    def set_checkpoint(
        self,
        tenant_id: str,
        session_id: str,
        *,
        adapter_version: str,
        state_schema_version: int,
        reference: str,
    ) -> None:
        self._session(tenant_id, session_id).checkpoint = (
            adapter_version,
            state_schema_version,
            reference,
        )

    def set_state(self, tenant_id: str, session_id: str, state: str) -> None:
        self._session(tenant_id, session_id).state = state

    async def cancel(
        self, tenant_id: str, session_id: str, *, reason: str
    ) -> tuple[CancellationResult, bool]:
        session = self._session(tenant_id, session_id)
        if session.cancellation is not None and session.cancellation.reason == reason:
            return session.cancellation, False
        cancelled_children = tuple(session.children)
        invalidated = tuple(session.leases)
        for work_item_id in invalidated:
            session.leases[work_item_id] += 1
        session.state = "cancelled"
        result = CancellationResult(
            tenant_id=tenant_id,
            session_id=session_id,
            reason=reason,
            cancelled_children=cancelled_children,
            invalidated_work_items=invalidated,
        )
        session.cancellation = result
        if self._on_persist is not None:
            self._on_persist()
        return result, True

    async def resume(
        self,
        tenant_id: str,
        session_id: str,
        *,
        adapter_version: str,
        state_schema_version: int,
    ) -> ResumeResult:
        session = self._session(tenant_id, session_id)
        if session.state in {"completed", "failed"}:
            raise ResumeRejectedError(f"terminal session cannot resume: {session.state}")
        if session.state != "cancelled":
            raise ResumeRejectedError(f"session is not cancelled: {session.state}")
        if session.checkpoint is None:
            raise IncompatibleCheckpointError("no compatible checkpoint is available")
        checkpoint_adapter, checkpoint_schema, reference = session.checkpoint
        if (
            checkpoint_adapter != adapter_version
            or checkpoint_schema != state_schema_version
        ):
            raise IncompatibleCheckpointError("checkpoint adapter/schema mismatch")
        session.attempt += 1
        session.state = "active"
        session.cancellation = None
        return ResumeResult(
            tenant_id=tenant_id,
            session_id=session_id,
            checkpoint_reference=reference,
            attempt=session.attempt,
        )


class PostgresCancellationRepository:
    """Persist cancellation propagation and checkpoint-compatible new attempts."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def cancel(
        self, tenant_id: str, session_id: str, *, reason: str
    ) -> tuple[CancellationResult, bool]:
        sessions = COORDINATION_TABLES["coordination_sessions"]
        executions = COORDINATION_TABLES["strategy_executions"]
        handoffs = COORDINATION_TABLES["handoffs"]
        work_items = COORDINATION_TABLES["work_items"]
        claims = COORDINATION_TABLES["claims"]
        events = COORDINATION_TABLES["coordination_events"]
        outbox = COORDINATION_TABLES["coordination_outbox"]
        now = datetime.now(UTC)
        event_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"{tenant_id}:{session_id}:cancel"
        ).hex

        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            session = (
                await db.execute(
                    select(sessions).where(sessions.c.id == session_id).with_for_update()
                )
            ).mappings().one_or_none()
            if session is None:
                raise KeyError(f"coordination session not found: {session_id}")

            if session["state"] == "cancelled":
                persisted = (
                    await db.execute(
                        select(events.c.payload).where(events.c.id == event_id)
                    )
                ).scalar_one_or_none()
                if persisted is None:
                    raise RuntimeError(
                        f"cancelled session is missing its canonical event: {session_id}"
                    )
                return (
                    CancellationResult(
                        tenant_id=tenant_id,
                        session_id=session_id,
                        reason=str(persisted["reason"]),
                        cancelled_children=tuple(persisted["cancelled_children"]),
                        invalidated_work_items=tuple(
                            persisted["invalidated_work_items"]
                        ),
                    ),
                    False,
                )

            child_ids = tuple(
                str(value)
                for value in (
                    await db.execute(
                        select(executions.c.id).where(
                            executions.c.session_id == session_id,
                            executions.c.state.in_(
                                ("pending", "running", "checkpointed", "cancelling")
                            ),
                        )
                    )
                ).scalars()
            )
            claim_rows = (
                await db.execute(
                    select(claims.c.id, claims.c.work_item_id, claims.c.fencing_token)
                    .select_from(
                        claims.join(work_items, claims.c.work_item_id == work_items.c.id)
                    )
                    .where(
                        work_items.c.session_id == session_id,
                        claims.c.state == "active",
                    )
                    .with_for_update()
                )
            ).all()
            invalidated = tuple(str(row.work_item_id) for row in claim_rows)
            result = CancellationResult(
                tenant_id=tenant_id,
                session_id=session_id,
                reason=str(session["cancellation_reason"] or reason),
                cancelled_children=child_ids,
                invalidated_work_items=invalidated,
            )
            if session["state"] in {"completed", "failed"}:
                raise ResumeRejectedError(
                    f"terminal session cannot cancel: {session['state']}"
                )

            await db.execute(
                update(executions)
                .where(executions.c.id.in_(child_ids))
                .values(state="cancelled", updated_at=now)
            )
            await db.execute(
                update(handoffs)
                .where(
                    handoffs.c.session_id == session_id,
                    handoffs.c.state.in_(("requested", "accepted", "active")),
                )
                .values(state="cancelled", updated_at=now)
            )
            for claim in claim_rows:
                await db.execute(
                    update(claims)
                    .where(claims.c.id == claim.id)
                    .values(
                        state="released",
                        fencing_token=int(claim.fencing_token) + 1,
                        updated_at=now,
                    )
                )
            if invalidated:
                await db.execute(
                    update(work_items)
                    .where(work_items.c.id.in_(invalidated))
                    .values(state="cancelled", updated_at=now)
                )

            sequence = int(session["next_sequence"])
            result = result.model_copy(update={"reason": reason})
            payload: dict[str, Any] = {
                "kind": "session_cancelled",
                "reason": reason,
                "cancelled_children": list(child_ids),
                "invalidated_work_items": list(invalidated),
            }
            await db.execute(
                update(sessions)
                .where(sessions.c.id == session_id)
                .values(
                    state="cancelled",
                    cancellation_requested_at=now,
                    cancellation_reason=reason,
                    next_sequence=sequence + 1,
                    version=int(session["version"]) + 1,
                    updated_at=now,
                )
            )
            envelope = {
                "event_id": event_id,
                "tenant_id": tenant_id,
                "session_id": session_id,
                "sequence": sequence,
                "schema_version": 1,
                "event_type": "session.cancelled",
                "occurred_at": now.isoformat(),
                "correlation_id": session_id,
                "causation_id": "",
                "idempotency_key": f"cancel:{session_id}",
                "classification": "internal",
                "payload": payload,
            }
            await db.execute(
                insert(events).values(
                    id=event_id,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    sequence=sequence,
                    schema_version=1,
                    event_type="session.cancelled",
                    occurred_at=now,
                    correlation_id=session_id,
                    causation_id="",
                    idempotency_key=f"cancel:{session_id}",
                    classification="internal",
                    expires_at=None,
                    payload=payload,
                )
            )
            await db.execute(
                insert(outbox).values(
                    id=uuid.uuid4().hex,
                    tenant_id=tenant_id,
                    event_id=event_id,
                    session_id=session_id,
                    stream=f"coord:{tenant_id}:{session_id}",
                    payload=envelope,
                    state="pending",
                    attempt_count=0,
                    available_at=now,
                    claim_owner="",
                    claimed_at=None,
                    published_at=None,
                    last_error="",
                )
            )
            return result, True

    async def resume(
        self,
        tenant_id: str,
        session_id: str,
        *,
        adapter_version: str,
        state_schema_version: int,
    ) -> ResumeResult:
        sessions = COORDINATION_TABLES["coordination_sessions"]
        executions = COORDINATION_TABLES["strategy_executions"]
        checkpoints = COORDINATION_TABLES["strategy_checkpoints"]
        now = datetime.now(UTC)
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            session = (
                await db.execute(
                    select(sessions).where(sessions.c.id == session_id).with_for_update()
                )
            ).mappings().one_or_none()
            if session is None:
                raise KeyError(f"coordination session not found: {session_id}")
            if session["state"] in {"completed", "failed"}:
                raise ResumeRejectedError(
                    f"terminal session cannot resume: {session['state']}"
                )
            if session["state"] != "cancelled":
                raise ResumeRejectedError(f"session is not cancelled: {session['state']}")

            checkpoint = (
                await db.execute(
                    select(checkpoints, executions)
                    .select_from(
                        checkpoints.join(
                            executions, checkpoints.c.execution_id == executions.c.id
                        )
                    )
                    .where(checkpoints.c.session_id == session_id)
                    .order_by(checkpoints.c.sequence.desc())
                    .limit(1)
                )
            ).mappings().one_or_none()
            if checkpoint is None:
                raise IncompatibleCheckpointError("no compatible checkpoint is available")
            if (
                checkpoint["adapter_version"] != adapter_version
                or int(checkpoint["state_schema_version"]) != state_schema_version
            ):
                raise IncompatibleCheckpointError("checkpoint adapter/schema mismatch")

            attempt = int(checkpoint["attempt"]) + 1
            execution_id = uuid.uuid4().hex
            await db.execute(
                insert(executions).values(
                    id=execution_id,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    goal_id=checkpoint["goal_id"],
                    adapter_id=checkpoint["adapter_id"],
                    adapter_version=adapter_version,
                    state_schema_version=state_schema_version,
                    profile_snapshot=checkpoint["profile_snapshot"],
                    state="pending",
                    result={},
                    cost=0,
                    idempotency_key=f"resume:{session_id}:{attempt}",
                    deadline=checkpoint["deadline"],
                    attempt=attempt,
                    prior_execution_id=checkpoint["execution_id"],
                )
            )
            await db.execute(
                update(sessions)
                .where(sessions.c.id == session_id)
                .values(
                    state="active",
                    cancellation_requested_at=None,
                    cancellation_reason=None,
                    version=int(session["version"]) + 1,
                    updated_at=now,
                )
            )
            return ResumeResult(
                tenant_id=tenant_id,
                session_id=session_id,
                checkpoint_reference=str(checkpoint["state_reference"]),
                attempt=attempt,
            )


Wakeup = Callable[[str], Awaitable[None]]


class CancellationCoordinator:
    """Persist intent before best-effort wakeup and resume only compatible state."""

    def __init__(
        self,
        repository: CancellationRepository,
        *,
        wakeup: Wakeup | None = None,
    ) -> None:
        self._repository = repository
        self._wakeup = wakeup

    async def cancel(
        self, tenant_id: str, session_id: str, *, reason: str
    ) -> CancellationResult:
        result, created = await self._repository.cancel(
            tenant_id, session_id, reason=reason
        )
        if created and self._wakeup is not None:
            await self._wakeup(session_id)
        return result

    async def resume(
        self,
        tenant_id: str,
        session_id: str,
        *,
        adapter_version: str,
        state_schema_version: int,
    ) -> ResumeResult:
        return await self._repository.resume(
            tenant_id,
            session_id,
            adapter_version=adapter_version,
            state_schema_version=state_schema_version,
        )


__all__ = [
    "CancellationCoordinator",
    "CancellationRepository",
    "CancellationResult",
    "InMemoryCancellationRepository",
    "IncompatibleCheckpointError",
    "PostgresCancellationRepository",
    "ResumeRejectedError",
    "ResumeResult",
]
