"""Reflection pattern adapter."""
from __future__ import annotations

from app.agent.patterns.base import AgentPattern, PatternState


class ReflectionPattern(AgentPattern):
    @property
    def pattern_id(self) -> str:
        return "reflection"

    @property
    def state(self) -> PatternState:
        return PatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return "Reflection: post-execution self-critique — implemented in app.agent.graph:_node_reflect"

    @property
    def node_name(self) -> str:
        return "_node_reflect"
