"""Tests for CostController daily reset."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.governance.cost import BudgetConfig, CostController
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="cost-reset-t1", plan=PlanTier.PROFESSIONAL, api_key_id="cr1")


def test_daily_total_resets_on_new_day():
    cost = CostController(BudgetConfig(per_goal_usd=100.0, per_tenant_daily_usd=5.0))
    # Record spending today
    cost.check_and_record(goal_id="g1", cost_usd=4.0, tenant_ctx=T)
    assert cost.daily_total(tenant_ctx=T) == pytest.approx(4.0)

    # Simulate next day by patching the date
    with patch("app.governance.cost.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz: datetime(2099, 1, 2, tzinfo=tz)
        # After "new day" — should reset
        cost._reset_if_new_day(T.tenant_id)
        assert cost.daily_total(tenant_ctx=T) == pytest.approx(0.0)


def test_goal_total_not_affected_by_daily_reset():
    cost = CostController()
    cost.check_and_record(goal_id="g1", cost_usd=2.0, tenant_ctx=T)
    cost._last_reset_date[T.tenant_id] = "1970-01-01"  # Force reset on next call
    cost._reset_if_new_day(T.tenant_id)
    # Goal total should remain (it's not per-day)
    assert cost.goal_total("g1", tenant_ctx=T) == pytest.approx(2.0)


def test_daily_total_not_reset_same_day():
    """Calling _reset_if_new_day twice on the same day must NOT zero the total."""
    cost = CostController()
    cost.check_and_record(goal_id="g2", cost_usd=3.0, tenant_ctx=T)
    cost._reset_if_new_day(T.tenant_id)  # second call, same day
    assert cost.daily_total(tenant_ctx=T) == pytest.approx(3.0)


def test_first_call_does_not_reset():
    """First call for a new tenant should not reset (no prior date stored)."""
    T3 = TenantContext(tenant_id="cost-reset-new", plan=PlanTier.FREE, api_key_id="crn")
    cost = CostController()
    cost.check_and_record(goal_id="g1", cost_usd=1.5, tenant_ctx=T3)
    assert cost.daily_total(tenant_ctx=T3) == pytest.approx(1.5)
