"""SetVariableStepNode — writes a mutable workflow variable."""
from __future__ import annotations

from typing import Any

from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowState
from app.workflow.variables import WorkflowVariableStore

_store = WorkflowVariableStore()


class SetVariableStepNode:
    def __init__(
        self, step: StepDefinition, context_resolver: ContextResolver, **services: Any
    ) -> None:
        self.step = step
        self.ctx = context_resolver

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        var_name = str(self.ctx.resolve(self.step.var_name, state))
        raw_value = self.ctx.resolve(self.step.var_value, state)

        # Optional type coercion
        vt = self.step.value_type
        if vt == "number":
            value = float(raw_value) if raw_value is not None else 0.0
        elif vt == "boolean":
            value = bool(raw_value)
        elif vt == "integer":
            value = int(float(str(raw_value))) if raw_value is not None else 0
        else:
            value = raw_value

        updates = _store.set(state, var_name, value)
        return {
            **updates,
            "step_outputs": {
                **(state.get("step_outputs") or {}),
                self.step.id: {"variable": var_name, "value": value},
            },
        }
