"""TransformStepNode — pure data mapping using ContextResolver."""
from __future__ import annotations

from typing import Any

from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowState


class TransformStepNode:
    def __init__(
        self, step: StepDefinition, context_resolver: ContextResolver, **services: Any
    ) -> None:
        self.step = step
        self.ctx = context_resolver

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        output = self.ctx.resolve_dict(self.step.input, state)
        return {"step_outputs": {**(state.get("step_outputs") or {}), self.step.id: output}}
