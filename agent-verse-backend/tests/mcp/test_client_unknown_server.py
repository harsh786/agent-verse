"""MCPCLI-02: an unknown server_id is "not found" — never another connector's tool."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from app.mcp.client import MCPClient
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="t-unknown", plan=PlanTier.FREE, api_key_id="k")


@pytest.mark.asyncio
async def test_unknown_server_id_does_not_dispatch_a_same_named_tool_elsewhere() -> None:
    reg = MCPRegistry(fakeredis.aioredis.FakeRedis(decode_responses=True))
    await reg.register(
        MCPServerConfig(
            server_id="payments-api",
            name="Payments",
            url="https://payments.example.com",
            tool_definitions=[{"name": "delete_account", "method": "DELETE", "path": "/acct"}],
        ),
        tenant_ctx=_CTX,
    )
    client = MCPClient(reg)
    dispatched: list[str] = []

    async def _spy(**kwargs):  # type: ignore[no-untyped-def]
        dispatched.append(kwargs["tool_def"]["name"])
        raise AssertionError("dispatched to another connector")

    client._dispatch_openapi_tool = _spy  # type: ignore[method-assign]

    result = await client.call_tool(
        server_id="does-not-exist",
        tool_name="delete_account",
        arguments={},
        tenant_ctx=_CTX,
    )

    assert result.success is False
    assert "not found" in (result.error or "").lower()
    assert dispatched == []
