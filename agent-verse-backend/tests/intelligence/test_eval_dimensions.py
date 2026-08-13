"""Tests: EvalRunner exposes all 7 dimensions in score_dimensions and DIMENSIONS."""
from __future__ import annotations

from app.agent.state import AgentState, GoalStatus
from app.intelligence.eval_runner import EvalRunner
from app.orchestration.runtime_profile import (
    AgentPatternConfig,
    EvalConfig,
    GoalProperties,
    GoalRuntimeProfile,
    MemoryCacheConfig,
    ModelPlanConfig,
    RAGStrategyConfig,
    SecurityConfig,
    StrategySelection,
)
from app.tenancy.context import PlanTier, TenantContext

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


def test_scorecard_contains_versioned_strategy_provenance():
    state = _make_state()
    profile = GoalRuntimeProfile(
        goal_id="g1",
        tenant_id="t1",
        properties=GoalProperties(raw_goal="test goal"),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
        primary_strategy=StrategySelection("react", "1.0.0"),
        auxiliary_strategies=(StrategySelection("reflection", "1.0.0"),),
    )
    state.context.update({
        "_runtime_profile": profile,
        "strategy_execution_id": "execution-1",
        "correlation_id": "correlation-1",
        "causation_id": "causation-1",
    })

    scorecard = EvalRunner().score(state=state, tenant_ctx=_ctx())

    assert scorecard.primary_strategy_id == "react"
    assert scorecard.primary_strategy_version == "1.0.0"
    assert scorecard.auxiliary_strategy_versions == {"reflection": "1.0.0"}
    assert scorecard.profile_id == profile.profile_id
    assert scorecard.profile_version == 2
    assert scorecard.strategy_execution_id == "execution-1"
    assert scorecard.evaluator_version == EvalRunner.EVALUATOR_VERSION
    assert scorecard.correlation_id == "correlation-1"
    assert scorecard.causation_id == "causation-1"
    assert set(scorecard.evidence_completeness) == ALL_7_DIMENSIONS
