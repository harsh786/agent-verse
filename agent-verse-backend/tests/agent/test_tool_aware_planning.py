"""Tests for Phase 5: tool-aware planning in graph.py."""
from __future__ import annotations


def _agent_source() -> str:
    """Read combined source of graph.py and all node mixin files."""
    import pathlib
    parts = [pathlib.Path("app/agent/graph.py").read_text(encoding="utf-8")]
    for f in sorted(pathlib.Path("app/agent/nodes").glob("*.py")):
        parts.append(f.read_text(encoding="utf-8"))
    return "\n".join(parts)

def test_graph_injects_tool_schemas_into_planner():
    """graph.py _node_plan must inject tool schemas into system prompt."""


    src = _agent_source()
    assert (
        "discover_all_tools" in src
        or "tool_context_text" in src
        or "[Available tools]" in src
    ), "_node_plan must inject live tool schemas from MCP registry"


def test_workflow_plan_validates_tools():
    """Plan validation should warn about unknown tools."""


    src = _agent_source()
    assert "_validate_plan_tools" in src, (
        "graph.py must have plan tool validation"
    )


def test_executor_prompt_requires_structured_tool_call_json():
    """Executor prompt must match the JSON-only tool-call parser contract."""
    from app.agent.prompts import EXECUTOR_SYSTEM

    assert '"tool"' in EXECUTOR_SYSTEM
    assert '"arguments"' in EXECUTOR_SYSTEM
    assert "ONLY JSON" in EXECUTOR_SYSTEM or "valid JSON" in EXECUTOR_SYSTEM
