"""Integration test: ``OrgService.finalize_mission`` is safe under concurrent calls.

``finalize_mission`` is invoked from more than one call site for the very same
mission: the worker's automatic ``_finalize_owning_mission`` hook
(``app/scaling/tasks.py``, fired from ``mark_worker_complete``/
``mark_worker_failed`` when the dispatched goal goes terminal) and the manual
``POST /{org_id}/missions/{mission_id}/finalize`` endpoint
(``app/org/router.py::org_finalize_mission``), whose own docstring claims it is
"idempotent-ish: safe to call repeatedly". Nothing serializes those two call
sites, and prior to this fix ``finalize_mission`` never checked whether the
mission had *already* been finalized -- it only branched on the dispatched
goal's terminal status. So two overlapping calls (worker callback racing a
user/UI-triggered manual finalize, or a retried Celery delivery) each read the
mission row with a plain ``SELECT`` (no locking), both observe it still
``active`` with a terminal goal, and both run the *entire* finalization body:
appending a duplicate aggregated report onto ``mission.outputs``, and calling
``update_mission_status`` a second time, which unconditionally re-emits
``mission.completed`` (a duplicate SSE/webhook notification) regardless of
whether the status transition actually happened.

The fix adds ``SELECT ... FOR UPDATE`` on the mission row plus an early return
once the mission is already in a terminal status, so the loser of the race
blocks until the winner commits, observes the now-terminal status, and returns
a no-op ``already_finalized`` result instead of redoing the finalization.

This test drives two genuinely concurrent ``finalize_mission`` calls -- each
on its own DB session/transaction, via ``asyncio.gather`` -- against a mission
whose linked goal already reports a terminal status, and asserts the
finalization side effects happened exactly once: one ``mission.outputs``
report, one ``mission.completed`` event, and only one of the two calls doing
"real" work.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/org/test_finalize_mission_idempotency.py -q -m integration
"""

from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import types
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.db.rls import sqlalchemy_rls_context
from app.org.service import OrgService

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "test_app"
GRANT_TABLES = ("organizations", "org_missions", "org_events", "org_tasks")


@pytest.fixture(scope="function")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        admin_url = postgres.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": admin_url},
            check=True,
            capture_output=True,
            text=True,
        )
        yield admin_url


def _app_url(admin_url: str, password: str) -> str:
    return (
        make_url(admin_url)
        .set(username=APP_ROLE, password=password)
        .render_as_string(hide_password=False)
    )


