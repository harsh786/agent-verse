"""Real-Postgres coverage for PostgresWorkflowRunStore (WT-6).

Exercises the store against the migration-0108 tables through an RLS-enforcing
(non-superuser, non-owner) role so tenant isolation is proven at the DB layer.

Run with:
    DOCKER_HOST=... TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/workflow/test_run_store.py -q
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.workflow.run_store import PostgresWorkflowRunStore
from app.workflow.state import StepStatus, WorkflowRunStatus

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "workflow_app"
GRANT_TABLES = (
    "workflow_definitions",
    "workflow_runs",
    "workflow_step_results",
    "workflow_definition_versions",
    "workflow_permissions",
    "workflow_webhook_events",
)

_DEF_DSL = {
    "name": "Nightly Report",
    "version": "1.0.0",
    "steps": [{"id": "s1", "type": "transform"}],
}


@pytest.fixture(scope="module")
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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def factories(postgres_url: str) -> AsyncIterator[tuple]:
    """Return (admin_factory, app_factory). app_factory enforces RLS."""
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

    app_engine = create_async_engine(_app_url(postgres_url, password), pool_size=4, max_overflow=0)
    yield (
        async_sessionmaker(admin_engine, expire_on_commit=False),
        async_sessionmaker(app_engine, expire_on_commit=False),
    )
    await app_engine.dispose()
    await admin_engine.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def seeded(factories: tuple) -> AsyncIterator[dict]:
    """Insert a workflow_definitions row for tenant A (admin bypasses RLS)."""
    admin_factory, app_factory = factories
    ids = {
        "tenant_a": str(uuid.uuid4()),
        "tenant_b": str(uuid.uuid4()),
        "workflow_id": str(uuid.uuid4()),
        "slug": f"wf-{uuid.uuid4().hex[:8]}",
    }
    async with admin_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO workflow_definitions "
                "(id, tenant_id, name, slug, definition_json, status) "
                "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), :name, :slug, "
                " CAST(:dj AS jsonb), 'published')"
            ),
            {
                "id": ids["workflow_id"],
                "tid": ids["tenant_a"],
                "name": "Nightly Report",
                "slug": ids["slug"],
                "dj": json.dumps(_DEF_DSL),
            },
        )
    yield {**ids, "store": PostgresWorkflowRunStore(app_factory)}
    # webhook_events reference both runs and the definition with no ON DELETE
    # CASCADE, so purge them first; runs' step_results cascade via run_id;
    # versions/permissions cascade off the definition.
    async with admin_factory() as s, s.begin():
        await s.execute(
            text("DELETE FROM workflow_webhook_events WHERE workflow_id = CAST(:id AS uuid)"),
            {"id": ids["workflow_id"]},
        )
        await s.execute(
            text("DELETE FROM workflow_runs WHERE workflow_id = CAST(:id AS uuid)"),
            {"id": ids["workflow_id"]},
        )
        await s.execute(
            text("DELETE FROM workflow_definitions WHERE id = CAST(:id AS uuid)"),
            {"id": ids["workflow_id"]},
        )


async def test_create_get_round_trip(seeded: dict) -> None:
    store: PostgresWorkflowRunStore = seeded["store"]
    run_id = str(uuid.uuid4())
    await store.create(
        run_id=run_id,
        workflow_id=seeded["workflow_id"],
        tenant_id=seeded["tenant_a"],
        trigger_type="api",
        inputs={"date": "2026-09-07"},
        labels={"env": "prod"},
    )
    run = await store.get(seeded["tenant_a"], run_id)
    assert run is not None
    assert run["run_id"] == run_id
    assert run["workflow_id"] == seeded["workflow_id"]
    assert run["workflow_name"] == "Nightly Report"  # joined from definitions
    assert run["status"] == "pending"
    assert run["inputs"] == {"date": "2026-09-07"}
    assert run["step_count"] == 0


async def test_update_status_transitions_and_completed_at(seeded: dict) -> None:
    store: PostgresWorkflowRunStore = seeded["store"]
    run_id = str(uuid.uuid4())
    await store.create(
        run_id=run_id, workflow_id=seeded["workflow_id"], tenant_id=seeded["tenant_a"]
    )

    # pending → running stamps started_at, leaves completed_at NULL
    assert await store.update_status(
        run_id, WorkflowRunStatus.RUNNING, tenant_id=seeded["tenant_a"]
    )
    running = await store.get(seeded["tenant_a"], run_id)
    assert running is not None
    assert running["status"] == "running"
    assert running["started_at"] is not None
    assert running["finished_at"] is None

    # running → complete stamps completed_at and a duration
    assert await store.update_status(
        run_id,
        WorkflowRunStatus.COMPLETE,
        tenant_id=seeded["tenant_a"],
        outputs={"ok": True},
    )
    done = await store.get(seeded["tenant_a"], run_id)
    assert done is not None
    assert done["status"] == "complete"
    assert done["finished_at"] is not None
    assert done["duration_ms"] is not None and done["duration_ms"] >= 0
    assert done["outputs"] == {"ok": True}


async def test_record_step_start_finish_queryable(seeded: dict) -> None:
    store: PostgresWorkflowRunStore = seeded["store"]
    run_id = str(uuid.uuid4())
    await store.create(
        run_id=run_id, workflow_id=seeded["workflow_id"], tenant_id=seeded["tenant_a"]
    )

    await store.record_step_start(
        run_id=run_id,
        tenant_id=seeded["tenant_a"],
        step_id="s1",
        step_type="transform",
        step_name="Shape rows",
    )
    await store.record_step_finish(
        run_id=run_id,
        tenant_id=seeded["tenant_a"],
        step_id="s1",
        status=StepStatus.COMPLETE,
        output={"rows": 42},
    )

    steps = await store.list_step_results(seeded["tenant_a"], run_id)
    assert len(steps) == 1
    assert steps[0]["step_id"] == "s1"
    assert steps[0]["status"] == "complete"
    assert steps[0]["output"] == {"rows": 42}
    assert steps[0]["duration_ms"] is not None

    one = await store.get_step_result(seeded["tenant_a"], run_id, "s1")
    assert one is not None and one["step_type"] == "transform"

    # step_count on the run reflects the persisted step
    run = await store.get(seeded["tenant_a"], run_id)
    assert run is not None and run["step_count"] == 1


async def test_get_definition_returns_stored_dsl(seeded: dict) -> None:
    store: PostgresWorkflowRunStore = seeded["store"]
    dsl = await store.get_definition(seeded["workflow_id"], seeded["tenant_a"])
    assert dsl == _DEF_DSL


async def test_cross_tenant_rls_isolation(seeded: dict) -> None:
    """Tenant B must not be able to read tenant A's run."""
    store: PostgresWorkflowRunStore = seeded["store"]
    run_id = str(uuid.uuid4())
    await store.create(
        run_id=run_id, workflow_id=seeded["workflow_id"], tenant_id=seeded["tenant_a"]
    )

    # Tenant A sees it.
    assert await store.get(seeded["tenant_a"], run_id) is not None
    # Tenant B does not (RLS filters the row).
    assert await store.get(seeded["tenant_b"], run_id) is None
    # And it does not leak into tenant B's listing.
    items, _total = await store.list(seeded["tenant_b"])
    assert all(r["run_id"] != run_id for r in items)


