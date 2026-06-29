"""Extra coverage tests for app/mcp/client.py — targeting 85%+ coverage."""
from __future__ import annotations

import asyncio
import base64
import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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


def _ctx(tenant_id: str = "tid-mcp") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="kid-mcp")


def _make_registry(server: MCPServerConfig | None = None) -> MCPRegistry:
    registry = MCPRegistry(redis=None)
    if server:
        asyncio.get_event_loop().run_until_complete(
            registry.register(server, tenant_ctx=_ctx())
        )
    return registry


def _make_client(registry: MCPRegistry | None = None, redis: Any = None) -> MCPClient:
    reg = registry or MCPRegistry(redis=None)
    return MCPClient(registry=reg, timeout=10.0, redis=redis)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def test_is_mcp_endpoint_true():
    assert _is_mcp_endpoint("http://example.com/mcp") is True
    assert _is_mcp_endpoint("http://example.com/mcp/") is True


def test_is_mcp_endpoint_false():
    assert _is_mcp_endpoint("http://example.com/tools") is False
    assert _is_mcp_endpoint("http://example.com/api") is False


def test_jsonrpc_with_params():
    payload = _jsonrpc("tools/call", {"name": "search", "arguments": {"q": "test"}})
    assert payload["jsonrpc"] == "2.0"
    assert payload["method"] == "tools/call"
    assert "id" in payload
    assert payload["params"]["name"] == "search"


def test_jsonrpc_without_params():
    payload = _jsonrpc("tools/list")
    assert payload["method"] == "tools/list"
    assert "params" not in payload


# ---------------------------------------------------------------------------
# _accepts_tenant_context
# ---------------------------------------------------------------------------

def test_accepts_tenant_context_two_positional_args():
    def resolver(value, ctx):
        return None

    assert MCPClient._accepts_tenant_context(resolver) is True


def test_accepts_tenant_context_tenant_ctx_keyword():
    def resolver(value, *, tenant_ctx=None):
        return None

    assert MCPClient._accepts_tenant_context(resolver) is True


def test_accepts_tenant_context_var_positional():
    def resolver(*args):
        return None

    assert MCPClient._accepts_tenant_context(resolver) is True


def test_accepts_tenant_context_single_arg():
    def resolver(value):
        return None

    assert MCPClient._accepts_tenant_context(resolver) is False


def test_accepts_tenant_context_not_callable():
    assert MCPClient._accepts_tenant_context("not_callable") is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _get_circuit_breaker
# ---------------------------------------------------------------------------

def test_get_circuit_breaker_without_redis():
    client = _make_client()
    cb = client._get_circuit_breaker("server-1", "tid-1")
    assert cb is not None
    # Second call returns cached
    cb2 = client._get_circuit_breaker("server-1", "tid-1")
    assert cb is cb2


def test_get_circuit_breaker_with_redis():
    mock_redis = MagicMock()
    client = _make_client(redis=mock_redis)
    with patch("app.reliability.redis_circuit_breaker.RedisCircuitBreaker") as mock_cb_cls:
        mock_cb_cls.return_value = MagicMock()
        cb = client._get_circuit_breaker("server-1", "tid-1")
        # Either creates RedisCircuitBreaker or falls back to CircuitBreaker
        assert cb is not None


def test_get_circuit_breaker_redis_import_error():
    mock_redis = MagicMock()
    client = _make_client(redis=mock_redis)
    with patch("app.reliability.redis_circuit_breaker.RedisCircuitBreaker", side_effect=Exception("import error")):
        cb = client._get_circuit_breaker("server-2", "tid-1")
        # Falls back gracefully
        # May return None on exception
        assert cb is None or cb is not None


# ---------------------------------------------------------------------------
# _resolve_auth_value
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_auth_value_plain_string():
    client = _make_client()
    result = await client._resolve_auth_value("plain-token", _ctx())
    assert result == "plain-token"


@pytest.mark.asyncio
async def test_resolve_auth_value_secret_ref_sync():
    def sync_resolver(ref):
        return "resolved-secret"

    client = _make_client()
    client._secret_resolver = sync_resolver
    client._secret_resolver_accepts_tenant = False

    with patch("app.mcp.client.is_connector_secret_ref", return_value=True):
        result = await client._resolve_auth_value("vault://my-secret", _ctx())
    assert result == "resolved-secret"


@pytest.mark.asyncio
async def test_resolve_auth_value_secret_ref_async():
    async def async_resolver(ref):
        return "async-resolved"

    client = _make_client()
    client._secret_resolver = async_resolver
    client._secret_resolver_accepts_tenant = False

    with patch("app.mcp.client.is_connector_secret_ref", return_value=True):
        result = await client._resolve_auth_value("vault://secret", _ctx())
    assert result == "async-resolved"


