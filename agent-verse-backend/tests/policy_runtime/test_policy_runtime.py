"""PolicyCompiler + RuntimeEnforcer — P0 security gates."""
from __future__ import annotations
import pytest
from app.policy_runtime.compiler import PolicyCompiler
from app.policy_runtime.runtime_enforcer import RuntimeEnforcer
from app.policy_runtime.constraint_model import RuntimeConstraints
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
        security=SecurityConfig(compliance_tags=compliance or []),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )


def _tenant(plan: PlanTier = PlanTier.PROFESSIONAL) -> TenantContext:
    return TenantContext(tenant_id="t1", plan=plan, api_key_id="k1")


def test_low_risk_standard_audit() -> None:
    c = PolicyCompiler()
    r = c.compile(_make_profile(RiskLevel.LOW), tenant_ctx=_tenant())
    assert r.audit_level == "standard"
    assert r.max_cost_usd == 50.0


def test_critical_risk_forensic_audit() -> None:
    c = PolicyCompiler()
    r = c.compile(_make_profile(RiskLevel.CRITICAL), tenant_ctx=_tenant())
    assert r.audit_level == "forensic"
    assert "hitl" in r.required_approvals


def test_free_plan_denies_shell() -> None:
    c = PolicyCompiler()
    r = c.compile(_make_profile(), tenant_ctx=_tenant(PlanTier.FREE))
    assert "tool:shell" in r.denied_capabilities
    assert r.max_cost_usd == 2.0


def test_enterprise_plan_high_cost_limit() -> None:
    c = PolicyCompiler()
    r = c.compile(_make_profile(), tenant_ctx=_tenant(PlanTier.ENTERPRISE))
    assert r.max_cost_usd == 500.0


def test_enforcer_allows_unlisted_capability() -> None:
    constraints = RuntimeConstraints(
        allowed_capabilities=[], denied_capabilities=[], required_approvals=[],
        max_cost_usd=10.0, audit_level="standard",
    )
    e = RuntimeEnforcer()
    assert e.is_capability_allowed("tool:web_search", constraints) is True


def test_enforcer_blocks_denied_capability() -> None:
    constraints = RuntimeConstraints(
        allowed_capabilities=[], denied_capabilities=["tool:shell"], required_approvals=[],
        max_cost_usd=10.0, audit_level="standard",
    )
    e = RuntimeEnforcer()
    assert e.is_capability_allowed("tool:shell", constraints) is False
    assert e.check_cost(5.0, constraints) is True
    assert e.check_cost(15.0, constraints) is False