# ── 2.W-2: advanced features (versions / permissions / analytics / webhooks) ──


async def test_list_versions_and_get_definition_version(seeded: dict, factories: tuple) -> None:
    admin_factory, _ = factories
    store: PostgresWorkflowRunStore = seeded["store"]
    async with admin_factory() as s, s.begin():
        for ver, summary in (("1.0.0", "init"), ("1.1.0", "tweak")):
            await s.execute(
                text(
                    "INSERT INTO workflow_definition_versions "
                    "(workflow_id, tenant_id, version, definition_yaml, definition_json, "
                    " change_summary) "
                    "VALUES (CAST(:wid AS uuid), CAST(:tid AS uuid), :ver, :yaml, "
                    " CAST(:dj AS jsonb), :sum)"
                ),
                {
                    "wid": seeded["workflow_id"],
                    "tid": seeded["tenant_a"],
                    "ver": ver,
                    "yaml": f"name: v{ver}",
                    "dj": json.dumps({"name": f"v{ver}", "steps": []}),
                    "sum": summary,
                },
            )
    versions = await store.list_versions(seeded["tenant_a"], seeded["workflow_id"])
    assert {v["version"] for v in versions} == {"1.0.0", "1.1.0"}

    snap = await store.get_definition_version(seeded["tenant_a"], seeded["workflow_id"], "1.1.0")
    assert snap is not None
    assert snap["definition_json"] == {"name": "v1.1.0", "steps": []}
    # Unknown version → None.
    assert (
        await store.get_definition_version(seeded["tenant_a"], seeded["workflow_id"], "9") is None
    )
    # RLS: tenant B cannot see tenant A's versions.
    assert await store.list_versions(seeded["tenant_b"], seeded["workflow_id"]) == []


