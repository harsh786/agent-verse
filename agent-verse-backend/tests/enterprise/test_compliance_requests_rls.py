"""Integration test: ComplianceController's Postgres persistence actually works
under a real, least-privilege (non-BYPASSRLS) DB role.

``compliance_requests`` has ``FORCE ROW LEVEL SECURITY`` (migration 0026), and
its tenant-isolation policy checks ``current_setting('app.tenant_id', TRUE)``
(fixed from the wrong ``app.current_tenant_id`` key by migration
767fe9d87bfe -- mirroring the identical bug migration 0034 already fixed for
``agent_snapshots``). ``ComplianceController._db_save_request`` /
``_db_load_request`` (the GDPR export-request persistence path used by
``request_data_export`` / ``get_export_status``) never set the
``app.tenant_id`` GUC at all before this fix, so under any DB role without
BYPASSRLS -- the least-privilege role this app actually provisions in
production, and the one built here -- every read/write against
``compliance_requests`` was silently rejected by RLS, caught by a broad
``except Exception``, and fell back to the in-process dict. The class
docstring promises durable Postgres persistence ("Export requests ... are
persisted to PostgreSQL"); under a real non-superuser role that promise was
never kept.

This test proves persistence actually reaches Postgres (not just the
in-memory fallback) by reading the row back with a *second*,
freshly-constructed ``ComplianceController`` sharing no in-memory state with
the one that created it.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/enterprise/test_compliance_requests_rls.py -q -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.enterprise.compliance import ComplianceController
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "test_app"
GRANT_TABLES = ("compliance_requests",)


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
    """Return (admin_factory, app_factory). app_factory's role is
    NOSUPERUSER/NOBYPASSRLS -- the configuration that actually exercises RLS,
    unlike a default superuser dev connection where this bug is invisible."""
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


@pytest.mark.asyncio
async def test_export_request_persists_and_reloads_under_nobypassrls_role(
    factories: tuple,
) -> None:
    admin_factory, app_factory = factories
    tenant_id = f"tenant-{secrets.token_hex(6)}"
    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k1")

    try:
        # First controller: create + persist the export request.
        writer = ComplianceController()
        writer.configure_services(db=app_factory)
        req = await writer.request_data_export(tenant_ctx=ctx)
        assert req.status == "ready"

        # Prove it actually reached Postgres (not just writer's in-memory dict)
        # by reading it back with the admin (RLS-bypassing) connection.
        async with admin_factory() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT tenant_id, status FROM compliance_requests "
                        "WHERE request_id = :rid"
                    ),
                    {"rid": req.request_id},
                )
            ).fetchone()
        assert row is not None, (
            "export request was never persisted to Postgres -- RLS silently "
            "rejected the INSERT under the non-BYPASSRLS role"
        )
        assert row[0] == tenant_id
        assert row[1] == "ready"

        # Second, independent controller with its own empty in-memory cache:
        # get_export_status must load the DB row, proving the *read* path also
        # satisfies RLS (not just falling through to an empty in-memory miss).
        reader = ComplianceController()
        reader.configure_services(db=app_factory)
        loaded = await reader.get_export_status(request_id=req.request_id, tenant_ctx=ctx)
        assert loaded is not None, (
            "get_export_status could not read the request back from Postgres "
            "under the non-BYPASSRLS role"
        )
        assert loaded.status == "ready"
        assert loaded.tenant_id == tenant_id
    finally:
        async with admin_factory() as s, s.begin():
            await s.execute(
                text("DELETE FROM compliance_requests WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            )
