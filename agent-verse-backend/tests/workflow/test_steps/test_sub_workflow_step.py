"""Tests for SubWorkflowStepNode — calls another workflow as a step."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowRunStatus
from app.workflow.steps.sub_workflow_step import SubWorkflowStepNode


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


@pytest.mark.asyncio
async def test_sub_workflow_step_no_runner_returns_mock_output() -> None:
    step = StepDefinition(
        id="sub1", type="sub_workflow", workflow_id="child-wf",
        workflow_inputs={"x": 1},
    )
    node = SubWorkflowStepNode(step, _ctx())
    result = await node.execute(_state())  # type: ignore[arg-type]
    out = result["step_outputs"]["sub1"]
    assert out["_sub_workflow"] == "child-wf"
    assert out["inputs"] == {"x": 1}
    assert out["_mock"] is True


@pytest.mark.asyncio
async def test_sub_workflow_step_test_run_returns_mock_even_with_runner() -> None:
    """is_test_run must force the mock branch even when a real runner is wired,
    so dry-run workflow validation never triggers a real sub-workflow execution."""
    runner = AsyncMock()
    step = StepDefinition(id="sub1", type="sub_workflow", workflow_id="child-wf")
    node = SubWorkflowStepNode(step, _ctx(), workflow_runner=runner)
    state = _state(is_test_run=True)
    result = await node.execute(state)  # type: ignore[arg-type]
    assert result["step_outputs"]["sub1"]["_mock"] is True
    runner.run.assert_not_called()


@pytest.mark.asyncio
async def test_sub_workflow_step_calls_real_runner() -> None:
    runner = AsyncMock()
    runner.run.return_value = "run-child-1"
    step = StepDefinition(
        id="sub1", type="sub_workflow", workflow_id="child-wf",
        workflow_inputs={"y": 2},
    )
    node = SubWorkflowStepNode(step, _ctx(), workflow_runner=runner)
    result = await node.execute(_state(tenant_id="t-1"))  # type: ignore[arg-type]

    out = result["step_outputs"]["sub1"]
    assert out == {"run_id": "run-child-1", "workflow_id": "child-wf"}
    runner.run.assert_awaited_once_with(
        workflow_id="child-wf",
        tenant_id="t-1",
        inputs={"y": 2},
        trigger_type="sub_workflow",
        wait_for_completion=True,
    )


@pytest.mark.asyncio
async def test_sub_workflow_step_resolves_templated_workflow_id_and_inputs() -> None:
    runner = AsyncMock()
    runner.run.return_value = "run-child-2"
    step = StepDefinition(
        id="sub1", type="sub_workflow",
        workflow_id="{{inputs.child_id}}",
        workflow_inputs={"amount": "{{inputs.amount}}"},
    )
    node = SubWorkflowStepNode(step, _ctx(), workflow_runner=runner)
    state = _state(inputs={"child_id": "wf-42", "amount": 99})
    result = await node.execute(state)  # type: ignore[arg-type]

    assert result["step_outputs"]["sub1"]["workflow_id"] == "wf-42"
    runner.run.assert_awaited_once_with(
        workflow_id="wf-42",
        tenant_id="t-1",
        inputs={"amount": 99},
        trigger_type="sub_workflow",
        wait_for_completion=True,
    )


@pytest.mark.asyncio
async def test_sub_workflow_step_falls_back_to_input_when_no_workflow_inputs() -> None:
    """When ``workflow_inputs`` is empty, the step's generic ``input`` dict is
    used instead (``self.step.workflow_inputs or self.step.input``)."""
    runner = AsyncMock()
    runner.run.return_value = "run-child-3"
    step = StepDefinition(
        id="sub1", type="sub_workflow", workflow_id="child-wf",
        input={"z": 3},
    )
    node = SubWorkflowStepNode(step, _ctx(), workflow_runner=runner)
    await node.execute(_state())  # type: ignore[arg-type]
    runner.run.assert_awaited_once_with(
        workflow_id="child-wf",
        tenant_id="t-1",
        inputs={"z": 3},
        trigger_type="sub_workflow",
        wait_for_completion=True,
    )


@pytest.mark.asyncio
async def test_sub_workflow_step_preserves_existing_step_outputs() -> None:
    step = StepDefinition(id="sub1", type="sub_workflow", workflow_id="child-wf")
    node = SubWorkflowStepNode(step, _ctx())
    state = _state(step_outputs={"prior": {"a": 1}})
    result = await node.execute(state)  # type: ignore[arg-type]
    assert result["step_outputs"]["prior"] == {"a": 1}
    assert "sub1" in result["step_outputs"]
