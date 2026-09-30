"""MEM-01: the executor feeds and uses ToolReliabilityStore.

* every real MCP dispatch records its outcome (success, latency, error class);
* the executor reads the store with the right signature and deprioritises
  unreliable tools / drops blacklisted ones from the offered tool list;
* a store outage is logged and surfaced, never silently treated as "all good".
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent.graph import AgentGraph
from app.agent.state import AgentState, StepResult, StepStatus
from app.agent.tool_context import ToolContext, ToolRef
from app.memory.tool_reliability import ToolReliabilityStore, ToolReliabilityUnavailableError
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="rel-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")


class _Result:
    def __init__(self, success: bool, output: object, error: str) -> None:
        self.success = success
        self.output = output
        self.error = error


class _MCP:
    def __init__(self, *, success: bool = True, raises: bool = False) -> None:
        self._success = success
        self._raises = raises

    async def call_tool(self, **_kw: object) -> object:
        if self._raises:
            raise TimeoutError("connector timed out")
        if self._success:
            return _Result(True, {"ok": True}, "")
        return _Result(False, None, "HTTP 502 bad gateway")


def _tc(*names: str) -> ToolContext:
    return ToolContext(
        connectors=[],
        tools=[
            ToolRef(server_id=n, server_name="Custom", name=n, description=f"tool {n}",
                    input_schema={})
            for n in names
        ],
    )


def _graph(executor: FakeProvider, **kw: Any) -> AgentGraph:
    return AgentGraph(
        planner=FakeProvider(responses=["plan"]),
        executor=executor,
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        **kw,
    )


def _state(step: str) -> AgentState:
    s = AgentState(goal="goal", tenant_ctx=T)
    s.steps.append(StepResult(description=step, status=StepStatus.RUNNING))
    return s


@pytest.mark.parametrize(
    ("mcp", "expect_success"),
    [(_MCP(success=True), True), (_MCP(success=False), False), (_MCP(raises=True), False)],
)
async def test_real_tool_dispatch_records_outcome(mcp: _MCP, expect_success: bool) -> None:
    store = ToolReliabilityStore()
    graph = _graph(
        FakeProvider(responses=['{"tool": "get_status", "arguments": {}}', "done"]),
        mcp_client=mcp,
        tool_reliability_store=store,
    )
    state = _state("check status")
    state.context["tool_context"] = _tc("get_status")

    try:
        await graph._execute_step("check status", state, T)
    except Exception:
        pass  # a raising connector may propagate; the outcome must still be recorded

    stats = await store.get_reliability(tenant_id=T.tenant_id, tool_name="get_status")
    assert stats["total_calls"] == 1
    assert stats["success_count"] == (1 if expect_success else 0)
    assert stats["failure_count"] == (0 if expect_success else 1)


async def test_unreliable_tool_is_deprioritised_and_hinted() -> None:
    store = ToolReliabilityStore()
    for _ in range(4):
        await store.record(tenant_id=T.tenant_id, tool_name="flaky_search", success=False)
    await store.record(tenant_id=T.tenant_id, tool_name="good_search", success=True)
    executor = FakeProvider(responses=["done"])
    graph = _graph(executor, tool_reliability_store=store)
    state = _state("look things up")
    state.context["tool_context"] = _tc("flaky_search", "good_search")

    await graph._execute_step("look things up", state, T)

    req = executor.call_history[0]
    assert [t.name for t in req.tools][-1] == "flaky_search"
    assert state.context["_unreliable_tools"] == ["flaky_search"]
    user_msg = req.messages[-1].content
    assert "flaky_search" in user_msg and "reliability" in user_msg.lower()


async def test_blacklisted_tool_is_not_offered_when_alternatives_exist() -> None:
    store = ToolReliabilityStore()
    await store.blacklist(tenant_id=T.tenant_id, tool_name="bad_tool", reason="self_improvement")
    executor = FakeProvider(responses=["done"])
    graph = _graph(executor, tool_reliability_store=store)
    state = _state("do it")
    state.context["tool_context"] = _tc("bad_tool", "ok_tool")

    await graph._execute_step("do it", state, T)

    assert [t.name for t in executor.call_history[0].tools] == ["ok_tool"]


async def test_store_outage_is_flagged_not_silent() -> None:
    class _Down:
        async def get_unreliable_tools(self, **_kw: object) -> list[dict[str, object]]:
            raise ToolReliabilityUnavailableError("db down")

        async def record(self, **_kw: object) -> None:
            return None

    executor = FakeProvider(responses=["done"])
    graph = _graph(executor, tool_reliability_store=_Down())
    state = _state("do it")
    state.context["tool_context"] = _tc("ok_tool")

    out = await graph._execute_step("do it", state, T)

    assert out
    assert state.context.get("_tool_reliability_degraded") is True
