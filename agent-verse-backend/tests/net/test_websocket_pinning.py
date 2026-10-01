"""SSRF-05: MCP WebSocket connections are pinned to the checked address.

The MCP ``ws_url`` was validated with ``assert_public_url`` and then handed to
``websockets.connect``, which resolved the name AGAIN to connect: a tenant
connector on a rebinding host (public at check time, 127.0.0.1 at connect time)
reached internal services. ``connect_public_websocket`` resolves and checks once
and connects the socket to the checked IP (TLS keeps the hostname for SNI and
certificate verification); env proxies are ignored because a proxy would
resolve the name itself.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.net.ssrf_guard as g
from app.net.ssrf_guard import SSRFError, connect_public_websocket


def _capture_connect(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def _connect(uri: str, **kwargs: Any) -> Any:
        calls.append((uri, kwargs))
        ws = MagicMock()
        ws.close = AsyncMock()
        return ws

    monkeypatch.setattr("websockets.connect", _connect)
    return calls


@pytest.mark.asyncio
async def test_connects_to_the_checked_ip_with_the_hostname_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(g, "_resolve_host", lambda h: ["93.184.216.34"])
    calls = _capture_connect(monkeypatch)
    await connect_public_websocket("wss://mcp.example/ws", additional_headers={"A": "b"})
    uri, kwargs = calls[0]
    assert uri == "wss://mcp.example/ws"  # Host header, SNI and cert check use the name
    assert kwargs["host"] == "93.184.216.34"  # ...but the socket dials the checked IP
    assert kwargs["proxy"] is None
    assert kwargs["additional_headers"] == {"A": "b"}


@pytest.mark.asyncio
async def test_a_name_resolving_internal_is_refused_before_any_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(g, "_resolve_host", lambda h: ["127.0.0.1"])
    calls = _capture_connect(monkeypatch)
    with pytest.raises(SSRFError):
        await connect_public_websocket("ws://rebind.example/ws")
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://mcp.example/ws", "ftp://x/", "ws:///nohost"])
async def test_non_websocket_or_hostless_urls_are_refused(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    calls = _capture_connect(monkeypatch)
    with pytest.raises(SSRFError):
        await connect_public_websocket(url)
    assert calls == []


@pytest.mark.asyncio
async def test_caller_cannot_override_the_pinned_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(g, "_resolve_host", lambda h: ["93.184.216.34"])
    calls = _capture_connect(monkeypatch)
    for bad in ({"host": "127.0.0.1"}, {"sock": object()}, {"unix": True}, {"proxy": True}):
        with pytest.raises(ValueError, match="pinned"):
            await connect_public_websocket("ws://mcp.example/ws", **bad)
    assert calls == []


@pytest.mark.asyncio
async def test_next_checked_address_is_tried_on_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(g, "_resolve_host", lambda h: ["93.184.216.34", "93.184.216.35"])
    hosts: list[str] = []

    async def _connect(uri: str, **kwargs: Any) -> Any:
        hosts.append(kwargs["host"])
        if kwargs["host"] == "93.184.216.34":
            raise OSError("refused")
        return MagicMock()

    monkeypatch.setattr("websockets.connect", _connect)
    await connect_public_websocket("ws://mcp.example/ws")
    assert hosts == ["93.184.216.34", "93.184.216.35"]


@pytest.mark.asyncio
async def test_mcp_ws_client_connects_through_the_pinned_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.mcp.ws_client import MCPWebSocketClient

    monkeypatch.setattr(g, "_resolve_host", lambda h: ["93.184.216.34"])
    calls = _capture_connect(monkeypatch)
    monkeypatch.setattr("asyncio.create_task", lambda coro: coro.close())
    client = MCPWebSocketClient("wss://mcp.example/ws", auth_token="tok")
    await client.connect()
    _uri, kwargs = calls[0]
    assert kwargs["host"] == "93.184.216.34"
    assert kwargs["additional_headers"] == {"Authorization": "Bearer tok"}


@pytest.mark.asyncio
async def test_mcp_ws_client_refuses_a_host_that_now_resolves_internal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.mcp.ws_client import MCPWebSocketClient

    monkeypatch.setattr(g, "_resolve_host", lambda h: ["169.254.169.254"])
    calls = _capture_connect(monkeypatch)
    with pytest.raises(SSRFError):
        await MCPWebSocketClient("ws://rebind.example/ws").connect()
    assert calls == []
