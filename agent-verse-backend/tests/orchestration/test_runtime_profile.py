"""Tests for runtime flags and GoalRuntimeProfile contracts."""
from __future__ import annotations

from app.core.runtime_flags import RuntimeFlags, get_runtime_flags
from app.orchestration.runtime_profile import (
    AgentPatternConfig,
    Complexity,
    ContextRuntimeProfile,
    Domain,
    EvalConfig,
    GoalProperties,
    GoalRuntimeProfile,
    KnowledgeRuntimeProfile,
    KnowledgeState,
    MemoryCacheConfig,
    ModelPlanConfig,
    MultimodalRuntimeProfile,
    RAGStrategyConfig,
    RiskLevel,
    SecurityConfig,
    SecurityRuntimeProfile,
    SelfImprovementProfile,
)
from app.rag.contracts import RAGStrategy


def test_flags_default_values():
    flags = RuntimeFlags()
    assert flags.dynamic_orchestration is False
    assert flags.agentic_rag is False
    assert flags.plan_verification is False
    assert flags.data_classification is False
    assert flags.capability_registry is False
    assert flags.policy_compiler is False


def test_flags_from_env(monkeypatch):
    monkeypatch.setenv("DYNAMIC_ORCHESTRATION", "true")
    monkeypatch.setenv("AGENTIC_RAG", "true")
    flags = RuntimeFlags.from_env()
    assert flags.dynamic_orchestration is True
    assert flags.agentic_rag is True


def test_get_runtime_flags_singleton():
    get_runtime_flags.cache_clear()
    f1 = get_runtime_flags()
    f2 = get_runtime_flags()
    assert f1 is f2


def test_goal_properties_defaults():
    props = GoalProperties(raw_goal="list all open tickets")
    assert props.complexity == Complexity.SIMPLE
    assert props.risk == RiskLevel.LOW
    assert props.domain == Domain.OPERATIONAL
    assert props.requires_web is False
    assert props.multi_step is True


def test_goal_runtime_profile_is_serializable():
    import json

    profile = GoalRuntimeProfile(
        goal_id="g1",
        tenant_id="t1",
        properties=GoalProperties(raw_goal="deploy to prod"),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(hitl_required=True),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )
    data = profile.to_dict()
    assert data["goal_id"] == "g1"
    assert data["security"]["hitl_required"] is True
    json.dumps(data)


def test_rag_profile_defaults_use_canonical_strategy_ids() -> None:
    assert AgentPatternConfig().rag == [RAGStrategy.HYBRID.value]
    assert RAGStrategyConfig().strategy == RAGStrategy.HYBRID.value


def test_high_risk_profile_flags():
    profile = GoalRuntimeProfile(
        goal_id="g2",
        tenant_id="t1",
        properties=GoalProperties(raw_goal="delete production db", risk=RiskLevel.CRITICAL),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(
            hitl_required=True, rollback_required=True, consensus_required=True
        ),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )
    assert profile.security.hitl_required is True
    assert profile.security.rollback_required is True
    assert profile.security.consensus_required is True


def test_goal_properties_kb_state_enum():
    props = GoalProperties(raw_goal="search docs", kb_state=KnowledgeState.EMPTY)
    assert props.kb_state == KnowledgeState.EMPTY
    assert props.web_fallback_required is True


def test_named_spec_profiles_importable():
    import json

    mp = MultimodalRuntimeProfile(content_type="pdf", model_roles={"extractor": "gpt-4o"})
    json.dumps(mp.to_dict())
    sp = SelfImprovementProfile(eval_suite="security")
    json.dumps(sp.to_dict())
    cp = ContextRuntimeProfile(reranker="rrf")
    json.dumps(cp.to_dict())
    kp = KnowledgeRuntimeProfile(kb_state="healthy", graph_strategy="entity")
    json.dumps(kp.to_dict())
    srp = SecurityRuntimeProfile(guardrail_bundle="strict", identity_scope="agent")
    json.dumps(srp.to_dict())
