"""Additional coverage for app/mcp/client.py — the 40% not yet covered.

Covers:
  - _accepts_tenant_context() static method (various resolver signatures)
  - _build_auth_headers() for all auth_types: bearer, api_key, basic, custom_header, oauth
  - _dispatch_openapi_tool() with GET and POST methods
  - _dispatch_builtin_tool() success and error paths
  - call_tool() with empty server_id / empty tool_name
  - call_tool() server-not-found fallback (scans OpenAPI tools)
  - call_tool() HTTP 4xx/5xx error returns ToolCallResult with success=False
  - call_tool() general exception handling
  - _update_tool_stats() no-ops when db is None
  - discover_tools() with MCP endpoint (JSON-RPC path)
  - discover_tools() with result.tools nested structure
  - discover_all_tools() happy path
  - CircuitBreakerOpenError propagation
"""
from __future__ import annotations

import base64
import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.mcp.client import (
    CircuitBreakerOpenError,
    MCPClient,
    ToolCallResult,
    ToolDefinition,
    _is_mcp_endpoint,
    _jsonrpc,
)
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="tenant-1", plan=PlanTier.ENTERPRISE, api_key_id="k1")


def _make_registry(cfg: MCPServerConfig | None = None) -> MCPRegistry:
    registry = MagicMock(spec=MCPRegistry)
    registry.get = AsyncMock(return_value=cfg)
    registry.list_all = AsyncMock(return_value=[])
    registry.list_server_records = AsyncMock(return_value=[])
    return registry


def _make_cfg(**kwargs: Any) -> MCPServerConfig:
    defaults: dict[str, Any] = {
        "server_id": "srv-1",
        "name": "Test Server",
        "url": "https://example.com/tools",
        "auth_type": "none",
        "auth_config": {},
        "builtin_handler": None,
        "tool_definitions": [],
        "base_url": "",
    }
    defaults.update(kwargs)
    return MCPServerConfig(**defaults)


# ---------------------------------------------------------------------------
# _is_mcp_endpoint
# ---------------------------------------------------------------------------


def test_is_mcp_endpoint_true() -> None:
    assert _is_mcp_endpoint("https://srv.example.com/mcp") is True
    assert _is_mcp_endpoint("https://srv.example.com/mcp/") is True


def test_is_mcp_endpoint_false() -> None:
    assert _is_mcp_endpoint("https://srv.example.com/tools") is False
    assert _is_mcp_endpoint("https://srv.example.com") is False


# ---------------------------------------------------------------------------
# _jsonrpc helper
# ---------------------------------------------------------------------------


def test_jsonrpc_structure_with_params() -> None:
    payload = _jsonrpc("tools/list", {"cursor": None})
    assert payload["jsonrpc"] == "2.0"
    assert payload["method"] == "tools/list"
    assert "id" in payload
    assert payload["params"] == {"cursor": None}


def test_jsonrpc_structure_no_params() -> None:
    payload = _jsonrpc("tools/list")
    assert "params" not in payload


# ---------------------------------------------------------------------------
# _accepts_tenant_context static method
# ---------------------------------------------------------------------------


def test_accepts_tenant_context_two_positional_args() -> None:
    def resolver(ref: str, tenant_ctx: Any) -> str:
        return ""

    assert MCPClient._accepts_tenant_context(resolver) is True


def test_accepts_tenant_context_named_tenant_ctx() -> None:
    def resolver(ref: str, *, tenant_ctx: Any = None) -> str:
        return ""

    assert MCPClient._accepts_tenant_context(resolver) is True


def test_accepts_tenant_context_single_arg_returns_false() -> None:
    def resolver(ref: str) -> str:
        return ""

    assert MCPClient._accepts_tenant_context(resolver) is False


def test_accepts_tenant_context_varargs_returns_true() -> None:
    def resolver(*args: Any) -> str:
        return ""

    assert MCPClient._accepts_tenant_context(resolver) is True


