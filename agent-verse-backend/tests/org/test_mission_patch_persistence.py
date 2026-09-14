"""Regression test: PATCH /v1/org/{org_id}/missions/{mission_id} (non-status
fields) must actually persist to the mission row.

``update_mission``'s non-status branch used to call
``service.update_organization(mission_id, updates)`` -- passing a MISSION id
to the ORGANIZATION update method. That method looks the id up in the
``organizations`` table (via ``get_organization``, which filters on
``Organization.id``), so it always returned ``None`` for a real mission id,
and the mismatched id/table meant any edit that *did* appear to apply was
hitting the wrong row. Non-status mission edits (title, objective, why,
priority, budget_usd, deadline, assigned_team_id, ...) were silently dropped.

The fix adds ``OrgService.update_mission`` (mirrors ``update_mission_status``
and ``update_organization``'s whitelist-by-attribute pattern) and wires the
router's non-status branch to call it. This test exercises the real Postgres
schema/migrations and the real FastAPI router end-to-end so it would have
caught the mis-routing.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/org/test_mission_patch_persistence.py -v -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.db.rls import sqlalchemy_rls_context
from app.org.router import router as org_router
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


async def _seed_org(admin_factory) -> dict:
    tenant_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    async with admin_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO organizations (id, tenant_id, name, slug) "
                "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), :name, :slug)"
            ),
            {
                "id": org_id,
                "tid": tenant_id,
                "name": "Acme AI Corp",
                "slug": f"acme-{uuid.uuid4().hex[:8]}",
            },
        )
    return {"tenant_id": tenant_id, "org_id": org_id}


async def _create_mission(app_factory, tenant_id: str, org_id: str) -> str:
    async with app_factory() as session, session.begin():
        async with sqlalchemy_rls_context(session, tenant_id):
            svc = OrgService(session=session, tenant_id=tenant_id)
            mission = await svc.create_mission(
                org_id=org_id,
                title="Original title",
                priority="low",
                objective="Original objective",
            )
            return str(mission.id)


def _build_test_app(app_factory, tenant_id: str) -> FastAPI:
    app = FastAPI()
    app.state.db_session_factory = app_factory

    @app.middleware("http")
    async def fake_tenant(request, call_next):
        from app.tenancy.context import PlanTier, TenantContext

        request.state.tenant = TenantContext(
            tenant_id=tenant_id,
            plan=PlanTier.PROFESSIONAL,
            api_key_id="test-key",
            roles=("admin",),
        )
        return await call_next(request)

    app.include_router(org_router)
    return app


@asynccontextmanager
async def _client_for(app_factory, tenant_id: str) -> AsyncIterator[AsyncClient]:
    app = _build_test_app(app_factory, tenant_id)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def _cleanup(admin_factory, org_id: str) -> None:
    async with admin_factory() as s, s.begin():
        await s.execute(
            text("DELETE FROM org_missions WHERE org_id = CAST(:id AS uuid)"), {"id": org_id}
        )
        await s.execute(
            text("DELETE FROM org_events WHERE org_id = CAST(:id AS uuid)"), {"id": org_id}
        )
        await s.execute(text("DELETE FROM organizations WHERE id = CAST(:id AS uuid)"), {"id": org_id})


@pytest.mark.asyncio
async def test_non_status_patch_persists_mission_fields(factories: tuple) -> None:
    """PATCH with title+priority (no status) must persist -- previously
    mis-routed to update_organization, which looked the id up in the
    organizations table and always no-opped for a real mission id."""
    admin_factory, app_factory = factories
    seeded = await _seed_org(admin_factory)
    tenant_id, org_id = seeded["tenant_id"], seeded["org_id"]
    mission_id = await _create_mission(app_factory, tenant_id, org_id)

    try:
        async with _client_for(app_factory, tenant_id) as client:
            r = await client.patch(
                f"/v1/org/{org_id}/missions/{mission_id}",
                json={"title": "Updated title", "priority": "critical"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["title"] == "Updated title"
            assert body["priority"] == "critical"
            # Original fields not part of the patch must be untouched.
            assert body["objective"] == "Original objective"

            # Fetch back independently to confirm it really persisted (not
            # just an unpersisted echo of the request body).
            r2 = await client.get(f"/v1/org/{org_id}/missions/{mission_id}")
            assert r2.status_code == 200, r2.text
            body2 = r2.json()
            assert body2["title"] == "Updated title"
            assert body2["priority"] == "critical"
    finally:
        await _cleanup(admin_factory, org_id)


@pytest.mark.asyncio
async def test_status_patch_still_works(factories: tuple) -> None:
    """The status branch of the same PATCH endpoint must be unaffected by
    the non-status fix."""
    admin_factory, app_factory = factories
    seeded = await _seed_org(admin_factory)
    tenant_id, org_id = seeded["tenant_id"], seeded["org_id"]
    mission_id = await _create_mission(app_factory, tenant_id, org_id)

    try:
        async with _client_for(app_factory, tenant_id) as client:
            r = await client.patch(
                f"/v1/org/{org_id}/missions/{mission_id}",
                json={"status": "active"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "active"

            r2 = await client.get(f"/v1/org/{org_id}/missions/{mission_id}")
            assert r2.status_code == 200, r2.text
            assert r2.json()["status"] == "active"
    finally:
        await _cleanup(admin_factory, org_id)