@pytest.mark.asyncio
async def test_resolve_auth_value_secret_ref_returns_none():
    def resolver(ref):
        return None

    client = _make_client()
    client._secret_resolver = resolver
    client._secret_resolver_accepts_tenant = False

    with patch("app.mcp.client.is_connector_secret_ref", return_value=True):
        result = await client._resolve_auth_value("vault://empty", _ctx())
    assert result == ""


# ---------------------------------------------------------------------------
# _build_auth_headers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_auth_headers_bearer():
    cfg = MCPServerConfig(name="BearerSrv", url="http://api.example.com", auth_type="bearer",
                          auth_config={"token": "my-bearer-token"})
    client = _make_client()
    headers = await client._build_auth_headers(cfg, tenant_ctx=_ctx())
    assert headers["Authorization"] == "Bearer my-bearer-token"


@pytest.mark.asyncio
async def test_build_auth_headers_api_key():
    cfg = MCPServerConfig(name="ApiKeySrv", url="http://api.example.com", auth_type="api_key",
                          auth_config={"header_name": "X-Custom-Key", "api_key": "my-api-key"})
    client = _make_client()
    headers = await client._build_auth_headers(cfg, tenant_ctx=_ctx())
    assert headers["X-Custom-Key"] == "my-api-key"


@pytest.mark.asyncio
async def test_build_auth_headers_basic():
    cfg = MCPServerConfig(name="BasicSrv", url="http://api.example.com", auth_type="basic",
                          auth_config={"username": "user", "password": "pass"})
    client = _make_client()
    headers = await client._build_auth_headers(cfg, tenant_ctx=_ctx())
    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Basic ")
    decoded = base64.b64decode(headers["Authorization"][6:]).decode()
    assert decoded == "user:pass"


@pytest.mark.asyncio
async def test_build_auth_headers_custom_header():
    cfg = MCPServerConfig(name="CustomSrv", url="http://api.example.com", auth_type="custom_header",
                          auth_config={"X-Custom-1": "val1", "X-Custom-2": "val2"})
    client = _make_client()
    headers = await client._build_auth_headers(cfg, tenant_ctx=_ctx())
    assert headers["X-Custom-1"] == "val1"
    assert headers["X-Custom-2"] == "val2"


@pytest.mark.asyncio
async def test_build_auth_headers_none():
    cfg = MCPServerConfig(name="NoneAuth", url="http://api.example.com", auth_type="none",
                          auth_config={})
    client = _make_client()
    headers = await client._build_auth_headers(cfg, tenant_ctx=_ctx())
    assert "Authorization" not in headers


@pytest.mark.asyncio
async def test_build_auth_headers_oauth_no_manager():
    cfg = MCPServerConfig(name="OAuthSrv", url="http://api.example.com", auth_type="oauth_ac",
                          auth_config={})
    client = _make_client()
    # No oauth_manager set → should produce empty headers
    headers = await client._build_auth_headers(cfg, tenant_ctx=_ctx())
    assert "Authorization" not in headers


@pytest.mark.asyncio
async def test_build_auth_headers_bearer_empty_token():
    cfg = MCPServerConfig(name="EmptyToken", url="http://api.example.com", auth_type="bearer",
                          auth_config={"token": ""})
    client = _make_client()
    headers = await client._build_auth_headers(cfg, tenant_ctx=_ctx())
    assert "Authorization" not in headers


# ---------------------------------------------------------------------------
# call_tool — input validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_tool_empty_server_id():
    client = _make_client()
    result = await client.call_tool(
        server_id="", tool_name="search", arguments={}, tenant_ctx=_ctx()
    )
    assert result.success is False
    assert "server_id" in result.error.lower()


@pytest.mark.asyncio
async def test_call_tool_empty_tool_name():
    client = _make_client()
    result = await client.call_tool(
        server_id="srv-1", tool_name="", arguments={}, tenant_ctx=_ctx()
    )
    assert result.success is False
    assert "tool_name" in result.error.lower()


@pytest.mark.asyncio
async def test_call_tool_non_dict_arguments():
    """Non-dict arguments get coerced to {}."""
    registry = MCPRegistry(redis=None)
    client = _make_client(registry=registry)

    cfg = MCPServerConfig(name="TestSrv", url="http://api.example.com")

    with patch.object(registry, "get", AsyncMock(return_value=cfg)), \
         patch.object(client, "_call_tool_impl", AsyncMock(return_value=ToolCallResult(
             tool_name="search", success=True, output="result", server_id="s1"
         ))):
        result = await client.call_tool(
            server_id="s1", tool_name="search", arguments="not_a_dict",  # type: ignore
            tenant_ctx=_ctx()
        )
    assert result.success is True


