"""DB persistence + tenant-isolation proof for the state-machine registry.

Connects to the running Postgres (``DATABASE_URL``; ``alembic upgrade head`` must have
created the ``trigger_state_machine_definitions`` /
``trigger_state_machine_instances`` tables with their RLS policies) and proves,
via the ``StateMachine`` async API:

1. A definition written on one ``StateMachine`` is visible on a *fresh*
   ``StateMachine`` sharing the same db_factory (survives a restart).
2. ``create_instance`` + ``transition`` persist ``current_state`` across a
   fresh registry.
3. A terminal transition marks the instance ``completed`` durably.
4. Tenant B cannot see tenant A's machine (per-tenant scoping).

Run with::

    source /tmp/av.env
    uv run pytest tests/api/test_state_machines_persistence.py -q
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.triggers.state_machine import (
    StateDefinition,
    StateMachine,
    StateMachineDefinition,
    TransitionDefinition,
)

pytestmark = pytest.mark.integration

_DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _make_definition(machine_id: str, tenant_id: str) -> StateMachineDefinition:
    return StateMachineDefinition(
        machine_id=machine_id,
        tenant_id=tenant_id,
        name="Order Flow",
        states=[
            StateDefinition(name="pending", is_initial=True),
            StateDefinition(name="processing"),
            StateDefinition(name="completed", is_terminal=True),
        ],
        transitions=[
            TransitionDefinition(from_state="pending", to_state="processing", event="start"),
            TransitionDefinition(from_state="processing", to_state="completed", event="complete"),
        ],
    )


@pytest.fixture
async def db_factory() -> AsyncIterator[async_sessionmaker]:
    if not _DATABASE_URL:
        pytest.skip("DATABASE_URL not set")
    engine = create_async_engine(_DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # Sanity check the tables exist (migration applied); skip otherwise.
    try:
        async with factory() as session:
            await session.execute(
                text("SELECT 1 FROM trigger_state_machine_definitions LIMIT 1")
            )
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"state_machine tables unavailable: {exc}")
    try:
        yield factory
    finally:
        await engine.dispose()


async def _cleanup(factory: async_sessionmaker, tenant_ids: list[str]) -> None:
    async with factory() as session:
        for tid in tenant_ids:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tid}
            )
            await session.execute(
                text("DELETE FROM trigger_state_machine_instances WHERE tenant_id = :tid"),
                {"tid": tid},
            )
            await session.execute(
                text("DELETE FROM trigger_state_machine_definitions WHERE tenant_id = :tid"),
                {"tid": tid},
            )
        await session.commit()


async def test_definition_survives_restart(db_factory: async_sessionmaker) -> None:
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    machine_id = uuid.uuid4().hex
    try:
        sm1 = StateMachine()
        sm1._db_factory = db_factory
        await sm1.define_async(_make_definition(machine_id, tenant))

        # Fresh registry (simulated restart) sharing the same factory.
        sm2 = StateMachine()
        sm2._db_factory = db_factory

        loaded = await sm2.get_definition_async(machine_id, tenant)
        assert loaded is not None
        assert loaded.name == "Order Flow"
        assert {s.name for s in loaded.states} == {"pending", "processing", "completed"}
        assert loaded.initial_state() == "pending"

        listed = await sm2.list_definitions_async(tenant)
        assert any(d.machine_id == machine_id for d in listed)
    finally:
        await _cleanup(db_factory, [tenant])


async def test_instance_transition_persists(db_factory: async_sessionmaker) -> None:
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    machine_id = uuid.uuid4().hex
    try:
        sm1 = StateMachine()
        sm1._db_factory = db_factory
        await sm1.define_async(_make_definition(machine_id, tenant))
        inst = await sm1.create_instance_async(machine_id, "order-1", tenant)
        assert inst.current_state == "pending"

        result = await sm1.transition_async(machine_id, "order-1", "start", tenant)
        assert result["transitioned"] is True
        assert result["to_state"] == "processing"

        # Fresh registry reads the persisted current_state.
        sm2 = StateMachine()
        sm2._db_factory = db_factory
        reloaded = await sm2.get_instance_async("order-1", tenant)
        assert reloaded is not None
        assert reloaded.current_state == "processing"
        assert reloaded.status == "running"
        assert len(reloaded.history) == 1
        assert reloaded.history[0]["event"] == "start"
    finally:
        await _cleanup(db_factory, [tenant])


async def test_terminal_transition_marks_completed(db_factory: async_sessionmaker) -> None:
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    machine_id = uuid.uuid4().hex
    try:
        sm = StateMachine()
        sm._db_factory = db_factory
        await sm.define_async(_make_definition(machine_id, tenant))
        await sm.create_instance_async(machine_id, "order-2", tenant)
        await sm.transition_async(machine_id, "order-2", "start", tenant)
        result = await sm.transition_async(machine_id, "order-2", "complete", tenant)
        assert result["to_state"] == "completed"

        sm2 = StateMachine()
        sm2._db_factory = db_factory
        reloaded = await sm2.get_instance_async("order-2", tenant)
        assert reloaded is not None
        assert reloaded.current_state == "completed"
        assert reloaded.status == "completed"
        assert len(reloaded.history) == 2
    finally:
        await _cleanup(db_factory, [tenant])


async def test_tenant_isolation(db_factory: async_sessionmaker) -> None:
    tenant_a = f"a-{uuid.uuid4().hex[:8]}"
    tenant_b = f"b-{uuid.uuid4().hex[:8]}"
    machine_id = uuid.uuid4().hex
    try:
        sm = StateMachine()
        sm._db_factory = db_factory
        await sm.define_async(_make_definition(machine_id, tenant_a))

        # Tenant B (fresh registry) cannot see tenant A's machine.
        sm_b = StateMachine()
        sm_b._db_factory = db_factory
        assert await sm_b.get_definition_async(machine_id, tenant_b) is None
        assert await sm_b.list_definitions_async(tenant_b) == []

        # Tenant A still sees it.
        assert await sm.get_definition_async(machine_id, tenant_a) is not None
    finally:
        await _cleanup(db_factory, [tenant_a, tenant_b])
