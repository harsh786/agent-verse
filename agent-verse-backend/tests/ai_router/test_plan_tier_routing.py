"""Tests for tenant-plan-tier-aware model routing in ModelOrchestrator.

Closes the gap where `app.tenancy.context.PlanTier` existed but was never
consulted by `ModelOrchestrator.select_models` — only raw cost-budget
percentages were used. Free/starter tenants must be capped to cheaper model
tiers regardless of complexity/risk/budget headroom; professional/enterprise
tenants are uncapped but remain subject to the existing budget-based
downgrade.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.pattern_config import Complexity, Domain, GoalProperties, PatternConfig, RiskLevel
from app.ai_router.model_orchestrator import ModelOrchestrator, ModelOrchestratorAdapter
from app.tenancy.context import PlanTier


def _premium_config(plan_tier: str = "") -> PatternConfig:
    """A config whose complexity/risk would normally select the 'high' tier."""
    props = GoalProperties(
        complexity=Complexity.EXPERT,
        domain=Domain.TECHNICAL,
        risk=RiskLevel.CRITICAL,
        time_sensitivity="normal",
    )
    return PatternConfig(goal_properties=props, plan_tier=plan_tier)


class TestPlanTierCap:
    def test_free_tier_capped_to_low_regardless_of_complexity_and_risk(self) -> None:
        orch = ModelOrchestrator()
        cfg = _premium_config(plan_tier=PlanTier.FREE.value)
        assignment = orch.select_models(cfg)
        assert assignment.quality_tier == "low"
        assert assignment.plan_tier == "free"

    def test_starter_tier_capped_to_medium(self) -> None:
        orch = ModelOrchestrator()
        cfg = _premium_config(plan_tier=PlanTier.STARTER.value)
        assignment = orch.select_models(cfg)
        assert assignment.quality_tier == "medium"

    def test_professional_tier_reaches_high(self) -> None:
        orch = ModelOrchestrator()
        cfg = _premium_config(plan_tier=PlanTier.PROFESSIONAL.value)
        assignment = orch.select_models(cfg)
        assert assignment.quality_tier == "high"

    def test_enterprise_tier_reaches_high(self) -> None:
        orch = ModelOrchestrator()
        cfg = _premium_config(plan_tier=PlanTier.ENTERPRISE.value)
        assignment = orch.select_models(cfg)
        assert assignment.quality_tier == "high"

    def test_no_plan_tier_is_uncapped_backward_compatible(self) -> None:
        """Empty plan_tier (the dataclass default) must not change pre-existing
        behavior for callers that never set it."""
        orch = ModelOrchestrator()
        cfg = _premium_config(plan_tier="")
        assignment = orch.select_models(cfg)
        assert assignment.quality_tier == "high"
        assert assignment.plan_tier == ""

    def test_unknown_plan_tier_value_is_uncapped(self) -> None:
        orch = ModelOrchestrator()
        cfg = _premium_config(plan_tier="not-a-real-plan")
        assignment = orch.select_models(cfg)
        assert assignment.quality_tier == "high"

    def test_plan_tier_value_is_case_and_whitespace_insensitive(self) -> None:
        orch = ModelOrchestrator()
        cfg = _premium_config(plan_tier="  FREE  ")
        assignment = orch.select_models(cfg)
        assert assignment.quality_tier == "low"

    def test_free_tier_models_are_the_cheap_tier_models(self) -> None:
        # judge/embedder/reranker are always taken straight from the resolved
        # tier (unlike planner/executor/verifier, which accept a PatternConfig
        # hint override) — the cleanest signal that the "low" tier is in effect.
        orch = ModelOrchestrator()
        cfg = _premium_config(plan_tier=PlanTier.FREE.value)
        assignment = orch.select_models(cfg)
        assert assignment.judge == "gpt-4o-mini"
        assert assignment.embedder == "voyage-3-lite"


class TestPlanTierBudgetInteraction:
    def test_budget_downgrade_still_applies_to_professional(self) -> None:
        """Plan tier raises the ceiling but does not exempt paid tenants from
        the existing budget-based downgrade."""
        orch = ModelOrchestrator()
        cfg = _premium_config(plan_tier=PlanTier.PROFESSIONAL.value)
        assignment = orch.select_models(cfg, budget_spent_ratio=0.92)
        assert assignment.quality_tier == "low"

    def test_budget_downgrade_still_applies_to_enterprise(self) -> None:
        orch = ModelOrchestrator()
        cfg = _premium_config(plan_tier=PlanTier.ENTERPRISE.value)
        assignment = orch.select_models(cfg, budget_spent_ratio=0.78)
        assert assignment.quality_tier == "medium"

    def test_free_tier_stays_low_even_with_zero_budget_spent(self) -> None:
        """'regardless of budget %' — free tenants never get upgraded past low,
        even with a fully-unspent budget."""
        orch = ModelOrchestrator()
        cfg = _premium_config(plan_tier=PlanTier.FREE.value)
        assignment = orch.select_models(cfg, budget_spent_ratio=0.0)
        assert assignment.quality_tier == "low"

    def test_starter_cap_and_budget_downgrade_combine_to_the_lower_tier(self) -> None:
        """Starter's ceiling is 'medium', but a high budget spend still forces
        'low' — the two mechanisms compose to the more conservative result."""
        orch = ModelOrchestrator()
        cfg = _premium_config(plan_tier=PlanTier.STARTER.value)
        assignment = orch.select_models(cfg, budget_spent_ratio=0.92)
        assert assignment.quality_tier == "low"


class TestModelOrchestratorAdapterPlanWiring:
    def _fake_profile(self, tenant_plan: str) -> SimpleNamespace:
        props = GoalProperties(
            complexity=Complexity.EXPERT,
            domain=Domain.TECHNICAL,
            risk=RiskLevel.CRITICAL,
        )
        model_plan = SimpleNamespace(planner="", executor="", verifier="")
        return SimpleNamespace(properties=props, model_plan=model_plan, tenant_plan=tenant_plan)

    def test_update_from_profile_threads_tenant_plan_into_selection(self) -> None:
        # Inspect the cached assignment directly rather than model_for(), which
        # first consults the (process-global, env-seeded) configured-model
        # registry and would make this test depend on unrelated test/env state.
        adapter = ModelOrchestratorAdapter()
        adapter.update_from_profile(self._fake_profile("free"))
        assignment = adapter._cached_assignment
        assert assignment is not None
        assert assignment.quality_tier == "low"
        assert assignment.plan_tier == "free"
        assert assignment.executor == "gpt-4o-mini"

    def test_update_from_profile_enterprise_plan_reaches_premium_models(self) -> None:
        adapter = ModelOrchestratorAdapter()
        adapter.update_from_profile(self._fake_profile("enterprise"))
        assignment = adapter._cached_assignment
        assert assignment is not None
        assert assignment.quality_tier == "high"
        assert assignment.executor == "gpt-5.2"

    def test_update_from_profile_missing_tenant_plan_defaults_uncapped(self) -> None:
        """A profile-like object with no `tenant_plan` attribute at all must not
        crash and must behave like the pre-plan-tier-routing default (uncapped)."""
        props = GoalProperties(complexity=Complexity.EXPERT, risk=RiskLevel.CRITICAL)
        model_plan = SimpleNamespace(planner="", executor="", verifier="")
        bare_profile = SimpleNamespace(properties=props, model_plan=model_plan)

        adapter = ModelOrchestratorAdapter()
        adapter.update_from_profile(bare_profile)
        assignment = adapter._cached_assignment
        assert assignment is not None
        assert assignment.quality_tier == "high"
        assert assignment.plan_tier == ""
