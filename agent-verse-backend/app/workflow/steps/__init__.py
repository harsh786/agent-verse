"""BaseStepNode — the public protocol for workflow step implementations.

Any class that satisfies this Protocol can be registered as a step type:

    class MyCustomNode:
        def __init__(
            self,
            step: StepDefinition,
            context_resolver: ContextResolver,
            **services: Any,
        ) -> None: ...

        async def execute(self, state: WorkflowState) -> dict[str, Any]: ...

The execute() method receives the current WorkflowState and must return
a dict of keys to merge back into the state (LangGraph reducer pattern).

Minimum required return:
    {"step_outputs": {step_id: output_dict}}

Optional state updates:
    {"cost_usd": float, "tokens_used": int, "status": ..., "vars": {...}}
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowState


@runtime_checkable
class BaseStepNode(Protocol):
    """Structural protocol — no inheritance required."""

    def __init__(
        self,
        step: StepDefinition,
        context_resolver: Any,  # ContextResolver
        **services: Any,
    ) -> None: ...

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        """Execute the step and return state updates."""
        ...
