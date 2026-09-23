"""Integration test: tool_capabilities reads/writes actually work under a
real, least-privilege (non-BYPASSRLS) DB role.

``tool_capabilities`` has ``FORCE ROW LEVEL SECURITY`` (migration 0030), and
its tenant-isolation policy (``tool_cap_isolation``) checked
``current_setting('app.current_tenant_id', TRUE)`` -- the wrong GUC name.
The application code path that sets tenant scope for RLS
(``app.db.rls.sqlalchemy_rls_context`` / ``rls_context``) sets
``app.tenant_id`` instead (see ``GET /connectors/capabilities`` in
``app/api/connectors.py::list_capabilities``, reproduced here verbatim, and
``app/mcp/openapi_importer.py::persist_tools``). Migration 06f8d39de7a2 fixes
the policy to check ``app.tenant_id``, mirroring the identical fix migration
0034 made for ``agent_snapshots``, 767fe9d87bfe made for
``compliance_requests``, and e79efcca385f made for ``decision_traces``.

Impact: under any DB role without BYPASSRLS (the least-privilege role this
app actually provisions in production, and the one built here), an INSERT
into ``tool_capabilities`` violated the policy's WITH CHECK, and a SELECT
under the same role returned zero rows -- even though the caller already
wrapped the session in the *correct* ``sqlalchemy_rls_context``. Superuser/
BYPASSRLS roles never hit this, which is why it was invisible in a dev setup
that connects as a superuser.

Note: this test exercises the read path (``list_capabilities``'s SELECT) and
an INSERT through ``sqlalchemy_rls_context`` matching
``app/mcp/openapi_importer.py::persist_tools``'s wrapping. Two *other*
tool_capabilities write paths (``discover_connector_tools`` in
``app/api/connectors.py`` and ``MCPClient._update_tool_stats`` in
``app/mcp/client.py``) never wrap their session in ``sqlalchemy_rls_context``/
``rls_context`` at all -- a separate application-code bug, tracked
separately, that this migration alone does not fix.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/mcp/test_tool_capabilities_rls.py -q -m integration
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
from app.mcp.openapi_importer import persist_tools

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


async def _insert_tool_capability(
    session: AsyncSession, *, row_id: str, tenant_id: str, connector_id: str, tool_name: str
) -> None:
    """INSERT matching app/mcp/openapi_importer.py::persist_tools's ORM insert
    (expressed as raw SQL so this test has no dependency on the separately
    broken ``ToolCapability`` ORM import)."""
    await session.execute(
        text(
            """INSERT INTO tool_capabilities
                (id, tenant_id, connector_id, tool_name, description, http_method, http_path)
                VALUES (:id, :tid, :cid, :name, :desc, 'GET', '/v1/example')"""
        ),
        {
            "id": row_id,
            "tid": tenant_id,
            "cid": connector_id,
            "name": tool_name,
            "desc": "example tool",
        },
    )


async def _list_capabilities(session: AsyncSession, *, tenant_id: str) -> list:
    """Verbatim copy of the SELECT in app/api/connectors.py::list_capabilities."""
    result = await session.execute(
        text(
            "SELECT tool_name, connector_id, description "
            "FROM tool_capabilities WHERE tenant_id = :tid"
        ),
        {"tid": tenant_id},
    )
    return result.fetchall()


@pytest.mark.asyncio
async def test_tool_capability_persists_and_lists_under_nobypassrls_role(
    factories: tuple,
) -> None:
    admin_factory, app_factory = factories
    tenant_id = f"tenant-{secrets.token_hex(6)}"
    row_id = uuid.uuid4().hex
    connector_id = "conn-" + secrets.token_hex(4)

    try:
        # Write path: matches persist_tools's sqlalchemy_rls_context wrapping.
        async with (
            app_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            await _insert_tool_capability(
                session,
                row_id=row_id,
                tenant_id=tenant_id,
                connector_id=connector_id,
                tool_name="http_request",
            )

        # Prove it actually reached Postgres (not silently rejected by RLS)
        # by reading it back with the admin (RLS-bypassing) connection.
        async with admin_factory() as s:
            row = (
                await s.execute(
                    text("SELECT tenant_id FROM tool_capabilities WHERE id = :rid"),
                    {"rid": row_id},
                )
            ).fetchone()
        assert row is not None, (
            "tool capability was never persisted to Postgres -- RLS silently "
            "rejected the INSERT under the non-BYPASSRLS role"
        )
        assert row[0] == tenant_id

        # Read path: matches GET /connectors/capabilities's sqlalchemy_rls_context
        # wrapping -- must see the row under the same tenant.
        async with (
            app_factory() as session,
            sqlalchemy_rls_context(session, tenant_id),
        ):
            rows = await _list_capabilities(session, tenant_id=tenant_id)
        assert len(rows) == 1, (
            "tool capability could not be listed back under the "
            "non-BYPASSRLS role even though it was written under the same tenant"
        )
        assert rows[0][0] == "http_request"
    finally:
        async with admin_factory() as s, s.begin():
            await s.execute(
                text("DELETE FROM tool_capabilities WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            )


@pytest.mark.asyncio
async def test_persist_tools_actually_persists_under_nobypassrls_role(
    factories: tuple,
) -> None:
    """Regression test for app/mcp/openapi_importer.py::persist_tools.

    persist_tools used to ``from app.db.models.mcp import ToolCapability`` --
    a class that does not exist anywhere in ``app/db/models/`` (every other
    tool_capabilities read/write path in this codebase uses raw SQL via
    ``text()``, never an ORM model). That import raised ``ImportError`` on
    every single call, which the function's own ``except Exception`` swallowed
    and logged as a warning, then returned ``0`` -- so tool-capability
    persistence via the OpenAPI importer path had never actually worked in
    production; every OpenAPI-imported connector silently ended up with zero
    persisted tool_capabilities rows despite ``import_and_register``/the
    ``/connectors/import`` endpoint reporting a nonzero ``tool_count``.

    This calls the real ``persist_tools`` (not a hand-copied INSERT) against a
    NOBYPASSRLS role, so it also proves the RLS wrapping inside persist_tools
    is correct now that the import bug no longer masks it.
    """
    _admin_factory, app_factory = factories
    tenant_id = f"tenant-{secrets.token_hex(6)}"
    connector_id = "conn-" + secrets.token_hex(4)
    tools = [
        {
            "id": uuid.uuid4().hex,
            "tenant_id": tenant_id,
            "connector_id": connector_id,
            "tool_name": "get_widgets",
            "description": "List widgets",
            "http_method": "GET",
            "http_path": "/widgets",
            "parameters_schema": {"type": "object", "properties": {}, "required": []},
            "response_schema": None,
        },
        {
            "id": uuid.uuid4().hex,
            "tenant_id": tenant_id,
            "connector_id": connector_id,
            "tool_name": "post_widgets",
            "description": "Create widget",
            "http_method": "POST",
            "http_path": "/widgets",
            "parameters_schema": {"type": "object", "properties": {"body": {}}, "required": []},
            "response_schema": {"type": "object"},
        },
    ]

    try:
        count = await persist_tools(tools, app_factory, tenant_id)
        assert count == len(tools), (
            "persist_tools did not report all tools as persisted -- it "
            "returned 0 when the ToolCapability ORM import raised ImportError"
        )

        # Prove the rows actually reached Postgres (not silently rejected/
        # skipped) by reading them back with the admin (RLS-bypassing)
        # connection.
        async with _admin_factory() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT tool_name, http_method, response_schema "
                        "FROM tool_capabilities WHERE tenant_id = :tid ORDER BY tool_name"
                    ),
                    {"tid": tenant_id},
                )
            ).fetchall()
        assert len(rows) == 2, (
            "persist_tools reported success but no rows were actually "
            "persisted to Postgres"
        )
        assert rows[0][0] == "get_widgets"
        assert rows[0][1] == "GET"
        assert rows[0][2] is None
        assert rows[1][0] == "post_widgets"
        assert rows[1][2] == {"type": "object"}
    finally:
        async with _admin_factory() as s, s.begin():
            await s.execute(
                text("DELETE FROM tool_capabilities WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            )
