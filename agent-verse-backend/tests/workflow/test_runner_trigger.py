"""WT-7/P0-6: WorkflowRunner.trigger maps the router contract onto run()."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.workflow.runner import WorkflowRunner, WorkflowValidationError


async def test_trigger_delegates_to_run_and_shapes_response():
    """FAILS TODAY: WorkflowRunner has no trigger() -> router raises AttributeError -> 500."""
    runner = WorkflowRunner.__new__(WorkflowRunner)
    runner._run_store = None
    runner.run = AsyncMock(return_value="run-123")

    out = await runner.trigger(
        workflow_id="wf1", tenant_id="t1", inputs={"a": 1}, dry_run=True
    )

    assert out["run_id"] == "run-123"
    assert out["workflow_id"] == "wf1"
    assert out["status"] == "pending"
    assert runner.run.await_args.kwargs["is_test_run"] is True


async def test_trigger_returns_stored_run_when_available():
    runner = WorkflowRunner.__new__(WorkflowRunner)
    runner._run_store = AsyncMock()
    runner._run_store.get = AsyncMock(
        return_value={"run_id": "run-9", "workflow_id": "wf1", "status": "running"}
    )
    runner.run = AsyncMock(return_value="run-9")

    out = await runner.trigger(workflow_id="wf1", tenant_id="t1", inputs={})
    assert out["status"] == "running"


async def test_trigger_propagates_validation_error():
    runner = WorkflowRunner.__new__(WorkflowRunner)
    runner._run_store = None
    runner.run = AsyncMock(side_effect=WorkflowValidationError("bad inputs"))
    with pytest.raises(WorkflowValidationError):
        await runner.trigger(workflow_id="wf1", tenant_id="t1", inputs={})
