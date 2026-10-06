"""a02-F030-05: a user-registered Jira REST connector is more than one search tool.

``_dispatch_jira_rest_tool`` refused every tool but ``jira_search_issues``. One
table (``_JIRA_REST_TOOLS``) now drives discovery and dispatch, so every
discovered tool is callable (search, get issue, list projects) and anything
else is refused with the supported list.
"""

from __future__ import annotations

from unittest.mock import patch

import fakeredis.aioredis
import httpx
import pytest
import respx

from app.mcp.client import MCPClient
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(tenant_id="t-jira-rest", plan=PlanTier.PROFESSIONAL, api_key_id="k")
BASE = "https://pinelabs.atlassian.net"


@pytest.fixture(autouse=True)
def _bypass_ssrf():  # type: ignore[no-untyped-def]
    with patch("app.mcp.client.assert_public_url"):
        yield


async def _client() -> tuple[MCPClient, str]:
    registry = MCPRegistry(redis=fakeredis.aioredis.FakeRedis(decode_responses=True))
    server_id = await registry.register(
        MCPServerConfig(
            name="PineLabs JIRA",
            url=BASE,
            auth_type="custom_header",
            auth_config={"Authorization": "Basic test-token"},
        ),
        tenant_ctx=TENANT,
    )
    return MCPClient(registry=registry), server_id


async def _call(client: MCPClient, server_id: str, tool: str, args: dict) -> object:
    return await client.call_tool(
        server_id=server_id, tool_name=tool, arguments=args, tenant_ctx=TENANT
    )


@pytest.mark.asyncio
async def test_every_discovered_tool_is_dispatchable() -> None:
    client, server_id = await _client()
    names = [t.name for t in await client.discover_tools(server_id=server_id, tenant_ctx=TENANT)]
    assert set(names) == {"jira_search_issues", "jira_get_issue", "jira_list_projects"}


@pytest.mark.asyncio
async def test_get_issue_reads_one_issue() -> None:
    client, server_id = await _client()
    with respx.mock:
        route = respx.get(f"{BASE}/rest/api/3/issue/AV-7").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "10007",
                    "key": "AV-7",
                    "fields": {
                        "summary": "Broken login",
                        "status": {"name": "In Progress"},
                        "reporter": {"displayName": "Ana"},
                        "labels": ["auth"],
                    },
                },
            )
        )
        result = await _call(client, server_id, "jira_get_issue", {"issue_key": "AV-7"})
    assert result.success is True, result.error
    assert result.output["key"] == "AV-7"
    assert result.output["status"] == "In Progress"
    assert result.output["reporter"] == "Ana"
    assert route.calls[0].request.headers["Authorization"] == "Basic test-token"


@pytest.mark.asyncio
async def test_get_issue_refuses_a_key_that_is_not_an_issue_key() -> None:
    client, server_id = await _client()
    with respx.mock(assert_all_called=False) as mock:
        result = await _call(
            client, server_id, "jira_get_issue", {"issue_key": "../../myself"}
        )
        assert not mock.calls
    assert result.success is False
    assert "issue_key" in result.error


@pytest.mark.asyncio
async def test_list_projects() -> None:
    client, server_id = await _client()
    with respx.mock:
        route = respx.get(f"{BASE}/rest/api/3/project/search").mock(
            return_value=httpx.Response(
                200,
                json={"total": 1, "values": [{"id": "1", "key": "AV", "name": "AgentVerse"}]},
            )
        )
        result = await _call(client, server_id, "jira_list_projects", {"max_results": 500})
    assert result.success is True, result.error
    assert result.output["projects"] == [
        {"id": "1", "key": "AV", "name": "AgentVerse", "type": ""}
    ]
    assert route.calls[0].request.url.params["maxResults"] == "100"  # clamped


@pytest.mark.asyncio
async def test_unknown_tool_is_refused_with_the_supported_list() -> None:
    client, server_id = await _client()
    result = await _call(client, server_id, "jira_create_issue", {"summary": "x"})
    assert result.success is False
    assert "jira_get_issue" in result.error and "jira_search_issues" in result.error


@pytest.mark.asyncio
async def test_search_without_jql_is_a_clear_error() -> None:
    client, server_id = await _client()
    result = await _call(client, server_id, "jira_search_issues", {})
    assert result.success is False
    assert "jql" in result.error
