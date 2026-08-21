"""SubWorkflowStepNode — calls another workflow as a step."""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowState

_log = get_logger(__name__)


class SubWorkflowStepNode:
    def __init__(
        self, step: StepDefinition, context_resolver: ContextResolver, **services: Any
    ) -> None:
        self.step = step
        self.ctx = context_resolver
        self.workflow_runner = services.get("workflow_runner")

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        sub_inputs = self.ctx.resolve_dict(self.step.workflow_inputs or self.step.input, state)
        workflow_id = str(self.ctx.resolve(self.step.workflow_id, state))

        if self.workflow_runner is None or state.get("is_test_run"):
            output = {"_sub_workflow": workflow_id, "inputs": sub_inputs, "_mock": True}
        else:
            run_id = await self.workflow_runner.run(
                workflow_id=workflow_id,
                tenant_id=state.get("tenant_id", ""),
                inputs=sub_inputs,
                trigger_type="sub_workflow",
                wait_for_completion=True,
            )
            output = {"run_id": run_id, "workflow_id": workflow_id}

        return {"step_outputs": {**(state.get("step_outputs") or {}), self.step.id: output}}
