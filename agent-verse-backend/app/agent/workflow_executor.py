"""Workflow executor: legacy sequential runner and new parallel DAG executor."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any

from app.agent.sanitization import sanitize_event
from app.agent.structured_executor import (
    ExecutionCheckpoint,
    StepExecutionError,
    StructuredPlanExecutor,
)
from app.agent.structured_plan import StructuredPlan, StructuredStep
from app.agent.tool_context import ToolContext, ToolRef
from app.agent.workflow_nodes import (
    execute_decision_node,
    execute_delay_node,
    execute_loop_node,
    execute_rag_node,
    execute_skill_node,
)
from app.agent.workflow_planner import (
    WorkflowPlan,
    WorkflowStep,
    _StaticWorkflowPlan,
    _StaticWorkflowStep,
)
from app.orchestration.runtime_profile import default_pattern_limits
from app.orchestration.strategy_contracts import PatternLimits
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

    def __init__(
        self,
        provider: Any = None,
        mcp_client: Any = None,
        llm_provider: Any = None,
        embedder: Any = None,
        retrieval_gateway: Any = None,
    ) -> None:
        self._provider = provider
        self._mcp_client = mcp_client
        # llm_provider is the dedicated LLM interface used by node types that
        # need LLM access (decision, etc.).  Falls back to provider if not given.
        self._llm_provider = llm_provider or provider
        self._embedder = embedder
        self._retrieval_gateway = retrieval_gateway

    # ── new parallel DAG API ──────────────────────────────────────────────────

    async def execute(
        self,
        plan: WorkflowPlan | StructuredPlan | _StaticWorkflowPlan,
        tenant_ctx: Any,
        *,
        limits: PatternLimits | None = None,
        cancelled: asyncio.Event | None = None,
        prior_checkpoint: ExecutionCheckpoint | None = None,
        tool_context: ToolContext | None = None,
        event_callback: WorkflowEventCallback | None = None,
        goal: str = "",
    ) -> dict[str, Any]:
        """Execute a workflow plan with parallel waves.

        Returns a result dict with keys:
          ``status``, ``steps_executed``, ``waves``, ``results``, ``summary``.
        """
        results: dict[str, Any] = {}
        canonical_plan = self._canonical_plan(plan)
        waves = canonical_plan.execution_waves()
        if event_callback is not None:
            await self._emit(
                event_callback,
                {
                    "type": "workflow_planned",
                    "goal": goal,
                    "steps": [asdict(step) for step in canonical_plan.steps],
                },
            )

        async def run_step(step: StructuredStep) -> dict[str, Any]:
            if event_callback is not None:
                await self._emit(
                    event_callback,
                    {"type": "workflow_step_started", **asdict(step)},
                )
            if step.connector_name is not None or step.intent:
                result = await self._run_step(
                    step,
                    tenant_ctx=tenant_ctx,
                    tool_context=tool_context,
                    previous_outputs=results,
                )
            else:
                result = await self._execute_step(step, tenant_ctx, prior_results=results)
            results[step.id] = result
            if event_callback is not None:
                await self._emit(
                    event_callback,
                    {
                        "type": "workflow_step_complete",
                        **asdict(step),
                        "output": result,
                    },
                )
            if result.get("status") == "failed" and not result.get("continue_on_error"):
                raise RuntimeError(result.get("error", "step failed"))
            return result

        try:
            await StructuredPlanExecutor().execute(
                canonical_plan,
                run_step,
                limits=limits or default_pattern_limits(),
                cancelled=cancelled or asyncio.Event(),
                prior_checkpoint=prior_checkpoint,
            )
        except StepExecutionError as exc:
            return {
                "status": "failed",
                "reason": str(exc),
                "completed_steps": list(results),
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

    @staticmethod
    def _canonical_plan(
        plan: WorkflowPlan | StructuredPlan | _StaticWorkflowPlan,
    ) -> StructuredPlan:
        if isinstance(plan, StructuredPlan):
            return plan.validate()
        if isinstance(plan, _StaticWorkflowPlan):
            return StructuredPlan(
                steps=[
                    StructuredStep(
                        id=step.step_id,
                        description=step.intent,
                        connector_name=step.connector_name,
                        agent_id=step.agent_id,
                        intent=step.intent,
                        depends_on=list(step.input_from),
                        requires_approval=step.requires_approval,
                    )
                    for step in plan.steps
                ]
            ).validate()
        return StructuredPlan(
            steps=[
                StructuredStep(
                    id=step.id,
                    description=step.description or step.tool or step.id,
                    tool=step.tool or None,
                    depends_on=list(step.depends_on),
                    can_parallel=step.can_parallel,
                    estimated_minutes=step.estimated_minutes,
                    config=dict(step.config),
                )
                for step in plan.steps
            ]
        ).validate()

    async def _execute_step(
        self,
        step: WorkflowStep | StructuredStep,
        tenant_ctx: Any,
        prior_results: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a single workflow step, falling back LLM → stub."""
        step.status = "running"

        # Build context from prior dependent step outputs
        prior_context = ""
        if step.depends_on and prior_results:
            dep_outputs = [prior_results.get(dep, {}).get("output", "") for dep in step.depends_on]
            prior_context = "\n".join(filter(None, dep_outputs))

        try:
            # ── New node-type dispatch ────────────────────────────────────────
            # When step.tool holds a logical node-type keyword (decision, loop,
            # delay, rag, skill) we route to the dedicated node executor rather
            # than treating it as an MCP tool name.
            node_type = step.tool
            node_cfg: dict[str, Any] = {
                "condition": step.description,
                "type": node_type,
                "goal": step.description,
                "seconds": 0,
                "query_template": step.description,
                "top_k": 5,
                "strategy": "hybrid",
                "collection_id": "",
                "items_key": "items",
                "max_iter": 10,
                **step.config,
            }
            ctx: dict[str, Any] = {
                "goal": step.description,
                **{k: v for k, v in prior_results.items() if isinstance(v, dict)},
            }

            if node_type == "decision":
                edge = await execute_decision_node(node_cfg, ctx, llm_provider=self._llm_provider)
                step.status = "complete"
                step.result = str(edge)
                return {
                    "status": "complete",
                    "output": str(edge),
                    "edge": edge,
                    "node_type": node_type,
                }
            elif node_type == "loop":
                items = await execute_loop_node(node_cfg, ctx)
                step.status = "complete"
                step.result = str(items)
                return {
                    "status": "complete",
                    "output": str(items),
                    "items": items,
                    "node_type": node_type,
                }
            elif node_type == "delay":
                delay_result = await execute_delay_node(node_cfg, ctx)
                step.status = "complete"
                step.result = str(delay_result)
                return {
                    "status": "complete",
                    "output": str(delay_result),
                    "node_type": node_type,
                    **delay_result,
                }
            elif node_type == "rag":
                rag_result = await execute_rag_node(
                    node_cfg,
                    ctx,
                    retrieval_gateway=self._retrieval_gateway,
                    tenant_ctx=tenant_ctx,
                )
                step.status = "complete"
                step.result = rag_result.get("context_text", "")
                return {
                    "status": "complete",
                    "output": rag_result.get("context_text", ""),
                    "node_type": node_type,
                    **rag_result,
                }
            elif node_type == "skill":
                skill_result = await execute_skill_node(node_cfg, ctx)
                step.status = "complete"
                step.result = skill_result.get("skill_instructions", "")
                return {
                    "status": "complete",
                    "output": skill_result.get("skill_instructions", ""),
                    "node_type": node_type,
                    **skill_result,
                }

            # ── Prefer tool execution via MCP when a tool name is specified
            if step.tool and self._mcp_client is not None:
                try:
                    server_id = ""
                    # If server_id is empty, resolve it from the registry
                    if not server_id and self._mcp_client is not None:
                        try:
                            all_servers = await self._mcp_client._registry.list_all(
                                tenant_ctx=tenant_ctx
                            )
                            for srv in all_servers:
                                for tdef in srv.tool_definitions or []:
                                    if tdef.get("name") == step.tool:
                                        server_id = srv.id
                                        break
                                if server_id:
                                    break
                        except Exception:
                            pass
                    result = await self._mcp_client.call_tool(
                        server_id=server_id,
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

                    logging.getLogger(__name__).warning("workflow_step_tool_failed: %s", tool_exc)

            # Fall back to LLM completion
            if self._provider is not None:
                from app.providers.base import CompletionRequest, Message

                context_text = f"\nPrior context:\n{prior_context}" if prior_context else ""
                model = getattr(self._provider, "_default_model", "")
                resp = await self._provider.complete(
                    CompletionRequest(
                        messages=[
                            Message(
                                role="user",
                                content=f"Execute this task: {step.description}{context_text}",
                            )
                        ],
                        model=model,
                        max_tokens=1000,
                    )
                )
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
        plan: StructuredPlan | _StaticWorkflowPlan,
        goal: str,
        tenant_ctx: TenantContext,
        tool_context: ToolContext | None = None,
        event_callback: WorkflowEventCallback,
    ) -> None:
        """Compatibility forwarding method over the canonical bounded DAG executor."""
        await self.execute(
            plan,
            tenant_ctx,
            tool_context=tool_context,
            event_callback=event_callback,
            goal=goal,
        )

    async def _emit(self, event_callback: WorkflowEventCallback, event: dict[str, Any]) -> None:
        await event_callback(sanitize_event(event))

    async def _run_step(
        self,
        step: StructuredStep | _StaticWorkflowStep,
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
        self,
        step: StructuredStep | _StaticWorkflowStep,
        tool_context: ToolContext | None,
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
    step: StructuredStep | _StaticWorkflowStep,
    previous_outputs: dict[str, Any],
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


def _summarize_inputs(
    step: StructuredStep | _StaticWorkflowStep,
    previous_outputs: dict[str, Any],
) -> str:
    inputs = {step_id: previous_outputs.get(step_id) for step_id in step.input_from}
    return f"Workflow inputs: {inputs}"
