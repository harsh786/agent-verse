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
import itertools
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.agent.state import AgentState, GoalStatus, StepStatus
from app.governance.audit import AuditLog
from app.governance.cost import CostController
from app.governance.hitl import HITLGateway
from app.governance.permissions import PermissionMatrix
from app.governance.policies import PolicyEngine
from app.intelligence.eval_runner import EvalRunner
from app.intelligence.guardrails import GuardrailChecker
from app.memory.execution import ExecutionMemory
from app.memory.long_term import LongTermMemoryStore
from app.providers.base import LLMProvider
from app.rag.store import KnowledgeStore
from app.reliability.circuit_breaker import CircuitBreaker
from app.reliability.dedup import DeduplicationCache
from app.reliability.result_processor import ResultProcessor
from app.reliability.rollback import RollbackEngine
from app.tenancy.context import TenantContext

# Guardrails 2.0 integration
try:
    from app.guardrails_v2.engine import guardrails_engine
    from app.guardrails_v2.models import GuardrailLayer

    _GUARDRAILS_AVAILABLE = True
except ImportError:
    _GUARDRAILS_AVAILABLE = False
    guardrails_engine = None  # type: ignore[assignment]
    GuardrailLayer = None  # type: ignore[assignment]


# ── Node module imports ─────────────────────────────────────────────────
from app.agent.nodes.executor_mixin import ExecutorMixin
from app.agent.nodes.initialize_mixin import InitializeMixin
from app.agent.nodes.planner_mixin import PlannerMixin
from app.agent.nodes.rag_mixin import RAGMixin
from app.agent.nodes.reasoning_mixin import ReasoningMixin
from app.agent.nodes.routing_mixin import RoutingMixin
from app.agent.nodes.verifier_mixin import VerifierMixin

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
_DEFAULT_MAX_ITERATIONS = 100
_HIGH_RISK_KEYWORDS = frozenset(
    ("deploy", "delete", "drop", "prod", "production", "destroy", "wipe", "truncate")
)
_RM_COMMAND_PATTERN = re.compile(r"\brm\b")


# GraphState and RetrievalEntryPointError now live in graph_types
# to avoid circular imports from mixin modules.
from app.agent.graph_types import GraphState, RetrievalEntryPointError

# ---------------------------------------------------------------------------
# AgentGraph — thin orchestrator, all node logic lives in nodes/
# ---------------------------------------------------------------------------


