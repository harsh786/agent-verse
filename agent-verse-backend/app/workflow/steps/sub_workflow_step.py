"""SubWorkflowStepNode — calls another workflow as a step.

Old bug: neither the API nor the worker gave the compiler a ``workflow_runner``,
so every real run took the mock branch and reported
``{"_mock": True}`` as a successful step without running the child.

Now (real runs):

* Durable (runner + persistent run store): create and dispatch ONE child run —
  keyed by an idempotency key derived from (parent run, step), so a
  re-dispatched parent never starts a second child — then suspend the parent as
  ``waiting_timer`` and poll the child's status on each beat wake (~30 s). The
  step completes with the child's outputs, or FAILS if the child failed.
* Runner without a run store (no-DB dev): run the child inline.
* No runner: FAIL with a clear error. Only ``is_test_run`` simulates.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.observability.logging import get_logger
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowRunStatus, WorkflowState

_log = get_logger(__name__)

# How often a suspended parent re-checks its child (beat granularity is ~30 s).
_CHILD_POLL_S = 30.0
_CHILD_FAILED = {
    WorkflowRunStatus.FAILED.value,
    WorkflowRunStatus.CANCELLED.value,
    "timed_out",
}


class SubWorkflowStepNode:
    def __init__(
        self, step: StepDefinition, context_resolver: ContextResolver, **services: Any
    ) -> None:
        self.step = step
        self.ctx = context_resolver
        self.workflow_runner = services.get("workflow_runner")
        self.run_store = services.get("run_store")
        self.durable = bool(services.get("durable_timer_waits"))

    def _can_suspend(self, state: WorkflowState) -> bool:
        rs = self.run_store
        return bool(
            self.durable
            and rs is not None
            and hasattr(rs, "set_timer_wait")
            and hasattr(rs, "get")
            and getattr(self.workflow_runner, "_run_store", None) is not None
            and state.get("run_id")
            and state.get("tenant_id")
        )

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        sub_inputs = self.ctx.resolve_dict(self.step.workflow_inputs or self.step.input, state)
        workflow_id = str(self.ctx.resolve(self.step.workflow_id, state))
        prior = state.get("step_outputs") or {}

        if state.get("is_test_run"):
            output: dict[str, Any] = {
                "_sub_workflow": workflow_id, "inputs": sub_inputs, "_mock": True,
            }
            return {"step_outputs": {**prior, self.step.id: output}}

        if self.workflow_runner is None:
            raise RuntimeError(
                f"sub_workflow step {self.step.id!r} cannot run child workflow "
                f"{workflow_id!r}: no workflow runner is configured for the engine"
            )
        tenant_id = str(state.get("tenant_id") or "")

        if not self._can_suspend(state):
            run_id = await self.workflow_runner.run(
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                inputs=sub_inputs,
                trigger_type="sub_workflow",
                wait_for_completion=True,
            )
            output = {"run_id": run_id, "workflow_id": workflow_id, "inline": True}
            return {"step_outputs": {**prior, self.step.id: output}}

        parent_run_id = str(state["run_id"])
        child_id = await self.workflow_runner.run(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            inputs=sub_inputs,
            trigger_type="sub_workflow",
            run_metadata={"parent_run_id": parent_run_id, "parent_step_id": self.step.id},
            # Same key on every re-execution of this step -> the SAME child run.
            idempotency_key=f"sub:{parent_run_id}:{self.step.id}",
        )
        child = await self.run_store.get(tenant_id, str(child_id)) or {}
        child_status = str(child.get("status") or "")
        if child_status == WorkflowRunStatus.COMPLETE.value:
            output = {
                "run_id": str(child_id),
                "workflow_id": workflow_id,
                "status": child_status,
                "outputs": child.get("outputs") or {},
            }
            return {"step_outputs": {**prior, self.step.id: output}}
        if child_status in _CHILD_FAILED:
            raise RuntimeError(
                f"sub_workflow {workflow_id!r} run {child_id} ended {child_status}: "
                f"{child.get('error') or 'no error message'}"
            )
        # Still pending/running/waiting: suspend durably and re-check later.
        wake_at = datetime.now(UTC) + timedelta(seconds=_CHILD_POLL_S)
        await self.run_store.set_timer_wait(tenant_id, parent_run_id, self.step.id, wake_at)
        return {
            "status": WorkflowRunStatus.WAITING_TIMER,
            "paused_by": f"wait_timer:{self.step.id}",
            "step_outputs": {
                **prior,
                self.step.id: {
                    "waiting": True,
                    "run_id": str(child_id),
                    "workflow_id": workflow_id,
                    "child_status": child_status or "pending",
                },
            },
        }
