"""Self-Consistency pattern adapter."""
from __future__ import annotations

from app.agent.patterns.base import AgentPattern, PatternState


class SelfConsistencyPattern(AgentPattern):
    @property
    def pattern_id(self) -> str:
        return "self_consistency"

    @property
    def state(self) -> PatternState:
        return PatternState.PLANNED

    @property
    def description(self) -> str:
        return "Self-Consistency: sample multiple reasoning paths and vote — planned"
