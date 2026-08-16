"""ConditionalStepNode — evaluates expressions to choose next branch."""
from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.expression_engine import ExpressionEngine
from app.workflow.state import WorkflowState

_log = get_logger(__name__)
_engine = ExpressionEngine()


class ConditionalStepNode:
    def __init__(
        self,
        step: StepDefinition,
        context_resolver: ContextResolver,
        **services: Any,
    ) -> None:
        self.step = step
        self.ctx = context_resolver

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        """Evaluate branches in order; first match wins. Default = last branch."""
        chosen_next: str | None = None

        for branch in self.step.branches:
            if branch.condition == "default":
                chosen_next = branch.next
                continue  # default is fallthrough — only used if no other branch matched

            # Resolve {{...}} in the condition expression first
            resolved_condition = str(
                self.ctx.resolve(branch.condition, state)
            )

            try:
                if _engine.evaluate(resolved_condition):
                    chosen_next = branch.next
                    break
            except Exception as exc:
                _log.warning(
                    "conditional_branch_eval_error",
                    step_id=self.step.id,
                    condition=branch.condition,
                    error=str(exc),
                )

        _log.info(
            "conditional_branch_taken",
            step_id=self.step.id,
            next=chosen_next,
        )

        return {
            "step_outputs": {
                **(state.get("step_outputs") or {}),
                self.step.id: {"chosen_branch": chosen_next},
            },
            "completed_branch": chosen_next,
            "_conditional_next": chosen_next,  # read by compiler router
        }
