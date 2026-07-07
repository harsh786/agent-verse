"""Tree-of-Thoughts pattern adapter."""
from __future__ import annotations

from app.agent.patterns.base import AgentPattern, PatternState


class TreeOfThoughtsPattern(AgentPattern):
    @property
    def pattern_id(self) -> str:
        return "tree_of_thoughts"

    @property
    def state(self) -> PatternState:
        return PatternState.PLANNED

    @property
    def description(self) -> str:
        return "Tree-of-Thoughts: deliberate search over reasoning tree — planned"
