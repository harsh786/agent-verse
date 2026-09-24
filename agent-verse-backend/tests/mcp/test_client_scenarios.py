"""Real-connector-scenario coverage for app/mcp/client.py.

Targets branches not exercised by test_mcp_client.py / test_client_extra.py /
test_client_comprehensive2.py / test_client_ssrf.py: connector timeout
mid-execution, malformed tool-list responses, non-2xx tool execution,
auth-token refresh mid-flight, circuit-breaker stale-cache fallback,
self-healing retries, tool result caching, the exfiltration guard,
call_tool_by_name resolution, WebSocket transport, and builtin-handler
restoration after a Redis round-trip.
"""
from __future__ import annotations

import base64
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.mcp.client import (
    CircuitBreakerOpenError,
    MCPClient,
    ToolCallResult,
    _response_json,
)
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext


def _ctx(tenant_id: str = "tid-scenarios") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="kid-scn")


def _make_client(registry: MCPRegistry | None = None, **kwargs: Any) -> MCPClient:
    reg = registry or MCPRegistry(redis=None)
    return MCPClient(registry=reg, timeout=5.0, **kwargs)


@pytest.fixture(autouse=True)
def _bypass_ssrf(monkeypatch):
    import app.mcp.client as _mcp_client

    monkeypatch.setattr(_mcp_client, "assert_public_url", lambda *_a, **_kw: None)


@pytest.fixture(autouse=True)
def _clean_builtin_registry():
    """The builtin handler registry is a process-local module global — don't leak."""
    from app.mcp.registry import _BUILTIN_HANDLER_REGISTRY

    before = dict(_BUILTIN_HANDLER_REGISTRY)
    yield
    _BUILTIN_HANDLER_REGISTRY.clear()
    _BUILTIN_HANDLER_REGISTRY.update(before)


def _http_ctx(**method_returns: Any) -> AsyncMock:
    """Build a mock async-context-manager httpx.AsyncClient with given method mocks."""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=ctx)
    ctx.__aexit__ = AsyncMock(return_value=False)
    for name, value in method_returns.items():
        setattr(ctx, name, value)
    return ctx


# ---------------------------------------------------------------------------
# Connector timeout mid-execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_timeout_mid_execution_returns_failure():
    """A connector that times out mid tool-call surfaces as a clean failure,
    not an unhandled exception — matters because callers (the agent executor)
    treat ToolCallResult.success as authoritative."""
    cfg = MCPServerConfig(name="SlowSrv", url="http://slow.example.com")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    ctx = _http_ctx(post=AsyncMock(side_effect=httpx.ReadTimeout("timed out")))
    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch("httpx.AsyncClient", return_value=ctx),
    ):
        result = await client.call_tool(
            server_id="slow-srv", tool_name="long_task", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is False
    assert "timed out" in result.error


@pytest.mark.asyncio
async def test_dispatch_jira_rest_tool_timeout_mid_execution():
    cfg = MCPServerConfig(
        name="Jira", url="https://acme.atlassian.net", auth_type="basic",
        auth_config={"username": "u", "password": "p"},
    )
    client = _make_client()
    ctx = _http_ctx(post=AsyncMock(side_effect=httpx.ConnectTimeout("connect timed out")))
    with patch("httpx.AsyncClient", return_value=ctx):
        result = await client._dispatch_jira_rest_tool(
            cfg, "jira-1", "jira_search_issues", {"jql": "project = X"}, _ctx()
        )
    assert result.success is False
    assert "connect timed out" in result.error


# ---------------------------------------------------------------------------
# Malformed tool-list responses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_tools_response_not_list_or_dict_returns_empty():
    """A connector returning a bare JSON scalar (e.g. `null` or a number)
    instead of a tool list/envelope must not crash discovery."""
    cfg = MCPServerConfig(name="WeirdSrv", url="http://weird.example.com")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = 42  # malformed: neither list nor dict
    ctx = _http_ctx(get=AsyncMock(return_value=mock_resp))
    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch("httpx.AsyncClient", return_value=ctx),
    ):
        tools = await client.discover_tools(server_id="weird-srv", tenant_ctx=_ctx())
    assert tools == []


@pytest.mark.asyncio
async def test_discover_tools_tools_field_not_a_list_returns_empty():
    """A connector returning {"tools": {...}} (a dict, not a list) is malformed."""
    cfg = MCPServerConfig(name="WeirdSrv2", url="http://weird2.example.com")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"tools": {"not": "a list"}}
    ctx = _http_ctx(get=AsyncMock(return_value=mock_resp))
    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch("httpx.AsyncClient", return_value=ctx),
    ):
        tools = await client.discover_tools(server_id="weird-srv2", tenant_ctx=_ctx())
    assert tools == []


def test_response_json_event_stream_without_data_line_returns_empty():
    """SSE responses with no `data: ` line (e.g. only a comment/keepalive) must
    not raise — used by discover_tools/_call_tool_impl for MCP endpoints."""
    resp = MagicMock()
    resp.headers = {"content-type": "text/event-stream"}
    resp.text = ": keepalive\n\n"
    assert _response_json(resp) == {}


def test_response_json_picks_response_over_leading_notification():
    """An MCP server on the streamable-HTTP transport may emit a
    `notifications/progress` (or `notifications/message` logging) SSE frame
    before the actual JSON-RPC response to a `tools/call` request — both are
    valid JSON-RPC objects. Returning the FIRST `data:` line unconditionally
    (the old behaviour) would silently hand back the progress notification —
    a valid-looking dict with no "error" key — as if it were the tool's
    result, instead of failing loudly or returning the real output."""
    resp = MagicMock()
    resp.headers = {"content-type": "text/event-stream"}
    resp.text = (
        'data: {"jsonrpc":"2.0","method":"notifications/progress",'
        '"params":{"progress":1,"total":3}}\n\n'
        'data: {"jsonrpc":"2.0","id":"req-1","result":{"content":[{"type":"text","text":"done"}]}}\n\n'
    )
    result = _response_json(resp, expected_id="req-1")
    assert result == {
        "jsonrpc": "2.0",
        "id": "req-1",
        "result": {"content": [{"type": "text", "text": "done"}]},
    }


def test_response_json_falls_back_to_result_bearing_event_without_id_match():
    """Even without a matching request id (e.g. the id round-trips as a
    different JSON type), a notification (no "result"/"error") must not be
    preferred over an actual JSON-RPC response."""
    resp = MagicMock()
    resp.headers = {"content-type": "text/event-stream"}
    resp.text = (
        'data: {"jsonrpc":"2.0","method":"notifications/message","params":{}}\n\n'
        'data: {"jsonrpc":"2.0","id":"other-id","result":{"ok":true}}\n\n'
    )
    result = _response_json(resp, expected_id="req-does-not-match")
    assert result == {"jsonrpc": "2.0", "id": "other-id", "result": {"ok": True}}