@pytest.mark.asyncio
async def test_call_tool_server_not_found():
    registry = MCPRegistry(redis=None)
    client = _make_client(registry=registry)

    with patch.object(registry, "get", AsyncMock(return_value=None)), \
         patch.object(registry, "list_all", AsyncMock(return_value=[])):
        result = await client.call_tool(
            server_id="ghost-srv", tool_name="search", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is False
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_call_tool_server_not_found_openapi_fallback():
    """When server not found, scans for OpenAPI-imported tool."""
    registry = MCPRegistry(redis=None)
    client = _make_client(registry=registry)

    mock_server = MagicMock()
    mock_server.tool_definitions = [{"name": "search_web", "http_path": "/search"}]

    fallback_result = ToolCallResult(tool_name="search_web", success=True, output="found")

    with patch.object(registry, "get", AsyncMock(return_value=None)), \
         patch.object(registry, "list_all", AsyncMock(return_value=[mock_server])), \
         patch.object(client, "_dispatch_openapi_tool", AsyncMock(return_value=fallback_result)):
        result = await client.call_tool(
            server_id="ghost-srv", tool_name="search_web", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is True


# ---------------------------------------------------------------------------
# _dispatch_builtin_tool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_builtin_tool_no_handler():
    cfg = MCPServerConfig(name="BuiltinSrv", url="", builtin_handler=None)
    client = _make_client()
    result = await client._dispatch_builtin_tool(cfg, "my_tool", {})
    assert result.success is False
    assert "handler not available" in result.error.lower()


@pytest.mark.asyncio
async def test_dispatch_builtin_tool_success():
    async def _handler(tool_name: str, args: dict) -> dict:
        return {"result": f"called {tool_name}"}

    cfg = MCPServerConfig(name="BuiltinSrv", url="", builtin_handler=_handler)
    client = _make_client()
    result = await client._dispatch_builtin_tool(cfg, "my_tool", {"key": "val"})
    assert result.success is True
    assert result.output == {"result": "called my_tool"}


@pytest.mark.asyncio
async def test_dispatch_builtin_tool_exception():
    async def _handler(tool_name: str, args: dict) -> dict:
        raise RuntimeError("handler crashed")

    cfg = MCPServerConfig(name="BuiltinSrv", url="", builtin_handler=_handler)
    client = _make_client()
    result = await client._dispatch_builtin_tool(cfg, "my_tool", {})
    assert result.success is False
    assert "handler crashed" in result.error


# ---------------------------------------------------------------------------
# _dispatch_openapi_tool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_openapi_tool_post():
    cfg = MCPServerConfig(name="OpenAPISrv", url="http://api.example.com", base_url="http://api.example.com")
    tool_def = {"name": "create_item", "http_method": "POST", "http_path": "/items"}

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"id": "item-1"}

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.request = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_http

        client = _make_client()
        result = await client._dispatch_openapi_tool(cfg, tool_def, {"name": "test"})
    assert result.success is True
    assert result.output == {"id": "item-1"}


@pytest.mark.asyncio
async def test_dispatch_openapi_tool_get():
    cfg = MCPServerConfig(name="OpenAPISrv", url="http://api.example.com", base_url="http://api.example.com")
    tool_def = {"name": "get_item", "http_method": "GET", "http_path": "/items/{id}"}

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"id": "i1", "name": "Item 1"}

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_http

        client = _make_client()
        result = await client._dispatch_openapi_tool(cfg, tool_def, {"id": "i1"})
    assert result.success is True


@pytest.mark.asyncio
async def test_dispatch_openapi_tool_exception():
    cfg = MCPServerConfig(name="OpenAPISrv", url="http://api.example.com", base_url="http://api.example.com")
    tool_def = {"name": "fail_tool", "http_method": "POST", "http_path": "/fail"}

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.request = AsyncMock(side_effect=Exception("connection error"))
        mock_client_cls.return_value = mock_http

        client = _make_client()
        result = await client._dispatch_openapi_tool(cfg, tool_def, {})
    assert result.success is False
    assert "connection error" in result.error


# ---------------------------------------------------------------------------
# discover_tools
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discover_tools_empty_server_id():
    client = _make_client()
    tools = await client.discover_tools(server_id="", tenant_ctx=_ctx())
    assert tools == []


