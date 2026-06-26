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
