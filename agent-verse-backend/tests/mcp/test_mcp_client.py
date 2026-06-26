"""Tests for MCP HTTP client."""
from __future__ import annotations

import builtins
import json

import httpx
import pytest
import respx

from app.mcp.client import MCPClient
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(
    tenant_id="mcp-test",
    plan=PlanTier.PROFESSIONAL,
    api_key_id="mcp-key-1",
)


class FakeRedis:
    def __init__(self) -> None:
        self._d: dict[str, str] = {}
        self._s: dict[str, builtins.set[str]] = {}

    async def get(self, k: str) -> str | None:
        return self._d.get(k)

    async def set(self, k: str, v: str, ex: int | None = None) -> None:
        self._d[k] = v

    async def delete(self, k: str) -> int:
        existed = k in self._d
        self._d.pop(k, None)
        return int(existed)

    async def sadd(self, k: str, v: str) -> None:
        self._s.setdefault(k, set()).add(v)

    async def srem(self, k: str, v: str) -> None:
        self._s.get(k, set()).discard(v)

    async def smembers(self, k: str) -> builtins.set[str]:
        return self._s.get(k, set())


@pytest.fixture
async def registry_with_server() -> tuple[MCPRegistry, str]:
    redis = FakeRedis()
    registry = MCPRegistry(redis=redis)
    cfg = MCPServerConfig(
        name="github-mcp",
        url="http://localhost:9001",
        auth_type="bearer",
        auth_config={"token": "ghp_test"},
    )
    server_id = await registry.register(cfg, tenant_ctx=TENANT)
    return registry, server_id


@pytest.fixture
async def registry_with_mcp_endpoint() -> tuple[MCPRegistry, str]:
    redis = FakeRedis()
    registry = MCPRegistry(redis=redis)
    cfg = MCPServerConfig(
        name="atlassian-mcp",
        url="https://mcp.atlassian.com/v1/mcp",
        auth_type="bearer",
        auth_config={"token": "atl_test"},
    )
    server_id = await registry.register(cfg, tenant_ctx=TENANT)
    return registry, server_id


@pytest.mark.asyncio
async def test_discover_tools_returns_list(
    registry_with_server: tuple[MCPRegistry, str],
) -> None:
    registry, server_id = registry_with_server
    client = MCPClient(registry=registry)

    with respx.mock:
        respx.get("http://localhost:9001/tools").mock(
            return_value=httpx.Response(
                200,
                json={
                    "tools": [
                        {
                            "name": "list_repos",
                            "description": "List GitHub repos",
                            "inputSchema": {},
                        },
                        {
                            "name": "create_issue",
                            "description": "Create an issue",
                            "inputSchema": {},
                        },
                    ]
                },
            )
        )
        tools = await client.discover_tools(server_id=server_id, tenant_ctx=TENANT)

    assert len(tools) == 2
    assert tools[0].name == "list_repos"


@pytest.mark.asyncio
async def test_discover_tools_uses_jsonrpc_for_mcp_endpoint(
    registry_with_mcp_endpoint: tuple[MCPRegistry, str],
) -> None:
    registry, server_id = registry_with_mcp_endpoint
    client = MCPClient(registry=registry)

    with respx.mock:
        route = respx.post("https://mcp.atlassian.com/v1/mcp").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": "response-id",
                    "result": {
                        "tools": [
                            {
                                "name": "search_jira",
                                "description": "Search Jira issues",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    },
                },
            )
        )
        tools = await client.discover_tools(server_id=server_id, tenant_ctx=TENANT)

    assert len(tools) == 1
    assert tools[0].name == "search_jira"
    assert tools[0].input_schema == {"type": "object"}
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer atl_test"
    assert request.headers["Accept"] == "application/json, text/event-stream"
    assert request.headers["Content-Type"] == "application/json"
    body = json.loads(request.content)
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "tools/list"
    assert body["id"]


