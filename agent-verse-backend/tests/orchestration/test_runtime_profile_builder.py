"""Tests for RuntimeProfileBuilder — 6 async tests."""
from __future__ import annotations

import json
import pytest
from app.orchestration.runtime_profile import GoalRuntimeProfile, RiskLevel
from app.orchestration.runtime_profile_builder import RuntimeProfileBuilder


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
