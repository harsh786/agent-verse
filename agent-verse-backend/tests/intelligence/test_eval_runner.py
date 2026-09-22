"""Tests for EvalRunner safety score from real events."""
from __future__ import annotations

import pytest

from app.agent.state import AgentState, GoalStatus
from app.intelligence.eval_runner import EvalRunner
from app.tenancy.context import PlanTier, TenantContext


def _ctx() -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")


def test_safety_score_zero_deny_events():
    """No deny events → safety = 1.0."""
    runner = EvalRunner()
    state = AgentState(
        goal_id="g1",
        goal="test",
        tenant_ctx=_ctx(),
        status=GoalStatus.COMPLETE,
        steps=[],
        verification_success=True,
        events=[],
    )
    scorecard = runner.score(state=state, tenant_ctx=_ctx())
    assert scorecard.scores["safety"] == 1.0


def test_safety_score_one_deny_event():
    """One DENY event → safety = 0.75."""
    runner = EvalRunner()
    state = AgentState(
        goal_id="g2",
        goal="test",
        tenant_ctx=_ctx(),
        status=GoalStatus.COMPLETE,
        steps=[],
        verification_success=True,
        events=[{"type": "tool_call_denied", "tool": "shell:execute"}],
    )
    scorecard = runner.score(state=state, tenant_ctx=_ctx())
    assert scorecard.scores["safety"] == pytest.approx(0.75)


def test_safety_score_clamps_at_zero():
    """Many DENY events → safety = 0.0 (not negative)."""
    runner = EvalRunner()
    state = AgentState(
        goal_id="g3",
        goal="test",
        tenant_ctx=_ctx(),
        status=GoalStatus.FAILED,
        steps=[],
        verification_success=False,
        events=[{"action_level": "DENY"} for _ in range(10)],
    )
    scorecard = runner.score(state=state, tenant_ctx=_ctx())
    assert scorecard.scores["safety"] == 0.0


def test_safety_score_action_level_deny():
    """action_level=DENY events are counted."""
    runner = EvalRunner()
    state = AgentState(
        goal_id="g4",
        goal="test",
        tenant_ctx=_ctx(),
        status=GoalStatus.COMPLETE,
        steps=[],
        verification_success=True,
        events=[
            {"action_level": "DENY", "tool": "shell:rm"},
            {"action_level": "DENY", "tool": "db:drop"},
        ],
    )
    scorecard = runner.score(state=state, tenant_ctx=_ctx())
    assert scorecard.scores["safety"] == pytest.approx(0.5)


def test_safety_score_no_events_attribute():
    """State with no events list → safety = 1.0."""
    runner = EvalRunner()
    state = AgentState(
        goal="test",
        tenant_ctx=_ctx(),
        status=GoalStatus.COMPLETE,
        steps=[],
        verification_success=True,
    )
    # Ensure events is empty by default
    state.events = []
    scorecard = runner.score(state=state, tenant_ctx=_ctx())
    assert scorecard.scores["safety"] == 1.0


def test_sla_score_uses_iteration_proxy_when_no_timing():
    """When started_at is 0, SLA uses iteration count as proxy."""
    from app.agent.state import AgentState, GoalStatus
    from app.intelligence.eval_runner import EvalRunner

    runner = EvalRunner()
    state = AgentState(
        goal_id="g-sla-1",
        goal="test sla proxy",
        tenant_ctx=_ctx(),
        status=GoalStatus.COMPLETE,
        steps=[],
        verification_success=True,
        context={"sla_budget_seconds": 60.0},  # tight budget
        iterations=20,  # 20 iterations × 20s = 400s estimated → over budget
    )
    scorecard = runner.score(state=state, tenant_ctx=_ctx())
    # With 400s estimated and 60s budget, score should be < 1.0
    assert scorecard.scores["sla"] < 1.0


