"""Tests for runtime flags and GoalRuntimeProfile contracts."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

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
    StrategyRejection,
    StrategySelection,
)
from app.orchestration.strategy_adapters import ExecutionTier
from app.orchestration.strategy_contracts import PatternLimits
from app.rag.contracts import RAGStrategy


def test_flags_default_values():
    flags = RuntimeFlags()
    # Advanced orchestration + safety/compliance are now first-class (default on).
    assert flags.dynamic_orchestration is True
    assert flags.data_classification is True
    assert flags.guardrail_profile is True
    assert flags.readiness_gate is True
    assert flags.enable_guardrail_profile is True
    # Agentic RAG is first-class (default on): the embedding cache no longer
    # bypasses budget, so the retrieval budget is enforced on every fetch.
    assert flags.agentic_rag is True
    # All orchestration flags are now first-class (default on): the flag fields
    # either gate nothing (their subsystems run unconditionally) or are already
    # implied by dynamic_orchestration, so their reported default is now truthful.
    assert flags.plan_verification is True
    assert flags.capability_registry is True
    assert flags.policy_compiler is True
    assert flags.runtime_scorecard is True
    assert flags.enable_runtime_scorecard is True
    assert flags.enable_pattern_sse_events is True


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


def _limits(**overrides: int | float) -> PatternLimits:
    values: dict[str, int | float] = {
        "calls": 10,
        "nodes": 20,
        "edges": 30,
        "depth": 4,
        "fan_out": 3,
        "rounds": 5,
        "tokens": 10_000,
        "duration_seconds": 60,
        "cost_usd": 1.0,
    }
    values.update(overrides)
    return PatternLimits.model_validate(values)


def test_goal_runtime_profile_v2_has_authoritative_strategy_snapshot() -> None:
    deadline = datetime.now(UTC) + timedelta(minutes=5)
    profile = GoalRuntimeProfile(
        goal_id="g-v2",
        tenant_id="t-v2",
        properties=GoalProperties(raw_goal="research safely"),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
        registry_revision="sha256:catalogue-v2",
        primary_strategy=StrategySelection("react", "1.0.0"),
        auxiliary_strategies=(StrategySelection("guardrails", "1.0.0"),),
        execution_tier=ExecutionTier.LOCAL,
        effective_limits=_limits(),
        readiness_snapshot_ref="readiness:r1",
        policy_snapshot_ref="policy:p1",
        budget_snapshot_ref="budget:b1",
        deadline=deadline,
        selected_alternatives=(StrategySelection("react", "1.0.0"),),
        rejected_alternatives=(StrategyRejection("debate", "incompatible_tier"),),
        model_role_assignments=(("planner", "model-a"),),
    )

    assert profile.profile_version == 2
    assert profile.primary_strategy.strategy_id == "react"
    assert profile.to_dict()["execution_tier"] == "local"
    assert profile.to_dict()["effective_limits"]["calls"] == 10


def test_goal_runtime_profile_v2_rejects_expired_deadline_and_duplicate_primary() -> None:
    kwargs = {
        "goal_id": "g-invalid",
        "tenant_id": "t-invalid",
        "properties": GoalProperties(raw_goal="goal"),
        "agent_patterns": AgentPatternConfig(),
        "rag_strategy": RAGStrategyConfig(),
        "model_plan": ModelPlanConfig(),
        "security": SecurityConfig(),
        "memory_cache": MemoryCacheConfig(),
        "eval_config": EvalConfig(),
        "deadline": datetime.now(UTC) - timedelta(seconds=1),
    }
    with pytest.raises(ValueError, match="deadline must be in the future"):
        GoalRuntimeProfile(**kwargs)

    kwargs["deadline"] = datetime.now(UTC) + timedelta(minutes=1)
    kwargs["primary_strategy"] = StrategySelection("react", "1.0.0")
    kwargs["auxiliary_strategies"] = (StrategySelection("react", "1.0.0"),)
    with pytest.raises(ValueError, match="primary strategy cannot be auxiliary"):
        GoalRuntimeProfile(**kwargs)


def test_goal_runtime_profile_is_top_level_immutable_and_enforces_tenant_limits() -> None:
    profile = GoalRuntimeProfile(
        goal_id="g-frozen",
        tenant_id="t-frozen",
        properties=GoalProperties(raw_goal="goal"),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )
    with pytest.raises(AttributeError):
        profile.primary_strategy = StrategySelection("reflection", "1.0.0")

    with pytest.raises(ValueError, match="effective limit exceeds tenant ceiling: calls"):
        GoalRuntimeProfile(
            goal_id="g-ceiling",
            tenant_id="t-ceiling",
            properties=GoalProperties(raw_goal="goal"),
            agent_patterns=AgentPatternConfig(),
            rag_strategy=RAGStrategyConfig(),
            model_plan=ModelPlanConfig(),
            security=SecurityConfig(),
            memory_cache=MemoryCacheConfig(),
            eval_config=EvalConfig(),
            effective_limits=_limits(calls=11),
            tenant_limit_ceiling=_limits(calls=10),
        )