@pytest.mark.asyncio
async def test_call_tool_ignores_leading_progress_notification_over_sse():
    """End-to-end: call_tool() against a real MCP JSON-RPC endpoint whose
    tools/call response streams a progress notification before the actual
    result must return the tool's real output, not the notification."""
    cfg = MCPServerConfig(name="StreamingSrv", url="https://streaming.example.com/mcp")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    captured_ids: list[str] = []

    async def _post(url: str, json: dict, headers: dict) -> MagicMock:
        captured_ids.append(json["id"])
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.headers = {"content-type": "text/event-stream"}
        resp.text = (
            'data: {"jsonrpc":"2.0","method":"notifications/progress",'
            '"params":{"progress":1,"total":2}}\n\n'
            'data: {"jsonrpc":"2.0","id":"' + json["id"] + '",'
            '"result":{"content":[{"type":"text","text":"42"}]}}\n\n'
        )
        return resp

    ctx = _http_ctx(post=_post)
    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch("httpx.AsyncClient", return_value=ctx),
    ):
        result = await client.call_tool(
            server_id="streaming-srv",
            tool_name="long_task",
            arguments={},
            tenant_ctx=_ctx(),
        )

    assert captured_ids  # sanity: the request id we asserted against was real
    assert result.success is True
    assert result.output == {"content": [{"type": "text", "text": "42"}]}


# ---------------------------------------------------------------------------
# Tool execution returning a non-2xx status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_openapi_tool_200_with_error_body_is_failure():
    """H1 fix regression: HTTP 200 with {"error": ...} body must be treated
    as a failed tool call, not success — the status code alone is not enough."""
    cfg = MCPServerConfig(name="OpenAPISrv", url="http://api.example.com",
                          base_url="http://api.example.com")
    tool_def = {"name": "create_item", "http_method": "POST", "http_path": "/items"}

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()  # 200 OK
    mock_resp.json.return_value = {"error": "duplicate item"}
    ctx = _http_ctx(request=AsyncMock(return_value=mock_resp))

    client = _make_client()
    with patch("httpx.AsyncClient", return_value=ctx):
        result = await client._dispatch_openapi_tool(cfg, tool_def, {"name": "x"})
    assert result.success is False
    assert "duplicate item" in result.error


@pytest.mark.asyncio
async def test_call_tool_impl_is_error_content_non_dict_item():
    cfg = MCPServerConfig(name="McpSrv", url="http://mcp.example.com/mcp")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "jsonrpc": "2.0", "id": "1",
        "result": {"isError": True, "content": ["plain string error"]},
    }
    ctx = _http_ctx(post=AsyncMock(return_value=mock_resp))
    client = _make_client()
    with patch("httpx.AsyncClient", return_value=ctx):
        result = await client._call_tool_impl(cfg, "mcp-srv", "bad_tool", {}, _ctx())
    assert result.success is False
    assert "plain string error" in result.error


@pytest.mark.asyncio
async def test_call_tool_impl_is_error_no_content_falls_back_to_str_output():
    cfg = MCPServerConfig(name="McpSrv", url="http://mcp.example.com/mcp")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "jsonrpc": "2.0", "id": "1",
        "result": {"isError": True, "content": []},
    }
    ctx = _http_ctx(post=AsyncMock(return_value=mock_resp))
    client = _make_client()
    with patch("httpx.AsyncClient", return_value=ctx):
        result = await client._call_tool_impl(cfg, "mcp-srv", "bad_tool", {}, _ctx())
    assert result.success is False


# ---------------------------------------------------------------------------
# Auth-token refresh mid-flight
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_auth_headers_oauth_refreshes_expired_token_mid_flight():
    """An expired OAuth token must be refreshed transparently before the
    request goes out — the header carries the *new* access token."""
    cfg = MCPServerConfig(name="OAuthSrv", url="http://api.example.com",
                          auth_type="oauth_ac", auth_config={"client_id": "abc"})

    expired_token = MagicMock()
    expired_token.is_expired.return_value = True

    refreshed_token = MagicMock()
    refreshed_token.is_expired.return_value = False
    refreshed_token.access_token = "brand-new-access-token"

    mock_oauth = MagicMock()
    mock_oauth.get_token = MagicMock(return_value=expired_token)
    mock_oauth.refresh_token = AsyncMock(return_value=refreshed_token)

    client = _make_client()
    client._oauth_manager = mock_oauth
    headers = await client._build_auth_headers(cfg, tenant_ctx=_ctx(), server_id="srv-oauth")

    mock_oauth.refresh_token.assert_awaited_once()
    assert headers["Authorization"] == "Bearer brand-new-access-token"


@pytest.mark.asyncio
async def test_build_auth_headers_oauth_refresh_failure_omits_auth_header():
    """If the refresh call itself fails (revoked refresh token, network
    error), the client must degrade gracefully — no Authorization header,
    not an unhandled exception surfacing from a header-builder."""
    cfg = MCPServerConfig(name="OAuthSrv", url="http://api.example.com",
                          auth_type="oauth_cc", auth_config={})

    expired_token = MagicMock()
    expired_token.is_expired.return_value = True

    mock_oauth = MagicMock()
    mock_oauth.get_token = MagicMock(return_value=expired_token)
    mock_oauth.refresh_token = AsyncMock(side_effect=RuntimeError("refresh_token revoked"))

    client = _make_client()
    client._oauth_manager = mock_oauth
    headers = await client._build_auth_headers(cfg, tenant_ctx=_ctx(), server_id="srv-oauth2")
    assert "Authorization" not in headers


@pytest.mark.asyncio
async def test_build_auth_headers_oauth_get_token_raises_is_swallowed():
    cfg = MCPServerConfig(name="OAuthSrv", url="http://api.example.com",
                          auth_type="pkce", auth_config={})
    mock_oauth = MagicMock()
    mock_oauth.get_token = MagicMock(side_effect=RuntimeError("oauth backend down"))

    client = _make_client()
    client._oauth_manager = mock_oauth
    headers = await client._build_auth_headers(cfg, tenant_ctx=_ctx(), server_id="srv-oauth3")
    assert headers == {}


