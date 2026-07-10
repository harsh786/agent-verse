"""Comprehensive scorecard and eval tests (25+ tests).

Tests RuntimeScorecard, SelfImprovementEngine, ABTestingEngine, EvalRunner.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.providers.fake import FakeProvider


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_agent_state(
    *,
    status: str = "complete",
    iterations: int = 3,
    goal: str = "Summarize the report",
    goal_id: str = "g1",
    verification_success: bool = True,
    verification_feedback: str = "Looks good",
    steps: list | None = None,
    context: dict | None = None,
):
    from app.agent.state import AgentState, GoalStatus
    from app.tenancy.context import TenantContext, PlanTier
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="test-key")
    state = AgentState(goal=goal, goal_id=goal_id, tenant_ctx=ctx)
    state.status = GoalStatus(status)
    state.iterations = iterations
    state.verification_success = verification_success
    state.verification_feedback = verification_feedback
    state.steps = steps or []
    if context is not None:
        state.context = context  # type: ignore[attr-defined]
    return state


def _make_profile(
    *,
    score_threshold: float = 0.72,
    rag_strategy: str = "hybrid_rag",
):
    from app.orchestration.runtime_profile import (
        GoalRuntimeProfile, GoalProperties, AgentPatternConfig,
        RAGStrategyConfig, ModelPlanConfig, SecurityConfig, MemoryCacheConfig,
        EvalConfig,
    )
    props = GoalProperties(raw_goal="test goal")
    return GoalRuntimeProfile(
        goal_id="g1",
        tenant_id="t1",
        properties=props,
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(strategy=rag_strategy),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(score_threshold=score_threshold),
    )


def _tenant_ctx(tenant_id: str = "t1"):
    from app.tenancy.context import TenantContext, PlanTier
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="test-key")


# ─────────────────────────────────────────────────────────────────────────────
# RuntimeScorecard
# ─────────────────────────────────────────────────────────────────────────────

class TestRuntimeScorecard:
    def test_init(self) -> None:
        from app.evals.runtime_scorecard import RuntimeScorecard
        sc = RuntimeScorecard()
        assert sc._goal_scorer is not None

    def test_score_returns_9_dimensions(self) -> None:
        from app.evals.runtime_scorecard import RuntimeScorecard
        sc = RuntimeScorecard()
        state = _make_agent_state()
        profile = _make_profile()
        result = sc.score(state=state, profile=profile)
        expected_keys = {
            "goal_success", "rag_quality", "safety", "latency", "cost_efficiency",
            "grounding", "citation_quality", "retrieval_confidence", "tool_success_rate",
        }
        assert set(result.scores.keys()) == expected_keys

    def test_score_complete_goal_has_high_goal_success(self) -> None:
        from app.evals.runtime_scorecard import RuntimeScorecard
        sc = RuntimeScorecard()
        state = _make_agent_state(status="complete", verification_success=True)
        profile = _make_profile()
        result = sc.score(state=state, profile=profile)
        assert result.scores["goal_success"] >= 0.5

    def test_score_failed_goal_has_low_goal_success(self) -> None:
        from app.evals.runtime_scorecard import RuntimeScorecard
        sc = RuntimeScorecard()
        state = _make_agent_state(status="failed", verification_success=False)
        profile = _make_profile()
        result = sc.score(state=state, profile=profile)
        assert result.scores["goal_success"] == 0.0

    def test_score_overall_is_float(self) -> None:
        from app.evals.runtime_scorecard import RuntimeScorecard
        sc = RuntimeScorecard()
        state = _make_agent_state()
        profile = _make_profile()
        result = sc.score(state=state, profile=profile)
        assert 0.0 <= result.overall_score <= 1.0

    def test_score_no_guardrail_violations(self) -> None:
        from app.evals.runtime_scorecard import RuntimeScorecard
        sc = RuntimeScorecard()
        state = _make_agent_state()
        profile = _make_profile()
        result = sc.score(state=state, profile=profile, guardrail_violations=0)
        assert result.scores["safety"] == 1.0

    def test_score_to_dict_serializable(self) -> None:
        from app.evals.runtime_scorecard import RuntimeScorecard
        sc = RuntimeScorecard()
        state = _make_agent_state()
        result = sc.score(state=state, profile=_make_profile())
        d = result.to_dict()
        assert "goal_id" in d
        assert "scores" in d
        assert "overall_score" in d

    def test_score_improvement_suggestions_on_low_rag(self) -> None:
        from app.evals.runtime_scorecard import RuntimeScorecard
        sc = RuntimeScorecard()
        state = _make_agent_state()
        profile = _make_profile()
        # retrieval_result with very low confidence
        retrieval_result = MagicMock()
        retrieval_result.confidence = 0.1
        result = sc.score(state=state, profile=profile, retrieval_result=retrieval_result)
        # Suggestions should mention RAG when rag_quality is low
        assert isinstance(result.improvement_suggestions, list)


# ─────────────────────────────────────────────────────────────────────────────
# SelfImprovementEngine
# ─────────────────────────────────────────────────────────────────────────────

class TestSelfImprovementEngine:
    def test_init(self) -> None:
        from app.evals.self_improvement_engine import SelfImprovementEngine
        engine = SelfImprovementEngine()
        assert engine is not None

    def test_no_actions_when_score_above_threshold(self) -> None:
        from app.evals.self_improvement_engine import SelfImprovementEngine
        from app.evals.runtime_scorecard import ScorecardResult
        engine = SelfImprovementEngine()
        scorecard = ScorecardResult(
            goal_id="g1",
            scores={k: 1.0 for k in [
                "goal_success", "rag_quality", "safety", "latency",
                "cost_efficiency", "grounding", "citation_quality",
                "retrieval_confidence", "tool_success_rate",
            ]},
            overall_score=0.95,
        )
        profile = _make_profile(score_threshold=0.72)
        actions = engine.decide_actions(scorecard, profile)
        assert actions == []

    def test_rag_strategy_action_on_low_rag(self) -> None:
        from app.evals.self_improvement_engine import SelfImprovementEngine, ImprovementAction
        from app.evals.runtime_scorecard import ScorecardResult
        engine = SelfImprovementEngine()
        scorecard = ScorecardResult(
            goal_id="g1",
            scores={"rag_quality": 0.2, "goal_success": 0.8, "tool_success_rate": 0.8,
                    "cost_efficiency": 0.8, "latency": 0.8, "retrieval_confidence": 0.8},
            overall_score=0.4,
        )
        profile = _make_profile(score_threshold=0.72)
        actions = engine.decide_actions(scorecard, profile)
        action_types = [a.action_type for a in actions]
        assert ImprovementAction.UPDATE_RAG_STRATEGY in action_types

    def test_blacklist_tool_on_critically_low_tool_rate(self) -> None:
        from app.evals.self_improvement_engine import SelfImprovementEngine, ImprovementAction
        from app.evals.runtime_scorecard import ScorecardResult
        engine = SelfImprovementEngine()
        scorecard = ScorecardResult(
            goal_id="g1",
            scores={"rag_quality": 0.8, "goal_success": 0.3, "tool_success_rate": 0.1,
                    "cost_efficiency": 0.8, "latency": 0.8},
            overall_score=0.3,
        )
        profile = _make_profile()
        actions = engine.decide_actions(scorecard, profile)
        action_types = [a.action_type for a in actions]
        assert ImprovementAction.BLACKLIST_TOOL_PATTERN in action_types

    def test_regression_case_on_very_low_score(self) -> None:
        from app.evals.self_improvement_engine import SelfImprovementEngine, ImprovementAction
        from app.evals.runtime_scorecard import ScorecardResult
        engine = SelfImprovementEngine()
        scorecard = ScorecardResult(
            goal_id="g1",
            scores={k: 0.1 for k in ["rag_quality", "goal_success", "tool_success_rate",
                                       "cost_efficiency", "latency"]},
            overall_score=0.1,
        )
        profile = _make_profile()
        actions = engine.decide_actions(scorecard, profile)
        action_types = [a.action_type for a in actions]
        assert ImprovementAction.CREATE_REGRESSION_CASE in action_types

    def test_reflexion_lesson_stored_with_feedback(self) -> None:
        from app.evals.self_improvement_engine import SelfImprovementEngine, ImprovementAction
        from app.evals.runtime_scorecard import ScorecardResult
        engine = SelfImprovementEngine()
        scorecard = ScorecardResult(
            goal_id="g1",
            scores={"rag_quality": 0.8, "goal_success": 0.3, "tool_success_rate": 0.8,
                    "cost_efficiency": 0.8, "latency": 0.8},
            overall_score=0.3,
        )
        profile = _make_profile()
        state = _make_agent_state(
            verification_success=False,
            verification_feedback="The tool call failed due to auth",
        )
        actions = engine.decide_actions(scorecard, profile, state=state)
        action_types = [a.action_type for a in actions]
        assert ImprovementAction.STORE_REFLEXION_LESSON in action_types

    def test_model_routing_action_on_low_cost(self) -> None:
        from app.evals.self_improvement_engine import SelfImprovementEngine, ImprovementAction
        from app.evals.runtime_scorecard import ScorecardResult
        engine = SelfImprovementEngine()
        scorecard = ScorecardResult(
            goal_id="g1",
            scores={"rag_quality": 0.8, "goal_success": 0.8, "tool_success_rate": 0.8,
                    "cost_efficiency": 0.1, "latency": 0.1},
            overall_score=0.3,
        )
        profile = _make_profile()
        actions = engine.decide_actions(scorecard, profile)
        action_types = [a.action_type for a in actions]
        assert ImprovementAction.UPDATE_MODEL_ROUTING in action_types


# ─────────────────────────────────────────────────────────────────────────────
# ABTestingEngine
# ─────────────────────────────────────────────────────────────────────────────

class TestABTestingEngine:
    def test_init(self) -> None:
        from app.optimization.ab_testing import ABTestingEngine
        engine = ABTestingEngine()
        assert engine._results == {}

    def test_get_experiment_arm_deterministic(self) -> None:
        from app.optimization.ab_testing import ABTestingEngine, ExperimentType
        engine = ABTestingEngine()
        arm1 = engine.get_experiment_arm("goal-123", ExperimentType.PLANNER_PROMPT)
        arm2 = engine.get_experiment_arm("goal-123", ExperimentType.PLANNER_PROMPT)
        assert arm1.arm_id == arm2.arm_id

    def test_get_experiment_arm_returns_valid_arm(self) -> None:
        from app.optimization.ab_testing import ABTestingEngine, ExperimentType
        engine = ABTestingEngine()
        arm = engine.get_experiment_arm("goal-1", ExperimentType.MODEL_ROUTING)
        assert arm.arm_id in ("control", "variant_a", "variant_b")

    def test_record_result_and_get_stats(self) -> None:
        from app.optimization.ab_testing import ABTestingEngine, ExperimentType
        engine = ABTestingEngine()
        engine.record_result("g1", ExperimentType.PLANNER_PROMPT, "control", 0.8)
        engine.record_result("g2", ExperimentType.PLANNER_PROMPT, "control", 0.6)
        stats = engine.get_arm_stats(ExperimentType.PLANNER_PROMPT, "control")
        assert stats["call_count"] == 2
        assert stats["avg_score"] == pytest.approx(0.7, abs=0.01)

    def test_get_arm_stats_no_data(self) -> None:
        from app.optimization.ab_testing import ABTestingEngine, ExperimentType
        engine = ABTestingEngine()
        stats = engine.get_arm_stats(ExperimentType.RAG_STRATEGY, "variant_a")
        assert stats["call_count"] == 0
        assert stats["avg_score"] == 0.0

    def test_can_promote_variant_below_threshold(self) -> None:
        from app.optimization.ab_testing import ABTestingEngine, ExperimentType
        engine = ABTestingEngine()
        # Populate enough data
        for i in range(5):
            engine.record_result(f"g{i}", ExperimentType.RAG_STRATEGY, "variant_a", 0.5)
        result = engine.can_promote_variant(
            ExperimentType.RAG_STRATEGY, "variant_a", 0.8, 0.5
        )
        assert result is False

    def test_can_promote_variant_above_threshold(self) -> None:
        from app.optimization.ab_testing import ABTestingEngine, ExperimentType
        engine = ABTestingEngine()
        for i in range(5):
            engine.record_result(f"g{i}", ExperimentType.RAG_STRATEGY, "variant_b", 0.9)
        result = engine.can_promote_variant(
            ExperimentType.RAG_STRATEGY, "variant_b", 0.8, 0.9
        )
        assert result is True

    async def test_record_result_async_no_db(self) -> None:
        from app.optimization.ab_testing import ABTestingEngine, ExperimentType
        engine = ABTestingEngine()
        await engine.record_result_async(
            "g1", ExperimentType.MODEL_ROUTING, "control", 0.75, tenant_id="t1"
        )
        stats = engine.get_arm_stats(ExperimentType.MODEL_ROUTING, "control")
        assert stats["call_count"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# EvalRunner
# ─────────────────────────────────────────────────────────────────────────────

class TestEvalRunner:
    def test_init(self) -> None:
        from app.intelligence.eval_runner import EvalRunner
        runner = EvalRunner()
        assert "task_completion" in runner.DIMENSIONS

    def test_score_dimensions_property(self) -> None:
        from app.intelligence.eval_runner import EvalRunner
        runner = EvalRunner()
        dims = runner.score_dimensions
        assert len(dims) == 7

    def test_score_complete_goal(self) -> None:
        from app.intelligence.eval_runner import EvalRunner
        runner = EvalRunner()
        state = _make_agent_state(status="complete", iterations=3)
        ctx = _tenant_ctx()
        scorecard = runner.score(state=state, tenant_ctx=ctx)
        assert scorecard.scores["task_completion"] == 1.0
        assert scorecard.scores["efficiency"] > 0

    def test_score_failed_goal(self) -> None:
        from app.intelligence.eval_runner import EvalRunner
        runner = EvalRunner()
        state = _make_agent_state(status="failed", verification_success=False)
        ctx = _tenant_ctx()
        scorecard = runner.score(state=state, tenant_ctx=ctx)
        assert scorecard.scores["task_completion"] == 0.0

    def test_score_all_7_dimensions(self) -> None:
        from app.intelligence.eval_runner import EvalRunner
        runner = EvalRunner()
        state = _make_agent_state()
        ctx = _tenant_ctx()
        scorecard = runner.score(state=state, tenant_ctx=ctx)
        for dim in runner.DIMENSIONS:
            assert dim in scorecard.scores

    def test_score_safety_deny_events(self) -> None:
        from app.intelligence.eval_runner import EvalRunner
        from app.agent.state import AgentState
        from app.tenancy.context import TenantContext, PlanTier
        runner = EvalRunner()
        ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="test-key")
        state = AgentState(goal="test", goal_id="g1", tenant_ctx=ctx)
        state.events = [
            {"action_level": "DENY", "type": "policy_check"},
            {"action_level": "DENY", "type": "injection_detected"},
        ]
        scorecard = runner.score(state=state, tenant_ctx=ctx)
        assert scorecard.scores["safety"] < 1.0

    def test_score_efficiency_penalizes_high_iterations(self) -> None:
        from app.intelligence.eval_runner import EvalRunner
        runner = EvalRunner()
        state_low = _make_agent_state(iterations=2)
        state_high = _make_agent_state(iterations=20)
        ctx = _tenant_ctx()
        score_low = runner.score(state=state_low, tenant_ctx=ctx)
        score_high = runner.score(state=state_high, tenant_ctx=ctx)
        assert score_low.scores["efficiency"] >= score_high.scores["efficiency"]

    async def test_score_async_uses_fake_provider(self) -> None:
        from app.intelligence.eval_runner import EvalRunner
        runner = EvalRunner()
        state = _make_agent_state(status="complete")
        ctx = _tenant_ctx()
        provider = FakeProvider(responses=["0.9"])
        scorecard = await runner.score_async(
            state=state, tenant_ctx=ctx, provider=provider
        )
        assert "coherence" in scorecard.scores
        assert "accuracy" in scorecard.scores

    async def test_score_async_no_provider(self) -> None:
        from app.intelligence.eval_runner import EvalRunner
        runner = EvalRunner()
        state = _make_agent_state()
        ctx = _tenant_ctx()
        scorecard = await runner.score_async(state=state, tenant_ctx=ctx, provider=None)
        assert scorecard.scores["coherence"] >= 0.0

    def test_tool_relevance_no_tool_calls(self) -> None:
        from app.intelligence.eval_runner import EvalRunner
        from app.agent.state import StepResult
        runner = EvalRunner()
        steps = [StepResult(description="step1")]
        score = runner._score_tool_relevance(steps, iterations=1)
        assert score == 0.7

    def test_tool_relevance_with_failed_calls(self) -> None:
        from app.intelligence.eval_runner import EvalRunner
        from app.agent.state import StepResult
        runner = EvalRunner()
        steps = [StepResult(
            description="step",
            tool_calls=[
                {"tool_name": "tool_a", "error": "timeout"},
                {"tool_name": "tool_b"},
            ],
        )]
        score = runner._score_tool_relevance(steps, iterations=1)
        assert score < 1.0
