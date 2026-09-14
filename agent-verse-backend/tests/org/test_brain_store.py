"""Real-Postgres coverage for BrainDecisionStore (Task 3, autonomous org brain).

Exercises the store against the migration-0128 ``org_brain_decisions`` table
through an RLS-enforcing (non-superuser, non-owner) role so tenant isolation
is proven at the DB layer, mirroring ``tests/workflow/test_run_store.py``.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/org/test_brain_store.py -q -m integration
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

from app.db.rls import sqlalchemy_rls_context
from app.org.brain_store import BrainDecisionStore

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "org_brain_app"
GRANT_TABLES = ("organizations", "org_brain_decisions")


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
async def seeded_org(factories: tuple) -> AsyncIterator[dict]:
    """Insert an organizations row for tenant A (admin bypasses RLS)."""
    admin_factory, app_factory = factories
    ids = {
        "org_id": str(uuid.uuid4()),
        "tenant_a": str(uuid.uuid4()),
        "tenant_b": str(uuid.uuid4()),
    }
    async with admin_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO organizations (id, tenant_id, name, slug) "
                "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), :name, :slug)"
            ),
            {
                "id": ids["org_id"],
                "tid": ids["tenant_a"],
                "name": "Test Org",
                "slug": f"test-org-{uuid.uuid4().hex[:8]}",
            },
        )
    yield {**ids, "app_factory": app_factory}
    async with admin_factory() as s, s.begin():
        await s.execute(
            text("DELETE FROM org_brain_decisions WHERE org_id = CAST(:id AS uuid)"),
            {"id": ids["org_id"]},
        )
        await s.execute(
            text("DELETE FROM organizations WHERE id = CAST(:id AS uuid)"),
            {"id": ids["org_id"]},
        )


async def test_record_and_list_roundtrip(seeded_org: dict) -> None:
    app_factory = seeded_org["app_factory"]
    async with app_factory() as session, session.begin():
        async with sqlalchemy_rls_context(session, seeded_org["tenant_a"]):
            store = BrainDecisionStore(session)
            did = await store.record(
                org_id=seeded_org["org_id"],
                tenant_id=seeded_org["tenant_a"],
                tick_id="tick-1",
                kind="proactive",
                rationale="advance goal g1",
                target_goal="g1",
                action="proposed",
                guardrail_verdict="propose",
                reason="within policy",
                est_cost_usd=5.0,
                mission_id=None,
            )
            assert did

            rows = await store.list(seeded_org["org_id"], seeded_org["tenant_a"], limit=10)

    assert rows and rows[0]["kind"] == "proactive"
    assert rows[0]["action"] == "proposed"
    assert rows[0]["target_goal"] == "g1"
    assert rows[0]["guardrail_verdict"] == "propose"
    assert rows[0]["est_cost_usd"] == 5.0
    assert rows[0]["mission_id"] is None
    assert str(rows[0]["id"]) == did


async def test_cross_tenant_rls_isolation(seeded_org: dict) -> None:
    """A decision recorded under tenant A must not be visible under tenant B."""
    app_factory = seeded_org["app_factory"]
    async with app_factory() as session, session.begin():
        async with sqlalchemy_rls_context(session, seeded_org["tenant_a"]):
            store = BrainDecisionStore(session)
            await store.record(
                org_id=seeded_org["org_id"],
                tenant_id=seeded_org["tenant_a"],
                tick_id="tick-2",
                kind="reactive",
                rationale="respond to alert",
                target_goal="g2",
                action="executed",
                guardrail_verdict="execute",
                reason="autonomy level sufficient",
                est_cost_usd=1.5,
            )

    # Tenant A sees its own decisions.
    async with app_factory() as session_a, session_a.begin():
        async with sqlalchemy_rls_context(session_a, seeded_org["tenant_a"]):
            rows_a = await BrainDecisionStore(session_a).list(
                seeded_org["org_id"], seeded_org["tenant_a"], limit=10
            )
    assert any(r["tick_id"] == "tick-2" for r in rows_a)

    # Tenant B, even asking for the SAME org_id, sees none of tenant A's rows
    # (RLS filters by the app.tenant_id GUC regardless of the org_id argument).
    async with app_factory() as session_b, session_b.begin():
        async with sqlalchemy_rls_context(session_b, seeded_org["tenant_b"]):
            rows_b = await BrainDecisionStore(session_b).list(
                seeded_org["org_id"], seeded_org["tenant_b"], limit=10
            )
    assert rows_b == []
