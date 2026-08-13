"""Backward-compatible import for the canonical :class:`AgentGraph` runtime.

``AgentGraph`` replaced the original hand-written loop.  Keep this alias so
older integrations can migrate without losing the single execution kernel.
"""

from app.agent.graph import AgentGraph

AgentLoop = AgentGraph

__all__ = ["AgentLoop"]
