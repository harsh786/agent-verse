"""ForeachStepNode — iterates over a list, runs body steps per item."""

from __future__ import annotations

import asyncio
from typing import Any

from app.observability.logging import get_logger
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowState

_log = get_logger(__name__)


class ForeachStepNode:
    def __init__(
        self,
        step: StepDefinition,
        context_resolver: ContextResolver,
        **services: Any,
    ) -> None:
        self.step = step
        self.ctx = context_resolver
        self.services = services

    async def execute(self, state: WorkflowState) -> dict[str, Any]:

        # Resolve the list to iterate over
        items = self.ctx.resolve(self.step.iterate_over, state)
        if not isinstance(items, list):
            items = list(items) if items else []

        total = len(items)
        var_name = self.step.as_var or "item"
        max_conc = max(1, self.step.max_concurrency or 5)

        collected: list[Any] = []
        failed_count = 0
        current_idx = 0

        # Process in batches of max_concurrency
        for batch_start in range(0, total, max_conc):
            batch = items[batch_start : batch_start + max_conc]
            batch_tasks = []

            for i, item in enumerate(batch):
                idx = batch_start + i
                # Inject foreach context into state copy
                foreach_ctx = {
                    var_name: item,
                    "index": idx,
                    "total": total,
                }
                item_state: dict[str, Any] = {
                    **state,
                    "_foreach_ctx": foreach_ctx,
                }
                batch_tasks.append(self._run_body(item_state))

            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
            current_idx += len(batch)

            for _item, result in zip(batch, batch_results, strict=False):
                if isinstance(result, Exception):
                    failed_count += 1
                    _log.warning(
                        "foreach_item_failed",
                        step_id=self.step.id,
                        error=str(result),
                    )
                    if self.step.on_item_failure == "abort":
                        raise RuntimeError(f"foreach step {self.step.id!r} aborted: {result}")
                    collected.append({"_error": str(result)})
                else:
                    # Extract the last body step's output
                    r: dict[str, Any] = result  # type: ignore[assignment]
                    body_outputs = (r or {}).get("step_outputs", {})
                    last_step_id = self.step.body[-1].id if self.step.body else ""
                    collected.append(body_outputs.get(last_step_id, {}))

            # Progress is returned in the final state update

        output_key = self.step.collect_output_as or f"{self.step.id}_results"
        output = {output_key: collected, "_total": total, "_failed": failed_count}

        foreach_progress = dict(state.get("foreach_progress") or {})
        foreach_progress[self.step.id] = {
            "current": total,
            "total": total,
            "failed": failed_count,
        }

        return {
            "step_outputs": {**(state.get("step_outputs") or {}), self.step.id: output},
            "foreach_progress": foreach_progress,
        }

    async def _run_body(self, item_state: dict[str, Any]) -> dict[str, Any]:
        """Run all body steps for one iteration, passing outputs forward."""
        from app.workflow.registry import StepTypeRegistry

        current_state: dict[str, Any] = dict(item_state)
        for body_step in self.step.body:
            node_class = StepTypeRegistry.get(body_step.type)
            node = node_class(body_step, self.ctx, **self.services)
            updates = await node.execute(current_state)  # type: ignore[arg-type]
            current_state.update(updates)

        return current_state
