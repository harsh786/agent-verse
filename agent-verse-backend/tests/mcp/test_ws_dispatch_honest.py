"""MCPCLI-03: WebSocket connectors are authenticated and never silently re-routed.

The WS branch of ``MCPClient._call_tool_impl`` opened ``MCPWebSocketClient``
without any credentials (``auth_token`` was never passed) and, when the call
failed, logged a warning and fell through to HTTP dispatch against the
connector's ``url`` — masking every WS failure behind a different transport.
Now the connector's auth headers go on the WS handshake and a WS failure is the
call's failure.
"""

from __future__ import annotations

from typing import Any

import fakeredis.aioredis
import pytest

from app.mcp.client import MCPClient
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")


class _FakeWS:
    instances: list[_FakeWS] = []
    fail: bool = False

    def __init__(self, ws_url: str, **kwargs: Any) -> None:
        self.ws_url = ws_url
        self.kwargs = kwargs
        _FakeWS.instances.append(self)

    async def __aenter__(self) -> _FakeWS:
        if _FakeWS.fail:
            raise ConnectionError("ws handshake refused")
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def call_tool(self, *, tool_name: str, arguments: dict[str, Any]) -> Any:
        return {"ok": tool_name}


@pytest.fixture
def ws_client(monkeypatch: pytest.MonkeyPatch) -> MCPClient:
    _FakeWS.instances = []
    _FakeWS.fail = False
    monkeypatch.setattr("app.mcp.ws_client.MCPWebSocketClient", _FakeWS)
    monkeypatch.setattr("app.mcp.client._assert_egress_allowed", lambda *a, **k: None)
    registry = MCPRegistry(fakeredis.aioredis.FakeRedis())
    client = MCPClient(registry)

    def _no_http(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("WS failure fell through to HTTP dispatch")

    monkeypatch.setattr(client, "_http_client", _no_http)
    return client


async def _register(client: MCPClient) -> str:
    return await client._registry.register(
        MCPServerConfig(
            name="feed",
            url="https://feed.example.com",
            ws_url="wss://feed.example.com/ws",
            transport="ws",
            auth_type="bearer",
            auth_config={"token": "tok-123"},
        ),
        tenant_ctx=CTX,
    )


async def test_ws_handshake_carries_the_connector_auth(ws_client: MCPClient) -> None:
    server_id = await _register(ws_client)
    result = await ws_client.call_tool(
        server_id=server_id, tool_name="quote", arguments={}, tenant_ctx=CTX
    )
    assert result.success and result.output == {"ok": "quote"}
    assert _FakeWS.instances[0].kwargs.get("headers") == {"Authorization": "Bearer tok-123"}


async def test_ws_failure_is_the_calls_failure(ws_client: MCPClient) -> None:
    server_id = await _register(ws_client)
    _FakeWS.fail = True
    result = await ws_client.call_tool(
        server_id=server_id, tool_name="quote", arguments={}, tenant_ctx=CTX
    )
    assert result.success is False
    assert "ws handshake refused" in result.error


async def test_ws_client_sends_given_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.mcp import ws_client as ws_mod

    seen: dict[str, Any] = {}

    async def _connect(url: str, **kwargs: Any) -> Any:
        seen.update(kwargs)
        raise ConnectionError("stop")

    monkeypatch.setattr("app.net.ssrf_guard.connect_public_websocket", _connect)
    client = ws_mod.MCPWebSocketClient(
        "wss://feed.example.com/ws", headers={"X-API-Key": "k"}, auth_token="t"
    )
    with pytest.raises(ConnectionError):
        await client.connect()
    assert seen["additional_headers"] == {"X-API-Key": "k", "Authorization": "Bearer t"}