@pytest.mark.asyncio
async def test_call_tool_success(
    registry_with_server: tuple[MCPRegistry, str],
) -> None:
    registry, server_id = registry_with_server
    client = MCPClient(registry=registry)

    with respx.mock:
        respx.post("http://localhost:9001/tools/list_repos").mock(
            return_value=httpx.Response(200, json={"repos": ["repo1", "repo2"]})
        )
        result = await client.call_tool(
            server_id=server_id,
            tool_name="list_repos",
            arguments={"org": "acme"},
            tenant_ctx=TENANT,
        )

    assert result.success is True
    assert result.output == {"repos": ["repo1", "repo2"]}


@pytest.mark.asyncio
async def test_call_tool_rest_error_payload_remains_success(
    registry_with_server: tuple[MCPRegistry, str],
) -> None:
    registry, server_id = registry_with_server
    client = MCPClient(registry=registry)
    payload = {"error": "rate limited", "retry_after": 60}

    with respx.mock:
        respx.post("http://localhost:9001/tools/list_repos").mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = await client.call_tool(
            server_id=server_id,
            tool_name="list_repos",
            arguments={"org": "acme"},
            tenant_ctx=TENANT,
        )

    assert result.success is True
    assert result.output == payload
    assert result.error == ""


@pytest.mark.asyncio
async def test_call_tool_rest_result_payload_is_not_unwrapped(
    registry_with_server: tuple[MCPRegistry, str],
) -> None:
    registry, server_id = registry_with_server
    client = MCPClient(registry=registry)
    payload = {"result": {"repos": ["repo1"]}, "metadata": {"count": 1}}

    with respx.mock:
        respx.post("http://localhost:9001/tools/list_repos").mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = await client.call_tool(
            server_id=server_id,
            tool_name="list_repos",
            arguments={"org": "acme"},
            tenant_ctx=TENANT,
        )

    assert result.success is True
    assert result.output == payload


@pytest.mark.asyncio
async def test_call_tool_uses_jsonrpc_for_mcp_endpoint(
    registry_with_mcp_endpoint: tuple[MCPRegistry, str],
) -> None:
    registry, server_id = registry_with_mcp_endpoint
    client = MCPClient(registry=registry)

    with respx.mock:
        route = respx.post("https://mcp.atlassian.com/v1/mcp").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": "response-id",
                    "result": {"content": [{"type": "text", "text": "ISSUE-1"}]},
                },
            )
        )
        result = await client.call_tool(
            server_id=server_id,
            tool_name="search_jira",
            arguments={"query": "project = TEST"},
            tenant_ctx=TENANT,
        )

    assert result.success is True
    assert result.output == {"content": [{"type": "text", "text": "ISSUE-1"}]}
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer atl_test"
    assert request.headers["Accept"] == "application/json, text/event-stream"
    assert request.headers["Content-Type"] == "application/json"
    body = json.loads(request.content)
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "tools/call"
    assert body["params"] == {
        "name": "search_jira",
        "arguments": {"query": "project = TEST"},
    }
    assert body["id"]


@pytest.mark.asyncio
async def test_call_tool_jsonrpc_error_returns_failure(
    registry_with_mcp_endpoint: tuple[MCPRegistry, str],
) -> None:
    registry, server_id = registry_with_mcp_endpoint
    client = MCPClient(registry=registry)

    with respx.mock:
        respx.post("https://mcp.atlassian.com/v1/mcp").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": "response-id",
                    "error": {"code": -32602, "message": "Unknown tool: missing"},
                },
            )
        )
        result = await client.call_tool(
            server_id=server_id,
            tool_name="missing",
            arguments={},
            tenant_ctx=TENANT,
        )

    assert result.success is False
    assert "Unknown tool: missing" in result.error
    assert "-32602" in result.error