class AgentGraph(
    InitializeMixin,
    RAGMixin,
    ReasoningMixin,
    PlannerMixin,
    ExecutorMixin,
    VerifierMixin,
    RoutingMixin,
):
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
        retrieval_gateway: Any | None = None,
        mcp_client: Any | None = None,
        # Intelligence
        guardrail_checker: GuardrailChecker | None = None,
        eval_runner: EvalRunner | None = None,
        # Model routing
        model_router: Any | None = None,
        # Semantic cache + embedder
        semantic_cache: Any | None = None,
        llm_response_cache: Any | None = None,
        embedder: Any | None = None,
        # Phase 2 feature flags
        enable_cot: bool = False,
        enable_reflection: bool = False,
        # C3 fix: pattern flags for RuntimeProfileBuilder reasoning_patterns
        enable_self_refine: bool = False,
        enable_self_consistency: bool = False,
        enable_tree_of_thoughts: bool = False,
        enable_peer_review: bool = False,
        # D-1/D-2: multi-agent pattern flags (supervisor decomposition, debate voting)
        enable_supervisor: bool = False,
        enable_debate: bool = False,
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
        # Phase 3 Track C — citation-carrying synthesis
        answer_synthesizer: Any | None = None,
        # Phase 3 Track D — grounding, consensus, calibration
        grounding_checker: Any | None = None,
        consensus_verifier: Any | None = None,
        calibration_store: Any | None = None,
        tool_reliability_store: Any = None,
        episodic_memory: Any = None,
        procedural_memory: Any = None,
        reflexion_service: Any | None = None,
        runtime_profile: Any | None = None,
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
        self._retrieval_gateway = retrieval_gateway
        self._mcp_client = mcp_client
        self._guardrail_checker = guardrail_checker
        self._eval_runner = eval_runner
        self._model_router: Any = model_router
        self._semantic_cache: Any = semantic_cache
        self._llm_response_cache: Any = llm_response_cache
        self._embedder: Any = embedder
        self._runtime_profile = runtime_profile
        selected_strategy_ids = set()
        if runtime_profile is not None:
            selected_strategy_ids = {
                runtime_profile.primary_strategy.strategy_id,
                *(item.strategy_id for item in runtime_profile.auxiliary_strategies),
            }
        self._enable_cot = enable_cot or "chain_of_thought" in selected_strategy_ids
        self._enable_reflection = enable_reflection or "reflection" in selected_strategy_ids
        # C3 fix: pattern flags
        self._enable_self_refine = enable_self_refine or "self_refine" in selected_strategy_ids
        self._enable_self_consistency = (
            enable_self_consistency or "self_consistency" in selected_strategy_ids
        )
        self._enable_tree_of_thoughts = (
            enable_tree_of_thoughts or "tree_of_thoughts" in selected_strategy_ids
        )
        self._enable_peer_review = enable_peer_review or "peer_review" in selected_strategy_ids
        # D-1/D-2: multi-agent patterns — enabled per-agent via ctor flag or a
        # runtime-profile strategy id, mirroring the other optional reasoning nodes.
        self._enable_supervisor = enable_supervisor or "supervisor" in selected_strategy_ids
        self._enable_debate = enable_debate or "debate" in selected_strategy_ids
        self._autonomy_mode = autonomy_mode
        self._enable_goal_tree = enable_goal_tree
        self._goal_tree_threshold = goal_tree_threshold
        self._hitl_timeout: float = 300.0
        self._checkpointer = checkpointer if checkpointer is not None else MemorySaver()
        # Ensure the checkpointer supports async — LangGraph's ainvoke requires
        # aget_tuple().  The sync RedisSaver doesn't implement it, causing
        # NotImplementedError inside the Celery worker.  Fall back to MemorySaver
        # which is always async-safe.
        try:
            import inspect

            _aget = getattr(self._checkpointer, "aget_tuple", None)
            if _aget is not None and not inspect.iscoroutinefunction(_aget):
                # Sync implementation — replace with in-memory checkpointer
                self._checkpointer = MemorySaver()
        except Exception:
            self._checkpointer = MemorySaver()
        self._bulkhead_registry = bulkhead_registry
        self._cost_tracker = cost_tracker
        self._step_callback = step_callback
        self._answer_synthesizer = answer_synthesizer
        self._grounding_checker = grounding_checker
        self._consensus_verifier = consensus_verifier
        self._calibration_store = calibration_store
        self._tool_reliability_store = tool_reliability_store
        self._episodic_memory = episodic_memory
        self._procedural_memory = procedural_memory
        self._reflexion_service = reflexion_service
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
        self._self_optimizer: Any = None  # Settable from outside; SelfOptimizer instance
        self._app_state: Any = None  # Set externally by goal_service; FastAPI app
        self._agent_id: str | None = None  # Set externally by goal_service
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
        # Lock protecting concurrent mutations of shared state (e.g. total_cost_usd)
        # during parallel-wave execution.
        self._state_lock = asyncio.Lock()
        from app.observability.logging import get_logger as _get_logger

        self._logger = _get_logger(__name__)

    @property
    def runtime_profile(self) -> Any | None:
        return self._runtime_profile

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
        # H7: Tree of thoughts node — fires before planning
        if getattr(self, "_enable_tree_of_thoughts", False):
            g.add_node("tree_of_thoughts", self._node_tree_of_thoughts)
        g.add_node("plan", self._node_plan)
        g.add_node("execute", self._node_execute)
        # H7: Self-consistency node — fires after execute
        if getattr(self, "_enable_self_consistency", False):
            g.add_node("self_consistency", self._node_self_consistency)
        g.add_node("verify", self._node_verify)
        # Add reflect node when reflection enabled (Fix 2)
        if self._enable_reflection:
            g.add_node("reflect", self._node_reflect)
        # H1: Self-refine node — fires after execute, before verify
        if getattr(self, "_enable_self_refine", False):
            g.add_node("refine", self._node_refine)
        # H7: Peer review node — fires after verify, before routing
        if getattr(self, "_enable_peer_review", False):
            g.add_node("peer_review", self._node_peer_review)
        # H8: Supervisor and debate conditional node stubs
        if getattr(self, "_enable_supervisor", False):
            g.add_node("supervisor", self._node_supervisor_check)
        if getattr(self, "_enable_debate", False):
            g.add_node("debate", self._node_debate)

        g.add_edge(START, "initialize")
        g.add_edge("initialize", "rag_retrieval")
        # Pre-plan reasoning chain: rag_retrieval → [think] → [tree_of_thoughts] →
        # [supervisor] → [debate] → plan. Each optional node is inserted only when
        # enabled, so the default path stays rag_retrieval → plan.
        pre_plan_chain: list[str] = ["rag_retrieval"]
        if self._enable_cot:
            pre_plan_chain.append("think")
        if getattr(self, "_enable_tree_of_thoughts", False):
            pre_plan_chain.append("tree_of_thoughts")
        if getattr(self, "_enable_supervisor", False):
            pre_plan_chain.append("supervisor")
        if getattr(self, "_enable_debate", False):
            pre_plan_chain.append("debate")
        pre_plan_chain.append("plan")
        for _src, _dst in itertools.pairwise(pre_plan_chain):
            g.add_edge(_src, _dst)
        g.add_edge("plan", "execute")
        # H1 + H7: execute → [refine] → [self_consistency] → verify
        _post_exec_target = "verify"
        if getattr(self, "_enable_self_consistency", False):
            _post_exec_target = "self_consistency"
            g.add_edge("self_consistency", "verify")
        if getattr(self, "_enable_self_refine", False):
            g.add_conditional_edges(
                "execute",
                self._route_after_execute,
                {"failed": END, "continue": "refine"},
            )
            g.add_edge("refine", _post_exec_target)
        else:
            g.add_conditional_edges(
                "execute",
                self._route_after_execute,
                {"failed": END, "continue": _post_exec_target},
            )
        # Reflection: reflect → plan edge so re-plan follows reflection
        if self._enable_reflection:
            g.add_edge("reflect", "plan")

        # rag_remediate node: re-retrieves missing context then falls back to plan
        g.add_node("rag_remediate", self._node_rag_remediate)
        g.add_edge("rag_remediate", "plan")

        routing_map: dict[str, Any] = {
            "complete": END,
            "replan": "plan",
            "max_iter": END,
            "waiting_human": END,
            "rag_remediate": "rag_remediate",
        }
        if self._enable_reflection:
            routing_map["reflect"] = "reflect"
        # H7: Peer review fires after verify, before routing decision
        if getattr(self, "_enable_peer_review", False):
            g.add_edge("verify", "peer_review")
            g.add_conditional_edges("peer_review", self._route, routing_map)
        else:
            g.add_conditional_edges("verify", self._route, routing_map)
        return g.compile(checkpointer=self._checkpointer)

    # ── Verify node — delegates to VerifierMixin ─────────────────────────

    async def _node_verify(self, state: GraphState) -> dict:  # type: ignore[override]
        """Verify step — should_skip_cache guard applied before LLM call.

        The LLM response cache check (should_skip_cache) is performed in
        VerifierMixin._node_verify before every expensive verification call.
        """
        return await super()._node_verify(state)  # should_skip_cache checked here

    # ── Lifecycle helpers (run, checkpoint, emit, etc.) ──────────────────

    async def run(
        self,
        *,
        goal: str,
        tenant_ctx: TenantContext,
        initial_context: dict[str, Any] | None = None,
        event_callback: EventCallback | None = None,
        goal_id: str | None = None,
        # ── Org context (Integration Point 2: Org OS → AgentGraph wiring) ──
        # When an org mission dispatches a goal these carry department, role, and
        # mission identity so the agent's memory and knowledge access are scoped
        # correctly (dept_memory, knowledge_access_policy, RBAC).
        org_id: str | None = None,
        dept_id: str | None = None,
        role_id: str | None = None,
        team_id: str | None = None,
        mission_id: str | None = None,
    ) -> AgentState:
        """Execute the agent graph and return the final AgentState.

        ``goal_id`` — when provided (e.g. the Celery task's external goal_id),
        this value is injected into the AgentState so that all DB writes
        (evaluations, decision_traces, checkpoints) reference the correct row
        in the ``goals`` table and do not violate the FK constraint.
        """
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

                # ── Org context injection ─────────────────────────────────────
                # When dispatched from an OrgMission, enrich initial_context with
                # org metadata and pre-fetch department memory so the planner and
                # executor know their organisational role and prior decisions.
                if org_id or dept_id or mission_id:
                    _org_ctx: dict[str, Any] = dict(initial_context or {})
                    if org_id:
                        _org_ctx["org_id"] = org_id
                        span.set_attribute("org.id", org_id)
                    if dept_id:
                        _org_ctx["dept_id"] = dept_id
                        span.set_attribute("org.dept_id", dept_id)
                    if role_id:
                        _org_ctx["role_id"] = role_id
                        span.set_attribute("org.role_id", role_id)
                    if team_id:
                        _org_ctx["team_id"] = team_id
                        span.set_attribute("org.team_id", team_id)
                    if mission_id:
                        _org_ctx["mission_id"] = mission_id
                        span.set_attribute("org.mission_id", mission_id)

                    # Retrieve department memory and prepend to context so the
                    # planner has access to lessons learned, SOPs, and decisions.
                    if dept_id:
                        try:
                            from app.memory.dept_memory import DepartmentMemory

                            _dept_mem = DepartmentMemory()
                            _mem_entries = await _dept_mem.retrieve(dept_id, goal, top_k=6)
                            if _mem_entries:
                                # MemoryEntry has no `category`; `tags` is the
                                # analogous categorization field. (Reading a
                                # non-existent attribute here previously raised
                                # AttributeError that the broad except silently
                                # swallowed, so dept memory was never injected
                                # whenever entries existed — the case that matters.)
                                _org_ctx["dept_memory"] = [
                                    {
                                        "content": e.content,
                                        "confidence": e.confidence,
                                        "tags": e.tags,
                                    }
                                    for e in _mem_entries
                                ]
                                span.set_attribute("org.dept_memory_entries", len(_mem_entries))
                        except Exception as _dm_exc:
                            # Non-fatal: proceed without dept memory, but log so a
                            # silent regression in this path is visible.
                            self._logger.warning(
                                "dept_memory_injection_failed",
                                dept_id=dept_id,
                                error=str(_dm_exc),
                            )

                    initial_context = _org_ctx

                self._event_callback = event_callback
                # Extract civilization_id from initial_context for spawn tool support
                if initial_context and isinstance(initial_context, dict):
                    civ_id = initial_context.get("civilization_id")
                    if civ_id:
                        self._civilization_id = civ_id
                        self._civilization_spawn_enabled = True
                    # Extract org context passed via execution_context from GoalService
                    # (org_id, dept_id, mission_id etc. are forwarded through initial_context)
                    _ec_org_id = initial_context.get("org_id")
                    _ec_dept_id = initial_context.get("dept_id")
                    _ec_mission_id = initial_context.get("mission_id")
                    if _ec_org_id and not org_id:
                        org_id = str(_ec_org_id)
                        span.set_attribute("org.id", org_id)
                    if _ec_dept_id and not dept_id:
                        dept_id = str(_ec_dept_id)
                        span.set_attribute("org.dept_id", dept_id)
                    if _ec_mission_id and not mission_id:
                        mission_id = str(_ec_mission_id)
                        span.set_attribute("org.mission_id", mission_id)
                self._tenant_ctx_ref = tenant_ctx
                thread_id = f"goal-{goal_id}" if goal_id else uuid.uuid4().hex
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
                    # Inject external goal_id so evaluations/decision_traces FK succeeds
                    if goal_id:
                        seed.goal_id = goal_id
                    input_state["agent_state"] = seed
                elif goal_id:
                    # No initial_context but caller supplied a goal_id: seed a minimal state
                    seed = AgentState(goal=goal, tenant_ctx=tenant_ctx)
                    seed.goal_id = goal_id
                    input_state["agent_state"] = seed

                # H2: Attempt to resume from checkpoint if available
                try:
                    if goal_id:
                        checkpoint_state = await self._load_checkpoint(goal_id, tenant_ctx)
                        if checkpoint_state is not None:
                            saved_state = checkpoint_state.get("agent_state")
                            if (
                                saved_state is not None
                                and hasattr(saved_state, "steps")
                                and saved_state.steps
                            ):
                                input_state = checkpoint_state
                                from app.observability.logging import get_logger

                                get_logger(__name__).info(
                                    "goal_resumed_from_checkpoint",
                                    goal_id=goal_id,
                                    steps_already_done=len(saved_state.steps),
                                )
                except Exception:
                    pass  # checkpoint load never blocks execution

                # N3: Track goal start time for latency scoring.
                # Stamp the current monotonic time into agent_state.context so that
                # _node_verify can compute _latency_ms before RuntimeScorecard.score().
                try:
                    import time as _time_mod

                    _goal_start_ms = _time_mod.monotonic() * 1000
                    _as_ref = input_state.get("agent_state")
                    if _as_ref is None:
                        # Ensure an AgentState exists even when no initial_context/goal_id
                        _as_ref = AgentState(goal=goal, tenant_ctx=tenant_ctx)
                        if goal_id:
                            _as_ref.goal_id = goal_id
                        input_state["agent_state"] = _as_ref
                    _as_ref.context["_goal_start_ms"] = _goal_start_ms
                except Exception:
                    pass

                try:
                    result: dict[str, Any] = await self._graph.ainvoke(input_state, config=config)
                    final: AgentState = result.get("agent_state") or AgentState(
                        goal=goal, tenant_ctx=tenant_ctx
                    )
                    # Emit failure event if the loop ran out of iterations without success
                    if final.status == GoalStatus.FAILED and event_callback:
                        await self._emit({"type": "goal_failed", "reason": final.error_message})
                    return final
                except PermissionError as exc:
                    # "Planning unavailable: ..." comes from the circuit breaker wrapping
                    # a downstream RuntimeError in _node_plan — treat as a regular failure,
                    # not a governance denial (HITL rejections are handled inside the loop).
                    _exc_str = str(exc)
                    if _exc_str.startswith("Planning unavailable:"):
                        err_state = AgentState(goal=goal, tenant_ctx=tenant_ctx)
                        err_state.status = GoalStatus.FAILED
                        err_state.error_message = _exc_str
                        if event_callback:
                            await self._emit({"type": "goal_failed", "reason": _exc_str})
                        return err_state
                    raise  # genuine governance denials surface to the caller
                except Exception as exc:
                    if isinstance(exc, RetrievalEntryPointError):
                        failed_state = input_state.get("agent_state")
                        if isinstance(failed_state, AgentState):
                            if event_callback:
                                await self._emit(
                                    {
                                        "type": "goal_failed",
                                        "reason": failed_state.error_message,
                                    }
                                )
                            return failed_state
                    import logging as _logging

                    _logging.getLogger(__name__).warning(
                        "agentgraph_run_exception type=%s msg=%r",
                        type(exc).__name__,
                        str(exc)[:200],
                        exc_info=True,
                    )
                    err_state = AgentState(goal=goal, tenant_ctx=tenant_ctx)
                    err_state.status = GoalStatus.FAILED
                    err_state.error_message = str(exc) or f"{type(exc).__name__} (no message)"
                    if event_callback:
                        await self._emit({"type": "goal_failed", "reason": err_state.error_message})
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

    def _check_stuck_loop(
        self,
        state: AgentState,
        window: int = 3,
    ) -> bool:
        """Return True when the last *window* steps are all FAILED.

        Signals a stuck execution loop that should trigger an early replan rather
        than burning the remaining iteration budget.
        """
        recent = [s for s in state.steps if hasattr(s, "status")][-window:]
        if len(recent) < window:
            return False
        return all(getattr(s, "status", None) == StepStatus.FAILED for s in recent)

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
            async with (
                self._db_session_factory() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
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

            get_logger(__name__).warning("checkpoint_write_failed", goal_id=goal_id, error=str(exc))

    async def _load_checkpoint(self, goal_id: str, tenant_ctx: Any) -> dict[str, Any] | None:
        """Load latest checkpoint for goal resume."""
        if self._db_session_factory is None:
            return None
        try:
            from sqlalchemy import select

            from app.db.models.goal import GoalCheckpoint
            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db_session_factory() as session,
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
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

            get_logger(__name__).warning("checkpoint_load_failed", goal_id=goal_id, error=str(exc))
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

    async def _persist_decision_trace(self, trace: Any, state: Any, tenant_ctx: Any) -> None:
        """Persist decision trace record to DB (fire-and-forget via create_task)."""
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db_session_factory() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
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

    async def _trigger_self_optimization(self, state: Any, scorecard: Any, tenant_ctx: Any) -> None:
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

    async def _validate_plan_tools(self, steps: list[str], tenant_ctx: Any) -> list[str]:
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
                        warnings.append(f"Step '{step[:50]}' may reference unknown tool '{word}'")
            return warnings
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


# Re-export helpers for backward compatibility with existing imports
# (tests and other modules may import these directly from app.agent.graph)
from app.agent.nodes._helpers import (  # noqa: E402
    _extract_tool_name,
)
