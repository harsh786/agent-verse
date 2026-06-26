"""LangGraph StateGraph-based autonomous agent.

Graph topology:
  START → initialize → rag_retrieval → plan → execute → verify →
          (complete → END | replan → plan | max_iter → END | waiting_human → END)

Five nodes, each is an async function receiving the graph state dict and returning updates.
LangGraph merges the returned dict into the running state (reducer pattern).

Checkpointing via MemorySaver means every state transition is persisted in-memory;
a crashed goal can be resumed by re-invoking with the same thread_id.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.agent.prompts import EXECUTOR_SYSTEM, PLANNER_SYSTEM, VERIFIER_SYSTEM
from app.agent.sanitization import (
    sanitize_event,
    sanitize_event_value,
    sanitize_tool_event_value,
    sanitize_tool_raw_output,
)
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus, SubGoal
from app.agent.tool_calls import extract_tool_call
from app.agent.tool_risk import classify_tool_risk
from app.governance.audit import AuditEvent, AuditLog
from app.governance.cost import CostController
from app.governance.hitl import ApprovalStatus, HITLGateway
from app.governance.permissions import ActionLevel, PermissionMatrix
from app.governance.policies import PolicyEngine, PolicyResult
from app.intelligence.eval_runner import EvalRunner
from app.intelligence.explainability import DecisionTrace
from app.intelligence.guardrails import GuardrailChecker
from app.memory.execution import ExecutionMemory
from app.memory.long_term import LongTermMemoryStore
from app.observability.metrics import (
    record_approval_wait,
    record_goal_completed,
    record_goal_failed,
    record_plan_duration,
    record_tool_call,
    record_verify_duration,
    track_tool_call,
)
from app.pipeline.steps import smart_context_fetch
from app.providers.base import CompletionRequest, LLMProvider, Message
from app.rag.store import KnowledgeStore
from app.reliability.circuit_breaker import CircuitBreaker
from app.reliability.dedup import DeduplicationCache
from app.reliability.result_processor import ResultProcessor
from app.reliability.rollback import RollbackEngine
from app.tenancy.context import TenantContext

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
_DEFAULT_MAX_ITERATIONS = 15
_HIGH_RISK_KEYWORDS = frozenset(
    ("deploy", "delete", "drop", "rm ", "prod", "production", "destroy", "wipe", "truncate")
)


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------


class GraphState(TypedDict, total=False):
    # Set at graph entry
    goal: str
    tenant_ctx: Any          # TenantContext (stored as Any for TypedDict compat)
    autonomy_mode: str       # supervised | bounded-autonomous | fully-autonomous
    # Populated by nodes
    agent_state: Any         # AgentState - the rich runtime state object
    rag_context: str         # retrieved context text
    plan: list[str]          # current step list
    iteration: int           # current iteration count
    terminal_reason: str     # why the graph terminated


# ---------------------------------------------------------------------------
# AgentGraph
# ---------------------------------------------------------------------------


class AgentGraph:
    """LangGraph-based agent loop with RAG retrieval, 12-step pipeline, and checkpointing."""

    def __init__(
        self,
        *,
        planner: LLMProvider,
        executor: LLMProvider,
        verifier: LLMProvider,
        max_iterations: int = _DEFAULT_MAX_ITERATIONS,
        # Governance
        permission_matrix: PermissionMatrix | None = None,
        audit_log: AuditLog | None = None,
        cost_controller: CostController | None = None,
        hitl_gateway: HITLGateway | None = None,
        policy_engine: PolicyEngine | None = None,
        # Reliability
        circuit_breakers: dict[str, CircuitBreaker] | None = None,
        rollback_engine: RollbackEngine | None = None,
        dedup_cache: DeduplicationCache | None = None,
        result_processor: ResultProcessor | None = None,
        # Memory / RAG
        exec_memory: ExecutionMemory | None = None,
        long_term_memory: LongTermMemoryStore | None = None,
        knowledge_store: KnowledgeStore | None = None,
        mcp_client: Any | None = None,
        # Intelligence
        guardrail_checker: GuardrailChecker | None = None,
        eval_runner: EvalRunner | None = None,
        # Autonomy
        autonomy_mode: str = "bounded-autonomous",
        # Goal-tree decomposition
        enable_goal_tree: bool = False,
        goal_tree_threshold: int = 4,  # decompose when plan >= this many steps
    ) -> None:
        self._planner = planner
        self._executor = executor
        self._verifier = verifier
        self._max_iterations = max_iterations
        self._permission_matrix = permission_matrix
        self._audit_log = audit_log
        self._cost_controller = cost_controller
        self._hitl_gateway = hitl_gateway
        self._policy_engine = policy_engine
        self._circuit_breakers = circuit_breakers
        self._rollback_engine = rollback_engine
        self._dedup_cache = dedup_cache
        self._result_processor = result_processor
        self._exec_memory = exec_memory
        self._long_term_memory = long_term_memory
        self._knowledge_store = knowledge_store
        self._mcp_client = mcp_client
        self._guardrail_checker = guardrail_checker
        self._eval_runner = eval_runner
        self._autonomy_mode = autonomy_mode
        self._enable_goal_tree = enable_goal_tree
        self._goal_tree_threshold = goal_tree_threshold
        self._hitl_timeout: float = 300.0
        self._checkpointer = MemorySaver()
        self._graph = self._build()
        # Per-run event callback (set in run())
        self._event_callback: EventCallback | None = None
        # OTel trace context injected by parent when spawned as sub-agent
        self._parent_trace_context: Any = None
        from opentelemetry import trace as _otel_trace
        self._tracer = _otel_trace.get_tracer(__name__)
        self._db_session_factory: Any = None  # Set by main.py after construction
        self._rpa_executor: Any = None  # Set externally to dispatch RPA tool calls directly

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def _build(self) -> Any:
        g: StateGraph[GraphState, Any, Any, Any] = StateGraph(GraphState)
        g.add_node("initialize", self._node_initialize)
        g.add_node("rag_retrieval", self._node_rag_retrieval)
        g.add_node("plan", self._node_plan)
        g.add_node("execute", self._node_execute)
        g.add_node("verify", self._node_verify)
        g.add_edge(START, "initialize")
        g.add_edge("initialize", "rag_retrieval")
        g.add_edge("rag_retrieval", "plan")
        g.add_edge("plan", "execute")
        g.add_edge("execute", "verify")
        g.add_conditional_edges(
            "verify",
            self._route,
            {"complete": END, "replan": "plan", "max_iter": END, "waiting_human": END},
        )
        return g.compile(checkpointer=self._checkpointer)

    def _sanitize_tool_raw_output(self, value: object) -> str:
        return sanitize_tool_raw_output(value, result_processor=self._result_processor)

    def _sanitize_tool_event_value(self, value: object) -> str:
        return sanitize_tool_event_value(value, result_processor=self._result_processor)

    def _sanitize_event_value(self, value: Any) -> Any:
        return sanitize_event_value(value, result_processor=self._result_processor)

    def _sanitize_event(self, event: dict[str, Any]) -> dict[str, Any]:
        return sanitize_event(event, result_processor=self._result_processor)

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------

    async def _node_initialize(self, state: GraphState) -> dict[str, Any]:
        goal: str = state["goal"]
        tenant_ctx: TenantContext = state["tenant_ctx"]
        existing_state = state.get("agent_state")
        agent_state = (
            existing_state
            if isinstance(existing_state, AgentState)
            else AgentState(goal=goal, tenant_ctx=tenant_ctx)
        )
        if not agent_state.goal:
            agent_state.goal = goal
        if agent_state.tenant_ctx is None:
            agent_state.tenant_ctx = tenant_ctx
        await self._emit({"type": "goal_started", "goal": goal})

        # Guardrail: check goal for injection attempts
        if self._guardrail_checker is not None:
            goal_issues = self._guardrail_checker.check_goal(goal=agent_state.goal)
            if goal_issues:
                agent_state.status = GoalStatus.FAILED
                agent_state.error_message = (
                    f"Goal rejected by guardrails: {'; '.join(goal_issues)}"
                )
                await self._emit({"type": "goal_rejected", "reason": agent_state.error_message})
                return {"agent_state": agent_state, "terminal_reason": "guardrail_rejected"}

        return {"agent_state": agent_state, "iteration": 0, "rag_context": ""}

    async def _node_rag_retrieval(self, state: GraphState) -> dict[str, Any]:
        agent_state: AgentState = state["agent_state"]
        tenant_ctx: TenantContext = state["tenant_ctx"]
        context_parts: list[str] = []

        # 1. Execution memory: recall past winning plans
        if self._exec_memory is not None:
            memories = self._exec_memory.recall(
                goal_hint=agent_state.goal, tenant_ctx=tenant_ctx, top_k=3
            )
            if memories:
                mem_text = "\n".join(
                    f"- Past plan: {m.get('plan', [])}" for m in memories
                )
                context_parts.append(f"[Past winning plans]\n{mem_text}")

        # 1b. Execution memory: recall past failure patterns to avoid repeating them
        if self._exec_memory is not None:
            try:
                failures = self._exec_memory.recall_failures(
                    goal_hint=agent_state.goal, tenant_ctx=tenant_ctx, top_k=3
                )
                if failures:
                    failure_lines = [
                        f"- {str(f.get('goal', f.get('goal_text', '')))[:100]}"
                        for f in failures[-3:]
                    ]
                    context_parts.append(
                        "[Previously Failed Approaches — Avoid These]\n"
                        + "\n".join(failure_lines)
                    )
            except Exception:
                pass

        # 2. Long-term memory
        if self._long_term_memory is not None:
            ltm = self._long_term_memory.recall(
                query=agent_state.goal, tenant_ctx=tenant_ctx, top_k=3
            )
            if ltm:
                ltm_text = "\n".join(f"- {m.content}" for m in ltm)
                context_parts.append(f"[Domain knowledge]\n{ltm_text}")

        # 3. KnowledgeStore hybrid search (skip — no collection_id available here)

        rag_context = "\n\n".join(context_parts)
        return {"rag_context": rag_context}

    async def _node_plan(self, state: GraphState) -> dict[str, Any]:
        agent_state: AgentState = state["agent_state"]
        tenant_ctx: TenantContext = state["tenant_ctx"]
        rag_context: str = state.get("rag_context", "")
        iteration: int = state.get("iteration", 0) + 1

        agent_state.status = GoalStatus.PLANNING
        agent_state.iterations = iteration

        # Build planner prompt with RAG context injected
        extra_parts: list[str] = []
        if rag_context:
            extra_parts.append(f"[Relevant context]\n{rag_context}")
        tool_prompt = agent_state.context.get("tool_prompt")
        if isinstance(tool_prompt, str) and tool_prompt:
            extra_parts.append(f"[Available connector tools]\n{tool_prompt}")

        # Append any visual/image context from perception pipeline
        image_context = agent_state.context.get("image_context", "")
        if image_context:
            extra_parts.append(f"[Visual context]\n{image_context}")

        if agent_state.verification_feedback:
            extra_parts.append(
                f"[Previous attempt feedback]\n{agent_state.verification_feedback}"
            )

        user_content = f"Goal: {agent_state.goal}"
        if extra_parts:
            user_content += "\n\n" + "\n\n".join(extra_parts)

        req = CompletionRequest(
            messages=[
                Message(role="system", content=PLANNER_SYSTEM),
                Message(role="user", content=user_content),
            ],
            model="claude-opus-4-8",
        )
        with self._tracer.start_as_current_span("agentverse.plan") as span:
            span.set_attribute("plan.iteration", agent_state.iterations)
            span.set_attribute("tenant.id", tenant_ctx.tenant_id)
            _plan_start = time.monotonic()
            resp = await self._planner.complete(req)
            record_plan_duration(agent_state.iterations, time.monotonic() - _plan_start)
        parsed = _parse_json(resp.content, key="steps")
        plan: list[str] = parsed.get("steps", [resp.content])
        if not plan:
            plan = [resp.content]

        agent_state.plan = plan
        await self._emit({"type": "plan_ready", "steps": plan, "iteration": iteration})
        return {"agent_state": agent_state, "plan": plan, "iteration": iteration}

    async def _node_execute(self, state: GraphState) -> dict[str, Any]:
        agent_state: AgentState = state["agent_state"]
        tenant_ctx: TenantContext = state["tenant_ctx"]
        plan: list[str] = state.get("plan") or agent_state.plan

        agent_state.status = GoalStatus.EXECUTING

        # Goal-tree decomposition: delegate large plans to parallel sub-agents
        if (
            self._enable_goal_tree
            and len(plan) >= self._goal_tree_threshold
        ):
            from app.agent.goal_tree import execute_goal_tree

            def _sub_graph_factory() -> AgentGraph:
                from opentelemetry import context as otel_context

                graph = AgentGraph(
                    planner=self._planner,
                    executor=self._executor,
                    verifier=self._verifier,
                    max_iterations=5,
                    # Inherit governance + reliability from parent
                    permission_matrix=self._permission_matrix,
                    audit_log=self._audit_log,
                    cost_controller=self._cost_controller,
                    hitl_gateway=self._hitl_gateway,
                    policy_engine=self._policy_engine,
                    result_processor=self._result_processor,
                    dedup_cache=DeduplicationCache(),      # fresh instance per sub-agent
                    rollback_engine=RollbackEngine(),       # fresh instance per sub-agent
                    guardrail_checker=self._guardrail_checker,
                    # Inherit memory + RAG
                    exec_memory=self._exec_memory,
                    long_term_memory=self._long_term_memory,
                    knowledge_store=self._knowledge_store,
                    mcp_client=self._mcp_client,
                    eval_runner=self._eval_runner,
                    # Sub-agents don't recurse into goal trees
                    enable_goal_tree=False,
                    autonomy_mode=self._autonomy_mode,
                )
                graph._parent_trace_context = otel_context.get_current()
                return graph

            try:
                sub_goals: list[SubGoal] = await execute_goal_tree(
                    agent_state.goal,
                    planner=self._planner,
                    tenant_ctx=tenant_ctx,
                    parent_goal_id=agent_state.goal_id,
                    graph_factory=_sub_graph_factory,
                )
                agent_state.sub_goals = sub_goals
                if sub_goals:
                    # Aggregate sub-goal results as steps so the verifier sees them
                    for sg in sub_goals:
                        step = StepResult(
                            description=sg.description,
                            output=sg.result or sg.error,
                            status=StepStatus.COMPLETE if not sg.error else StepStatus.FAILED,
                        )
                        agent_state.steps.append(step)
                    return {"agent_state": agent_state}
            except Exception as exc:
                # Fall through to normal execution if goal-tree fails
                await self._emit({"type": "goal_tree_error", "error": str(exc)})

        for step_index, step_desc in enumerate(plan):
            step = StepResult(description=step_desc, status=StepStatus.RUNNING)
            agent_state.steps.append(step)
            await self._emit({"type": "step_started", "step": step_desc})

            with self._tracer.start_as_current_span("agentverse.step.execute") as span:
                span.set_attribute("step.description", step_desc[:200])
                try:
                    output = await self._execute_step(step_desc, agent_state, tenant_ctx)
                except PermissionError as exc:
                    agent_state.status = GoalStatus.FAILED
                    agent_state.error_message = str(exc)
                    step.status = StepStatus.FAILED
                    step.error = str(exc)
                    raise  # re-raise so LangGraph propagates it out of ainvoke

                step.output = output
                step.status = StepStatus.COMPLETE
                await self._emit({"type": "step_complete", "step": step_desc, "output": output})
            await self._write_checkpoint(agent_state.goal_id, step_index, agent_state, tenant_ctx)

        return {"agent_state": agent_state}

    async def _execute_step(
        self, step: str, state: AgentState, tenant_ctx: TenantContext
    ) -> str:
        """12-step per-step pipeline mirroring AgentLoop._execute."""
        tool_name = _extract_tool_name(step)

        # 1. Cost check deferred — actual cost calculated after LLM call below.

        # 2. Exec memory recall — already done in rag_retrieval; skip here.

        # 3. Dedup
        if self._dedup_cache is not None:
            content_hash = hashlib.sha256(f"{step}:{state.goal}".encode()).hexdigest()
            if self._dedup_cache.is_duplicate(content_hash=content_hash, tenant_ctx=tenant_ctx):
                return "Duplicate step, returning cached result."
            self._dedup_cache.mark_seen(content_hash=content_hash, tenant_ctx=tenant_ctx)

        # 3b. Smart context fetch (per-step RAG)
        step_context = await smart_context_fetch(
            goal=state.goal,
            step=step,
            tenant_ctx=tenant_ctx,
            knowledge_store=self._knowledge_store,
        )

        # 4. Circuit breaker
        _active_breaker: CircuitBreaker | None = None
        if self._circuit_breakers:
            breaker = self._circuit_breakers.get("llm") or self._circuit_breakers.get(tool_name)
            if breaker is not None:
                if not breaker.can_call():
                    return "Circuit open, step skipped."
                _active_breaker = breaker  # track for success/failure recording

        # 5. Governance — permission check with scope extraction
        if self._permission_matrix is not None:
            scope_value = _extract_scope_value(step)
            level = self._permission_matrix.check(
                tool_name=tool_name,
                tenant_ctx=tenant_ctx,
                scope_value=scope_value,
            )
            if level == ActionLevel.DENY:
                record_tool_call(tool_name, "policy", "denied", 0.0)
                raise PermissionError(
                    f"Tool '{tool_name}' denied by governance policy "
                    f"for tenant '{tenant_ctx.tenant_id}'."
                )

        # 6. Guardrails — validate tool name, check injection/dangerous patterns
        if self._guardrail_checker is not None:
            violations = self._guardrail_checker.check(
                tool_name=tool_name,
                tool_args={"step_description": step},
            )
            if violations:
                return f"Guardrail blocked step: {'; '.join(violations)}"

        # 6b. Policy engine check (glob-based policies)
        if self._policy_engine is not None:
            policy_result = self._policy_engine.evaluate(
                tool_name=tool_name, tenant_ctx=tenant_ctx
            )
            if policy_result == PolicyResult.DENY:
                record_tool_call(tool_name, "policy", "denied", 0.0)
                raise PermissionError(
                    f"Tool '{tool_name}' denied by governance policy "
                    f"for tenant '{tenant_ctx.tenant_id}'."
                )
            elif policy_result == PolicyResult.REQUIRE_APPROVAL and self._hitl_gateway is not None:
                req_id = self._hitl_gateway.request_approval(
                    goal_id=state.goal_id, action=step, risk_level="high",
                    tenant_ctx=tenant_ctx,
                )
                if self._autonomy_mode == "supervised":
                    await self._emit(
                        {"type": "waiting_approval", "request_id": req_id, "action": step}
                    )
                    approval_started = time.monotonic()
                    final_status = await self._hitl_gateway.wait_for_approval(
                        req_id, tenant_ctx=tenant_ctx, timeout=self._hitl_timeout
                    )
                    record_approval_wait(time.monotonic() - approval_started)
                    if final_status == ApprovalStatus.REJECTED:
                        raise PermissionError(
                            f"Step '{step}' was rejected by human approver via policy."
                        )

        # 7. HITL gate
        if self._hitl_gateway is not None:
            risk = "high" if any(kw in step.lower() for kw in _HIGH_RISK_KEYWORDS) else "low"
            if risk == "high":
                req_id = self._hitl_gateway.request_approval(
                    goal_id=state.goal_id,
                    action=step,
                    risk_level=risk,
                    tenant_ctx=tenant_ctx,
                )
                if self._autonomy_mode == "supervised":
                    # Actually BLOCK until a human approves or rejects
                    await self._emit(
                        {"type": "waiting_approval", "request_id": req_id, "action": step}
                    )
                    approval_started = time.monotonic()
                    final_status = await self._hitl_gateway.wait_for_approval(
                        req_id, tenant_ctx=tenant_ctx
                    )
                    record_approval_wait(time.monotonic() - approval_started)
                    if final_status == ApprovalStatus.REJECTED:
                        raise PermissionError(f"Step '{step}' was rejected by human approver.")
                    elif final_status == ApprovalStatus.TIMED_OUT:
                        raise PermissionError(f"Step '{step}' approval timed out.")
                    await self._emit({"type": "approval_granted", "request_id": req_id})
                # In bounded/fully-autonomous: just log, don't block

        # 8. Execute via LLM executor
        recent_outputs = "\n".join(s.output for s in state.steps[-3:] if s.output)
        context_parts = []
        if recent_outputs:
            context_parts.append(f"Recent outputs:\n{recent_outputs}")
        if step_context:
            context_parts.append(f"Relevant knowledge:\n{step_context}")
        content = f"Step: {step}"
        if context_parts:
            content += "\n\n" + "\n\n".join(context_parts)
        req = CompletionRequest(
            messages=[
                Message(role="system", content=EXECUTOR_SYSTEM),
                Message(role="user", content=content),
            ],
            model="claude-opus-4-8",
        )
        try:
            async with track_tool_call(tool_name=tool_name, tenant_id=tenant_ctx.tenant_id):
                resp = await self._executor.complete(req)
            if _active_breaker is not None:
                _active_breaker.record_success()
        except Exception:
            if _active_breaker is not None:
                _active_breaker.record_failure()
            raise

        # 1. Calculate actual LLM cost from token usage and check budget
        if self._cost_controller is not None:
            from app.governance.pricing import estimate_cost as _estimate_cost
            _actual_cost = _estimate_cost(
                resp.model if hasattr(resp, "model") and resp.model else "",
                resp.input_tokens,
                resp.output_tokens,
            )
            state.context["total_cost_usd"] = (
                state.context.get("total_cost_usd", 0.0) + _actual_cost
            )
            ok = self._cost_controller.check_and_record(
                goal_id=state.goal_id,
                cost_usd=_actual_cost,
                tenant_ctx=tenant_ctx,
            )
            if not ok:
                return "Step skipped: budget exceeded."
        raw_output = resp.content
        raw_output_sanitized = False

        tool_call = extract_tool_call(raw_output)
        if tool_call is not None:
            tool_call_started = time.monotonic()
            if self._mcp_client is None:
                error = self._sanitize_tool_raw_output("MCP client unavailable")
                await self._emit(
                    {
                        "type": "tool_call_failed",
                        "tool": tool_call.tool,
                        "error": error,
                    }
                )
                record_tool_call(
                    tool_call.tool,
                    "unknown",
                    "failed",
                    time.monotonic() - tool_call_started,
                )
                raw_output = error
                raw_output_sanitized = True
            else:
                tool_context = state.context.get("tool_context")
                tool_ref = (
                    tool_context.find_tool(tool_call.tool)
                    if tool_context is not None and hasattr(tool_context, "find_tool")
                    else None
                )
                if tool_ref is None:
                    # Check if it's a built-in RPA tool (rpa_open_url, rpa_click, etc.)
                    from app.rpa.tools import RPA_TOOLS as _RPA_TOOLS
                    _rpa_tool_names = {str(t["name"]) for t in _RPA_TOOLS}
                    if tool_call.tool in _rpa_tool_names or any(
                        tool_call.tool.endswith(f".{t}") for t in _rpa_tool_names
                    ):
                        # Dispatch directly to RPAExecutor
                        rpa_tool_name = (
                            tool_call.tool.split(".")[-1]
                            if "." in tool_call.tool
                            else tool_call.tool
                        )
                        rpa_executor = (
                            getattr(state.context.get("_app_state"), "rpa_executor", None)
                            or getattr(self, "_rpa_executor", None)
                        )
                        if rpa_executor is not None:
                            try:
                                goal_id_str = str(getattr(state, "goal_id", ""))
                                rpa_result = await rpa_executor.execute(
                                    tool_name=rpa_tool_name,
                                    arguments=tool_call.arguments or {},
                                    tenant_id=tenant_ctx.tenant_id,
                                    goal_id=goal_id_str,
                                )
                                raw_output = (
                                    rpa_result.output
                                    if rpa_result.success
                                    else f"RPA error: {rpa_result.error}"
                                )
                                await self._emit({
                                    "type": "tool_call_complete",
                                    "tool": tool_call.tool,
                                    "server_id": "rpa",
                                    "success": rpa_result.success,
                                    "output": raw_output,
                                    "artifact_url": rpa_result.artifact_url,
                                    "artifact_name": rpa_result.artifact_name,
                                })
                                record_tool_call(
                                    rpa_tool_name, "rpa",
                                    "success" if rpa_result.success else "failed",
                                    time.monotonic() - tool_call_started,
                                )
                                raw_output_sanitized = True
                            except Exception as _rpa_exc:
                                raw_output = f"RPA execution error: {_rpa_exc}"
                                await self._emit({
                                    "type": "tool_call_failed",
                                    "tool": tool_call.tool,
                                    "error": str(_rpa_exc),
                                })
                                raw_output_sanitized = True
                        else:
                            raw_output = self._sanitize_tool_raw_output(
                                f"Tool not found: {tool_call.tool}"
                            )
                            raw_output_sanitized = True
                            await self._emit({
                                "type": "tool_call_failed",
                                "tool": tool_call.tool,
                                "error": self._sanitize_tool_event_value("Tool not found"),
                            })
                            record_tool_call(
                                tool_call.tool, "unknown", "failed",
                                time.monotonic() - tool_call_started,
                            )
                    else:
                        # Existing "tool_ref is None" error handling
                        raw_output = self._sanitize_tool_raw_output(
                            f"Tool not found: {tool_call.tool}"
                        )
                        raw_output_sanitized = True
                        await self._emit(
                            {
                                "type": "tool_call_failed",
                                "tool": tool_call.tool,
                                "error": self._sanitize_tool_event_value("Tool not found"),
                            }
                        )
                        record_tool_call(
                            tool_call.tool,
                            "unknown",
                            "failed",
                            time.monotonic() - tool_call_started,
                        )
                else:
                    tool_risk = classify_tool_risk(tool_ref.name, tool_ref.server_name)
                    if tool_risk == "destructive":
                        error = self._sanitize_tool_raw_output(
                            f"Jira tool '{tool_ref.name}' denied as destructive."
                        )
                        await self._emit(
                            {
                                "type": "tool_call_failed",
                                "tool": tool_ref.name,
                                "server_id": tool_ref.server_id,
                                "error": error,
                            }
                        )
                        record_tool_call(
                            tool_ref.name,
                            tool_ref.server_id,
                            "denied",
                            time.monotonic() - tool_call_started,
                        )
                        raw_output = error
                        raw_output_sanitized = True
                    elif tool_risk == "write_high":
                        if self._hitl_gateway is None:
                            error = self._sanitize_tool_raw_output(
                                f"Jira tool '{tool_ref.name}' requires approval."
                            )
                            await self._emit(
                                {
                                    "type": "tool_call_failed",
                                    "tool": tool_ref.name,
                                    "server_id": tool_ref.server_id,
                                    "error": error,
                                }
                            )
                            record_tool_call(
                                tool_ref.name,
                                tool_ref.server_id,
                                "failed",
                                time.monotonic() - tool_call_started,
                            )
                            raw_output = error
                            raw_output_sanitized = True
                        else:
                            req_id = self._hitl_gateway.request_approval(
                                goal_id=state.goal_id,
                                action=tool_ref.name,
                                risk_level=tool_risk,
                                tenant_ctx=tenant_ctx,
                            )
                            await self._emit(
                                {
                                    "type": "waiting_approval",
                                    "request_id": req_id,
                                    "action": tool_ref.name,
                                    "tool": tool_ref.name,
                                }
                            )
                            await self._emit(
                                {
                                    "type": "tool_call_pending_approval",
                                    "tool": tool_ref.name,
                                    "server_id": tool_ref.server_id,
                                    "request_id": req_id,
                                    "risk": tool_risk,
                                }
                            )
                            raw_output = self._sanitize_tool_raw_output(
                                f"Waiting for approval to call {tool_ref.name}."
                            )
                            record_tool_call(
                                tool_ref.name,
                                tool_ref.server_id,
                                "approval",
                                time.monotonic() - tool_call_started,
                            )
                            raw_output_sanitized = True
                    else:
                        try:
                            with self._tracer.start_as_current_span("agentverse.tool.call") as span:
                                span.set_attribute("tool.name", tool_call.tool if hasattr(tool_call, "tool") else "")
                                result = await self._mcp_client.call_tool(
                                    server_id=tool_ref.server_id,
                                    tool_name=tool_ref.name,
                                    arguments=tool_call.arguments,
                                    tenant_ctx=tenant_ctx,
                                )
                        except Exception:
                            record_tool_call(
                                tool_ref.name,
                                tool_ref.server_id,
                                "failed",
                                time.monotonic() - tool_call_started,
                            )
                            raise
                        # Apply PII check to raw tool output
                        raw_output_text = ""
                        if isinstance(result, dict):
                            raw_output_text = str(result.get("content") or result.get("result") or "")
                        if self._guardrail_checker and raw_output_text:
                            pii_issues = self._guardrail_checker.check_output(raw_output_text)
                            if pii_issues:
                                await self._emit({
                                    "type": "pii_redacted",
                                    "tool": getattr(tool_call, "tool", "") if tool_call else "",
                                    "issues": pii_issues,
                                })
                        raw_result_output = self._sanitize_tool_raw_output(result.output)
                        raw_result_error = self._sanitize_tool_raw_output(result.error)
                        await self._emit(
                            {
                                "type": "tool_call_complete",
                                "tool": tool_ref.name,
                                "server_id": tool_ref.server_id,
                                "success": result.success,
                                "output": self._sanitize_tool_event_value(result.output),
                                "error": self._sanitize_tool_event_value(result.error),
                            }
                        )
                        # Check for artifact capture (RPA screenshot etc.)
                        _artifact_uri: str = ""
                        _artifact_name: str = ""
                        if isinstance(result, dict):
                            _artifact_uri = result.get("artifact_url", "") or ""
                            _artifact_name = result.get("artifact_name", "") or ""
                        else:
                            _artifact_uri = getattr(result, "artifact_url", "") or ""
                            _artifact_name = getattr(result, "artifact_name", "") or ""
                        if _artifact_uri and not _artifact_uri.startswith("data:"):
                            await self._emit({
                                "type": "artifact_captured",
                                "artifact_type": "screenshot",
                                "artifact_url": _artifact_uri,
                                "artifact_name": _artifact_name,
                                "tool": tool_ref.name,
                            })
                        record_tool_call(
                            tool_ref.name,
                            tool_ref.server_id,
                            "success" if result.success else "failed",
                            time.monotonic() - tool_call_started,
                        )
                        raw_output = raw_result_output if result.success else raw_result_error
                        raw_output_sanitized = True

        # 9. Result processor / graph sanitizer — redact secrets, truncate
        if not raw_output_sanitized:
            raw_output = self._sanitize_tool_raw_output(raw_output)

        # Check output for data leakage
        if self._guardrail_checker is not None:
            output_issues = self._guardrail_checker.check_output(output=raw_output)
            if output_issues:
                raw_output = f"[Output redacted by guardrails: {'; '.join(output_issues)}]"

        # 10. Record rollback point
        if self._rollback_engine is not None:
            self._rollback_engine.register(action=step, inverse=lambda: None)

        # 11. Decision trace for explainability
        trace = DecisionTrace(
            action=step,
            reasoning="Executed step via LLM executor",
            evidence=[raw_output[:200]],
            alternatives=[],
            confidence=0.8,
        )
        state.context.setdefault("decision_traces", []).append(trace.to_dict())
        # Persist decision trace to DB
        if self._db_session_factory and hasattr(trace, "trace_id"):
            import asyncio as _asyncio
            _asyncio.create_task(self._persist_decision_trace(trace, state, tenant_ctx))

        # 12. Audit log
        if self._audit_log is not None:
            self._audit_log.record(
                AuditEvent(
                    goal_id=state.goal_id,
                    tool_name=tool_name,
                    action_level=ActionLevel.ALLOW_LOG,
                    outcome="step_complete",
                    step_id=state.steps[-1].step_id if state.steps else "",
                ),
                tenant_ctx=tenant_ctx,
            )

        return raw_output

    async def _node_verify(self, state: GraphState) -> dict[str, Any]:
        agent_state: AgentState = state["agent_state"]
        tenant_ctx: TenantContext = state["tenant_ctx"]

        agent_state.status = GoalStatus.VERIFYING
        summary = "\n".join(
            f"- {s.description}: {s.output}" for s in agent_state.steps[-5:]
        )
        req = CompletionRequest(
            messages=[
                Message(role="system", content=VERIFIER_SYSTEM),
                Message(
                    role="user",
                    content=f"Goal: {agent_state.goal}\nExecuted steps:\n{summary}",
                ),
            ],
            model="claude-opus-4-8",
        )
        with self._tracer.start_as_current_span("agentverse.verify") as span:
            span.set_attribute("verify.iteration", agent_state.iterations)
            _verify_start = time.monotonic()
            resp = await self._verifier.complete(req)
            record_verify_duration(time.monotonic() - _verify_start)
        parsed = _parse_json(resp.content)
        success: bool = bool(parsed.get("success", False))
        reason: str = self._sanitize_tool_raw_output(parsed.get("reason", ""))

        agent_state.verification_success = success
        agent_state.verification_feedback = reason
        await self._emit({"type": "verification_done", "success": success, "reason": reason})

        if success:
            # Record winning plan in execution memory
            if self._exec_memory is not None:
                self._exec_memory.record(
                    goal=agent_state.goal,
                    plan=agent_state.plan,
                    tenant_ctx=tenant_ctx,
                )
            # Auto-extract long-term learnings
            if self._long_term_memory is not None:
                step_outputs = " ".join(
                    s.output[:100] for s in agent_state.steps if s.output
                )
                self._long_term_memory.extract_from_goal(
                    goal=agent_state.goal,
                    result=step_outputs,
                    goal_id=agent_state.goal_id,
                    tenant_ctx=tenant_ctx,
                )
            # Score the completed goal
            if self._eval_runner is not None:
                scorecard = self._eval_runner.score(state=agent_state, tenant_ctx=tenant_ctx)
                agent_state.context["eval_scorecard"] = scorecard
            agent_state.status = GoalStatus.COMPLETE
            record_goal_completed(tenant_id=tenant_ctx.tenant_id)
            await self._emit({"type": "goal_complete"})

        return {"agent_state": agent_state}

    # ------------------------------------------------------------------
    # Routing (synchronous — LangGraph calls this synchronously)
    # ------------------------------------------------------------------

    def _route(self, state: GraphState) -> str:
        agent_state: AgentState | None = state.get("agent_state")
        if agent_state is None:
            return "max_iter"

        if state.get("terminal_reason") == "guardrail_rejected":
            return "max_iter"  # Terminate immediately

        if agent_state.verification_success:
            return "complete"

        iteration: int = state.get("iteration", 0)
        if iteration >= self._max_iterations:
            agent_state.status = GoalStatus.FAILED
            agent_state.error_message = (
                f"Goal failed: max iterations ({self._max_iterations}) reached."
            )
            record_goal_failed(tenant_id=agent_state.tenant_ctx.tenant_id)
            return "max_iter"

        # Supervised mode: pause if any HITL requests are pending
        if self._autonomy_mode == "supervised" and self._hitl_gateway is not None:
            tenant_ctx: TenantContext | None = state.get("tenant_ctx")
            if tenant_ctx is not None:
                pending = self._hitl_gateway.list_pending(tenant_ctx=tenant_ctx)
                if pending:
                    agent_state.status = GoalStatus.WAITING_HUMAN
                    return "waiting_human"

        return "replan"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def run(
        self,
        *,
        goal: str,
        tenant_ctx: TenantContext,
        initial_context: dict[str, Any] | None = None,
        event_callback: EventCallback | None = None,
    ) -> AgentState:
        """Execute the agent graph and return the final AgentState."""
        from opentelemetry import context as otel_context
        from opentelemetry import trace as otel_trace

        # Attach parent trace context if this is a sub-agent
        ctx_token = None
        if self._parent_trace_context is not None:
            ctx_token = otel_context.attach(self._parent_trace_context)

        try:
            _tracer = otel_trace.get_tracer(__name__)
            with _tracer.start_as_current_span("agentverse.goal.run") as span:
                span.set_attribute("goal.text", goal[:200])
                span.set_attribute("tenant.id", tenant_ctx.tenant_id)

                self._event_callback = event_callback
                thread_id = uuid.uuid4().hex
                config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}

                input_state: GraphState = {
                    "goal": goal,
                    "tenant_ctx": tenant_ctx,
                    "autonomy_mode": self._autonomy_mode,
                    "iteration": 0,
                }

                # Optionally seed the agent state with caller-provided context
                if initial_context:
                    seed = AgentState(goal=goal, tenant_ctx=tenant_ctx, context=initial_context)
                    input_state["agent_state"] = seed

                try:
                    result: dict[str, Any] = await self._graph.ainvoke(input_state, config=config)
                    final: AgentState = result.get("agent_state") or AgentState(
                        goal=goal, tenant_ctx=tenant_ctx
                    )
                    # Emit failure event if the loop ran out of iterations without success
                    if final.status == GoalStatus.FAILED and event_callback:
                        await self._emit({"type": "goal_failed", "reason": final.error_message})
                    return final
                except PermissionError:
                    raise  # governance denials surface directly to the caller
                except Exception as exc:
                    err_state = AgentState(goal=goal, tenant_ctx=tenant_ctx)
                    err_state.status = GoalStatus.FAILED
                    err_state.error_message = str(exc)
                    if event_callback:
                        await self._emit({"type": "goal_failed", "reason": str(exc)})
                    return err_state
        finally:
            if ctx_token is not None:
                otel_context.detach(ctx_token)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _emit(self, event: dict[str, Any]) -> None:
        from datetime import UTC, datetime
        if "ts" not in event:
            event["ts"] = datetime.now(UTC).isoformat()
        if self._event_callback is not None:
            await self._event_callback(self._sanitize_event(event))

    async def _write_checkpoint(
        self, goal_id: str, step_index: int, state: Any, tenant_ctx: Any
    ) -> None:
        """Write step checkpoint to DB after each successful step."""
        if self._db_session_factory is None:
            return
        try:
            from datetime import UTC, datetime

            from app.db.models.goal import GoalCheckpoint
            from app.db.rls import sqlalchemy_rls_context

            payload = {
                "step_index": step_index,
                "plan": getattr(state, "plan", []),
                "iterations": getattr(state, "iterations", 0),
                "completed_at": datetime.now(UTC).isoformat(),
            }
            async with self._db_session_factory() as session, session.begin(), \
                       sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                ck = GoalCheckpoint(
                    goal_id=goal_id,
                    tenant_id=tenant_ctx.tenant_id,
                    checkpoint_key=f"step_{step_index}",
                    sequence=step_index,
                    payload=payload,
                    recovery_status="checkpointed",
                )
                session.add(ck)
        except Exception as exc:
            from app.observability.logging import get_logger
            get_logger(__name__).warning(
                "checkpoint_write_failed", goal_id=goal_id, error=str(exc)
            )

    async def _load_checkpoint(
        self, goal_id: str, tenant_ctx: Any
    ) -> dict[str, Any] | None:
        """Load latest checkpoint for goal resume."""
        if self._db_session_factory is None:
            return None
        try:
            from sqlalchemy import select

            from app.db.models.goal import GoalCheckpoint
            from app.db.rls import sqlalchemy_rls_context

            async with self._db_session_factory() as session, \
                       sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                result = await session.execute(
                    select(GoalCheckpoint)
                    .where(
                        GoalCheckpoint.goal_id == goal_id,
                        GoalCheckpoint.tenant_id == tenant_ctx.tenant_id,
                    )
                    .order_by(GoalCheckpoint.sequence.desc())
                    .limit(1)
                )
                row = result.scalar_one_or_none()
                return row.payload if row else None
        except Exception as exc:
            from app.observability.logging import get_logger
            get_logger(__name__).warning(
                "checkpoint_load_failed", goal_id=goal_id, error=str(exc)
            )
            return None

    async def _persist_decision_trace(
        self, trace: Any, state: Any, tenant_ctx: Any
    ) -> None:
        """Persist decision trace record to DB (fire-and-forget via create_task)."""
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with self._db_session_factory() as session, session.begin(), \
                       sqlalchemy_rls_context(session, tenant_ctx.tenant_id):
                await session.execute(
                    text(
                        """INSERT INTO decision_traces
                            (id, goal_id, tenant_id, action, reasoning, confidence, created_at)
                            VALUES (:id, :gid, :tid, :action, :reasoning, :conf, NOW())
                            ON CONFLICT DO NOTHING"""
                    ),
                    {
                        "id": trace.trace_id,
                        "gid": state.goal_id,
                        "tid": tenant_ctx.tenant_id,
                        "action": str(getattr(trace, "action", ""))[:500],
                        "reasoning": str(getattr(trace, "reasoning", ""))[:1000],
                        "conf": float(getattr(trace, "confidence", 0.5)),
                    },
                )
        except Exception as exc:
            from app.observability.logging import get_logger
            get_logger(__name__).warning("decision_trace_persist_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _parse_json(text: str, key: str | None = None) -> dict[str, Any]:
    """Extract JSON from LLM text, tolerating markdown code-block wrappers."""
    text = re.sub(r"```(?:json)?\n?", "", text).strip()
    try:
        obj: dict[str, Any] = json.loads(text)
        return obj
    except json.JSONDecodeError:
        if key == "steps":
            return {"steps": [text]}
        return {"success": True, "reason": text}


def _extract_tool_name(step: str) -> str:
    """Heuristically extract a tool name from a step description.

    If the step contains 'call', takes the first word after 'call'.
    Otherwise returns 'llm_call' as the default.
    """
    lower = step.lower()
    if "call" in lower:
        parts = lower.split("call", 1)
        if len(parts) > 1:
            words = parts[1].strip().split()
            if words:
                return words[0].strip("_-.,;:")
    return "llm_call"


def _extract_scope_value(step: str) -> str | None:
    """Extract a repository / project / resource name from a step description.

    Checks (in order):
    1. GitHub / GitLab-style ``org/repo`` slug — e.g. ``acme/my-repo``
    2. JIRA-style project key embedded in an issue reference — e.g. ``PROJ-123``

    Returns the matched string, or ``None`` if no recognisable scope is found.
    """
    # GitHub / GitLab repo slug: word-chars-or-dots / word-chars-or-dots
    github_match = re.search(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\b", step)
    if github_match:
        return github_match.group(1)
    # JIRA project key: 2-10 uppercase letters preceding a dash+number issue ref
    jira_match = re.search(r"\b([A-Z]{2,10})-\d+\b", step)
    if jira_match:
        return jira_match.group(1)
    return None
