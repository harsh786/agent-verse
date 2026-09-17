"""Fast-tier (no Docker/Postgres) unit coverage for ``app.coordination.store``.

A sibling suite (``tests/coordination/test_store.py``) proves the Postgres-backed
``CoordinationStore`` end-to-end against a real database (``pytest.mark.integration``),
but that suite needs Docker and is excluded from the fast tier. This file mocks the
SQLAlchemy async session so ``CoordinationStore``'s branching logic (idempotent
replay, optimistic-conflict detection, not-found paths) is exercised without any
live infra, and directly exercises the pure-Python ``InMemoryCoordinationStore``
and ``PostgresReplayRepository`` (also mocked).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.coordination.store import (
    AcceptedTransition,
    CoordinationStore,
    InMemoryCoordinationStore,
    OptimisticConflictError,
    PostgresReplayRepository,
)
from app.tenancy.context import PlanTier, TenantContext

# asyncio_mode = "auto" (pyproject.toml) auto-detects `async def` tests.


def _ctx(tenant_id: str = "tenant-1") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="key-1")


# ── Fakes ──────────────────────────────────────────────────────────────────────


class FakeResult:
    def __init__(
        self,
        *,
        one_or_none: Any = None,
        scalar_one_or_none: Any = None,
        mappings_rows: list[Any] | None = None,
    ) -> None:
        self._one_or_none = one_or_none
        self._scalar_one_or_none = scalar_one_or_none
        self._mappings_rows = mappings_rows or []

    def one_or_none(self) -> Any:
        return self._one_or_none

    def scalar_one_or_none(self) -> Any:
        return self._scalar_one_or_none

    def mappings(self) -> list[Any]:
        return self._mappings_rows


class FakeSession:
    """One ``async with self._sessions() as db:`` block's worth of state."""

    def __init__(self, results: list[FakeResult] | None = None) -> None:
        self._results = list(results or [])
        self.executed: list[tuple[str, Any]] = []

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    def begin(self) -> FakeSession:
        return self

    async def execute(self, stmt: Any, params: Any = None) -> FakeResult:
        sql = str(stmt)
        self.executed.append((sql, params))
        if "set_config" in sql:
            return FakeResult()
        return self._results.pop(0) if self._results else FakeResult()


class FakeSessionFactory:
    """Callable ``session_factory`` handing out a fresh ``FakeSession`` per call."""

    def __init__(self, batches: list[list[FakeResult]] | None = None) -> None:
        self._batches = list(batches or [])
        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        results = self._batches.pop(0) if self._batches else []
        session = FakeSession(results)
        self.sessions.append(session)
        return session


def _session_row(state: str, next_sequence: int, version: int) -> SimpleNamespace:
    return SimpleNamespace(state=state, next_sequence=next_sequence, version=version)


def _full_row(session_id: str, tenant_id: str, state: str, next_sequence: int, version: int) -> Any:
    return SimpleNamespace(
        id=session_id,
        tenant_id=tenant_id,
        state=state,
        next_sequence=next_sequence,
        version=version,
    )


# ── CoordinationStore.create_session ────────────────────────────────────────


async def test_create_session_inserts_and_returns_pending_record() -> None:
    factory = FakeSessionFactory([[FakeResult()]])
    store = CoordinationStore(factory)
    record = await store.create_session(
        _ctx(),
        civilization_id="civ-1",
        goal_id="goal-1",
        policy_snapshot={"a": 1},
        budget_snapshot={"b": 2},
    )
    assert record.state == "pending"
    assert record.next_sequence == 1
    assert record.version == 1
    assert record.tenant_id == "tenant-1"
    session = factory.sessions[0]
    assert any("INSERT INTO coordination_sessions" in sql for sql, _ in session.executed)


# ── CoordinationStore.get_session ───────────────────────────────────────────


async def test_get_session_found() -> None:
    row = _full_row("sess-1", "tenant-1", "active", 3, 2)
    factory = FakeSessionFactory([[FakeResult(one_or_none=row)]])
    store = CoordinationStore(factory)
    record = await store.get_session(_ctx(), session_id="sess-1")
    assert record.session_id == "sess-1"
    assert record.state == "active"
    assert record.next_sequence == 3
    assert record.version == 2


async def test_get_session_missing_raises_keyerror() -> None:
    factory = FakeSessionFactory([[FakeResult(one_or_none=None)]])
    store = CoordinationStore(factory)
    with pytest.raises(KeyError):
        await store.get_session(_ctx(), session_id="missing")


# ── CoordinationStore.transition_session ────────────────────────────────────


async def test_transition_session_replay_returns_original_accepted_transition() -> None:
    accepted = AcceptedTransition(
        event_id="evt-1",
        session_id="sess-1",
        sequence=1,
        state="active",
        version=2,
        idempotency_key="key-1",
    )
    replay_payload = {"kind": "session_transition", "accepted": accepted.model_dump()}
    factory = FakeSessionFactory([[FakeResult(scalar_one_or_none=replay_payload)]])
    store = CoordinationStore(factory)
    result = await store.transition_session(
        _ctx(),
        session_id="sess-1",
        expected_version=1,
        target_state="active",
        idempotency_key="key-1",
    )
    assert result == accepted


async def test_transition_session_missing_session_raises_keyerror() -> None:
    factory = FakeSessionFactory([[FakeResult(scalar_one_or_none=None), FakeResult(one_or_none=None)]])
    store = CoordinationStore(factory)
    with pytest.raises(KeyError):
        await store.transition_session(
            _ctx(),
            session_id="missing",
            expected_version=1,
            target_state="active",
            idempotency_key="key-1",
        )


