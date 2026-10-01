"""OAUTH-PASSTHROUGH against real Postgres: the built-in reads each connection's
token from the durable ``oauth_tokens`` store (RLS-scoped, as the worker does)."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from app.mcp.client import MCPClient
from app.mcp.oauth import OAuthFlowManager, OAuthToken
from app.mcp.registry import MCPServerConfig
from app.mcp.servers.registry_wiring import get_builtin_server_configs
from app.tenancy.context import PlanTier, TenantContext

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_builtin_reads_each_connections_token_from_postgres(
    pg_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    sent: list[str] = []

    async def _send(self: Any, request: httpx.Request, **kw: Any) -> httpx.Response:
        sent.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json={"files": []}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", _send)
    monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "PLATFORM-GOOGLE-TOKEN")

    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        tenant = uuid.uuid4().hex
        async with factory() as s, s.begin():
            await s.execute(
                text("INSERT INTO tenants (id, name, email) VALUES (:t, 'oauth', :e)"),
                {"t": tenant, "e": f"{tenant}@oauth.test"},
            )
        writer = OAuthFlowManager()
        writer._db_session_factory = factory
        ids = ["builtin-google-drive:drive-a", "builtin-google-drive:drive-b"]
        for sid, at in zip(ids, ("db-at-a", "db-at-b"), strict=True):
            await writer._persist_token_to_db(tenant, sid, OAuthToken(access_token=at))

        # A process that never ran the OAuth flow (the Celery worker).
        reader = OAuthFlowManager()
        reader._db_session_factory = factory
        client = MCPClient(registry=AsyncMock())
        client._oauth_manager = reader
        (spec,) = [
            c for c in get_builtin_server_configs() if c["server_id"] == "builtin-google-drive"
        ]
        ctx = TenantContext(tenant_id=tenant, plan=PlanTier.PROFESSIONAL, api_key_id="k")
        for sid in ids:
            cfg = MCPServerConfig(
                server_id=sid,
                name=sid.split(":")[1],
                base_url="builtin://",
                auth_type="oauth_ac",
                builtin_type="builtin-google-drive",
                builtin_handler=spec["handler"],
            )
            result = await client._call_tool_impl(cfg, sid, "drive_list_files", {}, ctx)
            assert result.success, result.error

        assert sent == ["Bearer db-at-a", "Bearer db-at-b"]
    finally:
        await engine.dispose()
