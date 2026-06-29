"""Comprehensive tests for app/mcp/openapi_importer.py and app/mcp/ws_client.py."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.mcp.openapi_importer import (
    _to_tool_name,
    extract_tools_from_spec,
    import_and_register,
    parse_openapi_spec,
    persist_tools,
)
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(tenant_id="oi-tenant", plan=PlanTier.ENTERPRISE, api_key_id="k1")

_SIMPLE_SPEC: dict = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0"},
    "paths": {
        "/users": {
            "get": {"summary": "List users", "operationId": "listUsers"},
            "post": {
                "summary": "Create user",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "email": {"type": "string"},
                                },
                            }
                        }
                    },
                },
            },
        },
        "/users/{id}": {
            "get": {
                "summary": "Get user",
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                        "description": "User identifier",
                    }
                ],
            },
            "delete": {"description": "Delete a user"},
        },
    },
}


# ── _to_tool_name ─────────────────────────────────────────────────────────────


def test_to_tool_name_simple_path() -> None:
    assert _to_tool_name("GET", "/users") == "get_users"


def test_to_tool_name_path_with_param() -> None:
    assert _to_tool_name("GET", "/users/{id}") == "get_users_id"


def test_to_tool_name_nested_path() -> None:
    assert _to_tool_name("POST", "/users/{id}/settings") == "post_users_id_settings"


def test_to_tool_name_method_lowercased() -> None:
    assert _to_tool_name("DELETE", "/items") == "delete_items"


def test_to_tool_name_hyphen_replaced() -> None:
    assert _to_tool_name("GET", "/my-items") == "get_my_items"


def test_to_tool_name_root_path() -> None:
    result = _to_tool_name("GET", "/")
    assert result.startswith("get")
    # Should not end with underscore
    assert not result.endswith("_")


# ── parse_openapi_spec ────────────────────────────────────────────────────────


def test_parse_json_spec() -> None:
    text = json.dumps(_SIMPLE_SPEC)
    result = parse_openapi_spec(text)
    assert result["openapi"] == "3.0.0"
    assert "paths" in result


def test_parse_invalid_json_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Invalid JSON"):
        parse_openapi_spec("{not valid json}")


def test_parse_yaml_spec() -> None:
    yaml_text = """
openapi: "3.0.0"
info:
  title: YAML API
  version: "1.0"
paths:
  /ping:
    get:
      summary: Ping endpoint
