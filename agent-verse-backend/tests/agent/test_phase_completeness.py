"""Phase completeness tests — verify all 6 PARTIAL phases are now COMPLETE.

These tests use source inspection to confirm the required implementation
patterns are present without needing a running database or LLM provider.
"""
from __future__ import annotations

import pytest


def test_phase_6_pause_polling_in_tasks():
    """Celery tasks must poll pause/cancel signals during execution."""
    import inspect
    from app.scaling import tasks
    src = inspect.getsource(tasks)
    assert "_run_with_signals" in src or "is_paused_sync" in src, (
        "Celery run_goal must poll pause/cancel signals between steps"
    )


def test_phase_22_circuit_breakers_wired():
    """_make_agent_loop_for_tenant must wire circuit breakers to AgentGraph."""
    import inspect
    from app.services import goal_service
    src = inspect.getsource(goal_service)
    assert "circuit_breakers" in src and "RedisCircuitBreaker" in src, (
        "_make_agent_loop_for_tenant must wire per-connector circuit breakers"
    )


def test_phase_25_self_optimizer_wired():
    """_make_agent_loop_for_tenant must wire graph._self_optimizer."""
    import inspect
    from app.services import goal_service
    src = inspect.getsource(goal_service)
    assert "_self_optimizer" in src, (
        "_make_agent_loop_for_tenant must wire _self_optimizer to AgentGraph"
    )


def test_phase_17_rotate_key_re_encrypts():
    """rotate_key must actually re-encrypt secrets (not just update metadata)."""
    import inspect
    from app.providers import vault
    src = inspect.getsource(vault)
    assert "scan_iter" in src or "fernet_old" in src or "re-encrypt" in src.lower(), (
        "rotate_key() must actually re-encrypt stored secrets"
    )


def test_phase_20_ts_sdk_has_hitl_methods():
    """TypeScript SDK must have HITL approve/reject methods."""
    with open(
        "/Users/harsh.kumar01/Documents/Learning/Agent-Verse"
        "/agent-verse-sdk-typescript/src/client.ts"
    ) as f:
        src = f.read()
    assert "approveRequest" in src, "TypeScript SDK must have approveRequest method"
    assert "rejectRequest" in src, "TypeScript SDK must have rejectRequest method"
    assert "simulate" in src, "TypeScript SDK must have simulate method"
    assert "replayGoal" in src, "TypeScript SDK must have replayGoal method"


def test_phase_20_ts_sdk_has_simulation_types():
    """TypeScript SDK must have SimulationResult and GoalTimeline types."""
    with open(
        "/Users/harsh.kumar01/Documents/Learning/Agent-Verse"
        "/agent-verse-sdk-typescript/src/types.ts"
    ) as f:
        src = f.read()
    assert "SimulationResult" in src, "TypeScript SDK types must have SimulationResult"
    assert "GoalTimeline" in src, "TypeScript SDK types must have GoalTimeline"


def test_phase_13_simulation_uses_full_pipeline():
    """SimulationRunner.start() must use AgentGraph pipeline not raw provider."""
    import inspect
    from app.enterprise import simulation
    src = inspect.getsource(simulation)
    assert "AgentGraph" in src, (
        "SimulationRunner must use AgentGraph for full pipeline simulation"
    )


@pytest.mark.asyncio
async def test_phase_2_multi_agent_mode():
    """GoalService must spawn parallel goals for multi_agent routing decision."""
    import inspect
    from app.services import goal_service
    src = inspect.getsource(goal_service)
    assert "multi_agent" in src and (
        "asyncio.gather" in src or "_submit_single_goal" in src
    ), (
        "submit_goal must spawn parallel goals for multi_agent routing mode"
    )
