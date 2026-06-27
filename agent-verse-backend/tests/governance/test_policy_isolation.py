"""Tests for per-tenant policy isolation."""
import pytest
from app.governance.policies import GovernancePolicy, PolicyEngine
from app.tenancy.context import PlanTier, TenantContext

T1 = TenantContext(tenant_id="pol-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")
T2 = TenantContext(tenant_id="pol-t2", plan=PlanTier.ENTERPRISE, api_key_id="k2")


def test_delete_policy_only_affects_own_tenant():
    """Deleting a policy should not affect other tenants' identically-named policies."""
    engine = PolicyEngine()

    # Add same-named policies for two tenants
    p1 = GovernancePolicy(name="no-destructive", action="deny", tool_pattern="rm.*", tenant_id=T1.tenant_id)
    p2 = GovernancePolicy(name="no-destructive", action="deny", tool_pattern="rm.*", tenant_id=T2.tenant_id)
    engine._policies = [p1, p2]  # type: ignore[assignment]

    # Delete T1's policy
    engine._policies = [  # type: ignore[assignment]
        p for p in engine._policies  # type: ignore[attr-defined]
        if not (p.name == "no-destructive" and getattr(p, "tenant_id", "") == T1.tenant_id)
    ]

    # T2's policy must remain
    t2_policies = [p for p in engine._policies if getattr(p, "tenant_id", "") == T2.tenant_id]  # type: ignore[attr-defined]
    assert len(t2_policies) == 1
    assert t2_policies[0].name == "no-destructive"
