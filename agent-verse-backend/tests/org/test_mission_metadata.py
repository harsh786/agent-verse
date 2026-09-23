"""Integration test: ``OrgService.create_mission`` persists metadata.

Pins that ``create_mission``'s ``metadata=`` kwarg is routed to the real
``org_missions.extra_data`` JSONB column -- ``OrgMission`` has no ``metadata``
column (that name collides with the SQLAlchemy declarative ``Base.metadata``
registry every mapped class inherits), so passing ``metadata=`` straight into
the constructor silently sets a transient, never-persisted shadow attribute
and mission metadata was lost on reload. This is the same bug class already
fixed for ``create_task`` (``OrgTask``) and ``record_decision``
(``OrgDecision``) -- see the ``NOTE`` comments in ``app/org/service.py`` -- but
was missed in ``create_mission`` itself, even though
``org_create_mission_execute`` (``app/org/router.py``) passes
``metadata=body.metadata`` expecting it to be saved.

It additionally regresses a second, related bug this silently caused: any code
reading a *real* ``OrgMission`` row's ``.metadata`` attribute (as opposed to a
test double built with ``MagicMock(metadata=...)``) gets back the inherited
SQLAlchemy ``MetaData`` registry object, not a dict -- see
``app/gateway/mcp_server/__init__.py``'s ``_tool_list_missions`` /
``_tool_get_mission_result``, which called ``.get(...)`` on it and crashed
with ``AttributeError`` on every invocation (swallowed by ``call_tool``'s
broad ``except Exception``). Those two MCP tools now read ``.extra_data``
instead; ``tests/gateway/test_mcp_server.py`` covers that half with a real
``OrgMission`` instance instead of a masking ``MagicMock``.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/org/test_mission_metadata.py -q -m integration
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
GRANT_TABLES = ("organizations", "org_missions", "org_events")


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
async def test_create_mission_persists_metadata_to_extra_data(factories: tuple) -> None:
    """metadata= passed to create_mission must round-trip via extra_data --
    OrgMission has no 'metadata' column (that name collides with SQLAlchemy's
    declarative Base.metadata registry); passing it straight into the
    constructor silently dropped it on reload."""
    admin_factory, app_factory = factories
    seeded = await _seed_org(admin_factory, app_factory)
    metadata = {
        "requested_by": "harsh.kumar@toucanus.com",
        "source_channel": "slack",
        "goal_id": "goal-abc-123",
    }
    try:
        async with app_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, seeded["tenant_id"]):
                svc = OrgService(session=session, tenant_id=seeded["tenant_id"])
                mission = await svc.create_mission(
                    org_id=seeded["org_id"],
                    title="Ship the Q3 report",
                    objective="Compile and publish the Q3 report",
                    metadata=metadata,
                )
                mission_id = str(mission.id)

        # Fetch back the row fresh (new session, no identity-map reuse) to
        # prove the value was actually persisted to the DB, not just held on
        # the in-memory ORM instance.
        async with app_factory() as session:
            async with sqlalchemy_rls_context(session, seeded["tenant_id"]):
                row = (
                    await session.execute(
                        text("SELECT extra_data FROM org_missions WHERE id = CAST(:id AS uuid)"),
                        {"id": mission_id},
                    )
                ).scalar_one()

        assert row == metadata
    finally:
        async with admin_factory() as s, s.begin():
            await s.execute(
                text("DELETE FROM org_missions WHERE org_id = CAST(:id AS uuid)"),
                {"id": seeded["org_id"]},
            )
            await s.execute(
                text("DELETE FROM organizations WHERE id = CAST(:id AS uuid)"),
                {"id": seeded["org_id"]},
            )


@pytest.mark.asyncio
async def test_create_mission_defaults_extra_data_to_empty_dict(factories: tuple) -> None:
    """Omitting metadata= must not error and must persist an empty dict."""
    admin_factory, app_factory = factories
    seeded = await _seed_org(admin_factory, app_factory)
    try:
        async with app_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, seeded["tenant_id"]):
                svc = OrgService(session=session, tenant_id=seeded["tenant_id"])
                mission = await svc.create_mission(
                    org_id=seeded["org_id"],
                    title="Manual mission",
                )
                mission_id = str(mission.id)

        async with app_factory() as session:
            async with sqlalchemy_rls_context(session, seeded["tenant_id"]):
                row = (
                    await session.execute(
                        text("SELECT extra_data FROM org_missions WHERE id = CAST(:id AS uuid)"),
                        {"id": mission_id},
                    )
                ).scalar_one()

        assert row == {}
    finally:
        async with admin_factory() as s, s.begin():
            await s.execute(
                text("DELETE FROM org_missions WHERE org_id = CAST(:id AS uuid)"),
                {"id": seeded["org_id"]},
            )
            await s.execute(
                text("DELETE FROM organizations WHERE id = CAST(:id AS uuid)"),
                {"id": seeded["org_id"]},
            )