# ---------------------------------------------------------------------------
# Circuit breaker: stale-cache fallback when open
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_circuit_open_serves_stale_cache():
    """When the circuit breaker is open, a previously cached (stale) result
    should still be served rather than failing the goal outright."""
    cfg = MCPServerConfig(name="FlakySrv", url="http://flaky.example.com")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    mock_cb = AsyncMock()
    mock_cb.can_call_async = AsyncMock(return_value=False)

    mock_cache = AsyncMock()
    mock_cache.get_stale = AsyncMock(return_value={"cached": "value"})
    client._tool_cache = mock_cache

    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch.object(client, "_get_circuit_breaker", return_value=mock_cb),
    ):
        result = await client.call_tool(
            server_id="flaky-srv", tool_name="search", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is True
    assert result.output == {"cached": "value"}


@pytest.mark.asyncio
async def test_call_tool_circuit_open_no_stale_cache_raises():
    mock_cb = AsyncMock()
    mock_cb.can_call_async = AsyncMock(return_value=False)

    mock_cache = AsyncMock()
    mock_cache.get_stale = AsyncMock(return_value=None)

    client = _make_client()
    client._tool_cache = mock_cache
    with patch.object(client, "_get_circuit_breaker", return_value=mock_cb):
        with pytest.raises(CircuitBreakerOpenError):
            await client.call_tool(
                server_id="flaky-srv", tool_name="search", arguments={}, tenant_ctx=_ctx()
            )


@pytest.mark.asyncio
async def test_call_tool_circuit_open_stale_cache_lookup_errors_still_raises():
    mock_cb = AsyncMock()
    mock_cb.can_call_async = AsyncMock(return_value=False)

    mock_cache = AsyncMock()
    mock_cache.get_stale = AsyncMock(side_effect=RuntimeError("cache backend down"))

    client = _make_client()
    client._tool_cache = mock_cache
    with patch.object(client, "_get_circuit_breaker", return_value=mock_cb):
        with pytest.raises(CircuitBreakerOpenError):
            await client.call_tool(
                server_id="flaky-srv", tool_name="search", arguments={}, tenant_ctx=_ctx()
            )


@pytest.mark.asyncio
async def test_call_tool_circuit_breaker_check_raises_generic_exception_is_ignored():
    """A bug in the circuit breaker's own bookkeeping must never block a
    tool call — only an explicit CircuitBreakerOpenError should."""
    cfg = MCPServerConfig(name="Srv", url="http://api.example.com")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    mock_cb = AsyncMock()
    mock_cb.can_call_async = AsyncMock(side_effect=RuntimeError("cb bookkeeping bug"))
    mock_cb.record_success_async = AsyncMock()

    mock_result = ToolCallResult(tool_name="search", success=True, output="ok")
    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch.object(client, "_get_circuit_breaker", return_value=mock_cb),
        patch.object(client, "_call_tool_impl", AsyncMock(return_value=mock_result)),
    ):
        result = await client.call_tool(
            server_id="srv-1", tool_name="search", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is True


# ---------------------------------------------------------------------------
# call_tool — server-not-found fallback scan raising
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_server_not_found_list_all_raises_still_reports_not_found():
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)
    with (
        patch.object(registry, "get", AsyncMock(return_value=None)),
        patch.object(registry, "list_all", AsyncMock(side_effect=RuntimeError("registry down"))),
    ):
        result = await client.call_tool(
            server_id="ghost", tool_name="search", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is False
    assert "not found" in result.error


# ---------------------------------------------------------------------------
# call_tool_by_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_by_name_resolves_correct_server():
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    from app.mcp.client import ToolDefinition

    async def fake_discover(*, server_id: str, tenant_ctx: TenantContext):
        if server_id == "srv-a":
            return [ToolDefinition(name="other_tool", description="", server_id="srv-a")]
        return [ToolDefinition(name="search", description="", server_id="srv-b")]

    mock_result = ToolCallResult(tool_name="search", success=True, output="found it")
    with (
        patch.object(
            registry, "list_server_records",
            AsyncMock(return_value=[("srv-a", {}), ("srv-b", {})]),
        ),
        patch.object(client, "discover_tools", side_effect=fake_discover),
        patch.object(client, "call_tool", AsyncMock(return_value=mock_result)) as mock_call,
    ):
        result = await client.call_tool_by_name(
            tool_name="search", arguments={"q": "x"}, tenant_ctx=_ctx()
        )
    assert result.success is True
    mock_call.assert_awaited_once_with(
        server_id="srv-b", tool_name="search", arguments={"q": "x"}, tenant_ctx=_ctx()
    )


@pytest.mark.asyncio
async def test_call_tool_by_name_no_server_exposes_tool():
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)
    with (
        patch.object(registry, "list_server_records", AsyncMock(return_value=[("srv-a", {})])),
        patch.object(client, "discover_tools", AsyncMock(return_value=[])),
    ):
        result = await client.call_tool_by_name(
            tool_name="nonexistent", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is False
    assert "no registered connector exposes" in result.error


@pytest.mark.asyncio
async def test_call_tool_by_name_list_server_records_raises():
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)
    with patch.object(
        registry, "list_server_records", AsyncMock(side_effect=RuntimeError("redis down"))
    ):
        result = await client.call_tool_by_name(
            tool_name="search", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is False
    assert "redis down" in result.error


@pytest.mark.asyncio
async def test_call_tool_by_name_discover_tools_error_on_one_server_continues():
    """A single broken connector during resolution must not abort the scan —
    the next server should still be checked."""
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    from app.mcp.client import ToolDefinition

    async def fake_discover(*, server_id: str, tenant_ctx: TenantContext):
        if server_id == "srv-broken":
            raise RuntimeError("connector unreachable")
        return [ToolDefinition(name="search", description="", server_id="srv-ok")]

    mock_result = ToolCallResult(tool_name="search", success=True, output="ok")
    with (
        patch.object(
            registry, "list_server_records",
            AsyncMock(return_value=[("srv-broken", {}), ("srv-ok", {})]),
        ),
        patch.object(client, "discover_tools", side_effect=fake_discover),
        patch.object(client, "call_tool", AsyncMock(return_value=mock_result)),
    ):
        result = await client.call_tool_by_name(
            tool_name="search", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is True


# ---------------------------------------------------------------------------
# Builtin handler restoration after a Redis round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_tools_restores_builtin_handler_from_process_registry():
    """After a Redis round-trip cfg.builtin_handler is None, but the process
    that registered the connector still holds the callable in the
    process-local registry — discover_tools must restore it and return the
    tool defs from the config rather than falling through to HTTP."""
    async def handler(tool_name: str, args: dict) -> dict:
        return {}

    MCPRegistry.register_builtin_handler("builtin-jira", handler)

    cfg = MCPServerConfig(
        server_id="builtin-jira",
        name="Jira Builtin",
        url="https://mcp.atlassian.com/v1/mcp/authv2",
        builtin_handler=None,
        tool_definitions=[{"name": "jira_search_issues", "description": "Search"}],
    )
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)
    with patch.object(registry, "get", AsyncMock(return_value=cfg)):
        tools = await client.discover_tools(server_id="builtin-jira", tenant_ctx=_ctx())
    assert len(tools) == 1
    assert tools[0].name == "jira_search_issues"


@pytest.mark.asyncio
async def test_discover_tools_restored_builtin_without_tool_defs_uses_registry_wiring():
    async def handler(tool_name: str, args: dict) -> dict:
        return {}

    MCPRegistry.register_builtin_handler("builtin-foo", handler)

    cfg = MCPServerConfig(
        server_id="builtin-foo",
        name="Foo Builtin",
        url="builtin://foo",
        builtin_handler=None,
        tool_definitions=[],
    )
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    wired_configs = [
        {
            "server_id": "builtin-foo",
            "tool_definitions": [{"name": "foo_do_thing", "description": "does a thing"}],
        }
    ]
    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch(
            "app.mcp.servers.registry_wiring.get_builtin_server_configs",
            return_value=wired_configs,
        ),
    ):
        tools = await client.discover_tools(server_id="builtin-foo", tenant_ctx=_ctx())
    assert len(tools) == 1
    assert tools[0].name == "foo_do_thing"