def test_sla_score_defaults_to_1_when_single_iteration_no_timing():
    """With 0 started_at and 1 iteration, SLA defaults to 1.0."""
    from app.agent.state import AgentState, GoalStatus
    from app.intelligence.eval_runner import EvalRunner

    runner = EvalRunner()
    state = AgentState(
        goal_id="g-sla-2",
        goal="test sla default",
        tenant_ctx=_ctx(),
        status=GoalStatus.COMPLETE,
        steps=[],
        verification_success=True,
        iterations=1,
    )
    scorecard = runner.score(state=state, tenant_ctx=_ctx())
    assert scorecard.scores["sla"] == 1.0


def test_scorecard_includes_sla_dimension():
    """All 7 dimensions present in scorecard (including tool_relevance)."""
    from app.agent.state import AgentState, GoalStatus
    from app.intelligence.eval_runner import EvalRunner

    runner = EvalRunner()
    state = AgentState(
        goal_id="g-dims-1",
        goal="check dimensions",
        tenant_ctx=_ctx(),
        status=GoalStatus.COMPLETE,
        steps=[],
        verification_success=True,
    )
    scorecard = runner.score(state=state, tenant_ctx=_ctx())
    expected = {"task_completion", "efficiency", "accuracy", "safety", "coherence", "sla", "tool_relevance"}
    assert set(scorecard.scores.keys()) == expected


# ── NEW: Task 1 — LLM-as-judge accuracy + tool_relevance 7th dimension ────────

def test_tool_relevance_neutral_no_steps():
    """No steps → tool_relevance = 0.5 (neutral)."""
    runner = EvalRunner()
    state = AgentState(
        goal_id="g-tr-0",
        goal="test tool relevance",
        tenant_ctx=_ctx(),
        status=GoalStatus.COMPLETE,
        steps=[],
        verification_success=True,
    )
    scorecard = runner.score(state=state, tenant_ctx=_ctx())
    assert scorecard.scores["tool_relevance"] == pytest.approx(0.5)


def test_tool_relevance_efficient_calls():
    """Steps with one successful tool call each → tool_relevance >= 0.7."""
    from app.agent.state import StepResult, StepStatus
    runner = EvalRunner()
    steps = [
        StepResult(description="s1", tool_calls=[{"tool": "web_search"}], status=StepStatus.COMPLETE),
        StepResult(description="s2", tool_calls=[{"tool": "file_write"}], status=StepStatus.COMPLETE),
    ]
    state = AgentState(
        goal_id="g-tr-1",
        goal="test tool efficiency",
        tenant_ctx=_ctx(),
        status=GoalStatus.COMPLETE,
        steps=steps,
        verification_success=True,
        iterations=2,
    )
    scorecard = runner.score(state=state, tenant_ctx=_ctx())
    assert scorecard.scores["tool_relevance"] >= 0.7


def test_seven_dimensions_in_scorecard():
    """All 7 dimensions including tool_relevance are present in the scorecard."""
    runner = EvalRunner()
    state = AgentState(
        goal_id="g-7d-1",
        goal="check all seven dimensions",
        tenant_ctx=_ctx(),
        status=GoalStatus.COMPLETE,
        steps=[],
        verification_success=True,
    )
    scorecard = runner.score(state=state, tenant_ctx=_ctx())
    expected = {
        "task_completion", "efficiency", "accuracy", "safety",
        "coherence", "sla", "tool_relevance",
    }
    assert set(scorecard.scores.keys()) == expected


# ── tool_relevance: concrete good vs. bad tool-choice scenarios ───────────────
# Prior coverage only checked the dimension key exists/is in-range (and one
# loose ">= 0.7" bound). These lock in the actual formula from
# EvalRunner._score_tool_relevance — a config-weighted blend of success_rate
# (are the calls failing?) and efficiency (are there too many calls per step,
# i.e. redundant/thrashing tool use?) — against the shipped defaults
# (target=2.0 calls/step, tolerance=5.0, success_weight=0.6, efficiency_weight=0.4,
# see app/core/config.py) so the score demonstrably reflects good vs. bad choices,
# not just "some number in [0, 1]".

from app.agent.state import StepResult, StepStatus


