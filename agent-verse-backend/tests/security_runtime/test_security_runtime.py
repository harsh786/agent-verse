"""SecurityRuntime: GuardrailProfileSelector, GovernanceProfileSelector, PolicyBundleSelector."""
from __future__ import annotations
import pytest
from app.security_runtime.guardrail_profile import GuardrailProfileSelector, GuardrailBundle
from app.security_runtime.governance_profile import GovernanceProfileSelector, GovernanceBundle
from app.security_runtime.policy_bundle_selector import PolicyBundleSelector
from app.orchestration.runtime_profile import (
    GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
    ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig, RiskLevel,
)
from app.tenancy.context import TenantContext, PlanTier


def _make_profile(risk: RiskLevel = RiskLevel.LOW, compliance: list[str] | None = None) -> GoalRuntimeProfile:
    return GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test", risk=risk),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(hitl_required=risk in (RiskLevel.HIGH, RiskLevel.CRITICAL),
                                compliance_tags=compliance or []),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )


def _tenant(plan: PlanTier = PlanTier.PROFESSIONAL) -> TenantContext:
    return TenantContext(tenant_id="t1", plan=plan, api_key_id="k1")


def test_low_risk_gets_default_bundle():
    s = GuardrailProfileSelector()
    b = s.select(_make_profile(RiskLevel.LOW), tenant_ctx=_tenant())
    assert b.name == GuardrailBundle.DEFAULT


def test_high_risk_gets_strict_bundle():
    s = GuardrailProfileSelector()
    b = s.select(_make_profile(RiskLevel.HIGH), tenant_ctx=_tenant())
    assert b.name == GuardrailBundle.STRICT
    assert b.scan_prompt_injection is True
    assert b.scan_output_pii is True


def test_critical_gets_exfil_guard():
    s = GuardrailProfileSelector()
    b = s.select(_make_profile(RiskLevel.CRITICAL), tenant_ctx=_tenant())
    assert b.exfiltration_guard_enabled is True


def test_gdpr_tag_gets_regulated_bundle():
    s = GuardrailProfileSelector()
    b = s.select(_make_profile(RiskLevel.LOW, ["gdpr"]), tenant_ctx=_tenant())
    assert b.name == GuardrailBundle.REGULATED
    assert b.pii_redaction_enabled is True


def test_free_plan_governance():
    s = GovernanceProfileSelector()
    g = s.select(_make_profile(), tenant_ctx=_tenant(PlanTier.FREE))
    assert g.name == GovernanceBundle.FREE
    assert g.cost_control_enabled is True


def test_enterprise_governance():
    s = GovernanceProfileSelector()
    g = s.select(_make_profile(RiskLevel.CRITICAL), tenant_ctx=_tenant(PlanTier.ENTERPRISE))
    assert g.name == GovernanceBundle.ENTERPRISE
    assert g.policy_engine_enabled is True


def test_policy_bundle_low_risk():
    s = PolicyBundleSelector()
    p = s.select(_make_profile(RiskLevel.LOW), tenant_ctx=_tenant())
    assert p.audit_level == "standard"
    assert p.max_cost_usd > 0


def test_policy_bundle_critical_forensic():
    s = PolicyBundleSelector()
    p = s.select(_make_profile(RiskLevel.CRITICAL), tenant_ctx=_tenant())
    assert p.audit_level == "forensic"
    assert "hitl" in p.required_approvals


def test_policy_bundle_free_plan_denies_shell():
    s = PolicyBundleSelector()
    p = s.select(_make_profile(), tenant_ctx=_tenant(PlanTier.FREE))
    assert "tool:shell" in p.denied_capabilities


def test_unknown_dep_status_warns_but_passes():
    from app.runtime_readiness.dependency_health import DependencyHealth, DepStatus
    from app.runtime_readiness.readiness_gate import ReadinessGate
    from app.orchestration.runtime_profile import (
        GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
        ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig,
    )
    # UNKNOWN health should generate warning but not block
    health = DependencyHealth()  # all UNKNOWN by default
    gate = ReadinessGate(health)
    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(), security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )
    result = gate.check(profile)
    assert result.ready is True  # UNKNOWN doesn't block
    assert any("UNKNOWN" in w for w in result.degradation_warnings)


def test_developer_bundle_reduces_scanning():
    from app.security_runtime.guardrail_profile import GuardrailProfileSelector, GuardrailBundle
    from app.orchestration.runtime_profile import (
        GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
        ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig,
    )
    from app.tenancy.context import TenantContext, PlanTier
    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(guardrail_bundle="developer"),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )
    selector = GuardrailProfileSelector()
    config = selector.select(profile, tenant_ctx=TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1"))
    assert config.name == GuardrailBundle.DEVELOPER
    assert config.scan_prompt_injection is False


def test_rpa_bundle_scans_exfil():
    from app.security_runtime.guardrail_profile import GuardrailProfileSelector, GuardrailBundle
    from app.orchestration.runtime_profile import (
        GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
        ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig,
    )
    from app.tenancy.context import TenantContext, PlanTier
    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(guardrail_bundle="rpa"),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )
    selector = GuardrailProfileSelector()
    config = selector.select(profile, tenant_ctx=TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1"))
    assert config.name == GuardrailBundle.RPA
    assert config.exfiltration_guard_enabled is True


def test_high_risk_findings_preserved_after_critical_step():
    from app.plan_runtime.plan_risk_analyzer import PlanRiskAnalyzer
    analyzer = PlanRiskAnalyzer()
    plan = ["delete all user records", "send email notification to all users"]
    risk_level, findings = analyzer.analyze(plan)
    assert risk_level == "critical"
    # Both CRITICAL and HIGH findings must be in the list
    assert any("CRITICAL" in f for f in findings)
    assert any("HIGH" in f or "notification" in f.lower() for f in findings)