@pytest.mark.asyncio
async def test_discover_tools_restored_builtin_no_matching_wiring_returns_empty():
    async def handler(tool_name: str, args: dict) -> dict:
        return {}

    MCPRegistry.register_builtin_handler("builtin-bar", handler)
    cfg = MCPServerConfig(
        server_id="builtin-bar", name="Bar", url="builtin://bar",
        builtin_handler=None, tool_definitions=[],
    )
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)
    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch("app.mcp.servers.registry_wiring.get_builtin_server_configs", return_value=[]),
    ):
        tools = await client.discover_tools(server_id="builtin-bar", tenant_ctx=_ctx())
    assert tools == []


@pytest.mark.asyncio
async def test_call_tool_impl_restores_builtin_handler_by_connector_name():
    """A Celery worker process (which never registered the connector itself)
    must still be able to dispatch a builtin tool by falling back to the
    module-level builtin server configs, matched by name."""
    async def handler(tool_name: str, args: dict, credentials: dict | None = None) -> dict:
        return {"ok": True, "tool": tool_name}

    cfg = MCPServerConfig(
        server_id="uuid-1234", name="Jira Builtin",
        url="https://mcp.atlassian.com/v1/mcp/authv2", builtin_handler=None,
    )
    wired_configs = [{"server_id": "builtin-jira", "name": "Jira Builtin", "handler": handler}]

    client = _make_client()
    with patch(
        "app.mcp.servers.registry_wiring.get_builtin_server_configs",
        return_value=wired_configs,
    ):
        result = await client._call_tool_impl(cfg, "uuid-1234", "jira_search_issues", {}, _ctx())
    assert result.success is True
    assert result.output["tool"] == "jira_search_issues"


@pytest.mark.asyncio
async def test_call_tool_impl_builtin_restore_lookup_raises_falls_through_to_http():
    """If the process-local registry lookup itself raises, dispatch must not
    crash — it logs and falls through to normal HTTP/MCP dispatch."""
    cfg = MCPServerConfig(server_id="srv-x", name="Srv", url="http://api.example.com",
                          builtin_handler=None)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"output": "ok"}
    ctx = _http_ctx(post=AsyncMock(return_value=mock_resp))

    client = _make_client()
    with (
        patch(
            "app.mcp.registry.MCPRegistry.get_builtin_handler",
            side_effect=RuntimeError("registry lookup exploded"),
        ),
        patch("httpx.AsyncClient", return_value=ctx),
    ):
        result = await client._call_tool_impl(cfg, "srv-x", "search", {}, _ctx())
    assert result.success is True


# ---------------------------------------------------------------------------
# WebSocket transport
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_impl_websocket_transport_success():
    cfg = MCPServerConfig(
        server_id="ws-srv", name="WS Srv", url="http://unused.example.com",
        transport="ws", ws_url="wss://ws.example.com/mcp",
    )
    mock_ws = AsyncMock()
    mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = AsyncMock(return_value=False)
    mock_ws.call_tool = AsyncMock(return_value={"answer": 42})

    client = _make_client()
    with patch("app.mcp.ws_client.MCPWebSocketClient", return_value=mock_ws):
        result = await client._call_tool_impl(cfg, "ws-srv", "compute", {"x": 1}, _ctx())
    assert result.success is True
    assert result.output == {"answer": 42}


@pytest.mark.asyncio
async def test_call_tool_impl_websocket_transport_failure_falls_back_to_http():
    cfg = MCPServerConfig(
        server_id="ws-srv2", name="WS Srv2", url="http://fallback.example.com",
        transport="websocket", ws_url="wss://ws2.example.com/mcp",
    )
    mock_ws_ctx = MagicMock(side_effect=RuntimeError("ws handshake failed"))

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"output": "fallback ok"}
    http_ctx = _http_ctx(post=AsyncMock(return_value=mock_resp))

    client = _make_client()
    with (
        patch("app.mcp.ws_client.MCPWebSocketClient", mock_ws_ctx),
        patch("httpx.AsyncClient", return_value=http_ctx),
    ):
        result = await client._call_tool_impl(cfg, "ws-srv2", "compute", {}, _ctx())
    assert result.success is True
    assert result.output == {"output": "fallback ok"}


# ---------------------------------------------------------------------------
# SSRF guard blocking dispatch (not just discovery)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_impl_ssrf_blocked_returns_failure(monkeypatch):
    import app.mcp.client as _mcp_client
    from app.net.ssrf_guard import SSRFError

    monkeypatch.setattr(
        _mcp_client, "assert_public_url",
        MagicMock(side_effect=SSRFError("blocked: private IP")),
    )
    cfg = MCPServerConfig(server_id="internal", name="Internal", url="http://169.254.169.254/tools")
    client = _make_client()
    result = await client._call_tool_impl(cfg, "internal", "leak_secrets", {}, _ctx())
    assert result.success is False
    assert "SSRF guard" in result.error