@pytest.mark.asyncio
async def test_call_tool_server_not_found() -> None:
    redis = FakeRedis()
    registry = MCPRegistry(redis=redis)
    client = MCPClient(registry=registry)

    result = await client.call_tool(
        server_id="nonexistent",
        tool_name="test",
        arguments={},
        tenant_ctx=TENANT,
    )

    assert result.success is False
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_discover_tools_server_unreachable(
    registry_with_server: tuple[MCPRegistry, str],
) -> None:
    registry, server_id = registry_with_server
    client = MCPClient(registry=registry)

    with respx.mock:
        respx.get("http://localhost:9001/tools").mock(
            side_effect=httpx.ConnectError("refused")
        )
        tools = await client.discover_tools(server_id=server_id, tenant_ctx=TENANT)

    assert tools == []


@pytest.mark.asyncio
async def test_bearer_auth_header(
    registry_with_server: tuple[MCPRegistry, str],
) -> None:
    registry, server_id = registry_with_server
    client = MCPClient(registry=registry)

    cfg = await registry.get(server_id, tenant_ctx=TENANT)
    assert cfg is not None
    headers = await client._build_auth_headers(cfg, tenant_ctx=TENANT)

    assert headers.get("Authorization") == "Bearer ghp_test"


@pytest.mark.asyncio
async def test_build_auth_headers_resolves_bearer_secret_ref() -> None:
    client = MCPClient(
        registry=MCPRegistry(redis=FakeRedis()),
        secret_resolver=lambda ref: "resolved-token"
        if ref == "vault://connectors/srv-abc/token"
        else None,
    )
    cfg = MCPServerConfig(
        name="github-mcp",
        url="http://localhost:9001",
        auth_type="bearer",
        auth_config={"token": "vault://connectors/srv-abc/token"},
    )

    headers = await client._build_auth_headers(cfg, tenant_ctx=TENANT)

    assert headers.get("Authorization") == "Bearer resolved-token"


@pytest.mark.asyncio
async def test_call_tool_resolves_secret_ref_for_auth_header() -> None:
    redis = FakeRedis()
    registry = MCPRegistry(redis=redis)
    cfg = MCPServerConfig(
        name="atlassian-mcp",
        url="https://mcp.atlassian.com/v1/mcp",
        auth_type="bearer",
        auth_config={"token": "vault://connectors/srv-abc/token"},
    )
    server_id = await registry.register(cfg, tenant_ctx=TENANT)
    client = MCPClient(
        registry=registry,
        secret_resolver=lambda ref: "resolved-token"
        if ref == "vault://connectors/srv-abc/token"
        else None,
    )

    with respx.mock:
        route = respx.post("https://mcp.atlassian.com/v1/mcp").mock(
            return_value=httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": "response-id", "result": {"ok": True}},
            )
        )
        result = await client.call_tool(
            server_id=server_id,
            tool_name="search_jira",
            arguments={},
            tenant_ctx=TENANT,
        )

    assert result.success is True
    assert route.calls[0].request.headers["Authorization"] == "Bearer resolved-token"


@pytest.mark.asyncio
async def test_call_tool_http_error_returns_failure(
    registry_with_server: tuple[MCPRegistry, str],
) -> None:
    registry, server_id = registry_with_server
    client = MCPClient(registry=registry)

    with respx.mock:
        respx.post("http://localhost:9001/tools/bad_tool").mock(
            return_value=httpx.Response(404, text="Not Found")
        )
        result = await client.call_tool(
            server_id=server_id,
            tool_name="bad_tool",
            arguments={},
            tenant_ctx=TENANT,
        )

    assert result.success is False
    assert "404" in result.error


@pytest.mark.asyncio
async def test_discover_tools_flat_list_response(
    registry_with_server: tuple[MCPRegistry, str],
) -> None:
    """Server returns a flat JSON array instead of {tools: [...]}."""
    registry, server_id = registry_with_server
    client = MCPClient(registry=registry)

    with respx.mock:
        respx.get("http://localhost:9001/tools").mock(
            return_value=httpx.Response(
                200,
                json=[{"name": "ping", "description": "Ping", "inputSchema": {}}],
            )
        )
        tools = await client.discover_tools(server_id=server_id, tenant_ctx=TENANT)

    assert len(tools) == 1
    assert tools[0].name == "ping"
    assert tools[0].server_name == "github-mcp"
