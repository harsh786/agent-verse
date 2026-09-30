"""GoalService — in-memory goal lifecycle management.

Responsible for:
  - Accepting goal submissions and launching AgentGraph as asyncio background tasks
  - Tracking goal status and appending SSE events
  - Fanning out events to all live SSE subscribers via per-goal asyncio.Queue
  - Delegating audit-log queries and HITL approval to governance components
  - Per-tenant LLM provider dispatch (Fix 8): reads encrypted API keys from vault
    and builds real provider instances when a tenant has configured one.
"""

from __future__ import annotations

import asyncio
import collections
import json
import os
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from opentelemetry import trace

from app.observability.logging import get_logger as _get_logger

_svc_logger = _get_logger(__name__)


# Module-level pause event registry (not a class attr to avoid circular)
_GOAL_PAUSE_EVENTS: dict[str, asyncio.Event] = {}
_PAUSE_POLL_SECONDS = 2.0


def _tenant_llm_kwargs(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """``tenant_llm_config=`` only when one was resolved (keeps subclass
    overrides of ``_make_agent_loop_for_tenant`` without the kwarg working)."""
    return {"tenant_llm_config": cfg} if cfg is not None else {}


# goals columns filled from the runtime profile (see GoalService._build_runtime_profile).
_RUNTIME_PROFILE_COLUMNS = frozenset(
    {
        "runtime_profile_id",
        "runtime_profile_version",
        "strategy_registry_revision",
        "runtime_profile_snapshot",
        "rejected_strategies",
        "patterns_used",
        "rag_strategy_used",
    }
)


def _profile_column_kwargs(columns: dict[str, Any] | None) -> dict[str, Any]:
    """``runtime_profile_columns=`` only when a profile was built (same reason as above)."""
    return {"runtime_profile_columns": columns} if columns else {}


# execution_context flag: the goal's graph ended waiting for approvals (see
# GoalService._suspend_for_approval); resume must relaunch it.
_SUSPENDED_KEY = "_suspended_for_approval"
# execution_context: fingerprint of the goal's dedup scope (see services/dedup.py)
_DEDUP_SCOPE_KEY = "_dedup_scope"
# execution_context key naming who runs the goal: {"kind": "worker"} (Celery —
# redelivery and the stuck-goal sweeper own it) or {"kind": "in_process",
# "replica": <GoalService._replica_id>}. Restart recovery only touches the latter,
# and only once that replica's heartbeat is gone.
_RUNNER_KEY = "runner"
_RUNNER_WORKER = "worker"
_RUNNER_IN_PROCESS = "in_process"
_REPLICA_ALIVE_KEY = "goal_replica_alive:{replica}"
_REPLICA_HEARTBEAT_TTL_SECONDS = 45
_REPLICA_HEARTBEAT_INTERVAL_SECONDS = 15
# Worker per-goal execution lock (scaling/tasks.py _SyncGoalLock.KEY_PREFIX).
_WORKER_GOAL_LOCK_KEY = "goal_lock:{goal_id}"
# Events that produce usage records (see app/services/usage_metering.py).
_METERED_EVENT_TYPES = {"tool_call_complete", "goal_complete", "goal_failed", "goal_cancelled"}
from app.agent.sanitization import sanitize_event
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.agent.tool_context import ToolContext, ToolRef
from app.agent.workflow_executor import WorkflowExecutor
from app.agent.workflow_planner import build_static_workflow
from app.core.errors import ConflictError, NotFoundError, ServiceUnavailableError
from app.governance.audit import AuditLog
from app.governance.hitl import HITLGateway
from app.observability.metrics import record_goal_duration, record_goal_started
from app.providers.fake import FakeProvider
from app.reliability.goal_lifecycle import GoalCancelledError

# Sub-module imports — part of ongoing decomposition to reduce God-class size
# See: app/services/goal_events.py, goal_metrics.py, goal_lifecycle.py
from app.services.goal_lifecycle import GoalTransition, is_valid_transition  # noqa: F401
from app.services.goal_queue import GoalTaskQueue
from app.services.result_artifacts import build_result_artifact
from app.tenancy.context import PlanTier, TenantContext

# Module-level OTel tracer — no-ops cleanly when no exporter is configured.
_tracer = trace.get_tracer(__name__)

# Poison-pill sentinel — placed on a subscriber queue to signal end-of-stream.
_SENTINEL: dict[str, Any] | None = None
_TERMINAL_STATUSES = {GoalStatus.COMPLETE, GoalStatus.FAILED, GoalStatus.CANCELLED}
# Recovery never re-runs these: terminal, or parked waiting for a human
# (a suspended goal is relaunched by resume_goal, not by restart recovery).
_NOT_RECOVERABLE_STATUSES = {*_TERMINAL_STATUSES, GoalStatus.WAITING_HUMAN}


def _agent_grants_enforced() -> bool:
    """Whether Grantex tool-grant enforcement is on (opt-in, default off)."""
    try:
        from app.core.config import get_settings

        return bool(getattr(get_settings(), "enforce_agent_grants", False))
    except Exception:
        return False

# TTL for completed/failed/cancelled goals in the in-memory cache.
# They are safe to evict because they are already persisted in the DB.
_COMPLETED_GOAL_TTL_SECONDS = 3600  # 1 hour
_EVICTION_INTERVAL_SECONDS = 60  # evict at most once every 60 seconds


def _monotonic() -> float:
    return time.monotonic()


# ── domain model ──────────────────────────────────────────────────────────────


@dataclass
class GoalRecord:
    """Runtime state for a single submitted goal."""

    goal_id: str
    goal_text: str
    status: GoalStatus
    tenant_id: str
    priority: str
    dry_run: bool
    created_at: str  # ISO-8601
    agent_id: str | None = None
    workflow_mode: str = "single_agent"
    execution_context: dict[str, Any] = field(default_factory=dict)
    runtime_profile: Any | None = field(default=None, repr=False)
    # The profile as built, for OBSERVATION only (scorecards/evals), even when the
    # rollout does not let it drive execution (``runtime_profile`` is then None).
    observed_runtime_profile: Any | None = field(default=None, repr=False)
    events: list[dict[str, Any]] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
    subscribers: list[asyncio.Queue[dict[str, Any] | None]] = field(default_factory=list)
    started_monotonic: float = field(default_factory=lambda: _monotonic())
    terminal_metrics_recorded: bool = False
    # Goal-chain lifecycle channels already published for this record
    # ("goal.completed" / "goal.failed" / "goal.score_below").
    chain_events_published: set[str] = field(default_factory=set)
    # Timestamp when the goal entered a terminal state (complete/failed/cancelled).
    # Used by _evict_stale_goals() to avoid evicting goals that just finished.
    completed_at: str | None = None
    # Phase 12: rejection note from HITL operator — passed to planner for replanning
    hitl_rejection_note: str = ""
    error_message: str = ""
    # The goal's completion has been metered (UsageService) — once per goal.
    usage_recorded: bool = False


# ── helpers ───────────────────────────────────────────────────────────────────

try:
    from langgraph.checkpoint.memory import MemorySaver
except ImportError:
    MemorySaver = object  # type: ignore[misc,assignment]


def _resolve_checkpointer(app_state: Any) -> Any:
    """Return the best available checkpointer. Logs a WARNING if falling back to MemorySaver.

    Priority:
      1. app_state.langgraph_checkpointer (only if it's a real BaseCheckpointSaver)
      2. AsyncRedisSaver built from REDIS_URL / settings.redis_url
      3. Sync RedisSaver (same URL)
      4. MemorySaver with a WARNING about durability loss
    """
    import os

    cp = getattr(app_state, "langgraph_checkpointer", None)
    if cp is not None and not isinstance(cp, MemorySaver):
        try:
            from langgraph.checkpoint.base import BaseCheckpointSaver

            # Only accept a pre-wired saver that implements the ASYNC checkpoint
            # API — the agent graph runs via ``ainvoke``. A sync-only saver whose
            # ``aget_tuple`` is the base ``NotImplementedError`` would crash every
            # goal, so fall through to the async-capable resolution below.
            # Introspection defaults to "accept" when it cannot be evaluated
            # (e.g. a MagicMock(spec=...) whose class has no real ``aget_tuple``),
            # preserving the direct-return behaviour for test-injected savers.
            if isinstance(cp, BaseCheckpointSaver):
                try:
                    async_impl_ok = type(cp).aget_tuple is not BaseCheckpointSaver.aget_tuple
                except Exception:
                    async_impl_ok = True
                if async_impl_ok:
                    return cp
        except Exception:
            pass

    # Try to build from env REDIS_URL.
    # Use isinstance(str) guard: prevents MagicMock attrs on test app_state objects.
    redis_url: str = os.getenv("REDIS_URL", "")
    if not redis_url:
        _settings = getattr(app_state, "settings", None) if app_state is not None else None
        if _settings is not None:
            _url_attr = getattr(_settings, "redis_url", None)
            if isinstance(_url_attr, str) and _url_attr:
                redis_url = _url_attr

    # When Redis Sentinel is configured but no explicit REDIS_URL is set,
    # derive a sentinel:// connection string so RedisSaver can still connect.
    # The RedisSaver libraries accept "sentinel://host:port/db" as a shorthand;
    # for multi-node Sentinel pass the first node only (they all proxy the same
    # master) — the sentinel client discovers the rest automatically.
    if not redis_url:
        _sentinel_urls = os.getenv("REDIS_SENTINEL_URLS", "")
        if _sentinel_urls:
            _first_node = _sentinel_urls.split(",")[0].strip()
            _sentinel_db = os.getenv("REDIS_SENTINEL_DB", "0")
            redis_url = f"sentinel://{_first_node}/{_sentinel_db}"
            _svc_logger.info("checkpointer_using_sentinel_url sentinel_node=%s", _first_node)

    if redis_url:
        # AsyncRedisSaver (preferred).
        # NOTE: from_conn_string() may return an async context manager in some
        # langgraph-checkpoint-redis versions; validate the return value is a real
        # BaseCheckpointSaver before using it, otherwise fall through to sync saver.
        try:
            from langgraph.checkpoint.base import BaseCheckpointSaver
            from langgraph.checkpoint.redis.aio import AsyncRedisSaver

            _saver = AsyncRedisSaver.from_conn_string(redis_url)
            if not isinstance(_saver, BaseCheckpointSaver):
                raise TypeError(
                    f"AsyncRedisSaver.from_conn_string returned {type(_saver).__name__}, "
                    "not a BaseCheckpointSaver — needs async with pattern"
                )
            try:
                _loop = asyncio.get_running_loop()
                _loop.create_task(_saver.setup())  # noqa: RUF006  # fire-and-forget by design: intentionally not awaited/cancelled
            except RuntimeError:
                pass
            _svc_logger.info("checkpointer_redis_async_wired")
            return _saver
        except Exception:
            pass
        # Sync RedisSaver (fallback) — ONLY if it implements the async API.
        # The agent graph always runs via ``ainvoke``, which calls
        # ``aget_tuple``/``aput``. A sync-only ``RedisSaver`` leaves those as the
        # base ``NotImplementedError``, so returning it makes every goal crash
        # with ``NotImplementedError`` the moment the graph starts — a
        # production-only failure (Redis present, langgraph-checkpoint-redis's
        # async saver absent) invisible to MemorySaver-based tests. Reject such a
        # saver and fall through to MemorySaver instead of shipping a broken one.
        try:
            from langgraph.checkpoint.base import BaseCheckpointSaver
            from langgraph.checkpoint.redis import RedisSaver

            _saver2 = RedisSaver.from_conn_string(redis_url)
            if not isinstance(_saver2, BaseCheckpointSaver):
                raise TypeError(
                    f"RedisSaver.from_conn_string returned {type(_saver2).__name__}, "
                    "not a BaseCheckpointSaver"
                )
            if type(_saver2).aget_tuple is BaseCheckpointSaver.aget_tuple:
                raise TypeError(
                    "sync RedisSaver does not implement the async checkpoint API "
                    "(aget_tuple); it is unusable by the async agent graph"
                )
            _svc_logger.info("checkpointer_redis_sync_wired")
            return _saver2
        except Exception as _e2:
            _msg = (
                f"redis_saver_unavailable redis_url={redis_url[:30]} "
                f"error={_e2!s} "
                "impact=GOAL STATE WILL BE LOST ON PROCESS RESTART"
            )
            _svc_logger.warning(
                "redis_saver_unavailable_falling_back_to_memory",
                redis_url=redis_url[:30],
                error=str(_e2),
                impact="GOAL STATE WILL BE LOST ON PROCESS RESTART — install langgraph-checkpoint-redis",  # noqa: E501
            )
            _svc_logger.warning(_msg)
    else:
        _msg2 = (
            "no_redis_url_using_memory_saver "
            "impact=GOAL STATE WILL BE LOST ON PROCESS RESTART "
            "set REDIS_URL environment variable"
        )
        _svc_logger.warning(
            "no_redis_url_using_memory_saver",
            impact="GOAL STATE WILL BE LOST ON PROCESS RESTART — set REDIS_URL environment variable",  # noqa: E501
        )
        _svc_logger.warning(_msg2)
    return MemorySaver()


def _make_agent_loop() -> Any:
    """Construct an AgentGraph backed by FakeProvider (no real LLM required).

    WARNING: In production mode (ENVIRONMENT=production), this raises a
    ``RuntimeError`` instead of silently using FakeProvider for real goals.
    Configure ANTHROPIC_API_KEY, OPENAI_API_KEY, or GOOGLE_API_KEY to avoid this.
    """
    from app.core.config import get_settings as _get_cfg

    _cfg = _get_cfg()
    if getattr(_cfg, "environment", "development") == "production":
        raise RuntimeError(
            "Cannot use FakeProvider in production mode. "
            "Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or GOOGLE_API_KEY "
            "to configure a real LLM provider."
        )

    from app.agent.graph import AgentGraph
    from app.intelligence.guardrails import GuardrailChecker
    from app.reliability.dedup import DeduplicationCache as _DedupCache
    from app.reliability.result_processor import ResultProcessor
    from app.reliability.rollback import RollbackEngine

    planner = FakeProvider(responses=['{"steps": ["Complete the requested task"]}'])
    executor = FakeProvider(responses=["Task executed successfully"])
    verifier = FakeProvider(responses=['{"success": true, "reason": "Goal achieved"}'])
    try:
        return AgentGraph(
            planner=planner,
            executor=executor,
            verifier=verifier,
            result_processor=ResultProcessor(),
            dedup_cache=_DedupCache(),
            rollback_engine=RollbackEngine(),
            guardrail_checker=GuardrailChecker(),
            cost_tracker=None,
        )
    except Exception as exc:
        _svc_logger.error(
            "agentgraph_construction_failed",
            error=str(exc),
            exc_info=True,
        )
        raise RuntimeError(
            f"Failed to construct AgentGraph: {exc}. "
            "Check provider configuration (ANTHROPIC_API_KEY or OPENAI_API_KEY)."
        ) from exc


def _fake_provider() -> Any:
    """Return a cycling FakeProvider suitable for use as planner, executor, and verifier."""
    return FakeProvider(
        responses=[
            '{"steps": ["Complete the requested task"]}',
            "Task executed successfully",
            '{"success": true, "reason": "Goal achieved"}',
        ]
    )


def _failed_goal_state(
    goal_id: str, goal_text: str, tenant_ctx: TenantContext, reason: str
) -> AgentState:
    """A FAILED AgentState for a run that ended without returning one (timeout,
    crash, exhausted persistence) — so the outcome is still learned from."""
    state = AgentState(goal=goal_text, tenant_ctx=tenant_ctx)
    state.goal_id = goal_id
    state.status = GoalStatus.FAILED
    state.error_message = reason[:1000]
    return state


def _populate_guardrail_allowlist(
    checker: Any,
    tool_context: Any,
) -> None:
    """Populate the GuardrailChecker's known-tools allowlist from discovered tools.

    SAFE-2 fix: Without this, the checker is constructed with an empty known_tools
    set, which disables the registry check entirely — any tool name (including
    hallucinated ones) passes validation.

    Args:
        checker: A GuardrailChecker instance (or any object with register_tools).
        tool_context: A ToolContext whose .tools list holds ToolRef objects, or None.
    """
    if tool_context is None:
        return
    tools = getattr(tool_context, "tools", None)
    if not tools:
        return
    tool_names: set[str] = set()
    for tool_ref in tools:
        name = getattr(tool_ref, "name", "")
        if name:
            tool_names.add(str(name))
    if tool_names:
        checker.register_tools(tool_names)


def _build_dedup_cache(redis: Any) -> Any:
    """Build the executor's tool-call dedup cache.

    This cache backs the executor's *content-hash* idempotency check
    (``executor_mixin.py`` calls ``is_duplicate``/``mark_seen`` **synchronously**
    per step). That is inherently a per-goal-run, in-process concern — a single
    goal executes on one worker — so the in-memory ``DeduplicationCache`` is the
    correct backing here.

    It must NOT be a ``RedisDeduplicationCache``: that class implements a
    completely different, *async* goal-submission dedup API
    (``get_existing``/``register``) and has no ``is_duplicate``/``mark_seen``.
    Wiring it into this slot made every goal crash on its first step whenever
    Redis was available (``'RedisDeduplicationCache' object has no attribute
    'is_duplicate'``) — a production-only failure invisible to the in-memory
    unit tests. Cross-replica *goal-submission* dedup is handled separately by
    ``app.services.dedup._default_deduplicator`` in ``submit_goal``.

    ``redis`` is accepted for call-site compatibility but intentionally unused.
    """
    from app.reliability.dedup import DeduplicationCache as _DedupCache

    return _DedupCache()


# Ordered least- to most-autonomous. A compliance ceiling clamps downwards only.
_AUTONOMY_ORDER = ("supervised", "bounded-autonomous", "fully-autonomous")


def clamp_autonomy_mode(requested: str, ceiling: str) -> str:
    """Return the more restrictive of ``requested`` and ``ceiling``."""
    mode = requested if requested in _AUTONOMY_ORDER else "bounded-autonomous"
    if ceiling not in _AUTONOMY_ORDER:
        return mode
    return min(mode, ceiling, key=_AUTONOMY_ORDER.index)


async def compliance_autonomy_ceiling(app_state: Any, *, tenant_id: str) -> str:
    """The most restrictive autonomy mode the tenant's compliance bundles allow.

    ``ComplianceBundle.max_autonomy_mode`` (HIPAA and SOX cap at ``supervised``,
    GDPR/PCI at ``bounded-autonomous``) was computed by
    ``GET /trust-governance/compliance-bundles/active`` and read by nothing else
    in the codebase — enabling HIPAA told the operator the ceiling was
    ``supervised`` while every agent kept running at whatever its own config
    said.

    Never raises, and fails CLOSED: a lookup error, or an app that has no
    compliance store wired, reports ``supervised`` (the strictest bundle
    ceiling). A missing store used to report ``fully-autonomous``, silently
    dropping e.g. HIPAA's supervised limit. Only a bare service with no app at
    all (``app_state is None``: unit tests / scripts, where no tenant can have
    enabled a bundle) reports no ceiling.
    """
    if app_state is None:
        return "fully-autonomous"
    # ``app_state`` is the FastAPI *app* on GoalService (see self._app_state) but
    # ``app.state`` at most other call sites; normalise like the cost-controller
    # lookup above rather than silently reading an attribute the app object does
    # not have — which would report "no ceiling" for every tenant.
    resolved: Any = app_state
    try:
        from starlette.applications import Starlette as _Starlette

        if isinstance(app_state, _Starlette):
            resolved = app_state.state
    except Exception:  # pragma: no cover - starlette always importable here
        pass

    store = getattr(resolved, "compliance_bundle_store", None)
    if store is None:
        _svc_logger.warning("compliance_bundle_store_missing_fail_closed", tenant_id=tenant_id)
        return "supervised"
    try:
        from app.governance.compliance_bundles import effective_max_autonomy_for

        ceiling = await effective_max_autonomy_for(store, tenant_id)
    except Exception as exc:
        # Fail closed: a compliance lookup error used to lift the ceiling to
        # fully-autonomous, silently dropping e.g. HIPAA's supervised limit.
        _svc_logger.warning("compliance_ceiling_lookup_failed", error=str(exc))
        return "supervised"
    return ceiling if ceiling in _AUTONOMY_ORDER else "supervised"


async def resolve_effective_autonomy_mode(
    app_state: Any, *, tenant_id: str, requested: str
) -> str:
    """``requested``, clamped by the tenant's compliance ceiling. Never widens."""
    return clamp_autonomy_mode(
        requested, await compliance_autonomy_ceiling(app_state, tenant_id=tenant_id)
    )


# ── service ───────────────────────────────────────────────────────────────────



# execution_context keys (set by POST /goals before the goal runs) that the agent
# graph reads from its state context. Only an allow-list is forwarded: the rest of
# execution_context (runtime profile, trigger metadata, ...) is not graph state.
GRAPH_CONTEXT_KEYS: tuple[str, ...] = (
    "debate_consensus",
    "debate_confidence",
    "debate_winning_agent",
    "debate_error",
    "supervisor_applied",
    "supervisor_fallback",
)


# Agent-config reasoning-pattern flags the graph is compiled with. Snapshotted on
# the goal at submission (execution_context["agent_pattern_flags"]) so the Celery
# worker — which has no in-memory agent store — builds the same graph.
AGENT_PATTERN_FLAG_KEYS: tuple[str, ...] = (
    "enable_cot",
    "enable_reflection",
    "enable_goal_tree",
    "enable_self_refine",
    "enable_self_consistency",
    "enable_tree_of_thoughts",
    "enable_peer_review",
    "enable_supervisor",
    "enable_debate",
)


def graph_context_from_execution_context(execution_context: Any) -> dict[str, Any]:
    """The allow-listed execution_context entries that belong in the graph's context."""
    if not isinstance(execution_context, dict):
        return {}
    return {k: execution_context[k] for k in GRAPH_CONTEXT_KEYS if k in execution_context}

class _BoundedLRU(collections.OrderedDict[str, Any]):
    """A dict with a fixed capacity that evicts the least recently used entry.

    The per-replica eval-scorecard cache used to be a plain dict that grew by
    one scorecard per scored goal for the life of the process. Postgres
    (``evaluations`` / ``eval_scorecards``) is the record; this is only a cache.
    """

    def __init__(self, maxsize: int) -> None:
        super().__init__()
        self.maxsize = max(1, maxsize)

    def __setitem__(self, key: str, value: Any) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > self.maxsize:
            self.popitem(last=False)

    def get(self, key: str, default: Any = None) -> Any:
        if key in self:
            self.move_to_end(key)
            return super().__getitem__(key)
        return default


def _eval_cache_size() -> int:
    try:
        return int(os.getenv("AGENTVERSE_EVAL_CACHE_MAX", "2048"))
    except ValueError:
        return 2048


class GoalService:
    """In-memory goal service.

    Wire as ``app.state.goal_service`` in the application factory.
    Optionally inject ``audit_log`` and ``hitl`` for governance integration.
    Pass ``app_state`` (the FastAPI app) to enable per-tenant LLM provider dispatch.
    Pass ``db_session_factory`` to enable background PostgreSQL persistence.
    """

    def __init__(
        self,
        *,
        audit_log: AuditLog | None = None,
        hitl: HITLGateway | None = None,
        app_state: Any = None,
        db_session_factory: Any = None,
        event_store: Any = None,
        task_queue: GoalTaskQueue | None = None,
    ) -> None:
        self._goals: dict[str, GoalRecord] = {}
        self._audit_log: AuditLog = audit_log or AuditLog()
        self._hitl: HITLGateway = hitl or HITLGateway()
        self._app_state: Any = app_state  # FastAPI app; set by create_app after construction
        self._db: Any = db_session_factory  # async_sessionmaker or None
        self._event_store: Any = event_store
        self._task_queue = task_queue
        self._db_tasks: set[asyncio.Future[None]] = set()
        # Background tasks set (used for Celery SSE bridge, etc.)
        self._background_tasks: set[Any] = set()
        # Agent store reference — set externally after construction (H-4)
        self._agent_store: Any = None
        # Logger for use in methods
        self._logger = _svc_logger
        # Redis client for pub/sub (set by create_app lifespan when manage_pools=True)
        self._redis: Any = None
        # Redis URL for creating dedicated pub/sub connections (set by create_app lifespan).
        # Required for the cross-replica SSE path — a separate connection per SSE consumer
        # is needed because redis-py pub/sub blocks the connection while listening.
        self._redis_url_for_pubsub: str = ""
        # Eval scorecards keyed by goal_id; populated on goal completion.
        self._eval_scores: _BoundedLRU = _BoundedLRU(_eval_cache_size())
        # Goals whose completion-time eval is still running: GET /eval reports
        # "pending" for them instead of a misleading "not_evaluated" (the goal is
        # marked complete before its charged scoring calls finish).
        self._eval_pending: set[str] = set()
        # Per-tenant list of completed goal durations (seconds) for latency metrics.
        self._goal_durations: dict[str, list[float]] = {}
        # Time-based eviction: track last eviction timestamp.
        self._last_eviction_time: float = time.monotonic()
        # Identity of this process as an in-process goal runner. Recorded on
        # every goal it runs (execution_context.runner) and kept alive in Redis
        # by a heartbeat, so restart recovery on another replica can tell a
        # goal whose runner died from one that is still running.
        self._replica_id: str = uuid.uuid4().hex
        self._heartbeat_task: asyncio.Task[None] | None = None

    # ── Runner ownership + liveness (restart recovery) ───────────────────────

    def _runner_marker(self) -> dict[str, Any]:
        """``execution_context.runner`` for a goal this service launches now."""
        if self._task_queue is not None:
            return {"kind": _RUNNER_WORKER}
        return {"kind": _RUNNER_IN_PROCESS, "replica": self._replica_id}

    async def _touch_replica_heartbeat(self) -> None:
        redis = getattr(self, "_redis", None)
        if redis is None:
            return
        await redis.set(
            _REPLICA_ALIVE_KEY.format(replica=self._replica_id),
            "1",
            ex=_REPLICA_HEARTBEAT_TTL_SECONDS,
        )

    async def _ensure_replica_heartbeat(self) -> None:
        """Mark this replica alive now and keep refreshing it while it runs goals."""
        if getattr(self, "_redis", None) is None:
            return
        try:
            await self._touch_replica_heartbeat()
        except Exception as exc:
            _svc_logger.warning("replica_heartbeat_failed", error=str(exc)[:120])
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(
                self._replica_heartbeat_loop(), name=f"goal-replica-heartbeat-{self._replica_id}"
            )

    async def _replica_heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(_REPLICA_HEARTBEAT_INTERVAL_SECONDS)
            try:
                await self._touch_replica_heartbeat()
            except Exception as exc:
                _svc_logger.warning("replica_heartbeat_failed", error=str(exc)[:120])

    def stop_replica_heartbeat(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

    async def _replica_alive(self, replica_id: str) -> bool | None:
        """True/False from the replica's Redis heartbeat; None when it can't be known."""
        redis = getattr(self, "_redis", None)
        if redis is None:
            return None
        try:
            return bool(await redis.exists(_REPLICA_ALIVE_KEY.format(replica=replica_id)))
        except Exception as exc:
            _svc_logger.warning("replica_liveness_check_failed", error=str(exc)[:120])
            return None

    # ── P1.3: HITL rejection subscriber ──────────────────────────────────────

    def start_hitl_rejection_subscriber(self, redis_url: str) -> None:
        """Start the HITL rejection note subscriber as a background asyncio task."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(  # noqa: RUF006  # fire-and-forget by design: intentionally not awaited/cancelled
                self._subscribe_hitl_rejections(redis_url),
                name="hitl-rejection-subscriber",
            )
            _svc_logger.info("hitl_rejection_subscriber_scheduled", redis_url=redis_url[:30])
        except RuntimeError:
            _svc_logger.warning("hitl_rejection_subscriber_no_loop")

    async def _subscribe_hitl_rejections(self, redis_url: str) -> None:
        """Subscribe to HITL rejection notifications and store note for next plan cycle."""
        import json

        import redis.asyncio as aioredis

        while True:
            try:
                async with aioredis.from_url(redis_url, decode_responses=True) as r:
                    pubsub = r.pubsub()
                    await pubsub.psubscribe("hitl_rejected:*")
                    _svc_logger.info("hitl_rejection_subscriber_started")
                    async for msg in pubsub.listen():
                        if msg.get("type") != "pmessage":
                            continue
                        try:
                            data = json.loads(msg["data"])
                            goal_id = data.get("goal_id", "")
                            note = data.get("note", "")
                            if goal_id and goal_id in self._goals:
                                record = self._goals[goal_id]
                                record.hitl_rejection_note = note
                                record.events.append(
                                    {
                                        "type": "hitl_rejected",
                                        "note": note,
                                        "ts": __import__("datetime")
                                        .datetime.now(__import__("datetime").timezone.utc)
                                        .isoformat(),
                                    }
                                )
                                _svc_logger.info("hitl_rejection_note_stored", goal_id=goal_id)
                        except Exception as exc:
                            _svc_logger.warning(
                                "hitl_rejection_message_parse_failed", error=str(exc)
                            )
            except Exception as exc:
                _svc_logger.warning("hitl_rejection_subscriber_error", error=str(exc))
                await asyncio.sleep(5)

    def _track_db_task(self, coro: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.create_task(coro)
        self._db_tasks.add(task)
        task.add_done_callback(self._db_tasks.discard)

    # ── C-1: Celery → SSE event bridge ────────────────────────────────────────

    async def _subscribe_celery_goal_events(self, redis_url: str) -> None:
        """Bridge Celery worker goal events to in-process SSE queues.

        Celery workers publish events to Redis channels:
          goal_events:{tenant_id}:{goal_id}  -> JSON event dict

        This subscriber feeds those events into the in-process GoalRecord.subscribers
        queues so that SSE streams work correctly even when goals run on workers.
        """
        import json

        import redis.asyncio as aioredis

        while True:
            try:
                async with aioredis.from_url(redis_url, decode_responses=True) as r:
                    pubsub = r.pubsub()
                    await pubsub.psubscribe("goal_events:*")
                    self._logger.info("celery_event_bridge_subscribed")

                    async for message in pubsub.listen():
                        if message.get("type") not in ("pmessage", "message"):
                            continue
                        try:
                            data = json.loads(message.get("data", "{}"))
                            goal_id = data.get("goal_id", "")
                            tenant_id = data.get("tenant_id", "")
                            event_type = data.get("type", "")
                            payload = data.get("payload", {})

                            if goal_id:
                                record = self._goals.get(goal_id)
                                if record is None:
                                    # Create stub record for goals executed by Celery workers
                                    # that haven't been synced to this API process yet
                                    try:
                                        record = GoalRecord(
                                            goal_id=goal_id,
                                            goal_text="",  # Will be filled when DB syncs
                                            status=GoalStatus.EXECUTING,
                                            tenant_id=tenant_id,
                                            priority="normal",
                                            dry_run=False,
                                            created_at="",
                                        )
                                        self._goals[goal_id] = record
                                        self._logger.debug(
                                            "created_stub_goal_record_for_bridge",
                                            goal_id=goal_id,
                                        )
                                    except Exception as exc:
                                        self._logger.warning(
                                            "bridge_stub_creation_failed", error=str(exc)
                                        )

                                if record is not None:
                                    # Feed into SSE subscriber queues
                                    event = {
                                        "type": event_type,
                                        "payload": payload,
                                        "goal_id": goal_id,
                                        "tenant_id": tenant_id,
                                    }
                                    dead = []
                                    for q in list(record.subscribers):
                                        try:
                                            q.put_nowait(event)
                                        except asyncio.QueueFull:
                                            if event_type not in {"token_chunk", "heartbeat"}:
                                                dead.append(q)
                                        except Exception:
                                            dead.append(q)
                                    for q in dead:
                                        with suppress(Exception):
                                            record.subscribers.remove(q)
                                    # Send end-of-stream sentinel on terminal events.
                                    # worker_complete is terminal only for the status it
                                    # carries (waiting_human is a suspension, not an end).
                                    _terminal_bridge = {
                                        "goal_complete": GoalStatus.COMPLETE,
                                        "goal_failed": GoalStatus.FAILED,
                                        "worker_failed": GoalStatus.FAILED,
                                        "goal_cancelled": GoalStatus.CANCELLED,
                                    }
                                    _final: GoalStatus | None = (
                                        self._worker_complete_status(
                                            payload if isinstance(payload, dict) else {}
                                        )
                                        if event_type == "worker_complete"
                                        else _terminal_bridge.get(event_type)
                                    )
                                    if _final is not None:
                                        for q in list(record.subscribers):
                                            with suppress(Exception):
                                                q.put_nowait(_SENTINEL)
                                        record.status = _final
                        except Exception as exc:
                            self._logger.warning("celery_event_bridge_parse_failed", error=str(exc))
            except Exception as exc:
                self._logger.warning("celery_event_bridge_error", error=str(exc))
                await asyncio.sleep(5)

    def start_celery_event_bridge(self, redis_url: str) -> None:
        """Start the Celery→SSE event bridge as a background asyncio task."""
        task = asyncio.create_task(
            self._subscribe_celery_goal_events(redis_url),
            name="celery_goal_event_bridge",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    # ── memory management ─────────────────────────────────────────────────────
    def _evict_stale_goals(self) -> int:
        """Remove terminal goals older than TTL from the in-memory cache.

        These are already persisted in DB. Evicting from memory is safe
        and prevents unbounded growth in long-running processes.
        """
        from datetime import UTC, datetime, timedelta

        cutoff = datetime.now(UTC) - timedelta(seconds=_COMPLETED_GOAL_TTL_SECONDS)
        terminal = (GoalStatus.COMPLETE, GoalStatus.FAILED, GoalStatus.CANCELLED)
        to_evict: list[str] = []
        for goal_id, record in self._goals.items():
            if record.status in terminal:
                completed_at = getattr(record, "completed_at", None)
                if completed_at and isinstance(completed_at, str):
                    try:
                        dt = datetime.fromisoformat(completed_at.rstrip("Z"))
                        if dt.replace(tzinfo=UTC) < cutoff:
                            to_evict.append(goal_id)
                    except Exception:
                        pass
                # else: no timestamp yet — skip (don't evict; goal might have just completed)
        for gid in to_evict:
            del self._goals[gid]
            _GOAL_PAUSE_EVENTS.pop(gid, None)
        self._sweep_pause_events()
        return len(to_evict)

    async def _evict_async(self) -> int:
        """Async wrapper so eviction runs in the event loop without thread race."""
        return self._evict_stale_goals()

    def _sweep_pause_events(self) -> int:
        """Remove pause events for goals that are no longer tracked."""
        stale = [gid for gid in list(_GOAL_PAUSE_EVENTS.keys()) if gid not in self._goals]
        for gid in stale:
            _GOAL_PAUSE_EVENTS.pop(gid, None)
        return len(stale)

    async def _check_budget_preflight(self, tenant_ctx: TenantContext) -> None:
        """Reject goal submission when the tenant's daily cost budget is exhausted.

        Fail-closed: when a configured cost controller cannot be consulted the
        goal is rejected with a retryable ExternalServiceError (it used to be
        accepted unmetered). Prefers the Redis controller (cross-replica) and
        falls back to the in-memory one. Raises PlanLimitExceededError (HTTP 429)
        with a budget reason when there is no remaining daily budget.
        """
        from app.tenancy.limits import PlanLimitExceededError

        _aps: Any = self._app_state
        try:
            from starlette.applications import Starlette as _Starlette

            if isinstance(self._app_state, _Starlette):
                _aps = self._app_state.state
        except Exception:
            pass
        redis_cc = getattr(_aps, "redis_cost_controller", None) if _aps else None
        mem_cc = getattr(_aps, "cost_controller", None) if _aps else None

        has_budget = True
        try:
            if redis_cc is not None and hasattr(redis_cc, "get_budget_status"):
                status = await redis_cc.get_budget_status(tenant_ctx=tenant_ctx)
                has_budget = float(status.get("daily_remaining", 1.0)) > 0.0
            elif mem_cc is not None and hasattr(mem_cc, "ahas_remaining_budget"):
                # Async: resolves the tenant's configured budget_configs row.
                has_budget = await mem_cc.ahas_remaining_budget(tenant_ctx=tenant_ctx)
            elif mem_cc is not None and hasattr(mem_cc, "has_remaining_budget"):
                has_budget = mem_cc.has_remaining_budget(tenant_ctx=tenant_ctx)
        except PlanLimitExceededError:
            raise
        except Exception as exc:
            from app.core.errors import ExternalServiceError

            _svc_logger.warning("budget_preflight_fail_closed", error=str(exc)[:200])
            raise ExternalServiceError(
                "Cost budget could not be verified — goal rejected (fail-closed). "
                "Retry once the budget service is reachable.",
                cause=exc,
            ) from exc

        if not has_budget:
            raise PlanLimitExceededError(
                "Daily cost budget exhausted for this tenant — goal rejected. "
                "Increase the budget or wait for the daily reset."
            )

    async def _check_daily_goal_limit_redis(self, tenant_ctx: TenantContext) -> None:
        """Atomic Redis-based daily goal counter — works across all replicas.

        Falls back to in-memory count when Redis is unavailable.
        Raises PlanLimitExceededError (HTTP 429) when the limit is reached.
        """
        from app.tenancy.limits import check_daily_goal_limit

        redis = getattr(self, "_redis", None)
        if redis is None:
            # Fall back to in-memory count (single-process mode)
            today_prefix = datetime.now(UTC).strftime("%Y-%m-%d")
            daily_count = sum(
                1
                for r in self._goals.values()
                if r.tenant_id == tenant_ctx.tenant_id and r.created_at.startswith(today_prefix)
            )
            check_daily_goal_limit(tenant_ctx, daily_count)
            return

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        key = f"daily_goals:{tenant_ctx.tenant_id}:{today}"

        try:
            # INCR first (atomic), then validate — eliminates GET→check→INCR TOCTOU race.
            # If the limit is exceeded we roll back with DECR before raising.
            new_count = int(await redis.incr(key))
            now = datetime.now(UTC)
            end_of_day = datetime(now.year, now.month, now.day, 23, 59, 59, tzinfo=UTC)
            ttl = int((end_of_day - now).total_seconds()) + 3600
            await redis.expire(key, ttl)
            try:
                check_daily_goal_limit(tenant_ctx, new_count)
            except Exception:
                # Rollback the pre-emptive increment — goal is rejected.
                with suppress(Exception):
                    await redis.decr(key)
                raise
        except Exception:
            raise

    async def recover_interrupted_goals(self) -> int:
        """Startup recovery (call once Redis is wired — see the lifespan)."""
        return await self._recover_interrupted_goals()

    async def _recover_interrupted_goals(self) -> int:
        """Re-run goals whose IN-PROCESS runner is provably gone. Returns # re-enqueued.

        It used to re-enqueue every unfinished goal without a local task on
        every replica start — including goals running on Celery workers
        (duplicate execution), always on the free queue, and once per replica
        when several started together. Now a goal is recovered only when:

        * it was run in-process (``execution_context.runner.kind == in_process``)
          — worker goals belong to Celery (acks-late redelivery and the
          stuck-goal sweeper); goals with no recorded runner are not provably
          orphaned and are left to the sweeper as well;
        * its replica's Redis heartbeat is gone (unknown ⇒ not recovered) and no
          worker holds its execution lock;
        * this replica wins an atomic claim on the row (UPDATE … WHERE runner is
          still the dead replica … RETURNING), so replicas starting at once
          recover it exactly once;
        * the tenant's plan is known — it is re-enqueued on that plan's queue
          (never defaulted to free).

        Without a task queue the claimed goal is failed durably (resubmit).
        """
        redis = getattr(self, "_redis", None)
        if redis is None:
            _svc_logger.warning("goal_recovery_skipped_no_redis")
            return 0
        recovered = 0
        for goal_id, record in list(self._goals.items()):
            if record.status in _NOT_RECOVERABLE_STATUSES or self._runs_locally(record):
                continue
            runner = record.execution_context.get(_RUNNER_KEY)
            if not isinstance(runner, dict) or runner.get("kind") != _RUNNER_IN_PROCESS:
                continue
            owner = str(runner.get("replica") or "")
            if not owner or owner == self._replica_id:
                continue
            try:
                if await redis.exists(_WORKER_GOAL_LOCK_KEY.format(goal_id=goal_id)):
                    continue  # a worker is executing it
            except Exception as exc:
                _svc_logger.warning("goal_recovery_lock_check_failed", error=str(exc)[:120])
                continue
            if await self._replica_alive(owner) is not False:
                continue  # alive, or liveness unknown: not provably orphaned
            try:
                if await self._recover_one(record, owner):
                    recovered += 1
            except Exception as exc:
                _svc_logger.warning("goal_recovery_failed", goal_id=goal_id, error=str(exc))
        return recovered

    async def _recover_one(self, record: GoalRecord, dead_replica: str) -> bool:
        """Claim and re-dispatch one orphaned goal; True when it was re-enqueued."""
        goal_id, tenant_id = record.goal_id, record.tenant_id
        recovered_runner: dict[str, Any] = {"recovered_from": dead_replica}
        if self._task_queue is None:
            claimed = await self._db_claim_goal_runner(
                goal_id,
                tenant_id,
                expected_replica=dead_replica,
                runner={"kind": _RUNNER_IN_PROCESS, "replica": self._replica_id,
                        **recovered_runner},
            )
            if not claimed:
                return False
            reason = "Goal interrupted by process restart. Please resubmit."
            record.error_message = reason
            await self._db_update_goal_status(
                goal_id, tenant_id, GoalStatus.FAILED.value, error_message=reason
            )
            # Terminal event: releases the dead runner's concurrency slot and
            # dedup key, and tells any SSE subscriber.
            await self._dispatch_event(
                goal_id,
                {"type": "goal_failed", "reason": reason, "failure_reason": "runner_lost"},
                tenant_ctx=TenantContext(
                    tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="goal-recovery"
                ),
            )
            record.status = GoalStatus.FAILED
            return False
        plan = await self._tenant_plan(tenant_id)
        if not plan:
            _svc_logger.error("goal_recovery_plan_unknown", goal_id=goal_id, tenant_id=tenant_id)
            return False
        worker_runner = {"kind": _RUNNER_WORKER, **recovered_runner}
        if not await self._db_claim_goal_runner(
            goal_id, tenant_id, expected_replica=dead_replica, runner=worker_runner
        ):
            return False  # another replica recovered it
        record.execution_context[_RUNNER_KEY] = worker_runner
        try:
            self._task_queue.enqueue_goal(
                goal_id=goal_id,
                goal_text=record.goal_text,
                tenant_id=tenant_id,
                priority=getattr(record, "priority", "normal"),
                dry_run=getattr(record, "dry_run", False),
                agent_id=record.agent_id,
                connector_ids=[],
                workflow_mode=record.workflow_mode,
                goal_template="",
                plan=plan,
            )
        except Exception:
            # Hand the claim back so a later start can retry, then surface it.
            with suppress(Exception):
                await self._db_set_runner(
                    goal_id,
                    tenant_id,
                    {"kind": _RUNNER_IN_PROCESS, "replica": dead_replica},
                )
            record.execution_context[_RUNNER_KEY] = {
                "kind": _RUNNER_IN_PROCESS,
                "replica": dead_replica,
            }
            raise
        record.status = GoalStatus.PLANNING
        _svc_logger.info("goal_recovered", goal_id=goal_id, plan=plan, dead_replica=dead_replica)
        return True

    async def _db_claim_goal_runner(
        self,
        goal_id: str,
        tenant_id: str,
        *,
        expected_replica: str,
        runner: dict[str, Any],
    ) -> bool:
        """Atomically take over an orphaned goal; False when someone else already did.

        ``UPDATE … WHERE runner.replica = <dead replica> AND status is active
        RETURNING id`` under the tenant's RLS context: of several replicas
        starting at once exactly one gets the row back.
        """
        if self._db is None:
            return False
        from sqlalchemy import text as _sql

        from app.db.rls import sqlalchemy_rls_context

        try:
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                row = (
                    await session.execute(
                        _sql(
                            "UPDATE goals SET execution_context = ("
                            "COALESCE(execution_context::jsonb, '{}'::jsonb) "
                            "|| jsonb_build_object(CAST(:key AS text), CAST(:runner AS jsonb))"
                            ")::json "
                            "WHERE id = :g AND tenant_id = :t "
                            "AND status NOT IN ('complete', 'failed', 'cancelled', "
                            "'waiting_human') "
                            "AND execution_context::jsonb -> CAST(:key AS text) "
                            "->> 'replica' = :old "
                            "RETURNING id"
                        ),
                        {
                            "key": _RUNNER_KEY,
                            "runner": json.dumps(runner),
                            "g": goal_id,
                            "t": tenant_id,
                            "old": expected_replica,
                        },
                    )
                ).first()
            return row is not None
        except Exception as exc:
            _svc_logger.warning("goal_runner_claim_failed", goal_id=goal_id, error=str(exc))
            return False

    async def _db_set_runner(self, goal_id: str, tenant_id: str, runner: dict[str, Any]) -> None:
        """Record *runner* in goals.execution_context (atomic JSON merge)."""
        if self._db is None:
            return
        from sqlalchemy import text as _sql

        from app.db.rls import sqlalchemy_rls_context

        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            await session.execute(
                _sql(
                    "UPDATE goals SET execution_context = ("
                    "COALESCE(execution_context::jsonb, '{}'::jsonb) "
                    "|| jsonb_build_object(CAST(:key AS text), CAST(:runner AS jsonb))"
                    ")::json WHERE id = :g AND tenant_id = :t"
                ),
                {"key": _RUNNER_KEY, "runner": json.dumps(runner), "g": goal_id, "t": tenant_id},
            )

    async def _db_merge_context_key(
        self, goal_id: str, tenant_id: str, key: str, value: Any
    ) -> None:
        """Merge ``{key: value}`` into goals.execution_context under the tenant's RLS."""
        if self._db is None:
            return
        from sqlalchemy import text as _sql

        from app.db.rls import sqlalchemy_rls_context

        try:
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    _sql(
                        "UPDATE goals SET execution_context = ("
                        "COALESCE(execution_context::jsonb, '{}'::jsonb) "
                        "|| jsonb_build_object(CAST(:key AS text), CAST(:value AS jsonb))"
                        ")::json WHERE id = :g AND tenant_id = :t"
                    ),
                    {"key": key, "value": json.dumps(value), "g": goal_id, "t": tenant_id},
                )
        except Exception as exc:
            _svc_logger.warning(
                "goal_context_merge_failed", goal_id=goal_id, key=key, error=str(exc)
            )

    def _persist_strategy_execution(self, goal_id: str, tenant_id: str, record: Any) -> None:
        """Persist which strategy the goal's runtime actually runs (see strategy_execution)."""
        if self._db is None or record is None:
            return
        execution = record.execution_context.get("strategy_execution")
        if not isinstance(execution, dict):
            return
        coro = self._db_merge_context_key(goal_id, tenant_id, "strategy_execution", execution)
        try:
            self._track_db_task(coro)
        except RuntimeError:  # no running loop (sync caller) — nothing to schedule on
            coro.close()

    async def _tenant_plan(self, tenant_id: str) -> str | None:
        """The tenant's real plan tier (for the per-plan Celery queue), or None."""
        aps: Any = self._app_state
        with suppress(Exception):
            from starlette.applications import Starlette as _Starlette

            if isinstance(aps, _Starlette):
                aps = aps.state
        tenant_svc = getattr(aps, "tenant_service", None) if aps is not None else None
        if tenant_svc is not None:
            try:
                tenant = await tenant_svc.get_tenant(tenant_id)
                plan = tenant.get("plan") if isinstance(tenant, dict) else None
                if plan:
                    return str(getattr(plan, "value", plan))
            except Exception as exc:
                _svc_logger.warning("tenant_plan_lookup_failed", error=str(exc)[:120])
        if self._db is None:
            return None
        try:
            from sqlalchemy import text as _sql

            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                value = (
                    await session.execute(
                        _sql("SELECT plan_tier FROM tenants WHERE id = :t"),
                        {"t": tenant_id},
                    )
                ).scalar()
            return str(value) if value else None
        except Exception as exc:
            _svc_logger.warning("tenant_plan_db_lookup_failed", error=str(exc)[:120])
            return None

    async def _batch_event_counts(self, goal_ids: list[str], tenant_id: str) -> dict[str, int]:
        """Fetch event counts for multiple goals in one DB query.

        Replaces per-goal calls to _event_count_for_response() inside
        list_goals() to avoid N+1 query behaviour.
        """
        if not goal_ids or self._db is None:
            return {}
        try:
            from sqlalchemy import text

            async with self._db() as session:
                result = await session.execute(
                    text(
                        "SELECT goal_id, COUNT(*) as cnt FROM goal_events "
                        "WHERE tenant_id = :tid AND goal_id = ANY(:ids) "
                        "GROUP BY goal_id"
                    ),
                    {"tid": tenant_id, "ids": goal_ids},
                )
                return {row[0]: int(row[1]) for row in result.fetchall()}
        except Exception:
            return {}

    def _make_agent_loop_for_tenant(
        self,
        tenant_ctx: TenantContext,
        app_state: Any,
        *,
        agent_id: str | None = None,
        runtime_profile: Any | None = None,
        execution_context: dict[str, Any] | None = None,
        tenant_llm_config: dict[str, Any] | None = None,
    ) -> Any:
        """Build an AgentGraph using the tenant's configured LLM provider AND all
        governance/RAG/memory services from app.state.

        This is the production path — every goal runs with full pipeline.
        """
        from app.core.config import get_provider_env
        from app.intelligence.guardrails import GuardrailChecker
        from app.reliability.result_processor import ResultProcessor
        from app.reliability.rollback import RollbackEngine

        # ── Normalise app_state → the State object ───────────────────────────────
        # ``self._app_state`` is set to the FastAPI *app* (which carries ``.state``),
        # but every governance/RAG/memory service is registered on ``app.state``.
        # Only ``retrieval_gateway`` had a ``.state`` fallback below, so the live
        # agent graph was silently built with hitl_gateway / audit_log /
        # knowledge_store / cost_controller / policy_engine / eval_runner all None —
        # HITL gating, audit, budget, and policy enforcement disabled on the goal
        # loop. Unwrap once here so every lookup resolves against app.state. Guarded
        # to the real Starlette/FastAPI app so tests that pass app.state directly or
        # a mock object are unaffected.
        try:
            from starlette.applications import Starlette as _Starlette

            if isinstance(app_state, _Starlette):
                app_state = app_state.state
        except Exception:
            pass

        # ── LLM provider (real or fallback) ─────────────────────────────────────
        # 0. Explicit provider override on app.state. This is the single injection
        # seam used by the simulation sandbox, deterministic-replay harness, and
        # eval/e2e tiers to pin a specific LLMProvider for every goal without
        # touching per-tenant credentials. Highest precedence by design; the rest
        # of the resolution below is skipped because it is guarded on
        # ``provider is None``.
        provider: Any = getattr(app_state, "_llm_provider_override", None) if app_state else None

        # 1. The tenant's own (BYOK) config — resolved from the durable store by
        # the async caller (_resolve_tenant_llm_config). It used to be read only
        # from this replica's app.state, so it applied only on the replica that
        # had handled the tenant's PUT /tenants/me/llm.
        llm_configs: dict[str, Any] = getattr(app_state, "_llm_configs", {}) if app_state else {}
        if provider is not None:
            tenant_cfg = None
        elif tenant_llm_config is not None:
            tenant_cfg = tenant_llm_config
        else:
            tenant_cfg = llm_configs.get(tenant_ctx.tenant_id)
        if tenant_cfg:
            # One shared builder for the API and the worker. The inline copy here
            # sent groq/together keys to api.openai.com (base_url=None), ignored
            # gemini/nvidia/openrouter/openai_compatible configs and swallowed
            # decrypt failures — each silently running the goal on the PLATFORM
            # provider. It now returns the tenant's provider or raises
            # TenantProviderError, which fails the goal (no platform spend).
            from app.providers.tenant_provider import build_tenant_provider

            provider = build_tenant_provider(tenant_cfg, tenant_id=tenant_ctx.tenant_id)

        # 2. The app-wide provider resolved at startup from EVERY configured
        # backend (on-prem vLLM dispatcher, NVIDIA NIM, Ollama, OpenRouter,
        # Anthropic, OpenAI, …). This step was missing: resolution went straight
        # from tenant config to an Anthropic/OpenAI env check, so a deployment
        # configured with on-prem models or NVIDIA ran EVERY goal on the canned
        # FakeProvider — plan "Complete the requested task", answer "Task
        # executed successfully", verdict "Goal achieved" — and reported success.
        if provider is None and app_state is not None:
            from app.providers.fake import FakeProvider as _FakeProviderType

            _app_provider = getattr(app_state, "_app_provider", None)
            if _app_provider is not None and not isinstance(_app_provider, _FakeProviderType):
                provider = _app_provider

        # 3. Fall back to env-var provider
        if provider is None:
            anthropic_key = get_provider_env("ANTHROPIC_API_KEY")
            openai_key = get_provider_env("OPENAI_API_KEY")
            if anthropic_key:
                try:
                    from app.providers.anthropic_provider import AnthropicProvider

                    provider = AnthropicProvider(api_key=anthropic_key)
                except Exception:
                    pass
            elif openai_key:
                try:
                    from app.providers.openai_compatible import OpenAICompatibleProvider

                    provider = OpenAICompatibleProvider(api_key=openai_key)
                except Exception:
                    pass

        # 4. Final fallback: FakeProvider — development only, and always flagged.
        simulated = provider is None
        if simulated:
            import os as _os

            if _os.getenv("ENVIRONMENT", "development").lower() == "production":
                # Never fabricate a "successful" goal in production.
                raise RuntimeError(
                    "No LLM provider is configured; refusing to simulate goal execution "
                    "in production."
                )
            from app.providers.fake import FakeProvider as _FakeProvider

            provider = _FakeProvider(
                responses=[
                    '{"steps": ["Complete the requested task"]}',
                    "Task executed successfully",
                    '{"success": true, "reason": "Goal achieved"}',
                ]
            )
            _svc_logger.warning(
                "fake_provider_active",
                message=(
                    "No real LLM provider configured. Goal will use FakeProvider. "
                    "Set ANTHROPIC_API_KEY or OPENAI_API_KEY for real agent execution."
                ),
            )

        # ── Pull services from app.state ─────────────────────────────────────────
        audit_log = getattr(app_state, "audit_log", None) if app_state else self._audit_log
        # Prefer RedisCostController (cross-replica) over in-memory CostController
        cost_controller = (
            (
                getattr(app_state, "redis_cost_controller", None)
                or getattr(app_state, "cost_controller", None)
            )
            if app_state
            else None
        )
        hitl_gateway = getattr(app_state, "hitl_gateway", None) if app_state else self._hitl
        knowledge_store = getattr(app_state, "knowledge_store", None) if app_state else None
        retrieval_gateway = None
        if app_state is not None:
            retrieval_gateway = getattr(app_state, "retrieval_gateway", None)
            if retrieval_gateway is None:
                retrieval_gateway = getattr(
                    getattr(app_state, "state", None),
                    "retrieval_gateway",
                    None,
                )
        long_term_memory = getattr(app_state, "long_term_memory", None) if app_state else None
        eval_runner = getattr(app_state, "eval_runner", None) if app_state else None
        policy_engine = getattr(app_state, "policy_engine", None) if app_state else None
        mcp_client = self._get_mcp_client()

        # ── Extract RAG/routing/intelligence services from app.state ─────────────
        _embedder = getattr(app_state, "embedder", None) if app_state else None
        _semantic_cache = getattr(app_state, "semantic_cache", None) if app_state else None
        # BK3 (D-20 follow-up): knowledge-graph store for the planner's
        # graph_facts producer (ContextPipeline). Optional — None when unset.
        _knowledge_graph_store = (
            getattr(app_state, "knowledge_graph_store", None) if app_state else None
        )
        _model_router = None  # built after _agent_config is loaded below
        _prompt_optimizer = getattr(app_state, "prompt_optimizer", None) if app_state else None
        _bulkhead_registry = getattr(app_state, "bulkhead_registry", None) if app_state else None
        # Execution memory (H-3: wire from app.state instead of leaving None)
        exec_memory = getattr(app_state, "exec_memory", None) if app_state else None
        # Agent-level feature flags — read from agent config when available (H-4)
        _agent_config: dict[str, Any] = {}
        _system_prompt: str = ""
        if agent_id:
            _agent_store_ref = self._get_agent_store()
            if _agent_store_ref is not None:
                try:
                    _agent_record = _agent_store_ref.get(agent_id, tenant_ctx=tenant_ctx)
                    if isinstance(_agent_record, dict):
                        _agent_config = _agent_record
                        _system_prompt = _agent_record.get("system_prompt", "")
                except Exception as _ae:
                    _svc_logger.warning(
                        "agent_config_load_failed", agent_id=agent_id, error=str(_ae)
                    )

        # Use ModelOrchestrator for rich tier-based model selection with budget downgrade
        try:
            from app.ai_router.model_orchestrator import ModelOrchestrator, ModelOrchestratorAdapter

            _model_router = ModelOrchestratorAdapter(
                orchestrator=ModelOrchestrator(),
                default_tier=_agent_config.get("model_tier", "medium"),
            )
        except Exception:
            # Fallback to simple ModelRouter if orchestrator fails
            try:
                from app.agent.model_router import ModelRouter, get_router_for_tenant

                _model_router = get_router_for_tenant(_agent_config)
            except Exception:
                _model_router = None

        # Extract per-agent execution settings (FIX 4)
        _max_iterations = int(_agent_config.get("max_iterations", 6))
        # A goal-level model_override (POST /goals body) wins over the agent's
        # pinned model. It used to be written to execution_context and never read.
        _goal_model_override = str((execution_context or {}).get("model_override") or "")
        _model_override = _goal_model_override or str(
            _agent_config.get("model_override", "") or ""
        )

        # Per-goal role map: which configured model serves planning/execution/
        # verification, restricted to models THIS goal's provider can route (a
        # tenant-configured Anthropic/OpenAI provider gets none and keeps the
        # router's previous behaviour). See app/ai_router/deployment_roles.py.
        if _model_router is not None and hasattr(_model_router, "set_role_map"):
            try:
                from app.ai_router.deployment_roles import deployment_role_models, servable_models

                _servable = servable_models(provider)
                if _servable:
                    _model_router.set_role_map(deployment_role_models(servable=_servable))
            except Exception as _rm_exc:
                _svc_logger.warning("model_role_map_failed", error=str(_rm_exc))
            # Tenant routing policies (PUT /models/routing-policies): a preferred
            # model for planning/execution/verification pins that role. They were
            # saved and never applied to any goal.
            try:
                from app.ai_router.deployment_roles import servable_models as _sm
                from app.ai_router.registry import tenant_policy_role_models

                _policy_roles = tenant_policy_role_models(
                    tenant_ctx.tenant_id, servable=_sm(provider)
                )
                if _policy_roles:
                    _model_router.set_role_map({**_model_router.role_map, **_policy_roles})
            except Exception as _tp_exc:
                _svc_logger.warning("tenant_routing_policy_apply_failed", error=str(_tp_exc))

        # Apply model override to the model router before building the graph
        if _model_override:
            if _model_router is None:
                from app.agent.model_router import ModelRouter

                _model_router = ModelRouter()
            try:
                _model_router = _model_router.with_override(_model_override)  # copy-on-write
            except Exception as _mo_exc:
                if _goal_model_override:
                    # The caller explicitly asked for this model: never silently
                    # run the goal on a different one.
                    raise ValueError(
                        f"model_override '{_goal_model_override}' could not be applied: {_mo_exc}"
                    ) from _mo_exc
                _svc_logger.warning("agent_model_override_apply_failed", error=str(_mo_exc))

        # ── Phase 22: Wire per-connector circuit breakers ─────────────────────────
        from app.reliability.circuit_breaker import CircuitBreaker
        from app.reliability.redis_circuit_breaker import RedisCircuitBreaker

        _circuit_breakers: dict[str, Any] = {}
        _redis_for_cb = getattr(app_state, "_rate_limiter_redis", None) if app_state else None

        # Pre-wire a circuit breaker per connector the agent uses
        if _agent_config.get("connector_ids"):
            for _cid in list(_agent_config["connector_ids"])[:10]:
                _cid_str = str(_cid)
                if _redis_for_cb is not None:
                    _circuit_breakers[_cid_str] = RedisCircuitBreaker(
                        redis_client=_redis_for_cb,
                        tenant_id=tenant_ctx.tenant_id,
                        tool_name=f"mcp:{_cid_str}",
                        failure_threshold=5,
                        cooldown_seconds=60,
                    )
                else:
                    _circuit_breakers[_cid_str] = CircuitBreaker(
                        failure_threshold=5,
                        cooldown_seconds=60,
                    )

        # Always wire a circuit breaker for the LLM provider
        if _redis_for_cb is not None:
            _circuit_breakers["llm"] = RedisCircuitBreaker(
                redis_client=_redis_for_cb,
                tenant_id=tenant_ctx.tenant_id,
                tool_name="llm_provider",
                failure_threshold=3,
                cooldown_seconds=120,
            )
        else:
            _circuit_breakers["llm"] = CircuitBreaker(
                failure_threshold=3,
                cooldown_seconds=120,
            )

        # Phase 3 services — grounding, consensus, synthesis, calibration
        from app.agent.grounding import GroundingChecker
        from app.agent.synthesis import AnswerSynthesizer
        from app.intelligence.verifier_calibration import _default_calibration_store

        _grounding_checker = GroundingChecker()
        _answer_synthesizer = AnswerSynthesizer(llm_provider=provider)
        _calibration_store = getattr(app_state, "calibration_store", _default_calibration_store)
        _consensus_verifier = None
        try:
            from app.agent.consensus import ConsensusVerifier

            _consensus_verifier = ConsensusVerifier(primary_verifier=provider) if provider else None
        except Exception:
            pass

        # N1: Extract pattern flags from agent_config + execution_context runtime_profile.
        # These MUST be passed to the constructor — setting them post-construction is a no-op
        # because _build() runs inside __init__ and compiles the LangGraph statically.
        _enable_self_refine = bool(_agent_config.get("enable_self_refine", False))
        _enable_self_consistency = bool(_agent_config.get("enable_self_consistency", False))
        _enable_tree_of_thoughts = bool(_agent_config.get("enable_tree_of_thoughts", False))
        _enable_peer_review = bool(_agent_config.get("enable_peer_review", False))
        # D-2: multi-agent pattern flags were extracted for the other reasoning nodes
        # but supervisor/debate were dropped here, so an agent configured for them
        # never got the real SupervisorAgent/DebateOrchestrator nodes on the default
        # (non-dynamic-orchestration) path. Thread them through like the others.
        _enable_supervisor = bool(_agent_config.get("enable_supervisor", False))
        _enable_debate = bool(_agent_config.get("enable_debate", False))
        # Wrap the resolved provider as role-tagged traced providers so every
        # planner/executor/verifier LLM call emits a GenAI span (tokens/cost/
        # latency/role) under the goal's trace — the Langfuse-style generations.
        from app.observability.traced_provider import traced_role_providers

        _traced_roles = traced_role_providers(provider)
        graph_services = {
            "planner": _traced_roles["planner"],
            "executor": _traced_roles["executor"],
            "verifier": _traced_roles["verifier"],
            "max_iterations": _max_iterations,
            "audit_log": audit_log,
            "cost_controller": cost_controller,
            "hitl_gateway": hitl_gateway,
            "knowledge_store": knowledge_store,
            "knowledge_graph_store": _knowledge_graph_store,
            "prospective_service": (
                getattr(app_state, "prospective_memory_service", None) if app_state else None
            ),
            "grant_store": getattr(app_state, "grant_store", None) if app_state else None,
            "enforce_grants": _agent_grants_enforced(),
            "retrieval_gateway": retrieval_gateway,
            "long_term_memory": long_term_memory,
            "mcp_client": mcp_client,
            "eval_runner": eval_runner,
            "result_processor": ResultProcessor(),
            "dedup_cache": _build_dedup_cache(getattr(self, "_redis", None)),
            "rollback_engine": RollbackEngine(),
            "guardrail_checker": GuardrailChecker(),
            "policy_engine": policy_engine,
            # SAFE-1 (P0-12): wire the default-deny permission matrix so the
            # executor's per-tool governance check is live (previously always None).
            "permission_matrix": (
                getattr(app_state, "permission_matrix", None) if app_state else None
            ),
            # Phase 22: per-connector circuit breakers
            "circuit_breakers": _circuit_breakers,
            # Execution memory (H-3)
            "exec_memory": exec_memory,
            # RAG / intelligence services
            "embedder": _embedder,
            "semantic_cache": _semantic_cache,
            "llm_response_cache": getattr(app_state, "llm_response_cache", None),
            "model_router": _model_router,
            # Distributed per-tenant concurrency bulkhead
            "bulkhead_registry": _bulkhead_registry,
            # Agent feature flags (H-4: loaded from agent record when available)
            "enable_cot": _agent_config.get("enable_cot", False),
            "enable_reflection": _agent_config.get("enable_reflection", False),
            "enable_goal_tree": _agent_config.get("enable_goal_tree", False),
            # WS-3: a caller-supplied execution_context override (currently used
            # by OrgService.create_mission_and_execute to force "supervised" when
            # its MetaOrchestrator flags the mission as needing an approval gate)
            # takes precedence over the agent's own configured autonomy_mode, so
            # a mission-level HITL requirement cannot be silently downgraded by
            # whatever agent auto-routing happens to pick.
            # …then clamped by the tenant's compliance ceiling, resolved at
            # submission and carried on execution_context. The clamp belongs here
            # rather than at submission because this is where the requested mode
            # is finally assembled from BOTH the execution context and the
            # agent's own config — an agent configured "fully-autonomous" under a
            # bundle capping at "bounded-autonomous" is only visible at this point.
            "autonomy_mode": clamp_autonomy_mode(
                (execution_context or {}).get("autonomy_mode")
                or _agent_config.get("autonomy_mode", "bounded-autonomous"),
                str(
                    (execution_context or {}).get("compliance_autonomy_ceiling")
                    or "fully-autonomous"
                ),
            ),
            # N1: pattern flags passed at construction so _build() includes them in the graph
            "enable_self_refine": _enable_self_refine,
            "enable_self_consistency": _enable_self_consistency,
            "enable_tree_of_thoughts": _enable_tree_of_thoughts,
            "enable_peer_review": _enable_peer_review,
            # D-2: real supervisor decomposition / debate voting nodes, opt-in per agent
            "enable_supervisor": _enable_supervisor,
            "enable_debate": _enable_debate,
            # Use RedisSaver when available for cross-replica state persistence (Fix 7)
            "checkpointer": _resolve_checkpointer(app_state),
            # H-1: real token-cost tracker
            "cost_tracker": getattr(app_state, "cost_tracker", None),
            # Phase 3 services — grounding, consensus, synthesis, calibration
            "grounding_checker": _grounding_checker,
            "answer_synthesizer": _answer_synthesizer,
            "calibration_store": _calibration_store,
            "consensus_verifier": _consensus_verifier,
            "tool_reliability_store": getattr(app_state, "tool_reliability_store", None),
            "episodic_memory": getattr(app_state, "episodic_memory", None),
            "procedural_memory": getattr(app_state, "procedural_memory", None),
            "reflexion_service": getattr(app_state, "reflexion_service", None),
        }
        # What actually runs is recorded on the goal (execution_context
        # ["strategy_execution"]) from the constructed runtime — never inferred from
        # what the profile asked for — so a downgrade is visible instead of silent.
        # Shared with the Celery worker (app.scaling.tasks.run_goal).
        from app.orchestration.profiled_graph import build_profiled_graph

        graph, _strategy_execution = build_profiled_graph(
            runtime_profile,
            graph_services,
            _agent_config,
            distributed_loop_builder=lambda: self._try_build_distributed_strategy_loop(
                runtime_profile,
                tenant_ctx=tenant_ctx,
                app_state=app_state,
                agent_id=agent_id,
                provider=provider,
            ),
        )
        if execution_context is not None:
            execution_context["strategy_execution"] = _strategy_execution
        # Wire attributes that are set externally (not constructor params)
        graph._db_session_factory = self._db
        # Survives role/trace/circuit-breaker wrapping, unlike a type check on
        # graph._planner (which is a wrapper, so the old check never fired).
        graph._simulated_provider = simulated
        # Store agent system prompt so callers can inject it into initial_context
        graph._agent_system_prompt = _system_prompt
        graph._prompt_optimizer = _prompt_optimizer
        # Wire RPA executor for direct RPA tool dispatch without MCP
        _rpa_exec = getattr(app_state, "rpa_executor", None)
        graph._rpa_executor = _rpa_exec
        # H-2: Wire app_state and agent_id for SelfOptimizerV2 A/B experiment tracking
        graph._app_state = app_state
        graph._agent_id = agent_id
        # The civilization spawn tool submits the child's goal through this; it was
        # never assigned, so every spawn created an agent that never ran anything.
        graph._goal_service = self
        # Phase 25: Wire self-optimizer for automatic improvement on poor performance
        from app.intelligence.self_optimization import SelfOptimizer

        # app_state may be the FastAPI app; the optimizer is on app.state. The
        # direct lookup always missed and every graph got a throwaway instance.
        _opt_state = getattr(app_state, "state", app_state) if app_state else None
        _self_optimizer = getattr(_opt_state, "self_optimizer", None) if _opt_state else None
        if _self_optimizer is None:
            _self_optimizer = SelfOptimizer()
        graph._self_optimizer = _self_optimizer

        # Record model selections for observability (AI Router)
        try:
            model_selections = self._select_models_for_tenant(tenant_ctx)
            if model_selections:
                _svc_logger.info(
                    "ai_router_selections",
                    tenant=tenant_ctx.tenant_id,
                    selections=model_selections,
                )
        except Exception:
            pass

        return graph

    def _coordination_ready(self) -> bool:
        """True when the app wires a StrategyRunner that can really run DISTRIBUTED goals."""
        app_state: Any = self._app_state
        app_state = getattr(app_state, "state", app_state)
        runner = getattr(app_state, "strategy_runner", None) if app_state is not None else None
        return bool(
            runner is not None
            and getattr(runner, "has_real_executor", False) is True
            and getattr(app_state, "strategy_goal_context_store", None) is not None
        )

    def _try_build_distributed_strategy_loop(
        self,
        runtime_profile: Any,
        *,
        tenant_ctx: TenantContext,
        app_state: Any,
        agent_id: str | None,
        provider: Any,
    ) -> Any | None:
        """Build a DistributedStrategyLoop when the app has a genuinely wired StrategyRunner.

        Returns ``None`` (never raises) when the runner is absent or still carrying the inert
        default executor, so the caller can fall back to the local AgentGraph kernel — the
        DISTRIBUTED tier must never fail a goal outright just because the runner isn't wired.
        """
        strategy_runner = getattr(app_state, "strategy_runner", None) if app_state else None
        if strategy_runner is None or not getattr(strategy_runner, "has_real_executor", False):
            return None
        context_store = getattr(app_state, "strategy_goal_context_store", None)
        if context_store is None:
            return None
        from app.orchestration.distributed_strategy_loop import DistributedStrategyLoop

        return DistributedStrategyLoop(
            strategy_runner=strategy_runner,
            context_store=context_store,
            profile=runtime_profile,
            provider=provider,
            agent_id=agent_id,
        )

    def _select_models_for_tenant(self, tenant_ctx: TenantContext) -> dict[str, str]:
        """Use AI Router to select optimal models for each role."""
        try:
            from app.ai_router.models import TaskType
            from app.ai_router.router import ai_router

            selections: dict[str, str] = {}
            for task_type, role_name, need_tools in [
                (TaskType.PLANNING, "planner", False),
                (TaskType.EXECUTION, "executor", True),
                (TaskType.VERIFICATION, "verifier", False),
            ]:
                model = ai_router.select_model(
                    task_type,
                    tenant_ctx.tenant_id,
                    require_tools=(need_tools),
                )
                if model:
                    selections[role_name] = f"{model.provider}/{model.model_id}"
            return selections
        except Exception:
            return {}

    async def _build_runtime_profile(
        self,
        goal: str,
        *,
        goal_id: str,
        tenant_ctx: Any,
        agent_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the goal's GoalRuntimeProfile and decide whether it drives execution.

        Returns ``{}`` when dynamic orchestration is off, else a dict with:

        * ``profile_object`` — the profile the runtime compiles/dispatches, set ONLY when
          the strategy-runtime-v2 rollout admits this tenant (allowlist, no shadow, no kill
          switch). On the ``legacy`` / ``rejected`` paths it is ``None`` and the goal runs
          the legacy agent-config graph; the profile is still recorded for comparison.
        * ``context`` — JSON-safe entries for ``goals.execution_context``.
        * ``columns`` — JSON-safe values for the goals runtime-profile columns, written
          with the goal row (``_db_persist_goal``) under the tenant's RLS context.

        A build failure is never silent: it is logged and returned as an explicit
        ``runtime_profile_fallback`` record on the goal (the legacy path then runs).
        """
        from app.core.runtime_flags import get_runtime_flags

        flags = get_runtime_flags()
        if not flags.dynamic_orchestration:
            return {}
        requested_primary = (agent_config or {}).get("primary_strategy")
        try:
            from app.orchestration.runtime_profile_builder import RuntimeProfileBuilder

            builder = RuntimeProfileBuilder(llm_provider=self._classifier_provider())
            _builder_config = dict(agent_config or {})
            # DISTRIBUTED strategies are admitted only when a StrategyRunner with a real
            # executor is wired to run them (see _try_build_distributed_strategy_loop).
            _builder_config.setdefault("coordination_ready", self._coordination_ready())
            profile, trace = await builder.build_with_trace(
                goal,
                tenant_id=tenant_ctx.tenant_id,
                goal_id=goal_id,
                agent_config=_builder_config,
            )
            from app.orchestration.strategy_certification import RolloutController

            rollout = RolloutController(
                shadow=flags.strategy_runtime_v2_shadow,
                allowlist=flags.strategy_runtime_v2_tenant_allowlist,
                kill_switch=flags.strategy_runtime_v2_kill_switch,
            ).choose(
                tenant_ctx.tenant_id,
                {
                    "strategy": profile.agent_patterns.reasoning[0],
                    "topology": profile.agent_patterns.reasoning,
                    "readiness": "legacy_unverified",
                    "cost": profile.model_plan.cost_class,
                    "latency": profile.model_plan.latency_class,
                },
                {
                    "strategy": profile.primary_strategy.strategy_id,
                    "topology": [
                        profile.primary_strategy.strategy_id,
                        *(item.strategy_id for item in profile.auxiliary_strategies),
                    ],
                    "readiness": profile.readiness_snapshot_ref,
                    "cost": profile.effective_limits.cost_usd,
                    "latency": profile.effective_limits.duration_seconds,
                },
            )
        except Exception as exc:
            from app.orchestration.runtime_profile_builder import InvalidStrategyOverrideError

            reason = (
                "invalid_strategy_override"
                if isinstance(exc, InvalidStrategyOverrideError)
                else "profile_build_failed"
            )
            _svc_logger.warning(
                "runtime_profile_build_failed_legacy_fallback",
                goal_id=goal_id,
                reason=reason,
                error_type=type(exc).__name__,
                error=str(exc)[:300],
            )
            fallback: dict[str, Any] = {
                "reason": reason,
                "error_type": type(exc).__name__,
                "detail": str(exc)[:300],
                "fallback": "legacy",
            }
            if requested_primary:
                fallback["requested_primary"] = str(requested_primary)
            return {
                "profile_object": None,
                "context": {
                    "runtime_profile_fallback": fallback,
                    "strategy_runtime_path": "legacy",
                },
                "columns": None,
            }

        # Every value below is plain JSON (the dataclass profile goes through to_dict()),
        # so execution_context and the snapshot columns can be persisted as-is.
        snapshot = profile.to_dict()
        context: dict[str, Any] = {
            "runtime_profile": snapshot,
            "decision_trace": trace.to_dict(),
            "profile_id": profile.profile_id,
            "assembly_latency_ms": profile.assembly_latency_ms,
            "strategy_runtime_path": rollout.path,
            "strategy_runtime_shadow_comparison": rollout.shadow_comparison,
        }
        if rollout.path != "v2" and requested_primary:
            # An explicit strategy request only runs on the v2 runtime; say so on the goal.
            context["runtime_profile_fallback"] = {
                "reason": (
                    "strategy_runtime_v2_kill_switch"
                    if rollout.path == "rejected"
                    else "strategy_runtime_v2_not_enabled"
                ),
                "requested_primary": str(requested_primary),
                "fallback": "legacy",
            }
        columns = {
            "runtime_profile_id": profile.profile_id,
            "runtime_profile_version": profile.profile_version,
            "strategy_registry_revision": profile.registry_revision,
            "runtime_profile_snapshot": snapshot,
            "rejected_strategies": [
                {"strategy_id": item.strategy_id, "reason_code": item.reason_code}
                for item in profile.rejected_alternatives
            ],
            "patterns_used": [
                profile.primary_strategy.strategy_id,
                *(item.strategy_id for item in profile.auxiliary_strategies),
            ],
            "rag_strategy_used": profile.rag_strategy.strategy,
        }
        return {
            # The rollout decides: only an admitted v2 tenant executes the profile.
            "profile_object": profile if rollout.path == "v2" else None,
            # Always available for scoring: eval scorecards used to depend on
            # profile_object, so every non-v2 tenant (the default) stopped
            # getting a persisted scorecard once the rollout gate became real.
            "observed_profile": profile,
            "context": context,
            "columns": columns,
        }

    def _classifier_provider(self) -> Any:
        """The platform LLM for tier-2 goal classification, when enabled (else None)."""
        try:
            from app.core.config import get_settings

            if not get_settings().orchestration_llm_classifier_enabled:
                return None
        except Exception:
            return None
        app_state: Any = self._app_state
        app_state = getattr(app_state, "state", app_state)
        provider = getattr(app_state, "_app_provider", None) if app_state is not None else None
        if provider is None or isinstance(provider, FakeProvider):
            return None
        return provider

    async def _dependency_health(self, tenant_ctx: TenantContext | None) -> Any:
        """Live dependency health for the ReadinessGate (see runtime_readiness.health_probe)."""
        from app.runtime_readiness.dependency_health import DepStatus
        from app.runtime_readiness.health_probe import collect_dependency_health

        health = await collect_dependency_health(self._app_state)
        if health.llm_provider is not DepStatus.HEALTHY and tenant_ctx is not None:
            # No platform provider — a tenant BYOK config still makes the goal runnable.
            try:
                tenant_cfg = await self._resolve_tenant_llm_config(tenant_ctx)
            except Exception:
                tenant_cfg = None
            if tenant_cfg:
                health.llm_provider = DepStatus.HEALTHY
        return health

    async def _check_readiness(
        self,
        runtime_profile: Any = None,
        *,
        tenant_ctx: TenantContext | None = None,
    ) -> tuple[bool, str]:
        """Gate a goal on the platform's real dependency health (READINESS_GATE, default on).

        Blocks when a required dependency (Postgres, the LLM provider) is configured but
        down. A readiness implementation error is itself a blocking readiness failure, so
        production never executes a goal whose dependencies were not verified.
        """
        from app.core.runtime_flags import get_runtime_flags

        if not getattr(get_runtime_flags(), "readiness_gate", False):
            return True, ""
        try:
            from app.runtime_readiness.readiness_gate import ReadinessGate

            result = ReadinessGate(await self._dependency_health(tenant_ctx)).check(
                runtime_profile
            )
            if not result.ready:
                return False, (
                    "Platform not ready: required dependency unavailable: "
                    + ", ".join(result.blocking_deps)
                )
            return True, ""
        except Exception as exc:
            return False, f"Readiness check failed: {type(exc).__name__}"

    async def _readiness_preflight(self, tenant_ctx: TenantContext) -> None:
        """Refuse a goal up-front (503) instead of accepting one that cannot run."""
        ready, reason = await self._check_readiness(None, tenant_ctx=tenant_ctx)
        if not ready:
            _svc_logger.warning(
                "goal_blocked_by_readiness_gate", tenant_id=tenant_ctx.tenant_id, reason=reason
            )
            raise ServiceUnavailableError(reason, code="PLATFORM_NOT_READY")

    async def _emergency_stop_preflight(
        self, tenant_ctx: TenantContext, execution_context: dict[str, Any] | None
    ) -> None:
        """Refuse new work while a tenant/org emergency stop is active (fail closed)."""
        from app.governance.emergency_stop import UNVERIFIABLE_REASON, enforce_emergency_stop

        org_id = (execution_context or {}).get("org_id")
        reason = await enforce_emergency_stop(
            getattr(self, "_redis", None), tenant_ctx.tenant_id, str(org_id) if org_id else None
        )
        if reason is None:
            return
        _svc_logger.warning(
            "goal_blocked_by_emergency_stop", tenant_id=tenant_ctx.tenant_id, reason=reason
        )
        if reason == UNVERIFIABLE_REASON:
            raise ServiceUnavailableError(reason, code="EMERGENCY_STOP_UNVERIFIABLE")
        raise ConflictError(reason, code="EMERGENCY_STOP_ACTIVE")

    async def active_goal_ids(self, tenant_ctx: TenantContext) -> list[str]:
        """Every non-terminal goal of the tenant — on ANY replica or worker.

        The emergency stop used to enumerate this replica's in-memory goals only,
        so goals run by other replicas or Celery workers were never cancelled.
        Postgres is the fleet-wide source of truth; a DB error propagates (the
        caller must not report "all goals cancelled" on a partial list).
        """
        ids: dict[str, None] = {
            gid: None
            for gid, rec in list(self._goals.items())
            if getattr(rec, "tenant_id", "") == tenant_ctx.tenant_id
            and rec.status not in _TERMINAL_STATUSES
        }
        if self._db is not None:
            from sqlalchemy import select

            from app.db.models.goal import Goal
            from app.db.rls import sqlalchemy_rls_context

            terminal = [s.value for s in _TERMINAL_STATUSES]
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
                rows = (
                    await session.execute(
                        select(Goal.id).where(
                            Goal.tenant_id == tenant_ctx.tenant_id,
                            Goal.status.notin_(terminal),
                        )
                    )
                ).scalars().all()
            ids.update({str(r): None for r in rows})
        return list(ids)

    # ── private helpers ───────────────────────────────────────────────────────

    async def _submit_single_goal(
        self,
        *,
        goal: str,
        agent_id: str | None,
        tenant_ctx: TenantContext,
        priority: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        """Submit a goal for a specific agent, bypassing routing (used by multi-agent mode)."""
        return await self.submit_goal(
            goal=goal,
            priority=priority,
            dry_run=dry_run,
            tenant_ctx=tenant_ctx,
            agent_id=agent_id,
        )

    def _get_record(self, goal_id: str, tenant_ctx: TenantContext) -> GoalRecord:
        """Fetch and tenant-validate a :class:`GoalRecord`."""
        record = self._goals.get(goal_id)
        if record is None or record.tenant_id != tenant_ctx.tenant_id:
            raise NotFoundError(f"Goal not found: {goal_id}")
        return record

    async def _aget_record(self, goal_id: str, tenant_ctx: TenantContext) -> GoalRecord:
        """The goal as Postgres knows it, for lifecycle actions on ANY replica.

        ``_get_record`` only sees this replica's memory, so cancel / pause /
        resume / approve answered 404 on every replica but the creating one and
        for every worker-run goal. A goal this replica is not running itself is
        (re)loaded from the DB under the caller's tenant (RLS); one it runs
        locally keeps its in-memory record (live task, pause event, subscribers).
        """
        record = self._goals.get(goal_id)
        if record is not None and record.tenant_id == tenant_ctx.tenant_id:
            if record.task is not None and not record.task.done():
                return record
            refreshed = await self._db_get_goal_record(goal_id, tenant_ctx)
            return refreshed if refreshed is not None else record
        loaded = await self._db_get_goal_record(goal_id, tenant_ctx)
        if loaded is None:
            raise NotFoundError(f"Goal not found: {goal_id}")
        return loaded

    @staticmethod
    def _runs_locally(record: GoalRecord) -> bool:
        return record.task is not None and not record.task.done()

    async def _signal_runner(
        self, record: GoalRecord, signal: Callable[..., Awaitable[None]], action: str
    ) -> None:
        """Deliver a lifecycle signal through Redis to whoever runs the goal.

        For a goal this replica does not run (another replica's in-process loop,
        or a Celery worker) the Redis flag is the ONLY way to reach the runner,
        so a failed write fails the action closed (503) before any state
        changes. A locally-running goal also gets the flag (best effort) so a
        worker it may have been handed to still sees it.
        """
        redis = getattr(self, "_redis", None)
        local = self._runs_locally(record)
        if redis is None:
            if not local and self._task_queue is not None:
                # Queued goals run on workers, reachable only through Redis.
                raise ServiceUnavailableError(
                    f"Cannot {action} goal {record.goal_id}: no signal channel to its worker"
                )
            return
        try:
            await signal(record.goal_id, redis, strict=not local)
        except Exception as exc:
            raise ServiceUnavailableError(
                f"Cannot {action} goal {record.goal_id}: control signal not delivered",
                cause=exc,
            ) from exc

    def _get_agent_store(self) -> Any:
        """Return the configured agent store when the application wired one."""
        # Check directly wired store first (H-4: set by main.py lifespan)
        if self._agent_store is not None:
            return self._agent_store
        if self._app_state is None:
            return None
        store = getattr(self._app_state, "agent_store", None)
        if store is not None:
            return store
        state = getattr(self._app_state, "state", None)
        return getattr(state, "agent_store", None) if state is not None else None

    def _get_mcp_client(self) -> Any:
        """Return the configured MCP client when the application wired one."""
        if self._app_state is None:
            return None
        client = getattr(self._app_state, "mcp_client", None)
        if client is not None:
            return client
        state = getattr(self._app_state, "state", None)
        return getattr(state, "mcp_client", None) if state is not None else None

    def _validate_agent_id(self, agent_id: str | None, tenant_ctx: TenantContext) -> None:
        if agent_id is None:
            return
        agent_store = self._get_agent_store()
        if agent_store is None:
            return
        if agent_store.get(agent_id, tenant_ctx=tenant_ctx) is None:
            raise NotFoundError(f"Agent not found: {agent_id}")

    @staticmethod
    def _register_tools_from_context(loop: Any, tool_context: Any) -> None:
        """SAFE-2 (P0-13): register the tenant's discovered tool names into the
        agent's GuardrailChecker so hallucinated tool names are rejected.

        Without this the checker's known-tools registry stays empty and any
        tool name passes the hallucination guard.
        """
        checker = getattr(loop, "_guardrail_checker", None)
        if checker is None or tool_context is None:
            return
        try:
            names = {
                str(getattr(t, "name", "") or "")
                for t in (getattr(tool_context, "tools", None) or [])
                if getattr(t, "name", None)
            }
            names.discard("")
            if names:
                checker.register_tools(names)
        except Exception as _reg_exc:  # never break execution over registry wiring
            _svc_logger.warning("guardrail_tool_registration_failed", error=str(_reg_exc))

    async def _build_tool_context(
        self, agent_id: str | None, tenant_ctx: TenantContext, goal: str = ""
    ) -> ToolContext:
        # Always include built-in RPA tools so agents can use browser automation
        from app.rpa.tools import RPA_TOOLS

        tools: list[ToolRef] = [
            ToolRef(
                server_id="rpa",
                server_name="rpa",
                name=str(rpa_tool["name"]),
                description=str(rpa_tool["description"]),
                input_schema=dict(rpa_tool.get("input_schema", {})),
            )
            for rpa_tool in RPA_TOOLS
        ]

        if agent_id is None or self._app_state is None:
            return ToolContext(connectors=[], tools=tools)

        agent_store = self._get_agent_store()
        mcp_client = self._get_mcp_client()
        if agent_store is None or mcp_client is None:
            return ToolContext(connectors=[], tools=tools)

        agent: dict[str, Any] | None = agent_store.get(agent_id, tenant_ctx=tenant_ctx)
        if agent is None:
            return ToolContext(connectors=[], tools=tools)

        connector_errors: list[dict[str, str]] = []
        for connector_id in agent.get("connector_ids", []):
            connector_id_str = str(connector_id)
            try:
                discovered = await mcp_client.discover_tools(
                    server_id=connector_id_str, tenant_ctx=tenant_ctx
                )
            except Exception as exc:
                connector_errors.append({"connector_id": connector_id_str, "error": str(exc)})
                continue
            for discovered_tool in discovered:
                name = str(getattr(discovered_tool, "name", "") or "")
                if not name:
                    continue
                input_schema = getattr(discovered_tool, "input_schema", {})
                if not isinstance(input_schema, dict):
                    input_schema = {}
                server_id = connector_id_str
                server_name = str(getattr(discovered_tool, "server_name", server_id) or server_id)
                tools.append(
                    ToolRef(
                        server_id=server_id,
                        server_name=server_name,
                        name=name,
                        description=str(getattr(discovered_tool, "description", "") or ""),
                        input_schema=input_schema,
                    )
                )

        connector_metadata = dict(agent)
        if connector_errors:
            connector_metadata["connector_errors"] = connector_errors

        all_tools = tools  # full list (RPA + discovered connectors)

        # NEW: if we have a goal, ToolSelector, and enough tools, use tiered selection
        tool_selector = getattr(self._app_state, "tool_selector", None)
        if tool_selector is not None and goal and len(all_tools) > 0:
            try:
                selection = await tool_selector.select(
                    goal=goal,
                    tools=all_tools,
                    tenant_ctx=tenant_ctx,
                )
                from app.agent.tool_context import to_tiered_prompt

                tool_prompt = to_tiered_prompt(selection)
                return ToolContext(
                    connectors=[connector_metadata],
                    tools=selection.selected,
                    tool_prompt_override=tool_prompt,
                )
            except Exception as exc:
                _svc_logger.debug("tool_selector_failed_fallback_to_full", error=str(exc)[:60])

        return ToolContext(connectors=[connector_metadata], tools=all_tools)

    def _tenant_ctx_for_event_store(
        self, record: GoalRecord, tenant_ctx: TenantContext | None
    ) -> TenantContext:
        if tenant_ctx is not None:
            return tenant_ctx
        return TenantContext(
            tenant_id=record.tenant_id,
            plan=PlanTier.FREE,
            api_key_id="event-store-replay",
        )

    async def _persist_event(
        self,
        goal_id: str,
        event: dict[str, Any],
        record: GoalRecord,
        tenant_ctx: TenantContext | None,
    ) -> None:
        if self._event_store is None:
            return
        try:
            await self._event_store.append_event(
                goal_id,
                event,
                tenant_ctx=self._tenant_ctx_for_event_store(record, tenant_ctx),
            )
        except Exception as exc:
            _svc_logger.warning("DB persist goal event failed: %s", exc)

    async def _list_persisted_events(
        self, goal_id: str, tenant_ctx: TenantContext
    ) -> list[dict[str, Any]]:
        if self._event_store is None:
            return []
        try:
            events = await self._event_store.list_events(goal_id, tenant_ctx=tenant_ctx)
            return cast("list[dict[str, Any]]", events)
        except Exception as exc:
            _svc_logger.warning("DB list goal events failed: %s", exc)
            return []

    async def _list_events_since_persisted(
        self, goal_id: str, after_sequence: int, tenant_ctx: TenantContext
    ) -> list[dict[str, Any]]:
        """Return persisted events after *after_sequence* (with ``_seq`` keys)."""
        if self._event_store is None:
            return []
        try:
            return await self._event_store.list_events_since(
                goal_id, after_sequence=after_sequence, tenant_ctx=tenant_ctx
            )
        except Exception as exc:
            _svc_logger.warning("DB list events since failed: %s", exc)
            return []

    @staticmethod
    def _event_key(event: dict[str, Any]) -> str:
        try:
            return json.dumps(event, sort_keys=True, default=str)
        except TypeError:
            return repr(event)

    @classmethod
    def _merge_events_without_duplicates(
        cls, first: list[dict[str, Any]], second: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for event in [*first, *second]:
            key = cls._event_key(event)
            if key in seen:
                continue
            seen.add(key)
            merged.append(event)
        return merged

    async def _events_for_replay(
        self, goal_id: str, record: GoalRecord, tenant_ctx: TenantContext
    ) -> list[dict[str, Any]]:
        persisted_events = await self._list_persisted_events(goal_id, tenant_ctx)
        if not persisted_events:
            return list(record.events)
        if not record.events:
            return persisted_events
        return self._merge_events_without_duplicates(persisted_events, list(record.events))

    @staticmethod
    def _worker_complete_status(event: dict[str, Any]) -> GoalStatus | None:
        """Terminal status carried by a Celery ``worker_complete`` event.

        The worker emits ``worker_complete`` for EVERY finished run with the real
        final ``status`` (complete / failed / waiting_human ...). It used to be
        read as COMPLETE unconditionally, so failed runs looked successful and an
        approval-suspended goal had its SSE stream closed. ``None`` = not terminal.
        A legacy event without ``status`` keeps its historic COMPLETE meaning.
        """
        raw = str(event.get("status") or "complete").lower()
        return {
            "complete": GoalStatus.COMPLETE,
            "failed": GoalStatus.FAILED,
            "cancelled": GoalStatus.CANCELLED,
        }.get(raw)

    @classmethod
    def _status_from_events(cls, events: list[dict[str, Any]]) -> GoalStatus | None:
        for event in reversed(events):
            etype = event.get("type")
            if etype == "worker_complete":
                return cls._worker_complete_status(event)
            if etype == "goal_complete":
                return GoalStatus.COMPLETE
            if etype in {"goal_failed", "worker_failed"}:
                return GoalStatus.FAILED
            if etype == "goal_cancelled":
                return GoalStatus.CANCELLED
        return None

    def _should_refresh_goal_from_db(self, record: GoalRecord) -> bool:
        if self._db is None:
            return False
        if self._task_queue is not None:
            return True
        return record.task is None and record.status not in _TERMINAL_STATUSES

    async def _refresh_goal_from_db_if_needed(
        self, record: GoalRecord, tenant_ctx: TenantContext
    ) -> GoalRecord:
        if not self._should_refresh_goal_from_db(record):
            return record
        persisted = await self._db_get_goal_record(record.goal_id, tenant_ctx)
        if persisted is None:
            return record
        return persisted

    async def _event_count_for_response(
        self, goal_id: str, record: GoalRecord, tenant_ctx: TenantContext
    ) -> int:
        events = await self._events_for_replay(goal_id, record, tenant_ctx)
        return len(events)

    async def _deliver_completion_to_chat(
        self, record: GoalRecord, event: dict[str, Any]
    ) -> None:
        """Phase 2 delivery-back: if a goal was launched from a chat conversation,
        post its result back into that conversation. Fail-safe — never disrupts
        goal completion, no-op when there is no chat binding / no chat service."""
        with suppress(Exception):
            from app.chat.service import extract_delivery_target, humanize_goal_result

            target = extract_delivery_target(record.execution_context)
            if not target:
                return
            aps: Any = self._app_state
            with suppress(Exception):
                from starlette.applications import Starlette

                if isinstance(aps, Starlette):
                    aps = aps.state
            chat = getattr(aps, "chat_service", None)
            if chat is None:
                return
            # Always deliver human-readable prose — never a raw JSON result blob.
            content = humanize_goal_result(record.goal_text, event)
            await chat.adeliver_result(
                session_id=target["session_id"],
                tenant_id=record.tenant_id,
                content=content,
                goal_id=record.goal_id,
            )

    async def _dispatch_event(
        self,
        goal_id: str,
        event: dict[str, Any],
        tenant_ctx: TenantContext | None = None,
    ) -> None:
        """Append *event* to the record and fan out to all live subscribers.

        Ephemeral event types (``token_chunk``, ``heartbeat``) are forwarded to
        live SSE subscribers but are **not** stored in the in-memory event log or
        persisted to the database.  This prevents the event log from being flooded
        with thousands of token-level fragments during a single goal execution.
        """
        record = self._goals.get(goal_id)
        if record is None:
            return
        sanitized_event = sanitize_event(event)
        _ephemeral_event_types = {"token_chunk", "heartbeat"}
        _is_ephemeral = sanitized_event.get("type") in _ephemeral_event_types
        if not _is_ephemeral:
            record.events.append(sanitized_event)
            await self._persist_event(goal_id, sanitized_event, record, tenant_ctx)
        # Reflect terminal status in the record and record metrics.
        etype = sanitized_event.get("type")
        if etype == "goal_complete":
            record.status = GoalStatus.COMPLETE
            record.completed_at = datetime.now(UTC).isoformat()
            self._record_terminal_goal_metrics(record, "completed")
            # Phase 2: post the result back into the originating chat conversation.
            await self._deliver_completion_to_chat(record, sanitized_event)
            # Agent Runtime: mark trace success
            try:
                from app.agent_runtime.store import agent_runtime_store

                _t_id = record.execution_context.get("agent_runtime_trace_id")
                if _t_id:
                    await agent_runtime_store.update_trace(
                        record.tenant_id,
                        str(_t_id),
                        success=True,
                        duration_ms=(_monotonic() - record.started_monotonic) * 1000,
                    )
            except Exception:
                pass
            # Persist status update to PostgreSQL in the background.
            if self._db is not None:
                self._track_db_task(
                    self._db_update_goal_status(
                        goal_id,
                        record.tenant_id,
                        record.status.value,
                        error_message="",
                        iterations=len(record.events),
                    )
                )
            # ── Eval scoring on completion (Task 3) ────────────────────────────
            _logger = _svc_logger
            try:
                # ``self._app_state`` is the FastAPI app; eval_runner (like every
                # other service) lives on ``app.state``. Reading it off the app
                # directly always returned None, so auto-eval on goal completion
                # never ran (GET /eval stayed 'not_evaluated'). Unwrap to app.state
                # only for the real Starlette app (a mock/plain object is used as-is,
                # so unit tests that set _app_state.eval_runner directly still work).
                _eval_aps: Any = self._app_state
                try:
                    from starlette.applications import Starlette as _Starlette

                    if isinstance(self._app_state, _Starlette):
                        _eval_aps = self._app_state.state
                except Exception:
                    pass
                eval_runner = getattr(_eval_aps, "eval_runner", None)
                if eval_runner is not None:
                    tenant_ctx_for_record: TenantContext = (
                        tenant_ctx
                        if tenant_ctx is not None
                        else TenantContext(
                            tenant_id=record.tenant_id,
                            plan=PlanTier.FREE,
                            api_key_id="eval-scoring",
                        )
                    )
                    # Build a minimal AgentState from the recorded events.
                    steps: list[StepResult] = []
                    verification_success = False
                    for evt in record.events:
                        if evt.get("type") == "plan_ready":
                            for step_text in evt.get("steps", []):
                                steps.append(
                                    StepResult(
                                        description=str(step_text),
                                        status=StepStatus.COMPLETE,
                                    )
                                )
                        elif evt.get("type") == "verification_done":
                            verification_success = bool(evt.get("success", False))
                    agent_state = AgentState(
                        goal_id=goal_id,
                        goal=record.goal_text,
                        tenant_ctx=tenant_ctx_for_record,
                        status=GoalStatus.COMPLETE,
                        iterations=len(record.events),
                        steps=steps,
                        verification_success=verification_success,
                    )
                    self._eval_pending.add(goal_id)
                    try:
                        scorecard = await eval_runner.score_and_persist(
                            agent_state,
                            tenant_ctx_for_record,
                            provider=getattr(_eval_aps, "_app_provider", None),  # on app.state
                            db=self._db,
                        )
                        self._eval_scores[goal_id] = scorecard
                    finally:
                        self._eval_pending.discard(goal_id)
                    await self._publish_chain_event(
                        record,
                        "goal.score_below",
                        tenant_ctx,
                        score=float(scorecard.average_score()),
                    )
                    # Trigger self-optimizer when score falls below threshold.
                    if scorecard.average_score() < 0.7:
                        # self_optimizer lives on app.state (self._app_state is the
                        # app): this lookup always missed, so low scores never
                        # triggered the optimizer.
                        self_optimizer = getattr(_eval_aps, "self_optimizer", None)
                        if self_optimizer is not None:
                            try:
                                failed_events = [
                                    e
                                    for e in record.events
                                    if "fail" in e.get("type", "").lower()
                                    or "error" in e.get("type", "").lower()
                                ]
                                error_log = " | ".join(
                                    e.get("reason", str(e)) for e in failed_events
                                )
                                self_optimizer.analyze_and_suggest(
                                    goal=record.goal_text,
                                    scorecard=scorecard,
                                    error_log=error_log,
                                    tenant_ctx=tenant_ctx_for_record,
                                )
                            except Exception as opt_exc:
                                _logger.warning(
                                    "Self-optimizer failed for goal %s: %s", goal_id, opt_exc
                                )
            except Exception as exc:
                _svc_logger.warning("Eval scoring failed for goal %s: %s", goal_id, exc)
        elif etype == "goal_failed":
            record.status = GoalStatus.FAILED
            record.completed_at = datetime.now(UTC).isoformat()
            self._record_terminal_goal_metrics(record, "failed")
            # Agent Runtime: mark trace failed
            try:
                from app.agent_runtime.store import agent_runtime_store

                _t_id = record.execution_context.get("agent_runtime_trace_id")
                if _t_id:
                    await agent_runtime_store.update_trace(
                        record.tenant_id,
                        str(_t_id),
                        success=False,
                        error=sanitized_event.get("reason", "goal_failed"),
                        duration_ms=(_monotonic() - record.started_monotonic) * 1000,
                    )
            except Exception:
                pass
            if self._db is not None:
                self._track_db_task(
                    self._db_update_goal_status(
                        goal_id,
                        record.tenant_id,
                        record.status.value,
                        error_message=record.events[-1].get("reason", "") if record.events else "",
                        iterations=len(record.events),
                    )
                )
        elif etype == "goal_cancelled":
            record.status = GoalStatus.CANCELLED
            record.completed_at = datetime.now(UTC).isoformat()
            self._record_terminal_goal_metrics(record, "cancelled")
        elif etype in ("waiting_approval", "tool_call_pending_approval"):
            # WS-3: reflect an in-flight HITL gate on the goal's own status so
            # GET /goals/{id} shows "waiting_human" while the executor blocks on
            # HITLGateway.wait_for_approval — previously the record stayed
            # "executing" for the whole pause, which hid the gate from anyone
            # polling goal status instead of the approvals inbox.
            if record.status not in _TERMINAL_STATUSES:
                record.status = GoalStatus.WAITING_HUMAN
        elif etype == "approval_granted":
            # Approval resolved and the executor is about to resume — flip the
            # goal back to EXECUTING so status reporting matches reality.
            if record.status == GoalStatus.WAITING_HUMAN:
                record.status = GoalStatus.EXECUTING
        # Decrement the per-tenant concurrent-goal counter for every terminal event.
        if etype in {"goal_complete", "goal_failed", "goal_cancelled"}:
            from app.tenancy.limits import decrement_concurrent_goals

            await decrement_concurrent_goals(tenant_id=record.tenant_id, redis=self._redis)
            # Release dedup key so future identical goals can be submitted
            try:
                from app.services.dedup import _default_deduplicator as _goal_dedup

                _goal_text = getattr(record, "goal_text", "") or ""
                if _goal_text:
                    await _goal_dedup.release(
                        record.tenant_id,
                        _goal_text,
                        scope=str(record.execution_context.get(_DEDUP_SCOPE_KEY, "") or ""),
                    )
            except Exception:
                pass
        # Usage metering (in-process runs; worker-run goals meter in the worker).
        # Dry runs execute nothing and are not metered.
        if not record.dry_run and etype in _METERED_EVENT_TYPES:
            await self._meter_usage(record, sanitized_event)
        # Publish ALL non-ephemeral events to a goal-specific Redis channel so that
        # replica B can receive events for goals executing on replica A (P1-2 fix).
        if not _is_ephemeral and self._redis is not None and tenant_ctx is not None:
            try:
                _channel = f"goal_events:{tenant_ctx.tenant_id}:{goal_id}"
                await self._redis.publish(_channel, json.dumps(sanitized_event))
            except Exception:
                pass  # Redis unavailable — in-process delivery still works
        # Publish ephemeral token_chunk events to a *separate* lightweight channel
        # so front-end SSE consumers can display live typing without polluting the
        # main event log.  Only published when Redis is available.
        if (
            sanitized_event.get("type") == "token_chunk"
            and self._redis is not None
            and tenant_ctx is not None
        ):
            try:
                _token_channel = f"goal_tokens:{tenant_ctx.tenant_id}:{goal_id}"
                await self._redis.publish(_token_channel, json.dumps(sanitized_event))
            except Exception:
                pass
        # Goal-chain triggers (Family B): ChainTriggerConsumer subscribes to
        # goal.completed / goal.failed, but nothing ever published them, so a
        # "when goal X completes, run Y" trigger could never fire.
        if etype == "goal_complete":
            await self._publish_chain_event(record, "goal.completed", tenant_ctx)
        elif etype == "goal_failed":
            await self._publish_chain_event(record, "goal.failed", tenant_ctx)
        # Also publish terminal events to the broader platform channel used by
        # other subscribers (notification service, billing hooks, etc.).
        if etype in {"goal_complete", "goal_failed"} and self._redis and tenant_ctx:
            with suppress(Exception):
                await self._redis.publish(
                    f"platform_events:{tenant_ctx.tenant_id}",
                    json.dumps(sanitized_event),
                )
        # Push event to every live subscriber queue, pruning dead ones on all
        # non-ephemeral events so they don't accumulate until goal completion.
        _dead: list[asyncio.Queue[dict[str, Any] | None]] = []
        for q in list(record.subscribers):
            try:
                q.put_nowait(sanitized_event)
            except asyncio.QueueFull:
                if not _is_ephemeral:
                    _dead.append(q)
            except Exception:
                _dead.append(q)
        for q in _dead:
            with suppress(ValueError):
                record.subscribers.remove(q)
        # If the goal has reached a terminal state, send the end-of-stream sentinel
        if record.status in {GoalStatus.COMPLETE, GoalStatus.FAILED, GoalStatus.CANCELLED}:
            for q in list(record.subscribers):
                with suppress(Exception):
                    q.put_nowait(_SENTINEL)

    def _usage_service(self) -> Any:
        """The app's UsageService (``app.state.usage_service``), or None."""
        aps: Any = self._app_state
        if aps is None:
            return None
        try:
            from starlette.applications import Starlette as _Starlette

            if isinstance(aps, _Starlette):
                aps = aps.state
        except Exception:
            pass
        return getattr(aps, "usage_service", None)

    async def _meter_usage(self, record: GoalRecord, event: dict[str, Any]) -> None:
        """Record usage for a tool call / the goal's terminal event (never raises)."""
        from app.services.usage_metering import (
            TERMINAL_EVENT_STATUS,
            TOOL_CALL_EVENT,
            meter_goal_completion,
            meter_tool_call,
        )

        usage = self._usage_service()
        if usage is None:
            return
        etype = str(event.get("type", ""))
        if etype == TOOL_CALL_EVENT:
            await meter_tool_call(
                usage, tenant_id=record.tenant_id, goal_id=record.goal_id, event=event
            )
        elif etype in TERMINAL_EVENT_STATUS and not record.usage_recorded:
            record.usage_recorded = True
            await meter_goal_completion(
                usage,
                tenant_id=record.tenant_id,
                goal_id=record.goal_id,
                status=TERMINAL_EVENT_STATUS[etype],
            )

    async def _publish_chain_event(
        self,
        record: GoalRecord,
        channel: str,
        tenant_ctx: TenantContext | None,
        *,
        score: float | None = None,
    ) -> None:
        """Publish a goal lifecycle event for ChainTriggerConsumer, once per record.

        ``completion_event_id`` is deterministic (goal + channel), so a replica that
        relays the same terminal event yields the same dispatcher idempotency key
        and the chained trigger fires once. ``trigger_chain_depth`` is carried from
        the goal's execution context (set when a chain trigger created the goal) so
        the consumer's MAX_CHAIN_DEPTH guard stops self-re-triggering loops.
        """
        if record.dry_run or self._redis is None or channel in record.chain_events_published:
            return
        record.chain_events_published.add(channel)
        from app.triggers.consumers.chain import build_chain_event

        plan = getattr(getattr(tenant_ctx, "plan", None), "value", None) or str(
            getattr(tenant_ctx, "plan", "") or "free"
        )
        payload = build_chain_event(
            channel=channel,
            tenant_id=record.tenant_id,
            goal_id=record.goal_id,
            agent_id=record.agent_id or "",
            status=record.status.value,
            tenant_plan=plan,
            trigger_chain_depth=int(record.execution_context.get("trigger_chain_depth", 0) or 0),
            score=score,
            source_trigger_id=str(record.execution_context.get("source_trigger_id", "") or ""),
        )
        try:
            await self._redis.publish(channel, payload)
        except Exception as exc:
            record.chain_events_published.discard(channel)
            _svc_logger.warning(
                "goal_chain_event_publish_failed goal_id=%s channel=%s: %s",
                record.goal_id,
                channel,
                exc,
            )

    def _record_terminal_goal_metrics(self, record: GoalRecord, status: str) -> None:
        if record.terminal_metrics_recorded:
            return
        record.terminal_metrics_recorded = True
        duration_seconds = _monotonic() - record.started_monotonic
        record_goal_duration(
            status=status,
            duration_seconds=duration_seconds,
            priority=record.priority,
        )
        # Track per-tenant durations so get_metrics can compute avg_latency_ms.
        self._goal_durations.setdefault(record.tenant_id, []).append(duration_seconds)
        self._record_strategy_evidence(record, status)

    def _record_strategy_evidence(self, record: GoalRecord, status: str) -> None:
        """Append certification evidence for the strategies this goal actually ran."""
        if record.dry_run or status not in ("completed", "failed"):
            return
        execution = record.execution_context.get("strategy_execution")
        if not isinstance(execution, dict):
            return
        patterns = [str(item) for item in execution.get("patterns") or ()]
        if not patterns:
            return
        from app.orchestration.strategy_evidence import StrategyEvidenceRecorder

        state: Any = self._app_state
        state = getattr(state, "state", state)
        recorder = getattr(state, "strategy_evidence", None) if state is not None else None
        if not isinstance(recorder, StrategyEvidenceRecorder):
            return
        runtime_path = str(record.execution_context.get("strategy_runtime_path") or "legacy")

        async def _record() -> None:
            await recorder.record_run(
                tenant_id=record.tenant_id,
                goal_id=record.goal_id,
                strategy_ids=patterns,
                succeeded=status == "completed",
                runtime_path=runtime_path,
            )

        coro = _record()
        try:
            self._track_db_task(coro)
        except RuntimeError:  # no running loop
            coro.close()

    async def _run_agent_loop_persistent(
        self,
        goal_id: str,
        goal_text: str,
        tenant_ctx: TenantContext,
        tool_context: Any = None,
        persistence_config: dict[str, Any] | None = None,
    ) -> None:
        """Run agent with persistence — keep retrying until goal achieved."""
        from app.agent.persistence import GoalPersistenceEngine, PersistenceConfig

        record = self._goals.get(goal_id)

        async def callback(event: dict[str, Any]) -> None:
            await self._dispatch_event(goal_id, event, tenant_ctx=tenant_ctx)

        cfg_data = persistence_config or {}
        if record is not None and record.runtime_profile is not None:
            admitted = PersistenceConfig.from_runtime_profile(record.runtime_profile)
            config = PersistenceConfig(
                max_attempts=min(
                    int(cfg_data.get("max_attempts", admitted.max_attempts)),
                    admitted.max_attempts,
                ),
                iterations_per_attempt=min(
                    int(cfg_data.get("iterations_per_attempt", admitted.iterations_per_attempt)),
                    admitted.iterations_per_attempt,
                ),
                base_backoff_seconds=float(
                    cfg_data.get("base_backoff_seconds", admitted.base_backoff_seconds)
                ),
                max_backoff_seconds=float(
                    cfg_data.get("max_backoff_seconds", admitted.max_backoff_seconds)
                ),
                strategy_switch_after=int(
                    cfg_data.get("strategy_switch_after", admitted.strategy_switch_after)
                ),
                escalate_after_failures=int(
                    cfg_data.get("escalate_after_failures", admitted.escalate_after_failures)
                ),
                total_timeout_seconds=min(
                    float(cfg_data.get("total_timeout_seconds", admitted.total_timeout_seconds)),
                    admitted.total_timeout_seconds,
                ),
                decompose_on_failure=bool(
                    cfg_data.get("decompose_on_failure", admitted.decompose_on_failure)
                ),
                strategy_id=admitted.strategy_id,
                strategy_version=admitted.strategy_version,
                profile_id=admitted.profile_id,
                profile_version=admitted.profile_version,
            )
        else:
            config = PersistenceConfig(
                max_attempts=cfg_data.get("max_attempts", 10),
                iterations_per_attempt=cfg_data.get("iterations_per_attempt", 15),
                base_backoff_seconds=cfg_data.get("base_backoff_seconds", 30.0),
                max_backoff_seconds=cfg_data.get("max_backoff_seconds", 600.0),
                strategy_switch_after=cfg_data.get("strategy_switch_after", 2),
                escalate_after_failures=cfg_data.get("escalate_after_failures", 6),
                total_timeout_seconds=cfg_data.get("total_timeout_seconds", 0.0),
                decompose_on_failure=cfg_data.get("decompose_on_failure", True),
            )

        engine = GoalPersistenceEngine(
            config=config,
            # GoalService keeps its session factory on ``_db``; the old
            # ``_db_session_factory`` lookup was always None, so attempts were
            # never persisted.
            db=getattr(self, "_db", None),
            # Operator controls (abort / skip-strategy / guidance) live in Redis.
            redis=getattr(self, "_redis", None),
        )

        _persist_llm_config = await self._resolve_tenant_llm_config(tenant_ctx)
        if _persist_llm_config:
            # Validate the tenant's BYOK once, up front: an unusable config fails
            # the goal instead of being retried by the persistence engine.
            from app.providers.tenant_provider import TenantProviderError, build_tenant_provider

            try:
                build_tenant_provider(_persist_llm_config, tenant_id=tenant_ctx.tenant_id)
            except TenantProviderError as _byok_exc:
                await self._dispatch_event(
                    goal_id,
                    {
                        "type": "goal_failed",
                        "reason": str(_byok_exc),
                        "failure_reason": "tenant_llm_provider_unavailable",
                    },
                    tenant_ctx=tenant_ctx,
                )
                return

        def agent_factory() -> Any:
            # Set agent knowledge collection IDs for graph RAG
            _persist_record = self._goals.get(goal_id)
            _persist_profile_kwargs = (
                {"runtime_profile": _persist_record.runtime_profile}
                if _persist_record is not None and _persist_record.runtime_profile is not None
                else {}
            )
            loop = self._make_agent_loop_for_tenant(
                tenant_ctx,
                self._app_state,
                agent_id=_persist_record.agent_id if _persist_record is not None else None,
                execution_context=(
                    _persist_record.execution_context if _persist_record is not None else None
                ),
                **_persist_profile_kwargs,
                **_tenant_llm_kwargs(_persist_llm_config),
            )
            if _persist_record is not None:
                loop._observed_runtime_profile = _persist_record.observed_runtime_profile
            self._persist_strategy_execution(goal_id, tenant_ctx.tenant_id, _persist_record)
            _persist_collection_ids: list[str] = []
            if _persist_record is not None and _persist_record.agent_id:
                _persist_agent_store = self._get_agent_store()
                if _persist_agent_store is not None:
                    _persist_agent = _persist_agent_store.get(
                        _persist_record.agent_id, tenant_ctx=tenant_ctx
                    )
                    if isinstance(_persist_agent, dict):
                        _persist_collection_ids = list(
                            _persist_agent.get("allowed_collection_ids", [])
                        )
            loop._agent_collection_ids = _persist_collection_ids
            # SAFE-2: populate guardrail allowlist in persistent loop path too
            _gc = getattr(loop, "_guardrail_checker", None)
            if _gc is not None and tool_context is not None:
                _populate_guardrail_allowlist(_gc, tool_context)
            if tool_context is not None:
                # SAFE-2 (P0-13): register discovered tool names for hallucination guard.
                self._register_tools_from_context(loop, tool_context)
                # Seed initial_context into the agent's run via a wrapper
                _tc = tool_context

                class _WrappedAgent:
                    async def run(
                        self: _WrappedAgent,
                        goal: str,
                        tenant_ctx: TenantContext,
                        event_callback: Any = None,
                        **attempt_kwargs: Any,
                    ) -> Any:
                        initial_ctx: dict[str, Any] = {
                            "tool_prompt": _tc.to_prompt_block(),
                            "tool_context": _tc,
                        }
                        from app.agent.persistence import _attempt_kwargs

                        _fwd = _attempt_kwargs(
                            loop,
                            str(attempt_kwargs.get("goal_id") or ""),
                            int(attempt_kwargs.get("attempt") or 1),
                        )
                        return await loop.run(
                            goal=goal,
                            tenant_ctx=tenant_ctx,
                            initial_context=initial_ctx,
                            event_callback=event_callback,
                            **_fwd,
                        )

                return _WrappedAgent()
            return loop

        # The last attempt's final AgentState, for outcome learning.
        _last_attempt: dict[str, Any] = {}

        def capturing_agent_factory() -> Any:
            from app.agent.persistence import _attempt_kwargs

            inner = agent_factory()

            class _CapturingAgent:
                async def run(
                    self: _CapturingAgent,
                    goal: str,
                    tenant_ctx: TenantContext,
                    event_callback: Any = None,
                    **attempt_kwargs: Any,
                ) -> Any:
                    state = await inner.run(
                        goal=goal,
                        tenant_ctx=tenant_ctx,
                        event_callback=event_callback,
                        **_attempt_kwargs(
                            inner,
                            str(attempt_kwargs.get("goal_id") or ""),
                            int(attempt_kwargs.get("attempt") or 1),
                        ),
                    )
                    _last_attempt["state"] = state
                    return state

            return _CapturingAgent()

        try:
            success, attempts = await engine.run(
                goal=goal_text,
                agent_factory=capturing_agent_factory,
                tenant_ctx=tenant_ctx,
                event_callback=callback,
                goal_id=goal_id,
            )
            _final_attempt_state = _last_attempt.get("state")
            if not success and (
                _final_attempt_state is None
                or getattr(_final_attempt_state, "status", None) != GoalStatus.FAILED
            ):
                _exhausted = _failed_goal_state(
                    goal_id,
                    goal_text,
                    tenant_ctx,
                    f"Goal could not be achieved after {len(attempts)} attempts",
                )
                if _final_attempt_state is not None:
                    _exhausted.context = dict(getattr(_final_attempt_state, "context", {}) or {})
                    _exhausted.steps = list(getattr(_final_attempt_state, "steps", []) or [])
                _final_attempt_state = _exhausted
            if not success:
                # All attempts exhausted
                reason = (
                    f"Goal could not be achieved after {len(attempts)} attempts. "
                    f"Last strategy: {attempts[-1].strategy if attempts else 'none'}. "
                    f"Total cost: ${engine.total_cost_usd:.4f}"
                )
                await self._dispatch_event(
                    goal_id,
                    {"type": "goal_failed", "reason": reason, "attempts": len(attempts)},
                    tenant_ctx=tenant_ctx,
                )
            # After the terminal event: learning never delays completion.
            if _final_attempt_state is not None:
                await self._learn_from_goal_outcome(goal_id, tenant_ctx, _final_attempt_state)
        except asyncio.CancelledError:
            if record is not None and record.status != GoalStatus.CANCELLED:
                record.status = GoalStatus.CANCELLED
                await self._dispatch_event(
                    goal_id, {"type": "goal_cancelled"}, tenant_ctx=tenant_ctx
                )
            raise
        except Exception as exc:
            await self._dispatch_event(
                goal_id, {"type": "goal_failed", "reason": str(exc)}, tenant_ctx=tenant_ctx
            )

    async def _run_agent_loop(
        self,
        goal_id: str,
        goal_text: str,
        tenant_ctx: TenantContext,
        tool_context: ToolContext | None = None,
    ) -> None:
        """Background task: run the agent loop for a submitted goal.

        When ISOLATED_AGENT_EXECUTION=true the execution is routed through the
        ExecutionEnvironmentScheduler instead of running in-process.  All core
        agent behaviour (AgentGraph, guardrails, governance,
        RAG, memory, HITL, etc.) is unchanged — only the *where* changes.
        """
        with _tracer.start_as_current_span("goal.execute") as span:
            span.set_attribute("goal_id", goal_id)

            # ── Isolation routing ────────────────────────────────────────────
            # Flag-off path adds zero overhead.  On flag-check failure, we
            # check ISOLATED_EXECUTION_REQUIRED to decide whether to fail
            # closed (required=True) or fall through (required=False).
            try:
                from app.core.runtime_flags import get_runtime_flags as _get_flags

                _flags = _get_flags()
                if _flags.isolated_agent_execution:
                    await self._run_agent_loop_isolated(
                        goal_id=goal_id,
                        goal_text=goal_text,
                        tenant_ctx=tenant_ctx,
                        tool_context=tool_context,
                    )
                    return
            except Exception as _iso_import_exc:
                _svc_logger.warning(
                    "isolation_flag_check_failed goal_id=%s error=%s",
                    goal_id,
                    str(_iso_import_exc)[:120],
                )
                # Fail-closed when isolation is required
                try:
                    from app.core.runtime_flags import get_runtime_flags as _gf2

                    if _gf2().isolated_execution_required:
                        record = self._goals.get(goal_id)
                        if record is not None:
                            record.status = GoalStatus.FAILED
                            record.error_message = str(_iso_import_exc)
                        await self._dispatch_event(
                            goal_id,
                            {
                                "type": "goal_failed",
                                "reason": str(_iso_import_exc),
                                "_isolation_error": True,
                                "failure_reason": "internal_error",
                            },
                            tenant_ctx=tenant_ctx,
                        )
                        return
                except Exception:
                    pass
            # ── End isolation routing ────────────────────────────────────────

            record = self._goals.get(goal_id)
            _profile_kwargs = (
                {"runtime_profile": record.runtime_profile}
                if record is not None and record.runtime_profile is not None
                else {}
            )
            from app.providers.tenant_provider import TenantProviderError

            try:
                loop = self._make_agent_loop_for_tenant(
                    tenant_ctx,
                    self._app_state,
                    agent_id=record.agent_id if record is not None else None,
                    execution_context=record.execution_context if record is not None else None,
                    **_profile_kwargs,
                    **_tenant_llm_kwargs(await self._resolve_tenant_llm_config(tenant_ctx)),
                )
                if record is not None:
                    loop._observed_runtime_profile = record.observed_runtime_profile
            except TenantProviderError as _byok_exc:
                # BYOK configured but unusable: an explicit goal failure, not an
                # escaped exception (goal stuck "executing") or platform spend.
                if record is not None:
                    record.error_message = str(_byok_exc)
                await self._dispatch_event(
                    goal_id,
                    {
                        "type": "goal_failed",
                        "reason": str(_byok_exc),
                        "failure_reason": "tenant_llm_provider_unavailable",
                    },
                    tenant_ctx=tenant_ctx,
                )
                return
            self._persist_strategy_execution(goal_id, tenant_ctx.tenant_id, record)
            loop._pause_gate = self._make_pause_gate(goal_id, tenant_ctx)
            # Set agent knowledge collection IDs for graph RAG
            _agent_collection_ids: list[str] = []
            if record is not None and record.agent_id:
                _agent_store_ref = self._get_agent_store()
                if _agent_store_ref is not None:
                    _agent_rec = _agent_store_ref.get(record.agent_id, tenant_ctx=tenant_ctx)
                    if isinstance(_agent_rec, dict):
                        _agent_collection_ids = list(_agent_rec.get("allowed_collection_ids", []))
            loop._agent_collection_ids = _agent_collection_ids
            # Detect FakeProvider so get_goal() can surface a warning to callers
            if record is not None and (
                getattr(loop, "_simulated_provider", False) is True
                or type(getattr(loop, "_planner", None)).__name__ == "FakeProvider"
            ):
                record.execution_context["provider_warning"] = (
                    "No real LLM provider configured. Results are simulated."
                )

            # Guarantee tool_context is always available: fall back to building
            # a basic context (RPA tools) when the caller didn't pass one.
            if tool_context is None:
                try:
                    tool_context = await self._build_tool_context(
                        agent_id=None, tenant_ctx=tenant_ctx, goal=goal_text
                    )
                except Exception as _tc_exc:
                    _svc_logger.warning("tool_context_build_failed", error=str(_tc_exc))

            # SAFE-2: Populate the guardrail checker's tool allowlist from
            # discovered tools so hallucinated tool names are rejected.
            _guardrail_checker = getattr(loop, "_guardrail_checker", None)
            if _guardrail_checker is not None and tool_context is not None:
                _populate_guardrail_allowlist(_guardrail_checker, tool_context)

            initial_context: dict[str, Any] = {}
            if tool_context is not None:
                initial_context["tool_prompt"] = tool_context.to_prompt_block()
                initial_context["tool_context"] = tool_context
            # Inject agent system prompt when loaded from agent record (H-4)
            _agent_system_prompt = getattr(loop, "_agent_system_prompt", "")
            if _agent_system_prompt:
                initial_context["system_prompt"] = _agent_system_prompt
            from app.agent.supervisor import SUBGOAL_MARKER

            if record is not None and record.execution_context.get(SUBGOAL_MARKER):
                initial_context[SUBGOAL_MARKER] = record.execution_context[SUBGOAL_MARKER]
            # Pre-execution pattern results from the API (debate consensus,
            # supervisor fallback) were written to execution_context and never
            # reached the graph, so the planner could not use them.
            if record is not None:
                initial_context.update(graph_context_from_execution_context(record.execution_context))
            # N2: Load reflexion lessons to feed back into planning (close the feedback loop).
            # Lessons written by ReflexionWirer on failure are recalled here for the next goal.
            try:
                from app.agent.reflexion_wirer import get_reflexion_wirer

                _rw_for_ctx = get_reflexion_wirer()
                _lessons_for_ctx = _rw_for_ctx._store.recall(
                    tenant_id=tenant_ctx.tenant_id, limit=5
                )
                if _lessons_for_ctx:
                    initial_context["_reflexion_lessons"] = _lessons_for_ctx
            except Exception:
                pass

            async def callback(event: dict[str, Any]) -> None:
                await self._dispatch_event(goal_id, event, tenant_ctx=tenant_ctx)

            # Wall-clock timeout for the goal as a whole. Without this, a goal
            # running in-process (task_queue=None — no Redis/Celery configured,
            # or tests) has no upper bound: a stuck/slow tool call (hung MCP
            # server, RPA browser wedge, etc.) would let the goal — and the
            # bulkhead/concurrency slot it holds — run forever. The Celery task
            # path (app/scaling/tasks.py) already enforces this same per-plan
            # goal_timeout_seconds via asyncio.wait_for; mirror it here so the
            # in-process path gets the identical hard stop.
            from app.tenancy.context import PLAN_LIMITS as _PLAN_LIMITS

            _goal_timeout_s = getattr(
                _PLAN_LIMITS.get(tenant_ctx.plan), "goal_timeout_seconds", 3600
            )

            try:
                final_state = await asyncio.wait_for(
                    loop.run(
                        goal=goal_text,
                        tenant_ctx=tenant_ctx,
                        initial_context=initial_context or None,
                        event_callback=callback,
                        goal_id=goal_id,
                    ),
                    timeout=float(_goal_timeout_s),
                )
                if getattr(final_state, "status", None) == GoalStatus.WAITING_HUMAN:
                    await self._suspend_for_approval(goal_id, tenant_ctx)
                else:
                    # Terminal: learn from the outcome (bounded; never raises).
                    await self._learn_from_goal_outcome(goal_id, tenant_ctx, final_state)
            except TimeoutError:
                if record is not None:
                    record.status = GoalStatus.FAILED
                    record.error_message = f"Goal timed out after {_goal_timeout_s}s"
                    timeout_event: dict[str, Any] = {
                        "type": "goal_failed",
                        "reason": f"timeout after {_goal_timeout_s}s",
                    }
                    await self._dispatch_event(goal_id, timeout_event, tenant_ctx=tenant_ctx)
                await self._learn_from_goal_outcome(
                    goal_id,
                    tenant_ctx,
                    _failed_goal_state(
                        goal_id, goal_text, tenant_ctx, f"timeout after {_goal_timeout_s}s"
                    ),
                )
            except asyncio.CancelledError:
                # A terminal status was already set by whoever cancelled the task
                # (cancel_goal / a HITL rejection) along with its terminal event.
                if record is not None and record.status not in _TERMINAL_STATUSES:
                    cancelled_event: dict[str, Any] = {"type": "goal_cancelled"}
                    record.status = GoalStatus.CANCELLED
                    await self._dispatch_event(goal_id, cancelled_event, tenant_ctx=tenant_ctx)
                raise
            except GoalCancelledError:
                # Cancelled from ANOTHER replica (Redis flag, seen at a step
                # boundary). That replica already persisted CANCELLED, emitted
                # goal_cancelled (fanned out here over Redis) and released the
                # slot — only settle local state and close local streams.
                if record is not None:
                    record.status = GoalStatus.CANCELLED
                    record.completed_at = record.completed_at or datetime.now(UTC).isoformat()
                    for q in list(record.subscribers):
                        with suppress(Exception):
                            q.put_nowait(_SENTINEL)
                _GOAL_PAUSE_EVENTS.pop(goal_id, None)
            except Exception as exc:
                if record is not None:
                    failed_event: dict[str, Any] = {"type": "goal_failed", "reason": str(exc)}
                    await self._dispatch_event(goal_id, failed_event, tenant_ctx=tenant_ctx)
                await self._learn_from_goal_outcome(
                    goal_id,
                    tenant_ctx,
                    _failed_goal_state(goal_id, goal_text, tenant_ctx, str(exc)),
                )

    async def _learn_from_goal_outcome(
        self, goal_id: str, tenant_ctx: TenantContext, final_state: Any
    ) -> None:
        """Reflexion learning for a terminal goal (API in-process path).

        Bounded and never raises (see app.memory.goal_learning). Fails closed on
        an unknown goal record: without it the dry-run flag cannot be checked,
        so nothing is written.
        """
        record = self._goals.get(goal_id)
        if record is None:
            _svc_logger.info("goal_learning_skipped_unknown_goal", goal_id=goal_id)
            return
        from starlette.applications import Starlette

        from app.memory.goal_learning import learn_from_goal_outcome

        # self._app_state is the FastAPI app in production (services live on
        # app.state) and a plain namespace in tests.
        state_obj: Any = (
            self._app_state.state
            if isinstance(self._app_state, Starlette)
            else self._app_state
        )
        await learn_from_goal_outcome(
            getattr(state_obj, "reflexion_service", None),
            final_state,
            tenant_id=tenant_ctx.tenant_id,
            goal_id=goal_id,
            dry_run=record.dry_run,
            agent_id=record.agent_id,
        )

    async def _run_agent_loop_isolated(
        self,
        goal_id: str,
        goal_text: str,
        tenant_ctx: TenantContext,
        tool_context: ToolContext | None = None,
    ) -> None:
        """Route execution through the isolated execution environment.

        This method is called only when ISOLATED_AGENT_EXECUTION=true.
        It builds an ExecutionEnvelope from the current goal context and
        dispatches it to the ExecutionEnvironmentScheduler.  All control-plane
        semantics (status tracking, SSE events, audit, cost) are preserved —
        only the execution happens in the isolated plane.

        Fail-closed: if the scheduler raises RunnerUnavailableError, the goal
        is marked failed and a structured error event is emitted.  There is
        no silent fallback to in-process execution.
        """
        import contextlib

        from app.core.runtime_flags import get_runtime_flags as _get_flags
        from app.execution_environment.envelope import build_envelope
        from app.execution_environment.models import RunnerType
        from app.execution_environment.scheduler import (
            ExecutionEnvironmentScheduler,
            RunnerUnavailableError,
        )

        record = self._goals.get(goal_id)
        flags = _get_flags()

        # Select runner type from flags
        if flags.isolated_execution_kubernetes_runner:
            runner_type = RunnerType.KUBERNETES
        elif flags.isolated_execution_local_runner:
            runner_type = RunnerType.LOCAL
        else:
            runner_type = RunnerType.FAKE

        # Serialise tool_context for the envelope (no raw MCP credentials)
        tc_dict: dict[str, Any] = {}
        if tool_context is not None:
            with contextlib.suppress(Exception):
                tc_dict = {
                    "tool_prompt": tool_context.to_prompt_block(),
                    "tools": [
                        {"name": t.name, "description": getattr(t, "description", "")}
                        for t in getattr(tool_context, "tools", [])
                    ],
                }

        # Get agent_config for the envelope
        agent_config: dict[str, Any] = {}
        if record is not None and record.agent_id:
            _store = self._get_agent_store()
            if _store is not None:
                try:
                    cfg = _store.get(record.agent_id, tenant_ctx=tenant_ctx)
                    if isinstance(cfg, dict):
                        agent_config = cfg
                except Exception:
                    pass

        # Resolve the tenant's scoped (BYOK) LLM key. The store persists only the
        # vault ciphertext (``encrypted_key``); this read ``api_key`` — a field it
        # never writes — so the key was always "" (errors swallowed too) and the
        # isolated runner fell back to the platform key. A tenant WITH a BYOK
        # config whose key cannot be read/decrypted now fails the goal instead.
        scoped_llm_key = ""
        try:
            from app.services.llm_config_store import get_llm_config_store

            _config_store = get_llm_config_store()
            if _config_store is not None:
                _cfg = await _config_store.get_config(tenant_ctx.tenant_id, strict=True) or {}
                _enc = str(_cfg.get("encrypted_key") or "")
                if _enc:
                    from app.providers.vault import get_vault

                    scoped_llm_key = str(get_vault().decrypt(_enc) or "")
                    if not scoped_llm_key:
                        raise ValueError("tenant LLM API key decrypted to an empty value")
                elif _cfg:
                    scoped_llm_key = str(_cfg.get("api_key") or "")
        except Exception as _scoped_exc:
            _svc_logger.warning(
                "isolated_scoped_llm_key_unavailable", error=type(_scoped_exc).__name__
            )
            if record is not None:
                record.status = GoalStatus.FAILED
                record.error_message = "tenant LLM key unavailable for isolated execution"
            await self._dispatch_event(
                goal_id,
                {
                    "type": "goal_failed",
                    "reason": "tenant LLM configuration could not be read or decrypted; "
                    "the goal was not run on the platform key in its place",
                    "failure_reason": "tenant_llm_provider_unavailable",
                },
                tenant_ctx=tenant_ctx,
            )
            return

        # Collect runtime_profile and feature_flags snapshots for the envelope
        _runtime_profile: dict[str, Any] = {}
        _feature_flags: dict[str, bool] = {}
        _hitl_state: dict[str, Any] = {}
        if record is not None:
            _runtime_profile = record.execution_context.get("runtime_profile", {})
            _hitl_state = record.execution_context.get("hitl_state", {})
        try:
            _rf = flags
            _feature_flags = {
                "isolated_agent_execution": _rf.isolated_agent_execution,
                "isolated_execution_required": _rf.isolated_execution_required,
                "isolated_execution_local_runner": _rf.isolated_execution_local_runner,
                "isolated_execution_kubernetes_runner": _rf.isolated_execution_kubernetes_runner,
                "dynamic_orchestration": _rf.dynamic_orchestration,
                "agentic_rag": _rf.agentic_rag,
            }
        except Exception:
            pass

        envelope = build_envelope(
            tenant_id=tenant_ctx.tenant_id,
            goal_id=goal_id,
            goal_text=goal_text,
            agent_id=(record.agent_id or "") if record is not None else "",
            execution_context=(record.execution_context or {}) if record is not None else {},
            agent_config=agent_config,
            tool_context=tc_dict,
            runtime_profile=_runtime_profile,
            hitl_state=_hitl_state,
            feature_flags=_feature_flags,
            dry_run=(record.dry_run if record is not None else False),
            workflow_mode=(record.workflow_mode if record is not None else "single_agent"),
            priority=(record.priority if record is not None else "normal"),
            runner_type=runner_type,
            scoped_llm_api_key=scoped_llm_key,
        )

        # Resolve scheduler from app.state (registered by create_app) or
        # build a fresh one if not available (e.g. in lightweight test contexts)
        scheduler: ExecutionEnvironmentScheduler
        _scheduler_candidate = getattr(self._app_state, "execution_scheduler", None)
        if _scheduler_candidate is not None:
            scheduler = _scheduler_candidate
        else:
            scheduler = ExecutionEnvironmentScheduler.from_flags(
                isolated_execution_local_runner=flags.isolated_execution_local_runner,
                isolated_execution_kubernetes_runner=flags.isolated_execution_kubernetes_runner,
            )

        async def callback(event: dict[str, Any]) -> None:
            await self._dispatch_event(goal_id, event, tenant_ctx=tenant_ctx)

        try:
            result = await scheduler.schedule(envelope, event_callback=callback)
            # Mirror the execution result. The runner forwards the graph's own
            # goal_complete / goal_failed when the loop ran, but several paths
            # (policy rejection, pre-run failures) return a failed result with no
            # terminal event — the goal was marked FAILED in memory only and SSE
            # streams never ended. WAITING_HUMAN is a suspension, not an end.
            _terminal_types = {"goal_complete", "goal_failed", "goal_cancelled"}
            _already_terminal = record is not None and any(
                e.get("type") in _terminal_types for e in record.events
            )
            if str(result.status) == GoalStatus.WAITING_HUMAN.value:
                await self._suspend_for_approval(goal_id, tenant_ctx)
            elif result.success:
                if record is not None:
                    record.status = GoalStatus.COMPLETE
                if not _already_terminal:
                    await self._dispatch_event(
                        goal_id, {"type": "goal_complete"}, tenant_ctx=tenant_ctx
                    )
            else:
                if record is not None:
                    record.status = GoalStatus.FAILED
                    record.error_message = result.error_message
                if not _already_terminal:
                    await self._dispatch_event(
                        goal_id,
                        {
                            "type": "goal_failed",
                            "reason": result.error_message
                            or f"isolated execution ended with status {result.status}",
                        },
                        tenant_ctx=tenant_ctx,
                    )
        except RunnerUnavailableError as exc:
            _svc_logger.error(
                "isolated_runner_unavailable goal_id=%s reason=%s",
                goal_id,
                str(exc)[:200],
            )
            if record is not None:
                record.status = GoalStatus.FAILED
                record.error_message = str(exc)
            await self._dispatch_event(
                goal_id,
                {
                    "type": "goal_failed",
                    "reason": str(exc),
                    "failure_reason": exc.failure_reason.value,
                    "_isolation_error": True,
                },
                tenant_ctx=tenant_ctx,
            )
        except Exception as exc:
            _svc_logger.error(
                "isolated_execution_unexpected_error goal_id=%s error=%s",
                goal_id,
                str(exc)[:200],
            )
            if record is not None:
                record.status = GoalStatus.FAILED
                record.error_message = str(exc)
            await self._dispatch_event(
                goal_id,
                {"type": "goal_failed", "reason": str(exc), "_isolation_error": True},
                tenant_ctx=tenant_ctx,
            )

    async def _run_workflow(
        self,
        goal_id: str,
        goal_text: str,
        tenant_ctx: TenantContext,
        tool_context: ToolContext | None = None,
    ) -> None:
        """Background task: run the minimal multi-agent workflow path."""
        record = self._goals.get(goal_id)
        if record is not None:
            record.status = GoalStatus.EXECUTING

        async def callback(event: dict[str, Any]) -> None:
            await self._dispatch_event(goal_id, event, tenant_ctx=tenant_ctx)

        try:
            await self._dispatch_event(
                goal_id, {"type": "goal_started", "goal": goal_text}, tenant_ctx=tenant_ctx
            )
            plan = build_static_workflow(goal_text)
            app_state = getattr(self._app_state, "state", self._app_state)
            from app.agent.tool_gate import gate_from_app_state

            executor = WorkflowExecutor(
                mcp_client=self._get_mcp_client(),
                retrieval_gateway=getattr(app_state, "retrieval_gateway", None),
                # Same governance as the AgentGraph executor (was: none at all).
                tool_gate=gate_from_app_state(
                    app_state, agent_id=record.agent_id if record is not None else None
                ),
                goal_id=goal_id,
            )
            wf_result = await executor.execute(
                plan,
                tenant_ctx,
                tool_context=tool_context,
                event_callback=callback,
                goal=goal_text,
            )
            # Map the executor's real outcome to the terminal event. It returns
            # {"status": "failed", "reason": ...} when a step fails (it does not
            # raise), and goal_complete used to be emitted regardless.
            wf_status = str((wf_result or {}).get("status", "complete"))
            if wf_status == "complete":
                await self._dispatch_event(
                    goal_id, {"type": "goal_complete"}, tenant_ctx=tenant_ctx
                )
            else:
                reason = str(
                    (wf_result or {}).get("reason")
                    or (wf_result or {}).get("error")
                    or f"workflow ended with status {wf_status}"
                )
                if record is not None:
                    record.status = GoalStatus.FAILED
                    record.error_message = reason
                await self._dispatch_event(
                    goal_id, {"type": "goal_failed", "reason": reason}, tenant_ctx=tenant_ctx
                )
        except asyncio.CancelledError:
            if record is not None and record.status != GoalStatus.CANCELLED:
                record.status = GoalStatus.CANCELLED
                await self._dispatch_event(
                    goal_id, {"type": "goal_cancelled"}, tenant_ctx=tenant_ctx
                )
            raise
        except Exception as exc:
            await self._dispatch_event(
                goal_id, {"type": "goal_failed", "reason": str(exc)}, tenant_ctx=tenant_ctx
            )

    # ── public API ────────────────────────────────────────────────────────────

    async def create_goal(
        self,
        *,
        tenant_ctx: TenantContext,
        goal_text: str,
        agent_id: str | None = None,
        idempotency_key: str | None = None,
        priority: str = "normal",
        trigger_chain_depth: int = 0,
        source_trigger_id: str = "",
    ) -> dict[str, Any]:
        """Trigger-facing adapter over :meth:`submit_goal` (WT-1 / P0-5).

        The TriggerDispatcher codes to this signature. Delegating to
        ``submit_goal`` preserves the single execution entrypoint that owns
        dedup, daily-limit, and concurrency enforcement rather than
        duplicating that governance in the dispatcher. The trigger's
        idempotency key is parked in ``execution_context`` for traceability.
        """
        return await self.submit_goal(
            goal=goal_text,
            priority=priority,
            dry_run=False,
            tenant_ctx=tenant_ctx,
            agent_id=agent_id,
            execution_context={
                "source": "trigger",
                "trigger_idempotency_key": idempotency_key,
                # Chain depth of the event that created this goal; re-published
                # on its own completion so chains are bounded (MAX_CHAIN_DEPTH).
                "trigger_chain_depth": int(trigger_chain_depth or 0),
                # The goal-event trigger that created this goal; re-published on
                # its lifecycle event so that trigger never re-fires on it.
                **({"source_trigger_id": source_trigger_id} if source_trigger_id else {}),
            },
        )

    async def submit_goal(
        self,
        goal: str,
        priority: str,
        dry_run: bool,
        tenant_ctx: TenantContext,
        agent_id: str | None = None,
        workflow_mode: str = "single_agent",
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a goal record and (unless *dry_run*) launch it as a background task."""
        with _tracer.start_as_current_span("goal.submit") as span:
            span.set_attribute("tenant_id", tenant_ctx.tenant_id)
            span.set_attribute("goal", goal[:100])
            self._validate_agent_id(agent_id, tenant_ctx)

            # Enforce daily goal limit per plan tier (Redis-backed for multi-process safety)
            await self._check_daily_goal_limit_redis(tenant_ctx)

            # Budget pre-flight: reject up-front when the tenant has exhausted its
            # daily cost budget, so an over-budget tenant is blocked with a budget
            # reason instead of being accepted and then having every step silently
            # skipped ("Step skipped: budget exceeded.") mid-run.
            await self._check_budget_preflight(tenant_ctx)

            # Readiness pre-flight (READINESS_GATE): a goal whose required dependencies
            # are down is refused before any slot, record or queue entry is taken.
            if not dry_run:
                # Emergency stop: nothing new starts while a tenant/org stop is
                # active; an unreadable stop state refuses (503), never waves through.
                await self._emergency_stop_preflight(tenant_ctx, execution_context)
                await self._readiness_preflight(tenant_ctx)

            # ── Goal-level deduplication ────────────────────────────────────────
            # If an identical goal is already in-flight for this tenant, return
            # the existing goal_id rather than spawning a duplicate Celery task.
            # "Identical" means same text AND same agent / dry-run / workflow /
            # permissions / execution context (autonomy, profile, ...): text
            # alone attached real submissions to in-flight dry runs or to other
            # agents' runs. Fingerprinted before anything below mutates the
            # caller's execution_context.
            from app.services.dedup import goal_dedup_scope

            _dedup_scope = goal_dedup_scope(
                agent_id=agent_id,
                dry_run=dry_run,
                workflow_mode=workflow_mode,
                priority=priority,
                execution_context=execution_context,
                roles=getattr(tenant_ctx, "roles", ()) or (),
                scopes=getattr(tenant_ctx, "scopes", ()) or (),
            )
            try:
                from app.services.dedup import _default_deduplicator as _goal_dedup

                _dedup_redis = getattr(self, "_redis", None)
                if _dedup_redis is not None and not hasattr(_goal_dedup, "_redis_wired"):
                    _goal_dedup._redis = _dedup_redis
                    _goal_dedup._redis_wired = True  # type: ignore[attr-defined]
                from app.agent.supervisor import SUBGOAL_MARKER

                # A supervisor's sub-goal is a distinct unit of work even when its
                # text matches an in-flight goal — typically its own parent, which
                # dedup returned, so the parent waited on itself forever.
                _is_subgoal = bool((execution_context or {}).get(SUBGOAL_MARKER))
                _existing_id = (
                    None
                    if _is_subgoal
                    else await _goal_dedup.get_existing(
                        tenant_ctx.tenant_id, goal, scope=_dedup_scope
                    )
                )
                if _existing_id:
                    return {
                        "goal_id": _existing_id,
                        "status": "running",
                        "deduplicated": True,
                        "message": "Identical goal already in progress",
                    }
            except Exception as _dd_exc:
                _svc_logger.debug("goal_dedup_skipped", error=str(_dd_exc)[:60])

            # Take a concurrency slot only for a goal that will actually run. It
            # used to be taken BEFORE the dedup check, and the dedup early-return
            # never released it: every duplicate submission leaked a slot until
            # the tenant was locked out with 429s.
            # Check and atomically increment the concurrent-goal counter.
            # Raises PlanLimitExceededError (HTTP 429) when the tenant is at limit.
            from app.tenancy.limits import check_and_increment_concurrent_goals

            await check_and_increment_concurrent_goals(
                tenant_ctx=tenant_ctx,
                redis=getattr(self, "_redis", None),
            )

            goal_id = uuid.uuid4().hex

            # Register goal_id for deduplication (allow others to find it)
            try:
                from app.services.dedup import _default_deduplicator as _goal_dedup

                await _goal_dedup.register(
                    tenant_ctx.tenant_id, goal, goal_id, scope=_dedup_scope
                )
            except Exception:
                pass

            # Auto-route to best agent when agent_id not specified
            if agent_id is None and self._app_state is not None:
                agent_store = self._get_agent_store()
                if agent_store is not None:
                    # H-6: Use pre-wired router from app.state (has DB history scoring)
                    agent_router = getattr(self._app_state, "agent_router", None)
                    try:
                        if agent_router is not None:
                            decision = await agent_router.route(goal=goal, tenant_ctx=tenant_ctx)
                        else:
                            # Fallback: create fresh router (no DB history scoring)
                            from app.agent.router import AgentRouter

                            router = AgentRouter(agent_store=agent_store)
                            decision = await router.route(goal, tenant_ctx)
                        if decision.agent_id and decision.confidence >= 0.3:
                            agent_id = decision.agent_id
                            _svc_logger.info(
                                "auto_routed_goal",
                                goal_id=goal_id,
                                agent_id=agent_id,
                                confidence=decision.confidence,
                            )
                        # Phase 2: Multi-agent goal spawning — parallel independent executions
                        if decision.mode == "multi_agent" and decision.candidate_agents:
                            _ma_tasks = []
                            for _cand in decision.candidate_agents[:3]:
                                _cand_agent_id = _cand.get("agent_id")
                                if not _cand_agent_id:
                                    continue
                                _ma_tasks.append(
                                    self._submit_single_goal(
                                        goal=goal,
                                        agent_id=_cand_agent_id,
                                        tenant_ctx=tenant_ctx,
                                        priority=priority,
                                        dry_run=dry_run,
                                    )
                                )
                            if len(_ma_tasks) > 1:
                                _ma_results = await asyncio.gather(
                                    *_ma_tasks, return_exceptions=True
                                )
                                _ma_valid = [
                                    r for r in _ma_results if isinstance(r, dict) and "goal_id" in r
                                ]
                                if _ma_valid:
                                    return {
                                        "mode": "multi_agent",
                                        "goal_ids": [r["goal_id"] for r in _ma_valid],
                                        "primary_goal_id": _ma_valid[0]["goal_id"],
                                        "goal_id": _ma_valid[0]["goal_id"],
                                        "agents": [r.get("agent_id") for r in _ma_valid],
                                    }
                    except Exception as exc:
                        _svc_logger.warning("agent_router_failed", error=str(exc))

                    # ── Fallback: pick BEST-SCORED agent using router's scoring
                    # (not just first created) — prevents wrong agent selection
                    # when the main route() call fails or returns low confidence.
                    if agent_id is None:
                        try:
                            _all_agents = agent_store.list(tenant_ctx=tenant_ctx)
                            if asyncio.iscoroutine(_all_agents):
                                _all_agents = await _all_agents
                            if _all_agents:
                                # Use router scoring to pick the best agent
                                from app.agent.router import AgentRouter

                                _fallback_router = AgentRouter(agent_store=agent_store)
                                _fallback_agents = [
                                    a if isinstance(a, dict) else a.__dict__ for a in _all_agents
                                ]
                                # Score each agent and pick highest
                                _best_id = None
                                _best_score = -1.0
                                for _fa in _fallback_agents:
                                    _kw = _fallback_router._score_by_keywords(goal, _fa)
                                    _cn = _fallback_router._score_by_connector_match(goal, _fa)
                                    _score = _kw * 0.5 + _cn * 0.5
                                    if _score > _best_score:
                                        _best_score = _score
                                        _best_id = _fa.get("agent_id")
                                # Use best if it has any score; otherwise first
                                agent_id = _best_id or (
                                    _fallback_agents[0].get("agent_id")
                                    if _fallback_agents
                                    else None
                                )
                                if agent_id:
                                    _svc_logger.info(
                                        "auto_routed_fallback",
                                        goal_id=goal_id,
                                        agent_id=agent_id,
                                        score=round(_best_score, 3),
                                    )
                        except Exception as _fb_exc:
                            _svc_logger.debug("agent_fallback_failed: %s", _fb_exc)

            record = GoalRecord(
                goal_id=goal_id,
                goal_text=goal,
                status=GoalStatus.PLANNING,
                tenant_id=tenant_ctx.tenant_id,
                priority=priority,
                dry_run=dry_run,
                created_at=datetime.now(UTC).isoformat(),
                agent_id=agent_id,
                workflow_mode=workflow_mode,
                execution_context=execution_context or {},
            )
            # Persisted with the goal so whichever replica sees the terminal
            # event releases the same (scoped) dedup key.
            record.execution_context[_DEDUP_SCOPE_KEY] = _dedup_scope
            self._goals[goal_id] = record

            # Compliance autonomy ceiling. ComplianceBundle.max_autonomy_mode
            # (HIPAA/SOX cap at "supervised", GDPR/PCI at "bounded-autonomous")
            # was computed by the trust-governance endpoint and read nowhere
            # else, so enabling a bundle changed how the platform *described*
            # itself and nothing about how agents actually ran.
            #
            # The ceiling is resolved here (submission is async and has the
            # tenant) and recorded on the goal, so a Celery worker that picks the
            # goal up on another replica runs under the same ceiling. The clamp
            # itself is applied in _make_agent_loop_for_tenant, where the
            # requested mode is finally assembled from execution_context *and*
            # the agent's own config — stamping a mode here instead would miss an
            # agent configured more permissively than a looser-but-still-binding
            # ceiling. Clamps downwards only; never widens what was asked for.
            _ceiling = await compliance_autonomy_ceiling(
                self._app_state, tenant_id=tenant_ctx.tenant_id
            )
            if _ceiling != "fully-autonomous":
                record.execution_context["compliance_autonomy_ceiling"] = _ceiling

            # AI Router model selection — record in execution_context for observability
            try:
                from app.ai_router.models import TaskType
                from app.ai_router.router import ai_router

                planner_model = ai_router.select_model(TaskType.PLANNING, tenant_ctx.tenant_id)
                if planner_model:
                    record.execution_context["ai_router_planner"] = (
                        f"{planner_model.provider}/{planner_model.model_id}"
                    )
                    record.execution_context["ai_router_quality_score"] = (
                        planner_model.quality_score
                    )
            except Exception:
                pass

            # The agent's pattern flags travel with the goal: a queued goal runs on a
            # worker that cannot see this replica's agent store.
            if agent_id:
                with suppress(Exception):
                    _agent_rec = None
                    _agent_store_for_flags = self._get_agent_store()
                    if _agent_store_for_flags is not None:
                        _agent_rec = _agent_store_for_flags.get(agent_id, tenant_ctx=tenant_ctx)
                    if isinstance(_agent_rec, dict):
                        _flags = {
                            k: bool(_agent_rec[k])
                            for k in AGENT_PATTERN_FLAG_KEYS
                            if k in _agent_rec
                        }
                        if _flags:
                            record.execution_context["agent_pattern_flags"] = _flags

            # Dynamic orchestration: build the runtime profile, record it on the goal, and
            # let the v2 rollout decide whether it drives execution. The profile columns
            # are written with the goal row below (same RLS'd transaction).
            _profile_columns: dict[str, Any] | None = None
            _profile_data = await self._build_runtime_profile(
                goal,
                goal_id=goal_id,
                tenant_ctx=tenant_ctx,
                agent_config=record.execution_context.get("strategy_runtime"),
            )
            if _profile_data:
                record.runtime_profile = _profile_data.get("profile_object")
                record.observed_runtime_profile = _profile_data.get("observed_profile")
                record.execution_context.update(_profile_data.get("context") or {})
                _profile_columns = _profile_data.get("columns")

            # Always-on pattern selection record: which agent pattern this goal was
            # routed to (+ why), in plain language — independent of the heavy
            # dynamic_orchestration flag — so the choice is real, traceable and
            # retrievable for every goal (see GET /goals/{id}/pattern-selection).
            try:
                from app.orchestration.pattern_selection_summary import (
                    summarize_pattern_selection,
                )

                _pattern_selection = summarize_pattern_selection(
                    goal,
                    goal_id=goal_id,
                    tenant_id=tenant_ctx.tenant_id,
                    agent_config=record.execution_context.get("strategy_runtime"),
                )
                record.execution_context["pattern_selection"] = _pattern_selection
            except Exception:
                pass

            # Agent Runtime 2.0: auto-create AgentExecutionPlan + AgentRunTrace per goal
            try:
                from app.agent_runtime.models import AgentExecutionPlan, AgentRunTrace
                from app.agent_runtime.store import agent_runtime_store

                _ar_now = datetime.now(UTC).isoformat()
                _plan_id = uuid.uuid4().hex
                _trace_id = uuid.uuid4().hex
                _ar_plan = AgentExecutionPlan(
                    plan_id=_plan_id,
                    goal_id=goal_id,
                    tenant_id=tenant_ctx.tenant_id,
                    goal_text=goal,
                    strategy=workflow_mode,
                    created_at=_ar_now,
                    agent_id=agent_id,
                )
                _ar_trace = AgentRunTrace(
                    trace_id=_trace_id,
                    goal_id=goal_id,
                    tenant_id=tenant_ctx.tenant_id,
                    plan=_ar_plan,
                )
                # Bounded + Redis-shared (other replicas / worker-run goals).
                await agent_runtime_store.put_plan(_ar_plan)
                await agent_runtime_store.put_trace(_ar_trace)
                record.execution_context["agent_runtime_plan_id"] = _plan_id
                record.execution_context["agent_runtime_trace_id"] = _trace_id
                _svc_logger.debug(
                    "agent_runtime_plan_created",
                    goal_id=goal_id,
                    plan_id=_plan_id,
                    trace_id=_trace_id,
                )
            except Exception as _ar_exc:
                _svc_logger.debug("agent_runtime_wire_skipped", error=str(_ar_exc)[:60])

            # Time-based eviction: evict stale terminal goals to prevent OOM.
            now = time.monotonic()
            if now - self._last_eviction_time > _EVICTION_INTERVAL_SECONDS:
                self._last_eviction_time = now
                asyncio.create_task(self._evict_async())  # noqa: RUF006  # fire-and-forget by design: intentionally not awaited/cancelled

            # Fix 6: record that a new goal has been started.
            record_goal_started(tenant_id=tenant_ctx.tenant_id, priority=priority)

            # Record who runs the goal (persisted with it) so restart recovery on
            # any replica can tell a worker-owned goal — or one whose in-process
            # runner is still alive — from a genuinely orphaned one.
            if not dry_run:
                record.execution_context[_RUNNER_KEY] = self._runner_marker()
                if self._task_queue is None:
                    await self._ensure_replica_heartbeat()

            # Persist to PostgreSQL in the background when a DB factory is wired.
            if self._db is not None and self._task_queue is not None and not dry_run:
                try:
                    await self._db_persist_goal(
                        goal_id=goal_id,
                        tenant_id=tenant_ctx.tenant_id,
                        goal_text=goal,
                        status=GoalStatus.PLANNING.value,
                        priority=priority,
                        dry_run=dry_run,
                        agent_id=agent_id,
                        workflow_mode=workflow_mode,
                        execution_context=record.execution_context,
                        raise_on_error=True,
                        **_profile_column_kwargs(_profile_columns),
                    )
                except Exception:
                    # The concurrent-goal counter was already incremented; since no
                    # background task will be created to decrement it on completion,
                    # we must decrement here before re-raising.
                    try:
                        from app.tenancy.limits import decrement_concurrent_goals

                        await decrement_concurrent_goals(
                            tenant_id=tenant_ctx.tenant_id,
                            redis=getattr(self, "_redis", None),
                        )
                    except Exception as _dec_exc:
                        _svc_logger.warning(
                            "counter_decrement_failed_on_error", error=str(_dec_exc)
                        )
                    raise
            elif self._db is not None:
                self._track_db_task(
                    self._db_persist_goal(
                        goal_id=goal_id,
                        tenant_id=tenant_ctx.tenant_id,
                        goal_text=goal,
                        status=GoalStatus.PLANNING.value,
                        priority=priority,
                        dry_run=dry_run,
                        agent_id=agent_id,
                        workflow_mode=workflow_mode,
                        execution_context=record.execution_context,
                        **_profile_column_kwargs(_profile_columns),
                    )
                )

            if not dry_run:
                if self._task_queue is not None:
                    _connector_ids: list[str] = []
                    if agent_id is not None:
                        _agent_store_for_queue = self._get_agent_store()
                        if _agent_store_for_queue is not None:
                            _agent_for_queue = _agent_store_for_queue.get(
                                agent_id, tenant_ctx=tenant_ctx
                            )
                            if isinstance(_agent_for_queue, dict):
                                _connector_ids = [
                                    str(item) for item in _agent_for_queue.get("connector_ids", [])
                                ]
                    # Only chained goals carry a depth (the worker re-publishes it
                    # on completion so goal chains stay bounded); other goals keep
                    # the enqueue call shape unchanged.
                    _chain_depth = int(
                        (execution_context or {}).get("trigger_chain_depth", 0) or 0
                    )
                    _chain_kw: dict[str, Any] = (
                        {"trigger_chain_depth": _chain_depth} if _chain_depth else {}
                    )
                    _source_trigger = str(
                        (execution_context or {}).get("source_trigger_id", "") or ""
                    )
                    if _source_trigger:
                        _chain_kw["source_trigger_id"] = _source_trigger
                    self._task_queue.enqueue_goal(
                        goal_id=goal_id,
                        tenant_id=tenant_ctx.tenant_id,
                        goal_text=goal,
                        priority=priority,
                        dry_run=dry_run,
                        agent_id=agent_id,
                        connector_ids=_connector_ids,
                        workflow_mode=workflow_mode,
                        goal_template="",
                        # Tolerate either a PlanTier enum or a raw plan string —
                        # non-request callers (e.g. the scheduled/beat dispatcher)
                        # may hand a plain string.
                        plan=getattr(tenant_ctx.plan, "value", tenant_ctx.plan),
                        **_chain_kw,
                    )
                else:
                    tool_context = await self._build_tool_context(
                        agent_id=agent_id, tenant_ctx=tenant_ctx, goal=goal
                    )
                    if workflow_mode == "multi_agent":
                        workflow_task = asyncio.create_task(
                            self._run_workflow(
                                goal_id, goal, tenant_ctx, tool_context=tool_context
                            ),
                            name=f"goal-workflow-{goal_id}",
                        )
                        record.task = workflow_task
                        return {
                            "goal_id": goal_id,
                            "status": record.status.value,
                            "goal": goal,
                            "priority": priority,
                            "dry_run": dry_run,
                            "agent_id": record.agent_id,
                            "workflow_mode": record.workflow_mode,
                            "created_at": record.created_at,
                        }
                    persistence_mode = (
                        record.runtime_profile.agent_patterns.persistence_mode
                        if record.runtime_profile is not None
                        else (execution_context or {}).get("persistence_mode", False)
                    )
                    persistence_cfg = (execution_context or {}).get("persistence_config", {})
                    if persistence_mode:
                        agent_task: asyncio.Task[None] = asyncio.create_task(
                            self._run_agent_loop_persistent(
                                goal_id,
                                goal,
                                tenant_ctx,
                                tool_context=tool_context,
                                persistence_config=persistence_cfg,
                            ),
                            name=f"goal-persistent-{goal_id}",
                        )
                    else:
                        agent_task = asyncio.create_task(
                            self._run_agent_loop(
                                goal_id, goal, tenant_ctx, tool_context=tool_context
                            ),
                            name=f"goal-{goal_id}",
                        )
                    record.task = agent_task
            else:
                # Dry-run: validate intent only, but still emit visible lifecycle
                # events so completed dry-runs are explainable in the UI.
                await self._dispatch_event(
                    goal_id, {"type": "goal_started", "goal": goal}, tenant_ctx=tenant_ctx
                )
                await self._dispatch_event(
                    goal_id,
                    {
                        "type": "dry_run_preview",
                        "message": "Dry run completed without executing tools or writing changes.",
                        "would_execute": False,
                    },
                    tenant_ctx=tenant_ctx,
                )
                await self._dispatch_event(
                    goal_id, {"type": "goal_complete"}, tenant_ctx=tenant_ctx
                )
                # Don't count dry-runs against the daily Redis limit — decrement it back
                _redis_for_dry = getattr(self, "_redis", None)
                if _redis_for_dry is not None:
                    try:
                        _today = datetime.now(UTC).strftime("%Y-%m-%d")
                        _dry_key = f"daily_goals:{tenant_ctx.tenant_id}:{_today}"
                        await _redis_for_dry.decr(_dry_key)
                    except Exception:
                        pass

        return {
            "goal_id": goal_id,
            "status": record.status.value,
            "goal": goal,
            "priority": priority,
            "dry_run": dry_run,
            "agent_id": record.agent_id,
            "workflow_mode": record.workflow_mode,
            "created_at": record.created_at,
        }

    async def get_goal(self, goal_id: str, tenant_ctx: TenantContext) -> dict[str, Any]:
        """Return goal metadata and current status."""
        record = self._goals.get(goal_id)
        if record is None or record.tenant_id != tenant_ctx.tenant_id:
            record = await self._db_get_goal_record(goal_id, tenant_ctx)
        else:
            record = await self._refresh_goal_from_db_if_needed(record, tenant_ctx)
        if record is None:
            raise NotFoundError(f"Goal not found: {goal_id}")
        events_for_replay = await self._events_for_replay(goal_id, record, tenant_ctx)
        event_count = len(events_for_replay)
        result_artifact = build_result_artifact(
            goal=record.goal_text,
            status=record.status.value,
            events=events_for_replay,
        )
        return {
            "goal_id": record.goal_id,
            "status": record.status.value,
            "goal": record.goal_text,
            "priority": record.priority,
            "dry_run": record.dry_run,
            "agent_id": record.agent_id,
            "workflow_mode": record.workflow_mode,
            "created_at": record.created_at,
            "event_count": event_count,
            "provider_warning": record.execution_context.get("provider_warning"),
            "result_artifact": result_artifact,
        }

    async def get_pattern_selection(
        self, goal_id: str, tenant_ctx: TenantContext
    ) -> dict[str, Any]:
        """Return the agent pattern this goal was routed to (+ why).

        Reads the ``pattern_selection`` record persisted at goal creation (which
        reflects any explicit strategy override); recomputes the summary on demand
        for goals persisted before the record existed. ``get_goal`` intentionally
        does not surface ``execution_context``, so this reads the record directly.
        """
        record = self._goals.get(goal_id)
        if record is None or record.tenant_id != tenant_ctx.tenant_id:
            record = await self._db_get_goal_record(goal_id, tenant_ctx)
        else:
            record = await self._refresh_goal_from_db_if_needed(record, tenant_ctx)
        if record is None:
            raise NotFoundError(f"Goal not found: {goal_id}")
        ctx = record.execution_context if isinstance(record.execution_context, dict) else {}
        selection = ctx.get("pattern_selection")
        if not isinstance(selection, dict) or not selection:
            from app.orchestration.pattern_selection_summary import summarize_pattern_selection

            strategy_runtime = ctx.get("strategy_runtime")
            selection = summarize_pattern_selection(
                record.goal_text,
                goal_id=goal_id,
                tenant_id=tenant_ctx.tenant_id,
                agent_config=strategy_runtime if isinstance(strategy_runtime, dict) else None,
            )
        return {"goal_id": goal_id, "status": record.status.value, **selection}

    async def list_goals(
        self, tenant_ctx: TenantContext, *, limit: int = 50, offset: int = 0
    ) -> dict[str, list[dict[str, Any]]]:
        """Return the tenant's goals, newest first, PAGINATED.

        DB-backed with ``ORDER BY created_at DESC LIMIT/OFFSET`` (served by
        ``ix_goals_tenant_created``) when a database is wired — instead of scanning
        an in-memory mirror of the whole ``goals`` table, which grows unbounded and
        OOMs at millions of rows (distributed-scale audit X5). Falls back to the
        in-memory dict only for the no-DB test/dev build.
        """
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))

        if self._db is not None:
            from sqlalchemy import select

            from app.db.models.goal import Goal
            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
                rows = (
                    await session.execute(
                        select(Goal)
                        .where(Goal.tenant_id == tenant_ctx.tenant_id)
                        .order_by(Goal.created_at.desc())
                        .limit(limit)
                        .offset(offset)
                    )
                ).scalars().all()
            batch_counts = await self._batch_event_counts(
                [r.id for r in rows], tenant_ctx.tenant_id
            )
            responses: list[dict[str, Any]] = []
            for r in rows:
                try:
                    status = GoalStatus(r.status).value
                except ValueError:
                    status = GoalStatus.PLANNING.value
                # Prefer the fresher in-memory event count if this pod holds the run.
                mem = self._goals.get(r.id)
                event_count = max(
                    batch_counts.get(r.id, 0), len(mem.events) if mem is not None else 0
                )
                responses.append(
                    {
                        "id": r.id,
                        "goal_id": r.id,
                        "status": status,
                        "goal": r.goal_text,
                        "priority": r.priority,
                        "dry_run": r.dry_run,
                        "agent_id": r.agent_id,
                        "workflow_mode": r.workflow_mode,
                        "created_at": r.created_at.isoformat() if r.created_at else "",
                        "event_count": event_count,
                    }
                )
            return {"goals": responses}

        # ── In-memory fallback (no DB configured: tests / dev) ──────────────
        tenant_records = [
            rec for rec in self._goals.values() if rec.tenant_id == tenant_ctx.tenant_id
        ]
        tenant_records.sort(key=lambda record: record.created_at, reverse=True)
        tenant_records = tenant_records[offset : offset + limit]
        goal_ids = [r.goal_id for r in tenant_records]
        batch_counts = await self._batch_event_counts(goal_ids, tenant_ctx.tenant_id)
        responses = []
        for record in tenant_records:
            event_count = max(batch_counts.get(record.goal_id, 0), len(record.events))
            responses.append(
                {
                    "id": record.goal_id,
                    "goal_id": record.goal_id,
                    "status": record.status.value,
                    "goal": record.goal_text,
                    "priority": record.priority,
                    "dry_run": record.dry_run,
                    "agent_id": record.agent_id,
                    "workflow_mode": record.workflow_mode,
                    "created_at": record.created_at,
                    "event_count": event_count,
                }
            )
        return {"goals": responses}

    async def get_metrics(self, tenant_ctx: TenantContext) -> dict[str, Any]:
        """Return aggregated metrics for the tenant's goals — reads from DB when available."""
        # Try DB-backed metrics first (works correctly in Celery/multi-process mode)
        if self._db is not None:
            try:
                from sqlalchemy import text

                async with self._db() as session:
                    row = (
                        await session.execute(
                            text("""
                        SELECT
                          COUNT(*) FILTER (WHERE status IN ('complete','completed')) AS completed,
                          COUNT(*) FILTER (WHERE status IN ('failed','error')) AS failed,
                          COUNT(*) FILTER (
                            WHERE status IN ('planning','executing','waiting_human')
                          ) AS active,
                          COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled,
                          COUNT(*) FILTER (
                            WHERE status IN ('complete','completed')
                              AND created_at::date = CURRENT_DATE
                          ) AS completed_today,
                          AVG(EXTRACT(EPOCH FROM (completed_at - created_at))*1000) FILTER (
                            WHERE status IN ('complete','completed')
                              AND completed_at IS NOT NULL
                          ) AS avg_latency_ms,
                          COUNT(*) FILTER (WHERE created_at::date = CURRENT_DATE) AS submitted_today
                        FROM goals WHERE tenant_id = :tid
                    """),
                            {"tid": tenant_ctx.tenant_id},
                        )
                    ).fetchone()

                    if row:
                        completed = row[0] or 0
                        failed = row[1] or 0
                        active = row[2] or 0
                        cancelled = row[3] or 0
                        terminal = completed + failed + cancelled
                        cost_today_usd = 0.0
                        cost_ctrl = (
                            (
                                getattr(self._app_state, "redis_cost_controller", None)
                                or getattr(self._app_state, "cost_controller", None)
                            )
                            if self._app_state
                            else None
                        )
                        if cost_ctrl is not None:
                            try:
                                _cost_val = cost_ctrl.get_tenant_cost_today(tenant_ctx)
                                if hasattr(_cost_val, "__await__"):
                                    cost_today_usd = await _cost_val
                                else:
                                    cost_today_usd = float(_cost_val or 0)
                            except Exception:
                                cost_today_usd = 0.0
                        return {
                            "active_goals": active,
                            "completed_goals": completed,
                            "failed_goals": failed,
                            "cancelled_goals": cancelled,
                            "completed_today": row[4] or 0,
                            "submitted_today": row[6] or 0,
                            "success_rate": round(completed / terminal, 3) if terminal > 0 else 0.0,
                            "avg_latency_ms": round(float(row[5] or 0), 1),
                            "cost_today_usd": cost_today_usd,
                            # Legacy fields for backward compat
                            "total_goals": completed + failed + active + cancelled,
                            "goals_today": row[6] or 0,
                        }
            except Exception as exc:
                _svc_logger.warning("get_metrics_db_failed", error=str(exc))

        # Fallback: in-memory calculation
        today = datetime.now(UTC).date().isoformat()
        active_goals = 0
        total_goals = 0
        completed_goals = 0
        failed_goals = 0
        cancelled_goals = 0
        goals_today = 0

        for record in self._goals.values():
            if record.tenant_id != tenant_ctx.tenant_id:
                continue
            total_goals += 1
            if record.status not in _TERMINAL_STATUSES:
                active_goals += 1
            if record.status == GoalStatus.COMPLETE:
                completed_goals += 1
            elif record.status == GoalStatus.FAILED:
                failed_goals += 1
            elif record.status == GoalStatus.CANCELLED:
                cancelled_goals += 1
            if record.created_at.startswith(today):
                goals_today += 1

        # Use only terminal goals as denominator so that in-progress goals
        # do not dilute the success rate.
        terminal_goals = completed_goals + failed_goals + cancelled_goals
        success_rate = completed_goals / terminal_goals if terminal_goals > 0 else 0.0

        durations = self._goal_durations.get(tenant_ctx.tenant_id, [])
        avg_latency_ms = (sum(durations) / len(durations) * 1000.0) if durations else 0.0

        cost_today_usd = 0.0
        cost_controller = getattr(self._app_state, "cost_controller", None)
        if cost_controller is not None:
            try:
                cost_today_usd = cost_controller.get_tenant_cost_today(tenant_ctx)
            except Exception:
                cost_today_usd = 0.0

        return {
            "active_goals": active_goals,
            "total_goals": total_goals,
            "success_rate": success_rate,
            "avg_latency_ms": avg_latency_ms,
            "cost_today_usd": cost_today_usd,
            "goals_today": goals_today,
        }

    async def _persisted_eval(
        self, goal_id: str, tenant_ctx: TenantContext
    ) -> tuple[Any, float, bool] | None:
        """The goal's latest persisted scorecard as ``(scorecard, average, passed)``.

        Reads ``evaluations`` (EvalRunner scorecards) then ``eval_scorecards``
        (runtime scorecards) under the tenant's RLS context. None when nothing
        is persisted (or no DB is configured). A DB error raises
        :class:`ServiceUnavailableError` — reporting "not_evaluated" for a goal
        whose scorecard merely could not be read would be a false answer.
        """
        if self._db is None:
            return None
        import json as _json

        from sqlalchemy import text as _sql

        from app.db.rls import sqlalchemy_rls_context
        from app.intelligence.eval import EvalScorecard, _pass_threshold

        def _scores(raw: Any) -> dict[str, float]:
            data = _json.loads(raw) if isinstance(raw, str | bytes) else raw
            if not isinstance(data, dict):
                return {}
            return {
                str(k): float(v) for k, v in data.items() if isinstance(v, int | float)
            }

        params = {"gid": goal_id, "tid": tenant_ctx.tenant_id}
        try:
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
                row = (
                    await session.execute(
                        _sql(
                            "SELECT scores, average_score, passed FROM evaluations "
                            "WHERE goal_id = :gid AND tenant_id = :tid "
                            "ORDER BY created_at DESC LIMIT 1"
                        ),
                        params,
                    )
                ).fetchone()
                if row is not None:
                    card = EvalScorecard(goal_id=goal_id, scores=_scores(row[0]))
                    return card, float(row[1]), bool(row[2])
                row = (
                    await session.execute(
                        _sql(
                            "SELECT scores, overall_score FROM eval_scorecards "
                            "WHERE goal_id = :gid AND tenant_id = :tid "
                            "ORDER BY created_at DESC LIMIT 1"
                        ),
                        params,
                    )
                ).fetchone()
        except Exception as exc:
            _svc_logger.warning("eval_scorecard_read_failed", goal_id=goal_id, error=str(exc)[:200])
            raise ServiceUnavailableError(
                "eval scorecards are unavailable", code="EVAL_STORE_UNAVAILABLE"
            ) from exc
        if row is None:
            return None
        card = EvalScorecard(goal_id=goal_id, scores=_scores(row[0]))
        average = float(row[1])
        return card, average, average >= _pass_threshold()

    async def _scorecard_for(self, goal_id: str, tenant_ctx: TenantContext) -> Any:
        """Cached scorecard, else the persisted one (then cached). None if unscored."""
        scorecard = self._eval_scores.get(goal_id)
        if scorecard is not None:
            return scorecard
        persisted = await self._persisted_eval(goal_id, tenant_ctx)
        if persisted is None:
            return None
        self._eval_scores[goal_id] = persisted[0]
        return persisted[0]

    async def get_eval(self, goal_id: str, tenant_ctx: TenantContext) -> dict[str, Any]:
        """Return the eval scorecard for *goal_id*, or a not-evaluated response.

        This replica's cache first, then the persisted scorecard (Postgres,
        tenant RLS) — a goal scored on another replica or before a restart used
        to report "not_evaluated" forever.
        """
        self._get_record(goal_id, tenant_ctx)  # raises if not found / wrong tenant
        scorecard = self._eval_scores.get(goal_id)
        persisted = None
        if scorecard is None and goal_id not in self._eval_pending:
            persisted = await self._persisted_eval(goal_id, tenant_ctx)
        if persisted is not None:
            card, average, passed = persisted
            self._eval_scores[goal_id] = card
            return {
                "goal_id": goal_id,
                "status": "evaluated",
                "scores": card.scores,
                "average_score": average,
                "passed": passed,
                "iterations": card.iterations,
            }
        if scorecard is None:
            return {
                "goal_id": goal_id,
                "status": "pending" if goal_id in self._eval_pending else "not_evaluated",
                "scores": {},
                "average_score": None,
                "passed": None,
            }
        return {
            "goal_id": scorecard.goal_id,
            "status": "evaluated",
            "scores": scorecard.scores,
            "average_score": scorecard.average_score(),
            "passed": scorecard.passed(),
            "iterations": scorecard.iterations,
        }

    async def get_eval_suggestions(self, goal_id: str, tenant_ctx: TenantContext) -> dict[str, Any]:
        """Auto-suggest improvement actions for a goal from its real eval scores.

        Every dimension scoring below the config-driven pass threshold
        (``EvalScoringConfig.pass_threshold``, env-overridable) yields one
        actionable suggestion. Deterministic from the real scorecard — no
        fabricated data; honest empty when the goal is unevaluated or all
        dimensions pass. This is the read side of the self-improvement surface.
        """
        self._get_record(goal_id, tenant_ctx)  # raises if not found / wrong tenant
        scorecard = await self._scorecard_for(goal_id, tenant_ctx)
        if scorecard is None:
            return {"goal_id": goal_id, "status": "not_evaluated", "pass_threshold": None,
                    "suggestions": [], "count": 0}

        from app.evals.scoring_config import EvalScoringConfig

        threshold = EvalScoringConfig.from_settings().pass_threshold
        # Advisory copy per dimension (UI guidance, not data) — the trigger + score
        # are real; the phrasing points the operator at the right lever.
        _completion_advice = (
            "Goal wasn't fully achieved — tighten the planner prompt or decompose "
            "into smaller verifiable sub-goals."
        )
        advice = {
            "task_completion": _completion_advice,
            "completion": _completion_advice,
            "efficiency": (
                "Used more iterations/cost than budget — enable goal-tree parallelism "
                "or a cheaper executor model for simple steps."
            ),
            "accuracy": (
                "Low grounding/accuracy — attach a knowledge collection (RAG) or raise "
                "the retrieval top-k so claims are evidence-backed."
            ),
            "safety": (
                "Safety/guardrail signal — review the guardrail profile and add HITL "
                "gating for the risky tool/step."
            ),
            "coherence": (
                "Output coherence was low — add a reflection node or strengthen the "
                "verifier's rubric."
            ),
            "sla": (
                "Ran slower than the SLA — cache retrieval, reduce tool round-trips, "
                "or route to a faster model."
            ),
            "tool_relevance": (
                "Tool calls weren't relevant/efficient — refine tool descriptions or "
                "restrict the tool set for this agent."
            ),
        }
        suggestions: list[dict[str, Any]] = []
        for dim, score in (scorecard.scores or {}).items():
            if isinstance(score, int | float) and score < threshold:
                suggestions.append({
                    "dimension": dim,
                    "score": round(float(score), 4),
                    "threshold": threshold,
                    "suggestion": advice.get(
                        dim, f"'{dim}' scored below the pass threshold — review this dimension."
                    ),
                })
        suggestions.sort(key=lambda s: s["score"])  # worst first
        return {
            "goal_id": goal_id,
            "status": "evaluated",
            "pass_threshold": threshold,
            "suggestions": suggestions,
            "count": len(suggestions),
        }

    async def run_eval(self, goal_id: str, tenant_ctx: TenantContext) -> dict[str, Any]:
        """Score a goal on demand and cache the result.

        Unlike ``get_eval`` which returns cached scores, this always runs
        the EvalRunner and stores the result. Enables the "Run Eval" button.
        """
        record = self._get_record(goal_id, tenant_ctx)

        # Try to get live AgentState from the record
        state = getattr(record, "agent_state", None)
        if state is None:
            # Reconstruct minimal state from record data
            try:
                goal_status = GoalStatus(record.status.value)
            except (ValueError, AttributeError):
                goal_status = GoalStatus.COMPLETE

            state = AgentState(
                goal_id=goal_id,
                goal=record.goal_text,
                tenant_ctx=tenant_ctx,
                status=goal_status,
                steps=list(record.steps) if getattr(record, "steps", None) else [],
                verification_success=(record.status == record.status.COMPLETE),
                verification_feedback=record.execution_context.get("verification_feedback", "")
                if isinstance(record.execution_context, dict)
                else "",
                events=list(record.events) if getattr(record, "events", None) else [],
                iterations=int(record.execution_context.get("iterations", 1))
                if isinstance(record.execution_context, dict)
                else 1,
                context=dict(record.execution_context)
                if isinstance(record.execution_context, dict)
                else {},
            )

        from app.intelligence.eval_runner import EvalRunner

        runner = EvalRunner()
        provider = getattr(self, "_app_provider", None)
        scorecard = await runner.score_async(
            state=state,
            tenant_ctx=tenant_ctx,
            provider=provider,
        )

        # Cache for subsequent GET requests
        self._eval_scores[goal_id] = scorecard

        return {
            "goal_id": scorecard.goal_id,
            "status": "evaluated",
            "scores": scorecard.scores,
            "average_score": scorecard.average_score(),
            "passed": scorecard.passed(),
            "iterations": scorecard.iterations,
        }

    async def cancel_goal(self, goal_id: str, tenant_ctx: TenantContext) -> dict[str, Any]:
        """Cancel a running goal.  Idempotent if the goal is already terminal.

        Works from any replica: the goal is loaded from Postgres when this
        replica does not run it, and the cancel reaches its runner (another
        replica's in-process loop or a Celery worker) through the Redis cancel
        flag, which both poll. If that flag cannot be written for a goal running
        elsewhere the call fails (503) without changing anything.
        """
        from app.reliability.goal_lifecycle import signal_cancel

        record = await self._aget_record(goal_id, tenant_ctx)
        if record.status in _TERMINAL_STATUSES:
            return {"goal_id": goal_id, "status": record.status.value}
        await self._signal_runner(record, signal_cancel, "cancel")

        # Persist to the DB directly. The worker normally writes terminal status,
        # but a cancelled goal whose worker already died (or a stuck/zombie
        # "executing" row) would otherwise be refreshed straight back to its old
        # status by _refresh_goal_from_db_if_needed on the next read — leaving the
        # goal un-cancellable and holding a plan concurrency slot forever.
        # The write is CONDITIONAL: this replica's copy may be stale, and a goal
        # the worker finished meanwhile must keep its real terminal status. A
        # failed write is a 503, never a reported-but-lost cancel.
        changed = await self._db_update_goal_status(
            goal_id,
            tenant_ctx.tenant_id,
            GoalStatus.CANCELLED.value,
            only_if_active=True,
            raise_on_error=True,
        )
        if changed is False:
            fresh = await self._db_get_goal_record(goal_id, tenant_ctx)
            if fresh is not None and fresh.status in _TERMINAL_STATUSES:
                return {"goal_id": goal_id, "status": fresh.status.value}
        if self._runs_locally(record):
            assert record.task is not None
            record.task.cancel()

        record.status = GoalStatus.CANCELLED
        cancelled_event: dict[str, Any] = {"type": "goal_cancelled"}
        await self._dispatch_event(goal_id, cancelled_event, tenant_ctx=tenant_ctx)
        return {"goal_id": goal_id, "status": GoalStatus.CANCELLED.value}

    async def pause_goal(self, goal_id: str, tenant_ctx: TenantContext) -> dict[str, Any]:
        """Pause a running goal at its next step boundary — from any replica.

        A goal run by this replica blocks on its in-process pause event; one run
        by another replica or a worker blocks on the Redis pause flag (both
        runners' pause gates poll it). The flag must be written for a remote
        goal, or the call fails (503) before any state changes.
        """
        from app.reliability.goal_lifecycle import signal_pause

        record = await self._aget_record(goal_id, tenant_ctx)
        if record.status not in {GoalStatus.EXECUTING, GoalStatus.PLANNING}:
            raise ValueError(f"Goal {goal_id} is not running (status: {record.status.value})")
        await self._signal_runner(record, signal_pause, "pause")
        # Persist to the DB directly, mirroring the cancel_goal fix: a concurrent
        # (or merely subsequent) get_goal() call refreshes from the DB whenever a
        # task_queue is configured — regardless of status — via
        # _should_refresh_goal_from_db. Without this write, that refresh reloads
        # the stale "executing" row and silently un-pauses the goal from every
        # caller's point of view (while the operator believes it is paused and
        # the worker may in fact be blocked on the Redis pause flag).
        # Conditional (a goal that finished meanwhile is not "paused") and a
        # failed write is a 503, not a pause reported as done.
        changed = await self._db_update_goal_status(
            goal_id,
            tenant_ctx.tenant_id,
            GoalStatus.WAITING_HUMAN.value,
            only_if_active=True,
            raise_on_error=True,
        )
        if changed is False:
            fresh = await self._db_get_goal_record(goal_id, tenant_ctx)
            if fresh is not None and fresh.status in _TERMINAL_STATUSES:
                # Drop the pause flag we just set: nothing is left to pause.
                _redis = getattr(self, "_redis", None)
                if _redis is not None:
                    from app.reliability.goal_lifecycle import clear_signals

                    await clear_signals(goal_id, _redis)
                raise ValueError(
                    f"Goal {goal_id} is not running (status: {fresh.status.value})"
                )
        if self._runs_locally(record):
            _GOAL_PAUSE_EVENTS[goal_id] = asyncio.Event()
        record.status = GoalStatus.WAITING_HUMAN
        await self._dispatch_event(goal_id, {"type": "goal_paused"}, tenant_ctx=tenant_ctx)
        return {"goal_id": goal_id, "status": "paused"}

    async def _resolve_tenant_llm_config(self, tenant_ctx: TenantContext) -> dict[str, Any] | None:
        """The tenant's BYOK provider config from the durable store (None if unset)."""
        from app.services.llm_config_store import get_llm_config_store

        store = (
            getattr(self._app_state, "llm_config_store", None) if self._app_state else None
        ) or get_llm_config_store()
        if store is None:
            return None
        try:
            cfg = await store.get_config(tenant_ctx.tenant_id, strict=True)
        except TypeError:
            # A store without strict reads (tests/fakes).
            cfg = await store.get_config(tenant_ctx.tenant_id)
        # LLMConfigReadError propagates: the goal fails instead of silently
        # running on the platform provider when the tenant's BYOK is unknown.
        return dict(cfg) if cfg else None

    async def _suspend_for_approval(self, goal_id: str, tenant_ctx: TenantContext) -> None:
        """Record that the agent graph ENDED waiting for approvals (supervised mode).

        The run returned with status WAITING_HUMAN but emits no event for it, and
        its result used to be ignored: the goal stayed "executing" forever,
        holding its concurrency slot, and resume_goal (which requires
        WAITING_HUMAN) could never continue it. The goal is now marked waiting
        and suspended — no task holds it, so the slot is released — and
        ``resume_goal`` relaunches it from its step checkpoints.
        """
        record = self._goals.get(goal_id)
        if record is None or record.status in _TERMINAL_STATUSES:
            return
        record.status = GoalStatus.WAITING_HUMAN
        record.execution_context[_SUSPENDED_KEY] = True
        await self._db_update_goal_status(
            goal_id, tenant_ctx.tenant_id, GoalStatus.WAITING_HUMAN.value
        )
        await self._db_set_suspended(goal_id, tenant_ctx.tenant_id, True)
        from app.tenancy.limits import decrement_concurrent_goals

        await decrement_concurrent_goals(tenant_id=tenant_ctx.tenant_id, redis=self._redis)
        await self._dispatch_event(
            goal_id,
            {"type": "goal_waiting_human", "reason": "pending approvals (supervised mode)"},
            tenant_ctx=tenant_ctx,
        )

    async def _relaunch_suspended_goal(
        self, record: GoalRecord, tenant_ctx: TenantContext
    ) -> None:
        """Run a suspended goal again under its own id; its checkpoints resume it."""
        from app.tenancy.limits import check_and_increment_concurrent_goals

        # The slot was released on suspension; a tenant at its limit gets a 429
        # and the goal stays waiting (the approval can be retried).
        await check_and_increment_concurrent_goals(tenant_ctx=tenant_ctx, redis=self._redis)
        record.execution_context.pop(_SUSPENDED_KEY, None)
        await self._db_set_suspended(record.goal_id, tenant_ctx.tenant_id, False)
        # The relaunch may run somewhere else than the original run did.
        record.execution_context[_RUNNER_KEY] = self._runner_marker()
        try:
            await self._db_set_runner(
                record.goal_id, tenant_ctx.tenant_id, record.execution_context[_RUNNER_KEY]
            )
        except Exception as exc:
            _svc_logger.warning("db_set_runner_failed", goal_id=record.goal_id, error=str(exc))
        if self._task_queue is None:
            await self._ensure_replica_heartbeat()
        if self._task_queue is not None:
            self._task_queue.enqueue_goal(
                goal_id=record.goal_id,
                tenant_id=tenant_ctx.tenant_id,
                goal_text=record.goal_text,
                priority=record.priority,
                dry_run=False,
                agent_id=record.agent_id,
                connector_ids=[],
                workflow_mode=record.workflow_mode,
                goal_template="",
                plan=getattr(tenant_ctx.plan, "value", tenant_ctx.plan),
            )
            return
        tool_context = await self._build_tool_context(
            agent_id=record.agent_id, tenant_ctx=tenant_ctx, goal=record.goal_text
        )
        record.task = asyncio.create_task(
            self._run_agent_loop(
                record.goal_id, record.goal_text, tenant_ctx, tool_context=tool_context
            ),
            name=f"goal-{record.goal_id}",
        )

    async def _db_set_suspended(self, goal_id: str, tenant_id: str, suspended: bool) -> None:
        """Set/clear the suspended flag in goals.execution_context (atomic JSON merge,
        so the API and a worker never overwrite each other's context)."""
        if self._db is None:
            return
        try:
            from sqlalchemy import text as _sql

            from app.db.rls import sqlalchemy_rls_context

            expr = (
                "COALESCE(execution_context::jsonb, '{}'::jsonb) "
                "|| jsonb_build_object(CAST(:k AS text), true)"
                if suspended
                else "COALESCE(execution_context::jsonb, '{}'::jsonb) - CAST(:k AS text)"
            )
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    _sql(
                        f"UPDATE goals SET execution_context = ({expr})::json "
                        "WHERE id = :g AND tenant_id = :t"
                    ),
                    {"k": _SUSPENDED_KEY, "g": goal_id, "t": tenant_id},
                )
        except Exception as exc:
            _svc_logger.warning("db_set_suspended_failed", error=str(exc))

    def _make_pause_gate(
        self, goal_id: str, tenant_ctx: TenantContext
    ) -> Callable[[], Awaitable[None]]:
        """Return the gate the agent loop awaits between steps.

        It blocks while the goal is paused — by ``pause_goal`` on this replica
        (in-process event) or on any other replica (Redis pause flag) — and
        raises ``GoalCancelledError`` when the goal was cancelled from any
        replica (Redis cancel flag), at every step boundary: a cancel issued on
        another replica used to be observed only while paused, so the goal ran
        to completion.
        """
        from app.governance.emergency_stop import enforce_emergency_stop
        from app.reliability.goal_lifecycle import GoalCancelledError, is_cancelled, is_paused

        async def _gate() -> None:
            _redis = getattr(self, "_redis", None)
            if _redis is not None and await is_cancelled(goal_id, _redis):
                raise GoalCancelledError(f"Goal {goal_id} cancelled")
            # Emergency stop, observed at EVERY step boundary on every replica:
            # it used to be checked only when a goal started, so running goals
            # (and any on other replicas) kept going. Fails closed on a Redis error.
            _rec = self._goals.get(goal_id)
            _org = (_rec.execution_context or {}).get("org_id") if _rec is not None else None
            _stop = await enforce_emergency_stop(
                _redis, tenant_ctx.tenant_id, str(_org) if _org else None
            )
            if _stop:
                raise GoalCancelledError(f"Goal {goal_id} stopped: {_stop}")
            announced = False
            while True:
                evt = _GOAL_PAUSE_EVENTS.get(goal_id)
                local = evt is not None and not evt.is_set()
                redis = getattr(self, "_redis", None)
                remote = not local and redis is not None and await is_paused(goal_id, redis)
                if not (local or remote):
                    break
                if not announced:
                    announced = True
                    _rec = self._goals.get(goal_id)
                    if _rec is not None and _rec.status not in _TERMINAL_STATUSES:
                        # Paused from another replica: reflect it locally too.
                        _rec.status = GoalStatus.WAITING_HUMAN
                    await self._dispatch_event(
                        goal_id, {"type": "goal_paused_at_step_boundary"}, tenant_ctx=tenant_ctx
                    )
                if local and evt is not None:
                    with suppress(TimeoutError):
                        await asyncio.wait_for(evt.wait(), timeout=_PAUSE_POLL_SECONDS)
                else:
                    await asyncio.sleep(_PAUSE_POLL_SECONDS)
                if redis is not None and await is_cancelled(goal_id, redis):
                    raise GoalCancelledError(f"Goal {goal_id} cancelled while paused")
            if announced:
                _rec = self._goals.get(goal_id)
                if _rec is not None and _rec.status == GoalStatus.WAITING_HUMAN:
                    _rec.status = GoalStatus.EXECUTING
                await self._dispatch_event(
                    goal_id, {"type": "goal_execution_resumed"}, tenant_ctx=tenant_ctx
                )

        return _gate

    async def resume_goal(
        self,
        goal_id: str,
        tenant_ctx: TenantContext,
        *,
        approved: bool = True,
        feedback: str = "",
    ) -> dict[str, Any]:
        """Resume a paused goal.

        When *approved* is False the goal is immediately failed with the
        rejection feedback.  When True the goal is resumed — either by
        re-invoking the stored AgentGraph from its LangGraph checkpoint, or by
        firing the legacy asyncio pause-event (backward-compatible fallback).
        """
        record = await self._aget_record(goal_id, tenant_ctx)
        if record.status in _TERMINAL_STATUSES:
            raise ValueError(f"Goal {goal_id} is already terminal (status: {record.status.value})")
        if record.status != GoalStatus.WAITING_HUMAN:
            # Idempotency guard: without this, a duplicate/racing resume_goal
            # call (double-click, retried request, etc.) that arrives after the
            # first call has already flipped the record out of WAITING_HUMAN
            # (e.g. to EXECUTING, below) would pass the "not terminal" check
            # above and re-run this whole method a second time — firing a
            # second fire-and-forget graph._graph.astream(resume_input,
            # config=config) task against the *same* LangGraph checkpoint
            # thread_id concurrently with the first. Two concurrent astream()
            # calls against one checkpoint thread can interleave writes and
            # duplicate side effects (e.g. re-run the step the HITL gate was
            # blocking on). Requiring WAITING_HUMAN specifically closes that
            # window: the second call now raises instead of double-resuming.
            raise ValueError(
                f"Goal {goal_id} is not waiting for human approval (status: {record.status.value})"
            )

        if not approved:
            if not record.execution_context.get(_SUSPENDED_KEY):
                # A runner is blocked in its pause gate (here, on another replica,
                # or on a worker): stop it rather than leave it paused forever.
                from app.reliability.goal_lifecycle import signal_cancel

                await self._signal_runner(record, signal_cancel, "reject")
                if self._runs_locally(record):
                    assert record.task is not None
                    record.task.cancel()
            record.status = GoalStatus.FAILED
            record.execution_context["hitl_rejected"] = True
            record.execution_context["hitl_feedback"] = feedback
            # Phase 12: Store rejection note so agent graph can use it for replanning.
            # The graph should read agent_state.context.get("hitl_rejection_note")
            # and inject it into the reflection prompt.
            record.hitl_rejection_note = feedback
            record.execution_context["hitl_rejection_note"] = feedback
            await self._db_update_goal_status(
                goal_id, tenant_ctx.tenant_id, GoalStatus.FAILED.value
            )
            await self._dispatch_event(
                goal_id,
                {"type": "goal_failed", "reason": f"HITL rejected: {feedback}"},
                tenant_ctx=tenant_ctx,
            )
            return {"goal_id": goal_id, "status": "rejected"}

        # Store approval decision so the graph can read it on resume
        record.execution_context["hitl_approved"] = True
        record.execution_context["hitl_feedback"] = feedback

        # The running goal is blocked in its pause gate at a step boundary (see
        # _make_pause_gate); releasing the gate resumes it where it stopped. This
        # used to re-invoke the LangGraph graph via astream() on a thread id that
        # never matched the one run() uses and without the goal in the input, so
        # it always errored into this path — and had the ids matched it would
        # have started a second concurrent execution of the same goal.
        # A supervised goal whose graph ended waiting for approvals has no task
        # blocked in a pause gate to release: run it again (checkpoints skip the
        # steps it already finished).
        if record.execution_context.get(_SUSPENDED_KEY):
            await self._relaunch_suspended_goal(record, tenant_ctx)
        else:
            # Release the runner's pause gate wherever it runs: clearing the Redis
            # pause flag is what another replica's loop / a worker is polling.
            # Awaited (it was fire-and-forget) and, for a remote goal, required —
            # otherwise the goal would read "executing" while still blocked.
            from app.reliability.goal_lifecycle import signal_resume

            await self._signal_runner(record, signal_resume, "resume")
        record.status = GoalStatus.EXECUTING
        await self._db_update_goal_status(
            goal_id, tenant_ctx.tenant_id, GoalStatus.EXECUTING.value
        )
        record.events.append(
            {"type": "hitl_approved", "feedback": feedback, "ts": datetime.now(UTC).isoformat()}
        )
        evt = _GOAL_PAUSE_EVENTS.pop(goal_id, None)
        if evt is not None:
            evt.set()
        await self._dispatch_event(goal_id, {"type": "goal_resumed"}, tenant_ctx=tenant_ctx)
        return {"goal_id": goal_id, "status": "resumed"}

    async def get_events(self, goal_id: str, tenant_ctx: TenantContext) -> list[dict[str, Any]]:
        """Return a snapshot of all SSE events emitted so far for *goal_id*.

        Uses the same in-memory → DB fallback pattern as :meth:`get_goal` so
        that events are available even after a server restart or when the goal
        was executed by a Celery worker in a separate process.
        """
        # Try in-memory cache first; fall back to DB (mirrors get_goal logic)
        record = self._goals.get(goal_id)
        if record is None or record.tenant_id != tenant_ctx.tenant_id:
            record = await self._db_get_goal_record(goal_id, tenant_ctx)
        if record is None:
            raise NotFoundError(f"Goal not found: {goal_id}")
        # Merge in-memory + DB-persisted events without duplicates.
        # This handles Celery multi-process mode where record.events is empty
        # on the API server but events are durably stored in the event store.
        return await self._events_for_replay(goal_id, record, tenant_ctx)

    async def subscribe_events(
        self,
        goal_id: str,
        tenant_ctx: TenantContext,
        since_sequence: int = 0,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Async generator that yields SSE events for *goal_id* in real time.

        When *since_sequence* > 0 the initial replay is limited to events with
        sequence > since_sequence (SSE resume-from-cursor).  Each event yielded
        from the persisted replay path carries a ``_seq`` key for the SSE
        endpoint to emit as an ``id:`` line.

        **Cross-replica delivery (P1-2):** when the goal record is not present
        in this replica's in-memory ``_goals`` dict (it was submitted to a
        different replica), the method falls back to Redis pub/sub on the
        ``goal_events:{tenant_id}:{goal_id}`` channel that is published by
        ``_dispatch_event`` on the owning replica.
        """
        # ── Try local record first ─────────────────────────────────────────────
        local_record: GoalRecord | None = None
        with suppress(Exception):  # goal is on another replica — cross-replica path below
            local_record = self._get_record(goal_id, tenant_ctx)

        # ── Cross-replica path: subscribe via Redis pub/sub ────────────────────
        if local_record is None:
            # Validate the goal exists in DB and belongs to this tenant before
            # opening a long-lived pub/sub connection (avoids silent no-ops for
            # truly missing goal IDs).
            db_record = await self._db_get_goal_record(goal_id, tenant_ctx)
            if db_record is None:
                raise NotFoundError(f"Goal not found: {goal_id}")

            # Replay historical events persisted by the owning replica.
            if since_sequence > 0:
                replay_events = await self._list_events_since_persisted(
                    goal_id, after_sequence=since_sequence, tenant_ctx=tenant_ctx
                )
            else:
                replay_events = await self._list_persisted_events(goal_id, tenant_ctx)
            for event in replay_events:
                yield event

            # If the goal is already in a terminal state we're done — no need
            # to subscribe to live events.
            status_str = (
                db_record.status.value
                if hasattr(db_record.status, "value")
                else str(db_record.status)
            )
            if status_str in ("complete", "failed", "cancelled"):
                return

            # Subscribe to Redis pub/sub for live events published by the
            # owning replica's _dispatch_event().
            if not self._redis_url_for_pubsub:
                return  # No Redis URL configured — cross-replica delivery unavailable

            try:
                import redis.asyncio as _aioredis

                async with (
                    _aioredis.from_url(
                        self._redis_url_for_pubsub, decode_responses=True
                    ) as _pubsub_client,
                    _pubsub_client.pubsub() as pubsub,
                ):
                    channel = f"goal_events:{tenant_ctx.tenant_id}:{goal_id}"
                    await pubsub.subscribe(channel)
                    async for message in pubsub.listen():
                        if message.get("type") != "message":
                            continue
                        try:
                            event = json.loads(message["data"])
                            yield event
                            # Stop streaming at terminal events
                            if event.get("type") in (
                                "goal_complete",
                                "goal_failed",
                                "goal_cancelled",
                            ):
                                break
                        except Exception:
                            continue
            except Exception as exc:
                _svc_logger.warning(
                    "cross_replica_sse_failed",
                    goal_id=goal_id,
                    error=str(exc)[:120],
                )
            return

        # ── Local replica path (unchanged) ────────────────────────────────────
        record = local_record
        queue: asyncio.Queue[dict[str, Any] | None] | None = None
        if record.status not in _TERMINAL_STATUSES:
            queue = asyncio.Queue(maxsize=512)
            record.subscribers.append(queue)

        try:
            # Replay persisted events and in-memory events without duplicating events
            # already recovered from the durable stream. The live queue is registered
            # first so events emitted during replay are not missed by the SSE stream.
            if since_sequence > 0:
                replay_events = await self._list_events_since_persisted(
                    goal_id, after_sequence=since_sequence, tenant_ctx=tenant_ctx
                )
            else:
                replay_events = await self._events_for_replay(goal_id, record, tenant_ctx)
            seen = {self._event_key(event) for event in replay_events}
            for event in replay_events:
                yield event

            if queue is not None:
                while True:
                    try:
                        queued = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if queued is None:
                        return
                    key = self._event_key(queued)
                    if key in seen:
                        continue
                    seen.add(key)
                    yield queued

            replay_status = self._status_from_events(replay_events)
            if replay_status is not None:
                record.status = replay_status

            # If goal is already terminal, nothing more to await
            if record.status in _TERMINAL_STATUSES:
                return

            record = await self._refresh_goal_from_db_if_needed(record, tenant_ctx)
            if record.status in _TERMINAL_STATUSES:
                return

            if queue is None:
                return
            while True:
                item = await queue.get()
                if item is None:  # end-of-stream
                    break
                yield item
        finally:
            if queue is not None:
                with suppress(ValueError):
                    record.subscribers.remove(queue)

    # ── governance delegations ────────────────────────────────────────────────

    async def get_audit_entries(
        self,
        goal_id: str,
        tenant_ctx: TenantContext,
    ) -> list[dict[str, Any]]:
        """Return audit log entries for *goal_id* (tenant-validated)."""
        await self._aget_record(goal_id, tenant_ctx)  # raises if not found / wrong tenant
        # Prefer DB query (works across all processes / Celery workers)
        if hasattr(self._audit_log, "query_db"):
            try:
                db_entries = await self._audit_log.query_db(
                    tenant_ctx=tenant_ctx,
                    goal_id=goal_id,
                    limit=500,
                )
                return [
                    {
                        "event_id": e.event_id,
                        "goal_id": e.goal_id,
                        "tool_name": e.tool_name,
                        "outcome": e.outcome,
                        "created_at": e.created_at,
                    }
                    for e in db_entries
                ]
            except Exception:
                pass
        # Fallback to in-memory
        entries = self._audit_log.query(tenant_ctx=tenant_ctx, goal_id=goal_id)
        return [
            {
                "event_id": e.event_id,
                "goal_id": e.goal_id,
                "tool_name": e.tool_name,
                "action_level": e.action_level.value,
                "outcome": e.outcome,
                "step_id": e.step_id,
                "approver": e.approver,
                "note": e.note,
            }
            for e in entries
        ]

    async def handle_approval(
        self,
        goal_id: str,
        request_id: str,
        action: str,
        approver: str,
        note: str,
        tenant_ctx: TenantContext,
    ) -> dict[str, Any]:
        """Approve or reject a pending HITL request for *goal_id*."""
        # Any replica: the goal may have been created elsewhere / run on a worker.
        await self._aget_record(goal_id, tenant_ctx)  # raises if not found / wrong tenant
        if action == "approve":
            # DB-resolving: the sync approve() only sees this process's cache,
            # which is not warmed from the DB at startup (Postgres is the source
            # of truth), so a gate raised before a restart / on another replica
            # would read as not found.
            ok = await self._hitl.approve_async(
                request_id, approver=approver, note=note, tenant_ctx=tenant_ctx
            )
        elif action == "reject":
            ok = await self._hitl.reject(
                request_id, approver=approver, note=note, tenant_ctx=tenant_ctx
            )
        else:
            ok = False
        # Coerce to a plain bool for the response payload (the sync
        # ``HITLGateway.approve`` returns an ``_AwaitableBool`` pydantic cannot
        # serialize, and test doubles may return any truthy value).
        return {"request_id": request_id, "action": action, "accepted": bool(ok)}

    # ── DB persistence helpers ────────────────────────────────────────────────

    async def _db_persist_goal(
        self,
        goal_id: str,
        tenant_id: str,
        goal_text: str,
        status: str,
        priority: str,
        dry_run: bool,
        agent_id: str | None = None,
        workflow_mode: str = "single_agent",
        execution_context: dict[str, Any] | None = None,
        raise_on_error: bool = False,
        runtime_profile_columns: dict[str, Any] | None = None,
    ) -> None:
        """Persist goal record to PostgreSQL.

        ``runtime_profile_columns`` (JSON-safe, from ``_build_runtime_profile``) fill the
        goals runtime-profile columns in the same RLS'd insert as the row itself.
        """
        if self._db is None:
            return
        try:
            from app.db.models.goal import Goal
            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                g = Goal(
                    id=goal_id,
                    tenant_id=tenant_id,
                    goal_text=goal_text,
                    status=status,
                    priority=priority,
                    dry_run=dry_run,
                    agent_id=agent_id,
                    workflow_mode=workflow_mode,
                    execution_context=execution_context or {},
                    **{
                        key: value
                        for key, value in (runtime_profile_columns or {}).items()
                        if key in _RUNTIME_PROFILE_COLUMNS
                    },
                )
                session.add(g)
        except Exception as exc:
            _svc_logger.warning("DB persist goal failed: %s", exc)
            if raise_on_error:
                raise

    async def _db_ensure_goal_row(
        self,
        *,
        goal_id: str,
        tenant_id: str,
        goal_text: str,
        status: str,
        priority: str,
        dry_run: bool,
        agent_id: str | None = None,
        workflow_mode: str = "single_agent",
        execution_context: dict[str, Any] | None = None,
    ) -> None:
        """Create the durable goal row before worker events reference it."""
        if self._db is None:
            return
        tenant_ctx = TenantContext(
            tenant_id=tenant_id,
            plan=PlanTier.FREE,
            api_key_id="goal-row-ensure",
        )
        existing = await self._db_get_goal_record(goal_id, tenant_ctx)
        if existing is not None:
            return
        await self._db_persist_goal(
            goal_id=goal_id,
            tenant_id=tenant_id,
            goal_text=goal_text,
            status=status,
            priority=priority,
            dry_run=dry_run,
            agent_id=agent_id,
            workflow_mode=workflow_mode,
            execution_context=execution_context or {},
            raise_on_error=True,
        )

    async def _db_get_goal_record(
        self, goal_id: str, tenant_ctx: TenantContext
    ) -> GoalRecord | None:
        """Load one goal from PostgreSQL when this process has no memory record."""
        if self._db is None:
            return None
        try:
            from sqlalchemy import select

            from app.db.models.goal import Goal
            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
                result = await session.execute(
                    select(Goal).where(
                        Goal.id == goal_id,
                        Goal.tenant_id == tenant_ctx.tenant_id,
                    )
                )
                row = result.scalar_one_or_none()
            if row is None:
                return None
            try:
                status = GoalStatus(row.status)
            except ValueError:
                status = GoalStatus.PLANNING
            record = GoalRecord(
                goal_id=row.id,
                goal_text=row.goal_text,
                status=status,
                tenant_id=row.tenant_id,
                priority=row.priority,
                dry_run=row.dry_run,
                created_at=row.created_at.isoformat() if row.created_at else "",
                agent_id=row.agent_id,
                workflow_mode=row.workflow_mode,
                execution_context=row.execution_context or {},
            )
            # A refresh must not orphan live SSE subscribers: subscribe_events
            # registers its queue on the cached record, and the Celery bridge /
            # _dispatch_event fan out via self._goals[goal_id].subscribers. Share
            # the same list object (and keep a running local task) so the queue
            # stays reachable after the DB copy replaces the cached record.
            previous = self._goals.get(row.id)
            if previous is not None and previous.tenant_id == record.tenant_id:
                record.subscribers = previous.subscribers
                if record.task is None:
                    record.task = previous.task
            self._goals[row.id] = record
            return record
        except Exception as exc:
            _svc_logger.warning("DB get goal failed: %s", exc)
            return None

    async def _db_update_goal_status(
        self,
        goal_id: str,
        tenant_id: str,
        status: str,
        error_message: str = "",
        iterations: int = 0,
        only_if_active: bool = False,
        raise_on_error: bool = False,
    ) -> bool:
        """Update goal status in PostgreSQL; return whether a row was changed.

        *only_if_active* leaves a row that is already terminal untouched (e.g. a
        worker reporting "cancelled" after a HITL rejection recorded "failed", or
        a cancel racing the worker's "complete") — the result is then ``False``.

        *raise_on_error* turns a failed write into ``ServiceUnavailableError``
        (503) for callers that report the transition to a user; background
        writers keep the log-and-continue behaviour (result ``False``).
        """
        if self._db is None:
            return True
        try:
            from datetime import datetime

            from sqlalchemy import update

            from app.db.models.goal import Goal
            from app.db.rls import sqlalchemy_rls_context

            values: dict[str, Any] = {"status": status, "iterations": iterations}
            if error_message:
                values["error_message"] = error_message
            if status == "complete":
                values["completed_at"] = datetime.now(UTC)
            stmt = update(Goal).where(Goal.id == goal_id, Goal.tenant_id == tenant_id)
            if only_if_active:
                stmt = stmt.where(Goal.status.notin_([s.value for s in _TERMINAL_STATUSES]))
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                result = await session.execute(stmt.values(**values))
            rowcount = getattr(result, "rowcount", None)
            return not isinstance(rowcount, int) or rowcount > 0
        except Exception as exc:
            _svc_logger.warning("DB update goal status failed: %s", exc)
            if raise_on_error:
                raise ServiceUnavailableError(
                    f"Goal {goal_id} status could not be persisted", cause=exc
                ) from exc
            return False

    async def _db_persist_step(
        self,
        goal_id: str,
        tenant_id: str,
        step_index: int,
        description: str,
        status: str,
        output: str,
    ) -> None:
        """Persist goal step to PostgreSQL."""
        if self._db is None:
            return
        try:
            import uuid as _uuid

            from app.db.models.goal import GoalStep
            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                s = GoalStep(
                    id=_uuid.uuid4().hex,
                    goal_id=goal_id,
                    tenant_id=tenant_id,
                    step_index=step_index,
                    description=description,
                    status=status,
                    output=output,
                )
                session.add(s)
        except Exception as exc:
            _svc_logger.warning("DB persist step failed: %s", exc)

    async def sync_from_db(self) -> int:
        """Load goals from PostgreSQL into memory on startup.

        Returns number of goals loaded.
        """
        if self._db is None:
            return 0
        try:
            from datetime import datetime, timedelta

            from sqlalchemy import select

            from app.db.models.goal import Goal
            from app.db.models.tenant import Tenant
            from app.db.rls import sqlalchemy_rls_context

            loaded = 0
            async with self._db() as session:
                tenant_result = await session.execute(
                    select(Tenant).where(Tenant.is_active == True)  # noqa: E712
                )
                tenants = tenant_result.scalars().all()

                # Warm a BOUNDED set of recent goals into memory. list_goals and
                # get_goal are DB-backed (get_goal falls back to _db_get_goal_record),
                # so this mirror is only a warm cache — never load the whole table,
                # which OOMs at millions of goals (distributed-scale audit X5). Cap
                # per tenant (most-recent first) and globally.
                per_tenant_warm = 500
                global_warm_cap = 50_000
                cutoff = datetime.now(UTC) - timedelta(hours=24)
                for tenant in tenants:
                    if loaded >= global_warm_cap:
                        break
                    tenant_id = str(tenant.id)
                    async with sqlalchemy_rls_context(session, tenant_id):
                        result = await session.execute(
                            select(Goal)
                            .where(
                                Goal.tenant_id == tenant_id,
                                Goal.created_at >= cutoff,
                            )
                            .order_by(Goal.created_at.desc())
                            .limit(per_tenant_warm)
                        )
                    goals = result.scalars().all()
                    for g in goals:
                        if g.id not in self._goals:
                            try:
                                _g_status = GoalStatus(g.status)
                            except ValueError:
                                _g_status = GoalStatus.PLANNING
                            record = GoalRecord(
                                goal_id=g.id,
                                goal_text=g.goal_text,
                                status=_g_status,
                                tenant_id=g.tenant_id,
                                priority=g.priority,
                                dry_run=g.dry_run,
                                created_at=g.created_at.isoformat() if g.created_at else "",
                                agent_id=g.agent_id,
                                workflow_mode=g.workflow_mode,
                                execution_context=g.execution_context or {},
                            )
                            self._goals[g.id] = record
                            loaded += 1

            _svc_logger.info("Synced %d recent goals from DB", loaded)
            # Restart recovery is NOT run here: it needs Redis (runner liveness,
            # worker locks), which the lifespan wires after this sync. The
            # lifespan calls recover_interrupted_goals() once it is.
            return loaded
        except Exception as exc:
            _svc_logger.warning("DB sync goals failed: %s", exc)
            return 0
