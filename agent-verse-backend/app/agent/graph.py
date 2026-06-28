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

import asyncio
import hashlib
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.agent.prompts import CHAIN_OF_THOUGHT_SYSTEM, EXECUTOR_SYSTEM, PLANNER_SYSTEM, REFLECTION_SYSTEM, STRUCTURED_PLANNER_SYSTEM, VERIFIER_SYSTEM
from app.agent.sanitization import (
    sanitize_event,
    sanitize_event_value,
    sanitize_tool_event_value,
    sanitize_tool_raw_output,
)
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus, SubGoal
from app.agent.tool_calls import ToolCall, extract_tool_call
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
from app.providers.base import CompletionRequest, LLMProvider, Message, ToolDefinition
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
    cot_reasoning: str       # chain-of-thought output from _node_think


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
        # Model routing
        model_router: Any | None = None,
        # Semantic cache + embedder
        semantic_cache: Any | None = None,
        embedder: Any | None = None,
        # Phase 2 feature flags
        enable_cot: bool = False,
        enable_reflection: bool = False,
        # Autonomy
        autonomy_mode: str = "bounded-autonomous",
        # Goal-tree decomposition
        enable_goal_tree: bool = False,
        goal_tree_threshold: int = 4,  # decompose when plan >= this many steps
        # LangGraph checkpointer (RedisSaver when available, else MemorySaver)
        checkpointer: Any | None = None,
        # Distributed bulkhead registry (RedisBulkheadRegistry or None)
        bulkhead_registry: Any = None,
        # Cost tracker for real token-based cost recording
        cost_tracker: Any | None = None,
        # Step callback for streaming simulation (called after each step)
        step_callback: Any | None = None,
        **kwargs: Any,
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
        self._model_router: Any = model_router
        self._semantic_cache: Any = semantic_cache
        self._embedder: Any = embedder
        self._enable_cot = enable_cot
        self._enable_reflection = enable_reflection
        self._autonomy_mode = autonomy_mode
        self._enable_goal_tree = enable_goal_tree
        self._goal_tree_threshold = goal_tree_threshold
        self._hitl_timeout: float = 300.0
        self._checkpointer = checkpointer if checkpointer is not None else MemorySaver()
        self._bulkhead_registry = bulkhead_registry
        self._cost_tracker = cost_tracker
        self._step_callback = step_callback
        self._graph = self._build()
        # Per-run event callback (set in run())
        self._event_callback: EventCallback | None = None
        # OTel trace context injected by parent when spawned as sub-agent
        self._parent_trace_context: Any = None
        from opentelemetry import trace as _otel_trace
        self._tracer = _otel_trace.get_tracer(__name__)
        self._db_session_factory: Any = None  # Set by main.py after construction
        self._rpa_executor: Any = None  # Set externally to dispatch RPA tool calls directly
        self._tool_context: Any = None  # Settable from outside; used by _extract_tool_name
        self._prompt_optimizer: Any = None  # Settable from outside; PromptOptimizer instance
        self._self_optimizer: Any = None    # Settable from outside; SelfOptimizer instance
        self._app_state: Any = None         # Set externally by goal_service; FastAPI app
        self._agent_id: str | None = None   # Set externally by goal_service
        # Track fire-and-forget background tasks to prevent GC before completion
        self._background_tasks: set[Any] = set()
        # Agent knowledge collection binding — set externally by goal_service after construction
        self._agent_collection_ids: list[str] = []
        # Civilization spawn tool support — set when civilization_id is in initial_context
        self._civilization_id: str | None = None
        self._civilization_spawn_enabled: bool = False
        # Goal service reference — set externally for spawn tool dispatch
        self._goal_service: Any = None
        # Tenant context reference — set during run()
        self._tenant_ctx_ref: Any = None
        from app.observability.logging import get_logger as _get_logger
        self._logger = _get_logger(__name__)

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def _build(self) -> Any:
        g: StateGraph[GraphState, Any, Any, Any] = StateGraph(GraphState)
        g.add_node("initialize", self._node_initialize)
        g.add_node("rag_retrieval", self._node_rag_retrieval)
        # Add think node before plan when CoT enabled (Fix 2)
        if self._enable_cot:
            g.add_node("think", self._node_think)
        g.add_node("plan", self._node_plan)
        g.add_node("execute", self._node_execute)
        g.add_node("verify", self._node_verify)
        # Add reflect node when reflection enabled (Fix 2)
        if self._enable_reflection:
            g.add_node("reflect", self._node_reflect)

        g.add_edge(START, "initialize")
        g.add_edge("initialize", "rag_retrieval")
        # CoT: rag_retrieval → think → plan; otherwise rag_retrieval → plan
        if self._enable_cot:
            g.add_edge("rag_retrieval", "think")
            g.add_edge("think", "plan")
        else:
            g.add_edge("rag_retrieval", "plan")
        g.add_edge("plan", "execute")
        g.add_edge("execute", "verify")
        # Reflection: reflect → plan edge so re-plan follows reflection
        if self._enable_reflection:
            g.add_edge("reflect", "plan")

        routing_map: dict[str, Any] = {
            "complete": END, "replan": "plan", "max_iter": END, "waiting_human": END,
        }
        if self._enable_reflection:
            routing_map["reflect"] = "reflect"
        g.add_conditional_edges("verify", self._route, routing_map)
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

        # H-2: SelfOptimizerV2 arm config injection — pick experiment arm for this run
        self_opt_v2 = getattr(self._app_state, "self_optimizer_v2", None) if self._app_state else None
        if self_opt_v2 and self._agent_id:
            try:
                arm_config = await self_opt_v2.get_arm_config(
                    agent_id=self._agent_id,
                    goal_id=agent_state.goal_id,
                    tenant_id=tenant_ctx.tenant_id,
                )
                if arm_config and isinstance(agent_state.context, dict):
                    agent_state.context["_experiment_arm"] = arm_config.get("arm_name", "control")
            except Exception:
                pass

        return {"agent_state": agent_state, "iteration": 0, "rag_context": ""}

    async def _node_rag_retrieval(self, state: GraphState) -> dict[str, Any]:
        agent_state: AgentState = state["agent_state"]
        tenant_ctx: TenantContext = state["tenant_ctx"]
        context_parts: list[str] = []

        # 1. Execution memory: recall past winning plans (DB-backed async recall, BUG 2 fix)
        if self._exec_memory is not None:
            exec_plans: list[dict] = []
            try:
                exec_plans = await self._exec_memory.recall_async(
                    agent_state.goal,
                    tenant_id=tenant_ctx.tenant_id,
                    db=self._db_session_factory,
                    limit=3,
                )
            except Exception as _em_exc:
                self._logger.warning("exec_memory_recall_failed", error=str(_em_exc))
                exec_plans = self._exec_memory.recall(
                    goal_hint=agent_state.goal, tenant_ctx=tenant_ctx, top_k=3
                )
            if exec_plans:
                mem_text = "\n".join(
                    f"- Past plan: {m.get('plan', [])}" for m in exec_plans
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

        # 2. Long-term memory — async pgvector recall when embedder available (Task 4)
        if self._long_term_memory is not None:
            ltm = await self._long_term_memory.recall_async(
                query=agent_state.goal,
                tenant_ctx=tenant_ctx,
                top_k=3,
                db=self._db_session_factory,
                embedder=self._embedder,
            )
            if ltm:
                ltm_text = "\n".join(f"- {m.content}" for m in ltm)
                context_parts.append(f"[Domain knowledge]\n{ltm_text}")

        # 3. KnowledgeStore hybrid search using agent's allowed_collection_ids
        if self._knowledge_store is not None and self._agent_collection_ids:
            try:
                # Get query embedding if embedder is available
                query_embedding: list[float] = []
                if self._embedder is not None:
                    try:
                        from app.providers.base import EmbedRequest
                        _embed_resp = await self._embedder.embed(EmbedRequest(texts=[agent_state.goal]))
                        if _embed_resp.embeddings:
                            query_embedding = _embed_resp.embeddings[0]
                    except Exception:
                        pass
                knowledge_contexts = []
                for collection_id in self._agent_collection_ids[:3]:  # max 3 collections
                    results = await self._knowledge_store.hybrid_search_db(
                        query=agent_state.goal,
                        query_embedding=query_embedding,
                        collection_id=collection_id,
                        tenant_ctx=tenant_ctx,
                        top_k=3,
                    )
                    for result in results:
                        content = getattr(result, "content", str(result))
                        if content:
                            knowledge_contexts.append(
                                f"[From collection {collection_id}]\n{content[:300]}"
                            )
                if knowledge_contexts:
                    agent_state.context["rag_knowledge"] = "\n\n".join(knowledge_contexts)
                    self._logger.info(
                        "knowledge_store_rag_hit",
                        collections=len(self._agent_collection_ids),
                        chunks=len(knowledge_contexts),
                    )
            except Exception as _ks_exc:
                self._logger.warning("knowledge_store_rag_failed", error=str(_ks_exc))

        rag_context = "\n\n".join(context_parts)
        return {"rag_context": rag_context}

    async def _node_think(self, state: GraphState) -> dict[str, Any]:
        """Chain-of-thought thinking node: produces reasoning before planning."""
        agent_state: AgentState = state["agent_state"]
        req = CompletionRequest(
            messages=[
                Message(role="system", content=CHAIN_OF_THOUGHT_SYSTEM),
                Message(role="user", content=f"Goal: {agent_state.goal}"),
            ],
            model=(
                self._model_router.model_for("think")
                if self._model_router is not None
                else ""
            ),
        )
        resp = await self._planner.complete(req)
        return {"cot_reasoning": resp.content}

    async def _node_reflect(self, state: GraphState) -> dict[str, Any]:
        """Reflection node: diagnoses failure and populates verification_feedback."""
        agent_state: AgentState = state["agent_state"]
        failed_steps = [s for s in agent_state.steps if s.status == StepStatus.FAILED]
        failed_summary = (
            "\n".join(f"- {s.description}: {s.error}" for s in failed_steps)
            or agent_state.error_message
            or "No specific failure information available."
        )
        _reflect_model = ""
        if self._model_router is not None:
            try:
                _reflect_model = self._model_router.model_for("reflection") or ""
            except Exception:
                pass
        req = CompletionRequest(
            messages=[
                Message(role="system", content=REFLECTION_SYSTEM),
                Message(
                    role="user",
                    content=f"Goal: {agent_state.goal}\nFailed steps:\n{failed_summary}",
                ),
            ],
            model=_reflect_model,
        )
        resp = await self._planner.complete(req)
        agent_state.verification_feedback = resp.content
        return {"agent_state": agent_state}

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
        # Inject knowledge base context from agent's bound collections
        rag_knowledge: str = agent_state.context.get("rag_knowledge", "")
        if rag_knowledge:
            extra_parts.append(f"[Knowledge base context]\n{rag_knowledge}")
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

        # Inject chain-of-thought reasoning if available
        cot_reasoning: str = state.get("cot_reasoning", "")
        if cot_reasoning:
            extra_parts.append(f"[Chain-of-thought reasoning]\n{cot_reasoning}")

        user_content = f"Goal: {agent_state.goal}"
        if extra_parts:
            user_content += "\n\n" + "\n\n".join(extra_parts)

        # Determine system content — prepend agent system_prompt if present
        # Use PromptOptimizer variant when wired (Task 7)
        from app.intelligence.prompt_optimizer import PromptOptimizer as _PromptOptimizer
        _plan_optimizer = getattr(self, "_prompt_optimizer", None)
        if _plan_optimizer is not None:
            _plan_variant = _plan_optimizer.select_variant("planner", tenant_id=tenant_ctx.tenant_id)
            _planner_prompt = _plan_variant.prompt_text if _plan_variant is not None else PLANNER_SYSTEM
            # Store variant ID for A/B feedback in verify node (BUG 4 fix)
            if _plan_variant is not None:
                agent_state.context["planner_variant_id"] = _plan_variant.variant_id
        else:
            # Use structured planner when goal-tree is enabled for dependency-aware parallel execution
            _planner_prompt = STRUCTURED_PLANNER_SYSTEM if self._enable_goal_tree else PLANNER_SYSTEM
        agent_system_prompt = agent_state.context.get("system_prompt", "")
        system_content = (
            f"{agent_system_prompt}\n\n{_planner_prompt}" if agent_system_prompt else _planner_prompt
        )

        # P1.3: Inject HITL rejection note so planner avoids repeating the rejected action
        rejection_note: str = agent_state.context.get("hitl_rejection_note", "")
        if rejection_note:
            system_content += (
                f"\n\n[IMPORTANT — Previous Action REJECTED by Human Operator]\n"
                f"Rejection reason: {rejection_note}\n"
                f"Do NOT repeat the rejected action. Replan with a different approach."
            )

        # Phase 5: inject live tool schemas from MCP registry into system prompt
        tool_context_text = ""
        if self._mcp_client is not None and tenant_ctx is not None:
            try:
                all_tools = await self._mcp_client.discover_all_tools(tenant_ctx=tenant_ctx)
                if all_tools:
                    tool_lines = []
                    for t in all_tools[:20]:  # cap at 20 tools to stay within context
                        schema_str = ""
                        if hasattr(t, "input_schema") and t.input_schema:
                            schema_str = (
                                f" | schema: "
                                f"{json.dumps(t.input_schema, separators=(',', ':'))[:200]}"
                            )
                        tool_lines.append(
                            f"  - {t.name}: {t.description[:100]}{schema_str}"
                        )
                    tool_context_text = "\n\n[Available tools]\n" + "\n".join(tool_lines)
            except Exception as _tc_exc:
                self._logger.warning("tool_schema_injection_failed", error=str(_tc_exc))
        system_content = system_content + tool_context_text

        # Determine planning model via model_router if wired
        planning_model = "claude-opus-4-8"
        if self._model_router is not None:
            routed = self._model_router.model_for("planning")
            if routed:
                planning_model = routed

        req = CompletionRequest(
            messages=[
                Message(role="system", content=system_content),
                Message(role="user", content=user_content),
            ],
            model=planning_model,
        )
        with self._tracer.start_as_current_span("agentverse.plan") as span:
            span.set_attribute("plan.iteration", agent_state.iterations)
            span.set_attribute("tenant.id", tenant_ctx.tenant_id)
            _plan_start = time.monotonic()
            resp = await self._planner.complete(req)
            record_plan_duration(agent_state.iterations, time.monotonic() - _plan_start)
        parsed = _parse_json(resp.content, key="steps")
        raw_steps = parsed.get("steps", [resp.content])

        # Handle structured format (list of dicts from STRUCTURED_PLANNER_SYSTEM)
        # vs plain string steps (list of str from PLANNER_SYSTEM)
        if raw_steps and isinstance(raw_steps[0], dict):
            # Structured: pass full JSON as single entry so _node_execute can parse
            # it via StructuredPlan.from_llm_response (dependency-aware parallel waves)
            plan = [resp.content]
            _plan_display = [
                str(s.get("description", s.get("id", f"step{i}")))
                for i, s in enumerate(raw_steps)
            ]
        else:
            plan = [str(s) for s in raw_steps] if raw_steps else [resp.content]
            _plan_display = plan

        if not plan:
            plan = [resp.content]
            _plan_display = plan

        # Phase 5: validate that plan steps reference known tools
        _tool_warnings = await self._validate_plan_tools(_plan_display, tenant_ctx)
        for _warn in _tool_warnings:
            self._logger.warning("plan_tool_validation", warning=_warn)

        agent_state.plan = _plan_display
        await self._emit({"type": "plan_ready", "steps": _plan_display, "iteration": iteration})
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

        # Build StructuredPlan for wave-based parallel execution (Fix 1 + Fix 3)
        import asyncio as _asyncio
        from app.agent.structured_plan import StructuredPlan as _SP, StructuredStep as _SS

        _structured: _SP | None = None
        for _entry in plan:
            try:
                _parsed = json.loads(_entry)
                if isinstance(_parsed, dict) and "steps" in _parsed:
                    _structured = _SP.from_llm_response(_entry)
                    break
            except Exception:
                pass

        if _structured is None:
            # Plain string steps — treat as sequential (each depends on the previous)
            _structured = _SP(steps=[
                _SS(id=f"s{i}", description=sd, depends_on=[f"s{i - 1}"] if i > 0 else [])
                for i, sd in enumerate(plan)
            ])

        waves = _structured.execution_waves()
        step_global_index = 0
        # P1.1: Track completed StructuredStep objects for condition evaluation
        _completed_steps: dict[str, Any] = {}

        for wave_idx, wave in enumerate(waves):
            # P1.1: Filter out steps whose condition evaluates to False
            eligible_steps = [s for s in wave if s.should_execute(_completed_steps)]
            if not eligible_steps:
                self._logger.info(
                    "wave_all_steps_skipped_by_condition",
                    wave=wave_idx,
                    skipped=[s.id for s in wave],
                )
                continue

            if len(eligible_steps) == 1:
                # Single step — execute normally (with loop support if configured)
                struct_step = eligible_steps[0]
                step_desc = struct_step.description
                step = StepResult(description=step_desc, status=StepStatus.RUNNING)
                agent_state.steps.append(step)
                await self._emit({"type": "step_started", "step": step_desc})

                with self._tracer.start_as_current_span("agentverse.step.execute") as span:
                    span.set_attribute("step.description", step_desc[:200])
                    try:
                        if struct_step.loop_until is not None:
                            # P1.1: Loop execution
                            output = await self._execute_step_with_loop(
                                struct_step, agent_state, tenant_ctx
                            )
                        elif self._semantic_cache is not None:
                            output = await self._execute_step_with_cache(
                                step_desc, agent_state, tenant_ctx
                            )
                        else:
                            output = await self._execute_step(step_desc, agent_state, tenant_ctx)
                    except PermissionError as exc:
                        agent_state.status = GoalStatus.FAILED
                        agent_state.error_message = str(exc)
                        step.status = StepStatus.FAILED
                        step.error = str(exc)
                        raise  # re-raise so LangGraph propagates it out of ainvoke

                step.output = output
                step.status = StepStatus.COMPLETE
                # P1.1: Update StructuredStep runtime state for condition evaluation
                struct_step.output = output
                struct_step.status = "complete"
                _completed_steps[struct_step.id] = struct_step
                await self._emit({"type": "step_complete", "step": step_desc, "output": output})
                # Invoke step_callback for streaming simulation support
                if self._step_callback is not None:
                    try:
                        import asyncio as _asyncio_cb
                        _asyncio_cb.create_task(self._step_callback("step_completed", {
                            "description": step_desc,
                            "tool_called": self._extract_tool_name(step_desc),
                            "output": output[:500] if output else "",
                            "cost_increment": (
                                agent_state.context.get("last_step_cost", 0.0)
                                if isinstance(agent_state.context, dict)
                                else 0.0
                            ),
                        }))
                    except Exception:
                        pass
                await self._write_checkpoint(
                    agent_state.goal_id, step_global_index, agent_state, tenant_ctx
                )
                step_global_index += 1

            else:
                # Multiple independent steps — execute in parallel via asyncio.gather
                await self._emit({
                    "type": "steps_parallel_start",
                    "wave": wave_idx,
                    "steps": [s.description for s in eligible_steps],
                    "count": len(eligible_steps),
                })

                # Pre-create StepResult objects before parallel execution to maintain order
                parallel_steps: list[StepResult] = []
                for s in eligible_steps:
                    sr = StepResult(description=s.description, status=StepStatus.RUNNING)
                    agent_state.steps.append(sr)
                    await self._emit({"type": "step_started", "step": s.description})
                    parallel_steps.append(sr)

                # Lock to protect shared agent_state mutations across concurrent coroutines
                _state_lock = _asyncio.Lock()

                async def _run_wave_step(
                    desc: str, sr: StepResult
                ) -> None:
                    try:
                        if self._semantic_cache is not None:
                            out = await self._execute_step_with_cache(
                                desc, agent_state, tenant_ctx
                            )
                        else:
                            out = await self._execute_step(desc, agent_state, tenant_ctx)
                        async with _state_lock:
                            sr.output = out
                            sr.status = StepStatus.COMPLETE
                        await self._emit({"type": "step_complete", "step": desc, "output": out})
                    except PermissionError as exc:
                        async with _state_lock:
                            agent_state.status = GoalStatus.FAILED
                            agent_state.error_message = str(exc)
                            sr.status = StepStatus.FAILED
                            sr.error = str(exc)
                        raise
                    except Exception as exc:
                        async with _state_lock:
                            sr.status = StepStatus.FAILED
                            sr.error = str(exc)
                        raise

                tasks = [
                    _asyncio.create_task(
                        _run_wave_step(eligible_steps[i].description, parallel_steps[i])
                    )
                    for i in range(len(eligible_steps))
                ]
                try:
                    await _asyncio.gather(*tasks)
                except (PermissionError, Exception) as exc:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    await _asyncio.gather(*tasks, return_exceptions=True)
                    raise

                # P1.1: Update StructuredStep runtime state for parallel steps
                for i, struct_step_par in enumerate(eligible_steps):
                    struct_step_par.output = parallel_steps[i].output
                    struct_step_par.status = (
                        "complete" if parallel_steps[i].status == StepStatus.COMPLETE else "failed"
                    )
                    _completed_steps[struct_step_par.id] = struct_step_par

                for i in range(len(eligible_steps)):
                    await self._write_checkpoint(
                        agent_state.goal_id, step_global_index + i, agent_state, tenant_ctx
                    )
                step_global_index += len(eligible_steps)

                await self._emit({
                    "type": "steps_parallel_complete",
                    "wave": wave_idx,
                    "count": len(eligible_steps),
                })

        return {"agent_state": agent_state}

    async def _execute_step_with_loop(
        self,
        step: Any,
        agent_state: AgentState,
        tenant_ctx: TenantContext,
    ) -> str:
        """Execute a step with loop-until support (P1.1).

        Calls ``_execute_step`` repeatedly until ``loop_until`` evaluates to True
        or ``max_loop_iter`` is exceeded. Uses exponential backoff between iterations.
        """
        for iteration in range(step.max_loop_iter):
            step.iterations_used = iteration + 1
            output = await self._execute_step(step.description, agent_state, tenant_ctx)
            step.output = output

            try:
                from app.agent.structured_plan import _safe_eval_condition as _loop_eval
                done = _loop_eval(
                    step.loop_until,
                    {"output": output, "iteration": iteration + 1, "iterations": iteration + 1},
                )
            except Exception:
                done = True  # On eval error, exit loop

            if done:
                self._logger.info(
                    "loop_step_completed",
                    step_id=step.id,
                    iterations=step.iterations_used,
                )
                return output

            if iteration < step.max_loop_iter - 1:
                delay = min(2 ** iteration, 30)  # exponential backoff, max 30s
                self._logger.info(
                    "loop_step_retry",
                    step_id=step.id,
                    iteration=iteration + 1,
                    next_delay_s=delay,
                )
                await asyncio.sleep(delay)

        # Max iterations reached
        self._logger.warning(
            "loop_step_max_iterations_reached",
            step_id=step.id,
            max=step.max_loop_iter,
        )
        return step.output  # Return last output

    async def _execute_step(
        self, step: str, state: AgentState, tenant_ctx: TenantContext
    ) -> str:
        """12-step per-step pipeline mirroring AgentLoop._execute."""
        tool_name = self._extract_tool_name(step)

        # 1. Cost check deferred — actual cost calculated after LLM call below.

        # 2. Exec memory recall — already done in rag_retrieval; skip here.

        # 3. Dedup
        if self._dedup_cache is not None:
            content_hash = hashlib.sha256(f"{step}:{state.goal}".encode()).hexdigest()
            if self._dedup_cache.is_duplicate(content_hash=content_hash, tenant_ctx=tenant_ctx):
                return "Duplicate step, returning cached result."
            self._dedup_cache.mark_seen(content_hash=content_hash, tenant_ctx=tenant_ctx)

        # 3b. Generate step embedding for semantic RAG retrieval (Task 2)
        step_embedding: list[float] | None = None
        if self._embedder is not None:
            try:
                from app.providers.base import EmbedRequest
                _embed_resp = await self._embedder.embed(EmbedRequest(texts=[step]))
                if _embed_resp.embeddings:
                    step_embedding = _embed_resp.embeddings[0]
            except Exception:
                pass  # embedder unavailable — RAG still works via keyword fallback

        # 3c. Smart context fetch (per-step RAG)
        step_context = await smart_context_fetch(
            goal=state.goal,
            step=step,
            tenant_ctx=tenant_ctx,
            knowledge_store=self._knowledge_store,
            query_embedding=step_embedding,
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
        _hitl_already_requested = False
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
                req_id = str(self._hitl_gateway.request_approval(
                    goal_id=state.goal_id, action=step, risk_level="high",
                    tenant_ctx=tenant_ctx,
                ))
                _hitl_already_requested = True
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
        if not _hitl_already_requested and self._hitl_gateway is not None:
            risk = "high" if any(kw in step.lower() for kw in _HIGH_RISK_KEYWORDS) else "low"
            if risk == "high":
                req_id = str(self._hitl_gateway.request_approval(
                    goal_id=state.goal_id,
                    action=step,
                    risk_level=risk,
                    tenant_ctx=tenant_ctx,
                ))
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

        # Collect available tools for structured tool calling (Task 1)
        _tool_defs: list[ToolDefinition] = []
        _tc_ctx = state.context.get("tool_context")
        if _tc_ctx is not None and hasattr(_tc_ctx, "tools"):
            for _t in _tc_ctx.tools:
                _tool_defs.append(ToolDefinition(
                    name=(
                        f"{_t.server_name}.{_t.name}"
                        if hasattr(_t, "server_name")
                        else _t.name
                    ),
                    description=getattr(_t, "description", ""),
                    input_schema=getattr(_t, "input_schema", {}),
                ))

        # Select executor system prompt via PromptOptimizer if wired (Task 7)
        _executor_prompt = EXECUTOR_SYSTEM
        _exec_optimizer = getattr(self, "_prompt_optimizer", None)
        if _exec_optimizer is not None:
            _exec_variant = _exec_optimizer.select_variant("executor")
            if _exec_variant is not None:
                _executor_prompt = _exec_variant.prompt_text

        # Resolve executor model via model_router when available (Bug 3 fix)
        _exec_model = ""
        if self._model_router is not None:
            try:
                _exec_model = self._model_router.model_for("execution") or ""
            except Exception:
                pass

        req = CompletionRequest(
            messages=[
                Message(role="system", content=_executor_prompt),
                Message(role="user", content=content),
            ],
            model=_exec_model,
            tools=_tool_defs,
        )

        # 8a. Bulkhead — distributed concurrency limit per tenant (RedisBulkhead or Semaphore)
        _bulkhead = None
        if self._bulkhead_registry is not None and tenant_ctx is not None:
            try:
                _bulkhead = self._bulkhead_registry.get_bulkhead(tenant_ctx.tenant_id)
            except Exception:
                _bulkhead = None

        _bulkhead_acquired = False
        if _bulkhead is not None:
            try:
                if hasattr(_bulkhead, "acquire"):
                    # RedisBulkhead path
                    _bulkhead_acquired = await _bulkhead.acquire()
                    if not _bulkhead_acquired:
                        self._logger.warning(
                            "bulkhead_full",
                            tenant_id=getattr(tenant_ctx, "tenant_id", ""),
                            step=step[:100],
                        )
                        return (
                            "[Bulkhead: too many concurrent operations for this tenant."
                            " Please retry.]"
                        )
                else:
                    # asyncio.Semaphore fallback
                    await _bulkhead.acquire()
                    _bulkhead_acquired = True
            except Exception as bulkhead_exc:
                self._logger.warning("bulkhead_acquire_failed", error=str(bulkhead_exc))
                _bulkhead_acquired = False

        try:
            try:
                async with track_tool_call(tool_name=tool_name, tenant_id=tenant_ctx.tenant_id):
                    resp = await self._executor.complete(req)
                if _active_breaker is not None:
                    _active_breaker.record_success()
            except Exception:
                if _active_breaker is not None:
                    _active_breaker.record_failure()
                raise
        finally:
            if _bulkhead_acquired and _bulkhead is not None:
                try:
                    if hasattr(_bulkhead, "release"):
                        await _bulkhead.release()  # RedisBulkhead
                    else:
                        _bulkhead.release()  # asyncio.Semaphore
                except Exception:
                    pass

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
            ok = await self._cost_controller.check_and_record(
                goal_id=state.goal_id,
                cost_usd=_actual_cost,
                tenant_ctx=tenant_ctx,
            )
            if not ok:
                return "Step skipped: budget exceeded."

        # 1b. Record ACTUAL token cost via CostTracker when usage is available
        if self._cost_tracker is not None and getattr(resp, "usage", None) is not None:
            try:
                from app.intelligence.cost_tracker import calculate_cost as _calc_cost
                _model_name = resp.model if hasattr(resp, "model") and resp.model else _exec_model
                _real_cost = _calc_cost(
                    _model_name,
                    resp.usage.prompt_tokens,
                    resp.usage.completion_tokens,
                )
                state.context["total_cost_usd"] = (
                    state.context.get("total_cost_usd", 0.0) + _real_cost
                )
                await self._cost_tracker.record_llm_usage(
                    model=_model_name,
                    prompt_tokens=resp.usage.prompt_tokens,
                    completion_tokens=resp.usage.completion_tokens,
                    tenant_ctx=tenant_ctx,
                    goal_id=state.goal_id or "",
                    agent_id=state.context.get("agent_id"),
                    role="executor",
                )
            except Exception as _ct_exc:
                self._logger.warning("cost_tracker_record_failed", error=str(_ct_exc))
        raw_output = resp.content
        raw_output_sanitized = False

        # Prefer structured tool_calls from provider; fall back to text parsing (Task 1)
        _structured_tcs: list[dict[str, Any]] = resp.tool_calls if resp.tool_calls else []
        if _structured_tcs:
            _first_stc = _structured_tcs[0]
            _stc_name = _first_stc.get("name") or _first_stc.get("tool_name", "")
            _stc_args = _first_stc.get("input") or _first_stc.get("arguments") or {}
            if not isinstance(_stc_args, dict):
                _stc_args = {}
            tool_call = ToolCall(tool=_stc_name, arguments=_stc_args) if _stc_name else None
            # Update tool_name from structured response (Task 3)
            if _stc_name:
                tool_name = self._extract_tool_name(
                    step, tool_calls_result=[{"tool_name": _stc_name}]
                )
        else:
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
                    # Check if it's a civilization spawn tool call
                    if (
                        self._civilization_spawn_enabled
                        and self._civilization_id
                        and tool_call.tool == "civilization_spawn"
                    ):
                        try:
                            from app.civilization.spawn_tool import execute_spawn_tool
                            from app.civilization.governor import Governor
                            _gov_kwargs: dict[str, Any] = {
                                "civilization_id": self._civilization_id,
                                "tenant_id": tenant_ctx.tenant_id,
                            }
                            if self._db_session_factory is not None:
                                _gov_kwargs["db_session_factory"] = self._db_session_factory
                            _civ_const_placeholder = None
                            try:
                                from app.civilization.models import Constitution
                                _civ_const_placeholder = Constitution()
                            except Exception:
                                pass
                            if _civ_const_placeholder is not None:
                                _gov_kwargs["constitution"] = _civ_const_placeholder
                            governor = Governor(**_gov_kwargs)
                            spawn_result = await execute_spawn_tool(
                                arguments=tool_call.arguments or {},
                                governor=governor,
                                goal_service=self._goal_service,
                                tenant_ctx=tenant_ctx,
                            )
                            raw_output = str(spawn_result)
                            await self._emit({
                                "type": "child_agent_spawned",
                                "parent_agent_id": getattr(state, "agent_id", ""),
                                "child_agent_id": spawn_result.get("agent_id"),
                                "child_goal_id": spawn_result.get("goal_id"),
                                "depth": spawn_result.get("depth", 0),
                                "capability": (tool_call.arguments or {}).get("requested_capability", ""),
                            })
                            raw_output_sanitized = True
                            record_tool_call(
                                tool_call.tool, "civilization", "success",
                                time.monotonic() - tool_call_started,
                            )
                        except Exception as _spawn_exc:
                            raw_output = f"Civilization spawn error: {_spawn_exc}"
                            await self._emit({
                                "type": "tool_call_failed",
                                "tool": tool_call.tool,
                                "error": str(_spawn_exc),
                            })
                            raw_output_sanitized = True
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
                            req_id = str(self._hitl_gateway.request_approval(
                                goal_id=state.goal_id,
                                action=tool_ref.name,
                                risk_level=tool_risk,
                                tenant_ctx=tenant_ctx,
                            ))
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
                            pii_issues = self._guardrail_checker.check_output(output=raw_output_text)
                            if pii_issues:
                                await self._emit({
                                    "type": "pii_redacted",
                                    "tool": getattr(tool_call, "tool", "") if tool_call else "",
                                    "issues": pii_issues,
                                })
                                if self._audit_log is not None:
                                    try:
                                        self._audit_log.record(
                                            AuditEvent(
                                                goal_id=state.goal_id,
                                                tool_name="guardrail_checker",
                                                action_level=ActionLevel.ALLOW_LOG,
                                                outcome="pii_redacted",
                                                step_id=state.steps[-1].step_id if state.steps else "",
                                                api_key_id=getattr(tenant_ctx, "api_key_id", None) or "",
                                                note=f"issues_count={len(pii_issues)} step={step[:100]}",
                                            ),
                                            tenant_ctx=tenant_ctx,
                                        )
                                    except Exception:
                                        pass
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
            from app.reliability.tool_inverses import get_inverse_fn as _get_inverse_fn
            _rb_tool = tool_name
            _rb_args: dict[str, Any] = {}
            if tool_call is not None and tool_call.arguments:
                _rb_tool = tool_call.tool or tool_name
                _rb_args = dict(tool_call.arguments)
            self._rollback_engine.register(
                action=step,
                inverse=_get_inverse_fn(_rb_tool, _rb_args),
            )

        # 11. Decision trace for explainability — real LLM output (Task 6)
        _reasoning_text = raw_output[:500] if raw_output else "No output"
        if tool_call is not None and getattr(tool_call, "tool", None):
            _reasoning_text = f"Used tool '{tool_call.tool}': {raw_output[:300]}"
        trace = DecisionTrace(
            action=step,
            reasoning=_reasoning_text,
            evidence=[raw_output[:300]],
            alternatives=[],
            confidence=0.8,
        )
        state.context.setdefault("decision_traces", []).append(trace.to_dict())
        # Persist decision trace to DB
        if self._db_session_factory and hasattr(trace, "trace_id"):
            import asyncio as _asyncio
            _task = _asyncio.create_task(self._persist_decision_trace(trace, state, tenant_ctx))
            _task.add_done_callback(
                lambda t: (not t.cancelled() and t.exception()) and self._logger.warning(
                    "decision_trace_persist_failed", error=str(t.exception())
                )
            )
            # Hold a strong reference so the GC doesn't collect the task before it finishes
            self._background_tasks.add(_task)
            _task.add_done_callback(self._background_tasks.discard)

        # 12. Audit log
        if self._audit_log is not None:
            self._audit_log.record(
                AuditEvent(
                    goal_id=state.goal_id,
                    tool_name=tool_name,
                    action_level=ActionLevel.ALLOW_LOG,
                    outcome="step_complete",
                    step_id=state.steps[-1].step_id if state.steps else "",
                    api_key_id=getattr(tenant_ctx, "api_key_id", None) or "",
                    request_id=(
                        state.context.get("request_id")
                        or state.context.get("execution_context", {}).get("request_id")
                    ),
                ),
                tenant_ctx=tenant_ctx,
            )

        return raw_output

    async def _execute_step_with_cache(
        self, step: str, state: AgentState, tenant_ctx: TenantContext
    ) -> str:
        """Execute a step, returning a cached result when the semantic cache hits.

        Uses the Redis-backed async get/set API (not the in-process lookup/store API)
        so cache hits are shared across all workers/replicas for the same tenant.
        """
        _cache_embedding: list[float] | None = None
        if self._semantic_cache is not None and self._embedder is not None:
            try:
                from app.providers.base import EmbedRequest
                _cache_embed_resp = await self._embedder.embed(EmbedRequest(texts=[step]))
                _cache_embedding = (
                    _cache_embed_resp.embeddings[0] if _cache_embed_resp.embeddings else None
                )
                if _cache_embedding:
                    _sem_cache_hit = await self._semantic_cache.get(
                        query=step,
                        embedding=_cache_embedding,
                        tenant_id=tenant_ctx.tenant_id,
                    )
                    if _sem_cache_hit is not None:
                        await self._emit({"type": "cache_hit", "step": step})
                        return _sem_cache_hit
            except Exception:
                _cache_embedding = None

        raw_output = await self._execute_step(step, state, tenant_ctx)

        # Store result in Redis-backed cache for future cross-replica hits
        if self._semantic_cache is not None and _cache_embedding is not None:
            try:
                await self._semantic_cache.set(
                    query=step,
                    embedding=_cache_embedding,
                    response=raw_output,
                    tenant_id=tenant_ctx.tenant_id,
                )
            except Exception:
                pass

        return raw_output

    async def _node_verify(self, state: GraphState) -> dict[str, Any]:
        agent_state: AgentState = state["agent_state"]
        tenant_ctx: TenantContext = state["tenant_ctx"]

        agent_state.status = GoalStatus.VERIFYING
        summary = "\n".join(
            f"- {s.description}: {s.output}" for s in agent_state.steps[-5:]
        )
        # Resolve verifier model via model_router when available (Bug 3 fix)
        _verify_model = ""
        if self._model_router is not None:
            try:
                _verify_model = self._model_router.model_for("verification") or ""
            except Exception:
                pass
        req = CompletionRequest(
            messages=[
                Message(role="system", content=VERIFIER_SYSTEM),
                Message(
                    role="user",
                    content=f"Goal: {agent_state.goal}\nExecuted steps:\n{summary}",
                ),
            ],
            model=_verify_model,
        )
        with self._tracer.start_as_current_span("agentverse.verify") as span:
            span.set_attribute("verify.iteration", agent_state.iterations)
            _verify_start = time.monotonic()
            resp = await self._verifier.complete(req)
            record_verify_duration(time.monotonic() - _verify_start)
        # Bug 1 fix: use _parse_verifier_response which handles JSON and text formats
        parsed = _parse_verifier_response(resp.content)
        success: bool = bool(parsed.get("success", False))
        reason: str = self._sanitize_tool_raw_output(parsed.get("reason", ""))
        # Store retry flag for routing: True = can replan, False = permanently blocked
        retry: bool = bool(parsed.get("retry", True)) if not success else True
        agent_state.context["verification_retry"] = retry

        agent_state.verification_success = success
        agent_state.verification_feedback = reason
        await self._emit({"type": "verification_done", "success": success, "reason": reason})

        if success:
            # Record winning plan in execution memory (sync in-memory + async DB, BUG 2b fix)
            if self._exec_memory is not None:
                self._exec_memory.record(  # sync: immediate in-memory update
                    goal=agent_state.goal,
                    plan=agent_state.plan,
                    tenant_ctx=tenant_ctx,
                )
                # Async DB persistence — only when a DB session factory is available
                if self._db_session_factory is not None:
                    _em_task = asyncio.create_task(
                        self._exec_memory.record_async(
                            goal=agent_state.goal,
                            plan=agent_state.plan,
                            success=True,
                            tenant_id=tenant_ctx.tenant_id,
                            db=self._db_session_factory,
                        )
                    )
                    self._background_tasks.add(_em_task)
                    _em_task.add_done_callback(self._background_tasks.discard)

            # Auto-extract long-term learnings (sync in-memory + async DB, BUG 1 fix)
            if self._long_term_memory is not None:
                step_outputs = " ".join(
                    s.output[:100] for s in agent_state.steps if s.output
                )
                # Sync extract: immediate in-memory update (same-session recall)
                self._long_term_memory.extract_from_goal(
                    goal=agent_state.goal,
                    result=step_outputs,
                    goal_id=agent_state.goal_id,
                    tenant_ctx=tenant_ctx,
                )
                # Async DB persistence via extract_from_goal_async (BUG 1 fix)
                if self._db_session_factory is not None:
                    _ltm_task = asyncio.create_task(
                        self._long_term_memory.extract_from_goal_async(
                            goal=agent_state.goal,
                            result=step_outputs[:500],
                            tenant_ctx=tenant_ctx,
                            db=self._db_session_factory,
                            embedder=self._embedder,
                        )
                    )
                    self._background_tasks.add(_ltm_task)
                    _ltm_task.add_done_callback(
                        lambda t: t.exception() and self._logger.warning(
                            "ltm_persist_failed", error=str(t.exception())
                        )
                    )
                    _ltm_task.add_done_callback(self._background_tasks.discard)

            # Score the completed goal — persists eval to DB (BUG 3 fix)
            scorecard = None
            if self._eval_runner is not None:
                scorecard = await self._eval_runner.score_and_persist(
                    agent_state,
                    tenant_ctx,
                    provider=self._verifier,
                    db=self._db_session_factory,
                )
                agent_state.context["eval_scorecard"] = scorecard
            agent_state.status = GoalStatus.COMPLETE
            record_goal_completed(tenant_id=tenant_ctx.tenant_id)
            await self._emit({"type": "goal_complete"})

            # H-2: SelfOptimizerV2 result recording — feeds A/B experiment outcomes
            _self_opt_v2 = getattr(self._app_state, "self_optimizer_v2", None) if self._app_state else None
            if _self_opt_v2 and self._agent_id and isinstance(agent_state.context, dict):
                _arm = agent_state.context.get("_experiment_arm")
                if _arm:
                    _eval_scorecard = agent_state.context.get("eval_scorecard", {})
                    _eval_score: float | None = None
                    if hasattr(_eval_scorecard, "average_score"):
                        try:
                            _eval_score = float(_eval_scorecard.average_score())
                        except Exception:
                            pass
                    elif isinstance(_eval_scorecard, dict):
                        _eval_score_raw = _eval_scorecard.get("average_score")
                        if _eval_score_raw is not None:
                            try:
                                _eval_score = float(_eval_score_raw)
                            except Exception:
                                pass
                    if _eval_score is not None:
                        import asyncio as _asyncio
                        _v2_task = _asyncio.create_task(_self_opt_v2.record_result(
                            goal_id=agent_state.goal_id,
                            agent_id=self._agent_id,
                            arm_name=_arm,
                            eval_score=_eval_score,
                            cost_usd=float(agent_state.context.get("total_cost_usd", 0.0)),
                            tenant_id=tenant_ctx.tenant_id,
                        ))
                        self._background_tasks.add(_v2_task)
                        _v2_task.add_done_callback(self._background_tasks.discard)
        else:
            scorecard = None

        # Feed eval result back to PromptOptimizer for A/B learning (BUG 4 fix)
        if scorecard is not None and hasattr(agent_state, "context"):
            _planner_variant_id = agent_state.context.get("planner_variant_id")
            _avg_score = scorecard.average_score()
            _won = _avg_score >= 0.7
            if self._prompt_optimizer is not None and _planner_variant_id:
                try:
                    self._prompt_optimizer.record_result(
                        variant_id=_planner_variant_id,
                        eval_score=_avg_score,
                    )
                    _po_task = asyncio.create_task(
                        self._prompt_optimizer.persist_outcome(
                            _planner_variant_id, won=_won, db=self._db_session_factory
                        )
                    )
                    self._background_tasks.add(_po_task)
                    _po_task.add_done_callback(self._background_tasks.discard)
                except Exception:
                    pass

        # Trigger self-optimization when a goal scores poorly (BUG 5 fix)
        if (
            self._self_optimizer is not None
            and scorecard is not None
            and scorecard.average_score() < 0.5
        ):
            _so_task = asyncio.create_task(
                self._trigger_self_optimization(agent_state, scorecard, tenant_ctx)
            )
            self._background_tasks.add(_so_task)
            _so_task.add_done_callback(self._background_tasks.discard)

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

        # Bug 1 fix: when verifier says retry=False, permanently fail rather than replan
        _v_retry: bool = bool(agent_state.context.get("verification_retry", True))
        if not _v_retry:
            agent_state.status = GoalStatus.FAILED
            agent_state.error_message = (
                agent_state.verification_feedback
                or "Goal permanently failed: cannot be retried."
            )
            record_goal_failed(tenant_id=agent_state.tenant_ctx.tenant_id)
            return "max_iter"

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

        # Use reflection node on verify failure when reflection is enabled (Fix 2)
        if self._enable_reflection:
            return "reflect"
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
                # Extract civilization_id from initial_context for spawn tool support
                if initial_context and isinstance(initial_context, dict):
                    civ_id = initial_context.get("civilization_id")
                    if civ_id:
                        self._civilization_id = civ_id
                        self._civilization_spawn_enabled = True
                self._tenant_ctx_ref = tenant_ctx
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

    def _extract_tool_name(self, step: str, tool_calls_result: list | None = None) -> str:
        """Extract tool name — prefers structured tool_calls, then registry, then heuristic.

        Args:
            step: The step description text.
            tool_calls_result: Structured tool call dicts from resp.tool_calls, if available.

        Returns the first tool_name from tool_calls_result when provided (no text parsing).
        Falls back to checking self._tool_context.tools for a name that appears in the step,
        then to the module-level heuristic (_extract_tool_name).
        """
        # Prefer structured tool name (Task 1+3)
        if tool_calls_result:
            return tool_calls_result[0].get("tool_name", "llm_call")
        # Check known tool names from the tool registry
        tc = self._tool_context
        if tc is not None and hasattr(tc, "tools"):
            step_lower = step.lower()
            for _t in tc.tools:
                if hasattr(_t, "name") and _t.name.lower() in step_lower:
                    return _t.name
        # Final fallback to module-level heuristic
        return _extract_tool_name(step)

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

    async def _trigger_self_optimization(
        self, state: Any, scorecard: Any, tenant_ctx: Any
    ) -> None:
        """Trigger self-optimization when a goal scores poorly (BUG 5 fix).

        Called as a fire-and-forget task from ``_node_verify`` whenever a
        successfully-scored goal falls below the 0.5 average-score threshold.
        Suggestions are recorded internally and can be reviewed via the
        SelfOptimizer REST API.
        """
        try:
            suggestions = self._self_optimizer.analyze_and_suggest(
                goal=getattr(state, "goal", ""),
                scorecard=scorecard,
                error_log=getattr(state, "error_message", "") or "",
                tenant_ctx=tenant_ctx,
            )
            if suggestions:
                self._logger.info(
                    "self_optimization_suggestions",
                    count=len(suggestions),
                    goal_id=getattr(state, "goal_id", ""),
                    score=scorecard.average_score(),
                )
        except Exception as exc:
            self._logger.warning("self_optimization_trigger_failed", error=str(exc))

    async def _validate_plan_tools(
        self, steps: list[str], tenant_ctx: Any
    ) -> list[str]:
        """Warn about steps that reference unknown tools.

        Scans each step description for underscore-separated words that look
        like tool names (e.g. ``search_issues``, ``create_pr``) and checks
        them against the live MCP registry.  Returns a list of warning strings
        so the caller can log them without blocking plan execution.
        """
        if self._mcp_client is None:
            return []
        try:
            all_tools = await self._mcp_client.discover_all_tools(tenant_ctx=tenant_ctx)
            known = {t.name for t in all_tools}
            warnings: list[str] = []
            for step in steps:
                words = step.lower().split()
                for word in words:
                    if "_" in word and len(word) > 5 and word not in known:
                        warnings.append(
                            f"Step '{step[:50]}' may reference unknown tool '{word}'"
                        )
            return warnings
        except Exception:
            return []

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


def _parse_verifier_response(text: str) -> dict[str, Any]:
    """Parse verifier LLM response — handles both JSON and legacy text formats.

    JSON format (preferred — produced by updated VERIFIER_SYSTEM):
        {"success": true, "reason": "..."}
        {"success": false, "reason": "...", "retry": true}
        {"success": false, "reason": "...", "retry": false}

    Legacy text format (fallback for old/non-compliant responses):
        "SUCCESS: <reason>"
        "RETRY: <gap>"
        "FAIL: <reason>"
    """
    clean = re.sub(r"```(?:json)?\n?", "", text).strip()

    # 1. Try JSON first (preferred path after VERIFIER_SYSTEM update)
    try:
        obj: dict[str, Any] = json.loads(clean)
        return obj
    except json.JSONDecodeError:
        pass

    # 2. Parse legacy text formats: SUCCESS/RETRY/FAIL
    upper = clean.upper()
    if upper.startswith("SUCCESS"):
        reason = re.sub(r"^SUCCESS\s*[:\-]\s*", "", clean, flags=re.IGNORECASE)
        return {"success": True, "reason": reason, "retry": False}
    elif upper.startswith("RETRY"):
        reason = re.sub(r"^RETRY\s*[:\-]\s*", "", clean, flags=re.IGNORECASE)
        return {"success": False, "reason": reason, "retry": True}
    elif upper.startswith("FAIL"):
        reason = re.sub(r"^FAIL\s*[:\-]\s*", "", clean, flags=re.IGNORECASE)
        return {"success": False, "reason": reason, "retry": False}

    # 3. Unknown format — infer from negative keywords
    lower = clean.lower()
    inferred_success = not any(
        w in lower for w in ["fail", "error", "not ", "missing", "incomplete", "retry"]
    )
    return {"success": inferred_success, "reason": clean}


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
