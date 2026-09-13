"""Integration test for OrgService.create_mission status support (Task 6, Step 1).

OrgBrain's ACT step needs to create "proposed" missions (autonomy L3 verdict:
propose, awaiting human approval) without marking them active/dispatched. This
pins the new `status` kwarg added to `create_mission` in app/org/service.py —
the signature stays backward compatible (no new required arg) while callers
can now override the initial mission status.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/org/test_create_mission_status.py -q -m integration
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
async def test_create_mission_with_proposed_status(factories: tuple) -> None:
    """Explicit status="proposed" is preserved."""
    admin_factory, app_factory = factories
    seeded = await _seed_org(admin_factory, app_factory)
    try:
        async with app_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, seeded["tenant_id"]):
                svc = OrgService(session=session, tenant_id=seeded["tenant_id"])
                mission = await svc.create_mission(
                    org_id=seeded["org_id"],
                    title="Advance goal g1",
                    objective="advance g1",
                    source="autonomous",
                    status="proposed",
                )
                assert mission.status == "proposed"
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
async def test_create_mission_default_status_is_draft(factories: tuple) -> None:
    """Omitting status yields the ORM column default "draft"."""
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
                assert mission.status == "draft"
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
