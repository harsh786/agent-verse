"""Tests for all 7 optimization modules — 15+ tests."""
from __future__ import annotations

import pytest
from app.optimization.token_optimizer import TokenOptimizer
from app.optimization.cost_optimizer import CostOptimizer
from app.optimization.model_optimizer import ModelOptimizer, ModelOptimizationDecision
from app.optimization.latency_optimizer import LatencyOptimizer
from app.optimization.prompt_optimizer import PromptOptimizer
from app.optimization.cache_optimizer import CacheOptimizer
from app.optimization.ab_testing import ABTestingEngine, ExperimentType, ExperimentArm
from app.orchestration.runtime_profile import (
    GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
    ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig,
    Complexity, RiskLevel,
)


# ---------------------------------------------------------------------------
# TokenOptimizer — 2 tests
# ---------------------------------------------------------------------------

def test_token_optimizer_passthrough_short_text() -> None:
    opt = TokenOptimizer(max_tokens=10)
    text = "hello"
    assert opt.compress(text) == text


def test_token_optimizer_truncates_long_text() -> None:
    opt = TokenOptimizer(max_tokens=10)  # 40 chars max
    text = "a" * 100
    result = opt.compress(text)
    assert len(result) < len(text)
    assert "truncated" in result


# ---------------------------------------------------------------------------
# CostOptimizer — 2 tests
# ---------------------------------------------------------------------------

def test_cost_optimizer_savings_high_to_low() -> None:
    opt = CostOptimizer()
    # high=0.015/1k, low=0.0003/1k, 10k tokens
    savings = opt.estimate_savings("high", "low", 10_000)
    assert savings == pytest.approx((0.015 - 0.0003) * 10, abs=1e-6)


def test_cost_optimizer_no_savings_upgrade() -> None:
    opt = CostOptimizer()
    savings = opt.estimate_savings("low", "high", 10_000)
    assert savings == 0.0


# ---------------------------------------------------------------------------
# ModelOptimizer — 3 tests
# ---------------------------------------------------------------------------

def _make_profile_with_props(
    complexity: Complexity, risk: RiskLevel
) -> GoalRuntimeProfile:
    props = GoalProperties(raw_goal="test", complexity=complexity, risk=risk)
    return GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=props,
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )


def test_model_optimizer_simple_low_risk_downgrades() -> None:
    opt = ModelOptimizer()
    profile = _make_profile_with_props(Complexity.SIMPLE, RiskLevel.LOW)
    decision = opt.optimize(profile)
    assert decision.recommended_cost_class == "low"
    assert decision.downgrade_safe is True


def test_model_optimizer_high_risk_requires_high_model() -> None:
    opt = ModelOptimizer()
    profile = _make_profile_with_props(Complexity.MEDIUM, RiskLevel.HIGH)
    decision = opt.optimize(profile)
    assert decision.recommended_cost_class == "high"
    assert decision.downgrade_safe is False


def test_model_optimizer_no_properties_returns_medium() -> None:
    opt = ModelOptimizer()

    class EmptyProfile:
        properties = None

    decision = opt.optimize(EmptyProfile())
    assert decision.recommended_cost_class == "medium"


# ---------------------------------------------------------------------------
# LatencyOptimizer — 2 tests
# ---------------------------------------------------------------------------

def test_latency_optimizer_realtime_selects_low_tier() -> None:
    opt = LatencyOptimizer()
    config = opt.optimize_for_latency("high", "realtime", 200.0)
    assert config.recommended_tier == "low"


def test_latency_optimizer_acceptable_keeps_tier() -> None:
    opt = LatencyOptimizer()
    config = opt.optimize_for_latency("medium", "normal", 150.0)
    assert config.recommended_tier == "medium"


# ---------------------------------------------------------------------------
# PromptOptimizer — 2 tests
# ---------------------------------------------------------------------------

def test_prompt_optimizer_short_passthrough() -> None:
    opt = PromptOptimizer(max_tokens=100)
    prompt = "Short prompt"
    assert opt.optimize(prompt) == prompt


def test_prompt_optimizer_truncates_long_prompt() -> None:
    opt = PromptOptimizer(max_tokens=10)  # 40 chars
    prompt = "x" * 200
    result = opt.optimize(prompt)
    assert "truncated" in result
    assert len(result) < 200


# ---------------------------------------------------------------------------
# CacheOptimizer — 2 tests
# ---------------------------------------------------------------------------

def test_cache_optimizer_caches_stable_query() -> None:
    opt = CacheOptimizer()
    result = opt.should_cache("What is Python?", 5, 300.0, False, False)
    assert result.should_cache is True
    assert "eligible" in result.reason


def test_cache_optimizer_no_cache_for_realtime() -> None:
    opt = CacheOptimizer()
    result = opt.should_cache("live stock price", 10, 50.0, True, False)
    assert result.should_cache is False


# ---------------------------------------------------------------------------
# ABTestingEngine — 2 tests
# ---------------------------------------------------------------------------

def test_ab_testing_engine_assigns_arm() -> None:
    engine = ABTestingEngine()
    arm = engine.get_experiment_arm("goal_abc", ExperimentType.PLANNER_PROMPT)
    assert arm.arm_id in ("control", "variant_a", "variant_b")
    assert "arm" in arm.config


def test_ab_testing_engine_stats_after_records() -> None:
    engine = ABTestingEngine()
    exp = ExperimentType.MODEL_ROUTING
    engine.record_result("g1", exp, "variant_a", 0.9)
    engine.record_result("g2", exp, "variant_a", 0.7)
    engine.record_result("g3", exp, "control", 0.5)
    stats = engine.get_arm_stats(exp, "variant_a")
    assert stats["call_count"] == 2
    assert stats["avg_score"] == pytest.approx(0.8, abs=1e-3)
    control_stats = engine.get_arm_stats(exp, "control")
    assert control_stats["call_count"] == 1
