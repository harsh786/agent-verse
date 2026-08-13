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

from app.agent.prompts import (
    CHAIN_OF_THOUGHT_SYSTEM,
    EXECUTOR_SYSTEM,
    PLANNER_SYSTEM,
    REFLECTION_SYSTEM,
    STRUCTURED_PLANNER_SYSTEM,
    VERIFIER_SYSTEM,
)
from app.agent.sanitization import (
    _EXECUTOR_CONTEXT_MAX_LENGTH,
    sanitize_event,
    sanitize_event_value,
    sanitize_tool_event_value,
    sanitize_tool_raw_output,
)
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus, SubGoal
from app.agent.tool_calls import ToolCall, extract_tool_call, repair_tool_call_arguments
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
from app.providers.circuit_breaker import call_with_circuit_breaker
from app.rag.contracts import RAGExecutionResult, RAGStrategy, resolve_rag_strategy
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

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
_DEFAULT_MAX_ITERATIONS = 100
_HIGH_RISK_KEYWORDS = frozenset(
    ("deploy", "delete", "drop", "prod", "production", "destroy", "wipe", "truncate")
)
_RM_COMMAND_PATTERN = re.compile(r"\brm\b")


def _is_high_risk_step(step: str) -> bool:
    lowered = step.lower()
    return any(keyword in lowered for keyword in _HIGH_RISK_KEYWORDS) or bool(
        _RM_COMMAND_PATTERN.search(lowered)
    )


class RetrievalEntryPointError(RuntimeError):
    """A required, tenant-scoped retrieval leg failed."""


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
    reasoning_evidence: dict[str, Any]  # aggregate-only, privacy-safe evidence


# ---------------------------------------------------------------------------
# AgentGraph
# ---------------------------------------------------------------------------


def _build_verifier_summary(steps: list) -> str:
    """Build a rich step summary for the verifier LLM.

    Always includes ALL steps that had failures (TOOL FAILED or STEP ERROR)
    regardless of position. Appends the last 5 steps for recency context.
    Deduplication prevents failed steps in the last 5 from appearing twice.
    Steps marked UNGROUNDED are highlighted so the verifier treats them as failures.
    """

    def _step_line(s: Any) -> str:
        parts = [f"- {getattr(s, 'description', '?')}: {getattr(s, 'output', '')}"]
        # Ungrounded step marker — must appear before tool/error markers
        if getattr(s, "status", None) is not None:
            from app.agent.state import StepStatus as _SS
            if s.status == _SS.UNGROUNDED:
                parts.append(
                    "  [UNGROUNDED CLAIM] Step output contains claims not found in tool outputs"
                )
        for tc in getattr(s, "tool_calls", []) or []:
            if not (tc.get("success", True)):
                parts.append(
                    f"  [TOOL FAILED] {tc.get('tool_name', '?')}: "
                    f"{tc.get('error', 'unknown error')}"
                )
        if getattr(s, "error", None):
            parts.append(f"  [STEP ERROR] {s.error}")
        return "\n".join(parts)

    # All failed/ungrounded steps anywhere in the run
    failed = [
        s for s in steps
        if getattr(s, "error", None)
        or any(
            not tc.get("success", True)
            for tc in (getattr(s, "tool_calls", []) or [])
        )
        or (
            getattr(s, "status", None) is not None
            and _is_ungrounded_status(s.status)
        )
    ]

    last_five = steps[-5:]
    last_five_ids = {id(s) for s in last_five}
    early_failures = [s for s in failed if id(s) not in last_five_ids]

    parts: list[str] = []
    if early_failures:
        parts.append("FAILED STEPS (occurred before final 5 steps):")
        parts.extend(_step_line(s) for s in early_failures)
        parts.append("")

    if last_five:
        parts.append("MOST RECENT STEPS:")
        parts.extend(_step_line(s) for s in last_five)

    return "\n".join(parts) if parts else "(no steps executed)"


