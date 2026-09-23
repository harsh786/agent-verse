"""Integration test: the two tool_capabilities write paths that skipped RLS
context entirely -- ``discover_connector_tools`` (app/api/connectors.py) and
``MCPClient._update_tool_stats`` (app/mcp/client.py) -- actually persist
under a real, least-privilege (non-BYPASSRLS) DB role.

``tool_capabilities`` has ``FORCE ROW LEVEL SECURITY`` (migration 0030,
policy fixed to key on ``app.tenant_id`` by 06f8d39de7a2). Unlike
``list_capabilities`` (already wrapped in ``sqlalchemy_rls_context``) and
``persist_tools`` (see ``tests/mcp/test_tool_capabilities_rls.py``),
``discover_connector_tools`` and ``_update_tool_stats`` executed raw
INSERT/UPDATE statements without ever setting the ``app.tenant_id`` GUC --
see the note in migration 06f8d39de7a2 and in
``tests/mcp/test_tool_capabilities_rls.py``'s module docstring, which
tracked this as a separate bug.

Impact: under any DB role without BYPASSRLS, the discover endpoint's INSERT
violated the policy's WITH CHECK (row silently not persisted, "tools_saved"
undercounts or the whole discover call raises depending on driver behavior),
and MCPClient's reliability-tracking UPDATE matched zero rows (WHERE clause
never satisfies USING) even when the row exists -- so call/error counts and
health status silently stopped updating. Superuser/BYPASSRLS roles never hit
this, which is why it was invisible in a dev setup that connects as a
superuser.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/mcp/test_tool_capabilities_write_paths_rls.py -q -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.api.connectors import discover_connector_tools
from app.mcp.client import MCPClient

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "test_app"
GRANT_TABLES = ("tool_capabilities",)


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


def _fake_request(*, tenant_id: str, db_session_factory, mcp_client) -> SimpleNamespace:
    """Minimal stand-in exposing only what discover_connector_tools actually
    reads: request.state.tenant and request.app.state.{db_session_factory,mcp_client}."""
    return SimpleNamespace(
        state=SimpleNamespace(tenant=SimpleNamespace(tenant_id=tenant_id)),
        app=SimpleNamespace(
            state=SimpleNamespace(db_session_factory=db_session_factory, mcp_client=mcp_client)
        ),
    )


@pytest.mark.asyncio
async def test_discover_connector_tools_persists_under_nobypassrls_role(
    factories: tuple,
) -> None:
    admin_factory, app_factory = factories
    tenant_id = f"tenant-{secrets.token_hex(6)}"
    server_id = "conn-" + secrets.token_hex(4)

    fake_tool = SimpleNamespace(
        name="get_widgets",
        description="List widgets",
        input_schema={"type": "object"},
        risk_level="low",
    )

    class _FakeMCPClient:
        async def discover_tools(self, *, server_id: str, tenant_ctx: Any = None) -> list:
            return [fake_tool]

    try:
        fake_request = _fake_request(
            tenant_id=tenant_id, db_session_factory=app_factory, mcp_client=_FakeMCPClient()
        )
        result = await discover_connector_tools(fake_request, server_id)

        assert result["tools_saved"] == 1, (
            "discover_connector_tools reported 0 tools saved -- the INSERT's "
            "WITH CHECK was violated because the session was never wrapped "
            "in sqlalchemy_rls_context"
        )

        # Prove it actually reached Postgres (not silently rejected by RLS)
        # by reading it back with the admin (RLS-bypassing) connection.
        async with admin_factory() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT tenant_id, tool_name FROM tool_capabilities "
                        "WHERE tenant_id = :tid AND connector_id = :cid"
                    ),
                    {"tid": tenant_id, "cid": server_id},
                )
            ).fetchone()
        assert row is not None, (
            "tool capability was never persisted to Postgres by "
            "discover_connector_tools -- RLS silently rejected the INSERT "
            "under the non-BYPASSRLS role"
        )
        assert row[1] == "get_widgets"
    finally:
        async with admin_factory() as s, s.begin():
            await s.execute(
                text("DELETE FROM tool_capabilities WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            )


@pytest.mark.asyncio
async def test_update_tool_stats_persists_under_nobypassrls_role(factories: tuple) -> None:
    admin_factory, app_factory = factories
    tenant_id = f"tenant-{secrets.token_hex(6)}"
    connector_id = "conn-" + secrets.token_hex(4)
    row_id = uuid.uuid4().hex

    # Seed a tool_capabilities row via the admin (RLS-bypassing) connection --
    # this test is only about _update_tool_stats' own missing RLS wrapping.
    async with admin_factory() as s, s.begin():
        await s.execute(
            text(
                """INSERT INTO tool_capabilities
                    (id, tenant_id, connector_id, tool_name, description, http_method, http_path,
                     call_count, error_count, avg_latency_ms, success_rate, health_status)
                    VALUES (:id, :tid, :cid, :name, 'desc', 'GET', '/v1/example',
                            0, 0, 0.0, 1.0, 'unknown')"""
            ),
            {"id": row_id, "tid": tenant_id, "cid": connector_id, "name": "http_request"},
        )

    try:
        client = MCPClient.__new__(MCPClient)  # bypass __init__; only uses self implicitly not at all
        await MCPClient._update_tool_stats(
            client,
            server_id=connector_id,
            tool_name="http_request",
            tenant_id=tenant_id,
            success=True,
            latency_ms=42.0,
            db=app_factory,
        )

        async with admin_factory() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT call_count, error_count, health_status FROM tool_capabilities "
                        "WHERE id = :rid"
                    ),
                    {"rid": row_id},
                )
            ).fetchone()
        assert row is not None
        assert row[0] == 1, (
            "call_count was never incremented -- _update_tool_stats' UPDATE "
            "matched zero rows because the session was never wrapped in "
            "sqlalchemy_rls_context (USING clause never satisfied)"
        )
        assert row[1] == 0
        assert row[2] == "healthy"
    finally:
        async with admin_factory() as s, s.begin():
            await s.execute(
                text("DELETE FROM tool_capabilities WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            )
