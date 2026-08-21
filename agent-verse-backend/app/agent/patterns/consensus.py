"""Consensus verification pattern adapter."""

from __future__ import annotations

from app.agent.patterns.base import AgentPattern, PatternState


class ConsensusPattern(AgentPattern):
    @property
    def pattern_id(self) -> str:
        return "consensus"

    @property
    def state(self) -> PatternState:
        return PatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return "Consensus: multi-agent agreement verification — implemented in app.agent.consensus"

    @property
    def node_name(self) -> str:
        return "consensus"
