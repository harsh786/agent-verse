"""Tests: builtin handler receives credentials from connector auth_config."""
from __future__ import annotations

import pytest

from app.mcp.client import MCPClient, ToolCallResult
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext

_TENANT = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


class FakeRedis:
    def __init__(self):
        self._d = {}
        self._s = {}

    async def get(self, k):
        return self._d.get(k)

    async def set(self, k, v, ex=None):
        self._d[k] = v

    async def delete(self, k):
        e = k in self._d
        self._d.pop(k, None)
        return int(e)

    async def sadd(self, k, v):
        self._s.setdefault(k, set()).add(v)

    async def srem(self, k, v):
        self._s.get(k, set()).discard(v)

    async def smembers(self, k):
        return self._s.get(k, set())


@pytest.mark.asyncio
async def test_dispatch_builtin_passes_credentials_from_auth_config():
    received = {}

    async def fake_handler(tool_name, arguments, *, credentials=None):
        received.update(credentials or {})
        return {"result": "ok"}

    registry = MCPRegistry(redis=FakeRedis())
    sid = await registry.register(
        MCPServerConfig(
            server_id="test-jira",
            name="Jira",
            base_url="builtin://",
            auth_type="basic",
            auth_config={"username": "user@example.com", "password": "TOKEN"},
            builtin_handler=fake_handler,
        ),
        tenant_ctx=_TENANT,
    )
    MCPRegistry.register_builtin_handler(sid, fake_handler)
    client = MCPClient(registry=registry)
    result = await client.call_tool(
        server_id=sid,
        tool_name="jira_search_issues",
        arguments={"jql": "p=T"},
        tenant_ctx=_TENANT,
    )
    assert result.success is True
    assert received.get("username") == "user@example.com"
    assert received.get("password") == "TOKEN"


@pytest.mark.asyncio
async def test_dispatch_builtin_includes_url_when_not_builtin_placeholder():
    received = {}

    async def fake_handler(tool_name, arguments, *, credentials=None):
        received.update(credentials or {})
        return {"ok": True}

    registry = MCPRegistry(redis=FakeRedis())
    sid = await registry.register(
        MCPServerConfig(
            server_id="test-jira-url",
            name="Jira",
            url="https://mycompany.atlassian.net",
            auth_type="basic",
            auth_config={"username": "admin@mycompany.com", "password": "token"},
            builtin_handler=fake_handler,
        ),
        tenant_ctx=_TENANT,
    )
    MCPRegistry.register_builtin_handler(sid, fake_handler)
    client = MCPClient(registry=registry)
    await client.call_tool(
        server_id=sid,
        tool_name="jira_search_issues",
        arguments={},
        tenant_ctx=_TENANT,
    )
    assert received.get("url") == "https://mycompany.atlassian.net"


@pytest.mark.asyncio
async def test_dispatch_builtin_falls_back_for_old_style_handler():
    async def old_handler(tool_name, arguments):
        return {"result": "old ok"}

    registry = MCPRegistry(redis=FakeRedis())
    sid = await registry.register(
        MCPServerConfig(
            server_id="test-old",
            name="Old",
            base_url="builtin://",
            auth_config={"api_key": "k"},
            builtin_handler=old_handler,
        ),
        tenant_ctx=_TENANT,
    )
    MCPRegistry.register_builtin_handler(sid, old_handler)
    client = MCPClient(registry=registry)
    result = await client.call_tool(
        server_id=sid,
        tool_name="t",
        arguments={},
        tenant_ctx=_TENANT,
    )
    assert result.success is True
    assert result.output == {"result": "old ok"}


@pytest.mark.asyncio
async def test_dispatch_builtin_error_key_produces_failure():
    """If the handler returns a dict with 'error', ToolCallResult.success must be False."""

    async def error_handler(tool_name, arguments, *, credentials=None):
        return {"error": "something went wrong"}

    registry = MCPRegistry(redis=FakeRedis())
    sid = await registry.register(
        MCPServerConfig(
            server_id="test-error",
            name="ErrorServer",
            base_url="builtin://",
            builtin_handler=error_handler,
        ),
        tenant_ctx=_TENANT,
    )
    MCPRegistry.register_builtin_handler(sid, error_handler)
    client = MCPClient(registry=registry)
    result = await client.call_tool(
        server_id=sid,
        tool_name="some_tool",
        arguments={},
        tenant_ctx=_TENANT,
    )
    assert result.success is False
    assert "something went wrong" in result.error


@pytest.mark.asyncio
async def test_jira_server_call_tool_uses_credentials_over_env_vars():
    from app.mcp.servers.jira_server import call_tool
    import base64
    import httpx
    import respx

    resp_data = {
        "issues": [
            {
                "id": "1",
                "key": "TENANT-1",
                "fields": {
                    "summary": "Tenant issue",
                    "status": {"name": "Open"},
                    "priority": None,
                    "assignee": None,
                    "issuetype": None,
                    "created": "",
                    "updated": "",
                },
            }
        ]
    }

    with respx.mock:
        route = respx.post("https://tenant.atlassian.net/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json=resp_data)
        )
        result = await call_tool(
            "jira_search_issues",
            {"jql": "project = TENANT"},
            credentials={
                "url": "https://tenant.atlassian.net",
                "username": "tenant@co.com",
                "password": "TENANT-TOKEN",
            },
        )

    assert result["issues"][0]["key"] == "TENANT-1"
    auth = route.calls[0].request.headers["authorization"]
    assert auth == "Basic " + base64.b64encode(b"tenant@co.com:TENANT-TOKEN").decode()


@pytest.mark.asyncio
async def test_jira_server_call_tool_falls_back_to_env_when_no_credentials():
    from app.mcp.servers.jira_server import call_tool
    import base64
    import httpx
    import os
    import respx
    from unittest.mock import patch

    base = "https://envjira.atlassian.net"
    with respx.mock:
        route = respx.post(f"{base}/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": []})
        )
        with patch.dict(
            os.environ,
            {
                "JIRA_BASE_URL": base,
                "JIRA_EMAIL": "env@co.com",
                "JIRA_API_TOKEN": "ENV-TOKEN",
            },
        ):
            result = await call_tool("jira_search_issues", {"jql": "project = ENV"})

    auth = route.calls[0].request.headers["authorization"]
    assert auth == "Basic " + base64.b64encode(b"env@co.com:ENV-TOKEN").decode()
