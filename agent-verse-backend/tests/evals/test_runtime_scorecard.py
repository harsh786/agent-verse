"""Tests for RuntimeScorecard and all eval score components — 16 tests."""
from __future__ import annotations

import pytest

from app.agent.state import AgentState, GoalStatus, StepResult
from app.evals.goal_score import GoalScorer
from app.evals.rag_score import RAGScorer
from app.evals.regression_gate import RegressionGate
from app.evals.runtime_scorecard import RuntimeScorecard, ScorecardResult
from app.evals.safety_score import SafetyScorer
from app.orchestration.runtime_profile import (
    AgentPatternConfig,
    EvalConfig,
    GoalProperties,
    GoalRuntimeProfile,
    MemoryCacheConfig,
    ModelPlanConfig,
    RAGStrategyConfig,
    SecurityConfig,
)
from app.rag.agentic.retriever_tool import RetrievalResult
from app.tenancy.context import PlanTier, TenantContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tenant() -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")


def _make_state(
    status: GoalStatus = GoalStatus.COMPLETE,
    iterations: int = 3,
    steps: list[StepResult] | None = None,
    ungrounded_claims: list[str] | None = None,
    cited_answer: str = "",
    provenance: list | None = None,
) -> AgentState:
    state = AgentState(goal="test goal", tenant_ctx=_make_tenant())
    state.status = status
    state.iterations = iterations
    state.steps = steps or []
    state.ungrounded_claims = ungrounded_claims or []
    state.cited_answer = cited_answer
    state.provenance = provenance or []
    return state


def _make_profile(cost_class: str = "medium") -> GoalRuntimeProfile:
    return GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(cost_class=cost_class),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )


def _make_retrieval(source: str, confidence: float) -> RetrievalResult:
    return RetrievalResult(
        query="q", source=source, strategy_used="auto", confidence=confidence
    )


# ---------------------------------------------------------------------------
# GoalScorer — 3 tests
# ---------------------------------------------------------------------------

def test_goal_scorer_complete_returns_one() -> None:
    scorer = GoalScorer()
    state = _make_state(status=GoalStatus.COMPLETE, iterations=3)
    assert scorer.score(state) == 1.0


def test_goal_scorer_failed_returns_zero() -> None:
    scorer = GoalScorer()
    state = _make_state(status=GoalStatus.FAILED)
    assert scorer.score(state) == 0.0


def test_goal_scorer_high_iterations_penalty() -> None:
    scorer = GoalScorer()
    state = _make_state(status=GoalStatus.COMPLETE, iterations=20)
    score = scorer.score(state)
    # excess = 15, penalty = min(0.3, 0.15) = 0.15
    assert score == pytest.approx(0.85, abs=1e-6)


# ---------------------------------------------------------------------------
# RAGScorer — 3 tests
# ---------------------------------------------------------------------------

def test_rag_scorer_high_confidence_kb() -> None:
    scorer = RAGScorer()
    result = _make_retrieval("knowledge_base", 0.9)
    score = scorer.score(result)
    assert score == pytest.approx(0.95, abs=1e-6)


def test_rag_scorer_none_is_unavailable() -> None:
    scorer = RAGScorer()
    assert scorer.score(None) is None


def test_rag_scorer_parametric_penalized() -> None:
    scorer = RAGScorer()
    result = _make_retrieval("parametric", 0.8)
    assert scorer.score(result) == 0.3


# ---------------------------------------------------------------------------
# SafetyScorer — 2 tests
# ---------------------------------------------------------------------------

def test_safety_scorer_no_violations() -> None:
    scorer = SafetyScorer()
    assert scorer.score() == 1.0


def test_safety_scorer_violations_penalized() -> None:
    scorer = SafetyScorer()
    score = scorer.score(guardrail_violations=2, hitl_bypasses=1)
    # penalty = 0.4 + 0.3 = 0.7
    assert score == pytest.approx(0.3, abs=1e-6)


# ---------------------------------------------------------------------------
# RuntimeScorecard — 4 tests
# ---------------------------------------------------------------------------

def test_scorecard_has_nine_dimensions() -> None:
    sc = RuntimeScorecard()
    state = _make_state(status=GoalStatus.COMPLETE)
    result = sc.score(state=state, profile=_make_profile())
    expected_keys = {
        "goal_success", "rag_quality", "safety", "latency", "cost_efficiency",
        "grounding", "citation_quality", "retrieval_confidence", "tool_success_rate",
    }
    assert set(result.dimension_status) == expected_keys
    assert set(result.scores).issubset(expected_keys)
    assert result.dimension_status["rag_quality"] == "not_applicable"
    assert result.dimension_status["retrieval_confidence"] == "not_applicable"
    assert result.dimension_status["cost_efficiency"] == "unavailable"
    assert result.dimension_status["latency"] == "unavailable"


