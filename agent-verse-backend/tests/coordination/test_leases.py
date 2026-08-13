from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.coordination.leases import (
    ActiveClaimError,
    InMemoryLeaseRepository,
    LeaseManager,
    PostgresLeaseRepository,
    StaleFencingTokenError,
)
from app.db.models.coordination import COORDINATION_TABLES

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse",
)


@pytest_asyncio.fixture
async def postgres_lease_repository():
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = f"lease-{uuid.uuid4().hex[:12]}"
    work_item_id = uuid.uuid4().hex
    async with factory() as session, session.begin():
        await session.execute(
            text(
                """INSERT INTO tenants (id, name, email, plan_tier, is_active)
                VALUES (:id, 'Lease Test', :email, 'free', true)"""
            ),
            {"id": tenant_id, "email": f"{tenant_id}@example.test"},
        )
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant, true)"),
            {"tenant": tenant_id},
        )
        await session.execute(
            insert(COORDINATION_TABLES["work_items"]).values(
                id=work_item_id,
                tenant_id=tenant_id,
                session_id="session-1",
                state="pending",
                dependencies={},
                version=1,
            )
        )
    yield PostgresLeaseRepository(factory), tenant_id, work_item_id
    async with factory() as session, session.begin():
        await session.execute(text("SET LOCAL row_security = off"))
        await session.execute(
            text("DELETE FROM claims WHERE tenant_id=:tenant"), {"tenant": tenant_id}
        )
        await session.execute(
            text("DELETE FROM work_items WHERE tenant_id=:tenant"),
            {"tenant": tenant_id},
        )
        await session.execute(
            text("DELETE FROM tenants WHERE id=:tenant"), {"tenant": tenant_id}
        )
    await engine.dispose()


async def test_acquire_is_single_owner_and_fencing_tokens_are_monotonic() -> None:
    now = datetime.now(UTC)
    manager = LeaseManager(InMemoryLeaseRepository())

    first = await manager.acquire(
        tenant_id="tenant",
        work_item_id="work",
        owner_agent_id="agent-a",
        now=now,
        ttl=timedelta(seconds=30),
    )
    with pytest.raises(ActiveClaimError):
        await manager.acquire(
            tenant_id="tenant",
            work_item_id="work",
            owner_agent_id="agent-b",
            now=now + timedelta(seconds=1),
            ttl=timedelta(seconds=30),
        )

    reclaimed = await manager.acquire(
        tenant_id="tenant",
        work_item_id="work",
        owner_agent_id="agent-b",
        now=now + timedelta(seconds=31),
        ttl=timedelta(seconds=30),
    )
    assert reclaimed.fencing_token == first.fencing_token + 1


async def test_stale_owner_cannot_heartbeat_complete_or_release() -> None:
    now = datetime.now(UTC)
    manager = LeaseManager(InMemoryLeaseRepository())
    first = await manager.acquire(
        tenant_id="tenant",
        work_item_id="work",
        owner_agent_id="agent-a",
        now=now,
        ttl=timedelta(seconds=1),
    )
    current = await manager.acquire(
        tenant_id="tenant",
        work_item_id="work",
        owner_agent_id="agent-b",
        now=now + timedelta(seconds=2),
        ttl=timedelta(seconds=30),
    )

    with pytest.raises(StaleFencingTokenError):
        await manager.heartbeat(
            tenant_id="tenant",
            work_item_id="work",
            owner_agent_id="agent-a",
            fencing_token=first.fencing_token,
            now=now + timedelta(seconds=3),
            ttl=timedelta(seconds=10),
        )
    for operation in (manager.complete, manager.release):
        with pytest.raises(StaleFencingTokenError):
            await operation(
                tenant_id="tenant",
                work_item_id="work",
                owner_agent_id="agent-a",
                fencing_token=first.fencing_token,
                now=now + timedelta(seconds=3),
            )

    completed = await manager.complete(
        tenant_id="tenant",
        work_item_id="work",
        owner_agent_id="agent-b",
        fencing_token=current.fencing_token,
        now=now + timedelta(seconds=3),
    )
    assert completed.state == "completed"


async def test_heartbeat_extends_only_current_active_lease() -> None:
    now = datetime.now(UTC)
    manager = LeaseManager(InMemoryLeaseRepository())
    claim = await manager.acquire(
        tenant_id="tenant",
        work_item_id="work",
        owner_agent_id="agent-a",
        now=now,
        ttl=timedelta(seconds=10),
    )

    heartbeat = await manager.heartbeat(
        tenant_id="tenant",
        work_item_id="work",
        owner_agent_id="agent-a",
        fencing_token=claim.fencing_token,
        now=now + timedelta(seconds=5),
        ttl=timedelta(seconds=20),
    )

    assert heartbeat.heartbeat_at == now + timedelta(seconds=5)
    assert heartbeat.lease_expires_at == now + timedelta(seconds=25)


@pytest.mark.integration
async def test_postgres_lease_reclaim_fences_stale_worker(
    postgres_lease_repository,
) -> None:
    repository, tenant_id, work_item_id = postgres_lease_repository
    manager = LeaseManager(repository)
    now = datetime.now(UTC)
    first = await manager.acquire(
        tenant_id=tenant_id,
        work_item_id=work_item_id,
        owner_agent_id="agent-a",
        now=now,
        ttl=timedelta(seconds=1),
    )
    reclaimed = await manager.acquire(
        tenant_id=tenant_id,
        work_item_id=work_item_id,
        owner_agent_id="agent-b",
        now=now + timedelta(seconds=2),
        ttl=timedelta(seconds=30),
    )

    assert reclaimed.fencing_token == first.fencing_token + 1
    with pytest.raises(StaleFencingTokenError):
        await manager.complete(
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            owner_agent_id="agent-a",
            fencing_token=first.fencing_token,
            now=now + timedelta(seconds=3),
        )
