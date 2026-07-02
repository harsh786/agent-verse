"""Tests: EvalRunner exposes all 7 dimensions in score_dimensions and DIMENSIONS."""
from __future__ import annotations

import pytest

from app.intelligence.eval_runner import EvalRunner
from app.agent.state import AgentState, GoalStatus
from app.tenancy.context import TenantContext, PlanTier


ALL_7_DIMENSIONS = {
    "task_completion",
    "efficiency",
    "accuracy",
    "safety",
    "coherence",
    "sla",
    "tool_relevance",
}


def _ctx() -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")


# ── score_dimensions property ─────────────────────────────────────────────────

def test_score_dimensions_property_returns_all_7():
    runner = EvalRunner()
    dims = runner.score_dimensions
    assert set(dims) == ALL_7_DIMENSIONS


def test_score_dimensions_is_list():
    runner = EvalRunner()
    assert isinstance(runner.score_dimensions, list)
    assert len(runner.score_dimensions) == 7


def test_class_level_dimensions_constant():
    assert set(EvalRunner.DIMENSIONS) == ALL_7_DIMENSIONS


def test_score_dimensions_matches_class_constant():
    runner = EvalRunner()
    assert runner.score_dimensions == EvalRunner.DIMENSIONS


# ── score() produces all 7 keys ───────────────────────────────────────────────

def _make_state(status: GoalStatus = GoalStatus.COMPLETE) -> AgentState:
    return AgentState(
        goal_id="g1",
        goal="test goal",
        tenant_ctx=_ctx(),
        status=status,
        steps=[],
        iterations=1,  # avoid iter_efficiency > 1.0 edge case when iterations=0
        verification_success=status == GoalStatus.COMPLETE,
        events=[],
    )


def test_scorecard_has_all_7_dimension_keys():
    runner = EvalRunner()
    scorecard = runner.score(state=_make_state(), tenant_ctx=_ctx())
    assert set(scorecard.scores.keys()) == ALL_7_DIMENSIONS


def test_scorecard_all_dimensions_are_floats():
    runner = EvalRunner()
    scorecard = runner.score(state=_make_state(), tenant_ctx=_ctx())
    for dim, value in scorecard.scores.items():
        assert isinstance(value, float), f"Dimension {dim} should be float, got {type(value)}"


def test_scorecard_all_dimensions_in_range():
    runner = EvalRunner()
    scorecard = runner.score(state=_make_state(), tenant_ctx=_ctx())
    for dim, value in scorecard.scores.items():
        assert 0.0 <= value <= 1.0, f"Dimension {dim}={value} out of [0, 1] range"


def test_scorecard_failed_goal_has_low_task_completion():
    runner = EvalRunner()
    scorecard = runner.score(state=_make_state(GoalStatus.FAILED), tenant_ctx=_ctx())
    assert scorecard.scores["task_completion"] == 0.0


def test_scorecard_complete_goal_has_full_task_completion():
    runner = EvalRunner()
    scorecard = runner.score(state=_make_state(GoalStatus.COMPLETE), tenant_ctx=_ctx())
    assert scorecard.scores["task_completion"] == 1.0


# ── sla and tool_relevance specifically ──────────────────────────────────────

def test_sla_dimension_present():
    """sla was the 6th dimension; must be in scorecard."""
    runner = EvalRunner()
    scorecard = runner.score(state=_make_state(), tenant_ctx=_ctx())
    assert "sla" in scorecard.scores


def test_tool_relevance_dimension_present():
    """tool_relevance was the 7th dimension; must be in scorecard."""
    runner = EvalRunner()
    scorecard = runner.score(state=_make_state(), tenant_ctx=_ctx())
    assert "tool_relevance" in scorecard.scores
