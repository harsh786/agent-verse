"""a02-F030-04: a stale cached result is never consumed as a live successful call.

With the connector's circuit open, ``MCPClient.call_tool`` serves the read cache's
stale copy as ``ToolCallResult(success=True, stale=True)``. Nothing read
``.stale``, so the agent, workflow steps and the connector test treated it as a
live call. Now every consumer flags it and shows a notice.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.agent.graph import AgentGraph
from app.agent.state import AgentState, StepResult, StepStatus
from app.agent.tool_context import ToolContext, ToolRef
from app.mcp.client import ToolCallResult, stale_result_notice, with_stale_notice
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.steps.tool_step import ToolStepNode
from tests.agent.nodes.test_executor_approved_action_idempotency import _Human

T = TenantContext(tenant_id="stale-t1", plan=PlanTier.ENTERPRISE, api_key_id="k")
STALE = ToolCallResult(
    tool_name="mongodb_find", success=True, output={"rows": [1]}, server_id="mongo-1", stale=True
)
LIVE = ToolCallResult(tool_name="mongodb_find", success=True, output={"rows": [1]})


def test_notice_only_for_stale_results() -> None:
    assert stale_result_notice(LIVE) == ""
    assert with_stale_notice(LIVE, "x") == "x"
    notice = stale_result_notice(STALE)
    assert "STALE CACHED RESULT" in notice and "mongo-1" in notice
    assert with_stale_notice(STALE, "rows").startswith(notice)
    assert with_stale_notice(STALE, "rows").endswith("rows")


class _Server:
    def __init__(self, result: ToolCallResult) -> None:
        self.result = result

    async def call_tool(self, **_kwargs: Any) -> ToolCallResult:
        return self.result

    async def call_tool_by_name(self, **_kwargs: Any) -> ToolCallResult:
        return self.result


def _graph(server: _Server) -> AgentGraph:
    call = json.dumps({"tool": "mongodb_find", "arguments": {"collection": "orders"}})
    return AgentGraph(
        planner=FakeProvider(responses=["plan"]),
        executor=FakeProvider(responses=[call]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        hitl_gateway=_Human(),
        mcp_client=server,
        autonomy_mode="bounded-autonomous",
    )


def _agent_state() -> AgentState:
    state = AgentState(goal="find the order", tenant_ctx=T)
    state.goal_id = "g-stale"
    state.context["tool_context"] = ToolContext(
        connectors=[],
        tools=[
            ToolRef(server_id="mongo-1", server_name="orders-db", name="mongodb_find",
                    description="find documents", input_schema={}),
        ],
    )
    state.steps.append(StepResult(description="Find order", status=StepStatus.RUNNING))
    return state


@pytest.mark.asyncio
async def test_agent_step_output_carries_the_notice_and_the_call_is_flagged() -> None:
    state = _agent_state()
    out = await _graph(_Server(STALE))._execute_step("Find order", state, T)
    assert "STALE CACHED RESULT" in out
    assert state.steps[-1].tool_calls[-1]["stale"] is True


@pytest.mark.asyncio
async def test_live_agent_step_output_has_no_notice() -> None:
    state = _agent_state()
    out = await _graph(_Server(LIVE))._execute_step("Find order", state, T)
    assert "STALE CACHED RESULT" not in out
    assert state.steps[-1].tool_calls[-1]["stale"] is False


@pytest.mark.asyncio
async def test_workflow_tool_step_output_is_flagged_stale() -> None:
    definition = StepDefinition.model_validate(
        {"id": "s1", "type": "tool", "tool": "mongodb_find", "input": {"collection": "c"}}
    )
    node = ToolStepNode(definition, ContextResolver(), mcp_client=_Server(STALE))
    state: dict[str, Any] = {
        "run_id": "r1", "tenant_id": T.tenant_id, "tenant_ctx": T, "inputs": {},
        "step_outputs": {}, "vars": {}, "is_test_run": False, "mock_overrides": {},
    }
    out = await node.execute(state)  # type: ignore[arg-type]
    step_out = out["step_outputs"]["s1"]
    assert step_out["stale"] is True
    assert "STALE CACHED RESULT" in step_out["notice"]