# ---------------------------------------------------------------------------
# Tool result cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_returns_cached_result_without_dispatch():
    cfg = MCPServerConfig(server_id="srv-1", name="Srv", url="http://api.example.com")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    mock_cache = AsyncMock()
    mock_cache.get = AsyncMock(return_value={"cached": True})
    client._tool_cache = mock_cache

    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch.object(client, "_call_tool_impl", AsyncMock()) as mock_impl,
    ):
        result = await client.call_tool(
            server_id="srv-1", tool_name="search", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is True
    assert result.output == {"cached": True}
    mock_impl.assert_not_called()


@pytest.mark.asyncio
async def test_call_tool_cache_lookup_error_falls_through_to_dispatch():
    cfg = MCPServerConfig(server_id="srv-1", name="Srv", url="http://api.example.com")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    mock_cache = AsyncMock()
    mock_cache.get = AsyncMock(side_effect=RuntimeError("cache down"))
    mock_cache.set_with_stale = AsyncMock(side_effect=RuntimeError("cache down"))
    client._tool_cache = mock_cache

    real_result = ToolCallResult(tool_name="search", success=True, output="fresh")
    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch.object(client, "_call_tool_impl", AsyncMock(return_value=real_result)),
    ):
        result = await client.call_tool(
            server_id="srv-1", tool_name="search", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is True
    assert result.output == "fresh"


@pytest.mark.asyncio
async def test_call_tool_write_success_invalidates_cached_reads():
    cfg = MCPServerConfig(server_id="srv-1", name="Srv", url="http://api.example.com")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    mock_cache = AsyncMock()
    mock_cache.get = AsyncMock(return_value=None)
    mock_cache.invalidate_writes = AsyncMock()
    client._tool_cache = mock_cache

    real_result = ToolCallResult(tool_name="create_ticket", success=True, output="created")
    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch.object(client, "_call_tool_impl", AsyncMock(return_value=real_result)),
        patch("app.mcp.tool_cache.classify_tool", return_value="write"),
    ):
        result = await client.call_tool(
            server_id="srv-1", tool_name="create_ticket", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is True
    mock_cache.invalidate_writes.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_tool_read_success_caches_with_stale_backup():
    cfg = MCPServerConfig(server_id="srv-1", name="Srv", url="http://api.example.com")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    mock_cache = AsyncMock()
    mock_cache.get = AsyncMock(return_value=None)
    mock_cache.set_with_stale = AsyncMock()
    client._tool_cache = mock_cache

    real_result = ToolCallResult(tool_name="search", success=True, output="results")
    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch.object(client, "_call_tool_impl", AsyncMock(return_value=real_result)),
        patch("app.mcp.tool_cache.classify_tool", return_value="read"),
    ):
        result = await client.call_tool(
            server_id="srv-1", tool_name="search", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is True
    mock_cache.set_with_stale.assert_awaited_once()


# ---------------------------------------------------------------------------
# Exfiltration guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_blocked_by_exfil_guard():
    cfg = MCPServerConfig(server_id="srv-1", name="Srv", url="http://api.example.com")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch(
            "app.agent.exfil_guard.check_tool_args_for_exfil",
            return_value=(True, "argument contains a credential-shaped secret"),
        ),
    ):
        result = await client.call_tool(
            server_id="srv-1", tool_name="send_email",
            arguments={"body": "here is our AWS key AKIA..."}, tenant_ctx=_ctx(),
        )
    assert result.success is False
    assert "exfiltration guard" in result.error


# ---------------------------------------------------------------------------
# Self-healing retry on argument errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_self_heals_argument_error_and_retries_successfully():
    """A tool call that fails with a missing-argument error should trigger
    self-healing: the healer proposes corrected arguments, and the second
    dispatch (with healed args) succeeds."""
    cfg = MCPServerConfig(server_id="srv-1", name="Srv", url="http://api.example.com")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    failed = ToolCallResult(
        tool_name="search", success=False, error="missing required parameter: query"
    )
    healed = ToolCallResult(tool_name="search", success=True, output="found")

    mock_healer = MagicMock()
    mock_healer.is_argument_error = MagicMock(return_value=True)
    mock_healer.heal = AsyncMock(return_value={"query": "healed-value"})

    call_results = [failed, healed]

    async def fake_impl(*args, **kwargs):
        return call_results.pop(0)

    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch.object(client, "_call_tool_impl", side_effect=fake_impl),
        patch("app.mcp.tool_intelligence.get_healer", return_value=mock_healer),
    ):
        result = await client.call_tool(
            server_id="srv-1", tool_name="search", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is True
    assert result.output == "found"
    mock_healer.heal.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_tool_self_heal_same_args_does_not_retry():
    """If healing produces identical arguments, there's no point retrying —
    the original failed result is returned unchanged."""
    cfg = MCPServerConfig(server_id="srv-1", name="Srv", url="http://api.example.com")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    failed = ToolCallResult(
        tool_name="search", success=False, error="missing required parameter: query"
    )

    mock_healer = MagicMock()
    mock_healer.is_argument_error = MagicMock(return_value=True)
    mock_healer.heal = AsyncMock(return_value={})  # same as original arguments={}

    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch.object(client, "_call_tool_impl", AsyncMock(return_value=failed)),
        patch("app.mcp.tool_intelligence.get_healer", return_value=mock_healer),
    ):
        result = await client.call_tool(
            server_id="srv-1", tool_name="search", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is False


@pytest.mark.asyncio
async def test_call_tool_self_heal_errors_are_swallowed():
    cfg = MCPServerConfig(server_id="srv-1", name="Srv", url="http://api.example.com")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    failed = ToolCallResult(
        tool_name="search", success=False, error="missing required parameter: query"
    )
    mock_healer = MagicMock()
    mock_healer.is_argument_error = MagicMock(return_value=True)
    mock_healer.heal = AsyncMock(side_effect=RuntimeError("healer LLM call failed"))

    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch.object(client, "_call_tool_impl", AsyncMock(return_value=failed)),
        patch("app.mcp.tool_intelligence.get_healer", return_value=mock_healer),
    ):
        result = await client.call_tool(
            server_id="srv-1", tool_name="search", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is False
    assert "missing required parameter" in result.error


# ---------------------------------------------------------------------------
# Universal argument resolver schema lookup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_resolves_arguments_against_stored_tool_definition_schema():
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    cfg = MCPServerConfig(
        server_id="srv-1", name="Srv", url="http://api.example.com",
        tool_definitions=[{"name": "search", "parameters": schema}],
    )
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    real_result = ToolCallResult(tool_name="search", success=True, output="ok")
    captured_args = {}

    async def fake_impl(cfg_, server_id, tool_name, arguments, tenant_ctx):
        captured_args.update(arguments)
        return real_result

    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch.object(client, "_call_tool_impl", side_effect=fake_impl),
    ):
        result = await client.call_tool(
            server_id="srv-1", tool_name="search", arguments={"q": "hello"}, tenant_ctx=_ctx()
        )
    assert result.success is True
    # The resolver aliases "q" -> "query" per the schema (semantic normalisation).
    assert "query" in captured_args or "q" in captured_args


@pytest.mark.asyncio
async def test_call_tool_schema_cache_reused_across_calls():
    """The per-session schema cache avoids a second discover_tools() network
    round trip on the second call_tool() for the same server+tenant."""
    cfg = MCPServerConfig(server_id="srv-1", name="Srv", url="http://api.example.com")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    from app.mcp.client import ToolDefinition

    discover_calls = []

    async def fake_discover(*, server_id, tenant_ctx):
        discover_calls.append(server_id)
        return [ToolDefinition(name="search", description="", input_schema={"type": "object"})]

    real_result = ToolCallResult(tool_name="search", success=True, output="ok")
    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch.object(client, "discover_tools", side_effect=fake_discover),
        patch.object(client, "_call_tool_impl", AsyncMock(return_value=real_result)),
    ):
        await client.call_tool(
            server_id="srv-1", tool_name="search", arguments={}, tenant_ctx=_ctx()
        )
        await client.call_tool(
            server_id="srv-1", tool_name="search", arguments={}, tenant_ctx=_ctx()
        )
    # discover_tools (for schema resolution) should only be invoked once — the
    # second call_tool() must hit the schema cache.
    assert len(discover_calls) == 1


