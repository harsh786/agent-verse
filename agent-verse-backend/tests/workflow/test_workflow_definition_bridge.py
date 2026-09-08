"""Real-Postgres coverage for the workflows → workflow_definitions bridge.

The visual-builder create path (``POST /api/v1/workflows`` → ``_WorkflowStore``)
persists to the legacy ``workflows`` table (Text id). The run engine
(``WorkflowRunner`` + ``PostgresWorkflowRunStore``) is built entirely around
``workflow_definitions`` (uuid id) and ``workflow_runs.workflow_id`` FK-references
it. Nothing populated ``workflow_definitions`` — so triggering an API-created
workflow failed a ``workflow_runs_workflow_id_fkey`` violation.

These tests prove the reconciliation: after ``_WorkflowStore.create`` (DB mode),
the run engine can (a) read the DSL via ``get_definition`` and (b) create a run
row without an FK violation — all through an RLS-enforcing (non-superuser,
NOBYPASSRLS) role, so tenant isolation is proven at the DB layer.

Run with:
    DOCKER_HOST=... TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/workflow/test_workflow_definition_bridge.py -q --no-cov
"""

from __future__ import annotations

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
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.api.workflows import _WorkflowStore
from app.workflow.run_store import PostgresWorkflowRunStore

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "workflow_bridge_app"
GRANT_TABLES = (
    "workflows",
    "workflow_definitions",
    "workflow_runs",
    "workflow_step_results",
)

_DSL = {
    "name": "Bridge WF",
    "version": "1.0.0",
    "steps": [{"id": "s1", "type": "tool"}],
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
async def app_factory(postgres_url: str) -> AsyncIterator[async_sessionmaker]:
    """An async_sessionmaker bound to an RLS-enforcing (NOBYPASSRLS) role."""
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
            await conn.execute(
                text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO {APP_ROLE}")
            )
    await admin_engine.dispose()

    app_engine = create_async_engine(
        _app_url(postgres_url, password), pool_size=4, max_overflow=0
    )
    yield async_sessionmaker(app_engine, expire_on_commit=False)
    await app_engine.dispose()


async def test_create_bridges_workflow_into_workflow_definitions(
    app_factory: async_sessionmaker,
) -> None:
    """A DB-mode create must make the workflow triggerable by the run engine."""
    tenant_a = str(uuid.uuid4())
    store = _WorkflowStore()
    store.set_db(app_factory)
    run_store = PostgresWorkflowRunStore(app_factory)

    wf = await store.create(
        tenant_id=tenant_a,
        name="Bridge WF",
        description="reconciliation",
        definition=_DSL,
    )
    workflow_id = wf["id"]

    # (a) The run engine can now read the DSL from workflow_definitions.
    dsl = await run_store.get_definition(workflow_id, tenant_a)
    assert dsl == _DSL

    # (b) Creating a run no longer violates workflow_runs_workflow_id_fkey.
    run_id = str(uuid.uuid4())
    await run_store.create(
        run_id=run_id,
        workflow_id=workflow_id,
        tenant_id=tenant_a,
        trigger_type="api",
    )
    run = await run_store.get(tenant_a, run_id)
    assert run is not None
    assert run["workflow_id"] == workflow_id
    assert run["workflow_name"] == "Bridge WF"  # joined from workflow_definitions


async def test_bridge_preserves_tenant_isolation(
    app_factory: async_sessionmaker,
) -> None:
    """Tenant B must not see tenant A's bridged workflow definition (RLS)."""
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    store = _WorkflowStore()
    store.set_db(app_factory)
    run_store = PostgresWorkflowRunStore(app_factory)

    wf = await store.create(
        tenant_id=tenant_a, name="Private WF", description="", definition=_DSL
    )
    workflow_id = wf["id"]

    # Legacy table: RLS keeps it out of tenant B's view.
    assert await store.get(tenant_b, workflow_id) is None
    # Bridged definition: tenant B cannot read it either.
    with pytest.raises(KeyError):
        await run_store.get_definition(workflow_id, tenant_b)


async def test_update_mirrors_definition_changes(
    app_factory: async_sessionmaker,
) -> None:
    """Editing the visual builder must keep the run-engine DSL in sync."""
    tenant_a = str(uuid.uuid4())
    store = _WorkflowStore()
    store.set_db(app_factory)
    run_store = PostgresWorkflowRunStore(app_factory)

    wf = await store.create(
        tenant_id=tenant_a, name="Editable WF", description="", definition=_DSL
    )
    workflow_id = wf["id"]

    new_dsl = {"name": "Editable WF", "steps": [{"id": "s2", "type": "llm"}]}
    await store.update(
        tenant_id=tenant_a, workflow_id=workflow_id, definition=new_dsl
    )

    assert await run_store.get_definition(workflow_id, tenant_a) == new_dsl
