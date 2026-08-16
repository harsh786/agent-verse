"""ToolStepNode — executes any registered MCP tool."""
from __future__ import annotations

import time
from typing import Any

from app.observability.logging import get_logger
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowState

_log = get_logger(__name__)


class ToolStepNode:
    def __init__(
        self,
        step: StepDefinition,
        context_resolver: ContextResolver,
        **services: Any,
    ) -> None:
        self.step = step
        self.ctx = context_resolver
        self.mcp_client = services.get("mcp_client")

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        resolved_input = self.ctx.resolve_dict(self.step.input, state)

        _log.info("tool_step_executing", step_id=self.step.id, tool=self.step.tool)
        start = time.monotonic()

        if state.get("is_test_run") and self.step.id in (state.get("mock_overrides") or {}):
            output = (state["mock_overrides"] or {})[self.step.id]
            _log.debug("tool_step_mock", step_id=self.step.id)
        elif self.mcp_client is None:
            output = {"_mock": True, "tool": self.step.tool, "input": resolved_input}
        else:
            result = await self.mcp_client.call_tool(
                tool_name=self.step.tool or "",
                arguments=resolved_input,
                tenant_id=state.get("tenant_id", ""),
            )
            output = result if isinstance(result, dict) else {"result": result}

        duration_ms = int((time.monotonic() - start) * 1000)

        return {
            "step_outputs": {**(state.get("step_outputs") or {}), self.step.id: output},
            "step_timings": {**(state.get("step_timings") or {}), self.step.id: duration_ms},
        }
