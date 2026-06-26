"""Tests for MockMCPServer."""
from __future__ import annotations
import json
import pytest
from app.sdk.mock_server import MockMCPServer


# ── Test 1: tools/list returns registered tools ───────────────────────────────

@pytest.mark.asyncio
async def test_tools_list_returns_registered():
    server = MockMCPServer()
    server.register_tool("search", "Search the web", fixture={"results": []})
    server.register_tool("summarize", "Summarize text", fixture="Summary here")

    result = await server.handle_jsonrpc("tools/list", {})
    tools = result["tools"]
    assert len(tools) == 2
    names = [t["name"] for t in tools]
    assert "search" in names
    assert "summarize" in names


# ── Test 2: tools/call dispatches to fn ───────────────────────────────────────

@pytest.mark.asyncio
async def test_tools_call_dispatches_to_fn():
    server = MockMCPServer()

    def add(a: int, b: int) -> int:
        return a + b

    server.register_tool("add", "Add two numbers", fn=add)
    result = await server.handle_jsonrpc("tools/call", {"name": "add", "arguments": {"a": 3, "b": 4}})
    assert result["content"][0]["text"] == "7"


# ── Test 3: tools/call uses fixture response ──────────────────────────────────

@pytest.mark.asyncio
async def test_tools_call_uses_fixture_response():
    server = MockMCPServer()
    server.register_tool("get_user", "Get a user", fixture={"id": "u1", "name": "Alice"})

    result = await server.handle_jsonrpc("tools/call", {"name": "get_user", "arguments": {}})
    payload = json.loads(result["content"][0]["text"])
    assert payload["name"] == "Alice"


# ── Test 4: unknown tool returns error ────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_tool_returns_error():
    server = MockMCPServer()
    result = await server.handle_jsonrpc("tools/call", {"name": "nonexistent", "arguments": {}})
    assert "error" in result
    assert result["error"]["code"] == -32602
    assert "nonexistent" in result["error"]["message"]


# ── Test 5: unknown method returns error ──────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_method_returns_error():
    server = MockMCPServer()
    result = await server.handle_jsonrpc("tools/unknown_method", {})
    assert "error" in result
    assert result["error"]["code"] == -32601


# ── Test 6: enable_call_logging records calls ─────────────────────────────────

@pytest.mark.asyncio
async def test_enable_call_logging_records_calls():
    server = MockMCPServer()
    server.register_tool("ping", "Ping", fixture="pong")
    server.enable_call_logging()

    await server.handle_jsonrpc("tools/call", {"name": "ping", "arguments": {}})
    await server.handle_jsonrpc("tools/call", {"name": "ping", "arguments": {}})

    log = server.get_call_log()
    assert len(log) == 2
    assert log[0]["tool"] == "ping"
    assert log[1]["tool"] == "ping"


# ── Test 7: register_tool enables chaining ────────────────────────────────────

def test_register_tool_enables_chaining():
    server = MockMCPServer()
    result = (
        server
        .register_tool("a", "Tool A", fixture="a")
        .register_tool("b", "Tool B", fixture="b")
        .register_tool("c", "Tool C", fixture="c")
    )
    assert result is server
    assert len(server._tools) == 3


# ── Test 8: set_fixture on existing tool ─────────────────────────────────────

@pytest.mark.asyncio
async def test_set_fixture_on_existing_tool():
    server = MockMCPServer()
    server.register_tool("greet", "Greet user", fixture="Hello!")
    server.set_fixture("greet", "Hi there!")

    result = await server.handle_jsonrpc("tools/call", {"name": "greet", "arguments": {}})
    assert result["content"][0]["text"] == "Hi there!"


# ── Test 9: set_fixture on non-existing tool registers it ────────────────────

@pytest.mark.asyncio
async def test_set_fixture_on_nonexistent_tool_registers():
    server = MockMCPServer()
    server.set_fixture("new_tool", {"status": "ok"})

    result = await server.handle_jsonrpc("tools/call", {"name": "new_tool", "arguments": {}})
    payload = json.loads(result["content"][0]["text"])
    assert payload["status"] == "ok"


# ── Test 10: async fn works correctly ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_async_fn_works():
    server = MockMCPServer()

    async def async_add(x: int, y: int) -> dict:
        return {"sum": x + y}

    server.register_tool("async_add", "Async add", fn=async_add)
    result = await server.handle_jsonrpc(
        "tools/call", {"name": "async_add", "arguments": {"x": 10, "y": 5}}
    )
    payload = json.loads(result["content"][0]["text"])
    assert payload["sum"] == 15