@pytest_asyncio.fixture(scope="function")
async def factories(postgres_url: str) -> AsyncIterator[tuple]:
    """Return (admin_factory, app_factory) for integration tests."""
    password = secrets.token_urlsafe(24)
    admin_engine = create_async_engine(postgres_url)
    async with admin_engine.begin() as conn:
        quoted = (
            await conn.execute(text("SELECT quote_literal(:p)"), {"p": password})
        ).scalar_one()
        await conn.execute(
            text(
                f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD {quoted} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
        )
        await conn.execute(text(f"GRANT CONNECT ON DATABASE test TO {APP_ROLE}"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
        for tbl in GRANT_TABLES:
            await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO {APP_ROLE}"))

    admin_factory = async_sessionmaker(
        create_async_engine(postgres_url, echo=False), expire_on_commit=False
    )
    app_url = _app_url(postgres_url, password)
    # Two connections in the pool: the whole point is to run two concurrent,
    # independent transactions against the same mission row.
    app_engine = create_async_engine(app_url, pool_size=4, max_overflow=0, echo=False)
    app_factory = async_sessionmaker(app_engine, expire_on_commit=False)
    yield (admin_factory, app_factory)
    await app_engine.dispose()
    await admin_engine.dispose()


async def _seed_org(admin_factory, app_factory) -> dict:
    """Insert an organizations row (admin bypasses RLS) and return ids."""
    ids = {
        "org_id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
    }
    async with admin_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO organizations (id, tenant_id, name, slug) "
                "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), :name, :slug)"
            ),
            {
                "id": ids["org_id"],
                "tid": ids["tenant_id"],
                "name": "Test Org",
                "slug": f"test-org-{uuid.uuid4().hex[:8]}",
            },
        )
    return {**ids, "app_factory": app_factory, "admin_factory": admin_factory}


class _FakeGoalService:
    """Stands in for the wired GoalService: the goal is already terminal."""

    def __init__(self, status: str = "completed") -> None:
        self.status = status

    async def get_goal(self, goal_id: str, tenant_ctx: Any) -> dict:
        return {
            "status": self.status,
            "result_artifact": {"summary": f"deliverable for {goal_id}"},
        }


async def _finalize(app_factory, tenant_id: str, mission_id: str) -> dict:
    async with app_factory() as session, session.begin():
        async with sqlalchemy_rls_context(session, tenant_id):
            svc = OrgService(session=session, tenant_id=tenant_id)
            return await svc.finalize_mission(
                mission_id,
                app_state=types.SimpleNamespace(goal_service=_FakeGoalService()),
                tenant_ctx=None,
            )


@pytest.mark.asyncio
async def test_concurrent_finalize_mission_is_not_double_applied(factories: tuple) -> None:
    admin_factory, app_factory = factories
    seeded = await _seed_org(admin_factory, app_factory)
    tenant_id = seeded["tenant_id"]
    mission_id = str(uuid.uuid4())

    async with admin_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO org_missions "
                "(id, tenant_id, org_id, title, objective, status, priority, "
                " source, extra_data) "
                "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), CAST(:oid AS uuid), "
                "        :title, :objective, 'active', 'medium', 'manual', "
                "        CAST(:extra AS jsonb))"
            ),
            {
                "id": mission_id,
                "tid": tenant_id,
                "oid": seeded["org_id"],
                "title": "Ship the Q3 report",
                "objective": "Compile and publish the Q3 report",
                "extra": '{"goal_id": "goal-xyz"}',
            },
        )

    try:
        # Two genuinely concurrent finalize_mission calls, each its own
        # session/transaction -- simulating the worker's automatic finalize
        # hook racing the manual finalize endpoint (or two racing callers of
        # either) for the same mission.
        results = await asyncio.gather(
            _finalize(app_factory, tenant_id, mission_id),
            _finalize(app_factory, tenant_id, mission_id),
        )

        # Both calls report the mission finalized (as completed)...
        assert all(r["finalized"] is True for r in results)
        assert all(r["status"] == "completed" for r in results)
        # ...but exactly one of them actually did the finalization work; the
        # other must have observed it was already done and been a no-op.
        already_finalized_flags = sorted(bool(r.get("already_finalized")) for r in results)
        assert already_finalized_flags == [False, True], (
            "expected exactly one real finalize and one already-finalized "
            f"no-op, got: {results}"
        )

        async with admin_factory() as s:
            mission_row = (
                await s.execute(
                    text(
                        "SELECT status, jsonb_array_length(outputs) AS n_outputs "
                        "FROM org_missions WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": mission_id},
                )
            ).mappings().one()
            completed_events = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM org_events "
                        "WHERE entity_id = :mid AND event_type = 'mission.completed'"
                    ),
                    {"mid": mission_id},
                )
            ).scalar_one()
            progress_100_events = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM org_events "
                        "WHERE entity_id = :mid AND event_type = 'mission.progress' "
                        "AND payload->>'progress' = '1.0'"
                    ),
                    {"mid": mission_id},
                )
            ).scalar_one()

        assert mission_row["status"] == "completed"
        # The core regression: without the fix this is 2 (one duplicate
        # report appended per racing call) instead of 1.
        assert mission_row["n_outputs"] == 1
        assert completed_events == 1
        assert progress_100_events == 1
    finally:
        async with admin_factory() as s, s.begin():
            await s.execute(
                text("DELETE FROM org_events WHERE org_id = CAST(:id AS uuid)"),
                {"id": seeded["org_id"]},
            )
            await s.execute(
                text("DELETE FROM org_missions WHERE org_id = CAST(:id AS uuid)"),
                {"id": seeded["org_id"]},
            )
            await s.execute(
                text("DELETE FROM organizations WHERE id = CAST(:id AS uuid)"),
                {"id": seeded["org_id"]},
            )
