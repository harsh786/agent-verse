"""TRG-39 (+ TRG-40 on a real schema): concurrent transitions on real Postgres.

``transition_async`` read the instance, then updated it in another session with
no row lock or version, so two replicas transitioning the same instance at once
both "succeeded" from the same state and one history entry was lost. It now
reads and updates under ``SELECT ... FOR UPDATE`` in one transaction: of two
simultaneous transitions from the same state, one wins and the other sees the
new state and is rejected. Also proves the TRG-40 migration: two machines on the
same entity keep independent instances.

Stands up Postgres via testcontainers and runs ``alembic upgrade head``::

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/triggers/test_state_machine_concurrency_integration.py -q
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.triggers.state_machine import (
    StateDefinition,
    StateMachine,
    StateMachineDefinition,
    TransitionDefinition,
)

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def pg_url() -> Iterator[str]:
    try:
        from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover
        pytest.skip("testcontainers not installed")
    try:
        container = PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg")
        container.start()
    except Exception as exc:  # pragma: no cover - no Docker
        pytest.skip(f"Docker unavailable: {exc}")
    try:
        url = container.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": url},
            check=True,
            capture_output=True,
            text=True,
        )
        yield url
    finally:
        container.stop()


@pytest.fixture
async def db(pg_url: str) -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(pg_url, poolclass=NullPool)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _order(machine_id: str, tenant: str) -> StateMachineDefinition:
    return StateMachineDefinition(
        machine_id=machine_id, tenant_id=tenant, name="order",
        states=[StateDefinition(name="new", is_initial=True), StateDefinition(name="paid")],
        transitions=[TransitionDefinition(from_state="new", to_state="paid", event="pay")],
    )


async def test_two_simultaneous_transitions_one_wins_one_is_rejected(db) -> None:
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    machine = uuid.uuid4().hex
    setup = StateMachine()
    setup._db_factory = db
    await setup.define_async(_order(machine, tenant))
    await setup.create_instance_async(machine, "order-1", tenant)

    # Two "replicas" (separate registries, separate sessions) race.
    replicas = [StateMachine(), StateMachine()]
    for r in replicas:
        r._db_factory = db
    results = await asyncio.gather(
        *(r.transition_async(machine, "order-1", "pay", tenant) for r in replicas)
    )

    assert sorted(bool(r["transitioned"]) for r in results) == [False, True]
    rejected = next(r for r in results if not r["transitioned"])
    assert rejected["from_state"] == "paid"  # it saw the winner's committed state

    inst = await setup.get_instance_async(machine, "order-1", tenant)
    assert inst is not None and inst.current_state == "paid"
    assert [h["event"] for h in inst.history] == ["pay"]


async def test_two_machines_on_one_entity_are_independent(db) -> None:
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    pay, ship = uuid.uuid4().hex, uuid.uuid4().hex
    sm = StateMachine()
    sm._db_factory = db
    await sm.define_async(_order(pay, tenant))
    await sm.define_async(
        StateMachineDefinition(
            machine_id=ship, tenant_id=tenant, name="shipping",
            states=[StateDefinition(name="waiting", is_initial=True),
                    StateDefinition(name="sent")],
            transitions=[TransitionDefinition(from_state="waiting", to_state="sent",
                                              event="ship")],
        )
    )
    assert (await sm.transition_async(pay, "order-9", "pay", tenant))["to_state"] == "paid"
    shipped = await sm.transition_async(ship, "order-9", "ship", tenant)
    assert shipped["transitioned"] is True and shipped["from_state"] == "waiting"

    fresh = StateMachine()
    fresh._db_factory = db
    a = await fresh.get_instance_async(pay, "order-9", tenant)
    b = await fresh.get_instance_async(ship, "order-9", tenant)
    assert a is not None and b is not None
    assert (a.current_state, b.current_state) == ("paid", "sent")
