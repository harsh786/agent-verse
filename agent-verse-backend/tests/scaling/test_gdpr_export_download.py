"""Integration test: the async GDPR export job's download link actually
serves the exported data, end to end, against a real Postgres testcontainer
under a non-BYPASSRLS role.

Root cause (before this fix): ``run_gdpr_export`` (``app/scaling/tasks.py``)
computed ``export_json = json.dumps(export_data, ...)`` and then discarded
it -- never persisting it anywhere. It generated an unrelated
``export_id = uuid.uuid4().hex`` (not the job's own ``job_id``) and pointed
``gdpr_export_jobs.download_url`` at
``/compliance/export/{export_id}/download``. But the actual download
endpoint (``download_export`` in ``app/api/enterprise.py``) resolves via
``ComplianceController.get_export_status()``, which reads the entirely
separate ``compliance_requests`` table by ``request_id`` -- and the async
job's ``export_id`` was never inserted there, so the link 404ed forever and
the exported data was gone.

Additionally, the URL was missing the "/enterprise" prefix that
``download_export`` is actually registered under, and the data-collection
SELECTs against ``goals``/``audit_log`` (both FORCE ROW LEVEL SECURITY) never
set the ``app.tenant_id`` GUC, so under a non-BYPASSRLS role the "export"
would always come back empty even once persisted correctly.

This test proves, against a real non-BYPASSRLS Postgres role:
 1. ``run_gdpr_export`` actually collects non-empty tenant data.
 2. its ``download_url`` resolves to a real ``compliance_requests`` row.
 3. ``ComplianceController.get_export_status`` (what the download endpoint
    calls) returns that row's real payload -- not 404, not empty.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/scaling/test_gdpr_export_download.py -q -m integration
"""

from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "test_app"
GRANT_TABLES = ("compliance_requests", "gdpr_export_jobs", "goals", "audit_log", "tenants")


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
    """Return (admin_factory, app_factory, app_url). app_factory's role is
    NOSUPERUSER/NOBYPASSRLS -- the configuration that actually exercises RLS,
    unlike a default superuser dev connection where this bug is invisible.

    app_url is also returned so a *separate*, freshly-built engine/factory can
    be constructed for code that must run in its own event loop (this task's
    ``run_gdpr_export.run()`` builds its own loop via ``_run_async``) --
    asyncpg connections are bound to the loop that created them, so reusing
    the same pooled engine across loops would break.
    """
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
    yield (admin_factory, app_factory, app_url)
    await app_engine.dispose()
    await admin_engine.dispose()


def _make_task_session_factory(app_url: str) -> Any:
    """Build a brand-new engine/session-factory, deferred until called -- used
    as the patched ``get_session_factory()`` return so ``run_gdpr_export``'s
    own fresh event loop (via ``_run_async``) gets connections bound to
    *that* loop, not the main test loop's pool."""
    engine = create_async_engine(app_url, pool_size=2, max_overflow=0, echo=False)
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_gdpr_export_download_link_serves_real_data_under_nobypassrls_role(
    factories: tuple,
) -> None:
    admin_factory, app_factory, app_url = factories
    tenant_id = f"tenant-{secrets.token_hex(6)}"
    job_id = uuid.uuid4().hex
    goal_id = uuid.uuid4().hex
    goal_text = f"real goal text {secrets.token_hex(4)}"

    # Seed a tenant + a goal (via the admin/RLS-bypassing connection) so the
    # export job has real data to collect.
    async with admin_factory() as s, s.begin():
        await s.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:tid, :name, :email)"),
            {"tid": tenant_id, "name": "GDPR export tenant", "email": f"{tenant_id}@example.com"},
        )
        await s.execute(
            text("INSERT INTO goals (id, tenant_id, goal_text) VALUES (:gid, :tid, :text)"),
            {"gid": goal_id, "tid": tenant_id, "text": goal_text},
        )
        await s.execute(
            text(
                "INSERT INTO gdpr_export_jobs (id, tenant_id, status, created_at) "
                "VALUES (:id, :tid, 'pending', NOW())"
            ),
            {"id": job_id, "tid": tenant_id},
        )

    try:
        # Run the Celery task body under the non-BYPASSRLS role. run_gdpr_export
        # drives its own event loop internally (via _run_async, as it does in a
        # real Celery worker), so it must run off the main test loop's thread --
        # and get its own freshly-built session factory bound to *that* loop
        # (asyncpg connections can't cross event loops).
        from app.scaling.tasks import run_gdpr_export

        with patch(
            "app.db.session.get_session_factory",
            side_effect=lambda: _make_task_session_factory(app_url),
        ):
            result = await asyncio.to_thread(
                run_gdpr_export.run, job_id=job_id, tenant_id=tenant_id
            )

        assert result["status"] == "complete"
        download_url = result["download_url"]
        assert download_url == f"/enterprise/compliance/export/{job_id}/download", (
            "download_url must point at the endpoint that actually serves exports "
            "(app/api/enterprise.py::download_export, registered under /enterprise)"
        )

        # The gdpr_export_jobs row must reflect completion with the same URL.
        async with admin_factory() as s:
            job_row = (
                await s.execute(
                    text("SELECT status, download_url FROM gdpr_export_jobs WHERE id = :id"),
                    {"id": job_id},
                )
            ).fetchone()
        assert job_row is not None
        assert job_row[0] == "complete"
        assert job_row[1] == download_url

        # The download link's id (job_id) must resolve to a real
        # compliance_requests row -- not a 404, not an unrelated uuid nobody
        # ever wrote.
        async with admin_factory() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT tenant_id, status, payload FROM compliance_requests "
                        "WHERE request_id = :rid"
                    ),
                    {"rid": job_id},
                )
            ).fetchone()
        assert row is not None, (
            "the async export's data was never persisted anywhere the download "
            "endpoint can read -- the download link is dead"
        )
        assert row[0] == tenant_id
        assert row[1] == "ready"
        payload = row[2]
        assert payload["goals"], (
            "exported payload has no goals -- the data-collection read never saw "
            "the seeded goal (RLS GUC not set, or persistence bug)"
        )
        assert any(g["text"] == goal_text for g in payload["goals"])

        # Finally, exercise the actual service call the download endpoint makes
        # (ComplianceController.get_export_status), proving a real download
        # request would return this same real content, not 404 / empty.
        from app.enterprise.compliance import ComplianceController
        from app.tenancy.context import PlanTier, TenantContext

        controller = ComplianceController()
        controller.configure_services(db=app_factory)
        ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k1")
        served = await controller.get_export_status(request_id=job_id, tenant_ctx=ctx)
        assert served is not None, "download endpoint would 404 -- export not found"
        assert served.status == "ready"
        assert served.payload["goals"], "download endpoint would serve an empty export"
        assert any(g["text"] == goal_text for g in served.payload["goals"])
    finally:
        async with admin_factory() as s, s.begin():
            await s.execute(
                text("DELETE FROM compliance_requests WHERE tenant_id = :tid"), {"tid": tenant_id}
            )
            await s.execute(
                text("DELETE FROM gdpr_export_jobs WHERE tenant_id = :tid"), {"tid": tenant_id}
            )
            await s.execute(text("DELETE FROM goals WHERE tenant_id = :tid"), {"tid": tenant_id})
            await s.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tenant_id})
