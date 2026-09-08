"""Migration 0115 backfills workflow_definitions from legacy workflows rows.

Proves the data-reconciliation half of the fix: workflows created *before* the
store-level bridge existed must become triggerable. The test upgrades to 0114
(pre-bridge), seeds a legacy ``workflows`` row, asserts no mirror row exists
yet, then upgrades to head (running 0115) and asserts the mirror appears — so
the migration itself is shown to do the backfill.

Run with:
    DOCKER_HOST=... TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/workflow/test_workflow_bridge_migration.py -q --no-cov
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

BACKEND_ROOT = Path(__file__).resolve().parents[2]

_DSL = {"name": "Legacy WF", "steps": [{"id": "s1", "type": "tool"}]}


def _alembic(url: str, target: str) -> None:
    subprocess.run(
        ["alembic", "upgrade", target],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def container() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def factory(container: str) -> AsyncIterator[async_sessionmaker]:
    # Upgrade only to 0114 — the state *before* the backfill migration.
    _alembic(container, "0114")
    engine = create_async_engine(container)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_migration_0115_backfills_legacy_workflows(
    container: str, factory: async_sessionmaker
) -> None:
    workflow_id = str(uuid.uuid4())
    tenant_id = str(uuid.uuid4())

    # Seed a legacy workflows row (superuser bypasses RLS).
    async with factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO workflows (id, tenant_id, name, description, definition, status) "
                "VALUES (:id, :tid, :name, '', CAST(:dj AS jsonb), 'draft')"
            ),
            {"id": workflow_id, "tid": tenant_id, "name": "Legacy WF", "dj": json.dumps(_DSL)},
        )

    # Pre-migration: no mirror row exists.
    async with factory() as s:
        pre = (
            await s.execute(
                text("SELECT COUNT(*) FROM workflow_definitions WHERE id = CAST(:id AS uuid)"),
                {"id": workflow_id},
            )
        ).scalar_one()
    assert pre == 0

    # Run the backfill migration.
    _alembic(container, "head")

    # Post-migration: the legacy workflow is now mirrored and triggerable.
    async with factory() as s:
        row = (
            await s.execute(
                text(
                    "SELECT name, definition_json FROM workflow_definitions "
                    "WHERE id = CAST(:id AS uuid)"
                ),
                {"id": workflow_id},
            )
        ).mappings().first()
    assert row is not None
    assert row["name"] == "Legacy WF"
    dj = row["definition_json"]
    assert (json.loads(dj) if isinstance(dj, str) else dj) == _DSL


async def test_migration_0115_restores_rls_force(factory: async_sessionmaker) -> None:
    """After the backfill, RLS + FORCE must be re-enabled on both tables."""
    async with factory() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname IN ('workflows', 'workflow_definitions')"
                )
            )
        ).mappings().all()
    by_name = {r["relname"]: r for r in rows}
    for name in ("workflows", "workflow_definitions"):
        assert by_name[name]["relrowsecurity"] is True, f"{name} RLS disabled"
        assert by_name[name]["relforcerowsecurity"] is True, f"{name} FORCE disabled"