def test_accepts_tenant_context_bad_callable() -> None:
    # A builtin without an inspectable signature
    assert MCPClient._accepts_tenant_context(len) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _build_auth_headers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_auth_headers_bearer() -> None:
    cfg = _make_cfg(auth_type="bearer", auth_config={"token": "my-token"})
    client = MCPClient(registry=_make_registry())
    headers = await client._build_auth_headers(cfg, tenant_ctx=T)
    assert headers["Authorization"] == "Bearer my-token"


@pytest.mark.asyncio
async def test_build_auth_headers_api_key_default_header() -> None:
    cfg = _make_cfg(auth_type="api_key", auth_config={"api_key": "secret-123"})
    client = MCPClient(registry=_make_registry())
    headers = await client._build_auth_headers(cfg, tenant_ctx=T)
    assert headers["X-API-Key"] == "secret-123"


@pytest.mark.asyncio
async def test_build_auth_headers_api_key_custom_header() -> None:
    cfg = _make_cfg(
        auth_type="api_key",
        auth_config={"header_name": "X-Custom-Key", "api_key": "val"},
    )
    client = MCPClient(registry=_make_registry())
    headers = await client._build_auth_headers(cfg, tenant_ctx=T)
    assert headers["X-Custom-Key"] == "val"


@pytest.mark.asyncio
async def test_build_auth_headers_basic() -> None:
    cfg = _make_cfg(
        auth_type="basic",
        auth_config={"username": "user", "password": "pass"},
    )
    client = MCPClient(registry=_make_registry())
    headers = await client._build_auth_headers(cfg, tenant_ctx=T)
    expected = base64.b64encode(b"user:pass").decode()
    assert headers["Authorization"] == f"Basic {expected}"


@pytest.mark.asyncio
async def test_build_auth_headers_none_returns_empty() -> None:
    cfg = _make_cfg(auth_type="none", auth_config={})
    client = MCPClient(registry=_make_registry())
    headers = await client._build_auth_headers(cfg, tenant_ctx=T)
    assert headers == {}


@pytest.mark.asyncio
async def test_build_auth_headers_custom_header() -> None:
    cfg = _make_cfg(
        auth_type="custom_header",
        auth_config={"X-Service-Token": "tok-xyz", "X-Version": "2"},
    )
    client = MCPClient(registry=_make_registry())
    headers = await client._build_auth_headers(cfg, tenant_ctx=T)
    assert headers["X-Service-Token"] == "tok-xyz"
    assert headers["X-Version"] == "2"


@pytest.mark.asyncio
async def test_build_auth_headers_oauth_without_manager_returns_empty() -> None:
    cfg = _make_cfg(auth_type="oauth_ac", auth_config={})
    client = MCPClient(registry=_make_registry())
    client._oauth_manager = None
    headers = await client._build_auth_headers(cfg, tenant_ctx=T)
    assert "Authorization" not in headers


@pytest.mark.asyncio
async def test_build_auth_headers_oauth_with_manager_injects_bearer() -> None:
    cfg = _make_cfg(auth_type="pkce", auth_config={})
    mock_token = MagicMock()
    mock_token.is_expired.return_value = False
    mock_token.access_token = "access-token-xyz"

    mock_oauth = MagicMock()
    mock_oauth.get_token = MagicMock(return_value=mock_token)

    client = MCPClient(registry=_make_registry())
    client._oauth_manager = mock_oauth
    headers = await client._build_auth_headers(cfg, tenant_ctx=T, server_id="srv-1")
    assert headers["Authorization"] == "Bearer access-token-xyz"


# ---------------------------------------------------------------------------
# _dispatch_builtin_tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_builtin_tool_success() -> None:
    async def my_handler(tool_name: str, args: dict) -> dict:
        return {"result": "ok", "tool": tool_name}

    cfg = _make_cfg(builtin_handler=my_handler)
    client = MCPClient(registry=_make_registry())
    result = await client._dispatch_builtin_tool(cfg, "my_tool", {"x": 1})
    assert result.success is True
    assert result.output["result"] == "ok"


@pytest.mark.asyncio
async def test_dispatch_builtin_tool_no_handler() -> None:
    cfg = _make_cfg(builtin_handler=None)
    client = MCPClient(registry=_make_registry())
    result = await client._dispatch_builtin_tool(cfg, "my_tool", {})
    assert result.success is False
    assert "Built-in handler not available" in result.error


