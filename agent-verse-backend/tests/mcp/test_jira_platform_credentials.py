"""Platform Jira credentials must never be used for a tenant connector dispatch.

Regressions:
* ``jira_server._call_tool_inner`` filled any missing tenant credential from the
  platform's ``JIRA_BASE_URL`` / ``JIRA_EMAIL`` / ``JIRA_API_TOKEN``. A tenant
  connector with its own URL (e.g. an attacker host) plus only a username thus
  received the platform's Jira API token in the Authorization header.
* JQL argument repair resolved assignee display names by calling Jira with the
  platform env credentials and cached the answers in a process-global dict shared
  by all tenants.
"""

from __future__ import annotations

import base64
import os
from unittest.mock import patch

import httpx
import pytest
import respx

_ENV = {
    "JIRA_BASE_URL": "https://platform.atlassian.net",
    "JIRA_EMAIL": "platform@agentverse.io",
    "JIRA_API_TOKEN": "PLATFORM-TOKEN",
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "credentials",
    [
        {},  # tenant connector with no credentials at all
        {"url": "https://93.184.216.34"},  # tenant URL only
        {"url": "https://93.184.216.34", "username": "tenant@co.com"},  # no token
        {"username": "tenant@co.com", "api_token": "T"},  # no URL
    ],
)
async def test_tenant_dispatch_never_uses_platform_env(credentials: dict[str, str]) -> None:
    from app.mcp.servers.jira_server import call_tool

    with respx.mock(assert_all_called=False) as mock, patch.dict(os.environ, _ENV):
        route = mock.route().mock(return_value=httpx.Response(200, json={"issues": []}))
        result = await call_tool(
            "jira_search_issues", {"jql": "project = X"}, credentials=credentials
        )
    assert "error" in result
    for call in route.calls:
        auth = call.request.headers.get("authorization", "")
        assert "PLATFORM-TOKEN" not in base64.b64decode(auth.split(" ", 1)[-1] or "=").decode(
            errors="ignore"
        )
        assert "platform.atlassian.net" not in str(call.request.url)
    assert not route.called


@pytest.mark.asyncio
async def test_tenant_credentials_are_used_verbatim() -> None:
    from app.mcp.servers.jira_server import call_tool

    base = "https://93.184.216.34"
    with respx.mock as mock, patch.dict(os.environ, _ENV):
        route = mock.post(f"{base}/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": []})
        )
        await call_tool(
            "jira_search_issues",
            {"jql": "project = T"},
            credentials={"url": base, "username": "tenant@co.com", "api_token": "TENANT"},
        )
    auth = route.calls[0].request.headers["authorization"]
    assert auth == "Basic " + base64.b64encode(b"tenant@co.com:TENANT").decode()


@pytest.mark.asyncio
async def test_jql_repair_makes_no_platform_jira_call() -> None:
    from app.agent import tool_calls
    from app.agent.tool_calls import ToolCall, repair_tool_call_arguments

    assert not hasattr(tool_calls, "_jira_account_cache")
    jql = 'assignee = "Abhay Dwivedi" ORDER BY created DESC'
    with respx.mock(assert_all_called=False) as mock, patch.dict(os.environ, _ENV):
        route = mock.route().mock(return_value=httpx.Response(200, json=[]))
        repaired = await repair_tool_call_arguments(
            ToolCall(tool="jira_search_issues", arguments={"jql": jql}), step="search"
        )
    assert repaired.arguments["jql"] == jql
    assert not route.called
