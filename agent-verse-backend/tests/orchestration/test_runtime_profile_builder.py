"""Tests for RuntimeProfileBuilder — 6 async tests."""
from __future__ import annotations

import json

import pytest

from app.orchestration.runtime_profile import GoalRuntimeProfile
from app.orchestration.runtime_profile_builder import (
    InvalidStrategyOverrideError,
    RuntimeProfileBuilder,
)


async def test_build_returns_goal_runtime_profile():
    builder = RuntimeProfileBuilder()
    profile = await builder.build(
        "list all open tickets", tenant_id="t1", goal_id="g1"
    )
    assert isinstance(profile, GoalRuntimeProfile)
    assert profile.goal_id == "g1"
    assert profile.tenant_id == "t1"


async def test_build_with_trace_returns_tuple():
    builder = RuntimeProfileBuilder()
    result = await builder.build_with_trace(
        "deploy the service", tenant_id="t1", goal_id="g2"
    )
    assert isinstance(result, tuple)
    assert len(result) == 2
    profile, trace = result
    assert isinstance(profile, GoalRuntimeProfile)
    assert trace.goal_id == "g2"


async def test_trace_has_decisions():
    builder = RuntimeProfileBuilder()
    _, trace = await builder.build_with_trace(
        "analyze and research market trends", tenant_id="t1", goal_id="g3"
    )
    assert len(trace.decisions) >= 6  # classifier + 6 selector dimensions
    assert trace.total_latency_ms > 0


async def test_profile_is_json_serializable():
    builder = RuntimeProfileBuilder()
    profile = await builder.build(
        "write a python script to process CSV files", tenant_id="t1", goal_id="g4"
    )
    data = profile.to_dict()
    # Must not raise
    json.dumps(data)
    assert data["goal_id"] == "g4"
    assert data["tenant_id"] == "t1"


async def test_critical_risk_triggers_hitl():
    builder = RuntimeProfileBuilder()
    profile = await builder.build(
        "delete all production database tables permanently",
        tenant_id="t1",
        goal_id="g5",
    )
    assert profile.security.hitl_required is True
    assert profile.security.rollback_required is True


async def test_empty_kb_triggers_web_fallback():
    builder = RuntimeProfileBuilder()
    profile = await builder.build(
        "search for documents about quarterly goals",
        tenant_id="t1",
        goal_id="g6",
        kb_state="empty",
    )
    assert profile.rag_strategy.web_fallback_enabled is True
    assert "web_search" in profile.rag_strategy.sources


async def test_explicit_primary_override_is_canonical_and_versioned() -> None:
    profile = await RuntimeProfileBuilder().build(
        "analyze this incident",
        tenant_id="t1",
        goal_id="g-override",
        agent_config={
            "primary_strategy": "plan_and_execute",
            "ready_strategy_ids": ["plan_execute"],
        },
    )

    assert profile.primary_strategy.strategy_id == "plan_execute"
    assert profile.primary_strategy.adapter_version == "1.0.0"


async def test_explicit_override_without_limits_uses_bounded_defaults() -> None:
    profile = await RuntimeProfileBuilder().build(
        "analyze this incident",
        tenant_id="t1",
        goal_id="g-default-limits",
        agent_config={
            "primary_strategy": "plan_execute",
            "ready_strategy_ids": ["plan_execute"],
            "limits": None,
        },
    )

    assert profile.effective_limits.calls > 0
    assert profile.effective_limits.duration_seconds > 0


@pytest.mark.parametrize("strategy_id", ["unknown", "loop_engineering"])
async def test_invalid_explicit_primary_override_is_typed_client_error(
    strategy_id: str,
) -> None:
    with pytest.raises(InvalidStrategyOverrideError):
        await RuntimeProfileBuilder().build(
            "goal",
            tenant_id="t1",
            goal_id="g-invalid",
            agent_config={"primary_strategy": strategy_id},
        )


async def test_unready_automatic_candidate_falls_back_with_rejection() -> None:
    profile = await RuntimeProfileBuilder().build(
        "goal",
        tenant_id="t1",
        goal_id="g-fallback",
        agent_config={"ready_strategy_ids": ["reflection"]},
    )

    assert profile.primary_strategy.strategy_id == "react"
    assert profile.rejected_alternatives[0].reason_code == "not_ready_fallback"


async def test_incompatible_auxiliary_override_is_rejected_and_recorded() -> None:
    profile = await RuntimeProfileBuilder().build(
        "goal",
        tenant_id="t1",
        goal_id="g-aux",
        agent_config={
            "auxiliary_strategies": ["workflow_dag"],
            "ready_strategy_ids": ["react", "workflow_dag"],
        },
    )

    assert profile.auxiliary_strategies == ()
    assert profile.rejected_alternatives[0].reason_code == "incompatible_primary_tier"


async def test_repeated_builds_have_deterministic_authoritative_snapshot() -> None:
    builder = RuntimeProfileBuilder()
    first = await builder.build("same goal", tenant_id="t1", goal_id="g-same")
    second = await builder.build("same goal", tenant_id="t1", goal_id="g-same")

    assert first.profile_id == second.profile_id
    assert first.registry_revision == second.registry_revision
    assert first.primary_strategy == second.primary_strategy
    assert first.effective_limits == second.effective_limits


async def test_persistence_controls_are_captured_in_admitted_profile() -> None:
    profile = await RuntimeProfileBuilder().build(
        "durably complete this goal",
        tenant_id="t1",
        goal_id="g-persistent",
        agent_config={
            "persistence_mode": True,
            "max_persistence_attempts": 4,
            "max_iterations": 7,
        },
    )

    assert profile.agent_patterns.persistence_mode is True
    assert profile.agent_patterns.max_persistence_attempts == 4
    assert profile.agent_patterns.max_iterations == 7
