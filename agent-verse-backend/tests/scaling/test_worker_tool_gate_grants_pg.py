"""RV-05 (integration): the worker multi_agent gate reads grants from real Postgres.

``_worker_tool_gate`` now builds the gate on the worker's session factory with
the durable ``PostgresGrantStore`` (tenant RLS). Against a migrated database and
a least-privilege NOBYPASSRLS role:

* a tool call is allowed when the agent holds a covering grant;
* it is denied for an agent without one, and for the same agent id in another
  tenant (RLS + tenant filter);
* a database that cannot be reached denies (``grant_store_unavailable``).

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/scaling/test_worker_tool_gate_grants_pg.py -q -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.governance.grants import Grant
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
# Taken at import: the grant window must outlast a long full-suite run (the gate
# checks against the real clock), so it spans days, not an hour.
_NOW = datetime.now(UTC)


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        admin_url = postgres.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": admin_url, "ENVIRONMENT": "development"},
            check=True,
            capture_output=True,
            text=True,
        )
        yield admin_url


@pytest_asyncio.fixture
async def app_factory(postgres_url: str) -> AsyncIterator[Any]:
    """A NOBYPASSRLS app-role session factory, with one grant issued for t1/agent-1."""
    from app.governance.grants.postgres_store import PostgresGrantStore

    password = secrets.token_urlsafe(24)
    role = f"test_app_rv05_{secrets.token_hex(4)}"
    admin_engine = create_async_engine(postgres_url)
    async with admin_engine.begin() as conn:
        quoted = (
            await conn.execute(text("SELECT quote_literal(:p)"), {"p": password})
        ).scalar_one()
        await conn.execute(
            text(
                f"CREATE ROLE {role} LOGIN PASSWORD {quoted} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
        )
        await conn.execute(text(f"GRANT CONNECT ON DATABASE test TO {role}"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        await conn.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}"))
        await conn.execute(text("DELETE FROM agent_grants WHERE grant_id = 'g-rv05'"))
    await PostgresGrantStore(async_sessionmaker(admin_engine, expire_on_commit=False)).issue(
        Grant(
            grant_id="g-rv05",
            tenant_id="t1",
            grantor="user:alice",
            grantee_agent_id="agent-1",
            scopes=("jira.*",),
            not_before=_NOW - timedelta(hours=1),
            expires_at=_NOW + timedelta(days=7),
        )
    )
    app_url = (
        make_url(postgres_url)
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )
    app_engine = create_async_engine(app_url, pool_size=2, max_overflow=0)
    try:
        yield async_sessionmaker(app_engine, expire_on_commit=False)
    finally:
        await app_engine.dispose()
        await admin_engine.dispose()


def _gate(monkeypatch: pytest.MonkeyPatch, factory: Any, agent_id: str) -> Any:
    import app.db.session as session_mod
    from app.core.config import get_settings
    from app.scaling import tasks

    monkeypatch.setattr(get_settings(), "enforce_agent_grants", True)
    monkeypatch.setattr(session_mod, "get_session_factory", lambda: factory)
    monkeypatch.setattr(tasks, "_worker_async_redis", lambda: None)
    return tasks._worker_tool_gate(None, None, None, agent_id)


async def _authorize(gate: Any, tenant_id: str) -> Any:
    return await gate.authorize(
        tool_name="jira.search",
        server_name="jira",
        arguments={"q": "open bugs"},
        tenant_ctx=TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="k"),
        goal_id="g-rv05",
        step_description="search jira",
    )


@pytest.mark.asyncio
async def test_worker_gate_allows_with_a_persisted_grant(
    app_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    decision = await _authorize(_gate(monkeypatch, app_factory, "agent-1"), "t1")
    assert decision.allowed, decision.reason


@pytest.mark.asyncio
async def test_worker_gate_denies_without_a_grant_or_in_another_tenant(
    app_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    other_agent = await _authorize(_gate(monkeypatch, app_factory, "agent-2"), "t1")
    assert not other_agent.allowed
    assert "no_grant_for_agent" in other_agent.reason

    other_tenant = await _authorize(_gate(monkeypatch, app_factory, "agent-1"), "t2")
    assert not other_tenant.allowed
    assert "no_grant_for_agent" in other_tenant.reason


@pytest.mark.asyncio
async def test_worker_gate_denies_when_the_database_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dead = create_async_engine("postgresql+asyncpg://nouser:nopass@127.0.0.1:1/none")
    try:
        gate = _gate(monkeypatch, async_sessionmaker(dead, expire_on_commit=False), "agent-1")
        decision = await _authorize(gate, "t1")
    finally:
        await dead.dispose()
    assert not decision.allowed
