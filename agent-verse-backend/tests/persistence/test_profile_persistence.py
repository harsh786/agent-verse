"""GoalRuntimeProfile must survive in Postgres goals.execution_context."""
from __future__ import annotations

import json

from app.orchestration.runtime_profile_builder import RuntimeProfileBuilder
from app.orchestration.strategy_registry import build_default_registry


async def test_runtime_profile_serializes_to_json() -> None:
    builder = RuntimeProfileBuilder(registry=build_default_registry())
    profile, trace = await builder.build_with_trace(
        "list all open Jira tickets",
        tenant_id="t1",
        goal_id="persist_test_1",
    )
    profile_dict = profile.to_dict()
    json_str = json.dumps(profile_dict)
    assert len(json_str) > 100
    restored = json.loads(json_str)
    assert restored["goal_id"] == "persist_test_1"
    assert restored["tenant_id"] == "t1"
    assert "properties" in restored
    assert "agent_patterns" in restored
    assert "rag_strategy" in restored
    assert "security" in restored


async def test_decision_trace_serializes_to_json() -> None:
    builder = RuntimeProfileBuilder(registry=build_default_registry())
    profile, trace = await builder.build_with_trace(
        "delete production database",
        tenant_id="t1",
        goal_id="persist_test_2",
    )
    trace_dict = trace.to_dict()
    json_str = json.dumps(trace_dict)
    assert "decisions" in trace_dict
    assert len(trace_dict["decisions"]) > 0


async def test_profile_stored_in_execution_context_structure() -> None:
    builder = RuntimeProfileBuilder(registry=build_default_registry())
    profile, trace = await builder.build_with_trace(
        "analyze sales data",
        tenant_id="t1",
        goal_id="persist_test_3",
    )
    execution_context = {
        "runtime_profile": profile.to_dict(),
        "decision_trace": trace.to_dict(),
        "profile_id": profile.profile_id,
        "assembly_latency_ms": profile.assembly_latency_ms,
    }
    json_str = json.dumps(execution_context)
    assert "runtime_profile" in json.loads(json_str)
    assert "decision_trace" in json.loads(json_str)


def test_goal_model_has_execution_context_column() -> None:
    from app.db.models.goal import Goal

    assert hasattr(Goal, "execution_context"), (
        "Goal model missing execution_context column"
    )


def test_goal_service_build_runtime_profile_returns_dict() -> None:
    from app.services.goal_service import GoalService

    assert hasattr(GoalService, "_build_runtime_profile"), (
        "GoalService missing _build_runtime_profile"
    )
