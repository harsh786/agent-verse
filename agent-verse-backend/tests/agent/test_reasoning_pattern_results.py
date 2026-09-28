"""Reasoning / multi-agent pattern outputs are used, and never overwrite grounded output.

Before:
* self_refine / self_consistency replaced ``last_step.output`` — including real tool
  output — with an LLM rewrite before verification, so the verifier graded
  ungrounded text as if it were the tool result;
* ``tot_answer`` / ``debate_result`` / ``supervisor_result`` / the API debate
  ``debate_consensus`` and the CoT think output were written and never read.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent.graph import AgentGraph
from app.agent.state import AgentState, StepResult, StepStatus
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.asyncio

T = TenantContext(tenant_id="pattern-results", plan=PlanTier.ENTERPRISE, api_key_id="k")

TOOL_OUTPUT = '{"issues": [{"key": "ABC-1", "summary": "real issue"}], "total": 1}'


def _graph(executor: FakeProvider | None = None, planner: FakeProvider | None = None) -> AgentGraph:
    return AgentGraph(
        planner=planner or FakeProvider(responses=['{"steps": ["report"]}']),
        executor=executor or FakeProvider(responses=["rewritten ungrounded text"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
    )


def _state_with_step(tool_grounded: bool) -> AgentState:
    state = AgentState(goal="list open issues", tenant_ctx=T)
    step = StepResult(description="search issues", status=StepStatus.COMPLETE, output=TOOL_OUTPUT)
    if tool_grounded:
        step.tool_calls.append({"name": "jira_search", "arguments": {}})
    state.steps.append(step)
    return state


async def test_self_refine_never_overwrites_tool_grounded_output() -> None:
    executor = FakeProvider(responses=["rewritten ungrounded text"])
    graph = _graph(executor=executor)
    state = _state_with_step(tool_grounded=True)

    await graph._node_refine({"agent_state": state})

    assert state.steps[-1].output == TOOL_OUTPUT
    assert executor.call_history == []
    evidence = state.context["reasoning_evidence"][-1]
    assert evidence["status"] == "skipped"
    assert evidence["limit_reason"] == "grounded_tool_output"


async def test_self_refine_still_improves_pure_llm_output() -> None:
    graph = _graph(executor=FakeProvider(responses=["a better answer"]))
    state = _state_with_step(tool_grounded=False)

    await graph._node_refine({"agent_state": state})

    assert state.steps[-1].output == "a better answer"


async def test_self_consistency_never_overwrites_tool_grounded_output() -> None:
    executor = FakeProvider(responses=["majority vote rewrite"] * 5)
    graph = _graph(executor=executor)
    state = _state_with_step(tool_grounded=True)

    await graph._node_self_consistency({"agent_state": state})

    assert state.steps[-1].output == TOOL_OUTPUT
    assert executor.call_history == []
    assert state.context["reasoning_evidence"][-1]["limit_reason"] == "grounded_tool_output"


async def test_self_refine_failure_is_recorded_not_claimed() -> None:
    class _Broken(FakeProvider):
        async def complete(self, request: Any) -> Any:  # type: ignore[override]
            raise RuntimeError("provider down")

    graph = _graph(executor=_Broken())
    state = _state_with_step(tool_grounded=False)

    await graph._node_refine({"agent_state": state})

    assert state.steps[-1].output == TOOL_OUTPUT
    assert state.context["reasoning_evidence"][-1]["status"] == "failed"


async def test_planner_context_includes_pattern_results() -> None:
    graph = _graph()
    state = AgentState(goal="g", tenant_ctx=T)
    state.context["supervisor_result"] = "SUB-AGENT SYNTHESIS"
    state.context["debate_consensus"] = "API DEBATE CONSENSUS"
    state.context["tot_answer"] = "TOT ANSWER"

    parts = graph._pattern_result_parts(state)
    joined = "\n".join(parts)

    assert "SUB-AGENT SYNTHESIS" in joined
    assert "ALREADY EXECUTED" in joined
    assert "API DEBATE CONSENSUS" in joined
    assert "TOT ANSWER" in joined


async def test_think_reasoning_reaches_planner_once_and_is_not_checkpointed() -> None:
    planner = FakeProvider(responses=["Step back: the key constraint is X."])
    graph = _graph(planner=planner)
    state = AgentState(goal="g", tenant_ctx=T)

    await graph._node_think({"agent_state": state, "tenant_ctx": T})

    # Never stored in (checkpointed) state context.
    assert "key constraint is X" not in str(state.context)
    first = "\n".join(graph._pattern_result_parts(state))
    assert "key constraint is X" in first
    # Consumed: not injected again on replan.
    assert "key constraint is X" not in "\n".join(graph._pattern_result_parts(state))


async def test_planner_prompt_carries_supervisor_result() -> None:
    planner = FakeProvider(responses=['{"steps": ["summarize the sub-agent results"]}'] * 3)
    graph = _graph(planner=planner)
    state = AgentState(goal="research and summarize", tenant_ctx=T)
    state.context["supervisor_result"] = "SUB-AGENT SYNTHESIS 42"

    await graph._node_plan({"agent_state": state, "tenant_ctx": T, "rag_context": ""})

    prompts = [
        str(m.content)
        for req in planner.call_history
        for m in getattr(req, "messages", [])
        if getattr(m, "role", "") == "user"
    ]
    assert any("SUB-AGENT SYNTHESIS 42" in p for p in prompts)
