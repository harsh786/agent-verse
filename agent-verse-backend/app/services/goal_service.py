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
import inspect
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

# AGENT_PATTERN_FLAG_KEYS is re-exported: the worker imports it from here.
from app.agent.pattern_flags import AGENT_PATTERN_FLAG_KEYS, pattern_flags_from_record
from app.observability.logging import get_logger as _get_logger

_svc_logger = _get_logger(__name__)


# Module-level pause event registry (not a class attr to avoid circular)
_GOAL_PAUSE_EVENTS: dict[str, asyncio.Event] = {}
_PAUSE_POLL_SECONDS = 2.0


def _subgoal_queue_kwargs(execution_context: Any) -> dict[str, Any]:
    """``subgoal=True`` for a supervisor's sub-goal (else nothing, keeping the
    enqueue call shape): it runs on the dedicated sub-goal pool so a parent that
    holds a worker slot while waiting can never starve it (CORE-09)."""
    from app.agent.supervisor import SUBGOAL_MARKER

    ctx = execution_context if isinstance(execution_context, dict) else {}
    return {"subgoal": True} if ctx.get(SUBGOAL_MARKER) else {}


def _holds_concurrency_slot(execution_context: Any) -> bool:
    """False for a supervisor's sub-goal: it runs under its parent's concurrent-goal
    slot. Taking its own slot let a parent at the tenant's limit starve its own
    children (CORE-07). The fan-out is bounded (<= 6 sub-tasks, one level).
    SUBGOALS_SHARE_PARENT_SLOT=false (owner decision) gives sub-goals their own."""
    if not _subgoal_queue_kwargs(execution_context):
        return True
    return not subgoals_share_parent_slot()


def subgoals_share_parent_slot() -> bool:
    from app.core.config import get_settings

    return bool(getattr(get_settings(), "subgoals_share_parent_slot", True))


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
# Upper bound on how long a completion-time eval may be reported "pending" by
# other replicas; a marker left by a crashed scorer expires after this.
_EVAL_PENDING_TTL_SECONDS = 600
_REPLICA_HEARTBEAT_INTERVAL_SECONDS = 15
# Worker per-goal execution lock (scaling/tasks.py _SyncGoalLock.KEY_PREFIX).
_WORKER_GOAL_LOCK_KEY = "goal_lock:{goal_id}"
# Events that produce usage records (see app/services/usage_metering.py).
_METERED_EVENT_TYPES = {"tool_call_complete", "goal_complete", "goal_failed", "goal_cancelled"}
from app.agent.sanitization import sanitize_event
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.agent.tool_context import ToolContext
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
from app.services.failure_reason import public_failure_reason, terminal_reason_code
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
    """Whether Grantex tool-grant enforcement is on (default ON).

    An unreadable setting enforces (GRANT-07: it answered False, so a settings
    or import error silently turned enforcement off on the API and the worker).
    """
    try:
        from app.core.config import get_settings

        return bool(getattr(get_settings(), "enforce_agent_grants", True))
    except Exception:
        return True

# TTL for completed/failed/cancelled goals in the in-memory cache.
# They are safe to evict because they are already persisted in the DB.
_COMPLETED_GOAL_TTL_SECONDS = 3600  # 1 hour
# Delay before a crashed background Redis subscriber is restarted.
_SUBSCRIBER_RESTART_DELAY_S = 5.0
_EVICTION_INTERVAL_SECONDS = 60  # evict at most once every 60 seconds
# Hard cap on cached GoalRecords per replica (SVC-30): Postgres is the source of
# truth, so read traffic over many distinct goals must not grow the heap. Records
# with a live local task or SSE subscribers are never dropped by the cap.
_MAX_CACHED_GOALS = 5_000
# An idle goal stream yields a heartbeat marker this often (the SSE endpoint
# turns it into a ": ping" comment and checks for a disconnected client).
_SSE_HEARTBEAT_SECONDS = 15.0
SSE_HEARTBEAT_TYPE = "_sse_heartbeat"
# Events per keyset page when a stream replays the durable history (SVC-05).
_REPLAY_PAGE_SIZE = 500
# Live-only event types: never stored, no sequence, never treated as duplicates.
_EPHEMERAL_EVENT_TYPES = frozenset({"token_chunk", "heartbeat", SSE_HEARTBEAT_TYPE})


