"""SVC-30 integration (Postgres): a DB-loaded complete goal is evicted after its TTL,
and reading many distinct goals keeps the replica cache bounded.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/services/test_goal_cache_bounded_integration.py -m integration --no-cov
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.services import goal_service as gs_mod
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration


@pytest.fixture
async def db(pg_url: str) -> AsyncIterator[Any]:
    engine = create_async_engine(pg_url, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_db_loaded_goals_are_evicted_and_bounded(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = uuid.uuid4().hex
    ids = [uuid.uuid4().hex for _ in range(60)]
    async with db() as s, s.begin():
        await s.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:t, 'T', :e)"),
            {"t": tid, "e": f"{tid}@example.test"},
        )
        for gid in ids:
            await s.execute(
                text(
                    "INSERT INTO goals (id, tenant_id, goal_text, status, priority, "
                    "autonomy_mode, workflow_mode, execution_context, dry_run, iterations, "
                    "completed_at) VALUES (:g, :t, 'x', 'complete', 'normal', 'supervised', "
                    "'single_agent', '{}'::jsonb, false, 1, now() - interval '2 days')"
                ),
                {"g": gid, "t": tid},
            )
    ctx = TenantContext(tenant_id=tid, plan=PlanTier.PROFESSIONAL, api_key_id="k")
    monkeypatch.setattr(gs_mod, "_MAX_CACHED_GOALS", 25)
    svc = GoalService(db_session_factory=db)
    for gid in ids:
        rec = await svc._db_get_goal_record(gid, ctx)
        assert rec is not None and rec.completed_at
    assert len(svc._goals) <= 25

    # TTL eviction (1 h) drops every DB-loaded complete goal: they finished 2 days ago.
    assert svc._evict_stale_goals() > 0
    assert svc._goals == {}
