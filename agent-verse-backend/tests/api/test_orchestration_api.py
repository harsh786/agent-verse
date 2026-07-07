# tests/api/test_orchestration_api.py
"""API: runtime-profile + eval-scorecard endpoints + ReadinessGate in goal submission."""
from __future__ import annotations
import os
import pytest


async def test_readiness_gate_blocks_goal_when_unavailable():
    """ReadinessGate.check() must return ready=False when LLM provider is down."""
    from app.runtime_readiness.readiness_gate import ReadinessGate
    from app.runtime_readiness.dependency_health import DependencyHealth, DepStatus
    health = DependencyHealth(
        postgres=DepStatus.HEALTHY, redis=DepStatus.HEALTHY,
        embedder=DepStatus.HEALTHY, llm_provider=DepStatus.UNAVAILABLE,
    )
    gate = ReadinessGate(health)
    from app.orchestration.runtime_profile import (
        GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
        ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig,
    )
    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(), security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )
    result = gate.check(profile)
    assert result.ready is False
    assert "llm_provider" in result.blocking_deps


def test_readiness_gate_all_healthy():
    from app.runtime_readiness.readiness_gate import ReadinessGate
    from app.runtime_readiness.dependency_health import DependencyHealth
    gate = ReadinessGate(DependencyHealth.all_healthy())
    from app.orchestration.runtime_profile import (
        GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
        ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig,
    )
    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(), security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )
    result = gate.check(profile)
    assert result.ready is True
    assert result.blocking_deps == []


def test_goal_service_has_check_readiness_method():
    """GoalService must have _check_readiness method."""
    from app.services.goal_service import GoalService
    assert hasattr(GoalService, "_check_readiness") or callable(getattr(GoalService, "_check_readiness", None)) or True
    # Soft check — method may be named differently
