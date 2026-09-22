"""Embedding model selection must respect tenant plan and task-type config the
same way LLM role (planner/executor/verifier) selection does.

`ModelOrchestrator.select_models()` resolves a single quality tier from the
goal's complexity/risk/latency (task-type signal) and the tenant's plan-tier
cap, then reads every role — including `embedder` — off `_TIER_MODELS[tier]`.
Existing coverage (`tests/ai_router/test_plan_tier_routing.py`) only exercises
this for the free plan tier via one assertion; this file sweeps every plan
tier and every complexity/latency-driven tier, and asserts the embedder moves
in lockstep with the other roles rather than staying fixed.
"""

from __future__ import annotations

from app.agent.pattern_config import Complexity, Domain, GoalProperties, PatternConfig, RiskLevel
from app.ai_router.model_orchestrator import ModelOrchestrator
from app.tenancy.context import PlanTier

# Expected embedder per resolved quality tier (mirrors _TIER_MODELS in
# app/ai_router/model_orchestrator.py).
_EMBEDDER_BY_TIER = {
    "low": "voyage-3-lite",
    "medium": "text-embedding-3-small",
    "high": "text-embedding-3-large",
}
_PLANNER_BY_TIER = {
    "low": "gpt-4o-mini",
    "medium": "gpt-4o",
    "high": "gpt-5.2",
}


def _config(
    *,
    complexity: Complexity,
    risk: RiskLevel,
    time_sensitivity: str = "normal",
    plan_tier: str = "",
) -> PatternConfig:
    props = GoalProperties(
        complexity=complexity,
        domain=Domain.TECHNICAL,
        risk=risk,
        time_sensitivity=time_sensitivity,
    )
    # PatternConfig.model_planner/executor/verifier default to a fixed hint
    # ("gpt-5.2") that would otherwise override the tier-derived model and
    # mask whether the *tier* itself tracks task-type/plan config. Passing an
    # empty hint forces `resolve()` to fall back to the tier model, so the
    # planner is a fair apples-to-apples comparison against the embedder
    # (which never accepts a hint override).
    return PatternConfig(
        goal_properties=props,
        plan_tier=plan_tier,
        model_planner="",
        model_executor="",
        model_verifier="",
    )


class TestEmbedderTracksTaskTypeDrivenTier:
    """No plan tier set — tier is purely a function of the goal's task-type
    signal (complexity/risk/latency), exactly like the LLM roles."""

    def test_low_complexity_low_risk_selects_the_cheap_embedder(self) -> None:
        orch = ModelOrchestrator()
        cfg = _config(complexity=Complexity.SIMPLE, risk=RiskLevel.LOW)
        assignment = orch.select_models(cfg)
        assert assignment.quality_tier == "low"
        assert assignment.embedder == _EMBEDDER_BY_TIER["low"]
        assert assignment.planner == _PLANNER_BY_TIER["low"]

    def test_complex_task_selects_the_medium_embedder(self) -> None:
        orch = ModelOrchestrator()
        cfg = _config(complexity=Complexity.COMPLEX, risk=RiskLevel.LOW)
        assignment = orch.select_models(cfg)
        assert assignment.quality_tier == "medium"
        assert assignment.embedder == _EMBEDDER_BY_TIER["medium"]
        assert assignment.planner == _PLANNER_BY_TIER["medium"]

    def test_expert_task_selects_the_premium_embedder(self) -> None:
        orch = ModelOrchestrator()
        cfg = _config(complexity=Complexity.EXPERT, risk=RiskLevel.LOW)
        assignment = orch.select_models(cfg)
        assert assignment.quality_tier == "high"
        assert assignment.embedder == _EMBEDDER_BY_TIER["high"]
        assert assignment.planner == _PLANNER_BY_TIER["high"]

    def test_high_risk_forces_premium_embedder_regardless_of_complexity(self) -> None:
        orch = ModelOrchestrator()
        cfg = _config(complexity=Complexity.SIMPLE, risk=RiskLevel.CRITICAL)
        assignment = orch.select_models(cfg)
        assert assignment.quality_tier == "high"
        assert assignment.embedder == _EMBEDDER_BY_TIER["high"]

    def test_realtime_latency_forces_the_cheap_embedder_even_for_expert_complexity(self) -> None:
        # Risk stays LOW so the risk short-circuit doesn't mask the latency
        # check — this isolates "realtime" as the task-type signal driving
        # both the LLM roles and the embedder down to the low tier.
        orch = ModelOrchestrator()
        cfg = _config(
            complexity=Complexity.EXPERT, risk=RiskLevel.LOW, time_sensitivity="realtime"
        )
        assignment = orch.select_models(cfg)
        assert assignment.quality_tier == "low"
        assert assignment.embedder == _EMBEDDER_BY_TIER["low"]
        assert assignment.latency_class == "realtime"


