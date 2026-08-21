"""Debate multi-agent pattern adapter."""

from __future__ import annotations

from app.agent.patterns.base import AgentPattern, PatternState


class DebatePattern(AgentPattern):
    @property
    def pattern_id(self) -> str:
        return "debate"

    @property
    def state(self) -> PatternState:
        return PatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return (
            "Debate: adversarial multi-agent debate for quality — implemented in app.agent.debate"
        )

    @property
    def node_name(self) -> str:
        return "debate"
