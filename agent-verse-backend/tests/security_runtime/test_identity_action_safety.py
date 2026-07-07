"""Layer 1: identity resolution and action safety profiles."""
from __future__ import annotations
import pytest
from app.security_runtime.identity_profile import IdentityProfile, IdentityScope, IdentityResolver
from app.security_runtime.action_safety_profile import (
    ActionSafetyProfile, ActionSafetyLevel, ActionSafetyProfileSelector,
)
from app.tenancy.context import TenantContext, PlanTier


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k1", roles=("admin",))


def test_identity_profile_tenant_scope():
    profile = IdentityProfile(tenant_id="t1", identity_scope=IdentityScope.TENANT)
    assert profile.identity_scope == IdentityScope.TENANT

def test_identity_profile_agent_scope():
    profile = IdentityProfile(
        tenant_id="t1", identity_scope=IdentityScope.AGENT,
        agent_id="agent_abc", delegated_permissions=["read:goals", "write:goals"],
    )
    assert profile.agent_id == "agent_abc"
    assert "read:goals" in profile.delegated_permissions

def test_identity_profile_delegated_scope():
    profile = IdentityProfile(
        tenant_id="t1", identity_scope=IdentityScope.DELEGATED_AGENT,
        agent_id="delegated", sponsor_tenant_id="t2",
    )
    assert profile.is_delegated() is True
    assert profile.sponsor_tenant_id == "t2"

def test_identity_resolver_tenant(tenant_ctx):
    resolver = IdentityResolver()
    profile = resolver.resolve(tenant_ctx=tenant_ctx)
    assert isinstance(profile, IdentityProfile)
    assert profile.tenant_id == "t1"
    assert profile.identity_scope == IdentityScope.TENANT

def test_identity_resolver_agent(tenant_ctx):
    resolver = IdentityResolver()
    profile = resolver.resolve(tenant_ctx=tenant_ctx, agent_id="agent_xyz")
    assert profile.identity_scope == IdentityScope.AGENT
    assert profile.agent_id == "agent_xyz"

def test_identity_profile_serializable():
    import json
    profile = IdentityProfile(tenant_id="t1", identity_scope=IdentityScope.TENANT)
    json.dumps(profile.to_dict())

def test_action_safety_read_is_safe():
    selector = ActionSafetyProfileSelector()
    profile = selector.select("jira.search_issues", {"jql": "project = X"}, "low")
    assert profile.safety_level == ActionSafetyLevel.SAFE
    assert profile.requires_hitl is False

def test_action_safety_delete_requires_hitl():
    selector = ActionSafetyProfileSelector()
    profile = selector.select("postgres_query", {"query": "DELETE FROM users"}, "critical")
    assert profile.safety_level == ActionSafetyLevel.HITL_REQUIRED
    assert profile.requires_hitl is True

def test_action_safety_medium_is_log_only():
    selector = ActionSafetyProfileSelector()
    profile = selector.select("jira.update_issue", {"summary": "Updated"}, "medium")
    assert profile.safety_level == ActionSafetyLevel.LOG_ONLY
    assert profile.audit_required is True

def test_action_safety_profile_has_rollback_registered():
    selector = ActionSafetyProfileSelector()
    profile = selector.select(
        tool_name="create_jira_issue",
        tool_args={"summary": "Test issue"},
        risk_level="medium",
    )
    assert isinstance(profile, ActionSafetyProfile)
    assert profile.rollback_registered is not None


def test_action_safety_serializable():
    import json
    selector = ActionSafetyProfileSelector()
    profile = selector.select("any_tool", {}, "low")
    json.dumps(profile.to_dict())
