from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.coordination.outbox import PostgresOutboxRepository
from app.coordination.replay import SequenceReplay
from app.coordination.store import (
    CoordinationStore,
    OptimisticConflictError,
    PostgresReplayRepository,
)
from app.tenancy.context import PlanTier, TenantContext

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse",
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def store_context():
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = f"coord-{uuid.uuid4().hex[:12]}"
    async with factory() as session, session.begin():
        await session.execute(
            text(
                """INSERT INTO tenants (id, name, email, plan_tier, is_active)
                VALUES (:id, :name, :email, 'free', true)"""
            ),
            {
                "id": tenant_id,
                "name": "Coordination Test",
                "email": f"{tenant_id}@example.test",
            },
        )
    context = TenantContext(
        tenant_id=tenant_id,
        plan=PlanTier.FREE,
        api_key_id="coordination-test",
    )
    yield CoordinationStore(factory), context, factory
    async with factory() as session, session.begin():
        await session.execute(text("SET LOCAL row_security = off"))
        await session.execute(
            text("DELETE FROM coordination_outbox WHERE tenant_id=:tenant"),
            {"tenant": tenant_id},
        )
        await session.execute(
            text("DELETE FROM coordination_events WHERE tenant_id=:tenant"),
            {"tenant": tenant_id},
        )
        await session.execute(
            text("DELETE FROM coordination_sessions WHERE tenant_id=:tenant"),
            {"tenant": tenant_id},
        )
        await session.execute(
            text("DELETE FROM tenants WHERE id=:tenant"), {"tenant": tenant_id}
        )
    await engine.dispose()


async def test_mutation_atomically_writes_state_event_and_outbox(store_context) -> None:
    store, context, factory = store_context
    session = await store.create_session(
        context,
        civilization_id="civ-1",
        goal_id="goal-1",
        policy_snapshot={"version": "p1"},
        budget_snapshot={"ceiling": 10},
    )
    accepted = await store.transition_session(
        context,
        session_id=session.session_id,
        expected_version=1,
        target_state="active",
        idempotency_key="start-1",
    )

    assert accepted.sequence == 1
    assert accepted.version == 2
    async with factory() as db, db.begin():
        await db.execute(
            text("SELECT set_config('app.tenant_id', :tenant, true)"),
            {"tenant": context.tenant_id},
        )
        counts = (
            await db.execute(
                text(
                    """SELECT
                    (SELECT count(*) FROM coordination_events WHERE session_id=:session),
                    (SELECT count(*) FROM coordination_outbox WHERE session_id=:session)"""
                ),
                {"session": session.session_id},
            )
        ).one()
    assert counts == (1, 1)


async def test_idempotency_replay_returns_original_accepted_transition(store_context) -> None:
    store, context, _ = store_context
    session = await store.create_session(
        context,
        civilization_id="civ-1",
        goal_id="goal-1",
        policy_snapshot={},
        budget_snapshot={},
    )
    first = await store.transition_session(
        context,
        session_id=session.session_id,
        expected_version=1,
        target_state="active",
        idempotency_key="same-command",
    )
    replay = await store.transition_session(
        context,
        session_id=session.session_id,
        expected_version=1,
        target_state="active",
        idempotency_key="same-command",
    )
    assert replay == first


async def test_optimistic_conflict_does_not_allocate_event_or_outbox(store_context) -> None:
    store, context, _ = store_context
    session = await store.create_session(
        context,
        civilization_id="civ-1",
        goal_id="goal-1",
        policy_snapshot={},
        budget_snapshot={},
    )
    await store.transition_session(
        context,
        session_id=session.session_id,
        expected_version=1,
        target_state="active",
        idempotency_key="start",
    )
    with pytest.raises(OptimisticConflictError):
        await store.transition_session(
            context,
            session_id=session.session_id,
            expected_version=1,
            target_state="completed",
            idempotency_key="stale",
        )


async def test_concurrent_writers_accept_only_one_expected_version(store_context) -> None:
    store, context, _ = store_context
    session = await store.create_session(
        context,
        civilization_id="civ-1",
        goal_id="goal-1",
        policy_snapshot={},
        budget_snapshot={},
    )
    outcomes = await asyncio.gather(
        *(
            store.transition_session(
                context,
                session_id=session.session_id,
                expected_version=1,
                target_state="active",
                idempotency_key=f"writer-{index}",
            )
            for index in range(5)
        ),
        return_exceptions=True,
    )
    accepted = [item for item in outcomes if not isinstance(item, BaseException)]
    conflicts = [item for item in outcomes if isinstance(item, OptimisticConflictError)]
    assert len(accepted) == 1
    assert len(conflicts) == 4


async def test_postgres_replay_uses_exclusive_sequence_cursor(store_context) -> None:
    store, context, factory = store_context
    session = await store.create_session(
        context,
        civilization_id="civ-1",
        goal_id="goal-1",
        policy_snapshot={},
        budget_snapshot={},
    )
    await store.transition_session(
        context,
        session_id=session.session_id,
        expected_version=1,
        target_state="active",
        idempotency_key="start",
    )
    await store.transition_session(
        context,
        session_id=session.session_id,
        expected_version=2,
        target_state="completed",
        idempotency_key="finish",
    )

    events = await SequenceReplay(PostgresReplayRepository(factory)).page(
        tenant_id=context.tenant_id,
        session_id=session.session_id,
        after_sequence=1,
        limit=10,
    )

    assert [event.sequence for event in events] == [2]
    assert events[0].event_type == "session.state_changed"


async def test_postgres_outbox_claim_is_exclusive_and_confirmed(store_context) -> None:
    store, context, factory = store_context
    session = await store.create_session(
        context,
        civilization_id="civ-1",
        goal_id="goal-1",
        policy_snapshot={},
        budget_snapshot={},
    )
    await store.transition_session(
        context,
        session_id=session.session_id,
        expected_version=1,
        target_state="active",
        idempotency_key="start",
    )
    repository = PostgresOutboxRepository(factory, tenant_id=context.tenant_id)

    claimed = await repository.claim(owner="worker-a", limit=10)
    competing = await repository.claim(owner="worker-b", limit=10)

    assert len(claimed) == 1
    assert competing == []
    await repository.mark_published(claimed[0].record_id, "1-0")
    assert await repository.claim(owner="worker-b", limit=10) == []
