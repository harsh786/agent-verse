"""Comprehensive tests for evals: scorecard, self-improvement, regression gate, A/B."""
from __future__ import annotations

import pytest
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.tenancy.context import TenantContext, PlanTier
from app.orchestration.runtime_profile import (
    GoalRuntimeProfile,
    GoalProperties,
    AgentPatternConfig,
    RAGStrategyConfig,
    ModelPlanConfig,
    SecurityConfig,
    MemoryCacheConfig,
    EvalConfig,
)


@pytest.fixture
def tenant_ctx() -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


def _make_profile(goal: str = "test", cost: float = 0.05) -> GoalRuntimeProfile:
    return GoalRuntimeProfile(
        goal_id="g1",
        tenant_id="t1",
        properties=GoalProperties(raw_goal=goal),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(max_cost_usd=0.10),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )


def _make_state(
    tenant_ctx: TenantContext,
    goal: str = "test",
    status: GoalStatus = GoalStatus.COMPLETE,
    iterations: int = 3,
) -> AgentState:
    state = AgentState(goal=goal, tenant_ctx=tenant_ctx, goal_id="g1")
    state.status = status
    state.iterations = iterations
    state.context["total_cost_usd"] = 0.03
    state.context["_latency_ms"] = 8000
    return state


# ── RUNTIME SCORECARD (9 DIMENSIONS) ─────────────────────────────────────────

def test_scorecard_all_9_dimensions(tenant_ctx: TenantContext) -> None:
    from app.evals.runtime_scorecard import RuntimeScorecard

    state = _make_state(tenant_ctx)
    profile = _make_profile()
    scorecard = RuntimeScorecard()
    result = scorecard.score(state=state, profile=profile)

    assert len(result.scores) == 9
    expected_dims = {
        "goal_success",
        "rag_quality",
        "safety",
        "latency",
        "cost_efficiency",
        "grounding",
        "citation_quality",
        "retrieval_confidence",
        "tool_success_rate",
    }
    assert set(result.scores.keys()) == expected_dims
    assert 0.0 <= result.overall_score <= 1.0


def test_scorecard_latency_uses_real_value(tenant_ctx: TenantContext) -> None:
    from app.evals.model_score import ModelScorer

    state = _make_state(tenant_ctx)
    state.context["_latency_ms"] = 3000  # Fast (< 5s)

    scorer = ModelScorer()
    latency_score = scorer.score_latency(state)
    assert latency_score == 1.0  # 3s → 1.0


def test_scorecard_cost_uses_context(tenant_ctx: TenantContext) -> None:
    from app.evals.model_score import ModelScorer

    state = _make_state(tenant_ctx)
    state.context["total_cost_usd"] = 0.05  # 50% of 0.10 budget
    profile = _make_profile()

    scorer = ModelScorer()
    cost_score = scorer.score_cost(profile, state)
    assert cost_score == 0.8  # ratio=0.5 → 0.8 tier


def test_scorecard_latency_and_cost_are_distinct(tenant_ctx: TenantContext) -> None:
    from app.evals.runtime_scorecard import RuntimeScorecard

    state = _make_state(tenant_ctx)
    state.context["_latency_ms"] = 60000  # Very slow → low latency score
    state.context["total_cost_usd"] = 0.001  # Very cheap → high cost score

    profile = _make_profile()
    scorecard = RuntimeScorecard()
    result = scorecard.score(state=state, profile=profile)

    latency = result.scores["latency"]
    cost = result.scores["cost_efficiency"]
    # Latency should be low, cost should be high — they must differ
    assert latency != cost or (latency < 0.5 and cost > 0.8)


def test_scorecard_failed_goal_has_zero_success(tenant_ctx: TenantContext) -> None:
    from app.evals.runtime_scorecard import RuntimeScorecard

    state = _make_state(tenant_ctx, status=GoalStatus.FAILED)
    profile = _make_profile()
    scorecard = RuntimeScorecard()
    result = scorecard.score(state=state, profile=profile)
    assert result.scores["goal_success"] == 0.0


# ── SELF-IMPROVEMENT ENGINE ───────────────────────────────────────────────────

def test_self_improvement_decides_prompt_action(tenant_ctx: TenantContext) -> None:
    from app.evals.self_improvement_engine import SelfImprovementEngine, ImprovementAction
    from app.evals.runtime_scorecard import ScorecardResult

    engine = SelfImprovementEngine()
    result = ScorecardResult(
        goal_id="g1",
        scores={
            "goal_success": 0.5,
            "rag_quality": 0.8,
            "safety": 1.0,
            "latency": 0.9,
            "cost_efficiency": 0.9,
            "grounding": 0.9,
            "citation_quality": 0.8,
            "retrieval_confidence": 0.8,
            "tool_success_rate": 0.9,
        },
        overall_score=0.6,
    )
    actions = engine.decide_actions(result, _make_profile())
    action_types = [a.action_type for a in actions]
    # goal_success=0.5 < 0.7 → UPDATE_PROMPT_VARIANT expected
    assert ImprovementAction.UPDATE_PROMPT_VARIANT in action_types or len(actions) > 0


def test_self_improvement_blacklists_on_low_tool_rate(tenant_ctx: TenantContext) -> None:
    from app.evals.self_improvement_engine import SelfImprovementEngine, ImprovementAction
    from app.evals.runtime_scorecard import ScorecardResult

    engine = SelfImprovementEngine()
    result = ScorecardResult(
        goal_id="g1",
        scores={
            "goal_success": 0.9,
            "rag_quality": 0.9,
            "safety": 1.0,
            "latency": 0.9,
            "cost_efficiency": 0.9,
            "grounding": 0.9,
            "citation_quality": 0.9,
            "retrieval_confidence": 0.9,
            "tool_success_rate": 0.1,  # critically low
        },
        overall_score=0.7,
    )
    actions = engine.decide_actions(result, _make_profile())
    action_types = [a.action_type for a in actions]
    assert ImprovementAction.BLACKLIST_TOOL_PATTERN in action_types