def _is_ungrounded_status(status: Any) -> bool:
    """Return True when *status* equals ``StepStatus.UNGROUNDED`` without a hard import."""
    return str(status) == "ungrounded"


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
                *(
                    item.strategy_id
                    for item in runtime_profile.auxiliary_strategies
                ),
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
        self._autonomy_mode = autonomy_mode
        self._enable_goal_tree = enable_goal_tree
        self._goal_tree_threshold = goal_tree_threshold
        self._hitl_timeout: float = 300.0
        self._checkpointer = checkpointer if checkpointer is not None else MemorySaver()
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
        # H7: CoT + tree_of_thoughts: rag_retrieval → [think] → [tree_of_thoughts] → plan
        if getattr(self, "_enable_tree_of_thoughts", False):
            if self._enable_cot:
                g.add_edge("rag_retrieval", "think")
                g.add_edge("think", "tree_of_thoughts")
            else:
                g.add_edge("rag_retrieval", "tree_of_thoughts")
            g.add_edge("tree_of_thoughts", "plan")
        elif self._enable_cot:
            g.add_edge("rag_retrieval", "think")
            g.add_edge("think", "plan")
        else:
            g.add_edge("rag_retrieval", "plan")
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
            "complete": END, "replan": "plan", "max_iter": END, "waiting_human": END,
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

        if self._runtime_profile is not None:
            agent_state.context["_runtime_profile"] = self._runtime_profile

        # H23-H26: Security profiles — compute per-goal identity + action safety context
        try:
            from app.security_runtime.governance_profile import GovernanceProfileSelector
            from app.security_runtime.identity_profile import IdentityResolver
            _id_resolver = IdentityResolver()
            _identity = _id_resolver.resolve(tenant_ctx=agent_state.tenant_ctx)
            agent_state.context["_identity_scope"] = _identity.identity_scope.value

            _runtime_profile_ctx = agent_state.context.get("_runtime_profile")
            if _runtime_profile_ctx is not None:
                _gov_selector = GovernanceProfileSelector()
                _gov_profile = _gov_selector.select(
                    _runtime_profile_ctx, tenant_ctx=agent_state.tenant_ctx
                )
                agent_state.context["_governance_bundle"] = _gov_profile.name.value
        except Exception:
            pass

        # Build source inventory for planner awareness (M1d)
        try:
            from app.rag.agentic.source_inventory import SourceInventory
            _kb = getattr(self, "_knowledge_store", None)
            if _kb is not None:
                inventory = SourceInventory(knowledge_store=_kb)
                sources = await inventory.build(tenant_ctx=agent_state.tenant_ctx)
                agent_state.context["_source_inventory"] = sources.to_dict()
        except Exception:
            pass

        return {"agent_state": agent_state, "iteration": 0, "rag_context": ""}

    async def _node_rag_retrieval(self, state: GraphState) -> dict[str, Any]:
        agent_state: AgentState = state["agent_state"]
        tenant_ctx: TenantContext = state["tenant_ctx"]
        context_parts: list[str] = []

        # H3: Check if a specific RAG strategy was assembled by the orchestration layer
        _rag_strategy = agent_state.context.get("_rag_strategy_override")
        if _rag_strategy is None:
            _runtime_profile = agent_state.context.get("_runtime_profile")
            if _runtime_profile is not None:
                _rag_strategy = getattr(
                    getattr(_runtime_profile, "rag_strategy", None), "strategy", None
                )
        requested_strategy_id = str(
            _rag_strategy
            if _rag_strategy is not None
            else agent_state.context.get(
                "retrieval_strategy",
                RAGStrategy.HYBRID.value,
            )
        )
        try:
            resolved_strategy = resolve_rag_strategy(requested_strategy_id)
        except Exception as exc:
            agent_state.status = GoalStatus.FAILED
            agent_state.error_message = "Invalid retrieval strategy"
            agent_state.context["rag_retrieval_status"] = "failed"
            failure_event = {
                "type": "knowledge_retrieval_failed",
                "collections_searched": list(self._agent_collection_ids[:3]),
                "requested_strategy_id": requested_strategy_id,
                "status": "failed",
                "citations": [],
                "resolved_strategy_ids": [],
                "retrieval_legs": [],
                "strategy_trace": [],
            }
            agent_state.events.append(failure_event)
            await self._emit(failure_event)
            raise RetrievalEntryPointError("Invalid retrieval strategy") from exc
        agent_state.context["_requested_rag_strategy_id"] = requested_strategy_id
        agent_state.context["_active_rag_strategy"] = resolved_strategy.value
        agent_state.context["retrieval_strategy"] = resolved_strategy.value

        # N6e: chunking_strategy_selected SSE
        try:
            if self._event_callback is not None and _rag_strategy:
                from app.observability.runtime_decision_trace import RuntimeSSEEmitter
                _sse_cs = RuntimeSSEEmitter()
                await self._emit(_sse_cs.chunking_strategy_selected(
                    goal_id=agent_state.goal_id,
                    content_type="text",
                    strategy=_rag_strategy or "semantic",
                    reason="configured canonical strategy",
                ))
        except Exception:
            pass

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

        # 3. Required collection retrieval through the tenant-aware gateway.
        search_collections = list(self._agent_collection_ids[:3])
        if not search_collections and self._knowledge_store is not None:
            all_collections = await self._knowledge_store.list_collections_async(
                tenant_ctx=tenant_ctx
            )
            search_collections = [collection.collection_id for collection in all_collections[:3]]

        if search_collections:
            app_state = getattr(self._app_state, "state", self._app_state)
            gateway = self._retrieval_gateway or getattr(
                app_state, "retrieval_gateway", None
            )
            requested_strategy = str(
                agent_state.context.get("_requested_rag_strategy_id")
                or agent_state.context.get("retrieval_strategy")
                or RAGStrategy.HYBRID.value
            )
            raw_top_k = agent_state.context.get("retrieval_top_k", 3)
            retrieval_filters = agent_state.context.get("retrieval_filters", {})

            try:
                if gateway is None:
                    raise RuntimeError("Retrieval gateway is not configured")
                if (
                    not isinstance(raw_top_k, int)
                    or isinstance(raw_top_k, bool)
                    or raw_top_k < 1
                ):
                    raise ValueError("Invalid retrieval top_k")
                if not isinstance(retrieval_filters, dict):
                    raise TypeError("Invalid retrieval filters")
                canonical_strategy = resolve_rag_strategy(requested_strategy)
                agent_state.context["retrieval_strategy"] = canonical_strategy.value
                agent_state.context["_active_rag_strategy"] = canonical_strategy.value
                gateway_results: list[RAGExecutionResult] = []
                for collection_id in search_collections:
                    gateway_result = await gateway.execute(
                        tenant_ctx,
                        collection_id=collection_id,
                        query=agent_state.goal,
                        strategy_id=requested_strategy,
                        top_k=raw_top_k,
                        filters=retrieval_filters,
                        execution_id=agent_state.goal_id,
                    )
                    if not isinstance(gateway_result, RAGExecutionResult):
                        raise TypeError("Retrieval gateway returned an invalid result")
                    gateway_results.append(gateway_result)
            except Exception as exc:
                agent_state.status = GoalStatus.FAILED
                agent_state.error_message = "Required retrieval failed"
                agent_state.context["rag_retrieval_status"] = "failed"
                failure_event = {
                    "type": "knowledge_retrieval_failed",
                    "collections_searched": search_collections,
                    "requested_strategy_id": requested_strategy,
                    "status": "failed",
                    "citations": [],
                    "resolved_strategy_ids": [],
                    "retrieval_legs": [],
                    "strategy_trace": [],
                }
                agent_state.events.append(failure_event)
                await self._emit(failure_event)
                self._logger.warning(
                    "required_rag_retrieval_failed",
                    error_type=type(exc).__name__,
                    strategy=requested_strategy,
                )
                raise RetrievalEntryPointError("Required retrieval failed") from exc

            knowledge_citations = [
                {
                    **citation.model_dump(mode="json"),
                    "collection_id": collection_id,
                }
                for collection_id, result in zip(
                    search_collections, gateway_results, strict=True
                )
                for citation in result.citations
            ]
            retrieval_legs = [
                leg.model_dump(mode="json")
                for result in gateway_results
                for leg in result.retrieval_legs
            ]
            strategy_trace = [
                trace.model_dump(mode="json")
                for result in gateway_results
                for trace in result.strategy_trace
            ]
            resolved_strategy_ids = sorted(
                {result.resolved_strategy_id.value for result in gateway_results}
            )
            knowledge_contexts = [
                (
                    f"[Collection: {citation['collection_id']}, "
                    f"source: {citation['source']}, score: {citation['score']:.2f}]\n"
                    f"{citation['content'][:600]}"
                )
                for citation in knowledge_citations
            ]
            context_parts.extend(knowledge_contexts)
            agent_state.context["rag_knowledge"] = "\n\n".join(knowledge_contexts)
            agent_state.context["rag_citations"] = knowledge_citations
            agent_state.context["rag_requested_strategy_id"] = requested_strategy
            agent_state.context["rag_resolved_strategy_ids"] = resolved_strategy_ids
            agent_state.context["rag_retrieval_legs"] = retrieval_legs
            agent_state.context["rag_strategy_trace"] = strategy_trace
            agent_state.context["rag_retrieval_status"] = "complete"
            agent_state.context["retrieval_attempted"] = True
            average_confidence = (
                sum(float(item["score"]) for item in knowledge_citations)
                / len(knowledge_citations)
                if knowledge_citations
                else 0.0
            )
            agent_state.context["runtime_retrieval_evidence"] = {
                "source": "knowledge_base",
                "confidence": average_confidence,
            }
            agent_state.context["retrieval_evidence_ref"] = (
                f"goal:{agent_state.goal_id}:rag"
            )
            agent_state.provenance.extend(knowledge_citations)
            success_event = {
                "type": "knowledge_retrieved",
                "collections_searched": search_collections,
                "chunks_found": len(knowledge_citations),
                "citations": knowledge_citations,
                "requested_strategy_id": requested_strategy,
                "resolved_strategy_ids": resolved_strategy_ids,
                "retrieval_legs": retrieval_legs,
                "strategy_trace": strategy_trace,
            }
            agent_state.events.append(success_event)
            await self._emit(success_event)

        rag_context = "\n\n".join(context_parts)

        # ── RAGTrace: record retrieval for observability ───────────────────────
        try:
            from app.rag.agentic.rag_trace import RAGTrace
            _rag_trace = RAGTrace(goal_id=agent_state.goal_id, tenant_id=tenant_ctx.tenant_id)
            _strategy = agent_state.context.get("_active_rag_strategy", "hybrid")
            _rag_trace.record_retrieval(
                strategy=_strategy,
                query=agent_state.goal[:200],
                result_count=len(context_parts),
                confidence=0.7,
                latency_ms=0,
            )
            if self._event_callback is not None:
                await self._emit(_rag_trace.to_sse_event())
        except Exception:
            pass

        # H21: Emit rag_strategy_selected SSE
        try:
            if self._event_callback is not None:
                from app.observability.runtime_decision_trace import RuntimeSSEEmitter
                _sse = RuntimeSSEEmitter()
                _strategy = agent_state.context.get("_active_rag_strategy") or agent_state.context.get(
                    "retrieval_strategy", "hybrid"
                )
                await self._emit(_sse.rag_strategy_selected(
                    goal_id=agent_state.goal_id,
                    strategy=str(_strategy),
                    sources=[],
                    reranker="score",
                ))
        except Exception:
            pass

        return {"rag_context": rag_context}

    async def _node_rag_prime(self, state: GraphState) -> dict:
        """Alias for _node_rag_retrieval — spec-required name (doc-2 §9.1).

        Fires comprehensive first retrieval across all available sources before planning.
        Builds source_inventory for planner awareness. Activates web_search when KB is empty.
        """
        return await self._node_rag_retrieval(state)

    async def _node_rag_remediate(self, state: GraphState) -> dict:
        """Targeted re-retrieval when verification fails due to context gap (doc-2 §9.2).

        Triggered by _route() when verification_feedback contains gap signals:
          "insufficient", "unclear", "no information", "cannot determine",
          "lack of context", "not mentioned", "unknown"

        Strategy:
        1. Extract missing topic from verification_feedback
        2. Search with BROADER strategy (web if KB was empty before)
        3. Inject as [Remediation context: iteration N] into next plan
        4. Increment remediation_count (max 2 to prevent loops)
        """
        agent_state = state.get("agent_state")
        if agent_state is None:
            return {}

        if not agent_state.context.get("allow_rag_remediation", False):
            return {}

        try:
            from app.rag.agentic.context_gap_detector import ContextGapDetector
            from app.rag.agentic.retriever_tool import RetrieverTool

            feedback = agent_state.verification_feedback or ""
            detector = ContextGapDetector()
            missing_topic = detector.extract_missing_topic(feedback) or agent_state.goal[:100]
            app_state = getattr(self._app_state, "state", self._app_state)
            gateway = self._retrieval_gateway or getattr(app_state, "retrieval_gateway", None)
            strategy = RAGStrategy(
                str(agent_state.context.get("retrieval_strategy", RAGStrategy.HYBRID.value))
            )
            tool = RetrieverTool(retrieval_gateway=gateway)
            result = await tool.retrieve(
                query=missing_topic,
                tenant_ctx=agent_state.tenant_ctx,
                strategy=strategy,
                collection_ids=list(self._agent_collection_ids),
                top_k=7,
                min_confidence=0.2,
                execution_id=agent_state.goal_id,
            )

            count = agent_state.context.get("remediation_count", 0) + 1
            agent_state.context["remediation_count"] = count
            agent_state.context["remediation_context"] = (
                f"[Remediation context — iteration {count}]\n"
                f"Query: {missing_topic}\n"
                f"Source: {result.source} (confidence={result.confidence:.2f})\n\n"
                f"{result.context_text[:2000]}"
            )

        except Exception as exc:
            agent_state.status = GoalStatus.FAILED
            agent_state.error_message = "Required retrieval remediation failed"
            agent_state.context["rag_remediation_status"] = "failed"
            event = {
                "type": "knowledge_retrieval_failed",
                "status": "failed",
                "phase": "remediation",
                "requested_strategy_id": str(
                    agent_state.context.get(
                        "retrieval_strategy",
                        RAGStrategy.HYBRID.value,
                    )
                ),
                "citations": [],
                "resolved_strategy_ids": [],
                "retrieval_legs": [],
                "strategy_trace": [],
            }
            agent_state.events.append(event)
            await self._emit(event)
            self._logger.warning(
                "rag_remediate_failed",
                error_type=type(exc).__name__,
            )
            raise RetrievalEntryPointError(
                "Required retrieval remediation failed"
            ) from exc

        return {"agent_state": agent_state}

    async def _node_refine(self, state: GraphState) -> dict:
        """Self-Refine node — improves last step output before verification (doc-1 §3.4).

        Different from Reflection (which diagnoses a FAILURE).
        Self-Refine improves a SUCCESS — makes a good output better.

        Activated when 'self_refine' is in PatternConfig.reasoning_patterns.
        Fires AFTER execute, BEFORE verify, at most max_refine_iterations times.
        """
        agent_state = state.get("agent_state")
        if agent_state is None:
            return {}

        try:
            from app.agent.prompts import SELF_REFINE_SYSTEM
            from app.providers.base import CompletionRequest, Message

            if not agent_state.steps:
                return {"agent_state": agent_state}

            last_step = agent_state.steps[-1]
            if not last_step.output or not last_step.output.strip():
                return {"agent_state": agent_state}

            refine_iterations = agent_state.context.get("refine_iterations", 0)
            max_refine = agent_state.context.get("max_refine_iterations", 2)
            if refine_iterations >= max_refine:
                return {"agent_state": agent_state}

            refine_prompt = (
                f"Task: {last_step.description}\n\n"
                f"Current output:\n{last_step.output[:2000]}\n\n"
                "Improve this output following the review checklist."
            )

            resp = await self._executor.complete(
                CompletionRequest(
                    messages=[
                        Message(role="system", content=SELF_REFINE_SYSTEM),
                        Message(role="user", content=refine_prompt),
                    ],
                    model="",
                    max_tokens=2000,
                    temperature=0.0,
                )
            )

            refined = resp.content.strip() if resp.content else ""
            if refined and not refined.startswith("NO_CHANGES_NEEDED"):
                last_step.output = refined
                agent_state.context["refine_iterations"] = refine_iterations + 1
            evidence_status = "completed" if refined else "degraded"
            agent_state.context.setdefault("reasoning_evidence", []).append(
                {
                    "strategy_id": "self_refine",
                    "adapter_version": "1.0.0",
                    "status": evidence_status,
                    "call_count": 1,
                    "round": refine_iterations + 1,
                    "changed": bool(
                        refined and not refined.startswith("NO_CHANGES_NEEDED")
                    ),
                }
            )

        except Exception as exc:
            try:
                from app.observability.logging import get_logger
                get_logger(__name__).warning("node_refine_failed", error=str(exc))
            except Exception:
                pass

        return {"agent_state": agent_state}

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
        try:
            resp = await call_with_circuit_breaker(
                self._planner, "complete", req,
                provider_name=type(self._planner).__name__,
            )
        except RuntimeError as cb_exc:
            raise PermissionError(f"Planning unavailable: {cb_exc}") from cb_exc
        # The provider's private reasoning is intentionally discarded. Only
        # aggregate execution evidence is checkpointed or exposed.
        agent_state.context.setdefault("reasoning_evidence", []).append(
            {
                "strategy_id": "chain_of_thought",
                "adapter_version": "1.0.0",
                "status": "completed" if resp.content else "degraded",
                "call_count": 1,
                "safe_rationale_summary": "deliberate reasoning phase completed",
            }
        )
        return {
            "agent_state": agent_state,
            "reasoning_evidence": agent_state.context["reasoning_evidence"][-1],
        }

    async def _node_reflect(self, state: GraphState) -> dict[str, Any]:
        """Reflection node: diagnoses failure and populates verification_feedback."""
        agent_state: AgentState = state["agent_state"]
        reflection_attempts = int(agent_state.context.get("reflection_attempts", 0))
        max_reflections = self._max_reflection_rounds()
        if reflection_attempts >= max_reflections:
            agent_state.context.setdefault("reasoning_evidence", []).append(
                {
                    "strategy_id": "reflection",
                    "adapter_version": "1.0.0",
                    "status": "exhausted",
                    "call_count": 0,
                    "limit_reason": "reflection_round_limit",
                }
            )
            return {"agent_state": agent_state}
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
        try:
            resp = await call_with_circuit_breaker(
                self._planner, "complete", req,
                provider_name=type(self._planner).__name__,
            )
        except RuntimeError as cb_exc:
            raise PermissionError(f"Planning unavailable: {cb_exc}") from cb_exc
        from app.agent.reasoning_evidence import critique_categories

        categories = critique_categories(resp.content or "")
        agent_state.context.setdefault(
            "original_verification_evidence", agent_state.verification_feedback
        )
        agent_state.context["reflection_attempts"] = reflection_attempts + 1
        agent_state.verification_feedback = (
            "Reflection identified categories: " + ", ".join(categories)
        )
        agent_state.context.setdefault("reasoning_evidence", []).append(
            {
                "strategy_id": "reflection",
                "adapter_version": "1.0.0",
                "status": "completed",
                "call_count": 1,
                "critique_categories": list(categories),
                "attempt": reflection_attempts + 1,
            }
        )
        return {"agent_state": agent_state}

    # ------------------------------------------------------------------
    # H7: Advanced reasoning pattern nodes
    # ------------------------------------------------------------------

    async def _node_self_consistency(self, state: GraphState) -> dict[str, Any]:
        """Self-Consistency: sample N responses, return majority-vote answer."""
        agent_state: AgentState = state.get("agent_state")
        if agent_state is None or not agent_state.steps:
            return {}
        try:
            from app.agent.patterns.self_consistency import SelfConsistencyPattern
            last_step = agent_state.steps[-1]
            if not last_step.output:
                return {"agent_state": agent_state}
            pattern = SelfConsistencyPattern(n_samples=3)
            execution = await pattern.execute_with_evidence(
                prompt=f"Goal: {agent_state.goal}\nCurrent answer: {last_step.output}",
                provider=self._executor,
                call_limit=self.runtime_profile.effective_limits.calls
                if self.runtime_profile is not None
                else None,
            )
            refined = str(execution.result)
            agent_state.context.setdefault("reasoning_evidence", []).append(
                execution.evidence.model_dump(mode="json")
            )
            if refined and refined != last_step.output:
                last_step.output = refined
                agent_state.context["self_consistency_applied"] = True
        except Exception as exc:
            try:
                from app.observability.logging import get_logger
                get_logger(__name__).warning("node_self_consistency_failed", error=str(exc))
            except Exception:
                pass
        return {"agent_state": agent_state}

    async def _node_tree_of_thoughts(self, state: GraphState) -> dict[str, Any]:
        """Tree of Thoughts: deliberate reasoning over solution space."""
        agent_state: AgentState = state.get("agent_state")
        if agent_state is None:
            return {}
        try:
            from app.agent.patterns.tree_of_thoughts import TreeOfThoughtsPattern
            pattern = TreeOfThoughtsPattern(n_thoughts=3, max_depth=2)
            execution = await pattern.execute_with_evidence(
                problem=agent_state.goal,
                provider=self._planner,
            )
            answer = str(execution.result)
            agent_state.context.setdefault("reasoning_evidence", []).append(
                execution.evidence.model_dump(mode="json")
            )
            if answer:
                agent_state.context["tot_answer"] = answer
                agent_state.context["tree_of_thoughts_applied"] = True
        except Exception as exc:
            try:
                from app.observability.logging import get_logger
                get_logger(__name__).warning("node_tree_of_thoughts_failed", error=str(exc))
            except Exception:
                pass
        return {"agent_state": agent_state}

    async def _node_peer_review(self, state: GraphState) -> dict[str, Any]:
        """Peer Review: independent LLM review of current output quality."""
        agent_state: AgentState = state.get("agent_state")
        if agent_state is None or not agent_state.steps:
            return {}
        try:
            from app.agent.patterns.peer_review import PeerReviewPattern
            last_step = agent_state.steps[-1]
            if not last_step.output:
                return {"agent_state": agent_state}
            pattern = PeerReviewPattern(quality_threshold=0.7)
            role_assignments = (
                dict(self.runtime_profile.model_role_assignments)
                if self.runtime_profile is not None
                else {}
            )
            # Legacy graphs still have distinct executor/verifier roles even
            # when no runtime profile supplies concrete model identities.
            # Preserve that logical separation; explicit profiles remain the
            # source of truth and can reject an actual same-model assignment.
            producer_identity = role_assignments.get("executor", "executor-role")
            reviewer_identity = role_assignments.get("reviewer", "verifier-role")
            execution = await pattern.execute_with_evidence(
                output=last_step.output,
                goal=agent_state.goal,
                provider=self._verifier,
                producer_identity=producer_identity,
                reviewer_identity=reviewer_identity,
            )
            review = execution.result
            agent_state.context.setdefault("reasoning_evidence", []).append(
                execution.evidence.model_dump(mode="json")
            )
            agent_state.context["peer_review_score"] = review.quality_score
            agent_state.context["peer_review_approved"] = review.approved
            if not review.approved:
                agent_state.verification_feedback = (
                    f"Peer review rejected output at score {review.quality_score:.2f}; "
                    f"categories: {', '.join(execution.evidence.critique_categories)}"
                )
                agent_state.verification_success = False
        except Exception as exc:
            try:
                from app.observability.logging import get_logger
                get_logger(__name__).warning("node_peer_review_failed", error=str(exc))
            except Exception:
                pass
        return {"agent_state": agent_state}

    # ------------------------------------------------------------------
    # H8: Supervisor / debate node stubs
    # ------------------------------------------------------------------

    async def _node_supervisor_check(self, state: GraphState) -> dict[str, Any]:
        """Supervisor check stub — delegates to app.agent.supervisor when available."""
        agent_state: AgentState = state.get("agent_state")
        try:
            from app.observability.logging import get_logger
            get_logger(__name__).warning(
                "supervisor_node_stub_invoked",
                goal_id=getattr(agent_state, "goal_id", None),
            )
        except Exception:
            pass
        return {"agent_state": agent_state} if agent_state is not None else {}

    async def _node_debate(self, state: GraphState) -> dict[str, Any]:
        """Debate node stub — delegates to app.agent.debate when available."""
        agent_state: AgentState = state.get("agent_state")
        try:
            from app.observability.logging import get_logger
            get_logger(__name__).warning(
                "debate_node_stub_invoked",
                goal_id=getattr(agent_state, "goal_id", None),
            )
        except Exception:
            pass
        return {"agent_state": agent_state} if agent_state is not None else {}

    async def _node_plan(self, state: GraphState) -> dict[str, Any]:
        agent_state: AgentState = state["agent_state"]
        tenant_ctx: TenantContext = state["tenant_ctx"]
        rag_context: str = state.get("rag_context", "")
        iteration: int = state.get("iteration", 0) + 1

        agent_state.status = GoalStatus.PLANNING
        agent_state.iterations = iteration

        # ── ContextPipeline processing ─────────────────────────────────────────
        try:
            from app.context.context_pipeline import ContextPipeline
            from app.context.rerank_policy import RerankStrategy
            runtime_profile = agent_state.context.get("_runtime_profile")
            rerank_strategy = RerankStrategy.SCORE
            if runtime_profile is not None:
                reranker_name = getattr(runtime_profile.rag_strategy, "reranker", "score")
                try:
                    rerank_strategy = RerankStrategy(reranker_name)
                except ValueError:
                    pass
            retrieved_chunks = agent_state.context.get("_retrieved_chunks", [])
            if not retrieved_chunks and rag_context:
                retrieved_chunks = [{"content": rag_context, "score": 0.7, "chunk_id": "rag_0"}]
            if retrieved_chunks:
                pipeline = ContextPipeline(max_tokens=6000, rerank_strategy=rerank_strategy)
                reflexion_lessons = agent_state.context.get("_reflexion_lessons", [])
                pipeline_result = pipeline.run(
                    chunks=retrieved_chunks,
                    query=agent_state.goal,
                    goal_context=agent_state.goal,
                    reflexion_lessons=reflexion_lessons,
                )
                if pipeline_result.planner_context:
                    rag_context = pipeline_result.planner_context
                    agent_state.context["_pipeline_citations"] = [
                        {"index": c.index, "url": c.source_url}
                        for c in pipeline_result.citations
                    ]
                # N9: Store executor and verifier contexts for downstream nodes
                if pipeline_result.executor_context:
                    agent_state.context["_executor_context"] = pipeline_result.executor_context
                if pipeline_result.verifier_context:
                    agent_state.context["_verifier_context"] = pipeline_result.verifier_context
        except Exception as _ctx_exc:
            from app.observability.logging import get_logger
            get_logger(__name__).warning("context_pipeline_failed_in_plan", error=str(_ctx_exc))
        # ── end ContextPipeline ────────────────────────────────────────────────

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

        # ── Schema-Aware Prompt Injection ──────────────────────────────────────
        # Inject full JSON schemas for available tools so the LLM uses exact
        # parameter names and never halluccinates argument keys.
        try:
            _tool_ctx = agent_state.context.get("tool_context")
            if _tool_ctx is not None:
                _tools = getattr(_tool_ctx, "tools", []) or []
                if _tools:
                    from app.mcp.tool_intelligence import SchemaAwarePromptInjector
                    _schema_block = SchemaAwarePromptInjector.build_tool_schema_block(_tools)
                    if _schema_block:
                        extra_parts.append(_schema_block)
                        extra_parts.append(
                            SchemaAwarePromptInjector.build_tool_call_format_reminder()
                        )
        except Exception:
            pass  # Never block execution on schema injection failure

        # Inject civilization blackboard context (shared agent findings)
        blackboard_ctx: str = agent_state.context.get("blackboard_context", "")
        if blackboard_ctx:
            extra_parts.append(
                f"[Civilization blackboard — shared knowledge from other agents]\n{blackboard_ctx}"
            )

        # Append any visual/image context from perception pipeline
        image_context = agent_state.context.get("image_context", "")
        if image_context:
            extra_parts.append(f"[Visual context]\n{image_context}")

        if agent_state.verification_feedback:
            extra_parts.append(
                f"[Previous attempt feedback]\n{agent_state.verification_feedback}"
            )

        if state.get("reasoning_evidence"):
            extra_parts.append(
                "[Reasoning mode]\nUse deliberate decomposition; private reasoning is not retained."
            )

        # ── Skill selection ────────────────────────────────────────────────────
        try:
            from app.agent.skill_selector import SkillSelector
            _skill_sel = SkillSelector()
            _selected_skills = _skill_sel.select(
                agent_state.goal, max_skills=2, max_tokens=400
            )
            if _selected_skills:
                _skills_block = _skill_sel.build_skills_context(_selected_skills)
                extra_parts.append(_skills_block)
                # Narrow tool selection to skill's allowed_tools if specified
                _skill_tools: set[str] = set()
                for _sk in _selected_skills:
                    _skill_tools.update(_sk.allowed_tools)
                if _skill_tools:
                    agent_state.context["skill_allowed_tools"] = list(_skill_tools)
        except Exception:
            pass  # skills must never block execution

        # OutputContractBuilder — add output format constraint to planner prompt (M5c)
        try:
            from app.context.output_contract_builder import OutputContractBuilder
            _ocb = OutputContractBuilder()
            _contract = _ocb.build(goal=agent_state.goal)
            if _contract.instructions:
                extra_parts.append(f"[Output contract]\n{_contract.instructions}")
        except Exception:
            pass

        # Episodic memory recall — similar past experiences
        try:
            if self._episodic_memory is not None:
                _ep_episodes = await self._episodic_memory.recall(
                    goal=agent_state.goal,
                    tenant_id=tenant_ctx.tenant_id,
                    limit=3,
                )
                if _ep_episodes:
                    _ep_ctx = self._episodic_memory.format_for_context(_ep_episodes)
                    if _ep_ctx:
                        extra_parts.append(_ep_ctx)
        except Exception:
            pass

        # Procedural memory recall — relevant skills
        try:
            if self._procedural_memory is not None:
                _proc_skills = await self._procedural_memory.recall(
                    goal=agent_state.goal,
                    tenant_id=tenant_ctx.tenant_id,
                    limit=2,
                )
                if _proc_skills:
                    _proc_ctx = self._procedural_memory.format_for_context(_proc_skills)
                    if _proc_ctx:
                        extra_parts.append(_proc_ctx)
        except Exception:
            pass

        user_content = f"Goal: {agent_state.goal}"
        if extra_parts:
            user_content += "\n\n" + "\n\n".join(extra_parts)
        # Use PromptOptimizer variant when wired (Task 7)
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

        # Tool context is pre-built by ToolSelector in _build_tool_context and
        # injected via agent_state.context["tool_prompt"] (see lines above at
        # extra_parts.append). The duplicate discover_all_tools block has been
        # removed — single source of truth is now the tiered ToolSelector output.

        # Determine planning model — derive from the wired provider's default so
        # the model name always matches the active provider (OpenAI → gpt-4-turbo,
        # Anthropic → claude-opus-4-8, Fake → "fake", etc.).
        # Never hard-code a vendor-specific model name here.
        planning_model = getattr(self._planner, "_default_model", None) or "gpt-5.2"
        # Update ModelOrchestratorAdapter with current runtime profile for budget-aware selection
        try:
            _runtime_profile_for_router = agent_state.context.get("_runtime_profile")
            if _runtime_profile_for_router is not None and hasattr(self._model_router, "update_from_profile"):
                _budget_ratio = (
                    agent_state.context.get("total_cost_usd", 0.0) /
                    max(getattr(_runtime_profile_for_router.model_plan, "max_cost_usd", 0.10) or 0.10, 0.001)
                )
                self._model_router.update_from_profile(
                    _runtime_profile_for_router,
                    budget_spent_ratio=min(1.0, _budget_ratio),
                )
        except Exception:
            pass
        if self._model_router is not None:
            routed = self._model_router.model_for_goal("planning", goal=agent_state.goal)
            if routed:
                planning_model = routed
            # Cost Auto-Downgrade: use cheaper model when budget > 60% consumed
            if self._cost_controller is not None:
                try:
                    _cost_tier = await self._cost_controller.get_cost_tier(
                        goal_id=agent_state.goal_id,
                        tenant_ctx=tenant_ctx,
                    )
                    if _cost_tier == "economy":
                        _economy_model = self._model_router.model_for("verification")
                        if _economy_model:
                            self._logger.info(
                                "cost_downgrade_economy",
                                original=planning_model,
                                downgraded=_economy_model,
                            )
                            planning_model = _economy_model
                    elif _cost_tier == "standard":
                        _standard_model = self._model_router.model_for("execution")
                        if _standard_model:
                            self._logger.info(
                                "cost_downgrade_standard",
                                original=planning_model,
                                downgraded=_standard_model,
                             )
                            planning_model = _standard_model
                except Exception:
                    pass

        # H21: Emit model_route_selected SSE after model selection
        try:
            if self._event_callback is not None:
                from app.observability.runtime_decision_trace import RuntimeSSEEmitter
                _sse_mr = RuntimeSSEEmitter()
                _runtime_prof_mr = agent_state.context.get("_runtime_profile")
                await self._emit(_sse_mr.model_route_selected(
                    goal_id=agent_state.goal_id,
                    planner=planning_model,
                    executor=getattr(self._executor, "_default_model", "") or "",
                    verifier=getattr(self._verifier, "_default_model", "") or "",
                    cost_class=(
                        _runtime_prof_mr.model_plan.cost_class
                        if _runtime_prof_mr is not None else "unknown"
                    ),
                ))
        except Exception:
            pass

        # ── Prompt Compression: reduce token count before LLM call ────────────
        try:
            from app.agent.prompt_compressor import _default_compressor as _compressor
            system_content = _compressor.compress(system_content)
            user_content = _compressor.compress(user_content)
        except Exception:
            pass

        # ── LLM Response Cache: skip planner call on identical goals ──────────
        _llm_rc = getattr(self, "_llm_response_cache", None)
        if _llm_rc is not None and not _llm_rc.should_skip_cache(user_content):
            try:
                _cached_plan = await _llm_rc.get(
                    system=system_content,
                    user=user_content,
                    model=planning_model,
                    tenant_id=tenant_ctx.tenant_id,
                    task_type="planning",
                )
                if _cached_plan is not None:
                    self._logger.info("llm_cache_plan_hit", tenant=tenant_ctx.tenant_id)
                    # Use cached response directly — skip the LLM call
                    # Jump straight to parsing (replicate post-resp.content code)
                    resp_content = _cached_plan
                    # Store locally so the existing parse block below can use it
                    class _FakeResp:
                        content = resp_content
                    resp = _FakeResp()
                    record_plan_duration(agent_state.iterations, 0.0)
                    # bypass the real LLM call below
                    _llm_plan_cached = True
                else:
                    _llm_plan_cached = False
            except Exception:
                _llm_plan_cached = False
        else:
            _llm_plan_cached = False

        if not _llm_plan_cached:
            # Use structured output when available (Phase 3 Track A)
            _response_schema = None
            try:
                from app.agent.schemas import planner_schema
                if (
                    hasattr(self._planner, "supports_structured_output")
                    and self._planner.supports_structured_output()
                ):
                    _response_schema = planner_schema()
            except Exception:
                pass

            req = CompletionRequest(
                messages=[
                    Message(role="system", content=system_content),
                    Message(role="user", content=user_content),
                ],
                model=planning_model,
                response_schema=_response_schema,
                cache_prefix=system_content,  # stable prefix for Anthropic ephemeral caching
            )
            with self._tracer.start_as_current_span("agentverse.plan") as span:
                span.set_attribute("plan.iteration", agent_state.iterations)
                span.set_attribute("tenant.id", tenant_ctx.tenant_id)
                _plan_start = time.monotonic()
                try:
                    resp = await call_with_circuit_breaker(
                        self._planner, "complete", req,
                        provider_name=type(self._planner).__name__,
                    )
                except RuntimeError as cb_exc:
                    raise PermissionError(f"Planning unavailable: {cb_exc}") from cb_exc
                record_plan_duration(agent_state.iterations, time.monotonic() - _plan_start)
            # 2.3: Per-goal planner cost tracking
            try:
                from app.observability.cost_breakdown import record_role_cost as _rrc
                _rrc(
                    goal_id=agent_state.goal_id,
                    role="planner",
                    model=planning_model,
                    input_tok=getattr(resp, "input_tokens", 0),
                    output_tok=getattr(resp, "output_tokens", 0),
                    cost=0.0,
                )
            except Exception:
                pass
            # Store in LLM cache — only on successful, non-error responses
            if _llm_rc is not None:
                try:
                    _is_error_resp = (
                        not resp.content
                        or "error" in resp.content.lower()[:40]
                        or resp.content.strip().startswith("{\"error")
                    )
                    if not _is_error_resp:
                        await _llm_rc.set(
                            system=system_content,
                            user=user_content,
                            model=planning_model,
                            response=resp.content,
                            tenant_id=tenant_ctx.tenant_id,
                            task_type="planning",
                        )
                except Exception:
                    pass
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
        # ── Predictive Prefetch: embed all step descriptions now so semantic
        # cache lookups during execution are instant (sub-millisecond) ──────────
        if self._embedder is not None and self._semantic_cache is not None:
            async def _prefetch_steps() -> None:
                try:
                    step_texts = []
                    for _s in _plan_display:
                        if isinstance(_s, str):
                            step_texts.append(_s)
                        elif isinstance(_s, dict):
                            step_texts.append(_s.get("description") or _s.get("step") or str(_s))
                    if not step_texts:
                        return
                    # Batch embed all steps
                    _embeds = await self._embedder.embed_batch(step_texts)
                    # Warm the semantic cache with step embeddings
                    if hasattr(self._semantic_cache, "warm"):
                        await self._semantic_cache.warm(
                            queries=step_texts,
                            embeddings=_embeds,
                            tenant_id=tenant_ctx.tenant_id,
                        )
                    self._logger.debug(
                        "predictive_prefetch_complete",
                        steps=len(step_texts),
                        tenant=tenant_ctx.tenant_id,
                    )
                except Exception as _pf_exc:
                    self._logger.debug("predictive_prefetch_failed", error=str(_pf_exc)[:60])

            _pf_task = asyncio.create_task(_prefetch_steps())
            self._background_tasks.add(_pf_task)
            _pf_task.add_done_callback(self._background_tasks.discard)
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
                    retrieval_gateway=self._retrieval_gateway,
                    mcp_client=self._mcp_client,
                    eval_runner=self._eval_runner,
                    # Sub-agents don't recurse into goal trees
                    enable_goal_tree=False,
                    autonomy_mode=self._autonomy_mode,
                )
                graph._agent_collection_ids = list(self._agent_collection_ids)
                graph._event_callback = self._event_callback
                graph._parent_trace_context = otel_context.get_current()
                return graph

            try:
                sub_goals: list[SubGoal] = await execute_goal_tree(
                    agent_state.goal,
                    planner=self._planner,
                    tenant_ctx=tenant_ctx,
                    parent_goal_id=agent_state.goal_id,
                    graph_factory=_sub_graph_factory,
                    event_callback=self._event_callback,
                )
                agent_state.sub_goals = sub_goals
                if sub_goals:
                    child_failures = [
                        sub_goal
                        for sub_goal in sub_goals
                        if sub_goal.status is GoalStatus.FAILED
                    ]
                    for sub_goal in sub_goals:
                        agent_state.provenance.extend(sub_goal.provenance)
                    agent_state.context["child_retrieval_traces"] = [
                        trace
                        for sub_goal in sub_goals
                        for trace in sub_goal.retrieval_trace
                    ]
                    # Aggregate sub-goal results as steps so the verifier sees them
                    for sg in sub_goals:
                        step = StepResult(
                            description=sg.description,
                            output=sg.result or sg.error,
                            status=StepStatus.COMPLETE if not sg.error else StepStatus.FAILED,
                        )
                        agent_state.steps.append(step)
                    if child_failures:
                        agent_state.status = GoalStatus.FAILED
                        agent_state.error_message = "Nested retrieval failed"
                        await self._emit(
                            {
                                "type": "nested_goal_failed",
                                "failed_sub_goals": [
                                    sub_goal.sub_goal_id
                                    for sub_goal in child_failures
                                ],
                                "provenance": agent_state.provenance,
                                "retrieval_trace": agent_state.context[
                                    "child_retrieval_traces"
                                ],
                            }
                        )
                    return {"agent_state": agent_state}
            except Exception as exc:
                # Fall through to normal execution if goal-tree fails
                await self._emit({"type": "goal_tree_error", "error": str(exc)})

        # Build StructuredPlan for wave-based parallel execution (Fix 1 + Fix 3)
        import asyncio as _asyncio

        from app.agent.structured_plan import StructuredPlan as _SP
        from app.agent.structured_plan import StructuredStep as _SS

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

        # ── Batch cache prefetch ────────────────────────────────────────────
        # Embed ALL plan step descriptions at once (single embedding API call)
        # then batch-check the cache. This way, before the first wave executes,
        # we already know which steps have cache hits — saving per-step embedding
        # latency during the hot path.
        _batch_cache_results: dict[str, str] = {}  # step_desc → cached_response
        if self._semantic_cache is not None and self._embedder is not None:
            try:
                from app.providers.base import EmbedRequest as _EmbedReq
                _all_descs = [s.description for w in waves for s in w]
                if _all_descs:
                    _batch_resp = await self._embedder.embed(_EmbedReq(texts=_all_descs))
                    _batch_embs = _batch_resp.embeddings or []
                    if _batch_embs and hasattr(self._semantic_cache, "get_batch"):
                        _batch_hits = await self._semantic_cache.get_batch(
                            embeddings=_batch_embs,
                            tenant_id=tenant_ctx.tenant_id,
                        )
                        for desc, hit in zip(_all_descs, _batch_hits):
                            if hit is not None:
                                # Skip cached empty/error results so they are not
                                # served on fresh runs — forces a real tool call.
                                cached_resp = hit.response if hasattr(hit, 'response') else str(hit)
                                _cr_stripped = cached_resp.strip().lower() if cached_resp else ""
                                _is_llm_reasoning = (
                                    _cr_stripped.startswith((
                                        "i'll ", "i will ", "i'll use", "i will use",
                                        "to complete", "let me ", "i need to ",
                                        "step 1", "first,", "first i",
                                    ))
                                    or ("will use" in _cr_stripped and "tool" in _cr_stripped)
                                    or ("will call" in _cr_stripped and len(_cr_stripped) < 500)
                                )
                                _is_empty = (
                                    not cached_resp
                                    or '"total": 0' in cached_resp
                                    or '"issues": []' in cached_resp
                                    or '"projects": []' in cached_resp
                                    or cached_resp.strip() in ('{}', '[]', '')
                                    or len(cached_resp.strip()) < 10
                                    or _is_llm_reasoning  # Never serve stale LLM text as tool result
                                )
                                if not _is_empty:
                                    _batch_cache_results[desc] = cached_resp
                        if _batch_cache_results:
                            self._logger.info(
                                "batch_cache_prefetch",
                                total=len(_all_descs),
                                hits=len(_batch_cache_results),
                            )
            except Exception as _bp_exc:
                self._logger.debug("batch_cache_prefetch_skipped", error=str(_bp_exc)[:80])

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
                        elif step_desc in _batch_cache_results:
                            # Batch prefetch hit — serve from pre-fetched cache result
                            output = _batch_cache_results[step_desc]
                            await self._emit({
                                "type": "cache_hit",
                                "step": step_desc,
                                "source": "batch_prefetch",
                            })
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
                # Persist tool outcome for cross-restart trust scores
                try:
                    _orch_persist = (
                        getattr(self._app_state, "orchestration_persistence", None)
                        if self._app_state else None
                    )
                    if _orch_persist is not None:
                        _tool_nm = self._extract_tool_name(step_desc) or step_desc[:50]
                        _step_ok = output and "error" not in output.lower()[:50] and "failed" not in output.lower()[:50]
                        _step_lat = float(agent_state.context.get("last_step_latency_ms", 200.0))
                        import asyncio as _tp_asyncio
                        _tp_asyncio.ensure_future(
                            _orch_persist.persist_tool_outcome(
                                tool_name=_tool_nm,
                                success=bool(_step_ok),
                                latency_ms=_step_lat,
                                tenant_id=tenant_ctx.tenant_id,
                            )
                        )
                except Exception:
                    pass
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
                        # H4: Persist tool outcome for parallel wave steps
                        try:
                            _orch_persist_wave = (
                                getattr(self._app_state, "orchestration_persistence", None)
                                if self._app_state else None
                            )
                            if _orch_persist_wave is not None:
                                _tool_nm_wave = self._extract_tool_name(desc) or desc[:50]
                                _step_ok_wave = bool(out and "error" not in out.lower()[:50])
                                import asyncio as _wp_asyncio
                                _wp_asyncio.ensure_future(
                                    _orch_persist_wave.persist_tool_outcome(
                                        tool_name=_tool_nm_wave,
                                        success=_step_ok_wave,
                                        latency_ms=200.0,
                                        tenant_id=tenant_ctx.tenant_id,
                                    )
                                )
                        except Exception:
                            pass
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
                except (PermissionError, Exception):
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
        """Run the canonical governed per-step execution pipeline."""
        tool_name = self._extract_tool_name(step)

        # H23-H26: Action safety profile — assess per-tool risk
        try:
            from app.security_runtime.action_safety_profile import (
                ActionSafetyLevel,
                ActionSafetyProfileSelector,
            )
            _asp_selector = ActionSafetyProfileSelector()
            _risk = state.context.get("_risk_level", "low")
            _asp = _asp_selector.select(
                tool_name=tool_name,
                tool_args={},
                risk_level=str(_risk),
            )
            if _asp.safety_level.value == ActionSafetyLevel.BLOCKED.value:
                return f"Action blocked by safety profile: {_asp.reason}"
        except Exception:
            pass

        # 1. Cost check deferred — actual cost calculated after LLM call below.

        # 2. Exec memory recall — already done in rag_retrieval; skip here.

        # 3. Dedup
        if self._dedup_cache is not None:
            content_hash = hashlib.sha256(f"{step}:{state.goal}".encode()).hexdigest()
            if self._dedup_cache.is_duplicate(content_hash=content_hash, tenant_ctx=tenant_ctx):
                return "Duplicate step, returning cached result."
            self._dedup_cache.mark_seen(content_hash=content_hash, tenant_ctx=tenant_ctx)

        # 3b. Smart context fetch (per-step RAG)
        app_state = getattr(self._app_state, "state", self._app_state)
        step_strategy = RAGStrategy(
            str(state.context.get("retrieval_strategy", RAGStrategy.HYBRID.value))
        )
        step_context = await smart_context_fetch(
            goal=state.goal,
            step=step,
            tenant_ctx=tenant_ctx,
            retrieval_gateway=(
                self._retrieval_gateway
                or getattr(app_state, "retrieval_gateway", None)
            ),
            collection_ids=list(self._agent_collection_ids),
            strategy=step_strategy,
            top_k=int(state.context.get("retrieval_top_k", 3)),
            filters=state.context.get("retrieval_filters", {}),
            execution_id=state.goal_id,
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

        # 6. Guardrails — validate step text for injection, then tool name
        # 6a. Check the plan STEP TEXT for injection phrases (e.g. "ignore all previous instructions")
        # This is important: a compromised tool could return an injection-crafted step description.
        if self._guardrail_checker is not None:
            step_issues = self._guardrail_checker.check_goal(step)
            if step_issues:
                return f"Guardrail blocked step: {'; '.join(step_issues)}"

        # 6b. Check tool name (only check the name; do NOT pass the step description as tool_args
        # since it triggers false positives on benign words like "extract", "format").
        if self._guardrail_checker is not None:
            violations = self._guardrail_checker.check(
                tool_name=tool_name,
                tool_args={},
            )
            if violations:
                return f"Guardrail blocked step: {'; '.join(violations)}"

        # 6c. Profile-based GuardrailEnforcer (dynamic bundle selection from Part 11/13)
        try:
            from app.core.runtime_flags import get_runtime_flags as _ge_rtf
            from app.security_runtime.guardrail_enforcer import GuardrailEnforcer
            _ge_flags = _ge_rtf()
            _runtime_profile = state.context.get("_runtime_profile")
            if (
                (_ge_flags.dynamic_orchestration or _ge_flags.enable_guardrail_profile)
                and _runtime_profile is not None
            ):
                _ge = GuardrailEnforcer()
                _ge_result = _ge.check_tool_args(
                    tool_name=tool_name,
                    tool_args={},  # C2 fix: tool_args not defined at pre-LLM check stage
                    profile=_runtime_profile,
                )
                if _ge_result.blocked:
                    return f"GuardrailEnforcer blocked tool '{tool_name}': {_ge_result.reason}"
        except Exception:
            pass  # profile-based guardrail never crashes execution

        # N6b: guardrail_profile_selected SSE — only when dynamic orchestration profile present
        if self._event_callback is not None and _runtime_profile is not None:
            try:
                from app.observability.runtime_decision_trace import RuntimeSSEEmitter
                _sse_gps = RuntimeSSEEmitter()
                _bundle = getattr(
                    getattr(_runtime_profile, "security", None),
                    "guardrail_bundle", "default"
                ) or "default"
                await self._emit(_sse_gps.guardrail_profile_selected(
                    goal_id=state.goal_id,
                    bundle=_bundle,
                    scanners=["injection", "pii", "tool_args"],
                ))
            except Exception:
                pass

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
            risk = "high" if _is_high_risk_step(step) else "low"
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
        recent_outputs = "\n".join(
            (s.output or "")[:_EXECUTOR_CONTEXT_MAX_LENGTH]
            for s in state.steps[-3:]
            if s.output
        )
        context_parts = []
        if recent_outputs:
            context_parts.append(f"Recent outputs:\n{recent_outputs}")
        if step_context:
            context_parts.append(f"Relevant knowledge:\n{step_context}")

        # ── Search directive parsing ───────────────────────────────────────
        try:
            from app.rag.agentic.search_directive_parser import SearchDirectiveParser
            _directive_parser = SearchDirectiveParser()
            _directives = _directive_parser.extract(step)
            if _directives and self._agent_collection_ids:
                from app.rag.agentic.retriever_tool import RetrieverTool
                _retriever = RetrieverTool(
                    retrieval_gateway=(
                        self._retrieval_gateway
                        or getattr(app_state, "retrieval_gateway", None)
                    )
                )
                _directive_contexts: list[str] = []
                for _directive in _directives:
                    _strategy = {
                        "kb": RAGStrategy.HYBRID,
                        "graph": RAGStrategy.GRAPH,
                        "web": RAGStrategy.WEB_AUGMENTED,
                    }.get(_directive.source_type)
                    if _strategy is None:
                        raise ValueError("Unsupported search directive source")
                    _retrieval = await _retriever.retrieve(
                        query=_directive.query,
                        tenant_ctx=tenant_ctx,
                        strategy=_strategy,
                        collection_ids=list(self._agent_collection_ids),
                        top_k=3,
                        execution_id=state.goal_id,
                    )
                    if _retrieval.chunks:
                        _directive_contexts.append(
                            f"[{_directive.source_type.upper()} SEARCH: {_directive.query}]\n"
                            + _retrieval.context_text[:1000]
                        )
                if _directive_contexts:
                    _directive_context_str = "\n\n".join(_directive_contexts)
                    context_parts.append(_directive_context_str)
        except ValueError:
            raise
        # ── end search directives ──────────────────────────────────────────

        content = f"Step: {step}"
        if context_parts:
            content += "\n\n" + "\n\n".join(context_parts)

        # N9: Prepend executor context from ContextPipeline if available
        _exec_ctx = state.context.get("_executor_context", "") or ""
        if _exec_ctx and len(_exec_ctx) > 50:
            content = (
                f"[Relevant context for this step]\n{_exec_ctx[:1200]}\n\n{content}"
            )

        # Collect available tools for structured tool calling (Task 1)
        # IMPORTANT: OpenAI function names must match ^[a-zA-Z0-9_-]{1,64}$
        # DO NOT include server_name in the name — "Jira Connector.jira_search_issues"
        # is invalid and causes OpenAI to return text instead of a tool call.
        _tool_defs: list[ToolDefinition] = []
        _tc_ctx = state.context.get("tool_context")
        if _tc_ctx is not None and hasattr(_tc_ctx, "tools"):
            for _t in _tc_ctx.tools:
                import re as _re
                # Use only the bare tool name, sanitized to valid function-name chars
                _raw_name = _t.name if hasattr(_t, "name") else ""
                _safe_name = _re.sub(r"[^a-zA-Z0-9_-]", "_", _raw_name)[:64]
                if not _safe_name:
                    continue
                _tool_defs.append(ToolDefinition(
                    name=_safe_name,
                    description=getattr(_t, "description", ""),
                    input_schema=getattr(_t, "input_schema", {}),
                ))

        # Build allowed-tools allowlist for anti-hallucination grounding
        _allowed_tools_set: set[str] = set()
        if _tc_ctx is not None:
            try:
                _tools_list = getattr(_tc_ctx, "tools", []) or []
                _allowed_tools_set = {t.name for t in _tools_list if hasattr(t, "name")}
            except Exception:
                pass

        # ToolPromptBuilder — enrich content with formatted tool descriptions (M5b)
        try:
            from app.context.tool_prompt_builder import ToolPromptBuilder
            if _tool_defs:
                _tpb = ToolPromptBuilder()
                _defs_as_dicts = [
                    {"name": td.name, "description": td.description}
                    for td in _tool_defs
                ]
                _tool_context = _tpb.build(tools=_defs_as_dicts, step_context=step)
                if _tool_context:
                    content = f"{content}\n\nAvailable tools:\n{_tool_context}"
        except Exception:
            pass

        # N10: Tag unreliable tools (informational — don't hard-block, just log)
        try:
            _tr_store = getattr(self, "_tool_reliability_store", None)
            if _tr_store is not None and _tool_defs:
                _unreliable = await _tr_store.get_unreliable_tools(
                    tenant_id=tenant_ctx.tenant_id, threshold=0.3
                )
                _unreliable_names = {
                    t.get("tool_name", "") if isinstance(t, dict) else str(t)
                    for t in (_unreliable or [])
                }
                if _unreliable_names:
                    state.context["_unreliable_tools"] = list(_unreliable_names)
                    # Add a hint to the step context
                    _unreliable_hint = (
                        f"\n[Note: these tools have had reliability issues: "
                        f"{', '.join(list(_unreliable_names)[:3])}]"
                    )
                    content = content + _unreliable_hint if content else _unreliable_hint
        except Exception:
            pass

        # Select executor system prompt via PromptOptimizer if wired (Task 7)
        _executor_prompt = EXECUTOR_SYSTEM
        _exec_optimizer = getattr(self, "_prompt_optimizer", None)
        if _exec_optimizer is not None:
            _exec_variant = _exec_optimizer.select_variant("executor")
            if _exec_variant is not None:
                _executor_prompt = _exec_variant.prompt_text

        # Inject allowed-tools list into executor system prompt
        if _allowed_tools_set:
            _tool_lines = "\n".join(f"  - {n}" for n in sorted(_allowed_tools_set)[:30])
            _executor_prompt = _executor_prompt + f"\n\nALLOWED TOOLS (ONLY use these exact names):\n{_tool_lines}"

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

        # Token streaming — buffer for accumulation and closure for on_token callback.
        # Defined before the bulkhead try so the closure captures step by value.
        _token_buffer: list[str] = []
        _step_for_token = step

        async def _on_token(chunk: str) -> None:
            _token_buffer.append(chunk)
            await self._emit({
                "type": "token_chunk",
                "step": _step_for_token,
                "token": chunk,
                "cumulative": "".join(_token_buffer),
            })

        try:
            try:
                async with track_tool_call(tool_name=tool_name, tenant_id=tenant_ctx.tenant_id):
                    resp = await self._executor.stream_tokens(req, _on_token)
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
            async with self._state_lock:
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
                async with self._state_lock:
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
        # 2.3: Per-goal executor cost tracking
        try:
            from app.observability.cost_breakdown import record_role_cost as _rrc
            _rrc(
                goal_id=state.goal_id,
                role="executor",
                model=_exec_model,
                input_tok=getattr(resp, "input_tokens", 0),
                output_tok=getattr(resp, "output_tokens", 0),
                cost=_actual_cost if "_actual_cost" in locals() else 0.0,
            )
        except Exception:
            pass
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
            tool_call = await repair_tool_call_arguments(tool_call, step, goal=state.goal)
        # Validate tool name before dispatching
        if tool_call is not None and tool_call.tool:
            from app.agent.tool_calls import validate_tool_name as _validate_tn
            _tn_rejection = _validate_tn(tool_call.tool, _allowed_tools_set)
            if _tn_rejection:
                raw_output = _tn_rejection
                raw_output_sanitized = True
                await self._emit({
                    "type": "tool_call_failed",
                    "tool": tool_call.tool,
                    "error": _tn_rejection[:300],
                })
                record_tool_call(
                    tool_call.tool, "unknown", "rejected",
                    0.0,
                )
                tool_call = None  # prevent dispatch
        if tool_call is not None:
            # GuardrailEngine v2: evaluate tool arguments BEFORE the MCP call
            _guardrail_engine_v2 = (
                getattr(self._app_state, "guardrail_engine", None) if self._app_state else None
            )
            if _guardrail_engine_v2 is not None:
                try:
                    from app.intelligence.guardrail_engine import GuardrailContext as _GCtx
                    _ge_ctx = _GCtx(
                        tenant_id=tenant_ctx.tenant_id if tenant_ctx else "",
                        goal_id=state.goal_id or "",
                        agent_id=self._agent_id or "",
                        domain=getattr(tenant_ctx, "domain_context", "general") if tenant_ctx else "general",
                    )
                    _ge_args_result = await _guardrail_engine_v2.evaluate_tool_args(
                        tool_name=tool_name,
                        arguments=tool_call.arguments or {},
                        context=_ge_ctx,
                    )
                    if not _ge_args_result.allowed:
                        _ge_viol = _ge_args_result.violations[0] if _ge_args_result.violations else None
                        raise PermissionError(
                            f"Guardrail blocked tool call '{tool_name}': "
                            f"{_ge_viol.matched_pattern if _ge_viol else 'policy violation'}"
                        )
                except PermissionError:
                    raise
                except Exception as _ge_exc:
                    self._logger.warning("guardrail_engine_v2_pre_check_failed", error=str(_ge_exc))

            # Guardrail check: tool_args (Guardrails 2.0)
            if _GUARDRAILS_AVAILABLE and guardrails_engine is not None and tenant_ctx:
                try:
                    _g2_args_str = json.dumps(tool_call.arguments) if isinstance(tool_call.arguments, dict) else str(tool_call.arguments)
                    _g2_args_result = await guardrails_engine.evaluate(
                        content=_g2_args_str,
                        layer=GuardrailLayer.TOOL_ARGS,
                        tenant_id=tenant_ctx.tenant_id,
                        goal_id=getattr(state, "goal_id", None),
                        step_description=step,
                    )
                    if _g2_args_result.get("blocked"):
                        _g2_viol_name = (_g2_args_result.get("violations") or [{}])[0].get("rule_name", "policy")
                        raise PermissionError(
                            f"Tool call blocked by guardrail: {_g2_viol_name}"
                        )
                except PermissionError:
                    raise
                except Exception:
                    pass  # Guardrail errors must never break execution

            tool_call_started = time.monotonic()
            if self._mcp_client is None:
                self._logger.warning("mcp_client_none_at_tool_dispatch tool=%s", tool_call.tool)
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
                            from app.civilization.governor import Governor
                            from app.civilization.spawn_tool import execute_spawn_tool
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
                                # ── RPA failure → ExecutionMemory + SelfOptimizer ──
                                if not rpa_result.success:
                                    _rpa_url_fail = (
                                        (tool_call.arguments or {}).get("url", "")
                                        or (
                                            agent_state.context.get(
                                                "_current_rpa_url", ""
                                            )
                                            if isinstance(agent_state.context, dict)
                                            else ""
                                        )
                                    )
                                    # Record failure in ExecutionMemory for recall
                                    if (
                                        self._exec_memory is not None
                                        and self._db_session_factory is not None
                                    ):
                                        _fail_task = asyncio.create_task(
                                            self._exec_memory.record_failure_async(
                                                goal=agent_state.goal,
                                                error=(
                                                    f"RPA {rpa_tool_name} failed on "
                                                    f"{_rpa_url_fail}: "
                                                    f"{rpa_result.error or 'unknown'}"
                                                ),
                                                tenant_id=tenant_ctx.tenant_id,
                                                db=self._db_session_factory,
                                            )
                                        )
                                        self._background_tasks.add(_fail_task)
                                        _fail_task.add_done_callback(
                                            self._background_tasks.discard
                                        )
                                    # Generate RPA-specific suggestions
                                    if self._self_optimizer is not None:
                                        self._self_optimizer.analyze_rpa_failure(
                                            tool_name=rpa_tool_name,
                                            error=rpa_result.error or "",
                                            url=str(_rpa_url_fail),
                                            tenant_ctx=tenant_ctx,
                                        )
                                # ── RPA → LTM persistence ──────────────────
                                # Store extracted text and vision analysis so
                                # future goals can recall what was found on
                                # this page via semantic search.
                                if (
                                    rpa_result.success
                                    and self._long_term_memory is not None
                                    and rpa_tool_name in (
                                        "rpa_extract_text", "rpa_screenshot"
                                    )
                                    and rpa_result.output
                                    and len(rpa_result.output) > 50
                                ):
                                    _rpa_url = (tool_call.arguments or {}).get(
                                        "url",
                                        (state.context.get("_current_rpa_url", "")
                                         if isinstance(state.context, dict) else "")
                                    )
                                    _rpa_src = (
                                        "rpa_vision"
                                        if rpa_tool_name == "rpa_screenshot"
                                        else "rpa_extraction"
                                    )
                                    _rpa_ltm_task = asyncio.create_task(
                                        self._long_term_memory.store_rpa_extraction(
                                            url=str(_rpa_url or "unknown"),
                                            extracted_text=rpa_result.output,
                                            goal_id=str(
                                                getattr(state, "goal_id", "")
                                            ),
                                            tenant_ctx=tenant_ctx,
                                            db=self._db_session_factory,
                                            embedder=self._embedder,
                                            source_type=_rpa_src,
                                        )
                                    )
                                    self._background_tasks.add(_rpa_ltm_task)
                                    _rpa_ltm_task.add_done_callback(
                                        self._background_tasks.discard
                                    )
                                # Track current URL for extraction attribution
                                if rpa_tool_name == "rpa_open_url":
                                    _nav_url = (tool_call.arguments or {}).get("url", "")
                                    if isinstance(state.context, dict) and _nav_url:
                                        state.context["_current_rpa_url"] = _nav_url
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
                    # Gate write_high bypass behind an explicit env flag (default-secure).
                    import os as _os
                    _allow_fa_write_high = (
                        _os.getenv("ALLOW_FULLY_AUTONOMOUS_WRITE_HIGH", "false").lower() == "true"
                    )
                    if (
                        tool_risk == "write_high"
                        and self._autonomy_mode == "fully-autonomous"
                        and _allow_fa_write_high
                    ):
                        tool_risk = "write_low"
                    # else: falls through to write_high HITL gate below (default-secure)
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
                            if self._autonomy_mode == "supervised":
                                _hitl_start = time.monotonic()
                                final_status = await self._hitl_gateway.wait_for_approval(
                                    req_id, tenant_ctx=tenant_ctx
                                )
                                record_approval_wait(time.monotonic() - _hitl_start)
                                if final_status == ApprovalStatus.REJECTED:
                                    raise PermissionError(
                                        f"Tool '{tool_ref.name}' was rejected by human approver."
                                    )
                                elif final_status == ApprovalStatus.TIMED_OUT:
                                    raise PermissionError(
                                        f"Tool '{tool_ref.name}' approval timed out."
                                    )
                                # APPROVED: now actually dispatch the tool call
                                await self._emit({"type": "approval_granted", "request_id": req_id})
                                _approved_result = await self._mcp_client.call_tool(
                                    server_id=tool_ref.server_id,
                                    tool_name=tool_ref.name,
                                    arguments=tool_call.arguments,
                                    tenant_ctx=tenant_ctx,
                                )
                                raw_output = (
                                    _approved_result.output
                                    if _approved_result.success
                                    else str(_approved_result.error)
                                )
                                raw_output_sanitized = False
                                record_tool_call(
                                    tool_ref.name,
                                    tool_ref.server_id,
                                    "success" if _approved_result.success else "failed",
                                    time.monotonic() - tool_call_started,
                                )
                            else:
                                # Non-supervised: log the request but do not block
                                raw_output = self._sanitize_tool_raw_output(
                                    f"High-risk tool '{tool_ref.name}' "
                                    "requires approval (non-supervised mode)."
                                )
                                raw_output_sanitized = True
                                record_tool_call(
                                    tool_ref.name,
                                    tool_ref.server_id,
                                    "approval",
                                    time.monotonic() - tool_call_started,
                                )
                    else:
                        # V4: Validate arguments against JSON schema before MCP dispatch
                        from app.agent.tool_calls import validate_tool_arguments as _validate_args
                        _tool_schema = getattr(tool_ref, "input_schema", None) or {}
                        _arg_errors_v4 = _validate_args(tool_call.arguments or {}, _tool_schema)
                        if _arg_errors_v4:
                            _arg_error_msg = (
                                f"[ARGUMENT VALIDATION FAILED] Tool '{tool_call.tool}' "
                                f"called with invalid arguments:\n"
                                + "\n".join(f"  - {e}" for e in _arg_errors_v4)
                                + "\nPlease retry with correct arguments from the tool schema."
                            )
                            raw_output = self._sanitize_tool_raw_output(_arg_error_msg)
                            raw_output_sanitized = True
                            await self._emit({
                                "type": "tool_call_failed",
                                "tool": tool_call.tool,
                                "error": _arg_error_msg[:300],
                            })
                            record_tool_call(
                                tool_call.tool,
                                getattr(tool_ref, "server_id", "unknown"),
                                "arg_validation_failed",
                                time.monotonic() - tool_call_started,
                            )
                        else:
                            # V5: Placeholder argument guard — prevent LLM-generated
                            # placeholder values (e.g. "your_organization/your_repository")
                            # from reaching real MCP servers.
                            _PLACEHOLDER_PATTERNS = (
                                "your_organization", "your_repository",
                                "your_org", "your_repo", "your_project",
                                "your_workspace", "your_team", "your_board",
                                "<organization>", "<repository>", "<repo>",
                                "{organization}", "{repository}", "{repo}",
                                "example.com", "placeholder",
                            )
                            _ph_hits = [
                                f"{k}={v!r}"
                                for k, v in (tool_call.arguments or {}).items()
                                if isinstance(v, str)
                                and any(p in v.lower() for p in _PLACEHOLDER_PATTERNS)
                            ]
                            if _ph_hits:
                                _ph_msg = (
                                    f"[PLACEHOLDER ARGUMENTS DETECTED] Tool '{tool_call.tool}' "
                                    f"was called with generic placeholder values: "
                                    f"{', '.join(_ph_hits)}. "
                                    "Please use real values from the goal context or "
                                    "user-provided configuration instead of template placeholders."
                                )
                                raw_output = self._sanitize_tool_raw_output(_ph_msg)
                                raw_output_sanitized = True
                                await self._emit({
                                    "type": "tool_call_failed",
                                    "tool": tool_call.tool,
                                    "error": _ph_msg[:300],
                                })
                                record_tool_call(
                                    tool_call.tool,
                                    getattr(tool_ref, "server_id", "unknown"),
                                    "placeholder_args",
                                    time.monotonic() - tool_call_started,
                                )
                            else:
                                # No placeholders — dispatch to MCP
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
                            # Apply PII check to raw tool output (H3 fix: result is ToolCallResult not dict)
                            raw_output_text = ""
                            if isinstance(result.output, dict):
                                raw_output_text = str(result.output.get("content") or result.output.get("result") or "")
                            elif isinstance(result.output, str):
                                raw_output_text = result.output[:500]
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

                            # Guardrail check: tool_output (Guardrails 2.0)
                            if _GUARDRAILS_AVAILABLE and guardrails_engine is not None and tenant_ctx:
                                try:
                                    _g2_out_preview = str(raw_result_output)[:500] if raw_result_output else ""
                                    await guardrails_engine.evaluate(
                                        content=_g2_out_preview,
                                        layer=GuardrailLayer.TOOL_OUTPUT,
                                        tenant_id=tenant_ctx.tenant_id,
                                        goal_id=getattr(state, "goal_id", None),
                                    )
                                except Exception:
                                    pass  # Guardrail errors must never break execution

                            # ── Indirect injection scan on tool output ──────────────
                            # External tool results (Confluence, web, email) may contain
                            # adversarial text designed to hijack the agent (tool poisoning).
                            if result.success and raw_result_output:
                                try:
                                    from app.agent.exfil_guard import (
                                        check_tool_output_for_injection,
                                    )
                                    _injection_warning = check_tool_output_for_injection(
                                        tool_ref.name, raw_result_output
                                    )
                                    if _injection_warning:
                                        self._logger.warning(
                                            "indirect_injection_detected",
                                            tool=tool_ref.name,
                                            warning=_injection_warning[:120],
                                        )
                                        raw_result_output = _injection_warning + "\n\n" + raw_result_output
                                except Exception:
                                    pass  # injection scan must never block execution

                            # ── C4 Fix: Populate StepResult.tool_calls ─────────────
                            # This allows the verifier's [TOOL FAILED] markers to fire.
                            if state.steps:
                                state.steps[-1].tool_calls.append({
                                    "tool_name": tool_ref.name,
                                    "server_id": tool_ref.server_id,
                                    "success": result.success,
                                    "error": result.error or "",
                                    "output": str(result.output)[:300] if result.output else "",
                                })

                            # ── H3 Fix: PII check on ToolCallResult (not dict) ──────
                            raw_output_text = ""
                            if isinstance(result.output, dict):
                                raw_output_text = str(result.output.get("content") or result.output.get("result") or "")
                            elif isinstance(result.output, str):
                                raw_output_text = result.output[:500]
                            await self._emit(
                                {
                                    "type": "tool_call_complete",
                                    "tool": tool_ref.name,
                                    "server_id": tool_ref.server_id,
                                    "success": result.success,
                                    "output": self._sanitize_tool_event_value(result.output),
                                    "error": self._sanitize_tool_event_value(result.error),
                                    # tool_output preserves the raw structured dict for result_artifacts.py
                                    # without truncation so downstream consumers can access full data.
                                    "tool_output": result.output if isinstance(result.output, dict) else None,
                                }
                            )
                            # Check for artifact capture (RPA screenshot etc.)
                            # result is always ToolCallResult — use getattr not dict access
                            _artifact_uri: str = getattr(result, "artifact_url", "") or ""
                            _artifact_name: str = getattr(result, "artifact_name", "") or ""
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

        # GuardrailEngine v2: scan output for PII/secrets/cloud-destruction patterns
        _guardrail_engine_v2_out = (
            getattr(self._app_state, "guardrail_engine", None) if self._app_state else None
        )
        if _guardrail_engine_v2_out is not None and raw_output:
            try:
                from app.intelligence.guardrail_engine import GuardrailContext as _GCtxOut
                _ge_out_ctx = _GCtxOut(
                    tenant_id=tenant_ctx.tenant_id if tenant_ctx else "",
                    goal_id=state.goal_id or "",
                    agent_id=self._agent_id or "",
                    domain=getattr(tenant_ctx, "domain_context", "general") if tenant_ctx else "general",
                )
                _ge_out_result = await _guardrail_engine_v2_out.evaluate_tool_output(
                    tool_name=tool_name,
                    output=str(raw_output),
                    context=_ge_out_ctx,
                )
                if _ge_out_result.redacted_content:
                    raw_output = _ge_out_result.redacted_content
            except Exception as _ge_out_exc:
                self._logger.warning("guardrail_engine_v2_output_check_failed", error=str(_ge_out_exc))

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

        # 13. Claim grounding check — verify LLM claims against tool outputs.
        # SKIP when raw_output is already a structured tool result (JSON/dict),
        # as it IS the evidence and cannot be "ungrounded" against itself.
        try:
            from app.agent.grounding import annotate_ungrounded, check_grounding
            _raw_stripped = (raw_output or "").strip()
            _is_structured_tool_output = _raw_stripped.startswith(('{', '[', "{'"))
            _tool_outputs_for_grounding = [
                str(tc.get("output", ""))
                for tc in (state.steps[-1].tool_calls if state.steps else [])
                if tc.get("output")
            ]
            if raw_output and _tool_outputs_for_grounding and not _is_structured_tool_output:
                _ground_result = check_grounding(
                    output=raw_output,
                    tool_outputs=_tool_outputs_for_grounding,
                )
                if not _ground_result.grounded:
                    self._logger.info(
                        "grounding_failed",
                        ungrounded=_ground_result.ungrounded_claims[:3],
                        step=step[:100],
                    )
                    raw_output = annotate_ungrounded(raw_output, _ground_result)
                    state.ungrounded_claims.extend(_ground_result.ungrounded_claims[:5])
                    # C4: Mark the current step as UNGROUNDED
                    if state.steps:
                        _last_step = state.steps[-1]
                        if hasattr(_last_step, "status"):
                            from app.agent.state import StepStatus
                            _last_step.status = StepStatus.UNGROUNDED
                    await self._emit({
                        "type": "grounding_warning",
                        "ungrounded_claims": _ground_result.ungrounded_claims[:5],
                        "step": step,
                    })
                state.context["grounding_checked"] = True
        except Exception as exc:
            # Log but don't block execution — fail-open only on grounding check errors
            self._logger.warning("grounding_check_error", error=str(exc)[:80])

        # M12: Update session memory with step output
        try:
            _session_mem = getattr(self, "_session_memory", None)
            if _session_mem is not None and hasattr(_session_mem, "add"):
                _session_mem.add(
                    key=f"step_{len(state.steps)}",
                    value={"description": step, "output": (raw_output or "")[:500]},
                )
        except Exception:
            pass

        return raw_output

    async def _execute_step_with_cache(
        self, step: str, state: AgentState, tenant_ctx: TenantContext
    ) -> str:
        """
        Execute a step using the world-class semantic cache.

        True cosine-similarity matching (threshold 0.92) means paraphrases like
        "Search GitHub for open issues" and "Find open GitHub issues" both hit
        the same cache entry — no more exact-match-only limitation.

        Flow:
          1. Embed the step description (single API call, ~50ms)
          2. L1 lookup: in-process LRU (sub-millisecond, no network)
          3. L2 lookup: Redis vector scan (cosine similarity, ~5ms)
          4. Cache MISS → execute step fully → store result in L1+L2
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
                    # Use the new true-similarity API
                    hit = await self._semantic_cache.get_similar(
                        embedding=_cache_embedding,
                        tenant_id=tenant_ctx.tenant_id,
                    )
                    # Only serve non-empty, non-error responses from cache.
                    # Empty responses (stored by failed prior runs) must be ignored.
                    if hit is not None and hit.response and len(hit.response.strip()) >= 10:
                        await self._emit({
                            "type": "cache_hit",
                            "step": step,
                            "similarity": round(hit.similarity, 4),
                            "source": hit.source,
                            "latency_ms": round(hit.latency_ms, 1),
                        })
                        return hit.response
            except Exception as _ce:
                _cache_embedding = None
                self._logger.debug("cache_embed_failed", error=str(_ce)[:80])

        raw_output = await self._execute_step(step, state, tenant_ctx)

        # Store result — skip caching error responses so bad LLM outputs
        # (API errors, model-not-found messages, timeouts) never poison the cache.
        # Also skip caching empty/minimal results — they often represent transient
        # failures (401 auth, wrong JQL, empty project) and should not be served
        # as "correct" cached answers on future runs.
        # CRITICAL: Never cache plain-text "I'll call..." executor reasoning text.
        # Only cache actual tool call results (JSON or clearly structured output).
        _out_stripped = (raw_output or "").strip()
        _looks_like_llm_reasoning = (
            _out_stripped.lower().startswith((
                "i'll ", "i will ", "i'll use", "i will use",
                "to complete", "let me ", "i need to ", "i can ", "i should ",
                "step 1", "first,", "first i", "i'll now",
                "i'll start", "i'll call", "i'll search",
                "now i'll", "next, i", "to search",
            ))
            or ("will use" in _out_stripped.lower() and "tool" in _out_stripped.lower())
            or ("will call" in _out_stripped.lower() and len(_out_stripped) < 500)
        )
        _is_error_output = (
            not raw_output
            or raw_output.strip().startswith("{\"error")
            or "model_not_found" in raw_output.lower()
            or "invalid model" in raw_output.lower()
            or "rate_limit_exceeded" in raw_output.lower()
            or raw_output.strip().lower().startswith("error:")
            or "mcp client unavailable" in raw_output.lower()
            or "tool not available" in raw_output.lower()
            or "argument validation failed" in raw_output.lower()
            or "circuit open" in raw_output.lower()
            or "requires approval" in raw_output.lower()
            # Don't cache empty collection results (Jira 0 issues, empty lists)
            or raw_output.strip() in ('{"issues": [], "total": 0}', '{"projects": []}', '{"items": []}', '[]', '{}')
            or '"total": 0' in raw_output
            or '"issues": []' in raw_output
            or '"projects": []' in raw_output
            or len(raw_output.strip()) < 10
            # Don't cache plain LLM reasoning text (no actual tool result)
            or _looks_like_llm_reasoning
        )
        if self._semantic_cache is not None and _cache_embedding is not None and not _is_error_output:
            try:
                await self._semantic_cache.store_async(
                    embedding=_cache_embedding,
                    query=step,
                    response=raw_output,
                    tenant_id=tenant_ctx.tenant_id,
                )
            except Exception:
                pass  # write failures must never block execution

        return raw_output

    async def _node_verify(self, state: GraphState) -> dict[str, Any]:
        agent_state: AgentState = state["agent_state"]
        tenant_ctx: TenantContext = state["tenant_ctx"]

        agent_state.status = GoalStatus.VERIFYING

        # Build a rich step summary that explicitly flags failed tool calls so
        # the verifier LLM doesn't hallucinate success when tools errored out.
        # Uses module-level _build_verifier_summary to include ALL failed steps.
        summary = _build_verifier_summary(agent_state.steps)
        try:
            from app.agent.prompt_compressor import _default_compressor as _pc
            summary = _pc.compress(summary)
        except Exception:
            pass
        # N9: Prepend verifier context from ContextPipeline
        _verif_ctx = agent_state.context.get("_verifier_context", "") or ""
        if _verif_ctx and len(_verif_ctx) > 50:
            summary = (
                f"[Context for verification]\n{_verif_ctx[:800]}\n\n{summary}"
            )
        # Resolve verifier model via model_router when available (Bug 3 fix)
        _verify_model = ""
        if self._model_router is not None:
            try:
                _verify_model = self._model_router.model_for("verification") or ""
            except Exception:
                pass
        # ── LLM Response Cache for verifier ───────────────────────────────────
        _llm_rc = getattr(self, "_llm_response_cache", None)
        _verify_cached = False
        _verify_user = f"Goal: {agent_state.goal}\nExecuted steps:\n{summary}"
        if _llm_rc is not None and not _llm_rc.should_skip_cache(_verify_user):
            try:
                _cached_verify = await _llm_rc.get(
                    system=VERIFIER_SYSTEM,
                    user=_verify_user,
                    model=_verify_model,
                    tenant_id=tenant_ctx.tenant_id,
                    task_type="verification",
                )
                if _cached_verify is not None:
                    self._logger.info("llm_cache_verify_hit", tenant=tenant_ctx.tenant_id)
                    class _FakeResp:
                        content = _cached_verify
                    resp = _FakeResp()
                    _verify_cached = True
            except Exception:
                pass
        if not _verify_cached:
            # Use structured output when available (Phase 3 Track A)
            _verify_response_schema = None
            try:
                from app.agent.schemas import verifier_schema
                if (
                    hasattr(self._verifier, "supports_structured_output")
                    and self._verifier.supports_structured_output()
                ):
                    _verify_response_schema = verifier_schema()
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
                response_schema=_verify_response_schema,
            )
            with self._tracer.start_as_current_span("agentverse.verify") as span:
                span.set_attribute("verify.iteration", agent_state.iterations)
                _verify_start = time.monotonic()
                try:
                    resp = await call_with_circuit_breaker(
                        self._verifier, "complete", req,
                        provider_name=type(self._verifier).__name__,
                    )
                except RuntimeError as cb_exc:
                    raise PermissionError(f"Verification unavailable: {cb_exc}") from cb_exc
                record_verify_duration(time.monotonic() - _verify_start)
            # 2.3: Per-goal verifier cost tracking
            try:
                from app.observability.cost_breakdown import record_role_cost as _rrc
                _rrc(
                    goal_id=agent_state.goal_id,
                    role="verifier",
                    model=_verify_model,
                    input_tok=getattr(resp, "input_tokens", 0),
                    output_tok=getattr(resp, "output_tokens", 0),
                    cost=0.0,
                )
            except Exception:
                pass
            # Store in LLM cache — only on successful, non-error responses
            if _llm_rc is not None:
                try:
                    _is_error_verify = (
                        not resp.content
                        or resp.content.strip().startswith("{\"error")
                        or "model_not_found" in resp.content.lower()
                    )
                    if not _is_error_verify:
                        await _llm_rc.set(
                            system=VERIFIER_SYSTEM,
                            user=f"Goal: {agent_state.goal}\nExecuted steps:\n{summary}",
                            model=_verify_model,
                            response=resp.content,
                            tenant_id=tenant_ctx.tenant_id,
                            task_type="verification",
                        )
                except Exception:
                    pass
        # Phase 3 Track A: use parse_verifier_verdict (handles JSON and text fallback)
        from app.agent.schemas import parse_verifier_verdict
        parsed = parse_verifier_verdict(resp.content)
        success: bool = bool(parsed.get("success", False))
        reason: str = self._sanitize_tool_raw_output(parsed.get("reason", ""))
        # Store retry flag for routing: True = can replan, False = permanently blocked
        retry: bool = bool(parsed.get("retry", True)) if not success else True
        agent_state.context["verification_retry"] = retry

        # C5: 3-way consensus for high-risk goals
        if self._consensus_verifier is not None and not success:
            # Only attempt consensus when primary verifier says fail (to save cost)
            try:
                from app.agent.consensus import requires_consensus
                tool_risks = [
                    tc.risk_level
                    for step in agent_state.steps
                    for tc in getattr(step, "tool_calls", [])
                ]
                if requires_consensus(
                    agent_state.goal,
                    agent_state.context.get("domain"),
                    tool_risks,
                ):
                    consensus_result = await self._consensus_verifier.verify(
                        goal=agent_state.goal,
                        summary=summary,
                        model=_verify_model,
                    )
                    success = consensus_result.success
                    reason = consensus_result.majority_reason or reason
                    # HITL if disagreement
                    if consensus_result.requires_hitl and self._hitl_gateway is not None:
                        req_id = str(self._hitl_gateway.request_approval(
                            goal_id=agent_state.goal_id,
                            action=f"Consensus disagreement on goal: {agent_state.goal[:100]}",
                            risk_level="high",
                            tenant_ctx=tenant_ctx,
                        ))
                        self._logger.info("consensus_hitl_requested", req_id=req_id)
            except Exception as exc:
                self._logger.warning("consensus_verify_failed", error=str(exc)[:80])

        agent_state.verification_success = success
        agent_state.verification_feedback = reason
        await self._emit({"type": "verification_done", "success": success, "reason": reason})

        # Record verifier verdict for calibration (Phase 3 Track E)
        try:
            from app.intelligence.verifier_calibration import _default_calibration_store
            _cal_store = getattr(self, "_calibration_store", _default_calibration_store)
            if _cal_store is not None:
                _cal_task = asyncio.create_task(
                    _cal_store.record_verdict(
                        goal_id=agent_state.goal_id,
                        tenant_id=tenant_ctx.tenant_id,
                        verifier_verdict=success,
                        verifier_model=_verify_model,
                        iteration=agent_state.iterations,
                        goal_text=agent_state.goal,
                    )
                )
                self._background_tasks.add(_cal_task)
                _cal_task.add_done_callback(self._background_tasks.discard)
        except Exception as exc:
            self._logger.debug("calibration_record_failed", error=str(exc)[:60])

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

            # N3: Compute actual latency before scoring so RuntimeScorecard gets a real value
            try:
                import time as _lat_time
                _start_ms = agent_state.context.get("_goal_start_ms", 0.0)
                if _start_ms > 0:
                    agent_state.context["_latency_ms"] = (
                        _lat_time.monotonic() * 1000 - _start_ms
                    )
            except Exception:
                pass

            # Dynamic orchestration: scorecard + self-improvement + reflexion
            try:
                from app.core.runtime_flags import get_runtime_flags as _nv_rtf
                from app.evals.runtime_scorecard import RuntimeScorecard
                _nv_flags = _nv_rtf()
                _profile = agent_state.context.get("_runtime_profile")
                if (
                    (_nv_flags.dynamic_orchestration or _nv_flags.enable_runtime_scorecard)
                    and _profile is not None
                ):
                    _scorecard = RuntimeScorecard()
                    _guardrail_violations = sum(
                        1
                        for event in agent_state.events
                        if isinstance(event, dict)
                        and (
                            event.get("action_level") == "DENY"
                            or event.get("outcome") == "denied"
                            or event.get("type") in {"tool_call_denied", "guardrail_rejected"}
                        )
                    )
                    _scorecard_result = _scorecard.score(
                        state=agent_state,
                        profile=_profile,
                        retrieval_result=agent_state.context.get(
                            "runtime_retrieval_evidence"
                        ),
                        cost_usd=agent_state.context.get("total_cost_usd"),
                        latency_ms=agent_state.context.get("_latency_ms"),
                        guardrail_violations=_guardrail_violations,
                    )
                    agent_state.context["scorecard"] = _scorecard_result.to_dict()
                    # Persist scorecard to eval_scorecards table
                    try:
                        _orch_persist = (
                            getattr(self._app_state, "orchestration_persistence", None)
                            if self._app_state else None
                        )
                        if _orch_persist is not None:
                            import asyncio as _sc_asyncio
                            _sc_task = _sc_asyncio.ensure_future(
                                _orch_persist.persist_scorecard(
                                    _scorecard_result, profile=_profile
                                )
                            )
                            if hasattr(self, "_background_tasks"):
                                self._background_tasks.add(_sc_task)
                                _sc_task.add_done_callback(self._background_tasks.discard)
                    except Exception:
                        pass
                    # RegressionGate: catalogue low-scoring goals as regression cases
                    try:
                        from app.evals.regression_gate import RegressionGate
                        _rg = RegressionGate()
                        _regression_candidate = _rg.maybe_create_regression(
                            state=agent_state,
                            scorecard=_scorecard_result,
                            profile=_profile,
                        )
                        if _regression_candidate:
                            agent_state.context["regression_candidate"] = _regression_candidate
                            # Persist regression case
                            try:
                                _orch_p = (
                                    getattr(self._app_state, "orchestration_persistence", None)
                                    if self._app_state else None
                                )
                                if _orch_p is not None and hasattr(_orch_p, "persist_regression_case"):
                                    import asyncio as _rc_asyncio
                                    _rc_asyncio.ensure_future(
                                        _orch_p.persist_regression_case(
                                            {**_regression_candidate, "tenant_id": tenant_ctx.tenant_id}
                                        )
                                    )
                            except Exception:
                                pass
                    except Exception:
                        pass
                    # N12: Gate self-improvement on granular flag
                    _si_enabled = (
                        _nv_flags.dynamic_orchestration or _nv_flags.enable_self_improvement
                    )
                    _actions: list[Any] = []
                    if _si_enabled:
                        from app.evals.self_improvement_engine import SelfImprovementEngine
                        _engine = SelfImprovementEngine()
                        _actions = _engine.decide_actions(
                            _scorecard_result, _profile, state=agent_state
                        )
                    agent_state.context["improvement_actions"] = [
                        a.action_type.value for a in _actions
                    ]
                    # Dispatch improvement actions
                    try:
                        for _action in _actions:
                            _action_type = (
                                _action.action_type.value
                                if hasattr(_action, "action_type") else str(_action)
                            )
                            if "STORE_REFLEXION_LESSON" in _action_type:
                                pass  # handled by reflexion_wirer in failure branch
                            elif "UPDATE_PROMPT_VARIANT" in _action_type:
                                _po = (
                                    getattr(self._app_state, "prompt_optimizer", None)
                                    if self._app_state else None
                                )
                                if _po is not None and hasattr(_po, "record_result"):
                                    _variant_id = agent_state.context.get("planner_variant_id")
                                    if _variant_id:
                                        _po.record_result(
                                            variant_id=_variant_id,
                                            eval_score=_scorecard_result.overall_score,
                                        )
                            elif "SWITCH_MODEL" in _action_type or "UPDATE_MODEL_ROUTING" in _action_type:
                                # N7a: Persist model switch recommendation to agent config
                                try:
                                    if self._app_state is not None and self._agent_id is not None:
                                        _agent_store = getattr(self._app_state, "agent_store", None)
                                        if _agent_store is not None and hasattr(_agent_store, "update_config"):
                                            import asyncio as _mc_asyncio
                                            _mc_asyncio.ensure_future(
                                                _agent_store.update_config(
                                                    agent_id=self._agent_id,
                                                    tenant_ctx=tenant_ctx,
                                                    config_patch={
                                                        "model_downgrade_recommended": True,
                                                        "last_switch_reason": "low_eval_score",
                                                        "last_switch_score": _scorecard_result.overall_score,
                                                    },
                                                )
                                            )
                                except Exception:
                                    pass
                                from app.observability.logging import get_logger
                                get_logger(__name__).warning(
                                    "self_improvement_model_switch_applied",
                                    goal_id=agent_state.goal_id,
                                    score=_scorecard_result.overall_score,
                                )
                            elif "BLACKLIST_TOOL_PATTERN" in _action_type:
                                # N7b: Record tool as unreliable in ToolReliabilityStore
                                try:
                                    _tr_store = getattr(self, "_tool_reliability_store", None)
                                    if _tr_store is not None:
                                        # Find failed tools from recent steps
                                        _failed_tools: list[str] = []
                                        for _s in agent_state.steps:
                                            for _tc in (getattr(_s, "tool_calls", None) or []):
                                                if isinstance(_tc, dict) and not _tc.get("success", True):
                                                    _tn = _tc.get("tool_name", "")
                                                    if _tn and _tn not in _failed_tools:
                                                        _failed_tools.append(_tn)
                                        for _ft in _failed_tools[:3]:
                                            import asyncio as _bl_asyncio
                                            _bl_asyncio.ensure_future(
                                                _tr_store.record(
                                                    tool_name=_ft,
                                                    tenant_id=tenant_ctx.tenant_id,
                                                    success=False,
                                                    latency_ms=5000.0,
                                                    error="blacklisted_by_self_improvement",
                                                )
                                            )
                                        agent_state.context["_blacklisted_tools"] = _failed_tools[:3]
                                except Exception:
                                    pass
                                from app.observability.logging import get_logger
                                get_logger(__name__).warning(
                                    "self_improvement_tool_blacklisted",
                                    goal_id=agent_state.goal_id,
                                    score=_scorecard_result.overall_score,
                                )
                    except Exception:
                        pass
                    # N6c: self_improvement_suggested SSE
                    try:
                        if self._event_callback is not None and _actions:
                            from app.observability.runtime_decision_trace import RuntimeSSEEmitter
                            _sse_sis = RuntimeSSEEmitter()
                            await self._emit(_sse_sis.self_improvement_suggested(
                                goal_id=agent_state.goal_id,
                                suggestions=[
                                    a.action_type.value if hasattr(a, "action_type") else str(a)
                                    for a in _actions
                                ],
                            ))
                    except Exception:
                        pass
                    # Emit eval_score_recorded SSE (N12: gated on pattern SSE flag)
                    if _nv_flags.dynamic_orchestration or _nv_flags.enable_pattern_sse_events:
                        try:
                            from app.observability.runtime_decision_trace import RuntimeSSEEmitter
                            _sse_emitter = RuntimeSSEEmitter()
                            await self._emit(_sse_emitter.eval_score_recorded(
                                goal_id=agent_state.goal_id,
                                overall_score=_scorecard_result.overall_score,
                                scores=_scorecard_result.scores,
                            ))
                        except Exception:
                            pass
            except Exception:
                pass
            # Guardrail check: final_output (Guardrails 2.0)
            if _GUARDRAILS_AVAILABLE and guardrails_engine is not None and tenant_ctx:
                try:
                    _g2_final_content = (
                        agent_state.cited_answer
                        or " ".join(s.output[:200] for s in agent_state.steps if s.output)
                        or agent_state.verification_feedback
                    )
                    if _g2_final_content:
                        _g2_final_result = await guardrails_engine.evaluate(
                            content=str(_g2_final_content)[:2000],
                            layer=GuardrailLayer.FINAL_OUTPUT,
                            tenant_id=tenant_ctx.tenant_id,
                            goal_id=getattr(agent_state, "goal_id", None),
                        )
                        if _g2_final_result.get("blocked"):
                            agent_state.cited_answer = "[Output redacted by guardrail policy]"
                except Exception:
                    pass  # Guardrail errors must never block completion

            # Phase 3 Track C: synthesize cited answer on success
            if self._answer_synthesizer is not None:
                try:
                    cited = await self._answer_synthesizer.synthesize(
                        goal=agent_state.goal,
                        steps=agent_state.steps,
                        tenant_id=tenant_ctx.tenant_id,
                    )
                    agent_state.cited_answer = cited.answer
                    agent_state.provenance = [
                        {"text": c.text, "source": c.source, "step": c.step_index}
                        for c in cited.citations
                    ]
                    await self._emit({
                        "type": "synthesis_complete",
                        "cited_answer": cited.answer[:2000],
                        "citations": [{"text": c.text, "source": c.source} for c in cited.citations],
                    })
                except Exception as exc:
                    self._logger.debug("synthesis_failed", error=str(exc)[:60])

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
                        _v2_task = _asyncio.create_task(
                            _self_opt_v2.on_goal_completed(
                                tenant_id=tenant_ctx.tenant_id,
                                agent_id=self._agent_id,
                                goal_id=agent_state.goal_id,
                                eval_score=_eval_score,
                                cost_usd=float(agent_state.context.get("total_cost_usd", 0.0)),
                                latency_ms=0,
                            )
                        )
                        self._background_tasks.add(_v2_task)
                        _v2_task.add_done_callback(self._background_tasks.discard)
            # N5: Record result in module-level ABTestingEngine for cross-goal A/B analysis
            try:
                from app.optimization.ab_testing import ExperimentType
                from app.optimization.ab_testing import ab_testing_engine as _abt_eng
                if _abt_eng is not None and _eval_score is not None and tenant_ctx is not None:
                    import asyncio as _n5_asyncio
                    _abt_asyncio_task = _n5_asyncio.ensure_future(
                        _abt_eng.record_result_async(
                            goal_id=agent_state.goal_id,
                            experiment_type=ExperimentType.RAG_STRATEGY,
                            arm_id=agent_state.context.get("_experiment_arm", "control"),
                            score=float(_eval_score),
                            tenant_id=tenant_ctx.tenant_id,
                        )
                    )
                    if hasattr(self, "_background_tasks"):
                        self._background_tasks.add(_abt_asyncio_task)
                        _abt_asyncio_task.add_done_callback(self._background_tasks.discard)
            except Exception:
                pass
        else:
            scorecard = None
            # FIX: On permanent failure (retry=False), roll back all registered actions
            # using the async method to guarantee each inverse completes before moving on.
            if not retry and self._rollback_engine is not None and len(self._rollback_engine) > 0:
                rolled = await self._rollback_engine.rollback_all_async()
                self._logger.info("agent_rollback_complete", rolled_back=rolled)

            # Reflexion: store failure lesson (C1 fix — was incorrectly in success branch)
            try:
                from app.agent.reflexion_wirer import get_reflexion_wirer
                _rw = get_reflexion_wirer()
                import asyncio as _rf_asyncio
                _rf_asyncio.ensure_future(_rw.maybe_store_async(agent_state))
            except Exception:
                pass

            # H28: Also persist via OrchestrationPersistence (belt-and-suspenders)
            try:
                _orch_p = (
                    getattr(self._app_state, "orchestration_persistence", None)
                    if self._app_state else None
                )
                if _orch_p is not None and hasattr(_orch_p, "persist_reflexion_lesson"):
                    import asyncio as _rl_asyncio
                    _rl_asyncio.ensure_future(
                        _orch_p.persist_reflexion_lesson(agent_state)
                    )
            except Exception:
                pass

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

        # Trigger self-optimization when a goal scores below the excellence threshold.
        # Using < 0.5 ensures we only collect improvement insights for genuinely failing
        # goals (below 50% score), avoiding unnecessary optimization churn on good runs.
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

        # Record episodic experience after goal completion (success or failure)
        try:
            if self._episodic_memory is not None and tenant_ctx is not None:
                _ep_quality = float(
                    agent_state.context.get("scorecard", {}).get("overall_score", 0.5)
                    if isinstance(agent_state.context.get("scorecard"), dict)
                    else 0.5
                )
                _ep_task = asyncio.ensure_future(
                    self._episodic_memory.record(
                        state=agent_state,
                        tenant_ctx=tenant_ctx,
                        quality_score=_ep_quality,
                    )
                )
                if hasattr(self, "_background_tasks"):
                    self._background_tasks.add(_ep_task)
                    _ep_task.add_done_callback(self._background_tasks.discard)
        except Exception:
            pass

        # Learn procedural skill after successful goal
        try:
            if self._procedural_memory is not None and success and tenant_ctx is not None:
                _proc_task = asyncio.ensure_future(
                    self._procedural_memory.learn(
                        state=agent_state,
                        tenant_ctx=tenant_ctx,
                        success=success,
                    )
                )
                if hasattr(self, "_background_tasks"):
                    self._background_tasks.add(_proc_task)
                    _proc_task.add_done_callback(self._background_tasks.discard)
        except Exception:
            pass

        return {"agent_state": agent_state}

    # ------------------------------------------------------------------
    # Routing (synchronous — LangGraph calls this synchronously)
    # ------------------------------------------------------------------

    def _max_reflection_rounds(self) -> int:
        return max(
            0,
            min(
                2,
                int(
                    getattr(
                        getattr(self._runtime_profile, "effective_limits", None),
                        "rounds",
                        2,
                    )
                ),
            ),
        )

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

        # ── Stagnation detection ────────────────────────────────────────────
        # If the last 3 verification feedbacks are identical (agent keeps making
        # the same mistake) — stop immediately rather than burning all iterations.
        _feedback_history: list[str] = agent_state.context.get("_feedback_history", [])
        _current_feedback = agent_state.verification_feedback or ""
        if _current_feedback:
            _feedback_history = (_feedback_history + [_current_feedback])[-6:]
            agent_state.context["_feedback_history"] = _feedback_history

        if len(_feedback_history) >= 3 and len(set(_feedback_history[-3:])) == 1:
            agent_state.status = GoalStatus.FAILED
            agent_state.error_message = (
                "Goal stagnated: agent repeated the same failing approach 3 times in a row. "
                f"Last feedback: {_current_feedback[:200]}"
            )
            record_goal_failed(tenant_id=agent_state.tenant_ctx.tenant_id)
            return "max_iter"

        # Also stop if plan steps haven't changed for 3 iterations
        # (same plan, same failure = wrong agent/tools)
        _plan_history: list[str] = agent_state.context.get("_plan_history", [])
        _current_plan_key = "|".join(agent_state.plan[:3]) if agent_state.plan else ""
        if _current_plan_key:
            _plan_history = (_plan_history + [_current_plan_key])[-4:]
            agent_state.context["_plan_history"] = _plan_history

        if len(_plan_history) >= 3 and len(set(_plan_history[-3:])) == 1:
            agent_state.status = GoalStatus.FAILED
            agent_state.error_message = (
                "Goal stagnated: same plan was repeated 3 times without success. "
                "Check that the agent has the right connectors for this goal."
            )
            record_goal_failed(tenant_id=agent_state.tenant_ctx.tenant_id)
            return "max_iter"

        # Supervised mode: pause if any HITL requests are pending
        if self._autonomy_mode == "supervised" and self._hitl_gateway is not None:
            tenant_ctx: TenantContext | None = state.get("tenant_ctx")
            if tenant_ctx is not None:
                pending = self._hitl_gateway.list_pending(tenant_ctx=tenant_ctx, goal_id=agent_state.goal_id)
                if pending:
                    agent_state.status = GoalStatus.WAITING_HUMAN
                    return "waiting_human"

        # Context-gap detection (doc-2 §9.3) — route to rag_remediate before replanning
        try:
            from app.rag.agentic.context_gap_detector import ContextGapDetector
            _gap_detector = ContextGapDetector()
            _remediation_count = agent_state.context.get("remediation_count", 0)
            if (agent_state.context.get("allow_rag_remediation", False)
                    and not agent_state.verification_success
                    and _gap_detector.has_gap(agent_state.verification_feedback or "")
                    and _remediation_count < 2):
                return "rag_remediate"
        except Exception:
            pass  # never crash routing

        # This router only runs after verification, so a negative verification
        # result is itself sufficient evidence to enter the bounded reflection
        # path even when the verifier omitted explanatory text.
        if self._enable_reflection:
            reflection_attempts = int(
                agent_state.context.get("reflection_attempts", 0)
            )
            if reflection_attempts < self._max_reflection_rounds():
                return "reflect"
            evidence = {
                "strategy_id": "reflection",
                "adapter_version": "1.0.0",
                "status": "exhausted",
                "call_count": 0,
                "limit_reason": "reflection_round_limit",
                "attempt": reflection_attempts,
            }
            prior = agent_state.context.setdefault("reasoning_evidence", [])
            if not prior or prior[-1] != evidence:
                prior.append(evidence)
            agent_state.context["terminal_reason"] = "reflection_exhausted"
            agent_state.status = GoalStatus.FAILED
            agent_state.error_message = "Reflection round limit exhausted."
            record_goal_failed(tenant_id=agent_state.tenant_ctx.tenant_id)
            return "max_iter"
        return "replan"

    def _route_after_execute(self, state: GraphState) -> str:
        agent_state: AgentState = state["agent_state"]
        return "failed" if agent_state.status is GoalStatus.FAILED else "continue"

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
        goal_id: str | None = None,
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

                self._event_callback = event_callback
                # Extract civilization_id from initial_context for spawn tool support
                if initial_context and isinstance(initial_context, dict):
                    civ_id = initial_context.get("civilization_id")
                    if civ_id:
                        self._civilization_id = civ_id
                        self._civilization_spawn_enabled = True
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
                            if (saved_state is not None
                                    and hasattr(saved_state, "steps")
                                    and saved_state.steps):
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
                        type(exc).__name__, str(exc)[:200],
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
