"""A goal is never ``complete`` with an empty answer.

Live ONPREM-ROUTING-FAILOVER evidence: a goal ended ``complete`` while its final
answer read as empty. Whatever the verifier LLM says, a run whose every step
output is empty / whitespace (or only a reasoning block) and whose tools produced
nothing is not a success: the verdict becomes a replan with reason
``empty_answer``, and a run that ends that way fails with
``terminal_reason=empty_answer``. A result that is a tool's output (a message
sent, a record written) or a synthesized sub-goal result is never "empty".
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent.graph import AgentGraph
from app.agent.nodes._helpers import empty_final_answer
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.providers.fake import FakeProvider
from app.services.failure_reason import terminal_reason_code
from app.tenancy.context import PlanTier, TenantContext

_T = TenantContext(tenant_id="verifier-empty-answer-t1", plan=PlanTier.ENTERPRISE, api_key_id="k")


def _graph(**kw: Any) -> AgentGraph:
    return AgentGraph(
        planner=FakeProvider(responses=["step 1"]),
        executor=FakeProvider(responses=["step output"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "looks done"}']),
        **kw,
    )


def _state(*steps: StepResult, goal: str = "Compute 12 multiplied by 12") -> AgentState:
    state = AgentState(goal=goal, tenant_ctx=_T)
    state.steps.extend(steps)
    return state


def _step(output: str = "", tool_calls: list[dict[str, Any]] | None = None) -> StepResult:
    return StepResult(description="compute", status=StepStatus.COMPLETE, output=output,
                      tool_calls=tool_calls or [])


# ── the predicate ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("output", ["", "   \n\t ", "<think>12*12 is 144</think>\n  "])
def test_blank_or_reasoning_only_output_is_an_empty_answer(output: str) -> None:
    assert empty_final_answer(_state(_step(output))) is True
    assert empty_final_answer(_state()) is True  # no step at all


def test_real_output_or_a_tool_result_is_not_empty() -> None:
    assert empty_final_answer(_state(_step("144"))) is False
    assert empty_final_answer(_state(_step("<think>hm</think>144"))) is False
    # The result IS the tool's action (a message sent): not empty without prose.
    assert empty_final_answer(_state(_step("", [{"tool": "slack_post", "output": ""}]))) is False
    # A failed tool call is not a result.
    assert empty_final_answer(
        _state(_step("", [{"tool": "slack_post", "success": False, "error": "x"}]))
    ) is True
    cited = _state(_step(""))
    cited.cited_answer = "144"
    assert empty_final_answer(cited) is False
    sup = _state()
    sup.context["supervisor_result"] = "the synthesized sub-goal result"
    assert empty_final_answer(sup) is False


# ── the verifier verdict ────────────────────────────────────────────────────


async def test_verifier_success_on_an_empty_answer_becomes_an_empty_answer_replan() -> None:
    state = _state(_step("   "))

    out = (await _graph()._node_verify({"agent_state": state, "tenant_ctx": _T}))["agent_state"]

    assert out.verification_success is False
    assert out.status is not GoalStatus.COMPLETE
    assert out.verification_feedback.startswith("empty_answer:")
    assert out.context["verification_retry"] is True
    assert out.context["_empty_answer"] is True


async def test_verifier_still_completes_a_goal_with_an_answer() -> None:
    state = _state(_step("144"))

    out = (await _graph()._node_verify({"agent_state": state, "tenant_ctx": _T}))["agent_state"]

    assert out.verification_success is True
    assert out.status is GoalStatus.COMPLETE
    assert "_empty_answer" not in out.context


async def test_verifier_completes_a_goal_whose_result_is_a_tool_action() -> None:
    state = _state(_step("", [{"tool": "slack_post", "output": "", "success": True}]),
                   goal="Post the daily summary to the team channel")

    out = (await _graph()._node_verify({"agent_state": state, "tenant_ctx": _T}))["agent_state"]

    assert out.verification_success is True
    assert out.status is GoalStatus.COMPLETE


# ── the terminal reason ─────────────────────────────────────────────────────


def _routed_to_end(graph: AgentGraph, state: AgentState, iteration: int) -> str:
    return graph._route({"agent_state": state, "tenant_ctx": _T, "iteration": iteration})


def test_a_run_that_ends_on_an_empty_answer_fails_with_terminal_reason_empty_answer() -> None:
    graph = _graph()
    state = _state(_step(""))
    state.verification_success = False
    state.verification_feedback = "empty_answer: the run produced no answer"
    state.context.update(_empty_answer=True, verification_retry=True)

    decision = _routed_to_end(graph, state, iteration=graph._max_iterations)

    assert decision == "max_iter"
    assert state.status is GoalStatus.FAILED
    assert state.error_message.startswith("empty_answer:")
    assert "max iterations" in state.error_message  # the underlying stop is kept
    assert state.context["terminal_reason"] == "empty_answer"
    assert terminal_reason_code("failed", state.error_message) == "empty_answer"


def test_budget_exhaustion_keeps_its_own_reason() -> None:
    graph = _graph()
    state = _state(_step(""))
    state.context.update(_empty_answer=True, _budget_exhausted=True)

    assert _routed_to_end(graph, state, iteration=1) == "max_iter"

    assert state.error_message.startswith("budget_exceeded")
    assert state.context["terminal_reason"] == "budget_exceeded"


def test_other_failures_are_not_relabelled() -> None:
    graph = _graph()
    state = _state(_step("an answer the verifier rejected"))
    state.verification_success = False
    state.verification_feedback = "wrong arithmetic"
    state.context["verification_retry"] = True

    assert _routed_to_end(graph, state, iteration=graph._max_iterations) == "max_iter"

    assert not state.error_message.startswith("empty_answer")
    assert terminal_reason_code("failed", state.error_message) == "error"
