"""The memory services every agent graph gets, built one way for both run paths.

The API lifespan (``app.main``) binds these onto ``app.state`` for GoalService;
the Celery worker (``app.scaling.tasks.run_goal``) passes them straight into its
graph. Before this helper the worker omitted them, so queued goals never
recorded or recalled episodes, skills or tool reliability (MEM-02).
"""

from __future__ import annotations

from typing import Any


def build_memory_graph_services(db_factory: Any, embedder: Any = None) -> dict[str, Any]:
    """``{episodic_memory, procedural_memory, tool_reliability_store}`` for a graph.

    All three are DB-wired when *db_factory* is given (tenant-scoped under RLS);
    without one they are per-process, which only the DB-less dev/test build uses.
    """
    from app.memory.episodic import EpisodicMemoryStore
    from app.memory.procedural import ProceduralMemoryStore
    from app.memory.tool_reliability import ToolReliabilityStore

    return {
        "episodic_memory": EpisodicMemoryStore(db_factory=db_factory, embedder=embedder),
        "procedural_memory": ProceduralMemoryStore(db_factory=db_factory),
        "tool_reliability_store": ToolReliabilityStore(db_session_factory=db_factory),
    }
