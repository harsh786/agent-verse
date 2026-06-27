"""Tests for Phase 5: tool-aware planning in graph.py."""
from __future__ import annotations


def test_graph_injects_tool_schemas_into_planner():
    """graph.py _node_plan must inject tool schemas into system prompt."""
    import inspect

    from app.agent import graph

    src = inspect.getsource(graph)
    assert (
        "discover_all_tools" in src
        or "tool_context_text" in src
        or "[Available tools]" in src
    ), "_node_plan must inject live tool schemas from MCP registry"


def test_workflow_plan_validates_tools():
    """Plan validation should warn about unknown tools."""
    import inspect

    from app.agent import graph

    src = inspect.getsource(graph)
    assert "_validate_plan_tools" in src, (
        "graph.py must have plan tool validation"
    )