@pytest.mark.asyncio
async def test_dispatch_builtin_tool_handler_raises() -> None:
    async def bad_handler(tool_name: str, args: dict) -> dict:
        raise ValueError("boom")

    cfg = _make_cfg(builtin_handler=bad_handler)
    client = MCPClient(registry=_make_registry())
    result = await client._dispatch_builtin_tool(cfg, "my_tool", {})
    assert result.success is False
    assert "boom" in result.error


# ---------------------------------------------------------------------------
# _dispatch_openapi_tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_openapi_tool_get_success() -> None:
    cfg = _make_cfg(url="https://api.example.com", base_url="https://api.example.com")
    tool_def = {"name": "list_items", "http_method": "GET", "http_path": "/items"}

    mock_response = MagicMock()
    mock_response.json.return_value = {"items": [1, 2, 3]}
    mock_response.raise_for_status = MagicMock()

    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client_ctx)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_client_ctx.get = AsyncMock(return_value=mock_response)

    client = MCPClient(registry=_make_registry())
    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await client._dispatch_openapi_tool(cfg, tool_def, {"limit": 10})

    assert result.success is True
    assert result.output == {"items": [1, 2, 3]}


@pytest.mark.asyncio
async def test_dispatch_openapi_tool_post_success() -> None:
    cfg = _make_cfg(url="https://api.example.com", base_url="https://api.example.com")
    tool_def = {"name": "create_item", "http_method": "POST", "http_path": "/items"}

    mock_response = MagicMock()
    mock_response.json.return_value = {"id": "new-item-id"}
    mock_response.raise_for_status = MagicMock()

    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client_ctx)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_client_ctx.request = AsyncMock(return_value=mock_response)

    client = MCPClient(registry=_make_registry())
    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await client._dispatch_openapi_tool(cfg, tool_def, {"name": "item"})

    assert result.success is True
    assert result.output == {"id": "new-item-id"}


@pytest.mark.asyncio
async def test_dispatch_openapi_tool_http_error() -> None:
    cfg = _make_cfg(url="https://api.example.com", base_url="https://api.example.com")
    tool_def = {"name": "bad_tool", "http_method": "POST", "http_path": "/bad"}

    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client_ctx)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_client_ctx.request = AsyncMock(side_effect=RuntimeError("connection refused"))

    client = MCPClient(registry=_make_registry())
    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await client._dispatch_openapi_tool(cfg, tool_def, {})

    assert result.success is False
    assert "connection refused" in result.error


# ---------------------------------------------------------------------------
# call_tool — input validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_empty_server_id_returns_error() -> None:
    client = MCPClient(registry=_make_registry())
    result = await client.call_tool(
        server_id="", tool_name="my_tool", arguments={}, tenant_ctx=T
    )
    assert result.success is False
    assert "server_id" in result.error.lower()


@pytest.mark.asyncio
async def test_call_tool_empty_tool_name_returns_error() -> None:
    client = MCPClient(registry=_make_registry())
    result = await client.call_tool(
        server_id="srv-1", tool_name="", arguments={}, tenant_ctx=T
    )
    assert result.success is False
    assert "tool_name" in result.error.lower()


# ---------------------------------------------------------------------------
# call_tool — server not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_server_not_found_returns_error() -> None:
    registry = _make_registry(cfg=None)
    registry.list_all = AsyncMock(return_value=[])
    client = MCPClient(registry=registry)
    result = await client.call_tool(
        server_id="missing-srv", tool_name="my_tool", arguments={}, tenant_ctx=T
    )
    assert result.success is False
    assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_call_tool_falls_back_to_openapi_scan_when_server_missing() -> None:
    """If server_id not found, scan all registered servers for matching OpenAPI tool."""
    fallback_cfg = _make_cfg(
        server_id="openapi-srv",
        url="https://api.example.com",
        base_url="https://api.example.com",
        tool_definitions=[
            {"name": "search_items", "tool_name": "search_items", "http_method": "GET", "http_path": "/search"}
        ],
    )

    registry = MagicMock(spec=MCPRegistry)
    registry.get = AsyncMock(return_value=None)  # server not found
    registry.list_all = AsyncMock(return_value=[fallback_cfg])

    mock_response = MagicMock()
    mock_response.json.return_value = {"results": []}
    mock_response.raise_for_status = MagicMock()

    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client_ctx)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_client_ctx.get = AsyncMock(return_value=mock_response)

    client = MCPClient(registry=registry)
    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await client.call_tool(
            server_id="missing-srv", tool_name="search_items", arguments={}, tenant_ctx=T
        )

    assert result.success is True


