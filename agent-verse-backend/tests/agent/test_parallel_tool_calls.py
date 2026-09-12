"""Strategy B — concurrent dispatch of a turn's additional tool calls, with the
per-call safety gates preserved."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from app.agent.graph import AgentGraph
from app.agent.state import AgentState, StepResult, StepStatus
from app.agent.tool_context import ToolContext, ToolRef
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="pb-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1", roles=("admin",))


class _RecordingMCP:
    def __init__(self, delay: float = 0.1) -> None:
        self.calls: list[str] = []
        self._delay = delay

    async def call_tool(self, *, server_id, tool_name, arguments, tenant_ctx):
        self.calls.append(tool_name)
        await asyncio.sleep(self._delay)
        return SimpleNamespace(
            success=True, output=f"out-{tool_name}", error=None, artifact_url="", artifact_name=""
        )


def _graph(mcp, autonomy_mode="bounded-autonomous"):
    return AgentGraph(
        planner=FakeProvider(),
        executor=FakeProvider(),
        verifier=FakeProvider(),
        mcp_client=mcp,
        autonomy_mode=autonomy_mode,
    )


def _state(tools: list[ToolRef]) -> AgentState:
    st = AgentState(goal="g", tenant_ctx=T)
    st.context["tool_context"] = ToolContext(connectors=[], tools=tools)
    st.steps.append(StepResult(description="step", status=StepStatus.RUNNING))
    return st


def _tool(name: str, *, auto_approve: bool = False) -> ToolRef:
    return ToolRef(
        server_id="srv", server_name="Srv", name=name, description="", input_schema={},
        auto_approve=auto_approve,
    )


@pytest.mark.asyncio
async def test_extra_read_tools_run_concurrently():
    mcp = _RecordingMCP(delay=0.15)
    g = _graph(mcp)
    st = _state([_tool("search_a"), _tool("search_b")])
    extras = [{"name": "search_a", "arguments": {}}, {"name": "search_b", "arguments": {}}]
    t0 = time.monotonic()
    res = await g._dispatch_parallel_extra_tool_calls(extras, "step", st, T, {"search_a", "search_b"})
    elapsed = time.monotonic() - t0
    assert {n for n, _ in res} == {"search_a", "search_b"}
    assert set(mcp.calls) == {"search_a", "search_b"}
    # concurrent: two 0.15s calls finish well under the 0.30s sequential total
    assert elapsed < 0.27
    assert len(st.steps[-1].tool_calls) == 2


@pytest.mark.asyncio
async def test_write_high_extra_not_dispatched_without_opt_in():
    mcp = _RecordingMCP()
    g = _graph(mcp)
    st = _state([_tool("send_message")])  # "send" -> write_high
    res = await g._dispatch_parallel_extra_tool_calls(
        [{"name": "send_message", "arguments": {}}], "step", st, T, {"send_message"}
    )
    assert mcp.calls == []  # never dispatched
    assert "requires approval" in res[0][1]


@pytest.mark.asyncio
async def test_write_high_extra_dispatched_with_connector_opt_in():
    mcp = _RecordingMCP()
    g = _graph(mcp)
    st = _state([_tool("send_message", auto_approve=True)])
    res = await g._dispatch_parallel_extra_tool_calls(
        [{"name": "send_message", "arguments": {}}], "step", st, T, {"send_message"}
    )
    assert mcp.calls == ["send_message"]
    assert res[0][1] == "out-send_message"


@pytest.mark.asyncio
async def test_unknown_tool_rejected_not_dispatched():
    mcp = _RecordingMCP()
    g = _graph(mcp)
    st = _state([_tool("search_a")])
    res = await g._dispatch_parallel_extra_tool_calls(
        [{"name": "ghost_tool", "arguments": {}}], "step", st, T, {"search_a"}
    )
    assert mcp.calls == []
    assert "rejected" in res[0][1] or "not found" in res[0][1]


@pytest.mark.asyncio
async def test_one_failure_does_not_sink_batch():
    class _FlakyMCP(_RecordingMCP):
        async def call_tool(self, *, server_id, tool_name, arguments, tenant_ctx):
            self.calls.append(tool_name)
            if tool_name == "boom":
                raise RuntimeError("kaboom")
            return SimpleNamespace(success=True, output=f"out-{tool_name}", error=None)

    mcp = _FlakyMCP()
    g = _graph(mcp)
    st = _state([_tool("boom"), _tool("search_b")])
    res = await g._dispatch_parallel_extra_tool_calls(
        [{"name": "boom", "arguments": {}}, {"name": "search_b", "arguments": {}}],
        "step", st, T, {"boom", "search_b"},
    )
    outs = dict(res)
    assert "error" in outs["boom"].lower()
    assert outs["search_b"] == "out-search_b"
