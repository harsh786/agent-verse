"""a08-F189-05 (Postgres): restart recovery finds orphaned in-process goals of
other replicas in the database, whatever their age, not only in the warm cache.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.services.goal_service import _RUNNER_KEY, GoalService

pytestmark = pytest.mark.integration


@pytest.fixture
async def db(pg_url: str) -> AsyncIterator[Any]:
    engine = create_async_engine(pg_url, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_db_scan_finds_old_orphans_of_other_replicas_only(db: Any) -> None:
    tid = uuid.uuid4().hex
    goals = {
        "old_orphan": ("executing", {"kind": "in_process", "replica": "replica-dead"}, 30),
        "mine": ("executing", {"kind": "in_process", "replica": "replica-me"}, 0),
        "worker": ("executing", {"kind": "worker"}, 0),
        "done": ("complete", {"kind": "in_process", "replica": "replica-dead"}, 0),
        "waiting": ("waiting_human", {"kind": "in_process", "replica": "replica-dead"}, 0),
        "no_runner": ("planning", None, 0),
    }
    ids = {name: uuid.uuid4().hex for name in goals}
    async with db() as s, s.begin():
        await s.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:t, 'T', :e)"),
            {"t": tid, "e": f"{tid}@example.test"},
        )
        for name, (status, runner, age_days) in goals.items():
            ctx = {_RUNNER_KEY: runner} if runner else {}
            await s.execute(
                text(
                    "INSERT INTO goals (id, tenant_id, goal_text, status, priority, "
                    "autonomy_mode, workflow_mode, execution_context, dry_run, iterations, "
                    "created_at) VALUES (:g, :t, :txt, :st, 'normal', 'supervised', "
                    "'single_agent', CAST(:ctx AS jsonb), false, 0, "
                    "now() - make_interval(days => :age))"
                ),
                {"g": ids[name], "t": tid, "txt": name, "st": status,
                 "ctx": json.dumps(ctx), "age": age_days},
            )

    svc = GoalService(db_session_factory=db)
    svc._replica_id = "replica-me"
    found = {r.goal_id for r in await svc._db_orphan_candidates() if r.tenant_id == tid}
    assert found == {ids["old_orphan"]}
