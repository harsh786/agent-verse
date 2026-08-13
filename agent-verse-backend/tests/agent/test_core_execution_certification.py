from __future__ import annotations

import pytest

from app.agent.patterns.core_execution import (
    PlanExecuteStrategyAdapter,
    ReActStrategyAdapter,
)
from app.agent.structured_plan import PlanValidationError, StructuredPlan, StructuredStep
from app.orchestration.strategy_registry import build_default_registry


def test_react_and_plan_execute_have_executable_adapter_paths() -> None:
    registry = build_default_registry()

    react = registry.get("react")
    plan_execute = registry.get("plan_execute")
    assert react.adapter_path.endswith("ReActStrategyAdapter")
    assert plan_execute.adapter_path.endswith("PlanExecuteStrategyAdapter")
    assert react.adapter_version == plan_execute.adapter_version == "1.0.0"
    assert isinstance(react.adapter_descriptor.create_adapter(), ReActStrategyAdapter)
    assert isinstance(
        plan_execute.adapter_descriptor.create_adapter(), PlanExecuteStrategyAdapter
    )


def test_loop_engineering_is_a_bundle_not_an_adapter() -> None:
    capability = build_default_registry().get("loop_engineering")

    assert capability.adapter_descriptor is None
    assert capability.bundle_components == ("loop_until", "wave_execution")


def test_plan_execute_adapter_rejects_invalid_structured_plan_before_graph() -> None:
    plan = StructuredPlan(
        [
            StructuredStep(id="a", description="first", depends_on=["b"]),
            StructuredStep(id="b", description="second", depends_on=["a"]),
        ]
    )

    with pytest.raises(PlanValidationError, match="cycle"):
        PlanExecuteStrategyAdapter().create_runtime(structured_plan=plan)
