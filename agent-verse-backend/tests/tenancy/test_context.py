"""Tests for TenantContext, PlanTier, and PlanLimits."""

import pytest

from app.tenancy.context import PLAN_LIMITS, PlanTier, TenantContext


def test_plan_tiers_are_ordered_by_capability() -> None:
    tiers = (PlanTier.FREE, PlanTier.STARTER, PlanTier.PROFESSIONAL, PlanTier.ENTERPRISE)
    limits = [PLAN_LIMITS[t] for t in tiers]
    rpms = [lim.requests_per_minute for lim in limits]
    assert rpms == sorted(rpms), "Each tier should have higher rpm than the previous"


def test_free_tier_has_conservative_limits() -> None:
    lim = PLAN_LIMITS[PlanTier.FREE]
    assert lim.requests_per_minute <= 60
    assert lim.goals_per_day <= 10
    assert lim.max_agents <= 3
    assert lim.max_api_keys <= 3


def test_enterprise_tier_has_generous_limits() -> None:
    lim = PLAN_LIMITS[PlanTier.ENTERPRISE]
    assert lim.requests_per_minute >= 1000
    assert lim.goals_per_day >= 5000
    assert lim.max_agents >= 100


def test_plan_limits_is_frozen_dataclass() -> None:
    lim = PLAN_LIMITS[PlanTier.FREE]
    with pytest.raises((AttributeError, TypeError)):
        lim.requests_per_minute = 9999  # type: ignore[misc]


def test_tenant_context_holds_fields() -> None:
    ctx = TenantContext(tenant_id="tid-1", plan=PlanTier.STARTER, api_key_id="kid-1")
    assert ctx.tenant_id == "tid-1"
    assert ctx.plan == PlanTier.STARTER
    assert ctx.api_key_id == "kid-1"


def test_tenant_context_is_frozen() -> None:
    ctx = TenantContext(tenant_id="tid-1", plan=PlanTier.FREE, api_key_id="kid-1")
    with pytest.raises((AttributeError, TypeError)):
        ctx.tenant_id = "other"  # type: ignore[misc]


def test_plan_tier_values_are_strings() -> None:
    for tier in PlanTier:
        assert isinstance(tier.value, str)
        assert tier == tier.value  # StrEnum equality