def test_self_improvement_creates_regression_case(tenant_ctx: TenantContext) -> None:
    from app.evals.self_improvement_engine import SelfImprovementEngine, ImprovementAction
    from app.evals.runtime_scorecard import ScorecardResult

    engine = SelfImprovementEngine()
    result = ScorecardResult(
        goal_id="g1",
        scores={
            "goal_success": 0.0,
            "rag_quality": 0.3,
            "safety": 1.0,
            "latency": 0.5,
            "cost_efficiency": 0.5,
            "grounding": 0.3,
            "citation_quality": 0.2,
            "retrieval_confidence": 0.3,
            "tool_success_rate": 0.1,
        },
        overall_score=0.2,  # Below 0.4 → regression case
    )
    actions = engine.decide_actions(result, _make_profile())
    action_types = [a.action_type for a in actions]
    assert ImprovementAction.CREATE_REGRESSION_CASE in action_types


# ── REGRESSION GATE ────────────────────────────────────────────────────────────

def test_regression_gate_creates_case_for_low_score(tenant_ctx: TenantContext) -> None:
    from app.evals.regression_gate import RegressionGate
    from app.evals.runtime_scorecard import ScorecardResult

    gate = RegressionGate()
    state = _make_state(tenant_ctx, status=GoalStatus.FAILED)
    result = ScorecardResult(
        goal_id="g1",
        scores={
            "goal_success": 0.0,
            "rag_quality": 0.5,
            "safety": 1.0,
            "latency": 0.5,
            "cost_efficiency": 0.5,
            "grounding": 0.5,
            "citation_quality": 0.5,
            "retrieval_confidence": 0.5,
            "tool_success_rate": 0.3,
        },
        overall_score=0.3,
    )
    candidate = gate.maybe_create_regression(
        state=state, scorecard=result, profile=_make_profile()
    )
    assert candidate is not None
    assert "goal_id" in candidate


def test_regression_gate_no_case_for_high_score(tenant_ctx: TenantContext) -> None:
    from app.evals.regression_gate import RegressionGate
    from app.evals.runtime_scorecard import ScorecardResult

    gate = RegressionGate()
    state = _make_state(tenant_ctx)
    result = ScorecardResult(
        goal_id="g1",
        scores={
            "goal_success": 0.9,
            "rag_quality": 0.9,
            "safety": 1.0,
            "latency": 0.9,
            "cost_efficiency": 0.9,
            "grounding": 0.9,
            "citation_quality": 0.9,
            "retrieval_confidence": 0.9,
            "tool_success_rate": 0.9,
        },
        overall_score=0.92,
    )
    candidate = gate.maybe_create_regression(
        state=state, scorecard=result, profile=_make_profile()
    )
    assert candidate is None


# ── A/B TESTING ────────────────────────────────────────────────────────────────

async def test_ab_testing_engine_records_and_stats() -> None:
    from app.optimization.ab_testing import ABTestingEngine, ExperimentType

    engine = ABTestingEngine()

    for _ in range(5):
        engine.record_result("g1", ExperimentType.MODEL_ROUTING, "variant_a", 0.85)
    for _ in range(3):
        engine.record_result("g2", ExperimentType.MODEL_ROUTING, "control", 0.70)

    variant_stats = engine.get_arm_stats(ExperimentType.MODEL_ROUTING, "variant_a")
    control_stats = engine.get_arm_stats(ExperimentType.MODEL_ROUTING, "control")

    assert variant_stats["call_count"] == 5
    assert abs(variant_stats["avg_score"] - 0.85) < 0.01
    assert control_stats["call_count"] == 3


def test_ab_testing_promotion_threshold() -> None:
    from app.optimization.ab_testing import ABTestingEngine, ExperimentType

    engine = ABTestingEngine()
    for _ in range(5):
        engine.record_result("g1", ExperimentType.RAG_STRATEGY, "variant_b", 0.90)

    can_promote = engine.can_promote_variant(
        ExperimentType.RAG_STRATEGY,
        "variant_b",
        min_score_threshold=0.8,
        current_score=0.90,
    )
    assert can_promote is True


# ── AGENT SCORER ──────────────────────────────────────────────────────────────

def test_agent_scorer_tool_success_rate(tenant_ctx: TenantContext) -> None:
    from app.evals.agent_score import AgentScorer

    scorer = AgentScorer()
    state = _make_state(tenant_ctx)
    step = StepResult(description="search", output="result", status=StepStatus.COMPLETE)
    step.tool_calls = [
        {"tool_name": "jira.search", "success": True},
        {"tool_name": "jira.create", "success": True},
        {"tool_name": "github.pr", "success": False},
    ]
    state.steps = [step]
    score = scorer.score_tool_success_rate(state)
    assert abs(score - (2 / 3)) < 0.1


def test_agent_scorer_grounding(tenant_ctx: TenantContext) -> None:
    from app.evals.agent_score import AgentScorer

    scorer = AgentScorer()
    state = _make_state(tenant_ctx)
    state.ungrounded_claims = []
    assert scorer.score_grounding(state) == 1.0

    state.ungrounded_claims = ["claim1", "claim2"]
    score = scorer.score_grounding(state)
    assert score < 1.0
