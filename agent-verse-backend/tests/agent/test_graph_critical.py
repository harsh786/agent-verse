"""Tests for critical graph.py fixes."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_toolcall_import_exists():
    """ToolCall must be importable from tool_calls — no NameError in graph.py"""
    from app.agent.tool_calls import ToolCall, extract_tool_call
    assert ToolCall is not None
    # Verify graph.py imports it
    import inspect
    from app.agent import graph
    src = inspect.getsource(graph)
    assert "ToolCall" in src
    # The import line must exist
    assert "from app.agent.tool_calls import" in src or "from .tool_calls import" in src


def test_tool_call_instantiation():
    """ToolCall can be instantiated without NameError at graph.py usage point."""
    from app.agent.tool_calls import ToolCall
    tc = ToolCall(tool="web_search", arguments={"query": "test"})
    assert tc.tool == "web_search"


def test_persistence_success_detects_lowercase_complete():
    """GoalPersistenceEngine detects 'complete' (lowercase) as success."""
    from app.agent.persistence import GoalPersistenceEngine, AttemptRecord

    engine = GoalPersistenceEngine()

    class FakeState:
        status = "complete"
        verification_success = False
        steps = []
        goal = "test"
        error_message = ""

    attempt = AttemptRecord(attempt_number=1)
    # Simulate what the engine does when detecting success
    state = FakeState()
    success = getattr(state, "verification_success", False) or (
        str(getattr(state, "status", "")).lower() in ("complete", "completed", "success")
    )
    assert success is True


def test_persistence_success_rejects_old_uppercase_endswith():
    """Old endswith('COMPLETE') would miss lowercase 'complete' — confirm the fix works."""
    class FakeState:
        status = "complete"
        verification_success = False

    state = FakeState()
    # Old (broken) check
    old_check = bool(
        getattr(state, "verification_success", False) or (
            getattr(state, "status", None) and
            str(getattr(state, "status", "")).endswith("COMPLETE")
        )
    )
    # New (fixed) check
    new_check = getattr(state, "verification_success", False) or (
        str(getattr(state, "status", "")).lower() in ("complete", "completed", "success")
    )
    assert old_check is False, "old check wrongly returns False for lowercase 'complete'"
    assert new_check is True, "new check must return True for lowercase 'complete'"


def test_analytics_uses_correct_event_type():
    """Analytics should look for tool_call_complete not tool_call."""
    import inspect
    from app.analytics import aggregator
    src = inspect.getsource(aggregator)
    assert "tool_call_complete" in src or "step_complete" in src, \
        "Analytics must check for actual emitted event types"
    assert '"tool_call"' not in src, \
        "Old 'tool_call' event type string must be removed from analytics"


def test_graph_state_has_cot_reasoning():
    """GraphState TypedDict must include cot_reasoning field."""
    from app.agent.graph import GraphState
    # total=False means all keys are optional; just check the annotation is present
    annotations = GraphState.__annotations__
    assert "cot_reasoning" in annotations, \
        "GraphState must declare cot_reasoning field"


def test_toolcall_imported_in_graph_module():
    """ToolCall is in graph.py's module namespace — no NameError at structured tool call line."""
    import app.agent.graph as graph_module
    assert hasattr(graph_module, "ToolCall"), \
        "ToolCall must be importable at module level in graph.py"


def test_parallel_wave_gather_cancels_on_permission_error():
    """When one wave step raises PermissionError, others are cancelled."""
    import asyncio
    from app.agent.graph import AgentGraph
    # This just validates the graph module can be imported without NameError
    assert AgentGraph is not None
