"""CORE-23: the outbound A2A call is a real, governed agent tool.

``app/agent/tools/a2a_call.py`` had no importers: nothing let an agent delegate
to an external A2A agent. It is now the ``builtin-a2a`` built-in server
(registered with the other built-in agent tools in ``registry_wiring``):

* tenant-scoped — the endpoint and token come ONLY from the calling tenant's
  own registered "A2A Agent" connector, never from model-chosen arguments, and
  it is never wired from platform env (requires tenant configuration);
* SSRF-guarded — the MCP client's pre-dispatch guard and the call's own
  pinned-client guard refuse internal endpoints;
* governed like other tool calls — ``write_high`` risk (HITL under supervised /
  bounded autonomy), metered via tool_call_complete, and time-boxed.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

import app.net.ssrf_guard as g
from app.agent.tools import a2a_call
from app.mcp.client import MCPClient
from app.mcp.registry import MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(tenant_id="tenant-a2a", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_ENDPOINT = "https://agent.example/a2a"


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(g, "_resolve_host", lambda host: ["93.184.216.34"])


def test_registered_with_the_builtin_agent_tools() -> None:
    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    (cfg,) = [c for c in get_builtin_server_configs() if c["server_id"] == "builtin-a2a"]
    assert cfg["handler"] is a2a_call.call_tool
    assert [t["name"] for t in cfg["tool_definitions"]] == ["a2a_delegate_task"]
    # Needs the tenant's own agent endpoint: never inserted/wired from platform env.
    assert cfg["requires_env"]


def test_a_tenant_registers_its_agent_as_the_a2a_connector() -> None:
    """POST /connectors name="A2A Agent" adopts the canonical id + tool list."""
    from app.api.connectors import _get_builtin_config_for_name

    cfg = _get_builtin_config_for_name("A2A Agent")
    assert cfg is not None and cfg["server_id"] == "builtin-a2a"


async def test_not_put_on_a_tenant_surface_without_its_own_registration() -> None:
    from app.mcp.servers.registry_wiring import register_builtin_servers

    rows: dict[str, Any] = {}

    class _Reg:
        async def get(self, server_id: str, *, tenant_ctx: Any) -> Any:
            return rows.get(server_id)

        async def register(self, cfg: Any, *, tenant_ctx: Any) -> str:
            rows[cfg.server_id] = cfg
            return str(cfg.server_id)

        async def unregister(self, server_id: str, *, tenant_ctx: Any) -> bool:
            return rows.pop(server_id, None) is not None

    await register_builtin_servers(_Reg(), TENANT)
    assert "builtin-a2a" not in rows


def test_delegating_to_an_external_agent_needs_approval() -> None:
    from app.agent.tool_risk import classify_tool_risk

    assert classify_tool_risk("a2a_delegate_task", "A2A Agent") == "write_high"


async def test_no_registered_endpoint_fails_closed() -> None:
    result = await a2a_call.call_tool("a2a_delegate_task", {"task": "hi"}, credentials={})
    assert "error" in result and "registered" in result["error"].lower()


async def test_calls_only_the_tenants_registered_agent(public_dns: None) -> None:
    with respx.mock(assert_all_called=False) as mock:
        agent = mock.post(_ENDPOINT).mock(
            return_value=httpx.Response(200, json={"output": "summary ready"})
        )
        other = mock.post("https://evil.example/a2a").mock(
            return_value=httpx.Response(200, json={"output": "pwned"})
        )
        result = await a2a_call.call_tool(
            "a2a_delegate_task",
            # A model-chosen endpoint is ignored: only the registered one is called.
            {"task": "Summarise Q3", "context": {"k": "v"},
             "agent_endpoint": "https://evil.example/a2a"},
            credentials={"url": _ENDPOINT, "token": "tenant-tok"},
        )
    assert not other.called
    assert agent.called
    request = agent.calls.last.request
    assert request.headers["authorization"] == "Bearer tenant-tok"
    body = json.loads(request.content)
    assert body["message"]["parts"][0]["text"] == "Summarise Q3"
    assert body["context"] == {"k": "v"}
    assert result == {"output": "summary ready", "status": "completed"}


async def test_internal_endpoint_is_refused() -> None:
    result = await a2a_call.call_tool(
        "a2a_delegate_task", {"task": "hi"}, credentials={"url": "http://10.0.0.5/a2a"}
    )
    assert "error" in result


async def test_remote_error_is_a_failed_tool_call(public_dns: None) -> None:
    with respx.mock:
        respx.post(_ENDPOINT).mock(return_value=httpx.Response(502))
        result = await a2a_call.call_tool(
            "a2a_delegate_task", {"task": "hi"}, credentials={"url": _ENDPOINT}
        )
    assert "error" in result


async def test_missing_task_is_refused_without_a_call() -> None:
    result = await a2a_call.call_tool(
        "a2a_delegate_task", {"task": "  "}, credentials={"url": _ENDPOINT}
    )
    assert "error" in result


async def test_call_is_time_boxed(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def _fake(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"output": "ok", "status": "completed"}

    monkeypatch.setattr(a2a_call, "call_external_a2a_agent", _fake)
    await a2a_call.call_tool(
        "a2a_delegate_task", {"task": "hi"}, credentials={"url": _ENDPOINT, "timeout": "3600"}
    )
    assert 0 < seen["timeout"] <= a2a_call.MAX_A2A_TOOL_TIMEOUT_S


class _Registry:
    def __init__(self, cfg: MCPServerConfig) -> None:
        self.cfg = cfg

    async def get(self, server_id: str, *, tenant_ctx: Any) -> MCPServerConfig | None:
        return self.cfg if server_id == self.cfg.server_id else None

    def __getattr__(self, name: str) -> Any:
        return AsyncMock(return_value=None)


def _connector(url: str) -> MCPServerConfig:
    # What POST /connectors stores for a tenant's "A2A Agent" (canonical id).
    return MCPServerConfig(
        server_id="builtin-a2a", name="A2A Agent", url=url,
        auth_type="bearer", auth_config={"token": "tenant-tok"},
    )


async def test_agent_tool_call_reaches_the_registered_agent(public_dns: None) -> None:
    client = MCPClient(registry=_Registry(_connector(_ENDPOINT)))
    with respx.mock:
        route = respx.post(_ENDPOINT).mock(
            return_value=httpx.Response(200, json={"result": "done"})
        )
        result = await client.call_tool(
            server_id="builtin-a2a", tool_name="a2a_delegate_task",
            arguments={"task": "Draft the release notes"}, tenant_ctx=TENANT,
        )
    assert result.success is True, result.error
    assert route.calls.last.request.headers["authorization"] == "Bearer tenant-tok"


async def test_agent_tool_call_to_an_internal_registered_url_is_blocked() -> None:
    client = MCPClient(registry=_Registry(_connector("http://169.254.169.254/a2a")))
    result = await client.call_tool(
        server_id="builtin-a2a", tool_name="a2a_delegate_task",
        arguments={"task": "hi"}, tenant_ctx=TENANT,
    )
    assert result.success is False
