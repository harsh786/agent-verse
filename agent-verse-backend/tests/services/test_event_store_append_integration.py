"""SVC-08 integration (Postgres + Redis): concurrent appends are gap-free and return
their sequence; a failed append parked in the outbox is replayed into the store.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/services/test_event_store_append_integration.py -m integration --no-cov
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.services.event_store import (
    EventAppendError,
    EventStore,
    buffer_failed_event,
    drain_event_outbox,
)
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration


@pytest.fixture
async def db(pg_url: str) -> AsyncIterator[Any]:
    engine = create_async_engine(pg_url, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _goal(db: Any) -> tuple[TenantContext, str]:
    tid, gid = uuid.uuid4().hex, uuid.uuid4().hex
    async with db() as s, s.begin():
        await s.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:t, 'T', :e)"),
            {"t": tid, "e": f"{tid}@example.test"},
        )
        await s.execute(
            text(
                "INSERT INTO goals (id, tenant_id, goal_text, status, priority, "
                "autonomy_mode, workflow_mode, execution_context, dry_run, iterations) "
                "VALUES (:g, :t, 'x', 'executing', 'normal', 'supervised', 'single_agent', "
                "'{}'::jsonb, false, 0)"
            ),
            {"g": gid, "t": tid},
        )
    return TenantContext(tenant_id=tid, plan=PlanTier.PROFESSIONAL, api_key_id="k"), gid


async def test_fifty_concurrent_appends_are_gap_free(db: Any) -> None:
    ctx, gid = await _goal(db)
    store = EventStore(db)
    seqs = await asyncio.gather(
        *(store.append_event(gid, {"type": "e", "i": i}, tenant_ctx=ctx) for i in range(50))
    )
    assert sorted(seqs) == list(range(1, 51))
    replay = await store.list_events_since(gid, 0, limit=100, tenant_ctx=ctx)
    assert [e["_seq"] for e in replay] == list(range(1, 51))


async def test_missing_goal_row_raises_and_outbox_replays_once_it_exists(
    db: Any, redis_url: str
) -> None:
    import redis.asyncio as aioredis

    ctx, gid = await _goal(db)
    store = EventStore(db)
    ghost = uuid.uuid4().hex
    with pytest.raises(EventAppendError):
        await store.append_event(ghost, {"type": "early"}, tenant_ctx=ctx)

    redis = aioredis.from_url(redis_url, decode_responses=True)
    try:
        await redis.delete("goal_event_outbox")
        await buffer_failed_event(
            redis, tenant_id=ctx.tenant_id, goal_id=gid, event={"type": "late"}
        )
        assert await drain_event_outbox(store, redis) == {"replayed": 1, "dropped": 0}
    finally:
        await redis.aclose()
    assert [e["type"] for e in await store.list_events(gid, tenant_ctx=ctx)] == ["late"]
