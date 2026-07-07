"""Self-Refine pattern adapter."""
from __future__ import annotations

from app.agent.patterns.base import AgentPattern, PatternState


class SelfRefinePattern(AgentPattern):
    @property
    def pattern_id(self) -> str:
        return "self_refine"

    @property
    def state(self) -> PatternState:
        return PatternState.PARTIAL

    @property
    def description(self) -> str:
        return "Self-Refine: iterative self-improvement via feedback — partial implementation"

    @property
    def node_name(self) -> str:
        return "_node_refine"
