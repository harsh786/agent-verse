"""ReadinessGate — blocks unsafe degraded execution."""
from __future__ import annotations
import pytest
from app.runtime_readiness.readiness_gate import ReadinessGate, ReadinessResult
from app.runtime_readiness.dependency_health import DependencyHealth, DepStatus
from app.runtime_readiness.degraded_mode_policy import DegradedModePolicy
from app.orchestration.runtime_profile import (
    GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
    ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig,
)


def _make_profile() -> GoalRuntimeProfile:
    return GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(web_fallback_enabled=True),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )


def test_all_healthy_is_ready() -> None:
    gate = ReadinessGate(DependencyHealth.all_healthy())
    result = gate.check(_make_profile())
    assert result.ready is True
    assert result.degraded is False
    assert result.blocking_deps == []


def test_postgres_down_blocks() -> None:
    health = DependencyHealth.all_healthy()
    health.postgres = DepStatus.UNAVAILABLE
    gate = ReadinessGate(health)
    result = gate.check(_make_profile())
    assert result.ready is False
    assert "postgres" in result.blocking_deps


def test_llm_provider_down_blocks() -> None:
    health = DependencyHealth.all_healthy()
    health.llm_provider = DepStatus.UNAVAILABLE
    gate = ReadinessGate(health)
    result = gate.check(_make_profile())
    assert result.ready is False
    assert "llm_provider" in result.blocking_deps


def test_redis_down_degrades_not_blocks() -> None:
    health = DependencyHealth.all_healthy()
    health.redis = DepStatus.UNAVAILABLE
    gate = ReadinessGate(health)
    result = gate.check(_make_profile())
    assert result.ready is True
    assert result.degraded is True
    assert "redis" in result.unavailable_optional


def test_embedder_down_degrades() -> None:
    health = DependencyHealth.all_healthy()
    health.embedder = DepStatus.UNAVAILABLE
    gate = ReadinessGate(health)
    result = gate.check(_make_profile())
    assert result.ready is True
    assert result.degraded is True


def test_degraded_mode_policy_disables_web_fallback() -> None:
    policy = DegradedModePolicy()
    profile = _make_profile()
    assert profile.rag_strategy.web_fallback_enabled is True
    updated = policy.apply_degraded_rag(profile, unavailable_deps={"web_search"})
    assert updated.rag_strategy.web_fallback_enabled is False
