"""B7 live open item 1: the grounding gates rejected correct short answers.

* "391" for "What is 17*23?" held a concrete number found in no tool output and
  not in the goal: the keyword gate flagged it (and replanned it on a high-risk
  goal). Arithmetic in the goal / the agent's step texts is now recomputed and
  added as evidence (``app.agent.arithmetic_evidence``); a WRONG result is still
  flagged.
* "ACK" (live: a high-risk "Note for the record ... Reply ACK" goal) holds no
  atomic claim, and the NLI claim check scored 0 claims as 0/1 = 0.00 < 0.70,
  failing the goal after three replans. A claim-less answer is now vacuously
  supported unless the evidence contradicts it.
* On a goal that needs no retrieval and is not high-risk, a short answer may rest
  on the agent's own step outputs. Retrieval-required goals keep flagging
  unsupported claims.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent.arithmetic_evidence import derive_arithmetic, render_evidence, wrong_results
from app.agent.graph import AgentGraph
from app.agent.nodes._helpers import _is_high_risk_step
from app.agent.state import AgentState, StepResult, StepStatus
from app.intelligence.grounding_verification import verify_grounding
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

_T = TenantContext(tenant_id="b7-grounding-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")


# ── arithmetic evidence ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("What is 17*23?", [("17*23", "391")]),
        ("what is 17 x 23", [("17*23", "391")]),
        ("What is 17 × 23?", [("17*23", "391")]),
        ("17 times 23", [("17*23", "391")]),
        ("Compute (3 + 4) * 2.", [("(3+4)*2", "14")]),
        ("10/4", [("10/4", "2.5")]),
        ("2^10", [("2^10", "1024")]),
        ("1,024 * 2", [("1024*2", "2048")]),
        ("what is 100 - 7?", [("100-7", "93")]),
    ],
)
def test_arithmetic_is_recomputed(text: str, expected: list[tuple[str, str]]) -> None:
    assert [(f.expression, f.rendered) for f in derive_arithmetic([text])] == expected


@pytest.mark.parametrize(
    "text",
    [
        "deadline 2026-10-06",  # a date, not 2026 - 10 - 6
        "ticket SUP-12 and JIRA-7",  # ids
        "call 555-1234",  # a phone number
        "2**1000",  # bounded exponent
        "1/0",
        "no numbers here",
    ],
)
def test_non_arithmetic_is_not_derived(text: str) -> None:
    assert derive_arithmetic([text]) == []


def test_wrong_results() -> None:
    goal = "What is 17*23?"
    assert wrong_results("391", goal) == []
    assert wrong_results("The answer is 391.", goal) == []
    assert wrong_results("392", goal) == ["392"]
    assert wrong_results("The answer is 392.", goal) == ["392"]
    assert wrong_results("17 x 23 = 392", "") == ["392"]
    assert wrong_results("17 x 23 = 391", "") == []
    assert wrong_results("ACK", "Reply ACK") == []
    # Two expressions in the goal: a bare number is not attributable to one.
    assert wrong_results("5", "What are 2+2 and 3+3?") == []


# ── NLI claim check: claim-less answers ──────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["ACK", "391", "Done", "OK."])
async def test_claimless_answer_is_vacuously_supported(answer: str) -> None:
    verdict = await verify_grounding(answer, ["Supplier SUP-1 prefers email. Reply ACK"])
    assert verdict.safe_to_emit is True
    assert verdict.claim_score == 1.0
    assert verdict.reasons == []


@pytest.mark.asyncio
async def test_claimless_answer_contradicted_by_evidence_is_not_safe() -> None:
    verdict = await verify_grounding(
        "Done.", ["The delete was not done: permission denied. Nothing was done."]
    )
    assert verdict.safe_to_emit is False
    assert verdict.contradicted_claims == ["Done."]


@pytest.mark.asyncio
async def test_context_evidence_supports_claims_without_shifting_citations() -> None:
    ctx = [render_evidence(derive_arithmetic(["17*23"]))]
    verdict = await verify_grounding(
        "The answer is 391.", ["What is 17*23?"], context_evidence=ctx
    )
    assert verdict.safe_to_emit is True
    # Without it the same sentence is unsupported.
    assert (await verify_grounding("The answer is 391.", ["What is 17*23?"])).safe_to_emit is False


# ── through the verifier node ────────────────────────────────────────────────


def _graph(collections: list[str] | None = None) -> AgentGraph:
    graph = AgentGraph(
        planner=FakeProvider(responses=["step 1"]),
        executor=FakeProvider(responses=["step output"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "looks done"}']),
    )
    if collections:
        graph._agent_collection_ids = list(collections)
    return graph


def _state(
    goal: str,
    output: str,
    *,
    tool_output: str | None = None,
    cited: str | None = None,
    description: str = "answer the question",
) -> AgentState:
    state = AgentState(goal=goal, tenant_ctx=_T)
    state.cited_answer = cited
    state.steps.append(
        StepResult(
            description=description,
            status=StepStatus.COMPLETE,
            output=output,
            tool_calls=[{"output": tool_output}] if tool_output else [],
        )
    )
    return state


async def _verify(graph: AgentGraph, state: AgentState) -> tuple[AgentState, list[dict]]:
    events: list[dict[str, Any]] = []

    async def _capture(event: dict[str, Any]) -> None:
        events.append(event)

    graph._emit = _capture  # type: ignore[method-assign]
    result = await graph._node_verify({"agent_state": state, "tenant_ctx": _T})
    return result["agent_state"], events


def _grounding_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e.get("type") in ("grounding_warning", "claim_grounding_warning")]


@pytest.mark.asyncio
async def test_correct_arithmetic_answer_is_not_flagged() -> None:
    state, events = await _verify(_graph(), _state("What is 17*23?", "391"))

    assert state.verification_success is True
    assert state.context["final_answer_grounded"] is True
    assert state.context["claim_grounding_safe"] is True
    assert state.ungrounded_claims == []
    assert _grounding_events(events) == []


@pytest.mark.asyncio
async def test_correct_arithmetic_passes_the_strict_high_risk_gate() -> None:
    """High-risk goal WITH tool evidence (fail-closed path): the recomputation grounds it."""
    goal = "Compute 17*23 for the ticket before we delete the staging table"
    assert _is_high_risk_step(goal)
    state, events = await _verify(
        _graph(), _state(goal, "391", tool_output="staging table listing: 4 tables")
    )

    assert state.verification_success is True
    assert state.context["final_answer_grounded"] is True
    assert state.context["claim_grounding_safe"] is True
    assert _grounding_events(events) == []


@pytest.mark.asyncio
async def test_wrong_arithmetic_is_still_rejected_on_a_high_risk_goal() -> None:
    goal = "Compute 17*23 for the ticket before we delete the staging table"
    state, _ = await _verify(
        _graph(), _state(goal, "392", tool_output="staging table listing: 4 tables")
    )

    assert state.verification_success is False
    assert state.context["verification_retry"] is True
    assert "392" in state.ungrounded_claims
    assert "grounding" in state.verification_feedback


@pytest.mark.asyncio
async def test_wrong_arithmetic_is_flagged_on_a_normal_goal() -> None:
    """Even where own step outputs count as evidence, a wrong stated result is flagged."""
    state, events = await _verify(_graph(), _state("What is 17*23?", "392"))

    assert state.context["final_answer_grounded"] is False
    assert "392" in state.ungrounded_claims
    assert [e["type"] for e in _grounding_events(events)] == ["grounding_warning"]


@pytest.mark.asyncio
async def test_live_ack_answer_on_a_high_risk_goal_is_accepted() -> None:
    """The live B7 case: claim support 0.00 < 0.70 failed a correct "ACK"."""
    goal = "Note for the record: supplier SUP-9 prefers invoices by email. Reply ACK"
    assert _is_high_risk_step(goal)
    state, events = await _verify(
        _graph(), _state(goal, "ACK", tool_output="memory stored: supplier preference")
    )

    assert state.verification_success is True
    assert state.context["claim_grounding_safe"] is True
    assert state.context["claim_grounding_score"] == 1.0
    assert _grounding_events(events) == []


@pytest.mark.asyncio
async def test_short_direct_fact_from_own_step_output_is_not_flagged() -> None:
    state, events = await _verify(
        _graph(),
        _state(
            "What is the capital of France and how many regions does it have?",
            "The capital of France is Paris. France has 18 regions.",
            cited="Paris is the capital; France has 18 regions.",
        ),
    )

    assert state.context["final_answer_grounded"] is True
    assert state.context["claim_grounding_safe"] is True
    assert state.context["final_answer_grounding_basis"]["own_step_outputs"] is True
    assert _grounding_events(events) == []
    assert "UNGROUNDED" not in (state.cited_answer or "")


@pytest.mark.asyncio
async def test_retrieval_required_goal_still_flags_unsupported_claims() -> None:
    """Agent with explicit collections: own step outputs are NOT evidence."""
    state = _state("What was Q3 revenue?", "Q3 revenue was 4,200 units.")
    state.context["rag_knowledge"] = "Quarterly report: Q3 revenue was 3,100 units."
    state, events = await _verify(_graph(collections=["kb-1"]), state)

    assert state.context["final_answer_grounding_basis"]["retrieval_required"] is True
    assert state.context["final_answer_grounding_basis"]["own_step_outputs"] is False
    assert state.context["final_answer_grounded"] is False
    assert state.context["claim_grounding_safe"] is False
    # The keyword gate's number claim ("200" of "4,200") and the NLI sentence.
    assert "200" in state.ungrounded_claims
    assert "Q3 revenue was 4,200 units." in state.ungrounded_claims
    assert {e["type"] for e in _grounding_events(events)} == {
        "grounding_warning",
        "claim_grounding_warning",
    }


@pytest.mark.asyncio
async def test_retrieval_required_short_number_is_still_flagged() -> None:
    state = _state("What was Q3 revenue?", "4200")
    state.context["rag_knowledge"] = "Quarterly report: Q3 revenue was 3,100 units."
    state, _ = await _verify(_graph(collections=["kb-1"]), state)

    assert state.context["final_answer_grounded"] is False
    assert "4200" in state.ungrounded_claims


@pytest.mark.asyncio
async def test_long_answer_does_not_rest_on_own_outputs() -> None:
    """Only SHORT answers may rest on the agent's own step outputs."""
    long_answer = ("The migration plan covers every service in detail. " * 8) + (
        "It moved 4721 records."
    )
    state, _ = await _verify(_graph(), _state("Summarise the migration plan", long_answer))

    assert state.context["final_answer_grounding_basis"]["own_step_outputs"] is False
    assert state.context["final_answer_grounded"] is False
    assert "4721" in state.ungrounded_claims


