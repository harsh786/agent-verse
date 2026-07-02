"""Tests for EvalRunner safety score from real events."""
from __future__ import annotations

import pytest

from app.intelligence.eval_runner import EvalRunner
from app.agent.state import AgentState, GoalStatus
from app.tenancy.context import TenantContext, PlanTier


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
    from app.intelligence.eval_runner import EvalRunner
    from app.agent.state import AgentState, GoalStatus

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
    from app.intelligence.eval_runner import EvalRunner
    from app.agent.state import AgentState, GoalStatus

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
    """All 6 dimensions present in scorecard."""
    from app.intelligence.eval_runner import EvalRunner
    from app.agent.state import AgentState, GoalStatus

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
    expected = {"task_completion", "efficiency", "accuracy", "safety", "coherence", "sla"}
    assert set(scorecard.scores.keys()) == expected