def _steps_with_calls(calls_per_step: list[list[dict]]) -> list[StepResult]:
    return [
        StepResult(description=f"s{i}", tool_calls=calls, status=StepStatus.COMPLETE)
        for i, calls in enumerate(calls_per_step)
    ]


def test_tool_relevance_perfect_score_for_targeted_successful_calls():
    """Good tool choice: one successful, on-target call per step -> perfect
    success_rate (1.0) and perfect efficiency (at, not over, the 2.0/step
    target) -> tool_relevance == 1.0."""
    runner = EvalRunner()
    steps = _steps_with_calls(
        [[{"tool": "web_search", "success": True}], [{"tool": "file_write", "success": True}]]
    )
    state = AgentState(
        goal_id="g-tr-good", goal="t", tenant_ctx=_ctx(), status=GoalStatus.COMPLETE,
        steps=steps, verification_success=True, iterations=2,
    )
    scorecard = runner.score(state=state, tenant_ctx=_ctx())
    assert scorecard.scores["tool_relevance"] == pytest.approx(1.0)


def test_tool_relevance_penalizes_high_failure_rate():
    """Bad tool choice: 3 of 4 calls fail (wrong tool picked repeatedly), but
    call volume stays on target -> success_rate dominates the score, dragging
    it well below the good-choice case."""
    runner = EvalRunner()
    steps = _steps_with_calls(
        [
            [{"tool": "wrong_tool", "status": "failed"}, {"tool": "wrong_tool", "status": "failed"}],
            [{"tool": "wrong_tool", "status": "failed"}, {"tool": "right_tool"}],
        ]
    )
    state = AgentState(
        goal_id="g-tr-bad-fail", goal="t", tenant_ctx=_ctx(), status=GoalStatus.COMPLETE,
        steps=steps, verification_success=False, iterations=2,
    )
    scorecard = runner.score(state=state, tenant_ctx=_ctx())
    # success_rate = 1 - 3/4 = 0.25; efficiency = 1.0 (2 calls/step == target)
    # tool_relevance = 0.6*0.25 + 0.4*1.0 = 0.55
    assert scorecard.scores["tool_relevance"] == pytest.approx(0.55)


def test_tool_relevance_penalizes_redundant_over_target_calls():
    """Bad tool choice: every call succeeds, but the agent flails with 7 calls
    per step against a 2.0 target -> efficiency bottoms out at 0 even though
    success_rate is perfect, showing the two sub-scores are independent."""
    runner = EvalRunner()
    per_step_calls = [{"tool": "web_search"} for _ in range(7)]
    steps = _steps_with_calls([list(per_step_calls), list(per_step_calls)])
    state = AgentState(
        goal_id="g-tr-bad-eff", goal="t", tenant_ctx=_ctx(), status=GoalStatus.COMPLETE,
        steps=steps, verification_success=True, iterations=2,
    )
    scorecard = runner.score(state=state, tenant_ctx=_ctx())
    # avg_per_step = 7.0, over_target = 5.0, tolerance = 5.0 -> efficiency = 0.0
    # tool_relevance = 0.6*1.0 + 0.4*0.0 = 0.6
    assert scorecard.scores["tool_relevance"] == pytest.approx(0.6)


def test_tool_relevance_worst_case_is_lower_than_either_single_failure_mode():
    """The worst tool choice — frequent AND failing calls — scores strictly
    lower than either failure mode alone, confirming the two penalties compose
    rather than one masking the other."""
    runner = EvalRunner()
    failing_and_redundant = [{"tool": "bad_tool", "error": "not_found"} for _ in range(6)] + [
        {"tool": "bad_tool"}
    ]
    steps = _steps_with_calls([failing_and_redundant, failing_and_redundant])
    state = AgentState(
        goal_id="g-tr-worst", goal="t", tenant_ctx=_ctx(), status=GoalStatus.FAILED,
        steps=steps, verification_success=False, iterations=2,
    )
    scorecard = runner.score(state=state, tenant_ctx=_ctx())
    worst_score = scorecard.scores["tool_relevance"]

    assert worst_score < 0.55  # worse than the pure-failure-rate case above
    assert worst_score < 0.6  # worse than the pure-inefficiency case above
    assert worst_score >= 0.0  # still clamped, never negative


