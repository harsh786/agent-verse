"""Integration test: GET /goals/{id}/replay's read actually works under a
real, least-privilege (non-BYPASSRLS) DB role.

``app/api/replay.py::replay_goal`` read from ``goals``, ``goal_events``,
``goal_steps``, ``decision_traces`` and ``evaluations`` -- all of which have
``FORCE ROW LEVEL SECURITY`` policies keyed on ``app.tenant_id`` (migrations
0004, 0011, 0027, 0009) -- inside a single ``async with db() as session:``
block that never set that GUC at all. Every other path that reads these
tables (``app/api/goals.py``'s decision-traces endpoint, and the write path
in ``app/agent/graph.py``) wraps its session in
``app.db.rls.sqlalchemy_rls_context``/``rls_context`` first; replay_goal did
not.

Impact: under any DB role without BYPASSRLS (the least-privilege role this
app actually provisions in production, and the one built here), the
``goals`` SELECT the endpoint uses to verify the goal exists and belongs to
the tenant returned zero rows even for a goal that legitimately belongs to
that tenant, so the endpoint raised 404 "Goal not found" for every real goal.
Superuser/BYPASSRLS roles never hit this, which is why it was invisible in a
dev setup that connects as a superuser.

This test calls the real ``replay_goal`` function (not a hand-copied query)
against a NOBYPASSRLS role, via a minimal fake ``Request`` exposing only the
attributes ``replay_goal`` actually reads (``request.state.tenant`` and
``request.app.state.db_session_factory``), so it exercises the exact code
path (including the fix's ``sqlalchemy_rls_context`` wrapping) rather than a
reimplementation of it.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/api/test_replay_rls.py -q -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.api.replay import replay_goal

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "test_app"
GRANT_TABLES = (
    "tenants",
    "goals",
    "goal_events",
    "goal_steps",
    "decision_traces",
    "evaluations",
)


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


def _fake_request(*, tenant_id: str, db_session_factory) -> SimpleNamespace:
    """Minimal stand-in exposing only what replay_goal actually reads:
    request.state.tenant and request.app.state.db_session_factory."""
    return SimpleNamespace(
        state=SimpleNamespace(tenant=SimpleNamespace(tenant_id=tenant_id)),
        app=SimpleNamespace(state=SimpleNamespace(db_session_factory=db_session_factory)),
    )


@pytest.mark.asyncio
async def test_replay_goal_reads_succeed_under_nobypassrls_role(factories: tuple) -> None:
    admin_factory, app_factory = factories
    tenant_id = f"tenant-{secrets.token_hex(6)}"
    goal_id = uuid.uuid4().hex
    step_id = uuid.uuid4().hex
    trace_id = uuid.uuid4().hex
    eval_id = uuid.uuid4().hex

    # Seed via the admin (RLS-bypassing) connection -- this test is only
    # about replay_goal's own missing RLS wrapping, not about how the rows
    # got there.
    async with admin_factory() as s, s.begin():
        await s.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:tid, :name, :email)"),
            {"tid": tenant_id, "name": "Replay RLS tenant", "email": f"{tenant_id}@example.com"},
        )
        await s.execute(
            text(
                "INSERT INTO goals (id, tenant_id, goal_text, status) "
                "VALUES (:gid, :tid, :text, 'completed')"
            ),
            {"gid": goal_id, "tid": tenant_id, "text": "test goal for replay RLS"},
        )
        await s.execute(
            text(
                "INSERT INTO goal_events (id, tenant_id, goal_id, sequence, event_type, payload) "
                "VALUES (:id, :tid, :gid, 1, 'step_started', '{}')"
            ),
            {"id": uuid.uuid4().hex, "tid": tenant_id, "gid": goal_id},
        )
        await s.execute(
            text(
                "INSERT INTO goal_steps (id, tenant_id, goal_id, step_index, description, status) "
                "VALUES (:id, :tid, :gid, 0, 'do the thing', 'completed')"
            ),
            {"id": step_id, "tid": tenant_id, "gid": goal_id},
        )
        await s.execute(
            text(
                "INSERT INTO decision_traces (id, tenant_id, goal_id, action, reasoning, confidence) "
                "VALUES (:id, :tid, :gid, 'call_tool:http_request', 'because', 0.9)"
            ),
            {"id": trace_id, "tid": tenant_id, "gid": goal_id},
        )
        await s.execute(
            text(
                "INSERT INTO evaluations "
                "(id, tenant_id, goal_id, scores, average_score, passed, strategy_execution_id) "
                "VALUES (:id, :tid, :gid, '{}', 0.95, TRUE, :sid)"
            ),
            {"id": eval_id, "tid": tenant_id, "gid": goal_id, "sid": f"legacy:{eval_id}"},
        )

    try:
        fake_request = _fake_request(tenant_id=tenant_id, db_session_factory=app_factory)
        result = await replay_goal(fake_request, goal_id)

        assert result["goal_id"] == goal_id
        assert result["step_count"] == 1, (
            "goal_steps could not be read back under the non-BYPASSRLS role "
            "-- replay_goal's session was never wrapped in sqlalchemy_rls_context"
        )
        assert len(result["decision_traces"]) == 1, (
            "decision_traces could not be read back under the non-BYPASSRLS "
            "role -- replay_goal's session was never wrapped in "
            "sqlalchemy_rls_context"
        )
        assert result["decision_traces"][0]["action"] == "call_tool:http_request"
        assert len(result["evaluations"]) == 1
        assert result["event_count"] == 1
    finally:
        async with admin_factory() as s, s.begin():
            await s.execute(text("DELETE FROM evaluations WHERE tenant_id = :tid"), {"tid": tenant_id})
            await s.execute(
                text("DELETE FROM decision_traces WHERE tenant_id = :tid"), {"tid": tenant_id}
            )
            await s.execute(text("DELETE FROM goal_steps WHERE tenant_id = :tid"), {"tid": tenant_id})
            await s.execute(text("DELETE FROM goal_events WHERE tenant_id = :tid"), {"tid": tenant_id})
            await s.execute(text("DELETE FROM goals WHERE tenant_id = :tid"), {"tid": tenant_id})
            await s.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tenant_id})
