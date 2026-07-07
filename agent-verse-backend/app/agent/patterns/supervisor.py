"""Supervisor multi-agent pattern adapter."""
from __future__ import annotations

from app.agent.patterns.base import AgentPattern, PatternState


class SupervisorPattern(AgentPattern):
    @property
    def pattern_id(self) -> str:
        return "supervisor"

    @property
    def state(self) -> PatternState:
        return PatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return "Supervisor: orchestrates sub-agents — implemented in app.agent.supervisor"

    @property
    def node_name(self) -> str:
        return "supervisor"
