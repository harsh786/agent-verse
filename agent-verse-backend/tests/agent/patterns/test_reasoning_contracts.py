from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from app.agent.patterns.reasoning_contracts import (
    CompiledTask,
    ReasoningContractError,
    SearchNodeState,
    SubproblemState,
    ThoughtEdgeState,
    ThoughtNodeState,
    ToolPlanStep,
    validate_compiled_tasks,
    validate_subproblems,
    validate_thought_graph,
    validate_tool_plan,
)
from app.orchestration.strategy_registry import LOCAL_REASONING_LIMITS, build_default_registry


def _subproblem(identifier: str, dependencies: tuple[str, ...] = ()) -> SubproblemState:
    return SubproblemState(
        subproblem_id=identifier,
        question=identifier,
        depends_on=dependencies,
        status="pending",
        answer_ref=None,
    )


@pytest.mark.parametrize("score", [-0.1, 1.1, math.nan, math.inf])
def test_thought_node_rejects_invalid_scores(score: float) -> None:
    with pytest.raises(ValidationError):
        ThoughtNodeState(
            node_id="n1",
            content_ref="artifact://n1",
            safe_summary="summary",
            score=score,
            depth=0,
            status="candidate",
            parent_ids=(),
        )


@pytest.mark.parametrize(
    "items",
    [
        (_subproblem("a"), _subproblem("a")),
        (_subproblem("a", ("missing",)),),
        (_subproblem("a", ("b",)), _subproblem("b", ("a",))),
    ],
)
def test_subproblem_validation_rejects_duplicate_unknown_and_cycles(
    items: tuple[SubproblemState, ...]
) -> None:
    with pytest.raises(ReasoningContractError):
        validate_subproblems(items)


def test_tool_plan_rejects_duplicate_output_variables() -> None:
    steps = tuple(
        ToolPlanStep(
            step_id=identifier,
            tool_name="tool",
            arguments={},
            depends_on=(),
            output_variable="same",
        )
        for identifier in ("one", "two")
    )
    with pytest.raises(ReasoningContractError, match="duplicate output"):
        validate_tool_plan(steps)


def test_thought_graph_rejects_unknown_edges_and_cycles() -> None:
    nodes = tuple(
        ThoughtNodeState(
            node_id=identifier,
            content_ref=f"artifact://{identifier}",
            safe_summary="summary",
            score=0.8,
            depth=index,
            status="candidate",
            parent_ids=(),
        )
        for index, identifier in enumerate(("one", "two"))
    )
    with pytest.raises(ReasoningContractError, match="unknown"):
        validate_thought_graph(
            nodes, (ThoughtEdgeState(source_id="one", target_id="missing", relation="expands"),)
        )
    with pytest.raises(ReasoningContractError, match="cycle"):
        validate_thought_graph(
            nodes,
            (
                ThoughtEdgeState(source_id="one", target_id="two", relation="expands"),
                ThoughtEdgeState(source_id="two", target_id="one", relation="refines"),
            ),
        )


def test_compiled_tasks_are_topologically_ordered() -> None:
    second = CompiledTask(
        task_id="second",
        tool_name="tool",
        arguments={},
        depends_on=("first",),
        output_schema={},
        status="pending",
    )
    first = second.model_copy(update={"task_id": "first", "depends_on": ()})
    assert [item.task_id for item in validate_compiled_tasks((second, first))] == [
        "first",
        "second",
    ]


def test_search_node_rejects_non_finite_value() -> None:
    with pytest.raises(ValidationError):
        SearchNodeState(
            node_id="root",
            parent_id=None,
            action_ref="action://root",
            observation_ref=None,
            visits=0,
            value_sum=math.nan,
            depth=0,
            is_terminal=False,
        )


def test_registry_has_exact_limits_and_executable_adapters() -> None:
    registry = build_default_registry()
    assert set(LOCAL_REASONING_LIMITS) == {
        "constitutional_ai",
        "few_shot_cot",
        "graph_of_thoughts",
        "least_to_most",
        "rewoo",
        "lats",
        "llm_compiler",
    }
    for strategy_id, limits in LOCAL_REASONING_LIMITS.items():
        capability = registry.resolve(strategy_id).capability
        assert capability.default_limits == limits
        assert capability.adapter_version == "1.0.0"
        assert capability.state_schema_version == 1
        assert registry.resolve_adapter(strategy_id).is_executable
