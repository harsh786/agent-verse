"""ReAct (Reason + Act) pattern adapter."""
from __future__ import annotations

from app.agent.patterns.base import AgentPattern, PatternState


class ReActPattern(AgentPattern):
    @property
    def pattern_id(self) -> str:
        return "react"

    @property
    def state(self) -> PatternState:
        return PatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return "ReAct: interleaved reasoning and acting — implemented in app.agent.graph:AgentGraph"

    @property
    def node_name(self) -> str:
        return "execute"