@pytest.mark.asyncio
async def test_high_risk_short_answer_does_not_rest_on_own_outputs() -> None:
    """CORE-03 stays: a high-risk text-only answer inventing a count is ungrounded."""
    goal = "Delete the stale rows from the production orders table"
    state, _ = await _verify(_graph(), _state(goal, "Deleted 4721 rows."))

    assert state.context["final_answer_grounding_basis"]["own_step_outputs"] is False
    assert state.verification_success is False
    assert "4721" in state.ungrounded_claims


# ── through the executor's per-step gate ─────────────────────────────────────


def _exec_graph(output: str) -> AgentGraph:
    return AgentGraph(
        planner=FakeProvider(responses=["plan"]),
        executor=FakeProvider(responses=[output]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
    )


def _exec_state(goal: str) -> AgentState:
    state = AgentState(goal=goal, tenant_ctx=_T)
    state.steps.append(
        StepResult(
            description="compute it",
            status=StepStatus.RUNNING,
            tool_calls=[{"tool_name": "lookup", "output": "staging table listing: 4 tables"}],
        )
    )
    return state


@pytest.mark.asyncio
async def test_executor_gate_grounds_a_correct_computed_step() -> None:
    goal = "Compute 17*23 for the ticket before we delete the staging table"
    state = _exec_state(goal)
    await _exec_graph("391")._execute_step("Compute 17*23", state, _T)

    assert state.consecutive_ungrounded == 0
    assert state.steps[-1].status != StepStatus.UNGROUNDED
    assert state.ungrounded_claims == []


@pytest.mark.asyncio
async def test_executor_gate_still_flags_a_wrong_computed_step() -> None:
    goal = "Compute 17*23 for the ticket before we delete the staging table"
    state = _exec_state(goal)
    await _exec_graph("392")._execute_step("Compute 17*23", state, _T)

    assert state.consecutive_ungrounded == 1
    assert "392" in state.ungrounded_claims


@pytest.mark.asyncio
async def test_short_answer_with_tool_evidence_does_not_rest_on_own_outputs() -> None:
    """Where tools gathered evidence, the answer must be grounded in it, not in itself."""
    state, events = await _verify(
        _graph(),
        _state(
            "Summarise the quarterly report",
            "Revenue was 987654 across regions.",
            tool_output="the report text mentions growth",
        ),
    )

    assert state.context["final_answer_grounding_basis"]["own_step_outputs"] is False
    assert state.context["final_answer_grounded"] is False
    assert "987654" in state.ungrounded_claims


def test_dates_in_parentheses_are_not_arithmetic() -> None:
    assert derive_arithmetic(["removed rec-977 (last used 2024-12-31)."]) == []
