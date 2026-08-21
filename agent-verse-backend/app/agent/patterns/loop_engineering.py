"""Loop-Engineering pattern adapter."""

from __future__ import annotations

from app.agent.patterns.base import AgentPattern, PatternState


class LoopEngineeringPattern(AgentPattern):
    @property
    def pattern_id(self) -> str:
        return "loop_engineering"

    @property
    def state(self) -> PatternState:
        return PatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return (
            "Loop-Engineering: adaptive retry + loop control"
            " — implemented in app.agent.graph:_execute_step_with_loop"
        )

    @property
    def node_name(self) -> str:
        return "_execute_step_with_loop"
