"""Integration test: ComplianceController.execute_data_deletion_async actually
deletes rows under a real, least-privilege (non-BYPASSRLS) DB role.

``execute_data_deletion_async`` (the actual GDPR erasure executed 30 days
after a deletion request) iterates ~26 tables and issues a raw
``DELETE FROM {table} WHERE tenant_id = :tid`` for each, inside
``async with db() as session, session.begin():`` -- with NO
``sqlalchemy_rls_context``/``rls_context`` wrapping at all. Several of those
tables (``decision_traces``, ``tool_capabilities``, ``compliance_requests``,
``agent_snapshots``, ...) have ``FORCE ROW LEVEL SECURITY``. Under any
DB role without BYPASSRLS (the least-privilege role this app actually
provisions in production), Postgres evaluates each such table's
`tenant_id = current_setting('app.tenant_id', TRUE)` policy with the GUC
unset (empty string), which never equals a real tenant_id -- so every DELETE
against a FORCE-RLS table silently matches zero rows. The function still
reports "success" (a positive rowcount for the non-RLS'd tables, `0` folded
silently into the per-table dict for the RLS'd ones), so a GDPR erasure
request can be marked complete while an affected tenant's decision traces,
snapshots, and compliance-request history are never actually deleted.

Superuser/BYPASSRLS roles never hit this (RLS is skipped entirely for them),
which is why it went unnoticed in a dev setup that connects as a superuser.

A SECOND, distinct bug was found while writing this test: the per-table
``try/except`` around each DELETE does not actually isolate failures.
Postgres aborts the *entire enclosing transaction* on any error (a
permission error, a constraint violation, anything) until a ROLLBACK --
without a per-table SAVEPOINT, one table raising an exception poisons every
*subsequent* table's DELETE in the same loop too, each of which then fails
with "current transaction is aborted, commands ignored until end of
transaction block" and gets silently folded into that table's own
``"skipped: ..."`` entry. The resulting ``tables`` dict looks like a set of
independent per-table outcomes, but a single early failure actually means
*nothing after it in ``tables_ordered`` was deleted*, with no signal
distinguishing that from a genuinely benign per-table skip. Fixed by wrapping
each table's DELETE in its own ``session.begin_nested()`` (SAVEPOINT), so a
failure rolls back only that table's own attempt.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/enterprise/test_gdpr_erasure_rls.py -q -m integration
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

from app.enterprise.compliance import ComplianceController
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "test_app"
# A representative subset of the ~26 tables execute_data_deletion_async
# touches: compliance_requests and agent_snapshots both have FORCE RLS
# (migrations 0026/767fe9d87bfe and 0025/0034), so this exercises the exact
# bug class without needing to seed every FK-dependent table in the list.
GRANT_TABLES = ("compliance_requests", "agent_snapshots")


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
        # deleted_tenants has no RLS but is written by the same function.
        await conn.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON deleted_tenants TO {APP_ROLE}")
        )

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
async def test_gdpr_erasure_deletes_force_rls_rows_despite_earlier_table_failures(
    factories: tuple,
) -> None:
    """Proves both fixes: the RLS GUC is set (so FORCE-RLS tables actually get
    deleted), AND per-table SAVEPOINTs isolate failures (so the many earlier
    tables in ``tables_ordered`` that this test's app role has no grants on --
    each raising "permission denied" -- don't poison the transaction and
    silently prevent compliance_requests/agent_snapshots from being deleted
    later in the same loop)."""
    admin_factory, app_factory = factories
    tenant_id = f"tenant-{secrets.token_hex(6)}"
    request_id = uuid.uuid4().hex
    snapshot_id = uuid.uuid4().hex

    # Seed a row in each FORCE-RLS table via the admin (RLS-bypassing)
    # connection -- this test is only about execute_data_deletion_async's own
    # missing RLS-context wrapping, not about how these rows got there.
    async with admin_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO compliance_requests (request_id, tenant_id, status) "
                "VALUES (:rid, :tid, 'pending')"
            ),
            {"rid": request_id, "tid": tenant_id},
        )
        await s.execute(
            text(
                "INSERT INTO agent_snapshots (id, tenant_id, agent_id, snapshot) "
                "VALUES (:sid, :tid, :aid, '{}'::jsonb)"
            ),
            {"sid": snapshot_id, "tid": tenant_id, "aid": uuid.uuid4().hex},
        )

    try:
        # Sanity check: both rows are really there before erasure runs.
        async with admin_factory() as s:
            before = (
                await s.execute(
                    text("SELECT count(*) FROM compliance_requests WHERE tenant_id = :tid"),
                    {"tid": tenant_id},
                )
            ).scalar_one()
        assert before == 1

        controller = ComplianceController()
        tenant_ctx = TenantContext(
            tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="gdpr-erasure-test"
        )
        result = await controller.execute_data_deletion_async(
            tenant_ctx=tenant_ctx, db=app_factory
        )

        assert result["deleted_at"]
        assert result["tenant_id"] == tenant_id

        # The real, load-bearing assertion: prove the DELETE actually reached
        # Postgres and removed the row, rather than the policy silently
        # matching zero rows under the non-BYPASSRLS role.
        async with admin_factory() as s:
            remaining_requests = (
                await s.execute(
                    text("SELECT count(*) FROM compliance_requests WHERE tenant_id = :tid"),
                    {"tid": tenant_id},
                )
            ).scalar_one()
            remaining_snapshots = (
                await s.execute(
                    text("SELECT count(*) FROM agent_snapshots WHERE tenant_id = :tid"),
                    {"tid": tenant_id},
                )
            ).scalar_one()
        assert remaining_requests == 0, (
            "compliance_requests row survived GDPR erasure -- the DELETE "
            "silently matched zero rows under the non-BYPASSRLS role because "
            "execute_data_deletion_async never set the app.tenant_id RLS GUC"
        )
        assert remaining_snapshots == 0, (
            "agent_snapshots row survived GDPR erasure for the same reason"
        )
        assert result["tables"]["compliance_requests"] == 1
        assert result["tables"]["agent_snapshots"] == 1
    finally:
        async with admin_factory() as s, s.begin():
            await s.execute(
                text("DELETE FROM compliance_requests WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            )
            await s.execute(
                text("DELETE FROM agent_snapshots WHERE tenant_id = :tid"), {"tid": tenant_id}
            )
            await s.execute(
                text("DELETE FROM deleted_tenants WHERE tenant_id = :tid"), {"tid": tenant_id}
            )
