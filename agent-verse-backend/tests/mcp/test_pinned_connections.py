"""Regression: every tenant MCP / OpenAPI / Jira / GitHub / OAuth call is pinned.

These call sites checked the tenant URL with ``assert_public_url`` and then
connected with a plain ``httpx.AsyncClient``, which resolves the name again —
a DNS answer flipping from the checked public IP to 127.0.0.1 / 169.254.169.254
in between (rebinding) reached internal services. They now connect through
``public_async_client``, whose connect-time check sees the rebinding answer.
"""

from __future__ import annotations

import pytest

from app.mcp.client import MCPClient
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext
from tests._pinning import install_connect_spy
from tests.mcp.test_client_ssrf import _FakeRedis

TENANT = TenantContext(tenant_id="pin-test", plan=PlanTier.STARTER, api_key_id="pin-key")


async def _client_for(cfg: MCPServerConfig) -> tuple[MCPClient, str]:
    registry = MCPRegistry(redis=_FakeRedis())
    server_id = await registry.register(cfg, tenant_ctx=TENANT)
    return MCPClient(registry=registry), server_id


@pytest.mark.asyncio
async def test_discover_tools_connects_via_pinned_client(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = install_connect_spy(monkeypatch)
    client, sid = await _client_for(
        MCPServerConfig(name="custom", url="https://rebind.example/mcp", auth_type="none")
    )
    assert await client.discover_tools(server_id=sid, tenant_ctx=TENANT) == []
    assert spy.dialed == ["rebind.example"]


@pytest.mark.asyncio
async def test_call_tool_http_dispatch_connects_via_pinned_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = install_connect_spy(monkeypatch)
    client, sid = await _client_for(
        MCPServerConfig(name="custom", url="https://rebind.example/mcp", auth_type="none")
    )
    result = await client.call_tool(
        server_id=sid, tool_name="echo", arguments={}, tenant_ctx=TENANT
    )
    assert result.success is False
    assert spy.dialed and set(spy.dialed) == {"rebind.example"}


@pytest.mark.asyncio
async def test_openapi_tool_connects_via_pinned_client(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = install_connect_spy(monkeypatch)
    client, sid = await _client_for(
        MCPServerConfig(
            name="petstore",
            url="https://rebind.example",
            base_url="https://rebind.example/api",
            auth_type="none",
            tool_definitions=[{"name": "list_pets", "http_method": "GET", "http_path": "/pets"}],
        )
    )
    result = await client.call_tool(
        server_id=sid, tool_name="list_pets", arguments={}, tenant_ctx=TENANT
    )
    assert result.success is False
    assert set(spy.dialed) == {"rebind.example"}


@pytest.mark.asyncio
async def test_jira_rest_tool_connects_via_pinned_client(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = install_connect_spy(monkeypatch)
    client, sid = await _client_for(
        MCPServerConfig(name="issues", url="https://acme.atlassian.net", auth_type="none")
    )
    result = await client.call_tool(
        server_id=sid,
        tool_name="jira_search_issues",
        arguments={"jql": "project = X"},
        tenant_ctx=TENANT,
    )
    assert result.success is False
    assert set(spy.dialed) == {"acme.atlassian.net"}


@pytest.mark.asyncio
async def test_jira_server_tenant_url_connects_via_pinned_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.mcp.servers import jira_server

    spy = install_connect_spy(monkeypatch)
    out = await jira_server.call_tool(
        "jira_search_issues",
        {"jql": "project = X"},
        credentials={"url": "https://rebind.example", "email": "a@b.c", "api_token": "t"},
    )
    assert "error" in out
    assert spy.dialed == ["rebind.example"]
    assert spy.allowlists == [None]  # a tenant URL gets no private-range allowance


@pytest.mark.asyncio
async def test_jira_server_operator_env_url_is_pinned_with_its_own_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.mcp.servers import jira_server

    spy = install_connect_spy(monkeypatch)
    monkeypatch.setenv("JIRA_BASE_URL", "https://jira.internal.corp")
    monkeypatch.setenv("JIRA_EMAIL", "ops@corp")
    monkeypatch.setenv("JIRA_API_TOKEN", "tok")
    out = await jira_server.call_tool("jira_search_issues", {"jql": "project = X"})
    assert "error" in out
    assert spy.dialed == ["jira.internal.corp"]
    assert spy.allowlists == [["jira.internal.corp"]]


@pytest.mark.asyncio
async def test_github_server_tenant_url_connects_via_pinned_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.mcp.servers import github_server

    spy = install_connect_spy(monkeypatch)
    out = await github_server.call_tool(
        "github_list_repos",
        {"owner": "octo"},
        credentials={"url": "https://rebind.example/api/v3", "token": "t"},
    )
    assert "error" in out
    assert spy.dialed == ["rebind.example"]
    assert spy.allowlists == [None]


@pytest.mark.asyncio
async def test_oauth_code_exchange_connects_via_pinned_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.mcp.oauth import OAuthFlowManager

    spy = install_connect_spy(monkeypatch)
    manager = OAuthFlowManager()
    params = manager.start_flow(server_id="srv", tenant_ctx=TENANT)
    token = await manager.exchange_code(
        code="c",
        state=params["state"],
        token_url="https://rebind.example/token",
        client_id="cid",
        redirect_uri="https://app.example/cb",
        tenant_ctx=TENANT,
    )
    assert token is None
    assert spy.dialed == ["rebind.example"]
