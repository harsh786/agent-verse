"""Comprehensive tests for MCPWebSocketClient — connect, disconnect, call_tool,
subscribe, _dispatch_messages, list_tools, reconnection logic.
"""
from __future__ import annotations

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.mcp.ws_client import MCPWebSocketClient, _RECONNECT_BASE, _RECONNECT_MAX


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_ws_mock() -> MagicMock:
    """Create a mock websocket that can be iterated and sent to."""
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.__aiter__ = MagicMock(return_value=iter([]))
    return ws


# ── 1. Constructor ────────────────────────────────────────────────────────────

def test_constructor_defaults():
    client = MCPWebSocketClient("ws://localhost:8080/ws")
    assert client._ws_url == "ws://localhost:8080/ws"
    assert client._auth_token is None
    assert client._reconnect is True
    assert client._max_reconnects == 10
    assert client._connected is False
    assert client._pending == {}
    assert client._subscriptions == {}
    assert client._msg_id == 0


def test_constructor_with_auth():
    client = MCPWebSocketClient("ws://host/ws", auth_token="Bearer token123")
    assert client._auth_token == "Bearer token123"


def test_constructor_no_reconnect():
    client = MCPWebSocketClient("ws://host/ws", reconnect=False)
    assert client._reconnect is False


# ── 2. connect ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_connect_sets_connected():
    client = MCPWebSocketClient("ws://localhost/ws")
    mock_ws = _make_ws_mock()

    with patch("websockets.connect", AsyncMock(return_value=mock_ws)):
        with patch("asyncio.create_task"):
            result = await client.connect()

    assert client._connected is True
    assert client._ws is mock_ws
    assert result is client


@pytest.mark.asyncio
async def test_connect_with_auth_header():
    client = MCPWebSocketClient("ws://localhost/ws", auth_token="mytoken")
    mock_ws = _make_ws_mock()
    captured_headers = {}

    async def mock_connect(url, extra_headers=None, **kwargs):
        captured_headers.update(extra_headers or {})
        return mock_ws

    with patch("websockets.connect", side_effect=mock_connect):
        with patch("asyncio.create_task"):
            await client.connect()

    assert captured_headers.get("Authorization") == "Bearer mytoken"


@pytest.mark.asyncio
async def test_connect_without_auth_no_auth_header():
    client = MCPWebSocketClient("ws://localhost/ws")
    mock_ws = _make_ws_mock()
    captured_headers = {}

    async def mock_connect(url, extra_headers=None, **kwargs):
        captured_headers.update(extra_headers or {})
        return mock_ws

    with patch("websockets.connect", side_effect=mock_connect):
        with patch("asyncio.create_task"):
            await client.connect()

    assert "Authorization" not in captured_headers


@pytest.mark.asyncio
async def test_connect_missing_websockets_raises():
    client = MCPWebSocketClient("ws://localhost/ws")
    with patch.dict("sys.modules", {"websockets": None}):
        with pytest.raises(RuntimeError, match="websockets is not installed"):
            await client.connect()


# ── 3. disconnect ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_disconnect_closes_ws():
    client = MCPWebSocketClient("ws://localhost/ws")
    mock_ws = _make_ws_mock()

    with patch("websockets.connect", AsyncMock(return_value=mock_ws)):
        with patch("asyncio.create_task"):
            await client.connect()

    await client.disconnect()

    assert client._connected is False
    assert client._ws is None
    mock_ws.close.assert_called_once()


@pytest.mark.asyncio
async def test_disconnect_when_not_connected():
    client = MCPWebSocketClient("ws://localhost/ws")
    # Should not raise
    await client.disconnect()
    assert client._connected is False


# ── 4. __aenter__ / __aexit__ ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_context_manager():
    client = MCPWebSocketClient("ws://localhost/ws")
    mock_ws = _make_ws_mock()

    with patch("websockets.connect", AsyncMock(return_value=mock_ws)):
        with patch("asyncio.create_task"):
            async with client as c:
                assert c._connected is True
    assert client._connected is False


# ── 5. call_tool ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_call_tool_sends_message():
    client = MCPWebSocketClient("ws://localhost/ws")
    mock_ws = _make_ws_mock()
    client._ws = mock_ws
    client._connected = True

    # Manually resolve the future to simulate server response
    async def resolve_future():
        await asyncio.sleep(0)
        msg_id = str(client._msg_id)
        if msg_id in client._pending:
            client._pending[msg_id].set_result({"items": []})

    asyncio.create_task(resolve_future())
    result = await client.call_tool("search", {"query": "test"}, timeout=5.0)

    mock_ws.send.assert_called_once()
    sent_data = json.loads(mock_ws.send.call_args[0][0])
    assert sent_data["type"] == "call_tool"
    assert sent_data["tool"] == "search"
    assert sent_data["arguments"] == {"query": "test"}


@pytest.mark.asyncio
async def test_call_tool_timeout_raises():
    client = MCPWebSocketClient("ws://localhost/ws")
    mock_ws = _make_ws_mock()
    client._ws = mock_ws
    client._connected = True

    with pytest.raises(TimeoutError, match="timed out"):
        await client.call_tool("slow_tool", {}, timeout=0.01)


@pytest.mark.asyncio
async def test_call_tool_timeout_cleans_up_pending():
    client = MCPWebSocketClient("ws://localhost/ws")
    mock_ws = _make_ws_mock()
    client._ws = mock_ws

    try:
        await client.call_tool("tool", {}, timeout=0.01)
    except TimeoutError:
        pass

    # Pending dict should be cleaned up
    assert len(client._pending) == 0


