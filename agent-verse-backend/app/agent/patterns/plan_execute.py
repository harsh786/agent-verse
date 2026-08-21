"""Plan-and-Execute pattern adapter."""

from __future__ import annotations

from app.agent.patterns.base import AgentPattern, PatternState


class PlanExecutePattern(AgentPattern):
    @property
    def pattern_id(self) -> str:
        return "plan_execute"

    @property
    def state(self) -> PatternState:
        return PatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return (
            "Plan-and-Execute: upfront planning then sequential execution"
            " — implemented in app.agent.graph:AgentGraph"
        )

    @property
    def node_name(self) -> str:
        return "plan"