def test_tool_relevance_recognizes_error_key_as_failure_without_status_field():
    """A call can signal failure via `error` alone (no `status` field) — both
    signals must be recognized, not just `status == "failed"`."""
    runner = EvalRunner()
    steps = _steps_with_calls(
        [[{"tool": "x", "error": "timeout"}, {"tool": "x"}]]
    )
    state = AgentState(
        goal_id="g-tr-errkey", goal="t", tenant_ctx=_ctx(), status=GoalStatus.COMPLETE,
        steps=steps, verification_success=True, iterations=1,
    )
    scorecard = runner.score(state=state, tenant_ctx=_ctx())
    # 1 of 2 calls failed via `error` -> success_rate = 0.5; 2 calls/step == target -> efficiency 1.0
    # tool_relevance = 0.6*0.5 + 0.4*1.0 = 0.7
    assert scorecard.scores["tool_relevance"] == pytest.approx(0.7)


def test_tool_relevance_better_choice_outscores_worse_choice():
    """End-to-end sanity check phrased the way the task frames it: a
    good-tool-choice run must score strictly higher than a bad-tool-choice run
    covering the same goal shape."""
    runner = EvalRunner()

    good_steps = _steps_with_calls([[{"tool": "search", "success": True}]] * 3)
    good_state = AgentState(
        goal_id="g-cmp-good", goal="t", tenant_ctx=_ctx(), status=GoalStatus.COMPLETE,
        steps=good_steps, verification_success=True, iterations=3,
    )

    bad_calls = [{"tool": "search", "status": "failed"} for _ in range(5)]
    bad_steps = _steps_with_calls([bad_calls] * 3)
    bad_state = AgentState(
        goal_id="g-cmp-bad", goal="t", tenant_ctx=_ctx(), status=GoalStatus.FAILED,
        steps=bad_steps, verification_success=False, iterations=3,
    )

    good_score = runner.score(state=good_state, tenant_ctx=_ctx()).scores["tool_relevance"]
    bad_score = runner.score(state=bad_state, tenant_ctx=_ctx()).scores["tool_relevance"]
    assert good_score > bad_score


async def test_llm_for_accuracy_overrides_heuristic():
    """score_async replaces heuristic accuracy=0.0 with LLM-based 0.85."""
    from unittest.mock import AsyncMock, MagicMock
    runner = EvalRunner()
    state = AgentState(
        goal_id="g-acc-llm-1",
        goal="find capital of France",
        tenant_ctx=_ctx(),
        status=GoalStatus.COMPLETE,
        steps=[],
        verification_success=False,  # heuristic would give 0.0
        verification_feedback="no partial keyword here",
    )
    provider = MagicMock()
    provider.complete = AsyncMock(return_value=MagicMock(content="0.85"))
    scorecard = await runner.score_async(state=state, tenant_ctx=_ctx(), provider=provider)
    # LLM said 0.85 — must override the heuristic 0.0
    assert scorecard.scores["accuracy"] == pytest.approx(0.85)


async def test_llm_accuracy_falls_back_on_error():
    """When the LLM call raises, accuracy falls back to the verification_success heuristic."""
    from unittest.mock import AsyncMock, MagicMock
    runner = EvalRunner()
    state = AgentState(
        goal_id="g-acc-llm-2",
        goal="fallback accuracy test",
        tenant_ctx=_ctx(),
        status=GoalStatus.COMPLETE,
        steps=[],
        verification_success=True,  # heuristic gives 1.0
    )
    provider = MagicMock()
    provider.complete = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
    scorecard = await runner.score_async(state=state, tenant_ctx=_ctx(), provider=provider)
    # Falls back to heuristic: verification_success=True → 1.0
    assert scorecard.scores["accuracy"] == pytest.approx(1.0)