async def test_transition_session_version_mismatch_raises_optimistic_conflict() -> None:
    row = _session_row("pending", 1, 5)
    factory = FakeSessionFactory([[FakeResult(scalar_one_or_none=None), FakeResult(one_or_none=row)]])
    store = CoordinationStore(factory)
    with pytest.raises(OptimisticConflictError):
        await store.transition_session(
            _ctx(),
            session_id="sess-1",
            expected_version=1,
            target_state="active",
            idempotency_key="key-1",
        )


async def test_transition_session_success_writes_event_and_outbox() -> None:
    row = _session_row("pending", 1, 1)
    factory = FakeSessionFactory(
        [
            [
                FakeResult(scalar_one_or_none=None),  # replay lookup: none
                FakeResult(one_or_none=row),  # current session row
                FakeResult(),  # UPDATE sessions
                FakeResult(),  # INSERT events
                FakeResult(),  # INSERT outbox
            ]
        ]
    )
    store = CoordinationStore(factory)
    accepted = await store.transition_session(
        _ctx(),
        session_id="sess-1",
        expected_version=1,
        target_state="active",
        idempotency_key="start",
    )
    assert accepted.sequence == 1
    assert accepted.version == 2
    assert accepted.state == "active"
    session = factory.sessions[0]
    assert any("INSERT INTO coordination_events" in sql for sql, _ in session.executed)
    assert any("INSERT INTO coordination_outbox" in sql for sql, _ in session.executed)


async def test_transition_session_illegal_transition_raises() -> None:
    row = _session_row("completed", 1, 1)
    factory = FakeSessionFactory(
        [[FakeResult(scalar_one_or_none=None), FakeResult(one_or_none=row)]]
    )
    store = CoordinationStore(factory)
    from app.coordination.state_machines import InvalidTransitionError

    with pytest.raises(InvalidTransitionError):
        await store.transition_session(
            _ctx(),
            session_id="sess-1",
            expected_version=1,
            target_state="active",
            idempotency_key="start",
        )


# ── InMemoryCoordinationStore ────────────────────────────────────────────────


async def test_in_memory_store_create_get_and_contains() -> None:
    store = InMemoryCoordinationStore()
    ctx = _ctx()
    record = await store.create_session(
        ctx, civilization_id="c1", goal_id="g1", policy_snapshot={}, budget_snapshot={}
    )
    assert record.state == "pending"
    fetched = await store.get_session(ctx, session_id=record.session_id)
    assert fetched == record
    assert await store.contains(ctx.tenant_id, record.session_id) is True
    assert await store.contains(ctx.tenant_id, "nope") is False


async def test_in_memory_store_get_session_missing_raises_keyerror() -> None:
    store = InMemoryCoordinationStore()
    with pytest.raises(KeyError):
        await store.get_session(_ctx(), session_id="missing")


async def test_in_memory_store_transition_success_and_idempotent_replay() -> None:
    store = InMemoryCoordinationStore()
    ctx = _ctx()
    record = await store.create_session(
        ctx, civilization_id="c1", goal_id="g1", policy_snapshot={}, budget_snapshot={}
    )
    first = await store.transition_session(
        ctx,
        session_id=record.session_id,
        expected_version=1,
        target_state="active",
        idempotency_key="start",
    )
    assert first.state == "active"
    assert first.version == 2

    replay = await store.transition_session(
        ctx,
        session_id=record.session_id,
        expected_version=1,
        target_state="active",
        idempotency_key="start",
    )
    assert replay == first


async def test_in_memory_store_transition_missing_session_raises_keyerror() -> None:
    store = InMemoryCoordinationStore()
    with pytest.raises(KeyError):
        await store.transition_session(
            _ctx(),
            session_id="missing",
            expected_version=1,
            target_state="active",
            idempotency_key="start",
        )


async def test_in_memory_store_transition_version_conflict() -> None:
    store = InMemoryCoordinationStore()
    ctx = _ctx()
    record = await store.create_session(
        ctx, civilization_id="c1", goal_id="g1", policy_snapshot={}, budget_snapshot={}
    )
    await store.transition_session(
        ctx,
        session_id=record.session_id,
        expected_version=1,
        target_state="active",
        idempotency_key="start",
    )
    with pytest.raises(OptimisticConflictError):
        await store.transition_session(
            ctx,
            session_id=record.session_id,
            expected_version=1,
            target_state="completed",
            idempotency_key="stale",
        )


# ── PostgresReplayRepository ────────────────────────────────────────────────


async def test_postgres_replay_repository_pages_events() -> None:
    rows = [
        {
            "tenant_id": "tenant-1",
            "session_id": "sess-1",
            "sequence": 2,
            "event_id": "evt-2",
            "schema_version": 1,
            "event_type": "session.state_changed",
            "occurred_at": None,
            "correlation_id": "sess-1",
            "causation_id": "",
            "classification": "internal",
            "payload": {},
        }
    ]
    factory = FakeSessionFactory([[FakeResult(mappings_rows=rows)]])
    repository = PostgresReplayRepository(factory)
    events = await repository.page(
        tenant_id="tenant-1", session_id="sess-1", after_sequence=1, limit=10
    )
    assert len(events) == 1
    assert events[0]["sequence"] == 2
    assert events[0]["event_type"] == "session.state_changed"


async def test_postgres_replay_repository_empty_page() -> None:
    factory = FakeSessionFactory([[FakeResult(mappings_rows=[])]])
    repository = PostgresReplayRepository(factory)
    events = await repository.page(
        tenant_id="tenant-1", session_id="sess-1", after_sequence=0, limit=10
    )
    assert events == []
