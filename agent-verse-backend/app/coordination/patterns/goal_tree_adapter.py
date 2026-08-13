"""Durable Goal Tree adapter using the canonical dependency-wave runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.coordination.patterns.supervisor_adapter import DurableSupervisorRuntime
from app.orchestration.strategy_adapters import ExecutionTier


@dataclass(frozen=True, slots=True)
class DurableGoalTreeAdapter:
    strategy_id: str = "goal_tree"
    execution_tier: ExecutionTier = ExecutionTier.DISTRIBUTED

    def create_runtime(self, **kwargs: Any) -> DurableGoalTreeRuntime:
        return DurableGoalTreeRuntime(**kwargs)


class DurableGoalTreeRuntime(DurableSupervisorRuntime):
    """Same canonical DAG executor, with Goal Tree's strategy identity."""


__all__ = ["DurableGoalTreeAdapter", "DurableGoalTreeRuntime"]