def test_scorecard_omits_unavailable_dimensions_from_denominator() -> None:
    sc = RuntimeScorecard()
    state = _make_state(status=GoalStatus.COMPLETE)
    result = sc.score(state=state, profile=_make_profile())
    assert "cost_efficiency" not in result.scores
    assert "latency" not in result.scores
    assert result.coverage < 1.0
    assert result.overall_score == pytest.approx(
        sum(result.scores[name] * result.weights[name] for name in result.scores)
        / sum(result.weights[name] for name in result.scores),
        abs=1e-3,
    )


def test_scorecard_measures_supplied_cost_latency_and_retrieval() -> None:
    state = _make_state(status=GoalStatus.COMPLETE)
    state.context["grounding_checked"] = True
    state.cited_answer = "Answer [1]"
    state.provenance = [{"confidence": 0.9}]
    result = RuntimeScorecard().score(
        state=state,
        profile=_make_profile(),
        retrieval_result=_make_retrieval("knowledge_base", 0.9),
        cost_usd=0.02,
        latency_ms=2500.0,
        guardrail_violations=0,
    )
    for name in (
        "rag_quality", "retrieval_confidence", "cost_efficiency", "latency", "safety"
    ):
        assert result.dimension_status[name] == "measured"
        assert name in result.scores
    assert result.coverage == 1.0


def test_scorecard_json_serializable() -> None:
    import json
    sc = RuntimeScorecard()
    state = _make_state(status=GoalStatus.COMPLETE)
    result = sc.score(state=state, profile=_make_profile())
    d = result.to_dict()
    # Should not raise
    serialized = json.dumps(d)
    assert "goal_id" in serialized


def test_scorecard_failed_goal_low_overall() -> None:
    sc = RuntimeScorecard()
    state = _make_state(status=GoalStatus.FAILED)
    result = sc.score(
        state=state,
        profile=_make_profile(),
        guardrail_violations=2,
    )
    assert result.overall_score < 0.5


def test_scorecard_complete_no_violations_high_overall() -> None:
    sc = RuntimeScorecard()
    state = _make_state(status=GoalStatus.COMPLETE, iterations=2)
    retrieval = _make_retrieval("knowledge_base", 0.9)
    result = sc.score(
        state=state,
        profile=_make_profile(),
        retrieval_result=retrieval,
    )
    assert result.overall_score >= 0.6


# ---------------------------------------------------------------------------
# RegressionGate — 4 tests
# ---------------------------------------------------------------------------

def _make_scorecard(goal_id: str, overall: float) -> ScorecardResult:
    return ScorecardResult(
        goal_id=goal_id,
        scores={"goal_success": overall},
        overall_score=overall,
    )


def test_regression_gate_creates_candidate_for_failure() -> None:
    gate = RegressionGate(threshold=0.6)
    state = _make_state(status=GoalStatus.FAILED)
    scorecard = _make_scorecard(state.goal_id, 0.3)
    profile = _make_profile()
    candidate = gate.maybe_create_regression(state=state, scorecard=scorecard, profile=profile)
    assert candidate is not None
    assert candidate["goal_id"] == state.goal_id
    assert candidate["overall_score"] == 0.3


def test_regression_gate_skips_high_score() -> None:
    gate = RegressionGate(threshold=0.6)
    state = _make_state(status=GoalStatus.COMPLETE)
    scorecard = _make_scorecard(state.goal_id, 0.85)
    profile = _make_profile()
    result = gate.maybe_create_regression(state=state, scorecard=scorecard, profile=profile)
    assert result is None


def test_regression_gate_skips_non_terminal_status() -> None:
    gate = RegressionGate(threshold=0.6)
    state = _make_state(status=GoalStatus.EXECUTING)
    scorecard = _make_scorecard(state.goal_id, 0.1)
    profile = _make_profile()
    result = gate.maybe_create_regression(state=state, scorecard=scorecard, profile=profile)
    assert result is None


def test_regression_gate_creates_for_low_score_complete() -> None:
    gate = RegressionGate(threshold=0.6)
    state = _make_state(status=GoalStatus.COMPLETE)
    scorecard = _make_scorecard(state.goal_id, 0.4)
    profile = _make_profile()
    result = gate.maybe_create_regression(state=state, scorecard=scorecard, profile=profile)
    assert result is not None
    assert result["status"] == "complete"