async def test_permissions_crud(seeded: dict) -> None:
    store: PostgresWorkflowRunStore = seeded["store"]
    created = await store.add_permission(
        seeded["tenant_a"],
        seeded["workflow_id"],
        subject_type="user",
        subject_id="user-1",
        permission="editor",
    )
    assert created["subject_id"] == "user-1"
    perms = await store.get_permissions(seeded["tenant_a"], seeded["workflow_id"])
    assert any(p["permission"] == "editor" for p in perms)
    # Idempotent upsert — same (subject, permission) does not duplicate.
    await store.add_permission(
        seeded["tenant_a"],
        seeded["workflow_id"],
        subject_type="user",
        subject_id="user-1",
        permission="editor",
    )
    perms2 = await store.get_permissions(seeded["tenant_a"], seeded["workflow_id"])
    assert sum(1 for p in perms2 if p["subject_id"] == "user-1") == 1
    # Remove it.
    assert await store.remove_permission(seeded["tenant_a"], seeded["workflow_id"], created["id"])
    assert (
        await store.remove_permission(seeded["tenant_a"], seeded["workflow_id"], created["id"])
        is False
    )


async def test_run_stats_computed_from_runs(seeded: dict) -> None:
    store: PostgresWorkflowRunStore = seeded["store"]
    # 2 complete, 1 failed.
    for status in (
        WorkflowRunStatus.COMPLETE,
        WorkflowRunStatus.COMPLETE,
        WorkflowRunStatus.FAILED,
    ):
        rid = str(uuid.uuid4())
        await store.create(
            run_id=rid, workflow_id=seeded["workflow_id"], tenant_id=seeded["tenant_a"]
        )
        await store.update_status(rid, WorkflowRunStatus.RUNNING, tenant_id=seeded["tenant_a"])
        await store.update_status(rid, status, tenant_id=seeded["tenant_a"])

    stats = await store.workflow_run_stats(seeded["tenant_a"], seeded["workflow_id"], days=30)
    assert stats["total"] >= 3
    assert stats["completed"] >= 2
    assert stats["failed"] >= 1
    agg = await store.aggregate_run_stats(seeded["tenant_a"], days=30)
    assert agg["total"] >= 3


async def test_list_webhook_events(seeded: dict, factories: tuple) -> None:
    admin_factory, _ = factories
    store: PostgresWorkflowRunStore = seeded["store"]
    async with admin_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO workflow_webhook_events "
                "(tenant_id, workflow_id, webhook_token, payload, status, attempts) "
                "VALUES (CAST(:tid AS uuid), CAST(:wid AS uuid), :tok, CAST(:pl AS jsonb), "
                " 'completed', 1)"
            ),
            {
                "tid": seeded["tenant_a"],
                "wid": seeded["workflow_id"],
                "tok": "tok-123",
                "pl": json.dumps({"hello": "world"}),
            },
        )
    events, total = await store.list_webhook_events(
        seeded["tenant_a"], seeded["workflow_id"], limit=20, offset=0
    )
    assert total >= 1
    assert any(e["webhook_token"] == "tok-123" for e in events)
    # RLS: tenant B sees none of tenant A's events.
    b_events, b_total = await store.list_webhook_events(
        seeded["tenant_b"], seeded["workflow_id"], limit=20, offset=0
    )
    assert b_total == 0 and b_events == []
