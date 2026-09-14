"""Integration test: ``OrgService.record_decision`` persists metadata.

Pins that ``record_decision``'s ``metadata=`` kwarg is routed to the real
``org_decisions.extra_data`` JSONB column -- ``OrgDecision`` has no
``metadata`` column (that name is the SQLAlchemy declarative
``Base.metadata`` registry), so passing ``metadata=`` straight into the
constructor silently set a transient, never-persisted shadow attribute and
decision metadata was lost on reload. Mirrors the same bug-class fix already
applied to ``create_task`` (see the ``NOTE`` comments in
``app/org/service.py``), and mirrors the fixture/setup in
``tests/org/test_collaboration_persistence.py``. Exercises the real Postgres
schema/migrations (not a fake session), so this catches column/type
mismatches a unit test with a fake recorder cannot.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/org/test_decision_metadata.py -q -m integration
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
from app.org.service import OrgService

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "test_app"
GRANT_TABLES = ("organizations", "org_decisions")


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


@pytest.mark.asyncio
async def test_record_decision_persists_metadata_to_extra_data(factories: tuple) -> None:
    """metadata= passed to record_decision must round-trip via extra_data --
    OrgDecision has no 'metadata' column (that name collides with
    SQLAlchemy's declarative Base.metadata registry); passing it straight
    into the constructor silently dropped it on reload."""
    admin_factory, app_factory = factories
    seeded = await _seed_org(admin_factory, app_factory)
    metadata = {
        "confidence": 0.87,
        "model": "claude-opus",
        "reasoning_trace_id": "trace-123",
    }
    try:
        async with app_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, seeded["tenant_id"]):
                svc = OrgService(session=session, tenant_id=seeded["tenant_id"])
                decision = await svc.record_decision(
                    org_id=seeded["org_id"],
                    entity_type="agent",
                    entity_id="agent-a",
                    decision_type="tool_call_approved",
                    description="Approved a tool call",
                    why="because reasons",
                    metadata=metadata,
                )
                decision_id = str(decision.id)

        # Fetch back the row fresh (new session, no identity-map reuse) to
        # prove the value was actually persisted to the DB, not just held on
        # the in-memory ORM instance.
        async with app_factory() as session:
            async with sqlalchemy_rls_context(session, seeded["tenant_id"]):
                row = (
                    await session.execute(
                        text("SELECT extra_data FROM org_decisions WHERE id = CAST(:id AS uuid)"),
                        {"id": decision_id},
                    )
                ).scalar_one()

        assert row == metadata
    finally:
        async with admin_factory() as s, s.begin():
            await s.execute(
                text("DELETE FROM org_decisions WHERE org_id = CAST(:id AS uuid)"),
                {"id": seeded["org_id"]},
            )
            await s.execute(
                text("DELETE FROM organizations WHERE id = CAST(:id AS uuid)"),
                {"id": seeded["org_id"]},
            )


@pytest.mark.asyncio
async def test_record_decision_defaults_extra_data_to_empty_dict(factories: tuple) -> None:
    """Omitting metadata= must not error and must persist an empty dict,
    matching the column's default (mirrors create_task's default)."""
    admin_factory, app_factory = factories
    seeded = await _seed_org(admin_factory, app_factory)
    try:
        async with app_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, seeded["tenant_id"]):
                svc = OrgService(session=session, tenant_id=seeded["tenant_id"])
                decision = await svc.record_decision(
                    org_id=seeded["org_id"],
                    entity_type="agent",
                    entity_id="agent-b",
                    decision_type="tool_call_approved",
                )
                decision_id = str(decision.id)

        async with app_factory() as session:
            async with sqlalchemy_rls_context(session, seeded["tenant_id"]):
                row = (
                    await session.execute(
                        text("SELECT extra_data FROM org_decisions WHERE id = CAST(:id AS uuid)"),
                        {"id": decision_id},
                    )
                ).scalar_one()

        assert row == {}
    finally:
        async with admin_factory() as s, s.begin():
            await s.execute(
                text("DELETE FROM org_decisions WHERE org_id = CAST(:id AS uuid)"),
                {"id": seeded["org_id"]},
            )
            await s.execute(
                text("DELETE FROM organizations WHERE id = CAST(:id AS uuid)"),
                {"id": seeded["org_id"]},
            )
