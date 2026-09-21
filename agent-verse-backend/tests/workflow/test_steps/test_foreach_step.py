"""Tests for ForeachStepNode — iterates a list, runs body steps per item."""

from __future__ import annotations

import pytest

from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowRunStatus
from app.workflow.steps.foreach_step import ForeachStepNode


def _ctx() -> ContextResolver:
    return ContextResolver()


def _state(**kwargs) -> dict:
    defaults = {
        "run_id": "run-1", "workflow_id": "wf-1", "tenant_id": "t-1",
        "inputs": {}, "step_outputs": {}, "vars": {},
        "status": WorkflowRunStatus.RUNNING,
    }
    defaults.update(kwargs)
    return defaults


def _code_body(code: str, step_id: str = "body1") -> StepDefinition:
    return StepDefinition(id=step_id, type="code", runtime="python", code=code)


@pytest.mark.asyncio
async def test_foreach_empty_items_when_expression_unresolved() -> None:
    step = StepDefinition(id="fe", type="foreach", iterate_over="{{inputs.missing}}")
    node = ForeachStepNode(step, _ctx())
    result = await node.execute(_state())  # type: ignore[arg-type]
    out = result["step_outputs"]["fe"]
    assert out == {"fe_results": [], "_total": 0, "_failed": 0}
    assert result["foreach_progress"]["fe"] == {"current": 0, "total": 0, "failed": 0}


@pytest.mark.asyncio
async def test_foreach_non_list_iterable_is_coerced() -> None:
    """A resolved dict (not a list) is coerced via list(); iterating a dict yields
    its keys, exercising the ``if not isinstance(items, list)`` branch."""
    step = StepDefinition(id="fe", type="foreach", iterate_over="{{inputs.data}}")
    node = ForeachStepNode(step, _ctx())
    state = _state(inputs={"data": {"a": 1, "b": 2}})
    result = await node.execute(state)  # type: ignore[arg-type]
    out = result["step_outputs"]["fe"]
    assert out["_total"] == 2
    assert len(out["fe_results"]) == 2


@pytest.mark.asyncio
async def test_foreach_basic_iteration_collects_body_output() -> None:
    step = StepDefinition(
        id="fe",
        type="foreach",
        iterate_over="{{inputs.items}}",
        body=[_code_body("output = {'v': {{foreach.item}} * 2}")],
    )
    node = ForeachStepNode(step, _ctx())
    state = _state(inputs={"items": [1, 2, 3]})
    result = await node.execute(state)  # type: ignore[arg-type]
    out = result["step_outputs"]["fe"]
    assert out["fe_results"] == [{"v": 2}, {"v": 4}, {"v": 6}]
    assert out["_total"] == 3
    assert out["_failed"] == 0


@pytest.mark.asyncio
async def test_foreach_custom_collect_output_as_key() -> None:
    step = StepDefinition(
        id="fe",
        type="foreach",
        iterate_over="{{inputs.items}}",
        collect_output_as="doubled",
        body=[_code_body("output = {'v': {{foreach.item}} * 2}")],
    )
    node = ForeachStepNode(step, _ctx())
    state = _state(inputs={"items": [1, 2]})
    result = await node.execute(state)  # type: ignore[arg-type]
    out = result["step_outputs"]["fe"]
    assert "doubled" in out
    assert out["doubled"] == [{"v": 2}, {"v": 4}]


@pytest.mark.asyncio
async def test_foreach_custom_as_var_name() -> None:
    step = StepDefinition(
        id="fe",
        type="foreach",
        iterate_over="{{inputs.items}}",
        as_var="x",
        body=[_code_body("output = {'v': {{foreach.x}} * 3}")],
    )
    node = ForeachStepNode(step, _ctx())
    state = _state(inputs={"items": [1, 2]})
    result = await node.execute(state)  # type: ignore[arg-type]
    assert result["step_outputs"]["fe"]["fe_results"] == [{"v": 3}, {"v": 6}]


@pytest.mark.asyncio
async def test_foreach_on_item_failure_continue_collects_error() -> None:
    step = StepDefinition(
        id="fe",
        type="foreach",
        iterate_over="{{inputs.items}}",
        on_item_failure="continue",
        body=[_code_body("output = {'v': 10 / {{foreach.item}}}")],
    )
    node = ForeachStepNode(step, _ctx())
    state = _state(inputs={"items": [2, 0, 5]})
    result = await node.execute(state)  # type: ignore[arg-type]
    out = result["step_outputs"]["fe"]
    assert out["_total"] == 3
    assert out["_failed"] == 1
    assert out["fe_results"][0] == {"v": 5.0}
    assert "_error" in out["fe_results"][1]
    assert out["fe_results"][2] == {"v": 2.0}


@pytest.mark.asyncio
async def test_foreach_on_item_failure_abort_raises() -> None:
    step = StepDefinition(
        id="fe",
        type="foreach",
        iterate_over="{{inputs.items}}",
        on_item_failure="abort",
        body=[_code_body("output = {'v': 10 / {{foreach.item}}}")],
    )
    node = ForeachStepNode(step, _ctx())
    state = _state(inputs={"items": [2, 0, 5]})
    with pytest.raises(RuntimeError, match=r"fe.*aborted"):
        await node.execute(state)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_foreach_batches_by_max_concurrency() -> None:
    step = StepDefinition(
        id="fe",
        type="foreach",
        iterate_over="{{inputs.items}}",
        max_concurrency=2,
        body=[_code_body("output = {'v': {{foreach.item}}}")],
    )
    node = ForeachStepNode(step, _ctx())
    state = _state(inputs={"items": [1, 2, 3, 4, 5]})
    result = await node.execute(state)  # type: ignore[arg-type]
    out = result["step_outputs"]["fe"]
    assert out["_total"] == 5
    assert [r["v"] for r in out["fe_results"]] == [1, 2, 3, 4, 5]
    assert result["foreach_progress"]["fe"] == {"current": 5, "total": 5, "failed": 0}


@pytest.mark.asyncio
async def test_foreach_empty_body_yields_empty_dicts() -> None:
    step = StepDefinition(id="fe", type="foreach", iterate_over="{{inputs.items}}", body=[])
    node = ForeachStepNode(step, _ctx())
    state = _state(inputs={"items": ["a", "b"]})
    result = await node.execute(state)  # type: ignore[arg-type]
    assert result["step_outputs"]["fe"]["fe_results"] == [{}, {}]


@pytest.mark.asyncio
async def test_foreach_preserves_existing_step_outputs_and_progress() -> None:
    step = StepDefinition(
        id="fe", type="foreach", iterate_over="{{inputs.items}}", body=[]
    )
    node = ForeachStepNode(step, _ctx())
    state = _state(
        inputs={"items": [1]},
        step_outputs={"prior": {"x": 1}},
        foreach_progress={"other": {"current": 1, "total": 1, "failed": 0}},
    )
    result = await node.execute(state)  # type: ignore[arg-type]
    assert result["step_outputs"]["prior"] == {"x": 1}
    assert "fe" in result["step_outputs"]
    assert "other" in result["foreach_progress"]
    assert "fe" in result["foreach_progress"]