@pytest.mark.asyncio
async def test_call_tool_increments_msg_id():
    client = MCPWebSocketClient("ws://localhost/ws")
    mock_ws = _make_ws_mock()
    client._ws = mock_ws
    initial_id = client._msg_id

    # Start call but let it timeout
    try:
        await client.call_tool("t", {}, timeout=0.01)
    except TimeoutError:
        pass

    assert client._msg_id == initial_id + 1


# ── 6. _dispatch_messages ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_routes_rpc_response():
    client = MCPWebSocketClient("ws://localhost/ws")
    mock_ws = MagicMock()
    client._ws = mock_ws
    client._connected = True

    # Register a pending future
    loop = asyncio.get_event_loop()
    future: asyncio.Future = loop.create_future()
    client._pending["1"] = future

    # Simulate a message being received
    rpc_response = json.dumps({"id": "1", "result": {"data": "response_value"}})

    async def message_iter():
        yield rpc_response
        client._connected = False  # stop the loop

    mock_ws.__aiter__ = MagicMock(return_value=message_iter())

    await client._dispatch_messages()
    assert future.done()
    assert future.result() == {"data": "response_value"}


@pytest.mark.asyncio
async def test_dispatch_routes_error_to_future():
    client = MCPWebSocketClient("ws://localhost/ws")
    mock_ws = MagicMock()
    client._ws = mock_ws
    client._connected = True

    loop = asyncio.get_event_loop()
    future: asyncio.Future = loop.create_future()
    client._pending["2"] = future

    error_response = json.dumps({"id": "2", "error": "tool not found"})

    async def message_iter():
        yield error_response
        client._connected = False

    mock_ws.__aiter__ = MagicMock(return_value=message_iter())

    await client._dispatch_messages()
    assert future.done()
    assert future.exception() is not None


@pytest.mark.asyncio
async def test_dispatch_handles_non_json_message():
    client = MCPWebSocketClient("ws://localhost/ws")
    mock_ws = MagicMock()
    client._ws = mock_ws
    client._connected = True

    async def message_iter():
        yield "not-json-at-all"
        client._connected = False

    mock_ws.__aiter__ = MagicMock(return_value=message_iter())

    # Should not raise
    await client._dispatch_messages()


@pytest.mark.asyncio
async def test_dispatch_handles_ping_sends_pong():
    client = MCPWebSocketClient("ws://localhost/ws")
    mock_ws = MagicMock()
    mock_ws.send = AsyncMock()
    client._ws = mock_ws
    client._connected = True

    ping_msg = json.dumps({"type": "ping"})

    async def message_iter():
        yield ping_msg
        client._connected = False

    mock_ws.__aiter__ = MagicMock(return_value=message_iter())

    await client._dispatch_messages()
    mock_ws.send.assert_called_once()
    sent = json.loads(mock_ws.send.call_args[0][0])
    assert sent["type"] == "pong"


@pytest.mark.asyncio
async def test_dispatch_routes_subscription_event():
    client = MCPWebSocketClient("ws://localhost/ws")
    mock_ws = MagicMock()
    client._ws = mock_ws
    client._connected = True

    queue: asyncio.Queue = asyncio.Queue()
    client._subscriptions["temp_feed"] = queue

    event_msg = json.dumps({"channel": "temp_feed", "data": {"temperature": 23.5}})

    async def message_iter():
        yield event_msg
        client._connected = False

    mock_ws.__aiter__ = MagicMock(return_value=message_iter())

    await client._dispatch_messages()
    assert not queue.empty()
    item = await queue.get()
    assert item == {"temperature": 23.5}


# ── 7. subscribe ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_subscribe_registers_queue():
    client = MCPWebSocketClient("ws://localhost/ws")
    mock_ws = MagicMock()
    mock_ws.send = AsyncMock()
    client._ws = mock_ws
    client._connected = True

    # Run subscribe and immediately put None to stop it
    async def run_subscribe():
        async for _ in client.subscribe("my_channel"):
            pass  # Should exit on None sentinel

    # Put sentinel in the queue after subscribe registers it
    async def put_sentinel():
        await asyncio.sleep(0.05)
        if "my_channel" in client._subscriptions:
            await client._subscriptions["my_channel"].put(None)

    await asyncio.gather(run_subscribe(), put_sentinel())

    # Queue should be cleaned up after exit
    assert "my_channel" not in client._subscriptions


@pytest.mark.asyncio
async def test_subscribe_sends_subscribe_message():
    client = MCPWebSocketClient("ws://localhost/ws")
    mock_ws = MagicMock()
    mock_ws.send = AsyncMock()
    client._ws = mock_ws

    async def run_subscribe():
        async for _ in client.subscribe("channel1", params={"key": "val"}):
            break

    async def trigger_stop():
        await asyncio.sleep(0.02)
        if "channel1" in client._subscriptions:
            await client._subscriptions["channel1"].put(None)

    await asyncio.gather(run_subscribe(), trigger_stop())

    # First send should be the subscribe message
    first_call = json.loads(mock_ws.send.call_args_list[0][0][0])
    assert first_call["type"] == "subscribe"
    assert first_call["channel"] == "channel1"


# ── 8. list_tools ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_tools_calls_call_tool():
    client = MCPWebSocketClient("ws://localhost/ws")
    mock_ws = _make_ws_mock()
    client._ws = mock_ws
    client._connected = True

    async def resolve():
        await asyncio.sleep(0)
        msg_id = str(client._msg_id)
        if msg_id in client._pending:
            client._pending[msg_id].set_result([{"name": "tool1"}])

    asyncio.create_task(resolve())
    result = await client.list_tools()
    assert result == [{"name": "tool1"}]


# ── 9. Reconnect constants ─────────────────────────────────────────────────────

def test_reconnect_constants():
    assert _RECONNECT_BASE == 1.0
    assert _RECONNECT_MAX == 60.0
    assert 0 < _RECONNECT_MAX
