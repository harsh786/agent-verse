"""Agent package - canonical LangGraph-based autonomous execution kernel."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus

if TYPE_CHECKING:
    from app.agent.graph import AgentGraph


def __getattr__(name: str) -> Any:
    """Load the graph lazily so state-only imports cannot form a cycle."""
    if name == "AgentGraph":
        from app.agent.graph import AgentGraph

        return AgentGraph
    raise AttributeError(name)

__all__ = ["AgentGraph", "AgentState", "GoalStatus", "StepResult", "StepStatus"]
