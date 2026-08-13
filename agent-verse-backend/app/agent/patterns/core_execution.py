"""Canonical production adapters for the core single-agent strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent.structured_plan import StructuredPlan
from app.orchestration.graph_factory import GraphFactory
from app.orchestration.strategy_adapters import ExecutionTier


@dataclass(frozen=True, slots=True)
class ReActStrategyAdapter:
    strategy_id: str = "react"
    execution_tier: ExecutionTier = ExecutionTier.LOCAL

    def create_runtime(self, **kwargs: Any) -> Any:
        runtime_profile = kwargs.pop("runtime_profile", None)
        services = kwargs.pop("services", kwargs)
        return GraphFactory().create(runtime_profile, services)


@dataclass(frozen=True, slots=True)
class PlanExecuteStrategyAdapter:
    strategy_id: str = "plan_execute"
    execution_tier: ExecutionTier = ExecutionTier.LOCAL

    def create_runtime(self, **kwargs: Any) -> Any:
        structured_plan = kwargs.pop("structured_plan", None)
        if not isinstance(structured_plan, StructuredPlan):
            raise TypeError("plan_execute requires a StructuredPlan")
        structured_plan.validate()
        runtime_profile = kwargs.pop("runtime_profile", None)
        services = kwargs.pop("services", kwargs)
        runtime: Any = GraphFactory().create(runtime_profile, services)
        runtime._structured_plan = structured_plan
        return runtime


__all__ = ["PlanExecuteStrategyAdapter", "ReActStrategyAdapter"]