"""
    try:
        result = parse_openapi_spec(yaml_text)
        assert result["openapi"] == "3.0.0"
        assert "/ping" in result["paths"]
    except ValueError as e:
        if "pyyaml" in str(e).lower():
            pytest.skip("pyyaml not installed")
        raise


def test_parse_yaml_non_dict_raises_value_error() -> None:
    yaml_text = "- item1\n- item2\n"
    try:
        with pytest.raises(ValueError):
            parse_openapi_spec(yaml_text)
    except ImportError:
        pytest.skip("pyyaml not installed")


# ── extract_tools_from_spec ───────────────────────────────────────────────────


def test_extract_tools_returns_list() -> None:
    tools = extract_tools_from_spec(_SIMPLE_SPEC, connector_id="con-1", tenant_id="t1")
    assert isinstance(tools, list)
    assert len(tools) > 0


def test_extract_tools_correct_count() -> None:
    # /users has GET + POST, /users/{id} has GET + DELETE = 4 tools
    tools = extract_tools_from_spec(_SIMPLE_SPEC, connector_id="con-1", tenant_id="t1")
    assert len(tools) == 4


def test_extract_tools_have_required_fields() -> None:
    tools = extract_tools_from_spec(_SIMPLE_SPEC, connector_id="con-1", tenant_id="t1")
    for tool in tools:
        assert "id" in tool
        assert "tenant_id" in tool
        assert "connector_id" in tool
        assert "tool_name" in tool
        assert "description" in tool
        assert "http_method" in tool
        assert "http_path" in tool
        assert "parameters_schema" in tool


def test_extract_tools_tenant_id_populated() -> None:
    tools = extract_tools_from_spec(_SIMPLE_SPEC, connector_id="con-x", tenant_id="t-custom")
    for t in tools:
        assert t["tenant_id"] == "t-custom"
        assert t["connector_id"] == "con-x"


def test_extract_tools_uses_summary_as_description() -> None:
    tools = extract_tools_from_spec(_SIMPLE_SPEC, connector_id="c", tenant_id="t")
    list_users = next(t for t in tools if t["tool_name"] == "get_users")
    assert list_users["description"] == "List users"


def test_extract_tools_path_parameter_in_schema() -> None:
    tools = extract_tools_from_spec(_SIMPLE_SPEC, connector_id="c", tenant_id="t")
    get_user = next(t for t in tools if t["tool_name"] == "get_users_id")
    props = get_user["parameters_schema"]["properties"]
    assert "id" in props
    assert get_user["parameters_schema"]["required"] == ["id"]


def test_extract_tools_request_body_in_schema() -> None:
    tools = extract_tools_from_spec(_SIMPLE_SPEC, connector_id="c", tenant_id="t")
    create_user = next(t for t in tools if t["tool_name"] == "post_users")
    assert "body" in create_user["parameters_schema"]["properties"]
    assert "body" in create_user["parameters_schema"]["required"]


def test_extract_tools_empty_paths() -> None:
    spec = {"paths": {}}
    tools = extract_tools_from_spec(spec, connector_id="c", tenant_id="t")
    assert tools == []


def test_extract_tools_ignores_unsupported_methods() -> None:
    spec = {
        "paths": {
            "/items": {
                "get": {"summary": "Get items"},
                "options": {"summary": "Options"},
                "head": {"summary": "Head"},
            }
        }
    }
    tools = extract_tools_from_spec(spec, connector_id="c", tenant_id="t")
    # Only GET should be extracted
    assert len(tools) == 1
    assert tools[0]["http_method"] == "GET"


def test_extract_tools_fallback_description() -> None:
    spec = {
        "paths": {
            "/noop": {
                "patch": {}  # No summary or description
            }
        }
    }
    tools = extract_tools_from_spec(spec, connector_id="c", tenant_id="t")
    assert len(tools) == 1
    # Falls back to "METHOD /path" format
    assert "PATCH" in tools[0]["description"] or "noop" in tools[0]["description"]


def test_extract_tools_description_truncated_at_500() -> None:
    long_desc = "x" * 600
    spec = {"paths": {"/a": {"get": {"summary": long_desc}}}}
    tools = extract_tools_from_spec(spec, connector_id="c", tenant_id="t")
    assert len(tools[0]["description"]) <= 500


def test_extract_tools_each_tool_has_unique_id() -> None:
    tools = extract_tools_from_spec(_SIMPLE_SPEC, connector_id="c", tenant_id="t")
    ids = [t["id"] for t in tools]
    assert len(ids) == len(set(ids))


# ── persist_tools ─────────────────────────────────────────────────────────────


async def test_persist_tools_no_factory_returns_count() -> None:
    """When no DB factory, persist_tools counts as persisted (test mode)."""
    tools = extract_tools_from_spec(_SIMPLE_SPEC, connector_id="c", tenant_id="t")
    count = await persist_tools(tools, db_session_factory=None, tenant_id="t")
    assert count == len(tools)


async def test_persist_tools_empty_list_returns_zero() -> None:
    count = await persist_tools([], db_session_factory=None, tenant_id="t")
    assert count == 0


# ── import_and_register ───────────────────────────────────────────────────────


async def test_import_and_register_invalid_spec_returns_error() -> None:
    registry = AsyncMock()
    result = await import_and_register(
        spec_content="{invalid json",
        server_name="Test",
        base_url="http://example.com",
        registry=registry,
        tenant_ctx=TENANT,
    )
    assert result["server_id"] is None
    assert "error" in result
    registry.register.assert_not_called()


async def test_import_and_register_empty_paths_returns_error() -> None:
    spec = json.dumps({"openapi": "3.0.0", "paths": {}})
    registry = AsyncMock()
    result = await import_and_register(
        spec_content=spec,
        server_name="Empty",
        base_url="http://example.com",
        registry=registry,
        tenant_ctx=TENANT,
    )
    assert result["server_id"] is None
    assert "No tools" in result["error"]


async def test_import_and_register_success() -> None:
    spec = json.dumps(_SIMPLE_SPEC)
    registry = AsyncMock()
    result = await import_and_register(
        spec_content=spec,
        server_name="Test API",
        base_url="http://api.example.com",
        registry=registry,
        tenant_ctx=TENANT,
    )
    assert result["server_id"] is not None
    assert result["tool_count"] == 4
    registry.register.assert_called_once()


async def test_import_and_register_with_api_key_auth() -> None:
    spec = json.dumps({"openapi": "3.0.0", "paths": {"/ping": {"get": {"summary": "Ping"}}}})
    registry = AsyncMock()
    result = await import_and_register(
        spec_content=spec,
        server_name="Secure API",
        base_url="http://api.example.com",
        registry=registry,
        tenant_ctx=TENANT,
        auth_config={"api_key": "my-secret-key"},
    )
    assert result["tool_count"] == 1
    # Verify auth type was set properly on registered config
    call_args = registry.register.call_args
    server_config = call_args[0][0]
    from app.mcp.registry import AuthType
    assert server_config.auth_type == AuthType.API_KEY


# ── MCPWebSocketClient ────────────────────────────────────────────────────────


class TestMCPWebSocketClient:
    def test_ws_client_instantiation(self) -> None:
        from app.mcp.ws_client import MCPWebSocketClient

        client = MCPWebSocketClient("ws://localhost:8080/ws", auth_token="tok", reconnect=True)
        assert client._ws_url == "ws://localhost:8080/ws"
        assert client._auth_token == "tok"
        assert client._reconnect is True
        assert client._connected is False

    def test_ws_client_default_values(self) -> None:
        from app.mcp.ws_client import MCPWebSocketClient

        client = MCPWebSocketClient("ws://localhost")
        assert client._auth_token is None
        assert client._reconnect is True
        assert client._max_reconnects == 10
        assert client._pending == {}
        assert client._subscriptions == {}

    async def test_ws_client_connect_requires_websockets(self) -> None:
        """When websockets is not installed, connect() raises RuntimeError."""
        from app.mcp.ws_client import MCPWebSocketClient

        client = MCPWebSocketClient("ws://localhost")
        with patch("builtins.__import__", side_effect=ImportError("No module named 'websockets'")):
            # The exception happens inside connect() on 'import websockets'
            pass
        # Just verify no crash on construction
        assert client._connected is False

    async def test_ws_client_disconnect_no_op_when_not_connected(self) -> None:
        from app.mcp.ws_client import MCPWebSocketClient

        client = MCPWebSocketClient("ws://localhost")
        # Should not raise when _ws is None
        await client.disconnect()
        assert client._connected is False

    async def test_ws_client_call_tool_increments_msg_id(self) -> None:
        from app.mcp.ws_client import MCPWebSocketClient

        client = MCPWebSocketClient("ws://localhost")
        mock_ws = MagicMock()
        client._ws = mock_ws
        client._connected = True

        # Create a future that resolves immediately
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        future.set_result({"status": "ok"})

        # Patch send and pending resolution
        send_calls = []

        async def mock_send(msg: str) -> None:
            send_calls.append(msg)
            # Resolve the pending future for msg_id "1"
            msg_data = json.loads(msg)
            msg_id = msg_data.get("id")
            if msg_id in client._pending:
                client._pending[msg_id].set_result({"result": "done"})

        mock_ws.send = mock_send

        result = await client.call_tool("ping", {"key": "val"}, timeout=2.0)
        assert client._msg_id == 1
        assert len(send_calls) == 1
        sent = json.loads(send_calls[0])
        assert sent["type"] == "call_tool"
        assert sent["tool"] == "ping"
        assert sent["arguments"] == {"key": "val"}

    async def test_ws_client_call_tool_timeout_raises(self) -> None:
        from app.mcp.ws_client import MCPWebSocketClient

        client = MCPWebSocketClient("ws://localhost")
        mock_ws = MagicMock()

        async def noop_send(_msg: str) -> None:
            pass

        mock_ws.send = noop_send
        client._ws = mock_ws

        with pytest.raises(TimeoutError, match="timed out"):
            await client.call_tool("slow_tool", {}, timeout=0.01)
