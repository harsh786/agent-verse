"""CostController.has_remaining_budget — the goal-submission budget pre-flight."""

from __future__ import annotations

from app.governance.cost import BudgetConfig, CostController
from app.tenancy.context import PlanTier, TenantContext


def _tenant() -> TenantContext:
    return TenantContext(tenant_id="t-budget", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def test_zero_daily_budget_has_no_remaining() -> None:
    cc = CostController(BudgetConfig(per_goal_usd=0.0, per_tenant_daily_usd=0.0))
    assert cc.has_remaining_budget(tenant_ctx=_tenant()) is False


def test_fresh_tenant_with_budget_has_remaining() -> None:
    cc = CostController(BudgetConfig(per_goal_usd=10.0, per_tenant_daily_usd=500.0))
    assert cc.has_remaining_budget(tenant_ctx=_tenant()) is True


async def test_exhausted_daily_budget_has_no_remaining() -> None:
    cc = CostController(BudgetConfig(per_goal_usd=100.0, per_tenant_daily_usd=1.0))
    t = _tenant()
    # Spend the whole daily budget.
    ok = await cc.check_and_record(goal_id="g1", cost_usd=1.0, tenant_ctx=t)
    assert ok is True
    assert cc.has_remaining_budget(tenant_ctx=t) is False
