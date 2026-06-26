"""Minimal workflow executor for connector-targeted steps."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any

from app.agent.sanitization import sanitize_event
from app.agent.tool_context import ToolContext, ToolRef
from app.agent.workflow_planner import WorkflowPlan, WorkflowStep
from app.tenancy.context import TenantContext

WorkflowEventCallback = Callable[[dict[str, Any]], Awaitable[None]]


class WorkflowExecutor:
    """Execute a static workflow with deterministic events and safe fallbacks."""

    def __init__(self, *, mcp_client: Any | None = None) -> None:
        self._mcp_client = mcp_client

    async def run(
        self,
        *,
        plan: WorkflowPlan,
        goal: str,
        tenant_ctx: TenantContext,
        tool_context: ToolContext | None = None,
        event_callback: WorkflowEventCallback,
    ) -> None:
        await self._emit(
            event_callback,
            {
                "type": "workflow_planned",
                "goal": goal,
                "steps": [asdict(step) for step in plan.steps],
            }
        )

        outputs: dict[str, Any] = {}
        for step in plan.steps:
            step_dict = asdict(step)
            await self._emit(event_callback, {"type": "workflow_step_started", **step_dict})
            output = await self._execute_step(
                step,
                tenant_ctx=tenant_ctx,
                tool_context=tool_context,
                previous_outputs=outputs,
            )
            outputs[step.step_id] = output
            await self._emit(
                event_callback,
                {"type": "workflow_step_complete", **step_dict, "output": output}
            )

    async def _emit(
        self, event_callback: WorkflowEventCallback, event: dict[str, Any]
    ) -> None:
        await event_callback(sanitize_event(event))

    async def _execute_step(
        self,
        step: WorkflowStep,
        *,
        tenant_ctx: TenantContext,
        tool_context: ToolContext | None,
        previous_outputs: dict[str, Any],
    ) -> dict[str, Any]:
        if step.requires_approval:
            return {
                "status": "planned_not_executed",
                "reason": "approval_required",
            }

        tool = self._find_matching_tool(step, tool_context)
        if tool is None or self._mcp_client is None:
            return {
                "status": "planned_not_executed",
                "reason": "no_matching_connector_tool",
            }

        arguments = _arguments_for_step(step, previous_outputs)
        result = await self._mcp_client.call_tool(
            server_id=tool.server_id,
            tool_name=tool.name,
            arguments=arguments,
            tenant_ctx=tenant_ctx,
        )
        if bool(getattr(result, "success", False)):
            return {
                "status": "executed",
                "tool": tool.name,
                "server_id": tool.server_id,
                "success": True,
                "output": getattr(result, "output", None),
            }
        return {
            "status": "tool_call_failed",
            "tool": tool.name,
            "server_id": tool.server_id,
            "success": False,
            "error": str(getattr(result, "error", "")),
        }

    def _find_matching_tool(
        self, step: WorkflowStep, tool_context: ToolContext | None
    ) -> ToolRef | None:
        if tool_context is None or step.connector_name is None:
            return None

        connector = step.connector_name.casefold()
        intent_tokens = _INTENT_TOOL_TOKENS.get(step.intent, (step.intent,))
        for tool in tool_context.tools:
            haystack = " ".join(
                (tool.server_id, tool.server_name, tool.name, tool.description)
            ).casefold()
            if connector not in haystack:
                continue
            if any(token in haystack for token in intent_tokens):
                return tool
        return None


_INTENT_TOOL_TOKENS: dict[str, tuple[str, ...]] = {
    "fetch_open_issues": ("fetch_open_issues", "jira_search", "search", "issue"),
    "create_summary_page": ("create_summary_page", "create_page", "page", "confluence"),
    "send_summary_email": ("send_summary_email", "send_email", "mail", "email"),
    "browser_automation": ("browser_automation", "browser", "rpa", "navigate", "ui"),
}


def _arguments_for_step(
    step: WorkflowStep, previous_outputs: dict[str, Any]
) -> dict[str, Any]:
    if step.intent == "fetch_open_issues":
        return {"jql": "statusCategory != Done ORDER BY updated DESC"}
    if step.intent == "create_summary_page":
        return {
            "title": "Agent Verse workflow summary",
            "content": _summarize_inputs(step, previous_outputs),
        }
    if step.intent == "send_summary_email":
        return {
            "subject": "Agent Verse workflow summary",
            "body": _summarize_inputs(step, previous_outputs),
        }
    if step.intent == "browser_automation":
        return {"instruction": "Perform the requested browser automation."}
    return {}


def _summarize_inputs(step: WorkflowStep, previous_outputs: dict[str, Any]) -> str:
    inputs = {step_id: previous_outputs.get(step_id) for step_id in step.input_from}
    return f"Workflow inputs: {inputs}"
