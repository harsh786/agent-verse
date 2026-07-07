"""Peer-Review pattern adapter."""
from __future__ import annotations

from app.agent.patterns.base import AgentPattern, PatternState


class PeerReviewPattern(AgentPattern):
    @property
    def pattern_id(self) -> str:
        return "peer_review"

    @property
    def state(self) -> PatternState:
        return PatternState.PLANNED

    @property
    def description(self) -> str:
        return "Peer-Review: independent agent peer review of outputs — planned"
