"""SSRF: built-in MCP dispatch and tenant-URL built-in servers.

Regressions:
* ``_call_tool_impl`` returned from the built-in (and WebSocket) branch BEFORE the
  SSRF guard ran, so those connectors reached internal addresses unchecked.
* jira_server / github_server used the tenant connector's ``url``/``base_url``
  as the API base with no check.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.mcp.client import MCPClient
from app.mcp.registry import MCPServerConfig
from app.mcp.servers import github_server, jira_server
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(tenant_id="tenant-ssrf", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _handler(seen: list[Any]) -> Any:
    async def handler(
        tool_name: str, arguments: dict[str, Any], *, credentials: dict[str, Any]
    ) -> dict[str, Any]:
        seen.append(credentials)
        return {"ok": True}

    return handler


@pytest.mark.parametrize(
    "internal", ["http://169.254.169.254/latest", "http://127.0.0.1:8080", "http://10.1.2.3"]
)
async def test_builtin_connector_url_is_ssrf_checked_before_handler(internal: str) -> None:
    seen: list[Any] = []
    cfg = MCPServerConfig(
        server_id="builtin-custom-x",
        name="Custom X",
        url=internal,
        builtin_handler=_handler(seen),
    )
    client = MCPClient(registry=AsyncMock())
    result = await client._call_tool_impl(cfg, cfg.server_id, "x_tool", {}, TENANT)
    assert result.success is False
    assert "SSRF" in (result.error or "")
    assert seen == []


async def test_builtin_auth_config_url_is_ssrf_checked() -> None:
    seen: list[Any] = []
    cfg = MCPServerConfig(
        server_id="builtin-jira",
        name="Jira",
        base_url="builtin://",
        auth_config={"url": "http://169.254.169.254", "api_token": "t", "email": "e"},
        builtin_handler=_handler(seen),
    )
    client = MCPClient(registry=AsyncMock())
    result = await client._call_tool_impl(cfg, cfg.server_id, "jira_search_issues", {}, TENANT)
    assert result.success is False
    assert "SSRF" in (result.error or "")
    assert seen == []


async def test_websocket_connector_url_is_ssrf_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.mcp.ws_client as ws_mod

    opened: list[str] = []

    class _WS:
        def __init__(self, ws_url: str) -> None:
            opened.append(ws_url)

        async def __aenter__(self) -> _WS:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def call_tool(self, **kw: Any) -> Any:
            return {"ok": True}

    monkeypatch.setattr(ws_mod, "MCPWebSocketClient", _WS)
    cfg = MCPServerConfig(
        server_id="ws-x",
        name="WS",
        base_url="builtin://",
        transport="ws",
        ws_url="ws://127.0.0.1:9000/mcp",
    )
    client = MCPClient(registry=AsyncMock())
    result = await client._call_tool_impl(cfg, cfg.server_id, "t", {}, TENANT)
    assert result.success is False
    assert opened == []


async def test_jira_server_rejects_internal_tenant_url() -> None:
    out = await jira_server.call_tool(
        "jira_search_issues",
        {"jql": "project = X"},
        credentials={"url": "http://127.0.0.1:9999", "api_token": "t", "email": "e"},
    )
    assert "SSRF" in out["error"]


async def test_github_server_rejects_internal_tenant_url() -> None:
    out = await github_server.call_tool(
        "github_list_repos",
        {"owner": "o"},
        credentials={"url": "http://169.254.169.254", "token": "t"},
    )
    assert "SSRF" in out["error"]