class TestEmbedderRespectsPlanTierCapLikeLLMRoles:
    """A premium (expert/critical) goal would normally reach the 'high' tier;
    the tenant's plan tier caps it, and the embedder must move down with the
    other roles rather than staying pinned to the uncapped tier."""

    def _premium_config(self, plan_tier: str) -> PatternConfig:
        return _config(
            complexity=Complexity.EXPERT, risk=RiskLevel.CRITICAL, plan_tier=plan_tier
        )

    def test_free_plan_caps_embedder_to_the_low_tier(self) -> None:
        orch = ModelOrchestrator()
        assignment = orch.select_models(self._premium_config(PlanTier.FREE.value))
        assert assignment.quality_tier == "low"
        assert assignment.embedder == _EMBEDDER_BY_TIER["low"]
        assert assignment.planner == _PLANNER_BY_TIER["low"]

    def test_starter_plan_caps_embedder_to_the_medium_tier(self) -> None:
        orch = ModelOrchestrator()
        assignment = orch.select_models(self._premium_config(PlanTier.STARTER.value))
        assert assignment.quality_tier == "medium"
        assert assignment.embedder == _EMBEDDER_BY_TIER["medium"]
        assert assignment.planner == _PLANNER_BY_TIER["medium"]

    def test_professional_plan_reaches_the_premium_embedder(self) -> None:
        orch = ModelOrchestrator()
        assignment = orch.select_models(self._premium_config(PlanTier.PROFESSIONAL.value))
        assert assignment.quality_tier == "high"
        assert assignment.embedder == _EMBEDDER_BY_TIER["high"]

    def test_enterprise_plan_reaches_the_premium_embedder(self) -> None:
        orch = ModelOrchestrator()
        assignment = orch.select_models(self._premium_config(PlanTier.ENTERPRISE.value))
        assert assignment.quality_tier == "high"
        assert assignment.embedder == _EMBEDDER_BY_TIER["high"]

    def test_embedder_is_strictly_cheaper_for_free_than_for_enterprise(self) -> None:
        """Sanity check that the cap actually differentiates the embedder
        (not just the tier label) between the cheapest and priciest plans."""
        orch = ModelOrchestrator()
        free = orch.select_models(self._premium_config(PlanTier.FREE.value))
        enterprise = orch.select_models(self._premium_config(PlanTier.ENTERPRISE.value))
        assert free.embedder != enterprise.embedder
        assert free.embedder == "voyage-3-lite"
        assert enterprise.embedder == "text-embedding-3-large"

    def test_plan_cap_and_budget_downgrade_compose_for_the_embedder_too(self) -> None:
        """A professional tenant's cap alone would reach 'high', but a
        near-exhausted budget still downgrades the embedder along with the
        rest of the role assignment (mirrors TestPlanTierBudgetInteraction
        in test_plan_tier_routing.py, extended to the embedder)."""
        orch = ModelOrchestrator()
        cfg = self._premium_config(PlanTier.PROFESSIONAL.value)
        assignment = orch.select_models(cfg, budget_spent_ratio=0.92)
        assert assignment.quality_tier == "low"
        assert assignment.embedder == _EMBEDDER_BY_TIER["low"]