def _as_seq(value: Any) -> int | None:
    """A durable event sequence (``_seq``), or ``None`` when *value* is not one."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


class _DeliveredEvents:
    """What one goal stream has delivered, so replay and live never overlap (SVC-05).

    A sequenced event is a duplicate when its ``_seq`` is at or below the resume
    point or was delivered already. ``floor`` advances over the contiguous run
    of delivered sequences, so the set holds only out-of-order ones (bounded).
    An event without a sequence (its append failed) is a duplicate only of an
    identical one the replay delivered: each replayed copy cancels one live copy.
    """

    def __init__(self, since_sequence: int) -> None:
        self.floor = max(since_sequence, 0)
        self._above: set[int] = set()
        self._replayed_unsequenced: dict[str, int] = {}

    def fresh(self, event: dict[str, Any], *, replay: bool = False) -> bool:
        seq = _as_seq(event.get("_seq"))
        if seq is not None:
            if seq <= self.floor or seq in self._above:
                return False
            self._above.add(seq)
            while self.floor + 1 in self._above:
                self.floor += 1
                self._above.discard(self.floor)
            return True
        if event.get("type") in _EPHEMERAL_EVENT_TYPES:
            return True
        key = GoalService._event_key(event)
        if replay:
            self._replayed_unsequenced[key] = self._replayed_unsequenced.get(key, 0) + 1
            return True
        pending = self._replayed_unsequenced.get(key, 0)
        if pending:
            self._replayed_unsequenced[key] = pending - 1
            return False
        return True


def _row_completed_at(row: Any, status: GoalStatus) -> str | None:
    """completed_at for a record built from a goals row (terminal rows only)."""
    if status not in (GoalStatus.COMPLETE, GoalStatus.FAILED, GoalStatus.CANCELLED):
        return None
    ts = getattr(row, "completed_at", None) or getattr(row, "updated_at", None)
    return ts.isoformat() if ts is not None else None


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
    from app.providers.llm_resolution import FAKE_LLM_ENVIRONMENTS

    # BYOK-3: canned answers only in an explicit development/test environment
    # (it used to be every ENVIRONMENT except exactly "production").
    _env = str(getattr(_get_cfg(), "environment", "development") or "")
    if _env.strip().lower() not in FAKE_LLM_ENVIRONMENTS:
        raise RuntimeError(
            f"Cannot use FakeProvider outside development/test (ENVIRONMENT={_env!r}). "
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

    It must not be a goal-submission dedup (an async ``get_existing`` /
    ``register`` API without ``is_duplicate``/``mark_seen``): wiring the old
    ``RedisDeduplicationCache`` (since removed, a08-F192-02) into this slot made
    every goal crash on its first step whenever Redis was available.
    Cross-replica *goal-submission* dedup is handled separately by
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
    # POST /goals workflow_mode=supervisor: the in-graph fan-out width.
    "supervisor_max_parallel",
    # POST /goals workflow_mode=debate: the in-graph debate's round count.
    "debate_rounds",
)


# Agent-config reasoning-pattern flags the graph is compiled with. Snapshotted on
# the goal at submission (execution_context["agent_pattern_flags"]) so the Celery
# worker — which has no in-memory agent store — builds the same graph.
def _resolved_pattern_flags(
    agent_config: Any, execution_context: dict[str, Any] | None
) -> dict[str, bool]:
    """All pattern flags for the graph: submission snapshot over agent config."""
    flags = dict.fromkeys(AGENT_PATTERN_FLAG_KEYS, False)
    flags.update(pattern_flags_from_record(agent_config))
    snapshot = (execution_context or {}).get("agent_pattern_flags")
    if isinstance(snapshot, dict):
        flags.update({k: bool(v) for k, v in snapshot.items() if k in flags})
    return flags


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



_DOWNGRADE_DETAIL = {
    "strategy_runtime_v2_not_enabled": (
        "the strategy runtime v2 is not enabled for this tenant; set "
        "STRATEGY_RUNTIME_V2_TENANT_ALLOWLIST to the tenant id (or '*') to run "
        "explicit strategies"
    ),
    "strategy_runtime_v2_kill_switch": (
        "STRATEGY_RUNTIME_V2_KILL_SWITCH is on: explicit strategies are refused"
    ),
    "invalid_strategy_override": "the requested strategy cannot run as a goal",
    "profile_build_failed": "the runtime profile could not be built",
}


# POST /goals workflow modes that are strategies of their own: on the strategy
# runtime v2 they become the profile's primary strategy (P5-2).
_PROFILE_WORKFLOW_MODES = frozenset({"supervisor", "debate"})


def _strategy_downgrade(
    goal_id: str, tenant_id: str, requested: Any, reason: str
) -> dict[str, Any]:
    """Execution-context marks + a warning for an override that will not run."""
    detail = _DOWNGRADE_DETAIL.get(reason, reason)
    _svc_logger.warning(
        "strategy_override_downgraded",
        goal_id=goal_id,
        tenant_id=tenant_id,
        requested_strategy=str(requested),
        reason=reason,
        runs="legacy_kernel",
        detail=detail,
    )
    return {
        "strategy_downgraded": True,
        "strategy_downgrade": {
            "requested_strategy": str(requested),
            "reason": reason,
            "runs": "legacy_kernel",
            "detail": detail,
        },
    }


def _downgrade_fields(execution_context: dict[str, Any] | None) -> dict[str, Any]:
    ctx = execution_context if isinstance(execution_context, dict) else {}
    downgraded = bool(ctx.get("strategy_downgraded"))
    return {
        "strategy_downgraded": downgraded,
        "strategy_downgrade": ctx.get("strategy_downgrade") if downgraded else None,
    }


def _routing_outcome(decision: Any) -> dict[str, Any]:
    """A router decision as the JSON-safe dict recorded on the goal."""
    to_dict = getattr(decision, "to_dict", None)
    out = to_dict() if callable(to_dict) else None
    if isinstance(out, dict):
        return out
    try:
        confidence = float(getattr(decision, "confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    agent_id = getattr(decision, "agent_id", None)
    return {
        "agent_id": str(agent_id) if agent_id else None,
        "reason": str(getattr(decision, "reason", "") or ""),
        "confidence": round(confidence, 3),
        "mode": str(getattr(decision, "mode", "single_agent") or "single_agent"),
        "candidate_agents": list(getattr(decision, "candidate_agents", None) or []),
    }


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
        # HITL rejection subscriber: strongly held (a bare create_task was
        # garbage-collected while running), restarted if it exits, cancelled by
        # stop_background_subscribers() on shutdown.
        self._hitl_rejection_task: asyncio.Task[None] | None = None
        self._subscribers_stopping = False
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
        """Start (once) the supervised HITL rejection note subscriber task."""
        current = self._hitl_rejection_task
        if current is not None and not current.done():
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            _svc_logger.warning("hitl_rejection_subscriber_no_loop")
            return
        self._subscribers_stopping = False
        self._spawn_hitl_rejection_task(redis_url)
        _svc_logger.info("hitl_rejection_subscriber_scheduled", redis_url=redis_url[:30])

    def _spawn_hitl_rejection_task(self, redis_url: str) -> None:
        task = asyncio.get_running_loop().create_task(
            self._subscribe_hitl_rejections(redis_url), name="hitl-rejection-subscriber"
        )
        self._hitl_rejection_task = task
        self._background_tasks.add(task)

        def _on_done(done: asyncio.Task[None]) -> None:
            self._background_tasks.discard(done)
            if self._subscribers_stopping or done.cancelled():
                return
            exc = done.exception()
            _svc_logger.warning(
                "hitl_rejection_subscriber_exited_restarting",
                error=str(exc) if exc else "returned",
            )
            # Restart after a short delay, still strongly held.
            restart = asyncio.get_running_loop().create_task(
                self._restart_hitl_rejection_subscriber(redis_url),
                name="hitl-rejection-subscriber-restart",
            )
            self._background_tasks.add(restart)
            restart.add_done_callback(self._background_tasks.discard)

        task.add_done_callback(_on_done)

    async def _restart_hitl_rejection_subscriber(self, redis_url: str) -> None:
        await asyncio.sleep(_SUBSCRIBER_RESTART_DELAY_S)
        if not self._subscribers_stopping:
            self._spawn_hitl_rejection_task(redis_url)

    async def stop_background_subscribers(self) -> None:
        """Shutdown: cancel the Redis subscribers (HITL rejections, Celery bridge)."""
        self._subscribers_stopping = True
        tasks = [t for t in self._background_tasks if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        self._hitl_rejection_task = None

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
                                # Only goals this replica already holds a record
                                # of (a local run, or an SSE subscriber here). A
                                # stub record per unknown goal made every replica
                                # track every worker goal fleet-wide, unbounded
                                # (SVC-07); nobody here listens to those events.
                                record = self._goals.get(goal_id)
                                if record is not None and record.tenant_id != tenant_id:
                                    record = None
                                if record is not None:
                                    # Feed into SSE subscriber queues
                                    event = {
                                        "type": event_type,
                                        "payload": payload,
                                        "goal_id": goal_id,
                                        "tenant_id": tenant_id,
                                    }
                                    # SVC-05: the worker's durable sequence (SSE id).
                                    _bridge_seq = _as_seq(data.get("_seq")) or (
                                        _as_seq(payload.get("_seq"))
                                        if isinstance(payload, dict)
                                        else None
                                    )
                                    if _bridge_seq is not None:
                                        event["_seq"] = _bridge_seq
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
                                        if _final in _TERMINAL_STATUSES:
                                            # Starts its eviction TTL (SVC-30).
                                            record.completed_at = (
                                                record.completed_at
                                                or datetime.now(UTC).isoformat()
                                            )
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
        now_iso = datetime.now(UTC).isoformat()
        for goal_id, record in self._goals.items():
            if record.status in terminal and not self._is_pinned(record):
                completed_at = getattr(record, "completed_at", None)
                if not completed_at or not isinstance(completed_at, str):
                    # No timestamp (bridge stub, legacy load): start its TTL now.
                    # Skipping it forever is how these records piled up.
                    record.completed_at = now_iso
                    continue
                try:
                    dt = datetime.fromisoformat(completed_at.rstrip("Z"))
                    if dt.replace(tzinfo=UTC) < cutoff:
                        to_evict.append(goal_id)
                except Exception:
                    to_evict.append(goal_id)
        for gid in to_evict:
            del self._goals[gid]
            _GOAL_PAUSE_EVENTS.pop(gid, None)
        self._sweep_pause_events()
        return len(to_evict)

    async def _evict_async(self) -> int:
        """Async wrapper so eviction runs in the event loop without thread race."""
        return self._evict_stale_goals()

    @staticmethod
    def _is_pinned(record: GoalRecord) -> bool:
        """A record this replica must keep: a live local task or SSE subscribers."""
        task = record.task
        return bool(record.subscribers) or (task is not None and not task.done())

    def _cache_goal(self, record: GoalRecord) -> None:
        """Cache *record* as most-recently used, then keep the cache bounded:
        TTL eviction at most every _EVICTION_INTERVAL_SECONDS (it used to run
        only on submit), and an LRU cap of _MAX_CACHED_GOALS unpinned records.
        """
        self._goals.pop(record.goal_id, None)
        self._goals[record.goal_id] = record
        now = time.monotonic()
        if now - self._last_eviction_time > _EVICTION_INTERVAL_SECONDS:
            self._last_eviction_time = now
            self._evict_stale_goals()
        excess = len(self._goals) - _MAX_CACHED_GOALS
        if excess <= 0 or self._db is None:
            # Without a DB this map IS the goal store: never drop by size.
            return
        for gid in list(self._goals):
            if excess <= 0:
                break
            if gid == record.goal_id or self._is_pinned(self._goals[gid]):
                continue
            del self._goals[gid]
            _GOAL_PAUSE_EVENTS.pop(gid, None)
            excess -= 1

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
                **_subgoal_queue_kwargs(record.execution_context),
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

        # The patterns the runtime actually runs are also the goal's patterns_used
        # column (it used to hold the requested profile, even on the legacy path).
        patterns: list[str] | None = None
        if key == "strategy_execution" and isinstance(value, dict):
            patterns = [str(p) for p in value.get("patterns") or ()]
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
                if patterns is not None:
                    await session.execute(
                        _sql(
                            "UPDATE goals SET patterns_used = CAST(:p AS jsonb) "
                            "WHERE id = :g AND tenant_id = :t"
                        ),
                        {"p": json.dumps(patterns), "g": goal_id, "t": tenant_id},
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
            from app.providers.llm_resolution import fake_llm_allowed, no_provider_message

            if not fake_llm_allowed():
                # Never fabricate a "successful" goal outside development/test
                # (it used to be every ENVIRONMENT except exactly "production").
                raise RuntimeError(
                    f"{no_provider_message(tenant_ctx.tenant_id)}; refusing to simulate "
                    "goal execution"
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
                    # The tenant's own pins outrank the deployment-wide reasoning
                    # order (MR-6); the role map keeps them for fallback chains.
                    if hasattr(_model_router, "set_policy_roles"):
                        _model_router.set_policy_roles(_policy_roles)
            except Exception as _tp_exc:
                _svc_logger.warning("tenant_routing_policy_apply_failed", error=str(_tp_exc))

        # Apply model override to the model router before building the graph
        if _model_override:
            if _model_router is None:
                from app.agent.model_router import ModelRouter

                _model_router = ModelRouter()
            try:
                _model_router = _model_router.with_override(_model_override)  # copy-on-write
                if hasattr(_model_router, "set_plan_tier"):  # PROV-18: plan caps the pin
                    _model_router.set_plan_tier(tenant_ctx.plan)
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

        # Always wire a circuit breaker for the LLM provider (the worker graph
        # gets the same one: build_llm_circuit_breaker).
        from app.reliability.redis_circuit_breaker import build_llm_circuit_breaker

        _circuit_breakers["llm"] = build_llm_circuit_breaker(_redis_for_cb, tenant_ctx.tenant_id)

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
            # Every reasoning-pattern flag — passed at construction, since _build()
            # compiles the LangGraph inside __init__ — from the goal's submission
            # snapshot (DB-read, so right on any replica) over the local agent
            # config, which on a stale replica lacked them (CORE-04).
            **_resolved_pattern_flags(_agent_config, execution_context),
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
        # The agent's own wall-clock budget; the runner caps the goal at
        # min(plan goal_timeout_seconds, this) (it used to be stored and ignored).
        graph._agent_timeout_seconds = _agent_config.get("timeout_seconds")
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
        workflow_mode: str | None = None,
    ) -> dict[str, Any]:
        """Build the goal's GoalRuntimeProfile and decide whether it drives execution.

        ``workflow_mode`` ``supervisor`` / ``debate`` (POST /goals) becomes the
        profile's primary strategy unless an explicit ``strategy_override`` names
        another one (P5-2). It used to set only the legacy agent pattern flags,
        which the v2 GraphFactory may switch off but never on — the goal silently
        ran as plain ReAct. When the profile cannot host the mode (no coordination
        runtime) the legacy kernel runs it from the pattern flags, recorded as a
        ``runtime_profile_fallback``; a conflict with an explicit override is
        recorded as a downgrade.

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
        mode = workflow_mode if workflow_mode in _PROFILE_WORKFLOW_MODES else None
        mode_conflict = bool(mode and requested_primary and str(requested_primary) != mode)
        try:
            from app.orchestration.runtime_profile_builder import RuntimeProfileBuilder

            builder = RuntimeProfileBuilder(llm_provider=self._classifier_provider())
            _builder_config = dict(agent_config or {})
            if mode and not requested_primary:
                _builder_config["primary_strategy"] = mode
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
            if mode and not requested_primary:
                # The legacy kernel runs the mode from the goal's pattern flags.
                fallback["requested_workflow_mode"] = mode
            context_fb: dict[str, Any] = {
                "runtime_profile_fallback": fallback,
                "strategy_runtime_path": "legacy",
            }
            if requested_primary:
                fallback["requested_primary"] = str(requested_primary)
                context_fb.update(
                    _strategy_downgrade(goal_id, tenant_ctx.tenant_id, requested_primary, reason)
                )
            return {"profile_object": None, "context": context_fb, "columns": None}

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
            context.update(
                _strategy_downgrade(
                    goal_id,
                    tenant_ctx.tenant_id,
                    requested_primary,
                    context["runtime_profile_fallback"]["reason"],
                )
            )
        if mode_conflict and rollout.path == "v2":
            # The explicit override drives the v2 profile; the mode cannot co-run.
            detail = (
                f"workflow_mode '{mode}' cannot run alongside strategy_override "
                f"'{requested_primary}'; the override runs"
            )
            _svc_logger.warning(
                "workflow_mode_downgraded",
                goal_id=goal_id,
                tenant_id=tenant_ctx.tenant_id,
                requested_mode=mode,
                runs=str(requested_primary),
            )
            context["strategy_downgraded"] = True
            context["strategy_downgrade"] = {
                "requested_strategy": mode,
                "reason": "workflow_mode_conflicts_with_strategy_override",
                "runs": str(requested_primary),
                "detail": detail,
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
            # Nothing has run yet: patterns_used is filled from strategy_execution
            # (what the runtime actually runs — see _db_merge_context_key). It used
            # to be the REQUESTED profile for every goal, legacy-path ones included,
            # so reports claimed patterns that never ran (CORE-21). The request
            # itself stays in runtime_profile_snapshot.
            "patterns_used": [],
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

    async def _refresh_tenant_policies(self, tenant_ctx: TenantContext) -> None:
        """Load/refresh the tenant's governance policies before an in-process run.

        The engine on ``app.state`` is shared; its tenant slice is (re)loaded under
        the tenant's RLS context when missing or stale (POL-04 / POL-05). A first
        load that fails leaves a deny-all policy for the tenant (fail closed).
        """
        app_state = getattr(self._app_state, "state", self._app_state)
        engine = getattr(app_state, "policy_engine", None) if app_state is not None else None
        ensure = getattr(engine, "ensure_tenant_loaded", None)
        if ensure is None or self._db is None:
            return
        await ensure(self._db, tenant_ctx.tenant_id)

    async def active_goal_ids(
        self, tenant_ctx: TenantContext, *, limit: int | None = None
    ) -> list[str]:
        """Every non-terminal goal of the tenant — on ANY replica or worker.

        *limit* bounds the answer (the emergency stop cancels one bounded page
        inline and hands the rest to a keyset-batched worker task).

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
                        select(Goal.id)
                        .where(
                            Goal.tenant_id == tenant_ctx.tenant_id,
                            Goal.status.notin_(terminal),
                        )
                        .order_by(Goal.id)
                        .limit(limit)
                    )
                ).scalars().all()
            ids.update({str(r): None for r in rows})
        out = list(ids)
        return out if limit is None else out[:limit]

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

    def _routing_agent_store(self) -> Any:
        """The durable, tenant-scoped agent source auto-routing reads (RV-02).

        The wired store when it is DB-backed; otherwise, with a database (the
        Celery worker's GoalService has no app state), a DB-backed AgentStore
        whose bounded candidate query runs under the tenant's RLS. Without a
        database, the wired (in-memory) store.
        """
        store = self._get_agent_store()
        if store is not None and getattr(store, "_db", None) is not None:
            return store
        if self._db is not None:
            from app.api.agents import AgentStore

            return AgentStore(db_session_factory=self._db)
        return store

    async def _auto_route_goal(
        self, goal: str, tenant_ctx: TenantContext
    ) -> tuple[str | None, dict[str, Any] | None]:
        """``(agent_id, routing outcome)`` for a goal submitted without an agent.

        ``(None, None)`` when there is nothing to route over. A router error is
        logged and retried once with keyword scoring over the same bounded
        candidates; when that fails too the outcome says ``routing_failed``
        with both errors -- never a silent "no agent".
        """
        from app.agent.router import AgentRouter

        store = self._routing_agent_store()
        router = (
            getattr(self._app_state, "agent_router", None)
            if self._app_state is not None
            else None
        )
        if router is None:
            if store is None:
                return None, None
            router = AgentRouter(agent_store=store, db_session_factory=self._db)
        try:
            decision = await router.route(goal=goal, tenant_ctx=tenant_ctx)
        except Exception as exc:
            router_error = f"{type(exc).__name__}: {str(exc)[:160]}"
            _svc_logger.warning(
                "agent_router_failed", tenant_id=tenant_ctx.tenant_id, error=router_error
            )
            return await self._fallback_route(goal, tenant_ctx, store, router_error)
        outcome = _routing_outcome(decision)
        agent_id = outcome.get("agent_id") or None
        if agent_id:
            _svc_logger.info(
                "auto_routed_goal",
                tenant_id=tenant_ctx.tenant_id,
                agent_id=agent_id,
                confidence=outcome.get("confidence"),
            )
        else:
            _svc_logger.info(
                "auto_route_no_agent",
                tenant_id=tenant_ctx.tenant_id,
                reason=outcome.get("reason"),
            )
        return (str(agent_id) if agent_id else None), outcome

    async def _fallback_route(
        self, goal: str, tenant_ctx: TenantContext, store: Any, router_error: str
    ) -> tuple[str | None, dict[str, Any]]:
        """Keyword/connector scoring over the store's bounded candidates."""
        from app.agent.router import AgentRouter

        failed: dict[str, Any] = {
            "agent_id": None,
            "reason": "routing_failed",
            "confidence": 0.0,
            "mode": "single_agent",
            "candidate_agents": [],
            "router_error": router_error,
        }
        if store is None:
            return None, failed
        fallback = AgentRouter(agent_store=store)
        try:
            agents = await fallback.candidates(goal, tenant_ctx)
        except Exception as exc:
            failed["fallback_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
            _svc_logger.warning(
                "agent_routing_fallback_failed",
                tenant_id=tenant_ctx.tenant_id,
                error=failed["fallback_error"],
            )
            return None, failed
        decision = fallback.keyword_decision(goal, agents)
        outcome = decision.to_dict()
        outcome["router_error"] = router_error
        if decision.agent_id:
            outcome["reason"] = "routed_fallback"
            _svc_logger.info(
                "auto_routed_fallback",
                tenant_id=tenant_ctx.tenant_id,
                agent_id=decision.agent_id,
                score=round(decision.confidence, 3),
            )
        return decision.agent_id, outcome

    async def _submit_multi_agent_routing(
        self,
        goal: str,
        routing: dict[str, Any],
        tenant_ctx: TenantContext,
        *,
        priority: str,
        dry_run: bool,
    ) -> dict[str, Any] | None:
        """Fan a ``multi_agent`` routing decision out to up to 3 agents, else None."""
        if routing.get("mode") != "multi_agent":
            return None
        tasks = [
            self._submit_single_goal(
                goal=goal,
                agent_id=str(cand["agent_id"]),
                tenant_ctx=tenant_ctx,
                priority=priority,
                dry_run=dry_run,
            )
            for cand in (routing.get("candidate_agents") or [])[:3]
            if isinstance(cand, dict) and cand.get("agent_id")
        ]
        if len(tasks) < 2:
            return None
        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid = [r for r in results if isinstance(r, dict) and "goal_id" in r]
        if not valid:
            return None
        return {
            "mode": "multi_agent",
            "goal_ids": [r["goal_id"] for r in valid],
            "primary_goal_id": valid[0]["goal_id"],
            "goal_id": valid[0]["goal_id"],
            "agents": [r.get("agent_id") for r in valid],
        }

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

    async def _aget_read_record(self, goal_id: str, tenant_ctx: TenantContext) -> GoalRecord:
        """This replica's record if it holds one, else the goal from Postgres (RLS).

        For read-only views (eval scorecard / suggestions / on-demand scoring):
        a goal submitted to another replica, run by a worker, evicted after
        completion or predating a restart used to 404 here although Postgres
        had it. Unlike :meth:`_aget_record` an in-memory record is kept as is
        (it carries this replica's recorded events).
        """
        record = self._goals.get(goal_id)
        if record is not None and record.tenant_id == tenant_ctx.tenant_id:
            return record
        loaded = await self._db_get_goal_record(goal_id, tenant_ctx)
        if loaded is None:
            raise NotFoundError(f"Goal not found: {goal_id}")
        return loaded

    # ── Eval scoring helpers ──────────────────────────────────────────────────

    def _eval_app_state(self) -> Any:
        """``app.state`` for the real Starlette app; a mock/plain object as is."""
        aps: Any = self._app_state
        try:
            from starlette.applications import Starlette as _Starlette

            if isinstance(aps, _Starlette):
                return aps.state
        except Exception:
            pass
        return aps

    @staticmethod
    def _eval_provider(aps: Any) -> Any:
        """The platform provider for eval judgements, or None (heuristic scoring).

        The canned FakeProvider is the no-key fallback: its replies are not
        judgements, so scoring with it would dress defaults up as LLM scores.
        """
        provider = getattr(aps, "_app_provider", None) if aps is not None else None
        if provider is None or isinstance(provider, FakeProvider):
            return None
        return provider

    @staticmethod
    def _eval_steps_from_events(events: list[dict[str, Any]]) -> tuple[list[StepResult], bool]:
        """Planned steps and the verifier's verdict, as recorded in the goal's events."""
        steps: list[StepResult] = []
        verification_success = False
        for evt in events:
            if evt.get("type") == "plan_ready":
                steps.extend(
                    StepResult(description=str(step_text), status=StepStatus.COMPLETE)
                    for step_text in evt.get("steps", [])
                )
            elif evt.get("type") == "verification_done":
                verification_success = bool(evt.get("success", False))
        return steps, verification_success

    # ── Eval-scoring "pending" marker, shared across replicas ────────────────

    @staticmethod
    def _eval_pending_key(goal_id: str, tenant_id: str) -> str:
        return f"eval_pending:{tenant_id}:{goal_id}"

    async def _mark_eval_pending(self, goal_id: str, tenant_id: str) -> None:
        """Record that *goal_id* is being scored — locally and in Redis (TTL'd).

        The TTL bounds a marker left behind by a crashed scorer. Redis failures
        only lose the cross-replica hint (other replicas then report
        "not_evaluated" until the scorecard lands); scoring itself proceeds.
        """
        self._eval_pending.add(goal_id)
        redis = getattr(self, "_redis", None)
        if redis is None:
            return
        try:
            await redis.set(
                self._eval_pending_key(goal_id, tenant_id), "1", ex=_EVAL_PENDING_TTL_SECONDS
            )
        except Exception as exc:
            _svc_logger.warning("eval_pending_mark_failed", goal_id=goal_id, error=str(exc)[:120])

    async def _clear_eval_pending(self, goal_id: str, tenant_id: str) -> None:
        self._eval_pending.discard(goal_id)
        redis = getattr(self, "_redis", None)
        if redis is None:
            return
        try:
            await redis.delete(self._eval_pending_key(goal_id, tenant_id))
        except Exception as exc:
            _svc_logger.warning("eval_pending_clear_failed", goal_id=goal_id, error=str(exc)[:120])

    async def _is_eval_pending(self, goal_id: str, tenant_id: str) -> bool:
        if goal_id in self._eval_pending:
            return True
        redis = getattr(self, "_redis", None)
        if redis is None:
            return False
        try:
            return bool(await redis.exists(self._eval_pending_key(goal_id, tenant_id)))
        except Exception as exc:
            _svc_logger.warning("eval_pending_read_failed", goal_id=goal_id, error=str(exc)[:120])
            return False

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

    async def _refresh_agent_record(self, agent_id: str | None, tenant_ctx: TenantContext) -> None:
        """Reload the agent's config from Postgres into the agent store's cache.

        The goal path reads the agent (system_prompt, model_override,
        connectors ...) from the store's per-replica cache, which only
        ``sync_from_db`` at startup filled and which never picked up an UPDATE
        made elsewhere — a self-optimizer winner or an edit handled by another
        replica reached this replica only after a restart. ``get_async`` reads
        the row under RLS and refreshes the cache; on a DB error it keeps the
        cached copy (and logs), so the goal still runs.
        """
        if agent_id is None:
            return
        store = self._get_agent_store()
        get_async = getattr(store, "get_async", None) if store is not None else None
        if get_async is None or not inspect.iscoroutinefunction(get_async):
            return
        try:
            await get_async(agent_id, tenant_ctx=tenant_ctx)
        except Exception as exc:
            _svc_logger.warning("agent_refresh_failed", agent_id=agent_id, error=str(exc)[:200])

    async def _validate_agent_id(
        self, agent_id: str | None, tenant_ctx: TenantContext
    ) -> dict[str, Any] | None:
        """Raise NotFoundError for an unknown agent; return its record otherwise.

        Reads the DB-backed store when it offers ``get_async``: this replica's
        cache misses agents created on another replica (they were rejected as
        unknown, and their pattern flags were silently lost — CORE-04).
        """
        if agent_id is None:
            return None
        agent_store = self._get_agent_store()
        if agent_store is None:
            return None
        getter = getattr(agent_store, "get_async", None)
        if getter is not None and inspect.iscoroutinefunction(getter):
            record = await getter(agent_id, tenant_ctx=tenant_ctx)
        else:
            record = agent_store.get(agent_id, tenant_ctx=tenant_ctx)
        if record is None:
            raise NotFoundError(f"Agent not found: {agent_id}")
        return record if isinstance(record, dict) else None

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
        """The planner's tools — the same builder as the Celery worker.

        ``app.agent.tool_context_builder.build_goal_tool_context``: the agent's
        connectors (``auto_approve`` carried), or the tenant's connectors when
        the goal has no agent; unreachable connectors are recorded, not fatal;
        RPA tools only where Playwright runs; tiered ToolSelector when wired.
        """
        from app.agent.tool_context_builder import build_goal_tool_context

        mcp_client = self._get_mcp_client()
        registry = getattr(mcp_client, "_registry", None) or getattr(
            self._app_state, "mcp_registry", None
        )
        if mcp_client is None:
            # No connector runtime wired (bare test app): built-ins only.
            from app.agent.tool_context_builder import rpa_tools_available
            from app.rpa.tools import rpa_tool_refs

            return ToolContext(
                connectors=[], tools=rpa_tool_refs() if rpa_tools_available() else []
            )

        agent: dict[str, Any] | None = None
        connector_ids: list[str] | None = None
        if agent_id is not None:
            agent_store = self._get_agent_store()
            agent = (
                agent_store.get(agent_id, tenant_ctx=tenant_ctx)
                if agent_store is not None
                else None
            )
            # The agent's connectors; an agent without any is treated like a goal
            # without an agent (the tenant's connectors), exactly as the worker
            # does for a queued goal whose connector_ids are empty.
            connector_ids = [str(c) for c in (agent or {}).get("connector_ids", [])] or None

        return await build_goal_tool_context(
            registry=registry,
            mcp_client=mcp_client,
            tenant_ctx=tenant_ctx,
            connector_ids=connector_ids,
            goal=goal,
            tool_selector=getattr(self._app_state, "tool_selector", None),
            agent=agent,
        )

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
    ) -> int | None:
        """Persist *event*; return its durable sequence (``None`` when not stored)."""
        if self._event_store is None:
            return None
        ctx = self._tenant_ctx_for_event_store(record, tenant_ctx)
        try:
            seq = await self._event_store.append_event(goal_id, event, tenant_ctx=ctx)
        except Exception as exc:
            # Never silently dropped from the durable stream: park it in the
            # Redis outbox, which the drain-goal-event-outbox beat task replays.
            _svc_logger.warning("DB persist goal event failed, buffering: %s", exc)
            from app.services.event_store import buffer_failed_event_via_settings

            await buffer_failed_event_via_settings(
                tenant_id=ctx.tenant_id,
                goal_id=goal_id,
                event=event,
                redis=getattr(self, "_redis", None),
            )
            return None
        return _as_seq(seq)

    async def _list_persisted_events(
        self, goal_id: str, tenant_ctx: TenantContext
    ) -> list[dict[str, Any]]:
        if self._event_store is None:
            return []
        # A store error raises ServiceUnavailableError (503): an outage used to
        # replay as an empty history.
        events = await self._event_store.list_events(goal_id, tenant_ctx=tenant_ctx)
        return cast("list[dict[str, Any]]", events)

    async def _list_events_since_persisted(
        self,
        goal_id: str,
        after_sequence: int,
        tenant_ctx: TenantContext,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return one page of persisted events after *after_sequence* (with ``_seq``)."""
        if self._event_store is None:
            return []
        # Errors raise ServiceUnavailableError (503), never an empty history.
        events = await self._event_store.list_events_since(
            goal_id,
            after_sequence=after_sequence,
            limit=limit or _REPLAY_PAGE_SIZE,
            tenant_ctx=tenant_ctx,
        )
        return cast("list[dict[str, Any]]", events)

    async def _persisted_events_after(
        self, goal_id: str, after_sequence: int, tenant_ctx: TenantContext
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Every persisted event after *after_sequence*, oldest first, keyset-paged
        (SVC-05: resume used to stop after the first 100 events)."""
        cursor = after_sequence
        page_size = _REPLAY_PAGE_SIZE
        while True:
            page = await self._list_events_since_persisted(
                goal_id, after_sequence=cursor, tenant_ctx=tenant_ctx, limit=page_size
            )
            advanced = False
            for event in page:
                seq = _as_seq(event.get("_seq"))
                if seq is not None and seq > cursor:
                    cursor = seq
                    advanced = True
                yield event
            # A short page is the end; a page that did not move the cursor would
            # repeat forever, so it ends the read too.
            if len(page) < page_size or not advanced:
                return

    async def _replay_events(
        self,
        goal_id: str,
        record: GoalRecord | None,
        tenant_ctx: TenantContext,
        since_sequence: int,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """A stream's replay: every event after *since_sequence*, each with its
        ``_seq``. From the durable store; a local record on a replica without
        one replays from memory (its position is its sequence). Raises
        ServiceUnavailableError when the store cannot be read."""
        if self._event_store is None and record is not None:
            for position, event in enumerate(list(record.events), start=1):
                seq = _as_seq(event.get("_seq")) or position
                if seq > since_sequence:
                    yield event if "_seq" in event else {**event, "_seq": seq}
            return
        async for event in self._persisted_events_after(goal_id, since_sequence, tenant_ctx):
            yield event
        if since_sequence == 0 and record is not None:
            # Events this replica holds that never reached the store (their
            # append failed; they wait in the outbox) are still part of the
            # history a full replay shows. They have no sequence to resume by.
            for event in list(record.events):
                if "_seq" not in event:
                    yield event

    @staticmethod
    def _event_key(event: dict[str, Any]) -> str:
        # ``_seq`` is the delivery cursor, not content: the stored payload has
        # none while the live / in-memory copy of the same event does.
        if "_seq" in event:
            event = {k: v for k, v in event.items() if k != "_seq"}
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
    def _normalize_bus_event(message: Any) -> dict[str, Any]:
        """A goal event read from ``goal_events:*``: workers publish an envelope
        ``{goal_id, tenant_id, type, payload: <event>}``, the API publishes the
        event itself. Always return the event (keeping a ``_seq`` from either)."""
        if not isinstance(message, dict):
            return {"type": "unknown"}
        payload = message.get("payload")
        if (
            isinstance(payload, dict)
            and "goal_id" in message
            and payload.get("type", message.get("type")) == message.get("type")
        ):
            event = dict(payload)
            event.setdefault("type", message.get("type", ""))
            if "_seq" in message and "_seq" not in event:
                event["_seq"] = message["_seq"]
            return event
        return message

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
    ) -> GoalRecord | None:
        """The goal as persisted, or ``None`` when its row was deleted (erasure).

        With a task queue a (non-dry-run) goal's row is written synchronously at
        submit, so a missing row means the goal was erased: it used to be served
        on from this process's memory after a data-subject erasure deleted it.
        """
        if not self._should_refresh_goal_from_db(record):
            return record
        persisted = await self._db_get_goal_record(record.goal_id, tenant_ctx)
        if persisted is None:
            if self._task_queue is not None and not record.dry_run:
                self._goals.pop(record.goal_id, None)
                return None
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
        # P8b-1: every user-facing output in the event (step outputs, answers,
        # errors, the live token stream) is screened for PII / secrets and the
        # tenant's output rules BEFORE it is stored, published or kept — the
        # result artifact, /events, SSE and replay all derive from these events.
        from app.guardrails_v2.output_screening import screen_goal_event

        _screened = await screen_goal_event(sanitized_event, record.tenant_id)
        if _screened is None:
            return  # a live token chunk this tenant's output rules do not allow
        sanitized_event = _screened
        _ephemeral_event_types = {"token_chunk", "heartbeat"}
        _is_ephemeral = sanitized_event.get("type") in _ephemeral_event_types
        if not _is_ephemeral:
            record.events.append(sanitized_event)
            seq = await self._persist_event(goal_id, sanitized_event, record, tenant_ctx)
            if seq is None and self._event_store is None:
                # No durable store (dev / tests): the in-memory position is the
                # sequence, which is what a replay from this record uses too.
                seq = len(record.events)
            if seq is not None:
                # SVC-05: the SSE id / Last-Event-ID cursor, on the live copy
                # (queues and the cross-replica channel). Stamped after the
                # append, so the stored payload is unchanged. An event whose
                # append failed (outbox) has none rather than a made-up one.
                sanitized_event["_seq"] = seq
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
                _eval_aps: Any = self._eval_app_state()
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
                    steps, verification_success = self._eval_steps_from_events(record.events)
                    agent_state = AgentState(
                        goal_id=goal_id,
                        goal=record.goal_text,
                        tenant_ctx=tenant_ctx_for_record,
                        status=GoalStatus.COMPLETE,
                        iterations=len(record.events),
                        steps=steps,
                        verification_success=verification_success,
                    )
                    await self._mark_eval_pending(goal_id, record.tenant_id)
                    try:
                        scorecard = await eval_runner.score_and_persist(
                            agent_state,
                            tenant_ctx_for_record,
                            provider=self._eval_provider(_eval_aps),
                            db=self._db,
                        )
                        self._eval_scores[goal_id] = scorecard
                    finally:
                        await self._clear_eval_pending(goal_id, record.tenant_id)
                    await self._publish_chain_event(
                        record,
                        "goal.score_below",
                        tenant_ctx,
                        score=float(scorecard.average_score()),
                        scores=getattr(scorecard, "scores", None),
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
                                # Persisted (MEM-26): shared across replicas.
                                await self_optimizer.analyze_and_persist(
                                    goal=record.goal_text,
                                    scorecard=scorecard,
                                    error_log=error_log,
                                    tenant_ctx=tenant_ctx_for_record,
                                    goal_id=goal_id,
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

            # Idempotent per goal (ZREM of its lease): a worker-run goal that is
            # cancelled is released here AND by the worker; the second is a no-op.
            if _holds_concurrency_slot(record.execution_context):
                await decrement_concurrent_goals(
                    tenant_id=record.tenant_id, redis=self._redis, goal_id=goal_id
                )
            # Release this goal's dedup claim (compare-and-delete by goal_id, so a
            # newer identical goal's claim survives) so identical goals can run.
            from app.services.dedup import _default_deduplicator as _goal_dedup

            await _goal_dedup.release_goal(record.goal_id)
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
            if not record.dry_run:
                # A12: the tenant's agent_generated Sources index the answer now.
                from app.ingestion.agent_generated_events import notify_agent_generated

                await notify_agent_generated(
                    record.tenant_id, "goal_output", goal_id, db_factory=self._db
                )
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
        scores: dict[str, float] | None = None,
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
        from app.triggers.bus import publish_trigger_event
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
            scores=scores if isinstance(scores, dict) else None,
        )
        try:
            # Stream XADD (+ legacy pub/sub while dual publish is on), TRG-18.
            await publish_trigger_event(self._redis, channel, payload)
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
        if not isinstance(execution, dict) or not execution.get("patterns"):
            return
        from app.orchestration.strategy_evidence import (
            StrategyEvidenceRecorder,
            record_goal_strategy_evidence,
        )

        state: Any = self._app_state
        state = getattr(state, "state", state)
        recorder = getattr(state, "strategy_evidence", None) if state is not None else None
        if not isinstance(recorder, StrategyEvidenceRecorder):
            return

        async def _record() -> None:
            await record_goal_strategy_evidence(
                recorder,
                tenant_id=record.tenant_id,
                goal_id=record.goal_id,
                execution_context=record.execution_context,
                succeeded=status == "completed",
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
        from app.governance.cost import bind_cost_agent

        record = self._goals.get(goal_id)
        bind_cost_agent(getattr(record, "agent_id", None))  # per-agent caps (COST-02)
        await self._refresh_tenant_policies(tenant_ctx)

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
            # ESCALATE files an approval ("keep retrying?") and waits for it.
            hitl_gateway=(
                getattr(self._app_state, "hitl_gateway", None) if self._app_state else None
            )
            or self._hitl,
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
        except GoalCancelledError as exc:
            # A cancel / emergency stop seen at a step boundary ends the
            # persistence run as cancelled (the engine no longer retries it).
            if record is not None and record.status not in _TERMINAL_STATUSES:
                record.status = GoalStatus.CANCELLED
                await self._dispatch_event(
                    goal_id, {"type": "goal_cancelled", "reason": str(exc)}, tenant_ctx=tenant_ctx
                )
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
        # Attribute every charge this goal makes to its agent (per-agent daily
        # caps, COST-02). The task runs in its own context copy.
        from app.governance.cost import bind_cost_agent

        bind_cost_agent(getattr(self._goals.get(goal_id), "agent_id", None))
        await self._refresh_tenant_policies(tenant_ctx)
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
            from app.tenancy.limits import effective_goal_timeout

            # The agent's timeout_seconds can only shorten the plan's budget.
            _goal_timeout_s, _timeout_source = effective_goal_timeout(
                getattr(_PLAN_LIMITS.get(tenant_ctx.plan), "goal_timeout_seconds", 3600),
                getattr(loop, "_agent_timeout_seconds", None),
            )
            _timeout_note = " (agent timeout_seconds)" if _timeout_source == "agent" else ""

            try:
                from app.providers.rate_limit import run_with_llm_deadline

                final_state = await asyncio.wait_for(
                    # P5-1: throttling backoff never waits past the goal budget.
                    run_with_llm_deadline(
                        loop.run(
                            goal=goal_text,
                            tenant_ctx=tenant_ctx,
                            initial_context=initial_context or None,
                            event_callback=callback,
                            goal_id=goal_id,
                        ),
                        float(_goal_timeout_s),
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
                    record.error_message = f"Goal timed out after {_goal_timeout_s}s{_timeout_note}"
                    timeout_event: dict[str, Any] = {
                        "type": "goal_failed",
                        "reason": f"timeout after {_goal_timeout_s}s{_timeout_note}",
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
                    from app.providers.tenant_vault import decrypt_tenant_secret

                    scoped_llm_key = await decrypt_tenant_secret(
                        self._db, tenant_ctx.tenant_id, _enc
                    )
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
            # An agent-scoped API key submits only for its own agent, and its
            # tool restriction travels with the goal to the worker (AGKEY-01).
            from app.auth.agent_credentials import bind_goal_to_agent_key

            agent_id, execution_context = bind_goal_to_agent_key(
                tenant_ctx, agent_id, execution_context
            )
            # Auto-route when no agent was named (RV-02). Before validation so the
            # routed agent is validated and its pattern flags travel with the
            # goal; skipped when POST /goals already routed (its decision is in
            # the context), so a goal is never routed twice.
            if agent_id is None and "routing_decision" not in (execution_context or {}):
                agent_id, _routing = await self._auto_route_goal(goal, tenant_ctx)
                if _routing is not None:
                    execution_context = {**(execution_context or {}), "routing_decision": _routing}
                    _fanout = await self._submit_multi_agent_routing(
                        goal, _routing, tenant_ctx, priority=priority, dry_run=dry_run
                    )
                    if _fanout is not None:
                        return _fanout
            await self._refresh_agent_record(agent_id, tenant_ctx)
            _agent_record = await self._validate_agent_id(agent_id, tenant_ctx)

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
            from app.agent.supervisor import SUBGOAL_MARKER
            from app.services.dedup import _default_deduplicator as _goal_dedup

            _dedup_redis = getattr(self, "_redis", None)
            if _dedup_redis is not None and not hasattr(_goal_dedup, "_redis_wired"):
                _goal_dedup._redis = _dedup_redis
                _goal_dedup._redis_wired = True  # type: ignore[attr-defined]

            goal_id = uuid.uuid4().hex
            # A supervisor's sub-goal is a distinct unit of work even when its
            # text matches an in-flight goal — typically its own parent, which
            # dedup returned, so the parent waited on itself forever.
            _is_subgoal = bool((execution_context or {}).get(SUBGOAL_MARKER))
            # ONE atomic claim (SET NX): get_existing + register were separate
            # calls, so two concurrent identical submissions both ran (SVC-01).
            # Redis unavailable -> claim() logs and answers "run" (no dedup).
            _existing_id = (
                None
                if _is_subgoal
                else await _goal_dedup.claim(
                    tenant_ctx.tenant_id, goal, goal_id, scope=_dedup_scope
                )
            )
            if _existing_id:
                return {
                    "goal_id": _existing_id,
                    "status": "running",
                    "deduplicated": True,
                    "message": "Identical goal already in progress",
                }

            # Take a concurrency slot only for a goal that will actually run. It
            # used to be taken BEFORE the dedup check, and the dedup early-return
            # never released it: every duplicate submission leaked a slot until
            # the tenant was locked out with 429s.
            # Check and atomically increment the concurrent-goal counter.
            # Raises PlanLimitExceededError (HTTP 429) when the tenant is at limit.
            from app.tenancy.limits import check_and_increment_concurrent_goals

            # The slot is a lease keyed by the goal id minted above (RATE-04).
            _takes_slot = _holds_concurrency_slot(execution_context)
            if _takes_slot:
                try:
                    await check_and_increment_concurrent_goals(
                        tenant_ctx=tenant_ctx,
                        redis=getattr(self, "_redis", None),
                        goal_id=goal_id,
                    )
                except BaseException:
                    # Refused (429): the claim must not dedup the retry onto a
                    # goal that never existed.
                    await _goal_dedup.release_goal(goal_id)
                    raise

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
            # (Read from the DB-backed store by _validate_agent_id above — the
            # local cache, read under suppress(Exception), lost them silently.)
            _flags = pattern_flags_from_record(_agent_record)
            if workflow_mode == "supervisor":
                # POST /goals workflow_mode=supervisor (CORE-07): this goal is the
                # supervisor parent; its own graph runs the fan-out node.
                _flags["enable_supervisor"] = True
            elif workflow_mode == "debate":
                # POST /goals workflow_mode=debate (CORE-30): the debate runs in
                # this goal's own graph (on the worker), not in the request.
                _flags["enable_debate"] = True
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
                workflow_mode=workflow_mode,
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
            except Exception as _ps_exc:
                _svc_logger.warning(
                    "pattern_selection_summary_failed",
                    goal_id=goal_id,
                    error_type=type(_ps_exc).__name__,
                    error=str(_ps_exc)[:200],
                )

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

                        if _takes_slot:
                            await decrement_concurrent_goals(
                                tenant_id=tenant_ctx.tenant_id,
                                redis=getattr(self, "_redis", None),
                                goal_id=goal_id,
                            )
                    except Exception as _dec_exc:
                        _svc_logger.warning(
                            "counter_decrement_failed_on_error", error=str(_dec_exc)
                        )
                    await _goal_dedup.release_goal(goal_id)
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

            _downgrade = record.execution_context.get("strategy_downgrade")
            if record.execution_context.get("strategy_downgraded") and _downgrade:
                # Never silent: the goal's own event stream says the requested
                # strategy will not run (and why) before anything else happens.
                await self._dispatch_event(
                    goal_id,
                    {"type": "strategy_downgraded", "strategy_downgraded": True, **_downgrade},
                    tenant_ctx=tenant_ctx,
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
                        **_subgoal_queue_kwargs(execution_context),
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
                            **_downgrade_fields(record.execution_context),
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
            **_downgrade_fields(record.execution_context),
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
            # NF-14: why a failed / cancelled goal ended — sanitized (no
            # credentials, URLs, hosts or addresses); None for any other status.
            "failure_reason": (
                public_failure_reason(record.error_message)
                if record.status.value in {"failed", "cancelled"}
                else None
            ),
            "terminal_reason": terminal_reason_code(record.status.value, record.error_message),
            **_downgrade_fields(record.execution_context),
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

                from app.db.rls import sqlalchemy_rls_context

                # a09-F223-02: ``goals`` is FORCE RLS and the app role is
                # NOBYPASSRLS — without the tenant GUC this aggregate matched no
                # rows and the dashboards showed 0. The explicit tenant predicate
                # stays too (never rely on the GUC alone).
                async with (
                    self._db() as session,
                    sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
                ):
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
        """The persisted scorecard (Postgres is the record), else this replica's cache.

        Postgres is read first whenever it is configured: a re-score on another
        replica replaces the persisted row, and a cache-first read kept serving
        this replica's older scorecard. The cache only answers for goals whose
        scorecard never reached Postgres (no DB, or its write failed). None if
        unscored.
        """
        persisted = await self._persisted_eval(goal_id, tenant_ctx)
        if persisted is not None:
            self._eval_scores[goal_id] = persisted[0]
            return persisted[0]
        return self._eval_scores.get(goal_id)

    async def get_eval(self, goal_id: str, tenant_ctx: TenantContext) -> dict[str, Any]:
        """Return the eval scorecard for *goal_id*, or a pending / not-evaluated response.

        The persisted scorecard (Postgres, tenant RLS) first — a goal scored or
        re-scored on another replica or before a restart is served everywhere —
        then this replica's cache, then the cross-replica "pending" marker.
        """
        await self._aget_read_record(goal_id, tenant_ctx)  # 404 if unknown / wrong tenant
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
        scorecard = self._eval_scores.get(goal_id)
        if scorecard is None:
            pending = await self._is_eval_pending(goal_id, tenant_ctx.tenant_id)
            return {
                "goal_id": goal_id,
                "status": "pending" if pending else "not_evaluated",
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
        await self._aget_read_record(goal_id, tenant_ctx)  # 404 if unknown / wrong tenant
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
        """Score a goal on demand, persist the scorecard and return it.

        Enables the "Run Eval" button. Judgement dimensions use the platform
        provider the completion-time scorer uses (``scorer`` in the response
        says whether the model actually scored them). The scorecard replaces
        the goal's persisted one, so every replica's GET /eval serves it; a
        scorecard that cannot be persisted is a 503, not a replica-local result.
        """
        record = await self._aget_read_record(goal_id, tenant_ctx)

        # Try to get live AgentState from the record
        state = getattr(record, "agent_state", None)
        if state is None:
            # Reconstruct minimal state from record data. A goal loaded from
            # Postgres (another replica / worker ran it) has no in-memory events.
            events = list(record.events) or await self._list_persisted_events(
                goal_id, tenant_ctx
            )
            try:
                goal_status = GoalStatus(record.status.value)
            except (ValueError, AttributeError):
                goal_status = GoalStatus.COMPLETE

            state = AgentState(
                goal_id=goal_id,
                goal=record.goal_text,
                tenant_ctx=tenant_ctx,
                status=goal_status,
                steps=list(record.steps)
                if getattr(record, "steps", None)
                else self._eval_steps_from_events(events)[0],
                verification_success=(record.status == record.status.COMPLETE),
                verification_feedback=record.execution_context.get("verification_feedback", "")
                if isinstance(record.execution_context, dict)
                else "",
                events=events,
                iterations=int(record.execution_context.get("iterations", 1))
                if isinstance(record.execution_context, dict)
                else 1,
                context=dict(record.execution_context)
                if isinstance(record.execution_context, dict)
                else {},
            )

        from app.intelligence.eval_runner import EvalRunner

        aps = self._eval_app_state()
        runner = getattr(aps, "eval_runner", None) or EvalRunner()
        scorecard = await runner.score_async(
            state=state,
            tenant_ctx=tenant_ctx,
            provider=self._eval_provider(aps),
        )

        persisted = False
        if self._db is not None:
            try:
                persisted = bool(
                    await runner.persist_scorecard(
                        scorecard,
                        goal_id=goal_id,
                        tenant_ctx=tenant_ctx,
                        db=self._db,
                        replace=True,
                        strict=True,
                    )
                )
            except Exception as exc:
                raise ServiceUnavailableError(
                    "the eval scorecard could not be saved", code="EVAL_STORE_UNAVAILABLE"
                ) from exc

        # Cache for subsequent GET requests on this replica
        self._eval_scores[goal_id] = scorecard

        return {
            "goal_id": scorecard.goal_id,
            "status": "evaluated",
            "scores": scorecard.scores,
            "average_score": scorecard.average_score(),
            "passed": scorecard.passed(),
            "iterations": scorecard.iterations,
            "scorer": getattr(scorecard, "scorer", "heuristic"),
            "persisted": persisted,
        }

    def _cancel_local_strategy_run(self, goal_id: str) -> None:
        state: Any = self._app_state
        state = getattr(state, "state", state)
        runner = getattr(state, "strategy_runner", None) if state is not None else None
        cancel = getattr(runner, "cancel", None)
        if cancel is not None:
            with suppress(Exception):
                cancel(goal_id)

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
        changed = await self._persist_cancelling_status(
            record, tenant_ctx, GoalStatus.CANCELLED
        )
        if changed is False:
            fresh = await self._db_get_goal_record(goal_id, tenant_ctx)
            if fresh is not None and fresh.status in _TERMINAL_STATUSES:
                return {"goal_id": goal_id, "status": fresh.status.value}
        if self._runs_locally(record):
            assert record.task is not None
            record.task.cancel()
        # A distributed strategy run for this goal (cancellation token = goal id)
        # stops too; nothing ever called StrategyRunner.cancel (CORE-18).
        self._cancel_local_strategy_run(goal_id)

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

        if _holds_concurrency_slot(record.execution_context):
            await decrement_concurrent_goals(
                tenant_id=tenant_ctx.tenant_id, redis=self._redis, goal_id=goal_id
            )
        await self._dispatch_event(
            goal_id,
            {"type": "goal_waiting_human", "reason": "pending approvals (supervised mode)"},
            tenant_ctx=tenant_ctx,
        )

    async def _restore_suspended(
        self, record: GoalRecord, tenant_ctx: TenantContext, *, release_slot: bool
    ) -> None:
        """Undo a relaunch that could not be handed to a runner (best effort)."""
        if record.status not in _TERMINAL_STATUSES:
            record.status = GoalStatus.WAITING_HUMAN
        record.execution_context[_SUSPENDED_KEY] = True
        with suppress(Exception):
            await self._db_update_goal_status(
                record.goal_id,
                tenant_ctx.tenant_id,
                GoalStatus.WAITING_HUMAN.value,
                only_if_active=True,
            )
        with suppress(Exception):
            await self._db_set_suspended(record.goal_id, tenant_ctx.tenant_id, True)
        if release_slot:
            from app.tenancy.limits import decrement_concurrent_goals

            with suppress(Exception):
                await decrement_concurrent_goals(
                    tenant_id=tenant_ctx.tenant_id, redis=self._redis, goal_id=record.goal_id
                )

    async def _relaunch_suspended_goal(
        self, record: GoalRecord, tenant_ctx: TenantContext
    ) -> None:
        """Run a suspended goal again under its own id; its checkpoints resume it."""
        from app.tenancy.limits import check_and_increment_concurrent_goals

        # The slot was released on suspension; a tenant at its limit gets a 429
        # and the goal stays waiting (the approval can be retried). A sub-goal
        # runs under its parent's slot and never held one.
        _takes_slot = _holds_concurrency_slot(record.execution_context)
        if _takes_slot:
            await check_and_increment_concurrent_goals(
                tenant_ctx=tenant_ctx, redis=self._redis, goal_id=record.goal_id
            )
        try:
            # WF-18: the durable status leaves waiting_human BEFORE run_goal is
            # enqueued, so the worker's claim can refuse waiting_human rows (a
            # redelivered message must never un-pause a goal awaiting a human)
            # while this legitimate relaunch is still claimable. Fail closed: no
            # message when the transition cannot be recorded.
            if not await self._db_update_goal_status(
                record.goal_id,
                tenant_ctx.tenant_id,
                GoalStatus.EXECUTING.value,
                only_if_active=True,
                raise_on_error=True,
            ):
                raise ConflictError(f"Goal {record.goal_id} is no longer resumable")
            record.status = GoalStatus.EXECUTING
            record.execution_context.pop(_SUSPENDED_KEY, None)
            await self._db_set_suspended(record.goal_id, tenant_ctx.tenant_id, False)
            # The relaunch may run somewhere else than the original run did.
            record.execution_context[_RUNNER_KEY] = self._runner_marker()
            try:
                await self._db_set_runner(
                    record.goal_id, tenant_ctx.tenant_id, record.execution_context[_RUNNER_KEY]
                )
            except Exception as exc:
                _svc_logger.warning(
                    "db_set_runner_failed", goal_id=record.goal_id, error=str(exc)
                )
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
                    **_subgoal_queue_kwargs(record.execution_context),
                )
                return
        except BaseException:
            # Nothing will run it: leave the goal waiting and resumable again.
            await self._restore_suspended(record, tenant_ctx, release_slot=_takes_slot)
            raise
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

    async def _persist_cancelling_status(
        self, record: GoalRecord, tenant_ctx: TenantContext, status: GoalStatus
    ) -> bool:
        """Persist a cancel / reject AFTER the runner was signalled to stop.

        Conditional (a goal that finished meanwhile keeps its real status) and
        fail-closed: when the write fails the cancel signal is withdrawn and the
        503 propagates, so the goal is not stopped behind an error response.
        """
        try:
            return await self._db_update_goal_status(
                record.goal_id,
                tenant_ctx.tenant_id,
                status.value,
                only_if_active=True,
                raise_on_error=True,
            )
        except ServiceUnavailableError:
            redis = getattr(self, "_redis", None)
            if redis is not None:
                from app.reliability.goal_lifecycle import withdraw_cancel

                await withdraw_cancel(record.goal_id, redis)
            raise

    async def _real_status_after_lost_write(
        self, record: GoalRecord, tenant_ctx: TenantContext
    ) -> dict[str, Any]:
        """A conditional status write changed no row: report what the goal is."""
        fresh = await self._db_get_goal_record(record.goal_id, tenant_ctx)
        status = (fresh or record).status
        return {"goal_id": record.goal_id, "status": status.value}

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
            suspended = bool(record.execution_context.get(_SUSPENDED_KEY))
            if not suspended:
                # A runner is blocked in its pause gate (here, on another replica,
                # or on a worker): stop it rather than leave it paused forever.
                from app.reliability.goal_lifecycle import signal_cancel

                await self._signal_runner(record, signal_cancel, "reject")
                # Conditional + fail-closed (it was neither: a DB error was
                # swallowed while the API reported the rejection, and a goal
                # that finished meanwhile was overwritten with "failed").
                changed = await self._persist_cancelling_status(
                    record, tenant_ctx, GoalStatus.FAILED
                )
            else:
                changed = await self._db_update_goal_status(
                    goal_id,
                    tenant_ctx.tenant_id,
                    GoalStatus.FAILED.value,
                    only_if_active=True,
                    raise_on_error=True,
                )
            if changed is False:
                return await self._real_status_after_lost_write(record, tenant_ctx)
            if not suspended and self._runs_locally(record):
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
            # Persists "executing" itself, BEFORE it re-enqueues run_goal (WF-18);
            # writing it again afterwards could overwrite a fast run's terminal
            # status.
            await self._relaunch_suspended_goal(record, tenant_ctx)
        else:
            # Release the runner's pause gate wherever it runs: clearing the Redis
            # pause flag is what another replica's loop / a worker is polling.
            # Awaited (it was fire-and-forget) and, for a remote goal, required —
            # otherwise the goal would read "executing" while still blocked.
            from app.reliability.goal_lifecycle import signal_resume

            await self._signal_runner(record, signal_resume, "resume")
            # Conditional + fail-closed: a swallowed DB error left the goal
            # "waiting_human" forever while the API reported it resumed, and a
            # goal that finished meanwhile was flipped back to "executing". A
            # 503 here is safe to retry (releasing the pause flag is idempotent).
            changed = await self._db_update_goal_status(
                goal_id,
                tenant_ctx.tenant_id,
                GoalStatus.EXECUTING.value,
                only_if_active=True,
                raise_on_error=True,
            )
            if changed is False:
                return await self._real_status_after_lost_write(record, tenant_ctx)
            record.status = GoalStatus.EXECUTING
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

        Every stored event carries ``_seq``, its durable event-store sequence
        (the in-memory position when there is no store); the SSE endpoint emits
        it as the ``id:`` line, and *since_sequence* (from Last-Event-ID)
        resumes after it. The replay is keyset-paged over the whole history
        (SVC-05: it used to stop after 100 events). The live feed is always
        registered BEFORE the replay, and live events the replay already
        delivered are dropped by sequence, so nothing is lost or repeated in
        between.

        **Cross-replica delivery (P1-2):** when the goal record is not present
        in this replica's in-memory ``_goals`` dict (it was submitted to a
        different replica or runs on a worker), live events come from Redis
        pub/sub on ``goal_events:{tenant_id}:{goal_id}``, published by the
        owning replica's ``_dispatch_event`` and by workers.
        """
        # ── Try local record first ─────────────────────────────────────────────
        local_record: GoalRecord | None = None
        with suppress(Exception):  # goal is on another replica — cross-replica path below
            local_record = self._get_record(goal_id, tenant_ctx)

        if local_record is None:
            async for event in self._subscribe_remote(goal_id, tenant_ctx, since_sequence):
                yield event
            return

        # ── Local replica path ─────────────────────────────────────────────────
        record = local_record
        queue: asyncio.Queue[dict[str, Any] | None] | None = None
        if record.status not in _TERMINAL_STATUSES:
            # Registered before the replay so events emitted meanwhile are kept.
            queue = asyncio.Queue(maxsize=512)
            record.subscribers.append(queue)
        delivered = _DeliveredEvents(since_sequence)

        try:
            replay_events: list[dict[str, Any]] = []
            async for event in self._replay_events(goal_id, record, tenant_ctx, since_sequence):
                if delivered.fresh(event, replay=True):
                    replay_events.append(event)
                    yield event

            if queue is not None:
                while True:
                    try:
                        queued = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if queued is None:
                        return
                    if delivered.fresh(queued):
                        yield queued

            replay_status = self._status_from_events(replay_events)
            if replay_status is not None:
                record.status = replay_status

            # If goal is already terminal, nothing more to await
            if record.status in _TERMINAL_STATUSES:
                return

            refreshed = await self._refresh_goal_from_db_if_needed(record, tenant_ctx)
            if refreshed is None or refreshed.status in _TERMINAL_STATUSES:
                return
            record = refreshed

            if queue is None:
                return
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=_SSE_HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield {"type": SSE_HEARTBEAT_TYPE}
                    continue
                if item is None:  # end-of-stream
                    break
                if delivered.fresh(item):
                    yield item
        finally:
            if queue is not None:
                # ``record`` may have been swapped for the DB copy above: the
                # queue was registered on the local record.
                with suppress(ValueError):
                    local_record.subscribers.remove(queue)

    async def _subscribe_remote(
        self, goal_id: str, tenant_ctx: TenantContext, since_sequence: int
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Cross-replica stream: SUBSCRIBE, then replay, then the live events the
        replay did not already deliver (by ``_seq``). Replaying first lost
        whatever the owner published between the replay read and SUBSCRIBE."""
        # Validate the goal exists in DB and belongs to this tenant before
        # opening a long-lived pub/sub connection (avoids silent no-ops for
        # truly missing goal IDs).
        db_record = await self._db_get_goal_record(goal_id, tenant_ctx)
        if db_record is None:
            raise NotFoundError(f"Goal not found: {goal_id}")
        delivered = _DeliveredEvents(since_sequence)

        def _is_terminal(rec: GoalRecord) -> bool:
            raw = rec.status.value if hasattr(rec.status, "value") else str(rec.status)
            return raw in ("complete", "failed", "cancelled")

        if _is_terminal(db_record):
            # Already over: the durable history is everything; nothing is live.
            async for event in self._replay_events(goal_id, None, tenant_ctx, since_sequence):
                if delivered.fresh(event, replay=True):
                    yield event
            return

        async def _terminal_tail() -> list[dict[str, Any]] | None:
            """The undelivered persisted events when the goal has ended
            meanwhile, else ``None``. Pub/sub keeps no history: a terminal
            event published before SUBSCRIBE (or before the goal row said
            terminal) is otherwise never seen, and the stream heartbeats
            forever. A failed check keeps the stream open (no silent end)."""
            try:
                rec = await self._db_get_goal_record(goal_id, tenant_ctx)
                if rec is None or not _is_terminal(rec):
                    return None
                tail = [
                    e
                    async for e in self._persisted_events_after(
                        goal_id, delivered.floor, tenant_ctx
                    )
                ]
            except Exception as exc:
                _svc_logger.warning(
                    "cross_replica_sse_status_check_failed",
                    goal_id=goal_id,
                    error=str(exc)[:120],
                )
                return None
            return [e for e in tail if delivered.fresh(e)]

        if not self._redis_url_for_pubsub:
            # No live channel: deliver the history, then say so (it used to end
            # the stream silently, as if the goal had no further events).
            async for event in self._replay_events(goal_id, None, tenant_ctx, since_sequence):
                if delivered.fresh(event, replay=True):
                    yield event
            raise ServiceUnavailableError(
                "Live delivery for this goal is unavailable on this replica.",
                code="GOAL_STREAM_UNAVAILABLE",
            )

        try:
            import redis.asyncio as _aioredis

            async with (
                _aioredis.from_url(
                    self._redis_url_for_pubsub, decode_responses=True
                ) as _pubsub_client,
                _pubsub_client.pubsub() as pubsub,
            ):
                channel = f"goal_events:{tenant_ctx.tenant_id}:{goal_id}"
                # Subscribed BEFORE the replay: anything published from now on
                # is buffered on the connection, and the replay's copies of it
                # are dropped below by sequence (SVC-05).
                await pubsub.subscribe(channel)
                replayed: list[dict[str, Any]] = []
                async for event in self._replay_events(
                    goal_id, None, tenant_ctx, since_sequence
                ):
                    if delivered.fresh(event, replay=True):
                        replayed.append(event)
                        yield event
                if self._status_from_events(replayed) is not None:
                    return
                # One status check closes the case of a goal that ended without
                # a stored terminal event (e.g. its append went to the outbox).
                tail = await _terminal_tail()
                if tail is not None:
                    for event in tail:
                        yield event
                    return
                idle_ticks = 0
                next_check = 1  # idle ticks until the next status check
                while True:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=_SSE_HEARTBEAT_SECONDS
                    )
                    if message is None:
                        # Idle (e.g. waiting for approval). The terminal
                        # event may have been published before the goal row
                        # said terminal: re-check with backoff (1, 2, 4, then
                        # every 8 idle ticks) — bounded DB reads per stream.
                        idle_ticks += 1
                        if idle_ticks >= next_check:
                            idle_ticks = 0
                            next_check = min(next_check * 2, 8)
                            tail = await _terminal_tail()
                            if tail is not None:
                                for event in tail:
                                    yield event
                                return
                        # A heartbeat lets the endpoint notice a client
                        # that went away.
                        yield {"type": SSE_HEARTBEAT_TYPE}
                        continue
                    if not isinstance(message, dict) or message.get("type") != "message":
                        # Never spin without yielding to the event loop.
                        await asyncio.sleep(0)
                        continue
                    try:
                        event = self._normalize_bus_event(json.loads(message["data"]))
                    except Exception:
                        continue
                    if not delivered.fresh(event):
                        continue  # already replayed
                    yield event
                    # Every terminal event ends the stream — worker_failed
                    # (timeout, crash, lock failure) used to leave it open.
                    if self._status_from_events([event]) is not None:
                        return
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            _svc_logger.warning(
                "cross_replica_sse_failed",
                goal_id=goal_id,
                error=str(exc)[:120],
            )
            raise ServiceUnavailableError(
                "Live delivery for this goal was interrupted; reconnect.",
                code="GOAL_STREAM_UNAVAILABLE",
                cause=exc,
            ) from exc

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
            from app.agent.supervisor import SUBGOAL_MARKER
            from app.db.models.goal import Goal
            from app.db.rls import sqlalchemy_rls_context

            _parent = str((execution_context or {}).get(SUBGOAL_MARKER) or "")
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                # A supervisor's sub-goal is linked to its parent goal row (same
                # tenant, via RLS) when that row exists; the marker may be a bare
                # "supervisor" placeholder with no parent goal. The tenant is
                # checked explicitly too: a BYPASSRLS role's PK lookup sees
                # every tenant's goals.
                _parent_row = (
                    await session.get(Goal, _parent)
                    if _parent and _parent != goal_id
                    else None
                )
                parent_goal_id = (
                    _parent
                    if _parent_row is not None and _parent_row.tenant_id == tenant_id
                    else None
                )
                g = Goal(
                    id=goal_id,
                    tenant_id=tenant_id,
                    parent_goal_id=parent_goal_id,
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
        """Load one goal from PostgreSQL when this process has no memory record.

        ``None`` means the row does not exist (for this tenant). A database
        error raises :class:`ServiceUnavailableError` (HTTP 503): it used to be
        logged and returned as ``None`` too, so an outage answered 404 "Goal not
        found" and clients concluded the goal was gone.
        """
        if self._db is None:
            return None
        from sqlalchemy import select

        from app.db.models.goal import Goal
        from app.db.rls import sqlalchemy_rls_context

        try:
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
        except Exception as exc:
            _svc_logger.warning("DB get goal failed: %s", exc)
            raise ServiceUnavailableError(
                "The goal store is unavailable; try again.",
                code="GOAL_STORE_UNAVAILABLE",
                cause=exc,
            ) from exc
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
            completed_at=_row_completed_at(row, status),
            # NF-14: the durable failure reason (it was dropped on every DB load).
            error_message=str(getattr(row, "error_message", "") or ""),
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
        self._cache_goal(record)
        return record

    async def _db_update_goal_status(
        self,
        goal_id: str,
        tenant_id: str,
        status: str,
        error_message: str = "",
        iterations: int | None = None,
        only_if_active: bool = False,
        raise_on_error: bool = False,
    ) -> bool:
        """Update goal status in PostgreSQL; return whether a row was changed.

        *iterations* is written only when given (``None`` leaves the column
        alone: every cancel / pause / resume used to reset it to 0).

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

            values: dict[str, Any] = {"status": status}
            if iterations is not None:
                values["iterations"] = iterations
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
                                completed_at=_row_completed_at(g, _g_status),
                            )
                            self._cache_goal(record)
                            loaded += 1

            _svc_logger.info("Synced %d recent goals from DB", loaded)
            # Restart recovery is NOT run here: it needs Redis (runner liveness,
            # worker locks), which the lifespan wires after this sync. The
            # lifespan calls recover_interrupted_goals() once it is.
            return loaded
        except Exception as exc:
            _svc_logger.warning("DB sync goals failed: %s", exc)
            return 0
