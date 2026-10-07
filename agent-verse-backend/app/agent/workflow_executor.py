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
from app.agent.tool_gate import GovernedToolGate
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

# Step statuses that mean "ran and produced a real result".
_DONE_STATUSES = frozenset({"complete", "executed"})
_SEP = chr(10) * 2


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
        tool_gate: GovernedToolGate | None = None,
        goal_id: str = "",
    ) -> None:
        self._provider = provider
        # Every MCP tool call goes through the same governed gate as the AgentGraph
        # executor (guardrails, permissions, policy, grants, risk/HITL, budget). It
        # used to call tools with no check at all. Without explicit services the
        # default gate still applies guardrails + risk classification (write_high /
        # unknown tools are refused without an approval gateway; destructive denied).
        self._tool_gate = tool_gate if tool_gate is not None else GovernedToolGate()
        self._goal_id = goal_id
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
        if not canonical_plan.steps:
            # Fail closed: a plan with no steps did nothing, so it must never be
            # reported as a completed workflow (and a goal_complete event).
            return {
                "status": "failed",
                "reason": "empty_plan: no executable steps could be derived from the goal",
                "steps_executed": 0,
                "waves": 0,
                "results": results,
                "summary": "",
            }

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
                # Only a step with a real result is "complete"; a denied, blocked
                # or failed step used to be announced as workflow_step_complete.
                _done = str(result.get("status", "")) in _DONE_STATUSES
                await self._emit(
                    event_callback,
                    {
                        "type": "workflow_step_complete" if _done else "workflow_step_failed",
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
            if isinstance(r, dict) and r.get("status") in _DONE_STATUSES
        ]
        # A workflow is only "complete" when every step produced a real result —
        # steps that were planned-but-not-executed, denied, or whose tool failed
        # used to be swept into a "complete" workflow (and a goal_complete event).
        not_done = {
            sid: r.get("reason") or r.get("error") or r.get("status")
            for sid, r in results.items()
            if not (isinstance(r, dict) and r.get("status") in _DONE_STATUSES)
        }
        if not_done:
            return {
                "status": "incomplete",
                "reason": "steps without a real result: "
                + "; ".join(f"{k}: {v}" for k, v in not_done.items()),
                "steps_executed": len(results) - len(not_done),
                "waves": len(waves),
                "results": results,
                "summary": _SEP.join(filter(None, final_outputs)),
            }

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
                edge = await execute_decision_node(
                    node_cfg, ctx, llm_provider=self._llm_provider, tenant_ctx=tenant_ctx
                )
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

            # ── A tool step runs ONLY via a governed MCP call. It used to fall back
            # to an LLM "Execute this task" completion (or a stub) when the tool was
            # unavailable or raised, and report the step complete.
            if step.tool:
                if self._mcp_client is None:
                    raise RuntimeError(f"no MCP client available to run tool '{step.tool}'")
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
                    tool_args = {"description": step.description, "context": prior_context}
                    decision = await self._tool_gate.authorize(
                        tool_name=step.tool,
                        arguments=tool_args,
                        tenant_ctx=tenant_ctx,
                        goal_id=self._goal_id,
                        step_description=step.description,
                    )
                    if not decision.allowed:
                        step.status = "failed"
                        step.error = decision.reason
                        return {
                            "status": "denied",
                            "error": decision.reason,
                            "tool": step.tool,
                            "step_id": step.id,
                        }
                    result = await self._mcp_client.call_tool(
                        server_id=server_id,
                        tool_name=step.tool,
                        arguments=tool_args,
                        tenant_ctx=tenant_ctx,
                    )
                except Exception as tool_exc:
                    import logging

                    logging.getLogger(__name__).warning("workflow_step_tool_failed: %s", tool_exc)
                    raise
                # success=False objects AND failure dicts ({"success": false} /
                # MCP {"isError": true}) — a dict used to count as complete.
                from app.agent.tool_outcomes import is_failed_tool_result

                if is_failed_tool_result(result):
                    _err = (
                        result.get("error") if isinstance(result, dict)
                        else getattr(result, "error", "")
                    )
                    raise RuntimeError(f"tool '{step.tool}' failed: {_err or 'error'}")
                output = getattr(result, "output", result)
                from app.mcp.client import with_stale_notice

                # a02-F030-04: a cached result served while the circuit is open.
                output_text = str(with_stale_notice(result, str(output)))
                step.status = "complete"
                step.result = output_text
                done: dict[str, Any] = {
                    "status": "complete", "output": output_text, "tool": step.tool
                }
                if getattr(result, "stale", False) is True:
                    done["stale"] = True
                return done

            # Fall back to LLM completion
            if self._provider is not None:
                from app.providers.base import CompletionRequest, Message

                context_text = f"\nPrior context:\n{prior_context}" if prior_context else ""
                from app.ai_router.role_preference import resolve_role_model

                model = resolve_role_model("workflow_step", provider=self._provider)
                from app.providers.guarded_completion import (
                    complete_decision,
                    generation_timeout_seconds,
                )

                resp = await complete_decision(
                    self._provider,
                    CompletionRequest(
                        messages=[
                            Message(
                                role="user",
                                content=f"Execute this task: {step.description}{context_text}",
                            )
                        ],
                        model=model,
                        max_tokens=1000,
                    ),
                    role="workflow_step",
                    tenant_ctx=tenant_ctx,
                    charge=False,
                    timeout_seconds=generation_timeout_seconds(),
                )
                if not await self._tool_gate.charge_llm(
                    goal_id=self._goal_id, tenant_ctx=tenant_ctx, resp=resp
                ):
                    raise RuntimeError("budget_exceeded: workflow step LLM spend denied")
                if not (resp.content or "").strip():
                    raise RuntimeError("LLM returned no output for the step")
                # LLM prose is not evidence the task was done: it used to be marked
                # complete unchecked. A verifier call must accept it; otherwise the
                # step is "unverified" and the workflow ends incomplete (CORE-15).
                verified, why = await self._verify_llm_step(
                    step.description, resp.content, tenant_ctx
                )
                step.result = resp.content
                if not verified:
                    step.status = "unverified"
                    step.error = why
                    return {
                        "status": "unverified",
                        "output": resp.content,
                        "reason": f"unverified LLM output: {why}",
                        "step_id": step.id,
                    }
                step.status = "complete"
                return {"status": "complete", "output": resp.content, "verified": True}

            # No provider and no tool: nothing can execute this step. Never report a
            # stub "Completed: ..." as done.
            step.status = "failed"
            step.error = "no tool and no LLM provider available to execute the step"
            return {"status": "failed", "error": step.error, "step_id": step.id}

        except Exception as exc:
            step.status = "failed"
            step.error = str(exc)
            return {"status": "failed", "error": str(exc), "step_id": step.id}

    async def _verify_llm_step(
        self, description: str, output: str, tenant_ctx: Any
    ) -> tuple[bool, str]:
        """Ask the LLM, as a strict verifier, whether *output* accomplishes the step.

        Returns ``(verified, reason)``. Any verifier failure counts as unverified.
        """
        from app.agent.schemas import parse_verifier_verdict
        from app.providers.base import CompletionRequest, Message

        provider = self._llm_provider or self._provider
        if provider is None:
            return False, "no verifier available"
        prompt = (
            "Verify a workflow step result. The step was answered by a language model "
            "with no tool access. Judge strictly: it passes only if the text itself "
            "accomplishes the task (e.g. an analysis, a draft, an answer from the given "
            "context) — not if it claims to have taken an action (sent, created, "
            "deployed, fetched live data) that it could not have taken.\n\n"
            f"Task: {description}\n\nResult:\n{output[:4000]}\n\n"
            'Reply as JSON: {"success": true|false, "reason": "<one sentence>"}'
        )
        try:
            from app.ai_router.role_preference import resolve_role_model
            from app.providers.guarded_completion import complete_decision

            # Breaker + timeout via complete_decision; spend is charged below through
            # the goal's tool gate, like the step call itself (charge=False: no double
            # charge).
            resp = await complete_decision(
                provider,
                CompletionRequest(
                    messages=[Message(role="user", content=prompt)],
                    model=resolve_role_model("verifier", provider=provider),
                    max_tokens=200,
                ),
                role="verifier",
                tenant_ctx=tenant_ctx,
                goal_id=self._goal_id,
                charge=False,
            )
            if not await self._tool_gate.charge_llm(
                goal_id=self._goal_id, tenant_ctx=tenant_ctx, resp=resp
            ):
                return False, "verification spend denied by budget"
            verdict = parse_verifier_verdict(resp.content or "")
        except Exception as exc:
            return False, f"verification failed ({type(exc).__name__})"
        reason = str(verdict.get("reason", "") or "")[:300]
        return bool(verdict.get("success", False)), reason or "verifier rejected the output"

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
        tool = self._find_matching_tool(step, tool_context)
        if tool is None or self._mcp_client is None:
            return {
                "status": "planned_not_executed",
                "reason": "no_matching_connector_tool",
            }

        arguments = _arguments_for_step(step, previous_outputs)
        # Governed gate (was: no checks at all). ``requires_approval`` steps are
        # routed through HITL instead of being silently parked.
        decision = await self._tool_gate.authorize(
            tool_name=tool.name,
            server_name=tool.server_name,
            arguments=arguments,
            tenant_ctx=tenant_ctx,
            goal_id=self._goal_id,
            step_description=step.intent,
            requires_approval=bool(step.requires_approval),
            auto_approve=bool(getattr(tool, "auto_approve", False)),
        )
        if not decision.allowed:
            return {
                "status": "planned_not_executed",
                "reason": decision.reason,
                "tool": tool.name,
                "server_id": tool.server_id,
            }
        result = await self._mcp_client.call_tool(
            server_id=tool.server_id,
            tool_name=tool.name,
            arguments=arguments,
            tenant_ctx=tenant_ctx,
        )
        if bool(getattr(result, "success", False)):
            executed: dict[str, Any] = {
                "status": "executed",
                "tool": tool.name,
                "server_id": tool.server_id,
                "success": True,
                "output": getattr(result, "output", None),
            }
            if getattr(result, "stale", False) is True:
                # a02-F030-04: cached while the connector's circuit is open.
                from app.mcp.client import stale_result_notice

                executed.update(stale=True, notice=stale_result_notice(result))
            return executed
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
