"""Governance: audit v3, HITL, compliance bundles, RBAC, cost hard stop."""
from __future__ import annotations

from app.lifecycle.export_policy import ExportPolicy
from app.lifecycle.retention_policy import DataCategory, RetentionPolicy, RetentionTier
from app.tenancy.context import PlanTier, TenantContext


def test_audit_v3_records_tool_call():
    from app.governance.audit_v3 import AuditV3
    audit = AuditV3()
    record = audit.record(
        tenant_id="t1", goal_id="g1", action="tool_call",
        tool_name="postgres_query", tool_args={"query": "DELETE FROM users WHERE id=1"},
        actor="agent:g1", risk_level="high",
    )
    assert record is not None
    assert record.tenant_id == "t1"
    assert record.action == "tool_call"
    assert record.id is not None


def test_audit_v3_creates_hash_chain():
    from app.governance.audit_v3 import AuditV3
    audit = AuditV3()
    r1 = audit.record(tenant_id="t1", goal_id="g1", action="step_1",
                      tool_name="", tool_args={}, actor="agent")
    r2 = audit.record(tenant_id="t1", goal_id="g1", action="step_2",
                      tool_name="", tool_args={}, actor="agent")
    assert r1.id != r2.id
    assert audit.verify_chain() is True


def test_compliance_bundle_gdpr():
    from app.governance.compliance_bundles import COMPLIANCE_BUNDLES
    assert "gdpr" in COMPLIANCE_BUNDLES or "GDPR" in COMPLIANCE_BUNDLES


def test_compliance_bundle_soc2():
    from app.governance.compliance_bundles import COMPLIANCE_BUNDLES
    has_soc2 = "soc2" in COMPLIANCE_BUNDLES or "SOC2" in COMPLIANCE_BUNDLES
    assert has_soc2


def test_governance_selector_regulated_for_gdpr():
    from app.orchestration.runtime_profile import (
        AgentPatternConfig,
        EvalConfig,
        GoalProperties,
        GoalRuntimeProfile,
        MemoryCacheConfig,
        ModelPlanConfig,
        RAGStrategyConfig,
        SecurityConfig,
    )
    from app.security_runtime.governance_profile import GovernanceBundle, GovernanceProfileSelector
    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(compliance_tags=["gdpr", "soc2"]),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )
    selector = GovernanceProfileSelector()
    gov = selector.select(profile, tenant_ctx=TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1"))
    assert gov.compliance_reporting_enabled is True
    assert gov.name == GovernanceBundle.REGULATED


def test_export_policy_cross_tenant_blocked():
    policy = ExportPolicy()
    result = policy.can_export("t1", "admin", DataCategory.GOAL_ARTIFACT, requesting_tenant_id="t2")
    assert result is False


def test_retention_pii_regulated():
    policy = RetentionPolicy()
    assert policy.get_tier(DataCategory.PII_DATA) == RetentionTier.REGULATED
