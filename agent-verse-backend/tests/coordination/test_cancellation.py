from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.coordination.cancellation import (
    CancellationCoordinator,
    IncompatibleCheckpointError,
    InMemoryCancellationRepository,
    PostgresCancellationRepository,
    ResumeRejectedError,
)
from app.db.models.coordination import COORDINATION_TABLES
from app.db.rls import sqlalchemy_rls_context

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse",
)


@pytest_asyncio.fixture
async def postgres_cancellation_state():
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = f"cancel-{uuid.uuid4().hex[:11]}"
    session_id = uuid.uuid4().hex
    execution_id = uuid.uuid4().hex
    work_item_id = uuid.uuid4().hex
    claim_id = uuid.uuid4().hex
    now = datetime.now(UTC)
    async with factory() as db, db.begin():
        await db.execute(
            text(
                """INSERT INTO tenants (id, name, email, plan_tier, is_active)
                VALUES (:id, 'Cancellation Test', :email, 'free', true)"""
            ),
            {"id": tenant_id, "email": f"{tenant_id}@example.test"},
        )
        async with sqlalchemy_rls_context(db, tenant_id):
            await db.execute(
                insert(COORDINATION_TABLES["coordination_sessions"]).values(
                    id=session_id,
                    tenant_id=tenant_id,
                    civilization_id="civilization-1",
                    goal_id="goal-1",
                    state="active",
                    policy_snapshot={},
                    budget_snapshot={},
                    deadline=None,
                    cancellation_requested_at=None,
                    cancellation_reason=None,
                    next_sequence=1,
                )
            )
            await db.execute(
                insert(COORDINATION_TABLES["strategy_executions"]).values(
                    id=execution_id,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    goal_id="goal-1",
                    adapter_id="react",
                    adapter_version="1.0.0",
                    state_schema_version=2,
                    profile_snapshot={},
                    state="running",
                    result={},
                    cost=0,
                    idempotency_key="initial",
                    deadline=None,
                    attempt=1,
                    prior_execution_id=None,
                )
            )
            await db.execute(
                insert(COORDINATION_TABLES["strategy_checkpoints"]).values(
                    id=uuid.uuid4().hex,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    execution_id=execution_id,
                    sequence=1,
                    state_reference="checkpoint://one",
                )
            )
            await db.execute(
                insert(COORDINATION_TABLES["work_items"]).values(
                    id=work_item_id,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    state="running",
                    dependencies={},
                )
            )
            await db.execute(
                insert(COORDINATION_TABLES["claims"]).values(
                    id=claim_id,
                    tenant_id=tenant_id,
                    work_item_id=work_item_id,
                    owner_agent_id="agent-a",
                    lease_id=uuid.uuid4().hex,
                    fencing_token=7,
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(minutes=1),
                    state="active",
                )
            )
    yield factory, tenant_id, session_id, execution_id, work_item_id
    async with factory() as db, db.begin():
        await db.execute(text("SET LOCAL row_security = off"))
        for table_name in (
            "coordination_outbox",
            "coordination_events",
            "claims",
            "work_items",
            "strategy_checkpoints",
            "strategy_executions",
            "coordination_sessions",
        ):
            await db.execute(
                text(f"DELETE FROM {table_name} WHERE tenant_id=:tenant"),
                {"tenant": tenant_id},
            )
        await db.execute(text("DELETE FROM tenants WHERE id=:tenant"), {"tenant": tenant_id})
    await engine.dispose()


async def test_cancel_persists_before_wakeup_and_is_idempotent() -> None:
    order: list[str] = []
    repository = InMemoryCancellationRepository(on_persist=lambda: order.append("persist"))

    async def wakeup(_: str) -> None:
        order.append("wakeup")

    coordinator = CancellationCoordinator(repository, wakeup=wakeup)
    first = await coordinator.cancel("tenant", "session", reason="user_requested")
    replay = await coordinator.cancel("tenant", "session", reason="user_requested")

    assert first == replay
    assert order == ["persist", "wakeup"]


async def test_cancel_propagates_to_children_and_invalidates_leases() -> None:
    repository = InMemoryCancellationRepository()
    repository.add_child("tenant", "session", "child-1")
    repository.add_child("tenant", "session", "child-2")
    repository.add_lease("tenant", "session", "work-1", fencing_token=4)

    result = await CancellationCoordinator(repository).cancel(
        "tenant", "session", reason="deadline"
    )

    assert result.cancelled_children == ("child-1", "child-2")
    assert repository.lease_token("tenant", "session", "work-1") == 5


async def test_resume_requires_compatible_checkpoint_and_nonterminal_session() -> None:
    repository = InMemoryCancellationRepository()
    repository.set_checkpoint(
        "tenant",
        "session",
        adapter_version="1.0.0",
        state_schema_version=2,
        reference="checkpoint-1",
    )
    coordinator = CancellationCoordinator(repository)
    await coordinator.cancel("tenant", "session", reason="pause")

    resumed = await coordinator.resume(
        "tenant",
        "session",
        adapter_version="1.0.0",
        state_schema_version=2,
    )
    assert resumed.checkpoint_reference == "checkpoint-1"
    assert resumed.attempt == 2

    await coordinator.cancel("tenant", "session", reason="pause-again")
    with pytest.raises(IncompatibleCheckpointError):
        await coordinator.resume(
            "tenant",
            "session",
            adapter_version="2.0.0",
            state_schema_version=2,
        )

    repository.set_state("tenant", "session", "completed")
    with pytest.raises(ResumeRejectedError):
        await coordinator.resume(
            "tenant",
            "session",
            adapter_version="1.0.0",
            state_schema_version=2,
        )


@pytest.mark.integration
async def test_postgres_cancel_invalidates_claim_and_resume_creates_new_attempt(
    postgres_cancellation_state,
) -> None:
    factory, tenant_id, session_id, execution_id, work_item_id = (
        postgres_cancellation_state
    )
    coordinator = CancellationCoordinator(PostgresCancellationRepository(factory))

    cancelled = await coordinator.cancel(tenant_id, session_id, reason="operator")
    replay = await coordinator.cancel(tenant_id, session_id, reason="operator")
    assert replay == cancelled
    assert cancelled.cancelled_children == (execution_id,)
    assert cancelled.invalidated_work_items == (work_item_id,)

    async with factory() as db, db.begin(), sqlalchemy_rls_context(db, tenant_id):
        claim = (
            await db.execute(
                select(
                    COORDINATION_TABLES["claims"].c.fencing_token,
                    COORDINATION_TABLES["claims"].c.state,
                ).where(COORDINATION_TABLES["claims"].c.work_item_id == work_item_id)
            )
        ).one()
        assert claim.fencing_token == 8
        assert claim.state == "released"

    resumed = await coordinator.resume(
        tenant_id,
        session_id,
        adapter_version="1.0.0",
        state_schema_version=2,
    )
    assert resumed.checkpoint_reference == "checkpoint://one"
    assert resumed.attempt == 2

    async with factory() as db, db.begin(), sqlalchemy_rls_context(db, tenant_id):
        attempts = (
            await db.execute(
                select(
                    COORDINATION_TABLES["strategy_executions"].c.attempt,
                    COORDINATION_TABLES["strategy_executions"].c.prior_execution_id,
                )
                .where(
                    COORDINATION_TABLES["strategy_executions"].c.session_id
                    == session_id
                )
                .order_by(COORDINATION_TABLES["strategy_executions"].c.attempt)
            )
        ).all()
        assert attempts == [(1, None), (2, execution_id)]