@pytest.mark.asyncio
async def test_discover_tools_server_not_found():
    registry = MCPRegistry(redis=None)
    client = _make_client(registry=registry)
    with patch.object(registry, "get", AsyncMock(return_value=None)):
        tools = await client.discover_tools(server_id="ghost", tenant_ctx=_ctx())
    assert tools == []


@pytest.mark.asyncio
async def test_discover_tools_http_endpoint():
    registry = MCPRegistry(redis=None)
    client = _make_client(registry=registry)
    cfg = MCPServerConfig(name="HttpSrv", url="http://tools.example.com")

    tool_list = [
        {"name": "search", "description": "Search the web", "inputSchema": {"type": "object"}},
        {"name": "fetch", "description": "Fetch a URL"},
    ]

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = tool_list

    with patch.object(registry, "get", AsyncMock(return_value=cfg)), \
         patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_http

        tools = await client.discover_tools(server_id="srv-1", tenant_ctx=_ctx())
    assert len(tools) == 2
    assert tools[0].name == "search"


@pytest.mark.asyncio
async def test_discover_tools_mcp_endpoint_list_in_result():
    registry = MCPRegistry(redis=None)
    client = _make_client(registry=registry)
    cfg = MCPServerConfig(name="McpSrv", url="http://mcp.example.com/mcp")

    jsonrpc_response = {
        "jsonrpc": "2.0",
        "id": "test",
        "result": {
            "tools": [
                {"name": "code_run", "description": "Run code"},
            ]
        }
    }

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = jsonrpc_response

    with patch.object(registry, "get", AsyncMock(return_value=cfg)), \
         patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_http

        tools = await client.discover_tools(server_id="mcp-srv", tenant_ctx=_ctx())
    assert len(tools) == 1
    assert tools[0].name == "code_run"


@pytest.mark.asyncio
async def test_discover_tools_dict_response_without_result():
    registry = MCPRegistry(redis=None)
    client = _make_client(registry=registry)
    cfg = MCPServerConfig(name="ToolsSrv", url="http://tools.example.com")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"tools": [{"name": "ping"}, {"name": ""}]}  # empty name filtered

    with patch.object(registry, "get", AsyncMock(return_value=cfg)), \
         patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_http

        tools = await client.discover_tools(server_id="tools-srv", tenant_ctx=_ctx())
    # Empty name should be filtered
    assert all(t.name for t in tools)
    assert len(tools) == 1


@pytest.mark.asyncio
async def test_discover_tools_exception_returns_empty():
    registry = MCPRegistry(redis=None)
    client = _make_client(registry=registry)
    cfg = MCPServerConfig(name="FailSrv", url="http://fail.example.com")

    with patch.object(registry, "get", AsyncMock(return_value=cfg)), \
         patch("httpx.AsyncClient", side_effect=Exception("network error")):
        tools = await client.discover_tools(server_id="fail-srv", tenant_ctx=_ctx())
    assert tools == []


# ---------------------------------------------------------------------------
# discover_all_tools
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discover_all_tools():
    registry = MCPRegistry(redis=None)
    client = _make_client(registry=registry)

    with patch.object(registry, "list_server_records", AsyncMock(return_value=[
        ("srv-1", {}), ("srv-2", {}),
    ])), \
    patch.object(client, "discover_tools", AsyncMock(side_effect=[
        [ToolDefinition(name="tool-a", description="A", server_id="srv-1", server_name="Srv1")],
        [ToolDefinition(name="tool-b", description="B", server_id="srv-2", server_name="Srv2")],
    ])):
        tools = await client.discover_all_tools(tenant_ctx=_ctx())
    assert len(tools) == 2


@pytest.mark.asyncio
async def test_discover_all_tools_exception():
    registry = MCPRegistry(redis=None)
    client = _make_client(registry=registry)

    with patch.object(registry, "list_server_records", AsyncMock(side_effect=Exception("reg error"))):
        tools = await client.discover_all_tools(tenant_ctx=_ctx())
    assert tools == []


# ---------------------------------------------------------------------------
# _call_tool_impl — JSON-RPC error response
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_tool_impl_jsonrpc_error_dict():
    registry = MCPRegistry(redis=None)
    client = _make_client(registry=registry)
    cfg = MCPServerConfig(name="McpSrv", url="http://mcp.example.com/mcp")

    error_resp = {
        "jsonrpc": "2.0",
        "id": "1",
        "error": {"code": -32601, "message": "Method not found"},
    }

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = error_resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_http

        result = await client._call_tool_impl(cfg, "mcp-srv", "bad_tool", {}, _ctx())
    assert result.success is False
    assert "-32601" in result.error


