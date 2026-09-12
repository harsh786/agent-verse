"""Cooperative run-control: the engine honors an operator cancel/pause set via the
API at step boundaries, and a resumed run skips already-completed steps."""

from __future__ import annotations

from typing import Any

import pytest

from app.workflow.compiler import WorkflowCompiler
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition, WorkflowDefinition
from app.workflow.state import StepStatus, WorkflowCancelled, WorkflowPaused


class _FakeRunStore:
    """Minimal run store exposing just what node_fn's control check calls."""

    def __init__(self, status: str | None = None, prior: dict[str, Any] | None = None) -> None:
        self._status = status
        self._prior = prior

    async def get_status(self, tenant_id: str, run_id: str) -> str | None:
        return self._status

    async def get_step_result(
        self, tenant_id: str, run_id: str, step_id: str
    ) -> dict[str, Any] | None:
        return self._prior


def _wf() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="ctl",
        steps=[
            StepDefinition(id="a", type="transform", input={"v": 1}),
            StepDefinition(id="b", type="transform", input={"v": 2}, depends_on=["a"]),
        ],
    )


def _state() -> dict[str, Any]:
    return {
        "run_id": "run-1",
        "tenant_id": "t-1",
        "is_test_run": False,
        "inputs": {},
        "step_outputs": {},
        "step_timings": {},
        "vars": {},
    }


def _cfg() -> dict:
    return {"configurable": {"thread_id": "run-1"}}


@pytest.mark.asyncio
async def test_cancelled_status_halts_run() -> None:
    compiler = WorkflowCompiler(ContextResolver(), run_store=_FakeRunStore(status="cancelled"))
    compiled = compiler.compile(_wf())
    with pytest.raises(WorkflowCancelled):
        await compiled.ainvoke(_state(), _cfg())


@pytest.mark.asyncio
async def test_paused_status_halts_run() -> None:
    compiler = WorkflowCompiler(ContextResolver(), run_store=_FakeRunStore(status="paused"))
    compiled = compiler.compile(_wf())
    with pytest.raises(WorkflowPaused):
        await compiled.ainvoke(_state(), _cfg())


@pytest.mark.asyncio
async def test_completed_step_is_skipped_on_resume() -> None:
    """A step already persisted COMPLETE returns its stored output without re-running
    (so a resumed run continues instead of redoing work)."""
    prior = {"step_id": "a", "status": StepStatus.COMPLETE.value, "output": {"restored": True}}
    compiler = WorkflowCompiler(
        ContextResolver(), run_store=_FakeRunStore(status="running", prior=prior)
    )
    compiled = compiler.compile(_wf())
    final = await compiled.ainvoke(_state(), _cfg())
    # Step 'a' output is the persisted one, not a fresh transform of {"v": 1}.
    assert final["step_outputs"]["a"] == {"restored": True}
