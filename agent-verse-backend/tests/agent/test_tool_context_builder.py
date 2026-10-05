"""TOOLCTX-01/02/03/06/07: one planner tool context for the web and the worker path.

The in-process path (GoalService._build_tool_context) and the Celery worker
(_build_worker_mcp_context) built different tool sets for the same goal. Both
now go through ``app.agent.tool_context_builder.build_goal_tool_context``.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any

import fakeredis.aioredis
import pytest

from app.agent import tool_context_builder as tcb
from app.agent.tool_context_builder import build_goal_tool_context
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")


class _Client:
    def __init__(self, fail: set[str] | None = None) -> None:
        self.fail = fail or set()
        self.calls: list[str] = []

    async def discover_tools(self, *, server_id: str, tenant_ctx: Any) -> list[Any]:
        self.calls.append(server_id)
        if server_id in self.fail:
            raise ConnectionError("connector unreachable")
        return [
            SimpleNamespace(
                name=f"tool_{server_id[:4]}",
                server_name="srv",
                description="d",
                input_schema={"type": "object"},
            )
        ]


async def _registry(*cfgs: MCPServerConfig) -> tuple[MCPRegistry, list[str]]:
    reg = MCPRegistry(fakeredis.aioredis.FakeRedis())
    ids = [await reg.register(c, tenant_ctx=CTX) for c in cfgs]
    return reg, ids


async def test_auto_approve_is_carried() -> None:
    reg, ids = await _registry(
        MCPServerConfig(name="a", url="https://a.example.com/mcp", auto_approve=True),
        MCPServerConfig(name="b", url="https://b.example.com/mcp"),
    )
    ctx = await build_goal_tool_context(
        registry=reg,
        mcp_client=_Client(),
        tenant_ctx=CTX,
        connector_ids=ids,
        include_rpa=False,
    )
    by_server = {t.server_id: t.auto_approve for t in ctx.tools}
    assert by_server == {ids[0]: True, ids[1]: False}


async def test_no_agent_offers_the_tenants_connectors_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tcb, "MAX_TENANT_CONNECTORS", 2)
    reg, ids = await _registry(
        *(MCPServerConfig(name=f"c{i}", url=f"https://c{i}.example.com/mcp") for i in range(3))
    )
    client = _Client()
    ctx = await build_goal_tool_context(
        registry=reg, mcp_client=client, tenant_ctx=CTX, connector_ids=None, include_rpa=False
    )
    assert len(client.calls) == 2 and set(client.calls) <= set(ids)
    assert ctx.connectors[0]["connectors_truncated"] == 2


async def test_unreachable_connector_is_recorded_not_fatal() -> None:
    reg, ids = await _registry(
        MCPServerConfig(name="up", url="https://up.example.com/mcp"),
        MCPServerConfig(name="down", url="https://down.example.com/mcp"),
    )
    ctx = await build_goal_tool_context(
        registry=reg,
        mcp_client=_Client(fail={ids[1]}),
        tenant_ctx=CTX,
        connector_ids=ids,
        include_rpa=False,
    )
    assert [t.server_id for t in ctx.tools] == [ids[0]]
    assert ctx.connectors[0]["connector_errors"][0]["connector_id"] == ids[1]


async def test_rpa_tools_only_where_playwright_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    reg, _ = await _registry()
    monkeypatch.setattr(tcb, "rpa_tools_available", lambda: False)
    without = await build_goal_tool_context(
        registry=reg, mcp_client=_Client(), tenant_ctx=CTX, connector_ids=[]
    )
    monkeypatch.setattr(tcb, "rpa_tools_available", lambda: True)
    with_rpa = await build_goal_tool_context(
        registry=reg, mcp_client=_Client(), tenant_ctx=CTX, connector_ids=[]
    )
    assert not any(t.server_id == "rpa" for t in without.tools)
    assert any(t.server_id == "rpa" for t in with_rpa.tools)


async def test_tool_selector_is_applied() -> None:
    reg, ids = await _registry(MCPServerConfig(name="a", url="https://a.example.com/mcp"))

    class _Sel:
        async def select(self, *, goal: str, tools: list[Any], tenant_ctx: Any) -> Any:
            return SimpleNamespace(
                selected=tools[:1], signature=[], names_only=[], rpa_included=False
            )

    ctx = await build_goal_tool_context(
        registry=reg,
        mcp_client=_Client(),
        tenant_ctx=CTX,
        connector_ids=ids,
        goal="do it",
        tool_selector=_Sel(),
        include_rpa=False,
    )
    assert ctx.tool_prompt_override is not None and len(ctx.tools) == 1


async def test_web_path_uses_the_shared_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    """GoalService with agent_id=None now offers the tenant's connectors (was RPA only)."""
    from app.services.goal_service import GoalService

    reg, ids = await _registry(
        MCPServerConfig(name="a", url="https://a.example.com/mcp", auto_approve=True)
    )
    client = _Client()
    client._registry = reg  # type: ignore[attr-defined]
    svc = GoalService.__new__(GoalService)
    svc._app_state = SimpleNamespace(mcp_client=client, mcp_registry=reg, tool_selector=None)
    svc._agent_store = None
    monkeypatch.setattr(tcb, "rpa_tools_available", lambda: False)
    ctx = await svc._build_tool_context(None, CTX, goal="g")
    assert [(t.server_id, t.auto_approve) for t in ctx.tools] == [(ids[0], True)]


def test_worker_path_uses_the_shared_builder() -> None:
    from app.scaling import tasks

    src = inspect.getsource(tasks)
    worker = src[src.index("async def _build_worker_mcp_context") :]
    worker = worker[: worker.index("if not _loop_is_patched")]
    assert "build_goal_tool_context(" in worker
    assert "build_worker_tool_selector(" in worker
