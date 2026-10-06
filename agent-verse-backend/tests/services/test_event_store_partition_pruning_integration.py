"""a08-F191-03: a goal's event replay skips the monthly partitions before the goal.

list_events / list_events_since filtered only on tenant / goal / sequence, so
every replay page probed every monthly goal_events partition. They now carry a
created_at lower bound from the goal row (scalar subquery, pruned at executor
start-up / run time).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.models.goal import GoalEvent
from app.services.event_store import EventStore, _since_goal_created
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration

_OLD_PARTITION = "goal_events_2020_01"


@pytest.fixture
async def db(pg_url: str) -> AsyncIterator[Any]:
    engine = create_async_engine(pg_url, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _goal(db: Any, tid: str | None = None) -> tuple[TenantContext, str]:
    tid, gid = tid or uuid.uuid4().hex, uuid.uuid4().hex
    async with db() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO tenants (id, name, email) VALUES (:t, 'T', :e) "
                "ON CONFLICT (id) DO NOTHING"
            ),
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


async def test_replay_returns_the_goals_events_and_skips_older_partitions(db: Any) -> None:
    ctx, gid = await _goal(db)
    _, old_gid = await _goal(db, ctx.tenant_id)
    async with db() as s, s.begin():
        await s.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {_OLD_PARTITION} PARTITION OF goal_events "
                "FOR VALUES FROM ('2020-01-01') TO ('2020-02-01')"
            )
        )
        # An old row in the old partition, so scanning it is not free.
        await s.execute(
            text(
                "INSERT INTO goal_events (id, tenant_id, goal_id, sequence, event_type, "
                "payload, created_at) VALUES (:i, :t, :g, 1, 'old', '{}'::jsonb, "
                "'2020-01-15T00:00:00Z')"
            ),
            {"i": uuid.uuid4().hex, "t": ctx.tenant_id, "g": old_gid},
        )

    store = EventStore(db)
    for i in range(3):
        await store.append_event(gid, {"type": "e", "i": i}, tenant_ctx=ctx)
    assert [e["i"] for e in await store.list_events(gid, tenant_ctx=ctx)] == [0, 1, 2]
    since = await store.list_events_since(gid, 1, tenant_ctx=ctx)
    assert [e["_seq"] for e in since] == [2, 3]

    stmt = (
        select(GoalEvent)
        .where(
            GoalEvent.tenant_id == ctx.tenant_id,
            GoalEvent.goal_id == gid,
            GoalEvent.sequence > 0,
            _since_goal_created(gid, ctx.tenant_id),
        )
        .order_by(GoalEvent.sequence)
        .limit(100)
    )
    unbounded = (
        select(GoalEvent)
        .where(
            GoalEvent.tenant_id == ctx.tenant_id,
            GoalEvent.goal_id == gid,
            GoalEvent.sequence > 0,
        )
        .order_by(GoalEvent.sequence)
        .limit(100)
    )

    async def _plan(q: Any) -> str:
        sql = str(q.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        async with db() as s, s.begin():
            rows = (await s.execute(text(f"EXPLAIN (ANALYZE, COSTS OFF) {sql}"))).all()
        return "\n".join(r[0] for r in rows)

    # The old query (tenant / goal / sequence only) scans the 2020 partition.
    before = await _plan(unbounded)
    assert any(
        f" on {_OLD_PARTITION} " in line and "(never executed)" not in line
        for line in before.splitlines()
    ), before
    plan = await _plan(stmt)
    # Pruned at run time (the bound comes from the goal row): the partition from
    # before the goal is never executed. Without the bound it is scanned.
    old_lines = [line for line in plan.splitlines() if f" on {_OLD_PARTITION} " in line]
    assert old_lines, plan
    assert all("(never executed)" in line for line in old_lines), plan
