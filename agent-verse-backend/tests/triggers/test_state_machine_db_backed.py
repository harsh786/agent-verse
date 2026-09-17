"""Unit coverage for the DB-backed async CRUD on StateMachine.

``tests/api/test_state_machines_persistence.py`` already proves durability
end-to-end against a real Postgres (``pytest.mark.integration``). This file
covers the same ``*_async`` methods at the unit level with a fake SQLAlchemy
session, so the branches (row found / not found, insert vs. update, and the
"swallow + warn" exception handling around every DB call) are exercised
without a running database.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.triggers.state_machine import (
    StateDefinition,
    StateMachine,
    StateMachineDefinition,
    StateMachineInstance,
    TransitionDefinition,
)


class _Result:
    """Stand-in for a SQLAlchemy `Result`, supporting the two access patterns
    the state machine code uses: `.scalar_one_or_none()` and `.scalars().all()`."""

    def __init__(self, scalar: Any = None, scalars_list: list[Any] | None = None) -> None:
        self._scalar = scalar
        self._scalars_list = scalars_list if scalars_list is not None else []

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return self._scalars_list


class FakeSession:
    """A minimal async-context-manager session whose `.execute()` pops
    pre-scripted results in call order. The same instance is reused across
    nested `async with db() as session` blocks (db_factory just returns it)."""

    def __init__(
        self,
        execute_results: list[Any] | None = None,
        raise_on_call_index: int | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._queue = list(execute_results or [])
        self._raise_on_call_index = raise_on_call_index
        self._raise_exc = raise_exc or RuntimeError("db error")
        self.execute_calls: list[Any] = []
        self.added: list[Any] = []
        self.commit = AsyncMock()

    def __call__(self) -> FakeSession:
        """Used directly as the `db_factory` callable."""
        return self

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        idx = len(self.execute_calls)
        self.execute_calls.append((stmt, params))
        if self._raise_on_call_index is not None and idx == self._raise_on_call_index:
            raise self._raise_exc
        if self._queue:
            return self._queue.pop(0)
        return _Result()

    def add(self, obj: Any) -> None:
        self.added.append(obj)


def _defn(machine_id: str = "m1", tenant_id: str = "t1") -> StateMachineDefinition:
    return StateMachineDefinition(
        machine_id=machine_id,
        tenant_id=tenant_id,
        name="Order Flow",
        states=[
            StateDefinition(name="pending", is_initial=True),
            StateDefinition(name="completed", is_terminal=True),
        ],
        transitions=[
            TransitionDefinition(from_state="pending", to_state="completed", event="complete"),
        ],
    )


# ── define_async ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_define_async_no_db_only_caches_in_memory() -> None:
    sm = StateMachine()
    defn = _defn()
    await sm.define_async(defn)
    assert sm.get_definition("m1", "t1") is defn


@pytest.mark.asyncio
async def test_define_async_inserts_new_row() -> None:
    session = FakeSession(execute_results=[_Result(), _Result(scalar=None)])
    sm = StateMachine()
    sm._db_factory = session
    await sm.define_async(_defn())
    assert len(session.added) == 1
    session.commit.assert_awaited_once()
    # in-memory cache is always kept in sync too
    assert sm.get_definition("m1", "t1") is not None


@pytest.mark.asyncio
async def test_define_async_updates_existing_row() -> None:
    existing_row = MagicMock()
    existing_row.name = "old-name"
    existing_row.definition = {}
    session = FakeSession(execute_results=[_Result(), _Result(scalar=existing_row)])
    sm = StateMachine()
    sm._db_factory = session
    defn = _defn()
    await sm.define_async(defn)
    assert existing_row.name == defn.name
    assert existing_row.definition == StateMachine._def_to_json(defn)
    assert session.added == []
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_define_async_db_failure_is_swallowed() -> None:
    session = FakeSession(raise_on_call_index=0, raise_exc=RuntimeError("boom"))
    sm = StateMachine()
    sm._db_factory = session
    # Must not raise, and the in-memory definition must still be registered.
    await sm.define_async(_defn())
    assert sm.get_definition("m1", "t1") is not None


# ── get_definition_async ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_definition_async_no_db_falls_back_to_memory() -> None:
    sm = StateMachine()
    defn = _defn()
    sm.define(defn)
    result = await sm.get_definition_async("m1", "t1")
    assert result is defn


@pytest.mark.asyncio
async def test_get_definition_async_found_in_db() -> None:
    from datetime import UTC, datetime

    row = MagicMock()
    row.machine_id = "m1"
    row.tenant_id = "t1"
    row.name = "Order Flow"
    row.definition = StateMachine._def_to_json(_defn())
    row.created_at = datetime.now(UTC)
    session = FakeSession(execute_results=[_Result(), _Result(scalar=row)])
    sm = StateMachine()
    sm._db_factory = session
    result = await sm.get_definition_async("m1", "t1")
    assert result is not None
    assert result.name == "Order Flow"
    assert {s.name for s in result.states} == {"pending", "completed"}


@pytest.mark.asyncio
async def test_get_definition_async_not_found_in_db() -> None:
    session = FakeSession(execute_results=[_Result(), _Result(scalar=None)])
    sm = StateMachine()
    sm._db_factory = session
    assert await sm.get_definition_async("missing", "t1") is None


@pytest.mark.asyncio
async def test_get_definition_async_db_failure_falls_back_to_memory() -> None:
    defn = _defn()
    session = FakeSession(raise_on_call_index=0)
    sm = StateMachine()
    sm._db_factory = session
    sm.define(defn)  # in-memory fallback data
    result = await sm.get_definition_async("m1", "t1")
    assert result is defn


# ── list_definitions_async ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_definitions_async_no_db() -> None:
    sm = StateMachine()
    defn = _defn()
    sm.define(defn)
    result = await sm.list_definitions_async("t1")
    assert result == [defn]


@pytest.mark.asyncio
async def test_list_definitions_async_from_db() -> None:
    from datetime import UTC, datetime

    row = MagicMock()
    row.machine_id = "m1"
    row.tenant_id = "t1"
    row.name = "Order Flow"
    row.definition = StateMachine._def_to_json(_defn())
    row.created_at = datetime.now(UTC)
    session = FakeSession(execute_results=[_Result(), _Result(scalars_list=[row])])
    sm = StateMachine()
    sm._db_factory = session
    result = await sm.list_definitions_async("t1")
    assert len(result) == 1
    assert result[0].machine_id == "m1"


@pytest.mark.asyncio
async def test_list_definitions_async_db_failure_falls_back() -> None:
    defn = _defn()
    session = FakeSession(raise_on_call_index=0)
    sm = StateMachine()
    sm._db_factory = session
    sm.define(defn)
    result = await sm.list_definitions_async("t1")
    assert result == [defn]


# ── delete_definition_async ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_definition_async_no_db_removes_from_memory() -> None:
    sm = StateMachine()
    sm.define(_defn())
    await sm.delete_definition_async("m1", "t1")
    assert sm.get_definition("m1", "t1") is None


@pytest.mark.asyncio
async def test_delete_definition_async_hits_db() -> None:
    session = FakeSession(execute_results=[_Result(), _Result()])
    sm = StateMachine()
    sm.define(_defn())
    sm._db_factory = session
    await sm.delete_definition_async("m1", "t1")
    assert sm.get_definition("m1", "t1") is None
    session.commit.assert_awaited_once()
    assert len(session.execute_calls) == 2


@pytest.mark.asyncio
async def test_delete_definition_async_db_failure_is_swallowed() -> None:
    session = FakeSession(raise_on_call_index=0)
    sm = StateMachine()
    sm.define(_defn())
    sm._db_factory = session
    await sm.delete_definition_async("m1", "t1")  # must not raise
    assert sm.get_definition("m1", "t1") is None  # in-memory cache still cleared


# ── create_instance_async ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_instance_async_no_db_delegates_to_sync() -> None:
    sm = StateMachine()
    sm.define(_defn())
    inst = await sm.create_instance_async("m1", "e1", "t1")
    assert inst.current_state == "pending"


def test_sync_transition_autocreates_instance() -> None:
    """`transition()` with no prior instance auto-creates one before applying the event."""
    sm = StateMachine()
    sm.define(_defn())
    result = sm.transition("m1", "e-auto", "complete", "t1")
    assert result["transitioned"] is True
    assert result["from_state"] == "pending"
    assert sm.get_instance("e-auto", "t1") is not None


def test_sync_transition_unknown_machine_raises() -> None:
    sm = StateMachine()
    sm.define(_defn())
    sm.create_instance("m1", "e1", "t1")
    with pytest.raises(ValueError, match="Unknown state machine"):
        sm.transition("nonexistent", "e1", "complete", "t1")


def test_sync_create_instance_no_states_raises() -> None:
    sm = StateMachine()
    empty_defn = StateMachineDefinition(machine_id="empty", tenant_id="t1", name="Empty")
    sm.define(empty_defn)
    with pytest.raises(ValueError, match="has no states"):
        sm.create_instance("empty", "e1", "t1")


@pytest.mark.asyncio
async def test_create_instance_async_no_states_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    empty_defn = StateMachineDefinition(machine_id="empty", tenant_id="t1", name="Empty")
    sm = StateMachine()
    sm._db_factory = MagicMock()
    monkeypatch.setattr(sm, "get_definition_async", AsyncMock(return_value=empty_defn))
    with pytest.raises(ValueError, match="has no states"):
        await sm.create_instance_async("empty", "e1", "t1")


@pytest.mark.asyncio
async def test_create_instance_async_unknown_machine_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    sm = StateMachine()
    sm._db_factory = MagicMock()  # non-None, but get_definition_async is mocked below
    monkeypatch.setattr(sm, "get_definition_async", AsyncMock(return_value=None))
    with pytest.raises(ValueError, match="Unknown state machine"):
        await sm.create_instance_async("missing", "e1", "t1")


@pytest.mark.asyncio
async def test_create_instance_async_inserts_new_row(monkeypatch: pytest.MonkeyPatch) -> None:
    defn = _defn()
    session = FakeSession(execute_results=[_Result(), _Result(scalar=None)])
    sm = StateMachine()
    sm._db_factory = session
    monkeypatch.setattr(sm, "get_definition_async", AsyncMock(return_value=defn))
    inst = await sm.create_instance_async("m1", "e1", "t1")
    assert inst.current_state == "pending"
    assert len(session.added) == 1
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_instance_async_overwrites_existing_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defn = _defn()
    existing_row = MagicMock(instance_id="old-id", current_state="completed", history=[["x"]])
    session = FakeSession(execute_results=[_Result(), _Result(scalar=existing_row)])
    sm = StateMachine()
    sm._db_factory = session
    monkeypatch.setattr(sm, "get_definition_async", AsyncMock(return_value=defn))
    inst = await sm.create_instance_async("m1", "e1", "t1")
    assert inst.current_state == "pending"
    assert inst.instance_id == "old-id"  # re-used the existing row's id
    assert existing_row.current_state == "pending"
    assert existing_row.history == []
    assert existing_row.status == "running"
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_instance_async_db_failure_still_returns_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defn = _defn()
    session = FakeSession(raise_on_call_index=0)
    sm = StateMachine()
    sm._db_factory = session
    monkeypatch.setattr(sm, "get_definition_async", AsyncMock(return_value=defn))
    inst = await sm.create_instance_async("m1", "e1", "t1")
    assert inst.current_state == "pending"  # constructed before the DB write attempt


# ── get_instance_async ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_instance_async_no_db() -> None:
    sm = StateMachine()
    sm.define(_defn())
    created = sm.create_instance("m1", "e1", "t1")
    result = await sm.get_instance_async("e1", "t1")
    assert result is created


@pytest.mark.asyncio
async def test_get_instance_async_found_in_db() -> None:
    from datetime import UTC, datetime

    row = MagicMock(
        instance_id="i1",
        machine_id="m1",
        tenant_id="t1",
        entity_id="e1",
        current_state="pending",
        history=[{"event": "x"}],
        status="running",
        updated_at=datetime.now(UTC),
    )
    session = FakeSession(execute_results=[_Result(), _Result(scalar=row)])
    sm = StateMachine()
    sm._db_factory = session
    result = await sm.get_instance_async("e1", "t1")
    assert result is not None
    assert result.instance_id == "i1"
    assert result.history == [{"event": "x"}]


@pytest.mark.asyncio
async def test_get_instance_async_not_found_in_db() -> None:
    session = FakeSession(execute_results=[_Result(), _Result(scalar=None)])
    sm = StateMachine()
    sm._db_factory = session
    assert await sm.get_instance_async("missing", "t1") is None


@pytest.mark.asyncio
async def test_get_instance_async_db_failure_falls_back_to_memory() -> None:
    sm = StateMachine()
    sm.define(_defn())
    created = sm.create_instance("m1", "e1", "t1")
    session = FakeSession(raise_on_call_index=0)
    sm._db_factory = session
    result = await sm.get_instance_async("e1", "t1")
    assert result is created


# ── transition_async ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transition_async_no_db_delegates_to_sync() -> None:
    sm = StateMachine()
    sm.define(_defn())
    sm.create_instance("m1", "e1", "t1")
    result = await sm.transition_async("m1", "e1", "complete", "t1")
    assert result["transitioned"] is True
    assert result["to_state"] == "completed"


@pytest.mark.asyncio
async def test_transition_async_creates_missing_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    defn = _defn()
    instance = StateMachineInstance(
        instance_id="i1", machine_id="m1", tenant_id="t1", entity_id="e1",
        current_state="pending",
    )
    session = FakeSession(execute_results=[_Result(), _Result(scalar=MagicMock())])
    sm = StateMachine()
    sm._db_factory = session
    monkeypatch.setattr(sm, "get_instance_async", AsyncMock(return_value=None))
    monkeypatch.setattr(sm, "create_instance_async", AsyncMock(return_value=instance))
    monkeypatch.setattr(sm, "get_definition_async", AsyncMock(return_value=defn))

    result = await sm.transition_async("m1", "e1", "complete", "t1")
    assert result["transitioned"] is True
    assert result["from_state"] == "pending"
    assert result["to_state"] == "completed"


@pytest.mark.asyncio
async def test_transition_async_unknown_machine_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    instance = StateMachineInstance(
        instance_id="i1", machine_id="m1", tenant_id="t1", entity_id="e1",
        current_state="pending",
    )
    sm = StateMachine()
    sm._db_factory = MagicMock()
    monkeypatch.setattr(sm, "get_instance_async", AsyncMock(return_value=instance))
    monkeypatch.setattr(sm, "get_definition_async", AsyncMock(return_value=None))
    with pytest.raises(ValueError, match="Unknown state machine"):
        await sm.transition_async("m1", "e1", "complete", "t1")


@pytest.mark.asyncio
async def test_transition_async_no_matching_transition_skips_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defn = _defn()
    instance = StateMachineInstance(
        instance_id="i1", machine_id="m1", tenant_id="t1", entity_id="e1",
        current_state="completed",  # terminal — no outgoing transition for "complete"
    )
    sm = StateMachine()
    sm._db_factory = MagicMock()  # would blow up if actually called
    monkeypatch.setattr(sm, "get_instance_async", AsyncMock(return_value=instance))
    monkeypatch.setattr(sm, "get_definition_async", AsyncMock(return_value=defn))
    result = await sm.transition_async("m1", "e1", "complete", "t1")
    assert result["transitioned"] is False
    assert result["reason"] == "no_matching_transition"


@pytest.mark.asyncio
async def test_transition_async_updates_existing_row(monkeypatch: pytest.MonkeyPatch) -> None:
    defn = _defn()
    instance = StateMachineInstance(
        instance_id="i1", machine_id="m1", tenant_id="t1", entity_id="e1",
        current_state="pending",
    )
    row = MagicMock(current_state="pending", history=[], status="running")
    session = FakeSession(execute_results=[_Result(), _Result(scalar=row)])
    sm = StateMachine()
    sm._db_factory = session
    monkeypatch.setattr(sm, "get_instance_async", AsyncMock(return_value=instance))
    monkeypatch.setattr(sm, "get_definition_async", AsyncMock(return_value=defn))

    result = await sm.transition_async("m1", "e1", "complete", "t1")
    assert result["transitioned"] is True
    assert row.current_state == "completed"
    assert row.status == "completed"  # terminal state marks it completed
    assert len(row.history) == 1
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_transition_async_row_missing_still_returns_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defn = _defn()
    instance = StateMachineInstance(
        instance_id="i1", machine_id="m1", tenant_id="t1", entity_id="e1",
        current_state="pending",
    )
    session = FakeSession(execute_results=[_Result(), _Result(scalar=None)])
    sm = StateMachine()
    sm._db_factory = session
    monkeypatch.setattr(sm, "get_instance_async", AsyncMock(return_value=instance))
    monkeypatch.setattr(sm, "get_definition_async", AsyncMock(return_value=defn))

    result = await sm.transition_async("m1", "e1", "complete", "t1")
    assert result["transitioned"] is True
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_transition_async_db_failure_still_returns_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defn = _defn()
    instance = StateMachineInstance(
        instance_id="i1", machine_id="m1", tenant_id="t1", entity_id="e1",
        current_state="pending",
    )
    session = FakeSession(raise_on_call_index=0)
    sm = StateMachine()
    sm._db_factory = session
    monkeypatch.setattr(sm, "get_instance_async", AsyncMock(return_value=instance))
    monkeypatch.setattr(sm, "get_definition_async", AsyncMock(return_value=defn))

    result = await sm.transition_async("m1", "e1", "complete", "t1")
    assert result["transitioned"] is True
    assert result["to_state"] == "completed"