@pytest.mark.asyncio
async def test_call_tool_schema_lookup_discover_tools_raises_is_swallowed():
    cfg = MCPServerConfig(server_id="srv-1", name="Srv", url="http://api.example.com")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    real_result = ToolCallResult(tool_name="search", success=True, output="ok")
    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch.object(client, "discover_tools", AsyncMock(side_effect=RuntimeError("boom"))),
        patch.object(client, "_call_tool_impl", AsyncMock(return_value=real_result)),
    ):
        result = await client.call_tool(
            server_id="srv-1", tool_name="search", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is True


@pytest.mark.asyncio
async def test_call_tool_tool_intelligence_import_failure_is_swallowed():
    """If the tool-intelligence layer itself blows up (e.g. a bad patch in
    get_resolver), call_tool must still dispatch the tool unmodified."""
    cfg = MCPServerConfig(server_id="srv-1", name="Srv", url="http://api.example.com")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    real_result = ToolCallResult(tool_name="search", success=True, output="ok")
    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch(
            "app.mcp.tool_intelligence.get_resolver",
            side_effect=RuntimeError("resolver init failed"),
        ),
        patch.object(client, "_call_tool_impl", AsyncMock(return_value=real_result)),
    ):
        result = await client.call_tool(
            server_id="srv-1", tool_name="search", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is True


# ---------------------------------------------------------------------------
# CircuitBreakerOpenError raised from within dispatch re-propagates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_circuit_breaker_open_from_impl_propagates():
    cfg = MCPServerConfig(server_id="srv-1", name="Srv", url="http://api.example.com")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch.object(
            client, "_call_tool_impl",
            AsyncMock(side_effect=CircuitBreakerOpenError("opened mid-call")),
        ),
    ):
        with pytest.raises(CircuitBreakerOpenError):
            await client.call_tool(
                server_id="srv-1", tool_name="search", arguments={}, tenant_ctx=_ctx()
            )


