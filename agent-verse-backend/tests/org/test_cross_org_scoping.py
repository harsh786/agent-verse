"""Integration test: mission/task endpoints are scoped to the URL's org, not
just the tenant.

``OrgService.get_mission`` / ``get_task`` filter by ``tenant_id`` only. A
handful of ``app/org/router.py`` endpoints fetched an entity via one of those
lookups and returned/acted on it without also checking that the entity
belongs to the org named in the URL -- so a caller in org A could read or
mutate org B's mission/task (same tenant) simply by putting B's id under A's
URL. This pins the fix: each hardened endpoint now 404s (not 403, to avoid
confirming the id exists in another org) when the entity's ``org_id``
doesn't match the URL's ``org_id``, and still works normally on the correct
org's URL.

Exercises the real Postgres schema/migrations and the real FastAPI router
(not a fake session / mocked service), so this catches the exact
tenant-vs-org filtering bug a unit test with a mocked ``OrgService`` would
hide. Mirrors the app/fixture setup in ``tests/org/test_agent_audit.py`` /
``tests/org/test_collaboration_persistence.py`` for the DB side, and
``tests/org/test_org_router.py`` for the FastAPI test-app side.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/org/test_cross_org_scoping.py -v -m integration
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
GRANT_TABLES = ("organizations", "org_missions", "org_tasks", "org_events")


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


async def _seed_two_orgs_same_tenant(admin_factory) -> dict:
    """Insert two organizations rows under the SAME tenant (admin bypasses RLS)."""
    tenant_id = str(uuid.uuid4())
    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    async with admin_factory() as s, s.begin():
        for org_id, name in ((org_a, "Org A"), (org_b, "Org B")):
            await s.execute(
                text(
                    "INSERT INTO organizations (id, tenant_id, name, slug) "
                    "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), :name, :slug)"
                ),
                {
                    "id": org_id,
                    "tid": tenant_id,
                    "name": name,
                    "slug": f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:8]}",
                },
            )
    return {"tenant_id": tenant_id, "org_a": org_a, "org_b": org_b}


async def _create_mission_in_org_b(app_factory, tenant_id: str, org_b: str) -> str:
    async with app_factory() as session, session.begin():
        async with sqlalchemy_rls_context(session, tenant_id):
            svc = OrgService(session=session, tenant_id=tenant_id)
            mission = await svc.create_mission(
                org_id=org_b,
                title="Org B's confidential mission",
                priority="medium",
            )
            return str(mission.id)


async def _create_task_in_org_b(app_factory, tenant_id: str, org_b: str) -> str:
    async with app_factory() as session, session.begin():
        async with sqlalchemy_rls_context(session, tenant_id):
            svc = OrgService(session=session, tenant_id=tenant_id)
            task = await svc.create_task(
                org_id=org_b,
                title="Org B's confidential task",
            )
            return str(task.id)


def _build_test_app(app_factory, tenant_id: str) -> FastAPI:
    """Minimal FastAPI app with the real org router, a fake tenant middleware
    (auth is out of scope here), and a real DB-backed OrgService via
    app.state.db_session_factory -- exactly what get_org_service expects."""
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


async def _cleanup(admin_factory, org_a: str, org_b: str) -> None:
    async with admin_factory() as s, s.begin():
        for org_id in (org_a, org_b):
            await s.execute(
                text("DELETE FROM org_tasks WHERE org_id = CAST(:id AS uuid)"), {"id": org_id}
            )
            await s.execute(
                text("DELETE FROM org_missions WHERE org_id = CAST(:id AS uuid)"), {"id": org_id}
            )
            await s.execute(
                text("DELETE FROM org_events WHERE org_id = CAST(:id AS uuid)"), {"id": org_id}
            )
            await s.execute(
                text("DELETE FROM organizations WHERE id = CAST(:id AS uuid)"), {"id": org_id}
            )


@pytest.mark.asyncio
async def test_mission_endpoints_scoped_to_org(factories: tuple) -> None:
    """GET/PATCH/status/finalize on a mission via a DIFFERENT org's URL (same
    tenant) must 404 -- and still work via the mission's real org's URL."""
    admin_factory, app_factory = factories
    seeded = await _seed_two_orgs_same_tenant(admin_factory)
    tenant_id, org_a, org_b = seeded["tenant_id"], seeded["org_a"], seeded["org_b"]
    mission_id = await _create_mission_in_org_b(app_factory, tenant_id, org_b)

    try:
        async with _client_for(app_factory, tenant_id) as client:
            # ── Cross-org (org A's URL, org B's mission): every hardened
            # endpoint must 404, never leak org B's data or let org A act on it.
            r = await client.get(f"/v1/org/{org_a}/missions/{mission_id}")
            assert r.status_code == 404, r.text

            r = await client.patch(
                f"/v1/org/{org_a}/missions/{mission_id}", json={"status": "active"}
            )
            assert r.status_code == 404, r.text

            r = await client.post(
                f"/v1/org/{org_a}/missions/{mission_id}/status", json={"status": "active"}
            )
            assert r.status_code == 404, r.text

            r = await client.post(f"/v1/org/{org_a}/missions/{mission_id}/finalize")
            assert r.status_code == 404, r.text

            # ── Same-org (org B's own URL): normal path still works.
            r = await client.get(f"/v1/org/{org_b}/missions/{mission_id}")
            assert r.status_code == 200, r.text
            assert r.json()["id"] == mission_id
            assert r.json()["org_id"] == org_b

            r = await client.patch(
                f"/v1/org/{org_b}/missions/{mission_id}", json={"status": "active"}
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "active"

            r = await client.post(
                f"/v1/org/{org_b}/missions/{mission_id}/status", json={"status": "paused"}
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "paused"

            r = await client.post(f"/v1/org/{org_b}/missions/{mission_id}/finalize")
            assert r.status_code == 200, r.text
    finally:
        await _cleanup(admin_factory, org_a, org_b)


@pytest.mark.asyncio
async def test_task_endpoints_scoped_to_org(factories: tuple) -> None:
    """GET/status/approve/reject on a task via a DIFFERENT org's URL (same
    tenant) must 404 -- and still work via the task's real org's URL."""
    admin_factory, app_factory = factories
    seeded = await _seed_two_orgs_same_tenant(admin_factory)
    tenant_id, org_a, org_b = seeded["tenant_id"], seeded["org_a"], seeded["org_b"]
    task_id = await _create_task_in_org_b(app_factory, tenant_id, org_b)

    try:
        async with _client_for(app_factory, tenant_id) as client:
            # ── Cross-org (org A's URL, org B's task): every hardened
            # endpoint must 404.
            r = await client.get(f"/v1/org/{org_a}/tasks/{task_id}")
            assert r.status_code == 404, r.text

            r = await client.post(
                f"/v1/org/{org_a}/tasks/{task_id}/status", json={"status": "running"}
            )
            assert r.status_code == 404, r.text

            r = await client.post(f"/v1/org/{org_a}/tasks/{task_id}/approve", json={})
            assert r.status_code == 404, r.text

            r = await client.post(f"/v1/org/{org_a}/tasks/{task_id}/reject", json={})
            assert r.status_code == 404, r.text

            # ── Same-org (org B's own URL): normal path still works.
            r = await client.get(f"/v1/org/{org_b}/tasks/{task_id}")
            assert r.status_code == 200, r.text
            assert r.json()["id"] == task_id
            assert r.json()["org_id"] == org_b

            r = await client.post(
                f"/v1/org/{org_b}/tasks/{task_id}/status", json={"status": "running"}
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "running"

            r = await client.post(f"/v1/org/{org_b}/tasks/{task_id}/approve", json={})
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "running"

            r = await client.post(f"/v1/org/{org_b}/tasks/{task_id}/reject", json={})
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "failed"
    finally:
        await _cleanup(admin_factory, org_a, org_b)


@pytest.mark.asyncio
async def test_mission_stream_scoped_to_tenant_and_org(factories: tuple) -> None:
    """GET .../missions/{mission_id}/stream (SSE) must 404 -- before ever
    subscribing to the Redis pub/sub channel -- when the mission doesn't
    belong to the URL's org (same tenant). Regression test for a
    cross-tenant leak: mission_stream previously subscribed to
    ``mission:{mission_id}:events`` with no ownership check at all, so any
    authenticated caller could read any mission's live event stream by id."""
    admin_factory, app_factory = factories
    seeded = await _seed_two_orgs_same_tenant(admin_factory)
    tenant_id, org_a, org_b = seeded["tenant_id"], seeded["org_a"], seeded["org_b"]
    mission_id = await _create_mission_in_org_b(app_factory, tenant_id, org_b)

    try:
        async with _client_for(app_factory, tenant_id) as client:
            # Cross-org (org A's URL, org B's mission): must 404 before streaming.
            r = await client.get(f"/v1/org/{org_a}/missions/{mission_id}/stream")
            assert r.status_code == 404, r.text

            # A mission id that doesn't exist at all must also 404.
            r = await client.get(f"/v1/org/{org_b}/missions/{uuid.uuid4()}/stream")
            assert r.status_code == 404, r.text
    finally:
        await _cleanup(admin_factory, org_a, org_b)
