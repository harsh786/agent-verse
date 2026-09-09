"""Security: cross-tenant isolation, API key scoping, identity profile, SSRF guard."""
from __future__ import annotations

import pytest

from app.tenancy.context import PlanTier, TenantContext


def test_orchestration_profile_tenant_scoped():
    import asyncio

    from app.orchestration.runtime_profile_builder import RuntimeProfileBuilder
    from app.orchestration.strategy_registry import build_default_registry

    async def _run():
        builder = RuntimeProfileBuilder(registry=build_default_registry())
        p1 = await builder.build("list tickets", tenant_id="alpha_corp", goal_id="g1")
        p2 = await builder.build("list tickets", tenant_id="beta_corp", goal_id="g2")
        assert p1.tenant_id == "alpha_corp"
        assert p2.tenant_id == "beta_corp"
        assert p1.profile_id != p2.profile_id
    asyncio.run(_run())


def test_api_key_scoped_to_tenant():
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="key_for_t1")
    assert ctx.api_key_id == "key_for_t1"
    ctx2 = TenantContext(tenant_id="t2", plan=PlanTier.STARTER, api_key_id="key_for_t2")
    assert ctx2.api_key_id != ctx.api_key_id


def test_identity_profile_all_scopes():
    from app.security_runtime.identity_profile import (
        IdentityResolver,
        IdentityScope,
    )
    resolver = IdentityResolver()
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")
    # Tenant scope
    p = resolver.resolve(tenant_ctx=ctx)
    assert p.identity_scope == IdentityScope.TENANT
    # Agent scope
    p2 = resolver.resolve(tenant_ctx=ctx, agent_id="agent_xyz")
    assert p2.identity_scope == IdentityScope.AGENT
    assert p2.agent_id == "agent_xyz"
    # Delegated scope
    p3 = resolver.resolve(tenant_ctx=ctx, agent_id="delegated", sponsor_tenant_id="t2")
    assert p3.identity_scope == IdentityScope.DELEGATED_AGENT


def test_action_safety_profile_critical_requires_hitl():
    from app.security_runtime.action_safety_profile import (
        ActionSafetyLevel,
        ActionSafetyProfileSelector,
    )
    selector = ActionSafetyProfileSelector()
    profile = selector.select("postgres_query", {"query": "DELETE FROM users"}, "critical")
    assert profile.safety_level == ActionSafetyLevel.HITL_REQUIRED
    assert profile.requires_hitl is True


def test_action_safety_profile_read_is_safe():
    from app.security_runtime.action_safety_profile import (
        ActionSafetyLevel,
        ActionSafetyProfileSelector,
    )
    selector = ActionSafetyProfileSelector()
    profile = selector.select("jira.search_issues", {"jql": "project = X"}, "low")
    assert profile.safety_level == ActionSafetyLevel.SAFE
    assert profile.requires_hitl is False


def test_data_classifier_blocks_secret_from_prompt():
    from app.data_classification.classifier import DataClassifier
    from app.data_classification.schema import DataClass
    clf = DataClassifier()
    result = clf.classify("API key: sk-proj-abc123DEFxyz456")
    assert DataClass.SECRET in result.classes
    assert result.safe_for_prompt is False


def test_ssrf_guard_available():
    try:
        from app.net.ssrf_guard import is_ssrf_blocked
        assert is_ssrf_blocked("http://169.254.169.254/latest/meta-data") is True
        assert is_ssrf_blocked("http://127.0.0.1/secret") is True
        assert is_ssrf_blocked("https://api.github.com/repos") is False
    except ImportError:
        pytest.skip("app/net/ssrf_guard.py not yet available")
