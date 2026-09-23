"""Integration test: decision_traces writes/reads actually work under a real,
least-privilege (non-BYPASSRLS) DB role.

``decision_traces`` has ``FORCE ROW LEVEL SECURITY`` (migration 0027), and its
tenant-isolation policy checked ``current_setting('app.current_tenant_id',
TRUE)`` -- the wrong GUC name. Every application code path that sets tenant
scope for RLS (``app.db.rls.sqlalchemy_rls_context`` / ``rls_context``) sets
``app.tenant_id`` instead (see ``app/agent/graph.py::_persist_decision_trace``
and ``app/api/goals.py``'s decision-traces read endpoint, both reproduced
here verbatim). Migration e79efcca385f fixes the policy to check
``app.tenant_id``, mirroring the identical fix migration 0034 made for
``agent_snapshots`` and 767fe9d87bfe made for ``compliance_requests``.

Impact: under any DB role without BYPASSRLS (the least-privilege role this
app actually provisions in production, and the one built here), the INSERT
in ``_persist_decision_trace`` violated the policy's WITH CHECK (raising /
being swallowed as ``new row violates row-level security policy``) even
though the caller already wrapped the session in the *correct*
``sqlalchemy_rls_context``. Superuser/BYPASSRLS roles never hit this, which
is why it was invisible in a dev setup that connects as a superuser.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/agent/test_decision_traces_rls.py -q -m integration
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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.db.rls import sqlalchemy_rls_context

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "test_app"
GRANT_TABLES = ("decision_traces",)


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


async def _persist_decision_trace(
    session: AsyncSession, *, trace_id: str, goal_id: str, tenant_id: str
) -> None:
    """Verbatim copy of app/agent/graph.py::AgentGraph._persist_decision_trace's
    INSERT (minus the outer try/except that would otherwise swallow the RLS
    failure this test needs to observe)."""
    await session.execute(
        text(
            """INSERT INTO decision_traces
                (id, goal_id, tenant_id, action, reasoning, confidence, created_at)
                VALUES (:id, :gid, :tid, :action, :reasoning, :conf, NOW())
                ON CONFLICT DO NOTHING"""
        ),
        {
            "id": trace_id,
            "gid": goal_id,
            "tid": tenant_id,
            "action": "call_tool:http_request",
            "reasoning": "chose http_request because the step mentions an API call",
            "conf": 0.9,
        },
    )


async def _load_decision_traces(
    session: AsyncSession, *, goal_id: str, tenant_id: str
) -> list:
    """Verbatim copy of the decision-traces SELECT in app/api/goals.py."""
    result = await session.execute(
        text(
            """SELECT id, action, reasoning, confidence, created_at
                FROM decision_traces
                WHERE goal_id = :gid AND tenant_id = :tid
                ORDER BY created_at"""
        ),
        {"gid": goal_id, "tid": tenant_id},
    )
    return result.fetchall()


@pytest.mark.asyncio
async def test_decision_trace_persists_and_reads_back_under_nobypassrls_role(
    factories: tuple,
) -> None:
    admin_factory, app_factory = factories
    tenant_id = f"tenant-{secrets.token_hex(6)}"
    goal_id = uuid.uuid4().hex
    trace_id = uuid.uuid4().hex

    # decision_traces.goal_id has a FK to goals.id (which itself FKs to
    # tenants.id). Seed both via the admin (RLS-bypassing) connection --
    # this test is only about decision_traces' own RLS policy.
    async with admin_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO tenants (id, name, email) VALUES (:tid, :name, :email)"
            ),
            {"tid": tenant_id, "name": "RLS test tenant", "email": f"{tenant_id}@example.com"},
        )
        await s.execute(
            text(
                "INSERT INTO goals (id, tenant_id, goal_text) VALUES (:gid, :tid, :text)"
            ),
            {"gid": goal_id, "tid": tenant_id, "text": "test goal for decision_traces RLS"},
        )

    try:
        # Write path: exactly what AgentGraph._persist_decision_trace does --
        # session wrapped in the *correct* sqlalchemy_rls_context.
        async with (
            app_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            await _persist_decision_trace(
                session, trace_id=trace_id, goal_id=goal_id, tenant_id=tenant_id
            )

        # Prove it actually reached Postgres (not silently rejected by RLS)
        # by reading it back with the admin (RLS-bypassing) connection.
        async with admin_factory() as s:
            row = (
                await s.execute(
                    text("SELECT tenant_id, action FROM decision_traces WHERE id = :tid"),
                    {"tid": trace_id},
                )
            ).fetchone()
        assert row is not None, (
            "decision trace was never persisted to Postgres -- RLS silently "
            "rejected the INSERT under the non-BYPASSRLS role"
        )
        assert row[0] == tenant_id

        # Read path: exactly what the GET /goals/{id}/decision-traces endpoint
        # does -- session wrapped in sqlalchemy_rls_context, must see the row.
        async with (
            app_factory() as session,
            sqlalchemy_rls_context(session, tenant_id),
        ):
            rows = await _load_decision_traces(session, goal_id=goal_id, tenant_id=tenant_id)
        assert len(rows) == 1, (
            "decision trace could not be read back under the non-BYPASSRLS "
            "role even though it was written under the same tenant"
        )
        assert rows[0][0] == trace_id
    finally:
        async with admin_factory() as s, s.begin():
            await s.execute(
                text("DELETE FROM decision_traces WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            )
            await s.execute(text("DELETE FROM goals WHERE tenant_id = :tid"), {"tid": tenant_id})
            await s.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tenant_id})