# ---------------------------------------------------------------------------
# call_tool — HTTP errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_http_status_error_returns_failure() -> None:
    cfg = _make_cfg(url="https://api.example.com")
    registry = _make_registry(cfg=cfg)

    # Simulate HTTPStatusError from httpx
    mock_http_response = MagicMock()
    mock_http_response.status_code = 503
    mock_http_response.text = "Service Unavailable"

    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client_ctx)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_client_ctx.post = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "503", request=MagicMock(), response=mock_http_response
        )
    )

    client = MCPClient(registry=registry)
    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await client.call_tool(
            server_id="srv-1", tool_name="do_thing", arguments={}, tenant_ctx=T
        )

    assert result.success is False
    assert "503" in result.error


@pytest.mark.asyncio
async def test_call_tool_generic_exception_returns_failure() -> None:
    cfg = _make_cfg(url="https://api.example.com")
    registry = _make_registry(cfg=cfg)

    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client_ctx)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_client_ctx.post = AsyncMock(side_effect=RuntimeError("unexpected failure"))

    client = MCPClient(registry=registry)
    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await client.call_tool(
            server_id="srv-1", tool_name="do_thing", arguments={}, tenant_ctx=T
        )

    assert result.success is False
    assert "unexpected failure" in result.error


# ---------------------------------------------------------------------------
# call_tool — circuit breaker propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_circuit_breaker_open_raises() -> None:
    cfg = _make_cfg()
    registry = _make_registry(cfg=cfg)

    mock_cb = AsyncMock()
    mock_cb.can_call_async = AsyncMock(side_effect=CircuitBreakerOpenError("circuit open"))

    client = MCPClient(registry=registry)
    client._circuit_breakers["tenant-1:srv-1"] = mock_cb

    with pytest.raises(CircuitBreakerOpenError):
        await client.call_tool(
            server_id="srv-1", tool_name="my_tool", arguments={}, tenant_ctx=T
        )


# ---------------------------------------------------------------------------
# _update_tool_stats — no-op when db is None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_tool_stats_noop_without_db() -> None:
    client = MCPClient(registry=_make_registry())
    # Should complete without raising
    await client._update_tool_stats("srv-1", "my_tool", "tenant-1", success=True, latency_ms=50.0, db=None)


# ---------------------------------------------------------------------------
# discover_tools — MCP JSON-RPC endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_tools_jsonrpc_endpoint() -> None:
    cfg = _make_cfg(url="https://srv.example.com/mcp")
    registry = _make_registry(cfg=cfg)

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "jsonrpc": "2.0",
        "id": "1",
        "result": {
            "tools": [
                {"name": "list_files", "description": "List files", "inputSchema": {"type": "object"}}
            ]
        },
    }
    mock_response.raise_for_status = MagicMock()

    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client_ctx)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_client_ctx.post = AsyncMock(return_value=mock_response)

    client = MCPClient(registry=registry)
    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        tools = await client.discover_tools(server_id="srv-1", tenant_ctx=T)

    assert len(tools) == 1
    assert tools[0].name == "list_files"
    assert tools[0].server_id == "srv-1"


@pytest.mark.asyncio
async def test_discover_tools_returns_empty_for_missing_server() -> None:
    registry = _make_registry(cfg=None)
    client = MCPClient(registry=registry)
    tools = await client.discover_tools(server_id="ghost-srv", tenant_ctx=T)
    assert tools == []


@pytest.mark.asyncio
async def test_discover_tools_returns_empty_for_empty_server_id() -> None:
    client = MCPClient(registry=_make_registry())
    tools = await client.discover_tools(server_id="", tenant_ctx=T)
    assert tools == []


