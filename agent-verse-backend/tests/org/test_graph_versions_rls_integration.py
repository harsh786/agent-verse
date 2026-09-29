"""Integration: org_graph_versions under FORCE'd RLS and a NOBYPASSRLS role.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/org/test_graph_versions_rls_integration.py -q -m integration --no-cov
"""

from __future__ import annotations

import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.db.rls import sqlalchemy_rls_context
from app.org.service import OrgService

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "test_app_org_graph_versions"
_PASSWORD = secrets.token_urlsafe(24)


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


@pytest_asyncio.fixture(scope="function")
async def factories(postgres_url: str) -> AsyncIterator[tuple[Any, Any]]:
    admin_engine = create_async_engine(postgres_url, poolclass=NullPool)
    async with admin_engine.begin() as conn:
        exists = (
            await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE})
        ).scalar()
        if not exists:
            quoted = (
                await conn.execute(text("SELECT quote_literal(:p)"), {"p": _PASSWORD})
            ).scalar_one()
            await conn.execute(
                text(
                    f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD {quoted} "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
                )
            )
            await conn.execute(text(f"GRANT CONNECT ON DATABASE test TO {APP_ROLE}"))
            await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
            for tbl in ("organizations", "org_graph_versions"):
                await conn.execute(
                    text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO {APP_ROLE}")
                )
    app_url = (
        make_url(postgres_url)
        .set(username=APP_ROLE, password=_PASSWORD)
        .render_as_string(hide_password=False)
    )
    app_engine = create_async_engine(app_url, poolclass=NullPool)
    yield (
        async_sessionmaker(admin_engine, expire_on_commit=False),
        async_sessionmaker(app_engine, expire_on_commit=False),
    )
    await app_engine.dispose()
    await admin_engine.dispose()


async def _seed_org(admin: Any, tenant: str) -> str:
    org_id = str(uuid.uuid4())
    async with admin() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO organizations (id, tenant_id, name, slug) "
                "VALUES (:id, :tid, 'Org', :slug)"
            ),
            {"id": org_id, "tid": tenant, "slug": f"org-{org_id[:8]}"},
        )
    return org_id


@pytest.mark.asyncio
async def test_table_enables_and_forces_rls(factories: tuple[Any, Any]) -> None:
    admin, _app = factories
    async with admin() as s:
        row = (
            await s.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname = 'org_graph_versions'"
                )
            )
        ).one()
    assert row == (True, True)


@pytest.mark.asyncio
async def test_history_is_isolated_per_tenant(factories: tuple[Any, Any]) -> None:
    admin, app = factories
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    org_a = await _seed_org(admin, tenant_a)

    for _ in range(2):
        async with app() as s, s.begin(), sqlalchemy_rls_context(s, tenant_a):
            await OrgService(session=s, tenant_id=tenant_a).save_graph_version(
                org_a, snapshot={"node_ids": ["n"]}, changed_by="api"
            )

    async with app() as s, s.begin(), sqlalchemy_rls_context(s, tenant_a):
        svc = OrgService(session=s, tenant_id=tenant_a)
        history = await svc.list_graph_versions(org_a)
    assert [h["version_num"] for h in history] == [2, 1]

    # Tenant B: cannot see the org, nor its history, even by id.
    async with app() as s, s.begin(), sqlalchemy_rls_context(s, tenant_b):
        svc_b = OrgService(session=s, tenant_id=tenant_b)
        assert await svc_b.get_organization(org_a) is None
        assert await svc_b.list_graph_versions(org_a) == []
        raw = (await s.execute(text("SELECT count(*) FROM org_graph_versions"))).scalar()
    assert raw == 0

    # And cannot write a row claiming tenant A.
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app() as s, s.begin(), sqlalchemy_rls_context(s, tenant_b):
            await s.execute(
                text(
                    "INSERT INTO org_graph_versions "
                    "(id, tenant_id, org_id, version_num, content_hash) "
                    "VALUES (:id, :tid, :org, 99, 'x')"
                ),
                {"id": str(uuid.uuid4()), "tid": tenant_a, "org": org_a},
            )
