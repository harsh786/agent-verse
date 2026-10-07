"""LangGraph StateGraph-based autonomous agent.

Graph topology:
  START → initialize → rag_retrieval → plan → execute → verify →
          (complete → END | replan → plan | max_iter → END | waiting_human → END)
          supervisor / execute → END while a fan-out parent waits for its sub-goals
          (waiting_children; the last sub-goal re-queues it)

Five nodes, each is an async function receiving the graph state dict and returning updates.
LangGraph merges the returned dict into the running state (reducer pattern).

Checkpointing via MemorySaver means every state transition is persisted in-memory;
a crashed goal can be resumed by re-invoking with the same thread_id.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import uuid
from collections.abc import Awaitable, Callable, Hashable
from typing import Any, cast

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.agent.checkpoint_resume import checkpoint_payload, restore_from_checkpoint
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
from app.reliability.goal_lifecycle import GoalCancelledError
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
from app.agent.risk_classifier import HIGH_RISK_VOCABULARY

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
_DEFAULT_MAX_ITERATIONS = 100
# The step-gate vocabulary that is high risk on its own; the gate itself is
# ``app.agent.risk_classifier.assess_step_risk`` (verbs + targets + goal intent).
_HIGH_RISK_KEYWORDS = HIGH_RISK_VOCABULARY


# GraphState and RetrievalEntryPointError now live in graph_types
# to avoid circular imports from mixin modules.
from app.agent.graph_types import GraphState, RetrievalEntryPointError  # noqa: E402


def _checkpointer_fallback(checkpointer: Any, reason: str) -> None:
    """Warn (log + metric) that an unusable checkpointer is replaced by the
    in-memory MemorySaver — goal state then does not survive the process."""
    from app.observability.logging import get_logger as _get_logger

    _get_logger(__name__).warning(
        "agent_checkpointer_fallback_to_memory",
        checkpointer=type(checkpointer).__name__,
        reason=reason,
    )
    with contextlib.suppress(Exception):
        from app.observability.metrics import record_checkpointer_fallback

        record_checkpointer_fallback(reason)


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
        # BK3 (D-20 follow-up): tenant-scoped knowledge-graph store, used to
        # produce graph_facts for the planner prompt. Optional — Any to avoid
        # a hard dependency on app.knowledge_graph from the agent loop.
        knowledge_graph_store: Any | None = None,
        # Prospective memory (deferred intentions/reminders). Optional — Any to
        # avoid a hard dependency; recall is surfaced into the planner context.
        prospective_service: Any | None = None,
        # Grantex governance: mandatory grant enforcement at the tool-execution
        # choke point. ``enforce_grants`` defaults False (pass-through) until a
        # deployment opts in; ``grant_store`` supplies an agent's grants.
        grant_store: Any | None = None,
        enforce_grants: bool = False,
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
        # Adaptive, model-capability-aware execution strategy (A/B/C). When on,
        # the plan/tool/latency strategy is resolved per model instead of one
        # fixed behavior for all models. Default-on but safe: an unknown or weak
        # model resolves to today's SEQUENTIAL + SINGLE behavior.
        enable_adaptive_strategy: bool = True,
        execution_strategy: Any | None = None,  # explicit override (tests / callers)
        fast_model_id: str = "",  # a low-latency model for Strategy C verifier routing
        capability_tracker: Any | None = None,  # P5 adaptivity (RedisCapabilityTracker)
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
        self._knowledge_graph_store = knowledge_graph_store
        self._prospective_service = prospective_service
        self._grant_store = grant_store
        self._enforce_grants = enforce_grants
        self._retrieval_gateway = retrieval_gateway
        self._mcp_client = mcp_client
        self._guardrail_checker = guardrail_checker
        self._eval_runner = eval_runner
        self._model_router: Any = model_router
        self._semantic_cache: Any = semantic_cache
        self._llm_response_cache: Any = llm_response_cache
        self._embedder: Any = embedder
        self._runtime_profile = runtime_profile
        # Built profile for scoring only (set by GoalService even when the rollout
        # keeps it from driving execution); see initialize_mixin.
        self._observed_runtime_profile: Any = None
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
        # WS-10: additionally AUTO-select supervisor/debate from the goal's own
        # characteristics (one reachable selector) when the default-off safety gate
        # is open. The explicit ctor flag remains an override that always wins.
        auto_multi_agent = self._auto_select_multi_agent(runtime_profile)
        self._auto_multi_agent = auto_multi_agent
        self._enable_supervisor = (
            enable_supervisor
            or "supervisor" in selected_strategy_ids
            or "supervisor" in auto_multi_agent
        )
        self._enable_debate = (
            enable_debate
            or "debate" in selected_strategy_ids
            or "debate" in auto_multi_agent
        )
        self._autonomy_mode = autonomy_mode
        # The selector's multi-agent pick may be goal_tree (expert goals); honour it like
        # supervisor/debate instead of recording a topology that never compiles in.
        self._enable_goal_tree = (
            enable_goal_tree
            or "goal_tree" in selected_strategy_ids
            or "goal_tree" in auto_multi_agent
        )
        self._goal_tree_threshold = goal_tree_threshold
        # Adaptive execution strategy (A/B/C). Resolved once from the wired
        # per-role model ids; adaptivity (P5) refines it per goal at plan time.
        self._enable_adaptive_strategy = enable_adaptive_strategy
        self._strategy_override = execution_strategy
        self._fast_model_id = fast_model_id or ""
        self._capability_tracker = capability_tracker
        self._execution_strategy = self._resolve_execution_strategy()
        self._hitl_timeout: float = 300.0
        self._checkpointer = checkpointer if checkpointer is not None else MemorySaver()
        # Ensure the checkpointer supports async — LangGraph's ainvoke requires
        # aget_tuple().  The sync RedisSaver doesn't implement it, causing
        # NotImplementedError inside the Celery worker.  Fall back to MemorySaver
        # which is always async-safe.
        # WF-15: the swap used to be silent, so a deployment could believe its
        # goals were durably checkpointed while nothing survived the process.
        try:
            import inspect

            _aget = getattr(self._checkpointer, "aget_tuple", None)
            if _aget is not None and not inspect.iscoroutinefunction(_aget):
                # Sync implementation — replace with in-memory checkpointer
                _checkpointer_fallback(self._checkpointer, "sync_only")
                self._checkpointer = MemorySaver()
        except Exception:
            _checkpointer_fallback(self._checkpointer, "inspection_failed")
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
        # Outcome of every tool call of the current execute pass (from the emitted
        # events): a failed tool result the verifier cannot overrule.
        from app.agent.tool_outcomes import ToolOutcomeLedger

        self._tool_outcomes: ToolOutcomeLedger = ToolOutcomeLedger()
        # OTel trace context injected by parent when spawned as sub-agent
        self._parent_trace_context: Any = None
        from opentelemetry import trace as _otel_trace

        self._tracer = _otel_trace.get_tracer(__name__)
        self._db_session_factory: Any = None  # Set by main.py after construction
        # Awaited at every step boundary; blocks while the goal is paused. Wired
        # by GoalService (in-process event + cross-replica Redis flag).
        self._pause_gate: Any = None
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

    def _role_model_id(self, provider: Any) -> str:
        """Best-effort model id for a wired role provider (planner/executor/verifier)."""
        return str(getattr(provider, "_default_model", "") or "")

    def _resolve_execution_strategy(self) -> Any:
        """Resolve the per-model execution strategy from the wired role models.

        Returns the safe SEQUENTIAL+SINGLE default when adaptivity is disabled or
        resolution fails, so this never changes behavior for unknown/weak models.
        """
        from app.agent.execution_strategy import ExecutionStrategy

        if self._strategy_override is not None:
            return self._strategy_override
        if not self._enable_adaptive_strategy:
            return ExecutionStrategy.safe_default("adaptive strategy disabled")
        try:
            from app.agent.execution_strategy import profile_for, resolve

            return resolve(
                planner=profile_for(self._role_model_id(self._planner)),
                executor=profile_for(self._role_model_id(self._executor)),
                verifier=profile_for(self._role_model_id(self._verifier)),
                fast_model_id=self._fast_model_id or None,
            )
        except Exception:  # pragma: no cover - defensive
            return ExecutionStrategy.safe_default("resolution error")

    async def _current_strategy(self, tenant_ctx: Any = None) -> Any:
        """The execution strategy for the current goal — chosen adaptively.

        Starts from the statically-seeded strategy, then lets *observation* win:
        the capability tracker's per-model success rates promote or demote plan/
        tool mode (bidirectional learning), and during cold-start it probes the
        richer strategy to discover a model's real capabilities. With no tracker
        wired it falls back to the static seed. Any error falls back safely.
        """
        base = self._execution_strategy
        tracker = getattr(self, "_capability_tracker", None)
        if (
            not self._enable_adaptive_strategy
            or self._strategy_override is not None
            or tracker is None
        ):
            return base
        try:
            from app.agent.execution_strategy import profile_for
            from app.agent.strategy_adaptivity import apply_exploration, refine_strategy

            planner_model = self._role_model_id(self._planner)
            executor_model = self._role_model_id(self._executor)
            tid = getattr(tenant_ctx, "tenant_id", None)
            s_rate = await tracker.rate(planner_model, tenant_id=tid, kind="structured")
            p_rate = await tracker.rate(executor_model, tenant_id=tid, kind="parallel")
            learned = refine_strategy(base, structured_ok_rate=s_rate, parallel_ok_rate=p_rate)
            return apply_exploration(
                learned,
                planner_profile=profile_for(planner_model),
                executor_profile=profile_for(executor_model),
                structured_rate_known=s_rate is not None,
                parallel_rate_known=p_rate is not None,
            )
        except Exception:  # pragma: no cover - defensive
            return base

    @staticmethod
    def _auto_select_multi_agent(runtime_profile: Any | None) -> frozenset[str]:
        """Consume the ONE selector's multi-agent decision for this goal.

        Returns an empty set unless the default-off ``agent_auto_multi_agent_enabled``
        safety gate is open AND the runtime profile carries goal properties.

        The multi-agent topology is decided once, by ``PatternSelector`` during
        profile assembly, and recorded in the DecisionTrace. This seam therefore
        *reads* that already-traced decision from ``profile.agent_patterns.multi_agent``
        rather than re-deriving it — so the pattern that runs is exactly the pattern
        that was surfaced. It falls back to the shared pure rule only when a caller
        passes a profile that carries goal properties but no assembled patterns.
        """
        props = getattr(runtime_profile, "properties", None)
        if props is None:
            return frozenset()
        try:
            from app.core.config import get_settings

            if not get_settings().agent_auto_multi_agent_enabled:
                return frozenset()
        except Exception:  # pragma: no cover - defensive; fail safe (no auto-select)
            return frozenset()

        # Preferred path: the unified, already-traced decision from profile assembly.
        agent_patterns = getattr(runtime_profile, "agent_patterns", None)
        selected = getattr(agent_patterns, "multi_agent", None)
        if selected:
            return frozenset(p for p in selected if p != "single_agent")

        # Fallback: re-derive from properties via the same shared rule (keeps any
        # execution seam identical even without a fully-assembled profile).
        from app.agent.multi_agent_selector import select_multi_agent_patterns

        def _val(name: str, default: str = "") -> str:
            raw = getattr(props, name, None)
            return str(getattr(raw, "value", raw) or default).lower()

        selection = select_multi_agent_patterns(
            complexity=_val("complexity"),
            domain=_val("domain"),
            multi_step=bool(getattr(props, "multi_step", True)),
            risk=_val("risk"),
        )
        return selection.patterns

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
        # A goal rejected at initialize (guardrails, fail-closed safety checks) ends
        # here: it used to flow on into retrieval, planning and execution, so a
        # rejected goal still ran its steps and only `_route` (after verify) stopped it.
        g.add_conditional_edges(
            "initialize",
            lambda s: (
                "rejected" if s.get("terminal_reason") == "guardrail_rejected" else "continue"
            ),
            {"rejected": END, "continue": "rag_retrieval"},
        )
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
            if _src == "supervisor":
                # A supervisor that parked for its sub-goals ends the run here
                # (waiting_children); its re-entry continues from the ledger.
                g.add_conditional_edges(
                    "supervisor",
                    self._route_after_fanout,
                    {"parked": END, "continue": _dst},
                )
            else:
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
                {"failed": END, "parked": END, "continue": "refine"},
            )
            g.add_edge("refine", _post_exec_target)
        else:
            g.add_conditional_edges(
                "execute",
                self._route_after_execute,
                {"failed": END, "parked": END, "continue": _post_exec_target},
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
        routing_map_edges = cast("dict[Hashable, str]", routing_map)
        if getattr(self, "_enable_peer_review", False):
            g.add_edge("verify", "peer_review")
            g.add_conditional_edges("peer_review", self._route, routing_map_edges)
        else:
            g.add_conditional_edges("verify", self._route, routing_map_edges)
        return g.compile(checkpointer=self._checkpointer)

    # ── Verify node — delegates to VerifierMixin ─────────────────────────

    async def _node_verify(self, state: GraphState) -> dict:  # type: ignore[override]
        """Verify step — should_skip_cache guard applied before LLM call.

        The LLM response cache check (should_skip_cache) is performed in
        VerifierMixin._node_verify before every expensive verification call.
        """
        return await super()._node_verify(state)  # should_skip_cache checked here

    # ── Lifecycle helpers (run, checkpoint, emit, etc.) ──────────────────

    async def _emergency_stop_reason(self, tenant_id: str, org_id: str | None) -> str | None:
        """Tenant/org emergency-stop check against the API's runtime Redis.

        Fails closed: a Redis error blocks the run ("could not be verified")
        instead of being read as "not stopped". With no app/Redis wired no stop
        can have been activated (activation refuses without Redis).
        """
        aps: Any = self._app_state
        if aps is None:
            return None
        state = getattr(aps, "state", aps)  # FastAPI app -> app.state
        from app.governance.emergency_stop import enforce_emergency_stop

        return await enforce_emergency_stop(getattr(state, "_redis", None), tenant_id, org_id)

    async def _learn_procedural_outcome(
        self, final: AgentState, tenant_ctx: TenantContext
    ) -> None:
        """MEM-11: fold a terminal goal's outcome into procedural memory.

        Runs once per goal for BOTH outcomes (it used to run only on success, so
        every stored success_rate was 1.0). Bounded and never fails the goal; a
        store failure is logged and flagged on the final state.
        """
        store = getattr(self, "_procedural_memory", None)
        if store is None or final.status not in (GoalStatus.COMPLETE, GoalStatus.FAILED):
            return
        try:
            await asyncio.wait_for(
                store.learn(
                    state=final,
                    tenant_ctx=tenant_ctx,
                    success=final.status == GoalStatus.COMPLETE,
                ),
                timeout=10.0,
            )
        except Exception as exc:
            final.context.setdefault("memory_degraded", []).append("procedural_learn")
            self._logger.warning(
                "procedural_learn_failed",
                goal_id=final.goal_id,
                error=f"{type(exc).__name__}: {str(exc)[:200]}",
            )

    async def _record_failed_experiment_goal(
        self, final: AgentState, tenant_ctx: TenantContext
    ) -> None:
        """a05-F087-02: a FAILED goal of a SelfOptimizerV2 experiment arm is recorded.

        The verify node records the arm only on success, so a candidate config
        that made goals fail was never penalised — its arm only ever saw the
        goals it got right. A terminal failure is recorded as a scored goal with
        ``eval_score`` 0.0. Awaited and bounded (a background task is cancelled
        by the worker loop's teardown); never fails the goal.
        """
        context = final.context if isinstance(final.context, dict) else None
        if (
            final.status != GoalStatus.FAILED
            or context is None
            or not context.get("_experiment_arm")
            or context.get("_experiment_outcome_recorded")
            or not self._agent_id
        ):
            return
        optimizer = getattr(self._app_state, "self_optimizer_v2", None) if self._app_state else None
        if optimizer is None:
            return
        context["_experiment_outcome_recorded"] = True
        try:
            await asyncio.wait_for(
                optimizer.on_goal_completed(
                    tenant_id=tenant_ctx.tenant_id,
                    agent_id=self._agent_id,
                    goal_id=final.goal_id,
                    eval_score=0.0,
                    cost_usd=float(context.get("total_cost_usd", 0.0) or 0.0),
                    latency_ms=0,
                ),
                timeout=10.0,
            )
        except Exception as exc:
            self._logger.warning(
                "experiment_failed_goal_record_failed",
                goal_id=final.goal_id,
                error=f"{type(exc).__name__}: {str(exc)[:200]}",
            )

    async def run(
        self,
        *,
        goal: str,
        tenant_ctx: TenantContext,
        initial_context: dict[str, Any] | None = None,
        event_callback: EventCallback | None = None,
        goal_id: str | None = None,
        # Persistence attempt number (>1 = a retry): isolates the LangGraph thread and
        # skips crash-resume so a retry never resumes the previous attempt's state.
        attempt: int | None = None,
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
        _run_bag_token = None
        if self._parent_trace_context is not None:
            ctx_token = otel_context.attach(self._parent_trace_context)

        try:
            _tracer = otel_trace.get_tracer(__name__)
            with _tracer.start_as_current_span("agentverse.goal.run") as span:
                span.set_attribute("goal.text", goal[:200])
                span.set_attribute("tenant.id", tenant_ctx.tenant_id)

                # Set run-correlation baggage so every child span (LangGraph nodes,
                # gen_ai generations, tool calls) is stamped with goal/conversation/
                # tenant — traces group per goal and Langfuse groups a conversation's
                # calls into a session. Detached in the finally below.
                with contextlib.suppress(Exception):
                    from opentelemetry import baggage as _otel_baggage

                    from app.observability.trace_propagation import (
                        BAGGAGE_CONVERSATION_ID,
                        BAGGAGE_GOAL_ID,
                        BAGGAGE_TENANT_ID,
                    )

                    _init = initial_context or {}
                    _conv = _init.get("conversation_id") or _init.get("session_id")
                    _bctx = _otel_baggage.set_baggage(BAGGAGE_TENANT_ID, tenant_ctx.tenant_id)
                    if goal_id:
                        _bctx = _otel_baggage.set_baggage(
                            BAGGAGE_GOAL_ID, str(goal_id), context=_bctx
                        )
                    if _conv:
                        _bctx = _otel_baggage.set_baggage(
                            BAGGAGE_CONVERSATION_ID, str(_conv), context=_bctx
                        )
                    _run_bag_token = otel_context.attach(_bctx)

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
                            from app.memory.dept_memory import (
                                DepartmentMemory,
                                get_dept_memory,
                            )

                            # MEM-14: the DB-wired store the org CRUD routes write
                            # to (a bare DepartmentMemory() was always empty), read
                            # for the goal's tenant. A worker process has no
                            # lifespan-wired singleton: use its session factory.
                            _dept_mem = get_dept_memory()
                            _graph_db = getattr(self, "_db_session_factory", None)
                            if _dept_mem._db_factory is None and _graph_db is not None:
                                _dept_mem = DepartmentMemory()
                                _dept_mem.set_db(_graph_db)
                            from app.memory.embedding import memory_embedder_from_provider

                            # MEM-42: semantic recall with the graph's embedder
                            # when the store has none (worker processes).
                            _mem_entries = await _dept_mem.retrieve(
                                dept_id, goal, top_k=6, tenant_id=tenant_ctx.tenant_id,
                                embedder=memory_embedder_from_provider(
                                    getattr(self, "_embedder", None)
                                ),
                            )
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
                _is_retry_attempt = attempt is not None and attempt > 1
                if _is_retry_attempt:
                    thread_id = f"{thread_id}-attempt-{attempt}"
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
                    if goal_id and not _is_retry_attempt:
                        checkpoint_state = await self._load_checkpoint(goal_id, tenant_ctx)
                        # This used to look for an "agent_state" key the writer
                        # never stored, so a goal re-delivered after a worker crash
                        # always started over and re-ran every side-effecting tool.
                        resumed = restore_from_checkpoint(
                            checkpoint_state,
                            input_state.get("agent_state"),
                            goal=goal,
                            tenant_ctx=tenant_ctx,
                            goal_id=goal_id,
                        )
                        if resumed is not None:
                            input_state["agent_state"] = resumed
                            input_state["iteration"] = max(0, resumed.iterations - 1)
                            from app.observability.logging import get_logger

                            get_logger(__name__).info(
                                "goal_resumed_from_checkpoint",
                                goal_id=goal_id,
                                steps_already_done=len(
                                    resumed.context.get("_resume_completed", {})
                                ),
                            )
                except Exception as _ckpt_exc:
                    # Fail closed: when the goal's checkpoints cannot be read we do
                    # not know which side-effecting steps already ran. Starting
                    # over (this used to be ``except: pass``) could re-run them —
                    # a duplicate email, ticket, payment. Fail the goal honestly;
                    # a redelivery retries once the store is readable.
                    from app.observability.logging import get_logger

                    get_logger(__name__).warning(
                        "checkpoint_resume_failed_closed",
                        goal_id=goal_id,
                        error=str(_ckpt_exc)[:200],
                    )
                    fail_state = input_state.get("agent_state") or AgentState(
                        goal=goal, tenant_ctx=tenant_ctx
                    )
                    if goal_id:
                        fail_state.goal_id = goal_id
                    fail_state.status = GoalStatus.FAILED
                    fail_state.error_message = (
                        "checkpoint_unavailable: the goal's checkpoints could not be read, "
                        "so it was not re-run (completed side-effecting steps could repeat)."
                    )
                    fail_state.context["terminal_reason"] = "checkpoint_unavailable"
                    if event_callback:
                        await self._emit(
                            {"type": "goal_failed", "reason": fail_state.error_message}
                        )
                    return fail_state

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
                except Exception as _stamp_exc:
                    # Latency scoring loses its start time; say so (was silent).
                    from app.observability.logging import get_logger

                    get_logger(__name__).warning(
                        "goal_start_latency_stamp_failed",
                        goal_id=goal_id,
                        error=str(_stamp_exc)[:200],
                    )

                # Emergency stop (tenant- or org-level). In-process API execution
                # used to check no flag at all; the worker checks the same keys.
                _stop_reason = await self._emergency_stop_reason(tenant_ctx.tenant_id, org_id)
                if _stop_reason:
                    stop_state = input_state.get("agent_state") or AgentState(
                        goal=goal, tenant_ctx=tenant_ctx
                    )
                    stop_state.status = GoalStatus.FAILED
                    stop_state.error_message = _stop_reason
                    if event_callback:
                        await self._emit({"type": "goal_failed", "reason": _stop_reason})
                    return stop_state

                try:
                    from app.providers.guarded_completion import goal_charge_scope

                    # Narrow-decision LLM calls made inside this run (guardrail
                    # judges, RAG graders, routers) are charged to this goal.
                    with goal_charge_scope(self, input_state.get("agent_state"), tenant_ctx):
                        result: dict[str, Any] = await self._graph.ainvoke(
                            input_state, config=config
                        )
                    final: AgentState = result.get("agent_state") or AgentState(
                        goal=goal, tenant_ctx=tenant_ctx
                    )
                    # Emit failure event if the loop ran out of iterations without success
                    if final.status == GoalStatus.FAILED and event_callback:
                        await self._emit({"type": "goal_failed", "reason": final.error_message})
                    await self._learn_procedural_outcome(final, tenant_ctx)
                    await self._record_failed_experiment_goal(final, tenant_ctx)
                    return final
                except PermissionError as exc:
                    # "Planning unavailable: ..." / "Verification unavailable: ..." come
                    # from the circuit breaker wrapping a downstream RuntimeError/TimeoutError
                    # in _node_plan / _node_verify — treat as a regular failure, not a
                    # governance denial (HITL rejections are handled inside the loop).
                    _exc_str = str(exc)
                    if _exc_str.startswith(("Planning unavailable:", "Verification unavailable:")):
                        err_state = AgentState(goal=goal, tenant_ctx=tenant_ctx)
                        err_state.status = GoalStatus.FAILED
                        err_state.error_message = _exc_str
                        if event_callback:
                            await self._emit({"type": "goal_failed", "reason": _exc_str})
                        return err_state
                    raise  # genuine governance denials surface to the caller
                except GoalCancelledError:
                    # An operator cancel observed at a step boundary (pause gate).
                    # It must reach the runner as a cancel — folding it into the
                    # generic handler below reported the goal as "failed".
                    raise
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
            if _run_bag_token is not None:
                with contextlib.suppress(Exception):
                    otel_context.detach(_run_bag_token)
            if ctx_token is not None:
                otel_context.detach(ctx_token)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _stream_with_failover(
        self, request: Any, on_token: Any, token_buffer: list[str]
    ) -> Any:
        """Executor LLM call with a timeout and ordered model failover.

        The executor used to call ``stream_tokens`` once, with no timeout: a hung
        endpoint hung the goal, and one bad model (a hosted reasoning model that
        returned empty completions) failed the goal while another configured
        model was healthy. Partial tokens from a failed attempt are discarded.
        """
        import dataclasses
        import os

        # Wall-clock cap per attempt. Generous by default so a long healthy
        # generation is not cut off; tune per deployment.
        timeout = float(os.getenv("AGENTVERSE_EXECUTOR_CALL_TIMEOUT_SECONDS", "300"))
        primary = getattr(request, "model", "") or ""
        models = [primary, *(m for m in self._role_fallback_models() if m != primary)]
        self._last_served_model = primary
        self._failed_models: list[str] = []
        last_exc: BaseException | None = None
        emitted = [0]

        async def _counting_on_token(chunk: str) -> None:
            emitted[0] += 1
            await on_token(chunk)

        from app.providers import circuit_breaker as _cb
        from app.providers.rate_limit import is_rate_limit_error, with_rate_limit_retry

        for i, model in enumerate(models):
            attempt = request if i == 0 else dataclasses.replace(request, model=model)
            # PROV-21: the same per-model circuit the non-streaming path uses — a
            # model whose circuit is open is skipped, outcomes feed the circuit.
            _key = _cb.breaker_key(self._executor, attempt)
            # The circuit is fleet-wide (a01-F023-02): a model another replica or
            # worker found broken is skipped here too.
            if await _cb.circuit_open_anywhere(_key):
                last_exc = _cb.ProviderCircuitOpenError(f"circuit open for {_key}")
                self._logger.warning("executor_model_circuit_open", model=model)
                continue
            emitted[0] = 0

            async def _stream_once(_attempt: Any = attempt, _key: str = _key) -> Any:
                admission = await _cb.admit_call(_key)
                recorded = False
                try:
                    out = await asyncio.wait_for(
                        self._executor.stream_tokens(_attempt, _counting_on_token),
                        timeout=timeout,
                    )
                    recorded = True
                    await _cb.report_success(admission)
                    return out
                except Exception as exc:
                    # A 429 is throttling, not a failure: it never trips the
                    # circuit (P5-1); it is retried below with backoff.
                    if (
                        not recorded
                        and getattr(exc, "provider_failure", True)
                        and not is_rate_limit_error(exc)
                    ):
                        recorded = True
                        await _cb.report_failure(admission)
                    raise
                finally:
                    if not recorded:
                        await _cb.report_no_verdict(admission)

            try:
                # Retry a throttled attempt only while no token of it reached the
                # client (a 429 arrives before the stream starts).
                resp = await with_rate_limit_retry(
                    _stream_once, label=_key, should_retry=lambda: emitted[0] == 0
                )
                self._last_served_model = model
                return resp
            except Exception as exc:
                last_exc = exc
                self._failed_models.append(model)
                token_buffer.clear()
                if emitted[0]:
                    # Tokens of the failed attempt already reached SSE clients;
                    # tell them to discard the partial text — before a retry, and
                    # also when this was the last model (the step then fails and
                    # the half-answer must not stay on screen as its output).
                    await self._emit(
                        {
                            "type": "token_reset",
                            "reason": "model_failover" if i + 1 < len(models) else "stream_failed",
                        }
                    )
                if i + 1 < len(models):
                    self._logger.warning(
                        "executor_model_failover",
                        from_model=model, to_model=models[i + 1], error=str(exc)[:200],
                    )
        if last_exc is None:  # pragma: no cover - models always has one entry
            raise RuntimeError("no executor model to call")
        raise last_exc

    def _routed_model(self, task_type: str, provider: Any) -> str:
        router = getattr(self, "_model_router", None)
        if router is not None:
            try:
                routed = router.model_for(task_type)
                if routed:
                    return str(routed)
            except Exception:
                pass
        return str(getattr(provider, "_default_model", "") or "")

    def _role_fallback_models(self) -> list[str]:
        """Other configured models an LLM role may fail over to, in preference order.

        The execution model first (on a mixed deployment typically the fast local
        model), then the verification model, then the operator's reasoning
        preference order, then the executor's own default.
        ``complete_with_failover`` skips whichever one is the primary.
        """
        candidates: list[str] = []
        router = getattr(self, "_model_router", None)
        if router is not None:
            for task in ("execution", "verification"):
                try:
                    candidates.append(router.model_for(task) or "")
                except Exception:
                    continue
        # The operator's reasoning preference order (Model Registry), so a
        # failing model falls over to the next one the operator ranked.
        try:
            from app.ai_router.selection import resolve_fallback_models

            candidates.extend(resolve_fallback_models("planning", "", limit=4))
        except Exception:
            pass
        candidates.append(getattr(getattr(self, "_executor", None), "_default_model", "") or "")
        role_map = getattr(router, "role_map", None) if router is not None else None
        if isinstance(role_map, dict) and role_map:
            from app.ai_router.deployment_roles import role_fallback_chain

            candidates.extend(role_fallback_chain(role_map))
        return [m for i, m in enumerate(candidates) if m and m not in candidates[:i]]

    async def _emit(self, event: dict[str, Any]) -> None:
        from datetime import UTC, datetime

        if "ts" not in event:
            event["ts"] = datetime.now(UTC).isoformat()
        from app.agent.tool_outcomes import ledger_for

        ledger_for(self).record(event)
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

    # Backoff between checkpoint write attempts (CORE-26): 1 try + len() retries.
    _CHECKPOINT_RETRY_DELAYS_S: tuple[float, ...] = (0.05, 0.25)

    async def _write_checkpoint(
        self, goal_id: str, step_index: int, state: Any, tenant_ctx: Any
    ) -> None:
        """Write step checkpoint to DB after each step; retried, never silently lost.

        CORE-26: a failed write used to be logged and ignored, so the durable
        record of a completed side-effecting step could be missing while the
        goal went on; a crash/redelivery then resumed from an older checkpoint
        and repeated the step. The upsert is retried with a short backoff; if
        it still fails the goal is flagged ``checkpoint_degraded`` (and a
        ``checkpoint_write_failed`` event is emitted), after which every
        non-read tool call fails closed (see ``_checkpoint_degraded``).
        """
        if self._db_session_factory is None or not getattr(self, "_checkpoints_enabled", True):
            return
        delays = tuple(getattr(self, "_CHECKPOINT_RETRY_DELAYS_S", ()))
        last_exc: Exception | None = None
        for attempt in range(len(delays) + 1):
            try:
                await self._write_checkpoint_once(goal_id, step_index, state, tenant_ctx)
                return
            except Exception as exc:
                last_exc = exc
                if attempt < len(delays):
                    await asyncio.sleep(delays[attempt])
        from app.observability.logging import get_logger

        get_logger(__name__).error(
            "checkpoint_write_failed",
            goal_id=goal_id,
            step_index=step_index,
            error=type(last_exc).__name__,
        )
        ctx = getattr(state, "context", None)
        if isinstance(ctx, dict):
            ctx["checkpoint_degraded"] = True
        with contextlib.suppress(Exception):
            await self._emit(
                {
                    "type": "checkpoint_write_failed",
                    "step_index": step_index,
                    "error_type": type(last_exc).__name__,
                    "effect": "side-effecting tool calls are refused for the rest of this run",
                }
            )

    async def _write_checkpoint_once(
        self, goal_id: str, step_index: int, state: Any, tenant_ctx: Any
    ) -> None:
        """One checkpoint upsert; raises on failure."""
        try:
            from datetime import UTC, datetime

            from app.db.models.goal import GoalCheckpoint
            from app.db.rls import sqlalchemy_rls_context

            payload = checkpoint_payload(state, step_index)
            payload["completed_at"] = datetime.now(UTC).isoformat()
            checkpoint_key = f"step_{step_index}"
            async with (
                self._db_session_factory() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
                # Upsert, not insert: the agent loop re-checkpoints the same step
                # on every replan, so a plain INSERT collides with
                # uq_goal_checkpoints_key (tenant_id, goal_id, checkpoint_key) and
                # the replanned state is never persisted (crash-recovery would then
                # restore a stale checkpoint). On Postgres use an atomic
                # ON CONFLICT DO UPDATE; elsewhere (SQLite unit tests) fall back to
                # a read-then-write — safe because a single worker owns a goal's
                # checkpoints, so there is no concurrent writer for the same key.
                dialect = session.bind.dialect.name if session.bind is not None else ""
                if dialect == "postgresql":
                    from sqlalchemy.dialects.postgresql import insert as pg_insert

                    stmt = pg_insert(GoalCheckpoint).values(
                        id=uuid.uuid4().hex,
                        goal_id=goal_id,
                        tenant_id=tenant_ctx.tenant_id,
                        checkpoint_key=checkpoint_key,
                        sequence=step_index,
                        payload=payload,
                        recovery_status="checkpointed",
                    )
                    stmt = stmt.on_conflict_do_update(
                        constraint="uq_goal_checkpoints_key",
                        set_={
                            "sequence": step_index,
                            "payload": payload,
                            "recovery_status": "checkpointed",
                            "updated_at": datetime.now(UTC),
                        },
                    )
                    await session.execute(stmt)
                else:
                    from sqlalchemy import select

                    existing = (
                        await session.execute(
                            select(GoalCheckpoint).where(
                                GoalCheckpoint.tenant_id == tenant_ctx.tenant_id,
                                GoalCheckpoint.goal_id == goal_id,
                                GoalCheckpoint.checkpoint_key == checkpoint_key,
                            )
                        )
                    ).scalar_one_or_none()
                    if existing is not None:
                        existing.sequence = step_index
                        existing.payload = payload
                        existing.recovery_status = "checkpointed"
                        existing.updated_at = datetime.now(UTC)
                    else:
                        session.add(
                            GoalCheckpoint(
                                goal_id=goal_id,
                                tenant_id=tenant_ctx.tenant_id,
                                checkpoint_key=checkpoint_key,
                                sequence=step_index,
                                payload=payload,
                                recovery_status="checkpointed",
                            )
                        )
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning(
                "checkpoint_write_attempt_failed", goal_id=goal_id, error=type(exc).__name__
            )
            raise

    async def _load_checkpoint(self, goal_id: str, tenant_ctx: Any) -> dict[str, Any] | None:
        """Load latest checkpoint for goal resume."""
        if self._db_session_factory is None or not getattr(self, "_checkpoints_enabled", True):
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
                    # Latest write wins, not the highest step index: a replan
                    # restarts step numbering, so step_0 of iteration 2 is newer
                    # than step_3 of iteration 1.
                    .order_by(GoalCheckpoint.updated_at.desc(), GoalCheckpoint.sequence.desc())
                    .limit(1)
                )
                row = result.scalar_one_or_none()
                return row.payload if row else None
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("checkpoint_load_failed", goal_id=goal_id, error=str(exc))
            # "Could not read" is not "no checkpoint": the caller fails closed.
            raise RuntimeError(f"checkpoint load failed: {exc}") from exc

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
            # MEM-26: persisted (tenant-scoped, awaited) so /suggestions is the
            # same on every replica and survives a restart.
            suggestions = await self._self_optimizer.analyze_and_persist(
                goal=getattr(state, "goal", ""),
                scorecard=scorecard,
                error_log=getattr(state, "error_message", "") or "",
                tenant_ctx=tenant_ctx,
                goal_id=str(getattr(state, "goal_id", "") or ""),
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
