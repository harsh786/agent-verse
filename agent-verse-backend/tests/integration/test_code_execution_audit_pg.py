"""CODE-02 against real Postgres: execute-code and chat execute each commit exactly
one audit_log row, visible under the tenant's RLS context and not another's.

Runs as a least-privilege (NOSUPERUSER, NOBYPASSRLS) role so FORCE RLS binds.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.governance.audit import AuditLog
from app.tenancy.context import PlanTier, TenantContext
from app.tools.code_interpreter import CodeResult

pytestmark = pytest.mark.integration


def _plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
def app_role_url(pg_url: str) -> Iterator[str]:
    role = f"app_audit_{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(16)

    async def _run(*stmts: str) -> None:
        conn = await asyncpg.connect(_plain(pg_url))
        try:
            for stmt in stmts:
                await conn.execute(stmt)
        finally:
            await conn.close()

    asyncio.run(
        _run(
            f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOBYPASSRLS",
            f"GRANT SELECT, INSERT ON audit_log TO {role}",
            # NATIVE-01/04: the durable workspace (files + per-tenant usage quota).
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON workspace_files, workspace_usage TO {role}",
        )
    )
    head, tail = pg_url.split("://", 1)
    yield f"{head}://{role}:{password}@{tail.split('@', 1)[1]}"
    asyncio.run(
        _run(
            f"REVOKE ALL ON audit_log, workspace_files, workspace_usage FROM {role}",
            f"DROP ROLE IF EXISTS {role}",
        )
    )


class _Interp:
    def __init__(self, **_kw: Any) -> None:
        pass

    async def execute(self, **_kw: Any) -> CodeResult:
        return CodeResult(stdout="ok\n", stderr="", exit_code=0)


def _factory(url: str) -> Any:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    return async_sessionmaker(create_async_engine(url, poolclass=NullPool), expire_on_commit=False)


async def _rows(url: str, tenant: str) -> list[tuple[str, str]]:
    conn = await asyncpg.connect(_plain(url))
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant)
            recs = await conn.fetch(
                "SELECT goal_id, note FROM audit_log WHERE tool_name LIKE 'code_interpreter.%'"
            )
    finally:
        await conn.close()
    return [(r["goal_id"], r["note"]) for r in recs]


def test_each_execution_commits_one_tenant_scoped_row(
    app_role_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.tools import router as tools_router
    from app.chat.router import router as chat_router

    monkeypatch.setattr("app.tools.code_execution.CodeInterpreter", _Interp)
    tenant_a = f"ta-{secrets.token_hex(4)}"
    tenant_b = f"tb-{secrets.token_hex(4)}"
    ctx = TenantContext(
        tenant_id=tenant_a, plan=PlanTier.FREE, api_key_id="key-a", roles=("operator",)
    )

    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(tools_router)
    app.include_router(chat_router)
    app.state.audit_log = AuditLog(db_session_factory=_factory(app_role_url))
    chat = MagicMock()
    chat.aget_session = AsyncMock(return_value={"id": "sess-a"})
    app.state.chat_service = chat

    client = TestClient(app)
    assert client.post("/tools/execute-code", json={"code": "print(1)"}).status_code == 200
    assert (
        client.post("/chat/sessions/sess-a/execute", json={"code": "print(2)"}).status_code == 200
    )

    rows_a = asyncio.run(_rows(app_role_url, tenant_a))
    assert len(rows_a) == 2
    sources = sorted(note.split()[0] for _, note in rows_a)
    assert sources == ["source=chat.execute", "source=tools.execute_code"]
    assert "sess-a" in {g for g, _ in rows_a}
    assert asyncio.run(_rows(app_role_url, tenant_b)) == []


async def _native_rows(url: str, tenant: str) -> list[str]:
    conn = await asyncpg.connect(_plain(url))
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant)
            recs = await conn.fetch(
                "SELECT tool_name FROM audit_log WHERE tool_name IN "
                "('workspace.write', 'workspace.delete', 'email.send') ORDER BY created_at"
            )
    finally:
        await conn.close()
    return [r["tool_name"] for r in recs]


def test_native_tool_side_effects_commit_tenant_scoped_rows(
    app_role_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NATIVE-03: workspace write/delete and email send each commit a row first."""
    from app.api.tools import router as tools_router

    async def _send(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"success": True}

    monkeypatch.setattr("app.tools.email_tool.email_send", _send)
    tenant_a = f"ta-{secrets.token_hex(4)}"
    ctx = TenantContext(
        tenant_id=tenant_a, plan=PlanTier.FREE, api_key_id="key-a", roles=("operator",)
    )
    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(tools_router)
    app.state.audit_log = AuditLog(db_session_factory=_factory(app_role_url))
    # NATIVE-01: the workspace is the durable Postgres store (the route refuses a
    # missing / non-durable one with 503).
    from app.tools.workspace_store import PostgresWorkspaceStore

    app.state.workspace_store = PostgresWorkspaceStore(_factory(app_role_url))
    client = TestClient(app)
    assert client.post("/tools/files/n.txt", json={"content": "x"}).status_code == 201
    assert client.delete("/tools/files/n.txt").status_code == 204
    assert (
        client.post(
            "/tools/email/send", json={"to": "a@example.com", "subject": "s", "body": "b"}
        ).status_code
        == 200
    )
    assert asyncio.run(_native_rows(app_role_url, tenant_a)) == [
        "workspace.write",
        "workspace.delete",
        "email.send",
    ]
    assert asyncio.run(_native_rows(app_role_url, f"tb-{secrets.token_hex(4)}")) == []
