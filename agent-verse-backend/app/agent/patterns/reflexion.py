"""Reflexion pattern adapter."""
from __future__ import annotations

from app.agent.patterns.base import AgentPattern, PatternState


class ReflexionPattern(AgentPattern):
    @property
    def pattern_id(self) -> str:
        return "reflexion"

    @property
    def state(self) -> PatternState:
        return PatternState.PARTIAL

    @property
    def description(self) -> str:
        return "Reflexion: reflection with episodic memory for self-improvement — partial implementation"
