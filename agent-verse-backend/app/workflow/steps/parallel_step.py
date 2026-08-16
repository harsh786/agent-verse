"""ParallelStepNode — runs sub-branches concurrently via asyncio.gather."""
from __future__ import annotations

import asyncio
from typing import Any

from app.observability.logging import get_logger
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowState

_log = get_logger(__name__)


class ParallelStepNode:
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
        """Execute all parallel_branches concurrently and merge results."""
        from app.workflow.registry import StepTypeRegistry

        if not self.step.parallel_branches:
            return {}

        tasks = []
        for branch in self.step.parallel_branches:
            node_class = StepTypeRegistry.get(branch.type)
            node = node_class(branch, self.ctx, **self.services)
            tasks.append(node.execute(state))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        merged_outputs: dict[str, Any] = dict(state.get("step_outputs") or {})
        merged_timings: dict[str, Any] = dict(state.get("step_timings") or {})
        total_cost = state.get("cost_usd") or 0.0
        total_tokens = state.get("tokens_used") or 0

        parallel_output: dict[str, Any] = {}

        for branch, result in zip(self.step.parallel_branches, results, strict=False):
            if isinstance(result, Exception):
                _log.error(
                    "parallel_branch_failed",
                    branch_id=branch.id,
                    error=str(result),
                )
                parallel_output[branch.id] = {"_error": str(result)}
            else:
                r: dict[str, Any] = result  # type: ignore[assignment]
                branch_outputs = (r or {}).get("step_outputs", {})
                merged_outputs.update(branch_outputs)
                branch_out = branch_outputs.get(branch.id, {})
                parallel_output[branch.id] = branch_out
                merged_timings.update((r or {}).get("step_timings", {}))
                total_cost += (r or {}).get("cost_usd", 0.0)
                total_tokens += (r or {}).get("tokens_used", 0)

        # Also store the aggregated output under the parallel step's own ID
        merged_outputs[self.step.id] = parallel_output

        return {
            "step_outputs": merged_outputs,
            "step_timings": merged_timings,
            "cost_usd": total_cost,
            "tokens_used": total_tokens,
        }
