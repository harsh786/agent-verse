"""All 9 scorecard dimensions + self-improvement loop actions."""
from __future__ import annotations
import pytest
from app.evals.agent_score import AgentScorer
from app.evals.runtime_scorecard import RuntimeScorecard, ScorecardResult
from app.evals.self_improvement_engine import SelfImprovementEngine, ImprovementAction
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.orchestration.runtime_profile import (
    GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
    ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig,
)
from app.tenancy.context import TenantContext, PlanTier


def _make_state(status=GoalStatus.COMPLETE, iterations=3):
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    s = AgentState(goal="test", tenant_ctx=ctx, goal_id="g1")
    s.status = status; s.iterations = iterations; return s


def _make_profile():
    return GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(), security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )


def test_agent_scorer_tool_success_rate():
    scorer = AgentScorer()
    state = _make_state()
    step = StepResult(description="search", output="found", status=StepStatus.COMPLETE)
    step.tool_calls = [{"tool_name": "jira.search", "success": True}]
    state.steps = [step]
    score = scorer.score_tool_success_rate(state)
    assert 0.0 <= score <= 1.0
    assert score > 0.5


def test_agent_scorer_penalizes_failed_tools():
    scorer = AgentScorer()
    state = _make_state(GoalStatus.FAILED)
    step = StepResult(description="search", output="", status=StepStatus.FAILED)
    step.tool_calls = [{"tool_name": "web_search", "success": False}]
    state.steps = [step]
    score = scorer.score_tool_success_rate(state)
    assert score < 0.5


def test_agent_scorer_grounding_no_claims():
    scorer = AgentScorer()
    state = _make_state(); state.ungrounded_claims = []
    assert scorer.score_grounding(state) == 1.0


def test_agent_scorer_penalizes_hallucinations():
    scorer = AgentScorer()
    state = _make_state(); state.ungrounded_claims = ["claim 1", "claim 2"]
    score = scorer.score_grounding(state)
    assert score < 1.0


def test_scorecard_has_all_9_dimensions():
    scorecard = RuntimeScorecard()
    state = _make_state()
    result = scorecard.score(state=state, profile=_make_profile())
    required = {"goal_success", "rag_quality", "safety", "latency", "cost_efficiency",
                "grounding", "citation_quality", "retrieval_confidence", "tool_success_rate"}
    missing = required - set(result.scores.keys())
    assert not missing, f"Missing scorecard dimensions: {sorted(missing)}"


def test_scorecard_result_serializable():
    import json
    scorecard = RuntimeScorecard()
    state = _make_state()
    result = scorecard.score(state=state, profile=_make_profile())
    json.dumps(result.to_dict())


def test_scorecard_failed_goal_low_overall():
    scorecard = RuntimeScorecard()
    state = _make_state(GoalStatus.FAILED)
    # Add failed tool calls so tool_success_rate is also low
    step = StepResult(description="search", output="", status=StepStatus.FAILED)
    step.tool_calls = [
        {"tool_name": "web_search", "success": False},
        {"tool_name": "jira.search", "success": False},
    ]
    state.steps = [step]
    state.ungrounded_claims = ["claim 1", "claim 2", "claim 3"]
    result = scorecard.score(state=state, profile=_make_profile())
    assert result.overall_score < 0.6


def test_self_improvement_rag_change_on_low_rag():
    engine = SelfImprovementEngine()
    result = ScorecardResult(
        goal_id="g1", overall_score=0.4,
        scores={"goal_success":1.0,"rag_quality":0.2,"safety":1.0,"latency":0.8,
                "cost_efficiency":0.8,"grounding":0.9,"citation_quality":0.7,
                "retrieval_confidence":0.3,"tool_success_rate":1.0},
    )
    actions = engine.decide_actions(result, _make_profile())
    action_types = [a.action_type for a in actions]
    assert ImprovementAction.UPDATE_RAG_STRATEGY in action_types


def test_self_improvement_no_action_on_high_score():
    engine = SelfImprovementEngine()
    result = ScorecardResult(
        goal_id="g1", overall_score=0.94,
        scores={"goal_success":1.0,"rag_quality":0.9,"safety":1.0,"latency":0.9,
                "cost_efficiency":0.9,"grounding":0.95,"citation_quality":0.9,
                "retrieval_confidence":0.88,"tool_success_rate":1.0},
    )
    actions = engine.decide_actions(result, _make_profile())
    assert len(actions) == 0


def test_self_improvement_stores_reflexion_on_failure():
    engine = SelfImprovementEngine()
    state = _make_state(GoalStatus.FAILED)
    state.verification_feedback = "permission denied for table users"
    result = ScorecardResult(
        goal_id="g1", overall_score=0.3,
        scores={"goal_success":0.0,"rag_quality":0.5,"safety":1.0,"latency":0.8,
                "cost_efficiency":0.9,"grounding":0.7,"citation_quality":0.6,
                "retrieval_confidence":0.6,"tool_success_rate":0.3},
    )
    actions = engine.decide_actions(result, _make_profile(), state=state)
    action_types = [a.action_type for a in actions]
    assert ImprovementAction.STORE_REFLEXION_LESSON in action_types
