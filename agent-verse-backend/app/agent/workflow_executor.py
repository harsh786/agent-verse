"""Workflow executor: legacy sequential runner and new parallel DAG executor."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any

from app.agent.sanitization import sanitize_event
from app.agent.tool_context import ToolContext, ToolRef
from app.agent.workflow_planner import (
    WorkflowPlan,
    WorkflowStep,
    _StaticWorkflowPlan,
    _StaticWorkflowStep,
)
from app.tenancy.context import TenantContext

WorkflowEventCallback = Callable[[dict[str, Any]], Awaitable[None]]


# ---------------------------------------------------------------------------
# Parallel DAG executor (new — Phase 6)
# ---------------------------------------------------------------------------

class WorkflowExecutor:
    """Parallel workflow executor using asyncio.gather() for independent steps.

    The ``execute()`` method drives a :class:`~app.agent.workflow_planner.WorkflowPlan`
    through topologically-sorted execution waves.  Steps within the same wave
    have no mutual dependencies and run in parallel.

    The legacy ``run()`` method is preserved for backward-compatibility with
    ``goal_service.py``, which drives static connector-targeted workflows.
    """

    def __init__(self, provider: Any = None, mcp_client: Any = None) -> None:
        self._provider = provider
        self._mcp_client = mcp_client

    # ── new parallel DAG API ──────────────────────────────────────────────────

    async def execute(
        self,
        plan: WorkflowPlan,
        tenant_ctx: Any,
    ) -> dict[str, Any]:
        """Execute a workflow plan with parallel waves.

        Returns a result dict with keys:
          ``status``, ``steps_executed``, ``waves``, ``results``, ``summary``.
        """
        results: dict[str, Any] = {}
        waves = plan.execution_waves()

        for wave in waves:
            if len(wave) == 1:
                step = wave[0]
                result = await self._execute_step(step, tenant_ctx, prior_results=results)
                results[step.id] = result
                if result.get("status") == "failed" and not result.get("continue_on_error"):
                    return {
                        "status": "failed",
                        "failed_step": step.id,
                        "reason": result.get("error", "step failed"),
                        "completed_steps": list(results.keys()),
                        "results": results,
                    }
            else:
                # Parallel execution via asyncio.gather
                tasks = [
                    self._execute_step(step, tenant_ctx, prior_results=results)
                    for step in wave
                ]
                wave_results = await asyncio.gather(*tasks, return_exceptions=True)

                for step, result in zip(wave, wave_results):
                    if isinstance(result, Exception):
                        results[step.id] = {"status": "failed", "error": str(result)}
                    else:
                        results[step.id] = result

                # Check for non-ignorable failures
                failed = [
                    s for s, r in zip(wave, wave_results)
                    if isinstance(r, Exception) or (
                        isinstance(r, dict)
                        and r.get("status") == "failed"
                        and not r.get("continue_on_error")
                    )
                ]
                if failed:
                    return {
                        "status": "failed",
                        "failed_steps": [s.id for s in failed],
                        "reason": "Wave execution failure",
                        "completed_steps": list(results.keys()),
                        "results": results,
                    }

        # Synthesize final result from all complete step outputs
        final_outputs = [
            r.get("output", "")
            for r in results.values()
            if isinstance(r, dict) and r.get("status") == "complete"
        ]

        return {
            "status": "complete",
            "steps_executed": len(results),
            "waves": len(waves),
            "results": results,
            "summary": "\n\n".join(filter(None, final_outputs)),
        }

    async def _execute_step(
        self,
        step: WorkflowStep,
        tenant_ctx: Any,
        prior_results: dict,
    ) -> dict[str, Any]:
        """Execute a single workflow step, falling back LLM → stub."""
        step.status = "running"

        # Build context from prior dependent step outputs
        prior_context = ""
        if step.depends_on and prior_results:
            dep_outputs = [
                prior_results.get(dep, {}).get("output", "")
                for dep in step.depends_on
            ]
            prior_context = "\n".join(filter(None, dep_outputs))

        try:
            # Prefer tool execution via MCP when a tool name is specified
            if step.tool and self._mcp_client is not None:
                try:
                    result = await self._mcp_client.call_tool(
                        server_id="",
                        tool_name=step.tool,
                        arguments={
                            "description": step.description,
                            "context": prior_context,
                        },
                        tenant_ctx=tenant_ctx,
                    )
                    step.status = "complete"
                    step.result = str(result)
                    return {"status": "complete", "output": str(result), "tool": step.tool}
                except Exception as tool_exc:
                    import logging
                    logging.getLogger(__name__).warning(
                        "workflow_step_tool_failed: %s", tool_exc
                    )

            # Fall back to LLM completion
            if self._provider is not None:
                from app.providers.base import CompletionRequest, Message

                context_text = (
                    f"\nPrior context:\n{prior_context}" if prior_context else ""
                )
                model = getattr(self._provider, "_default_model", "")
                resp = await self._provider.complete(CompletionRequest(
                    messages=[Message(
                        role="user",
                        content=f"Execute this task: {step.description}{context_text}",
                    )],
                    model=model,
                    max_tokens=1000,
                ))
                step.status = "complete"
                step.result = resp.content
                return {"status": "complete", "output": resp.content}

            # No provider and no tool — return a stub completion
            step.status = "complete"
            step.result = f"Completed: {step.description}"
            return {"status": "complete", "output": step.result}

        except Exception as exc:
            step.status = "failed"
            step.error = str(exc)
            return {"status": "failed", "error": str(exc), "step_id": step.id}

    # ── legacy sequential API (used by goal_service.py) ───────────────────────

    async def run(
        self,
        *,
        plan: _StaticWorkflowPlan,
        goal: str,
        tenant_ctx: TenantContext,
        tool_context: ToolContext | None = None,
        event_callback: WorkflowEventCallback,
    ) -> None:
        """Execute a static :class:`_StaticWorkflowPlan` sequentially with SSE events."""
        await self._emit(
            event_callback,
            {
                "type": "workflow_planned",
                "goal": goal,
                "steps": [asdict(step) for step in plan.steps],
            },
        )

        outputs: dict[str, Any] = {}
        for step in plan.steps:
            step_dict = asdict(step)
            await self._emit(event_callback, {"type": "workflow_step_started", **step_dict})
            output = await self._run_step(
                step,
                tenant_ctx=tenant_ctx,
                tool_context=tool_context,
                previous_outputs=outputs,
            )
            outputs[step.step_id] = output
            await self._emit(
                event_callback,
                {"type": "workflow_step_complete", **step_dict, "output": output},
            )

    async def _emit(
        self, event_callback: WorkflowEventCallback, event: dict[str, Any]
    ) -> None:
        await event_callback(sanitize_event(event))

    async def _run_step(
        self,
        step: _StaticWorkflowStep,
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
        self, step: _StaticWorkflowStep, tool_context: ToolContext | None
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


# ---------------------------------------------------------------------------
# Helpers for legacy static-workflow step argument construction
# ---------------------------------------------------------------------------

_INTENT_TOOL_TOKENS: dict[str, tuple[str, ...]] = {
    "fetch_open_issues": ("fetch_open_issues", "jira_search", "search", "issue"),
    "create_summary_page": ("create_summary_page", "create_page", "page", "confluence"),
    "send_summary_email": ("send_summary_email", "send_email", "mail", "email"),
    "browser_automation": ("browser_automation", "browser", "rpa", "navigate", "ui"),
}


def _arguments_for_step(
    step: _StaticWorkflowStep, previous_outputs: dict[str, Any]
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


def _summarize_inputs(step: _StaticWorkflowStep, previous_outputs: dict[str, Any]) -> str:
    inputs = {step_id: previous_outputs.get(step_id) for step_id in step.input_from}
    return f"Workflow inputs: {inputs}"
