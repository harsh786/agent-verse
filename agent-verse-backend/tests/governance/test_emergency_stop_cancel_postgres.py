"""INC-04 / INC-06: emergency-stop goal cancellation against real Postgres + Redis.

``cancel_goals_under_stop`` (run by the ``cancel_goals_for_emergency_stop``
Celery task) pages the tenant's (or one org's) non-terminal goals by keyset,
raises each goal's Redis cancel flag and marks the batch cancelled — as the
NOBYPASSRLS application role, never touching terminal goals or other orgs.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.governance.emergency_stop import cancel_goals_under_stop
from tests.memory._pg import app_role_engine, sessionmaker_for

pytestmark = pytest.mark.integration


async def _seed(admin_url: str, tenant_id: str, goals: list[tuple[str, str, str | None]]) -> None:
    admin = create_async_engine(admin_url)
    try:
        async with admin.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, :id, :email, 'free', true) ON CONFLICT (id) DO NOTHING"
                ),
                {"id": tenant_id, "email": f"{tenant_id}@example.test"},
            )
            for gid, status, org in goals:
                await conn.execute(
                    text(
                        "INSERT INTO goals (id, tenant_id, goal_text, status, priority, "
                        "autonomy_mode, workflow_mode, execution_context, dry_run, iterations) "
                        "VALUES (:id, :t, 'g', :s, 'normal', 'bounded-autonomous', "
                        "'single_agent', CAST(:ec AS jsonb), false, 0)"
                    ),
                    {
                        "id": gid,
                        "t": tenant_id,
                        "s": status,
                        "ec": json.dumps({"org_id": org} if org else {}),
                    },
                )
    finally:
        await admin.dispose()


async def test_org_stop_cancels_only_that_orgs_active_goals_in_batches(
    pg_url: str, redis_url: str
) -> None:
    import redis.asyncio as aioredis

    tenant = f"t-estop-{uuid.uuid4().hex[:8]}"
    active_a = [f"a{i}{uuid.uuid4().hex[:20]}" for i in range(5)]
    done_a = f"d{uuid.uuid4().hex[:20]}"
    active_b = f"b{uuid.uuid4().hex[:20]}"
    await _seed(
        pg_url,
        tenant,
        [(g, "executing", "org-a") for g in active_a]
        + [(done_a, "complete", "org-a"), (active_b, "executing", "org-b")],
    )
    engine = await app_role_engine(pg_url, ["goals"])
    r = aioredis.from_url(redis_url, decode_responses=True)
    try:
        factory = sessionmaker_for(engine)
        result = await cancel_goals_under_stop(factory, r, tenant, "org-a", batch_size=2)
        assert result == {"scanned": 5, "cancelled": 5, "signal_failures": 0}
        admin = create_async_engine(pg_url)
        async with admin.connect() as conn:
            rows = dict(
                (
                    await conn.execute(
                        text("SELECT id, status FROM goals WHERE tenant_id = :t"), {"t": tenant}
                    )
                ).all()
            )
        await admin.dispose()
        assert all(rows[g] == "cancelled" for g in active_a)
        assert rows[done_a] == "complete"
        assert rows[active_b] == "executing"
        for g in active_a:
            assert await r.get(f"goal_cancelled:{g}") == "1"
        assert await r.get(f"goal_cancelled:{active_b}") is None
        # Idempotent: a retried task finds nothing left to cancel.
        again = await cancel_goals_under_stop(factory, r, tenant, "org-a", batch_size=2)
        assert again["cancelled"] == 0
    finally:
        await r.aclose()
        await engine.dispose()


async def test_tenant_stop_cancels_every_active_goal(pg_url: str, redis_url: str) -> None:
    import redis.asyncio as aioredis

    tenant = f"t-estop-{uuid.uuid4().hex[:8]}"
    ids = [uuid.uuid4().hex for _ in range(3)]
    await _seed(
        pg_url,
        tenant,
        [(ids[0], "planning", None), (ids[1], "waiting_human", "o"), (ids[2], "failed", None)],
    )
    engine = await app_role_engine(pg_url, ["goals"])
    r = aioredis.from_url(redis_url, decode_responses=True)
    try:
        result = await cancel_goals_under_stop(sessionmaker_for(engine), r, tenant, None)
        assert result["cancelled"] == 2
    finally:
        await r.aclose()
        await engine.dispose()
