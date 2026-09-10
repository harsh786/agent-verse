"""Tests for Phase 1b entitlements system."""
import pytest

from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.entitlements import assert_feature, assert_limit, check_limit, has_feature


def _ctx(plan: PlanTier) -> TenantContext:
    return TenantContext(tenant_id="t1", plan=plan, api_key_id="k1", roles=())


class TestHasFeature:
    def test_free_has_goals(self):
        assert has_feature(_ctx(PlanTier.FREE), "goals") is True

    def test_free_lacks_rpa(self):
        assert has_feature(_ctx(PlanTier.FREE), "rpa") is False

    def test_starter_has_marketplace(self):
        assert has_feature(_ctx(PlanTier.STARTER), "marketplace") is True

    def test_professional_has_rpa(self):
        assert has_feature(_ctx(PlanTier.PROFESSIONAL), "rpa") is True

    def test_enterprise_has_all(self):
        assert has_feature(_ctx(PlanTier.ENTERPRISE), "sso") is True
        assert has_feature(_ctx(PlanTier.ENTERPRISE), "data_residency") is True
        assert has_feature(_ctx(PlanTier.ENTERPRISE), "any_feature") is True


class TestCheckLimit:
    def test_within_limit(self):
        allowed, limit = check_limit(_ctx(PlanTier.FREE), "agents", 0)
        assert allowed is True

    def test_at_limit(self):
        from app.tenancy.context import PLAN_LIMITS
        max_agents = PLAN_LIMITS[PlanTier.FREE].max_agents
        allowed, limit = check_limit(_ctx(PlanTier.FREE), "agents", max_agents)
        assert allowed is False

    def test_enterprise_higher_limit(self):
        from app.tenancy.context import PLAN_LIMITS
        free_max = PLAN_LIMITS[PlanTier.FREE].max_agents
        allowed, limit = check_limit(_ctx(PlanTier.ENTERPRISE), "agents", free_max + 100)
        assert allowed is True


class TestAssertFeature:
    def test_passes_for_available_feature(self):
        assert_feature(_ctx(PlanTier.PROFESSIONAL), "rpa")  # no exception

    def test_raises_for_unavailable_feature(self):
        with pytest.raises(PermissionError, match="rpa"):
            assert_feature(_ctx(PlanTier.FREE), "rpa")


class TestAssertLimit:
    def test_passes_when_within_limit(self):
        assert_limit(_ctx(PlanTier.ENTERPRISE), "agents", 0)  # no exception

    def test_raises_when_at_limit(self):
        from app.tenancy.context import PLAN_LIMITS
        max_agents = PLAN_LIMITS[PlanTier.STARTER].max_agents
        with pytest.raises(PermissionError, match="agents"):
            assert_limit(_ctx(PlanTier.STARTER), "agents", max_agents)


class TestInflationTest:
    def test_plan_features_monotone(self):
        """Higher plans must have ALL features of lower plans."""
        tiers = [PlanTier.FREE, PlanTier.STARTER, PlanTier.PROFESSIONAL]
        from app.tenancy.entitlements import _PLAN_FEATURES
        for i in range(len(tiers) - 1):
            lower = _PLAN_FEATURES[tiers[i]]
            higher = _PLAN_FEATURES[tiers[i + 1]]
            missing = lower - higher
            assert not missing, \
                f"{tiers[i+1].value} is missing features from {tiers[i].value}: {missing}"
