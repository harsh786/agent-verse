"""Goal-Tree multi-agent pattern adapter."""

from __future__ import annotations

from app.agent.patterns.base import AgentPattern, PatternState


class GoalTreePattern(AgentPattern):
    @property
    def pattern_id(self) -> str:
        return "goal_tree"

    @property
    def state(self) -> PatternState:
        return PatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return "Goal-Tree: hierarchical goal decomposition — implemented in app.agent.goal_tree"

    @property
    def node_name(self) -> str:
        return "goal_tree_plan"