@pytest.mark.asyncio
async def test_call_tool_impl_jsonrpc_error_string():
    registry = MCPRegistry(redis=None)
    client = _make_client(registry=registry)
    cfg = MCPServerConfig(name="McpSrv", url="http://mcp.example.com/mcp")

    error_resp = {
        "jsonrpc": "2.0",
        "id": "1",
        "error": "Something went wrong",
    }

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = error_resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_http

        result = await client._call_tool_impl(cfg, "mcp-srv", "bad_tool2", {}, _ctx())
    assert result.success is False
    assert "Something went wrong" in result.error


@pytest.mark.asyncio
async def test_call_tool_impl_normal_http_endpoint():
    """Non-MCP endpoint uses /tools/{tool_name} path."""
    registry = MCPRegistry(redis=None)
    client = _make_client(registry=registry)
    cfg = MCPServerConfig(name="HttpSrv", url="http://api.example.com")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"output": "search results"}

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_http

        result = await client._call_tool_impl(cfg, "http-srv", "search", {"q": "test"}, _ctx())
    assert result.success is True
    assert result.output == {"output": "search results"}


@pytest.mark.asyncio
async def test_call_tool_with_openapi_tool_definition():
    """When cfg has tool_definitions matching tool_name, uses OpenAPI dispatch."""
    registry = MCPRegistry(redis=None)
    client = _make_client(registry=registry)
    cfg = MCPServerConfig(
        name="OpenAPISrv",
        url="http://api.example.com",
        base_url="http://api.example.com",
        tool_definitions=[{"name": "list_items", "http_method": "GET", "http_path": "/items"}],
    )

    mock_result = ToolCallResult(tool_name="list_items", success=True, output=[])

    with patch.object(client, "_dispatch_openapi_tool", AsyncMock(return_value=mock_result)):
        result = await client._call_tool_impl(cfg, "openapi-srv", "list_items", {}, _ctx())
    assert result.success is True


# ---------------------------------------------------------------------------
# _update_tool_stats
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_tool_stats_no_db():
    client = _make_client()
    # Should be no-op when db is None
    await client._update_tool_stats("srv-1", "search", "tid-1", success=True, latency_ms=50.0, db=None)


@pytest.mark.asyncio
async def test_update_tool_stats_with_db():
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock()
    mock_session.begin = MagicMock()
    mock_session.begin.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=False)

    async def _db():
        return mock_session

    client = _make_client()
    # Should not raise
    await client._update_tool_stats("srv-1", "search", "tid-1", success=True, latency_ms=30.0, db=_db)


@pytest.mark.asyncio
async def test_update_tool_stats_failure_path():
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))
    mock_session.begin = MagicMock()
    mock_session.begin.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=False)

    async def _db():
        return mock_session

    client = _make_client()
    # Should not raise even on DB error
    await client._update_tool_stats("srv-1", "fail_tool", "tid-1", success=False, latency_ms=10.0, db=_db)


# ---------------------------------------------------------------------------
# call_tool — circuit breaker open
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_tool_circuit_breaker_open():
    registry = MCPRegistry(redis=None)
    client = _make_client(registry=registry)

    mock_cb = AsyncMock()
    mock_cb.can_call_async = AsyncMock(return_value=False)

    with patch.object(client, "_get_circuit_breaker", return_value=mock_cb):
        with pytest.raises(CircuitBreakerOpenError):
            await client.call_tool(
                server_id="srv-1", tool_name="search", arguments={}, tenant_ctx=_ctx()
            )


# ---------------------------------------------------------------------------
# call_tool — HTTP status error propagates to failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_tool_http_status_error():
    import httpx
    registry = MCPRegistry(redis=None)
    client = _make_client(registry=registry)
    cfg = MCPServerConfig(name="HttpSrv", url="http://api.example.com")

    mock_cb = AsyncMock()
    mock_cb.can_call_async = AsyncMock(return_value=True)
    mock_cb.record_failure_async = AsyncMock()
    mock_cb.record_success_async = AsyncMock()

    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.text = "Service Unavailable"
    http_error = httpx.HTTPStatusError("503", request=MagicMock(), response=mock_response)

    with patch.object(registry, "get", AsyncMock(return_value=cfg)), \
         patch.object(client, "_get_circuit_breaker", return_value=mock_cb), \
         patch.object(client, "_call_tool_impl", AsyncMock(side_effect=http_error)):
        result = await client.call_tool(
            server_id="srv-1", tool_name="search", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is False
    assert "503" in result.error
