"""Test worker AgentGraph includes embedder= kwarg (FIX 0.6)."""
import pathlib


def test_tasks_py_includes_embedder_kwarg():
    src = pathlib.Path("app/scaling/tasks.py").read_text()
    idx = src.find("_agent_runner = AgentGraph(")
    assert idx != -1, "AgentGraph instantiation not found in tasks.py"
    snippet = src[idx:idx + 2000]
    assert "embedder=" in snippet, (
        "AgentGraph() in tasks.py must include embedder= kwarg. "
        "pgvector LTM recall is disabled without it."
    )


def test_tasks_py_builds_embedder_before_agentgraph():
    src = pathlib.Path("app/scaling/tasks.py").read_text()
    embedder_build_idx = src.find("_embedder_for_graph = None")
    agentgraph_idx = src.find("_agent_runner = AgentGraph(")
    assert embedder_build_idx != -1, "tasks.py must initialise _embedder_for_graph"
    assert embedder_build_idx < agentgraph_idx, (
        "_embedder_for_graph must be built before AgentGraph() constructor call"
    )
