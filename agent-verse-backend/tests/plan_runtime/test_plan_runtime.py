"""PlanVerifier + PlanRiskAnalyzer + PlanCostEstimator."""
from __future__ import annotations
import pytest
from app.plan_runtime.plan_verifier import PlanVerifier, PlanVerificationResult
from app.plan_runtime.plan_risk_analyzer import PlanRiskAnalyzer
from app.plan_runtime.plan_cost_estimator import PlanCostEstimator
from app.orchestration.runtime_profile import (
    GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
    ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig, RiskLevel,
)


def _make_profile(risk: RiskLevel = RiskLevel.LOW, hitl: bool = False) -> GoalRuntimeProfile:
    return GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test", risk=risk),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(hitl_required=hitl),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )


def test_empty_plan_is_not_feasible() -> None:
    v = PlanVerifier()
    result = v.verify(plan=[], profile=_make_profile())
    assert result.feasible is False
    assert result.safe_to_execute is False


def test_safe_plan_passes() -> None:
    v = PlanVerifier()
    plan = ["Search for recent papers on LLMs", "Summarize findings", "Write report"]
    result = v.verify(plan=plan, profile=_make_profile())
    assert result.feasible is True
    assert result.risk_level == "low"
    assert result.estimated_cost_usd > 0


def test_destructive_step_raises_critical() -> None:
    v = PlanVerifier()
    plan = ["delete all records from the users table", "confirm deletion"]
    result = v.verify(plan=plan, profile=_make_profile())
    assert result.risk_level == "critical"
    assert len(result.findings) > 0


def test_critical_requires_hitl() -> None:
    v = PlanVerifier()
    plan = ["drop the production database", "proceed with migration"]
    result = v.verify(plan=plan, profile=_make_profile())
    assert result.requires_hitl is True


def test_deploy_step_is_high_risk() -> None:
    v = PlanVerifier()
    plan = ["Build the Docker image", "deploy to staging server"]
    result = v.verify(plan=plan, profile=_make_profile())
    assert result.risk_level in ("high", "critical")


def test_long_plan_warning() -> None:
    v = PlanVerifier()
    plan = [f"step {i}" for i in range(55)]
    result = v.verify(plan=plan, profile=_make_profile())
    assert any("55 steps" in w for w in result.warnings)


def test_cost_estimator_scales_with_steps() -> None:
    est = PlanCostEstimator()
    c3 = est.estimate(["a", "b", "c"], model_cost_class="medium")
    c6 = est.estimate(["a", "b", "c", "d", "e", "f"], model_cost_class="medium")
    assert c6.estimated_cost_usd == pytest.approx(c3.estimated_cost_usd * 2)
    assert c6.estimated_tokens == c3.estimated_tokens * 2