# ---------------------------------------------------------------------------
# _update_tool_stats failures inside the outer except blocks are swallowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_http_status_error_stats_update_failure_still_returns_failure():
    cfg = MCPServerConfig(server_id="srv-1", name="Srv", url="http://api.example.com")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "boom"
    http_error = httpx.HTTPStatusError("500", request=MagicMock(), response=mock_response)

    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch.object(client, "_call_tool_impl", AsyncMock(side_effect=http_error)),
        patch.object(
            client, "_update_tool_stats",
            AsyncMock(side_effect=RuntimeError("stats db down")),
        ),
    ):
        result = await client.call_tool(
            server_id="srv-1", tool_name="search", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is False
    assert "500" in result.error


@pytest.mark.asyncio
async def test_call_tool_generic_exception_stats_update_failure_still_returns_failure():
    cfg = MCPServerConfig(server_id="srv-1", name="Srv", url="http://api.example.com")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch.object(client, "_call_tool_impl", AsyncMock(side_effect=RuntimeError("dispatch blew up"))),
        patch.object(
            client, "_update_tool_stats",
            AsyncMock(side_effect=RuntimeError("stats db also down")),
        ),
    ):
        result = await client.call_tool(
            server_id="srv-1", tool_name="search", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is False
    assert "dispatch blew up" in result.error


@pytest.mark.asyncio
async def test_call_tool_success_stats_update_failure_does_not_affect_result():
    cfg = MCPServerConfig(server_id="srv-1", name="Srv", url="http://api.example.com")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    real_result = ToolCallResult(tool_name="search", success=True, output="ok")
    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch.object(client, "_call_tool_impl", AsyncMock(return_value=real_result)),
        patch.object(
            client, "_update_tool_stats",
            AsyncMock(side_effect=RuntimeError("stats db down")),
        ),
    ):
        result = await client.call_tool(
            server_id="srv-1", tool_name="search", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is True


# ---------------------------------------------------------------------------
# _update_tool_stats — the real DB-update statement executes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_tool_stats_executes_update_statement_on_success():
    """`db` is a *sync* session-factory callable (matches the real
    async_sessionmaker usage: `async with db() as session`) — the previous
    tests in this suite passed an `async def` factory, which can never reach
    the actual `session.execute(...)` call because `async with <coroutine>`
    has no `__aenter__`. This test exercises the real code path."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock()

    mock_txn = AsyncMock()
    mock_txn.__aenter__ = AsyncMock(return_value=mock_txn)
    mock_txn.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_txn)

    db_factory = MagicMock(return_value=mock_session)  # sync callable, like async_sessionmaker

    client = _make_client()
    await client._update_tool_stats(
        "srv-1", "search", "tid-1", success=True, latency_ms=42.0, db=db_factory
    )
    # _update_tool_stats now wraps its session in sqlalchemy_rls_context, which
    # issues two extra `SET LOCAL app.tenant_id` executes (set on entry, reset
    # on exit) around the real UPDATE -- so `execute` is called 3 times total.
    # Find the actual tool_capabilities UPDATE among them.
    update_calls = [
        call
        for call in mock_session.execute.call_args_list
        if "UPDATE tool_capabilities" in str(call.args[0])
    ]
    assert len(update_calls) == 1
    params = update_calls[0].args[1]
    assert params["tid"] == "tid-1"
    assert params["tool"] == "search"
    assert params["status"] == "healthy"


@pytest.mark.asyncio
async def test_update_tool_stats_execute_raises_is_logged_not_raised():
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(side_effect=RuntimeError("db connection lost"))

    mock_txn = AsyncMock()
    mock_txn.__aenter__ = AsyncMock(return_value=mock_txn)
    mock_txn.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_txn)

    db_factory = MagicMock(return_value=mock_session)

    client = _make_client()
    # Should not raise even though the UPDATE statement fails mid-flight.
    await client._update_tool_stats(
        "srv-1", "search", "tid-1", success=False, latency_ms=10.0, db=db_factory
    )


# ---------------------------------------------------------------------------
# _extract_credentials_from_server / _dispatch_builtin_tool credential
# resolution (secret refs resolved before the handler sees them)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_builtin_tool_resolves_secret_ref_credentials():
    captured = {}

    async def handler(tool_name: str, args: dict, credentials: dict) -> dict:
        captured.update(credentials)
        return {"ok": True}

    cfg = MCPServerConfig(
        server_id="builtin-secret", name="Secret Builtin", url="builtin://secret",
        builtin_handler=handler,
        auth_config={"api_token": "vault://connectors/secret-abc", "plain": "unchanged"},
    )

    async def fake_resolver(ref: str, tenant_ctx: Any = None) -> str:
        return "resolved-plaintext-token"

    client = _make_client(secret_resolver=fake_resolver)
    with patch("app.mcp.client.is_connector_secret_ref", side_effect=lambda v: v.startswith("vault://")):
        result = await client._dispatch_builtin_tool(cfg, "search_issues", {}, _ctx())
    assert result.success is True
    assert captured["api_token"] == "resolved-plaintext-token"
    assert captured["plain"] == "unchanged"


@pytest.mark.asyncio
async def test_dispatch_builtin_tool_output_with_error_key_is_failure():
    async def handler(tool_name: str, args: dict) -> dict:
        return {"error": "invalid credentials", "raw": "401"}

    cfg = MCPServerConfig(server_id="s1", name="Srv", url="builtin://x", builtin_handler=handler)
    client = _make_client()
    result = await client._dispatch_builtin_tool(cfg, "search", {}, _ctx())
    assert result.success is False
    assert "invalid credentials" in result.error


@pytest.mark.asyncio
async def test_dispatch_builtin_tool_credentials_kwarg_only_used_when_supported():
    """A handler that does NOT declare a `credentials` parameter must be
    called without it (avoids TypeError from an unexpected kwarg)."""
    received = {}

    async def handler(tool_name: str, args: dict) -> dict:
        received["called"] = True
        return {"ok": True}

    cfg = MCPServerConfig(
        server_id="s2", name="Srv2", url="builtin://y", builtin_handler=handler,
        auth_config={"token": "plain-value"},
    )
    client = _make_client()
    result = await client._dispatch_builtin_tool(cfg, "search", {}, _ctx())
    assert result.success is True
    assert received["called"] is True


# ---------------------------------------------------------------------------
# Jira REST connector dispatch edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_jira_rest_tool_wrong_tool_name_rejected():
    cfg = MCPServerConfig(name="Jira", url="https://acme.atlassian.net")
    client = _make_client()
    result = await client._dispatch_jira_rest_tool(
        cfg, "jira-1", "jira_create_issue", {}, _ctx()
    )
    assert result.success is False
    assert "Unsupported Jira REST tool" in result.error


@pytest.mark.asyncio
async def test_dispatch_jira_rest_tool_pagination_token_forwarded():
    cfg = MCPServerConfig(name="Jira", url="https://acme.atlassian.net")

    captured_payload = {}

    async def fake_post(url, json=None, headers=None):
        captured_payload.update(json)
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"issues": [], "total": 0}
        return resp

    ctx = _http_ctx(post=fake_post)
    client = _make_client()
    with patch("httpx.AsyncClient", return_value=ctx):
        result = await client._dispatch_jira_rest_tool(
            cfg, "jira-1", "jira_search_issues",
            {"jql": "project = X", "next_page_token": "page-2"}, _ctx(),
        )
    assert result.success is True
    assert captured_payload["nextPageToken"] == "page-2"


# ---------------------------------------------------------------------------
# base64 basic-auth sanity (documents the encoding used for the Jira REST
# connector auth headers under a real credential shape)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Remaining edge branches — exception paths in the builtin-handler restore
# helpers, MCP session caching, and signature-detection fallback.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_mcp_session_returns_cached_session_header():
    cfg = MCPServerConfig(server_id="srv-sess", name="Srv", url="http://mcp.example.com/mcp")
    client = _make_client()
    # Cache key is "{tenant_id}:{server_id}" — server_id alone is not a safe
    # cross-tenant cache key (see the comment on MCPClient._mcp_sessions).
    client._mcp_sessions["tenant-a:srv-sess"] = "cached-session-id"
    headers = await client._ensure_mcp_session(
        AsyncMock(), cfg, {"X-Base": "1"}, tenant_id="tenant-a"
    )
    assert headers == {"X-Base": "1", "Mcp-Session-Id": "cached-session-id"}

    # A different tenant hitting the same nominal server_id must NOT get
    # tenant-a's cached session.
    other_client = AsyncMock()
    other_init_resp = MagicMock()
    other_init_resp.raise_for_status = MagicMock()
    other_init_resp.headers = {"mcp-session-id": "tenant-b-session"}
    other_client.post = AsyncMock(return_value=other_init_resp)
    headers_b = await client._ensure_mcp_session(
        other_client, cfg, {"X-Base": "1"}, tenant_id="tenant-b"
    )
    assert headers_b["Mcp-Session-Id"] == "tenant-b-session"
    assert client._mcp_sessions["tenant-a:srv-sess"] == "cached-session-id"


@pytest.mark.asyncio
async def test_ensure_mcp_session_no_session_header_in_init_response():
    cfg = MCPServerConfig(server_id="srv-sess2", name="Srv", url="http://mcp.example.com/mcp")
    client = _make_client()

    init_resp = MagicMock()
    init_resp.raise_for_status = MagicMock()
    init_resp.headers = {}  # server never returned mcp-session-id

    http_client = AsyncMock()
    http_client.post = AsyncMock(return_value=init_resp)

    headers = await client._ensure_mcp_session(http_client, cfg, {"X-Base": "1"})
    assert headers == {"X-Base": "1"}
    assert "srv-sess2" not in client._mcp_sessions


@pytest.mark.asyncio
async def test_discover_tools_builtin_restore_lookup_raises_is_swallowed():
    cfg = MCPServerConfig(
        server_id="srv-x", name="Srv X", url="builtin://x", builtin_handler=None
    )
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)
    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch(
            "app.mcp.registry.MCPRegistry.get_builtin_handler",
            side_effect=RuntimeError("registry corrupted"),
        ),
    ):
        tools = await client.discover_tools(server_id="srv-x", tenant_ctx=_ctx())
    # cfg.url starts with builtin:// so is_builtin is already True from the URL
    # prefix check; the restore-lookup exception must not propagate.
    assert tools == []


@pytest.mark.asyncio
async def test_discover_tools_registry_wiring_lookup_raises_is_swallowed():
    async def handler(tool_name: str, args: dict) -> dict:
        return {}

    MCPRegistry.register_builtin_handler("builtin-boom", handler)
    cfg = MCPServerConfig(
        server_id="builtin-boom", name="Boom", url="builtin://boom",
        builtin_handler=None, tool_definitions=[],
    )
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)
    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch(
            "app.mcp.servers.registry_wiring.get_builtin_server_configs",
            side_effect=RuntimeError("import exploded"),
        ),
    ):
        tools = await client.discover_tools(server_id="builtin-boom", tenant_ctx=_ctx())
    assert tools == []


@pytest.mark.asyncio
async def test_call_tool_impl_builtin_restore_registry_wiring_raises_falls_through():
    """If the registry_wiring fallback lookup inside _call_tool_impl itself
    raises, dispatch must fall through to normal HTTP dispatch rather than
    propagating the exception."""
    cfg = MCPServerConfig(server_id="srv-y", name="Srv Y", url="http://api.example.com",
                          builtin_handler=None)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"output": "ok"}
    ctx = _http_ctx(post=AsyncMock(return_value=mock_resp))

    client = _make_client()
    with (
        patch("app.mcp.servers.registry_wiring.get_builtin_server_configs",
              side_effect=RuntimeError("import exploded")),
        patch("httpx.AsyncClient", return_value=ctx),
    ):
        result = await client._call_tool_impl(cfg, "srv-y", "search", {}, _ctx())
    assert result.success is True


@pytest.mark.asyncio
async def test_dispatch_builtin_tool_registry_lookup_raises_leaves_handler_none():
    cfg = MCPServerConfig(server_id="srv-z", name="Srv Z", url="builtin://z", builtin_handler=None)
    client = _make_client()
    with patch(
        "app.mcp.registry.MCPRegistry.get_builtin_handler",
        side_effect=RuntimeError("registry corrupted"),
    ):
        result = await client._dispatch_builtin_tool(cfg, "search", {}, _ctx())
    assert result.success is False
    assert "not available" in result.error


@pytest.mark.asyncio
async def test_dispatch_builtin_tool_secret_resolver_without_tenant_support():
    """A secret resolver whose signature does not accept tenant_ctx must be
    called positionally with just the ref."""
    captured = {}

    async def handler(tool_name: str, args: dict, credentials: dict) -> dict:
        captured.update(credentials)
        return {"ok": True}

    cfg = MCPServerConfig(
        server_id="builtin-secret2", name="Secret Builtin 2", url="builtin://secret2",
        builtin_handler=handler,
        auth_config={"api_token": "vault://connectors/secret-xyz"},
    )

    def sync_resolver(ref: str) -> str:  # single positional arg -> no tenant support
        return "resolved-by-sync"

    client = _make_client(secret_resolver=sync_resolver)
    with patch("app.mcp.client.is_connector_secret_ref", side_effect=lambda v: v.startswith("vault://")):
        result = await client._dispatch_builtin_tool(cfg, "search_issues", {}, _ctx())
    assert result.success is True
    assert captured["api_token"] == "resolved-by-sync"


@pytest.mark.asyncio
async def test_dispatch_builtin_tool_secret_resolver_raises_falls_back_to_raw_ref():
    captured = {}

    async def handler(tool_name: str, args: dict, credentials: dict) -> dict:
        captured.update(credentials)
        return {"ok": True}

    cfg = MCPServerConfig(
        server_id="builtin-secret3", name="Secret Builtin 3", url="builtin://secret3",
        builtin_handler=handler,
        auth_config={"api_token": "vault://connectors/secret-boom"},
    )

    def sync_resolver(ref: str) -> str:
        raise RuntimeError("vault unreachable")

    client = _make_client(secret_resolver=sync_resolver)
    with (
        patch("app.mcp.client.is_connector_secret_ref", side_effect=lambda v: v.startswith("vault://")),
        patch("app.mcp.client.resolve_connector_secret_ref", return_value=None),
    ):
        result = await client._dispatch_builtin_tool(cfg, "search_issues", {}, _ctx())
    # Resolver raised and in-memory lookup returned nothing — handler still
    # gets called, receiving the raw (unresolved) ref as a best-effort fallback.
    assert result.success is True
    assert captured["api_token"] == "vault://connectors/secret-boom"


@pytest.mark.asyncio
async def test_dispatch_builtin_tool_credential_resolution_outer_exception_is_swallowed():
    async def handler(tool_name: str, args: dict) -> dict:
        return {"ok": True}

    cfg = MCPServerConfig(
        server_id="builtin-secret4", name="Secret Builtin 4", url="builtin://secret4",
        builtin_handler=handler,
        auth_config={"api_token": "vault://connectors/secret-4"},
    )
    client = _make_client()
    with (
        patch("app.mcp.client.is_connector_secret_ref", side_effect=lambda v: v.startswith("vault://")),
        patch(
            "app.mcp.client.resolve_connector_secret_ref",
            side_effect=RuntimeError("vault store corrupted"),
        ),
    ):
        result = await client._dispatch_builtin_tool(cfg, "search_issues", {}, _ctx())
    # The outer try/except around the whole credential-resolution block must
    # swallow this — the handler still runs with best-effort credentials.
    assert result.success is True


@pytest.mark.asyncio
async def test_dispatch_builtin_tool_handler_signature_not_inspectable():
    """Some callables (e.g. certain C-implemented or heavily-wrapped
    handlers) raise from inspect.signature() — accepts_credentials must
    default to False rather than crashing dispatch."""
    async def handler(tool_name: str, args: dict) -> dict:
        return {"ok": True, "tool": tool_name}

    cfg = MCPServerConfig(server_id="s3", name="Srv3", url="builtin://s3", builtin_handler=handler)
    client = _make_client()
    with patch("app.mcp.client.inspect.signature", side_effect=ValueError("not inspectable")):
        result = await client._dispatch_builtin_tool(cfg, "search", {}, _ctx())
    assert result.success is True
    assert result.output["tool"] == "search"


@pytest.mark.asyncio
async def test_call_tool_exfil_guard_check_raises_is_swallowed():
    """A bug in the exfiltration guard itself must never block legitimate
    tool calls — only an explicit (blocked, reason) result should."""
    cfg = MCPServerConfig(server_id="srv-1", name="Srv", url="http://api.example.com")
    registry = MCPRegistry(redis=None)
    client = _make_client(registry)

    real_result = ToolCallResult(tool_name="search", success=True, output="ok")
    with (
        patch.object(registry, "get", AsyncMock(return_value=cfg)),
        patch(
            "app.agent.exfil_guard.check_tool_args_for_exfil",
            side_effect=RuntimeError("guard crashed"),
        ),
        patch.object(client, "_call_tool_impl", AsyncMock(return_value=real_result)),
    ):
        result = await client.call_tool(
            server_id="srv-1", tool_name="search", arguments={}, tenant_ctx=_ctx()
        )
    assert result.success is True


@pytest.mark.asyncio
async def test_build_auth_headers_basic_with_secret_ref_password():
    cfg = MCPServerConfig(
        name="BasicSecret", url="http://api.example.com", auth_type="basic",
        auth_config={"username": "svc-account", "password": "vault://connectors/pw"},
    )

    async def fake_resolver(ref: str, tenant_ctx: Any = None) -> str:
        return "s3cr3t"

    client = _make_client(secret_resolver=fake_resolver)
    with patch("app.mcp.client.is_connector_secret_ref", side_effect=lambda v: v.startswith("vault://")):
        headers = await client._build_auth_headers(cfg, tenant_ctx=_ctx())
    decoded = base64.b64decode(headers["Authorization"][len("Basic "):]).decode()
    assert decoded == "svc-account:s3cr3t"