@pytest.mark.asyncio
async def test_discover_tools_handles_http_exception_gracefully() -> None:
    cfg = _make_cfg(url="https://dead.example.com/tools")
    registry = _make_registry(cfg=cfg)

    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client_ctx)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_client_ctx.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

    client = MCPClient(registry=registry)
    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        tools = await client.discover_tools(server_id="dead-srv", tenant_ctx=T)

    assert tools == []


# ---------------------------------------------------------------------------
# discover_tools — flat list response (non-JSONRPC)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_tools_flat_list_response() -> None:
    cfg = _make_cfg(url="https://srv.example.com/tools")
    registry = _make_registry(cfg=cfg)

    mock_response = MagicMock()
    mock_response.json.return_value = [
        {"name": "ping", "description": "Ping the server", "inputSchema": {}},
        {"name": "status", "description": "Get status", "inputSchema": {}},
    ]
    mock_response.raise_for_status = MagicMock()

    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client_ctx)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_client_ctx.get = AsyncMock(return_value=mock_response)

    client = MCPClient(registry=registry)
    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        tools = await client.discover_tools(server_id="srv-1", tenant_ctx=T)

    assert len(tools) == 2
    assert {t.name for t in tools} == {"ping", "status"}


# ---------------------------------------------------------------------------
# discover_all_tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_all_tools_returns_empty_when_no_servers() -> None:
    registry = MagicMock(spec=MCPRegistry)
    registry.list_server_records = AsyncMock(return_value=[])
    client = MCPClient(registry=registry)
    tools = await client.discover_all_tools(tenant_ctx=T)
    assert tools == []


@pytest.mark.asyncio
async def test_discover_all_tools_aggregates_multiple_servers() -> None:
    cfg1 = _make_cfg(server_id="srv-a", url="https://a.example.com/tools")
    cfg2 = _make_cfg(server_id="srv-b", url="https://b.example.com/tools")

    registry = MagicMock(spec=MCPRegistry)
    registry.list_server_records = AsyncMock(return_value=[("srv-a", {}), ("srv-b", {})])
    registry.get = AsyncMock(side_effect=lambda sid, tenant_ctx: (
        cfg1 if sid == "srv-a" else cfg2
    ))

    call_count = [0]

    async def fake_get(*args, **kwargs):
        call_count[0] += 1
        m = MagicMock()
        m.json.return_value = [{"name": f"tool_{call_count[0]}", "description": "t", "inputSchema": {}}]
        m.raise_for_status = MagicMock()
        return m

    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client_ctx)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_client_ctx.get = fake_get

    client = MCPClient(registry=registry)
    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        tools = await client.discover_all_tools(tenant_ctx=T)

    assert len(tools) == 2


# ---------------------------------------------------------------------------
# JSON-RPC error response handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_jsonrpc_error_in_response() -> None:
    """If the MCP server returns a JSON-RPC error object, call_tool returns failure."""
    cfg = _make_cfg(url="https://srv.example.com/mcp")
    registry = _make_registry(cfg=cfg)

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "jsonrpc": "2.0",
        "id": "1",
        "error": {"code": -32601, "message": "Method not found"},
    }
    mock_response.raise_for_status = MagicMock()

    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client_ctx)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_client_ctx.post = AsyncMock(return_value=mock_response)

    client = MCPClient(registry=registry)
    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        result = await client.call_tool(
            server_id="srv-1", tool_name="missing_method", arguments={}, tenant_ctx=T
        )

    assert result.success is False
    assert "Method not found" in result.error or "-32601" in result.error


# ---------------------------------------------------------------------------
# non-dict arguments are coerced to {}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_nonedict_arguments_coerced() -> None:
    async def handler(tool: str, args: dict) -> dict:
        assert isinstance(args, dict)
        return {"ok": True}

    cfg = _make_cfg(builtin_handler=handler)
    registry = _make_registry(cfg=cfg)
    client = MCPClient(registry=registry)
    result = await client.call_tool(
        server_id="srv-1", tool_name="my_tool",
        arguments="not-a-dict",  # type: ignore[arg-type]
        tenant_ctx=T,
    )
    assert result.success is True
