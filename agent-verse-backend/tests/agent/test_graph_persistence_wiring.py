# tests/agent/test_graph_persistence_wiring.py
"""graph.py must call correct SelfOptimizerV2 API and persist tool outcomes/scorecards."""
from __future__ import annotations


def _agent_source() -> str:
    """Read combined source of graph.py and all node mixin files."""
    import pathlib
    parts = [pathlib.Path("app/agent/graph.py").read_text(encoding="utf-8")]
    for f in sorted(pathlib.Path("app/agent/nodes").glob("*.py")):
        parts.append(f.read_text(encoding="utf-8"))
    return "\n".join(parts)

def test_graph_calls_on_goal_completed_not_record_result():
    """graph.py must use on_goal_completed(), not the non-existent record_result()."""
    src = _agent_source()
    assert "record_result(" not in src or "on_goal_completed(" in src, \
        "graph.py still calls record_result() — should be on_goal_completed()"
    # Stronger: on_goal_completed must appear
    assert "on_goal_completed(" in src, "on_goal_completed() not found in graph.py"


def test_graph_references_persist_tool_outcome():
    """graph.py must reference persist_tool_outcome for cross-restart trust."""
    src = _agent_source()
    assert "persist_tool_outcome" in src, "persist_tool_outcome not called in graph.py"


def test_graph_references_persist_scorecard():
    """graph.py must persist scorecards via OrchestrationPersistence."""
    src = _agent_source()
    assert "persist_scorecard" in src, "persist_scorecard not called in graph.py"


def test_graph_references_regression_gate():
    """graph.py must call RegressionGate for low-scoring goals."""
    src = _agent_source()
    assert "RegressionGate" in src or "regression_gate" in src.lower(), \
        "RegressionGate not wired in graph.py"


def test_self_optimizer_v2_has_no_record_result_method():
    """SelfOptimizerV2 must NOT have record_result() (only on_goal_completed)."""
    from app.intelligence.self_optimizer_v2 import SelfOptimizerV2
    assert not hasattr(SelfOptimizerV2, "record_result"), \
        "SelfOptimizerV2.record_result should not exist — use on_goal_completed()"
    assert hasattr(SelfOptimizerV2, "on_goal_completed"), \
        "SelfOptimizerV2 must have on_goal_completed()"
