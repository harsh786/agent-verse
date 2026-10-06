"""Celery tasks — real implementations for goal execution and scheduling."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import datetime
import hashlib
import itertools
import os
import re
import signal as _signal
import threading
import time
import uuid
from datetime import UTC
from typing import Any, cast

from celery.signals import task_postrun as _task_postrun
from celery.signals import task_prerun as _task_prerun
from celery.signals import worker_init as _worker_init

from app.observability.logging import get_logger
from app.org.feature_flags import is_feature_enabled
from app.reliability.goal_lifecycle import GoalCancelledError
from app.scaling.beat_guard import beat_task_guard
from app.scaling.celery_app import celery_app, goal_queue_for
from app.scaling.retry_policy import is_transient_infra_error

logger = get_logger(__name__)


# ── SIGTERM graceful shutdown handler ─────────────────────────────────────────
def _setup_sigterm() -> None:
    """Register a SIGTERM handler so Celery workers shut down gracefully.

    WF-15: the log used to claim a LangGraph checkpoint was written, but worker
    goals run on the in-memory MemorySaver, which persists nothing. It now names
    the checkpointer actually in use, so operators know what survives.
    """

    def _handler(sig: int, frame: Any) -> None:
        import logging as _stdlib_logging

        kind = _worker_checkpointer_kind()
        _stdlib_logging.getLogger(__name__).warning(
            "SIGTERM received — Celery worker shutting down; checkpointer=%s (%s)",
            kind,
            "durable"
            if kind != "MemorySaver"
            else "not durable: in-flight goal state is lost; acks_late redelivers the goal",
        )
        raise SystemExit(0)

    # OSError: not in main thread; ValueError: invalid signal — both safe to ignore
    with contextlib.suppress(OSError, ValueError):
        _signal.signal(_signal.SIGTERM, _handler)


_setup_sigterm()

# Module-level Redis URL — read once at import time so tasks don't re-read env
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Patchable reference to the real AgentGraph class — set to None in tests that
# want to prevent real agent loop execution without importing the full graph module.
_REAL_AGENT_LOOP_CLASS: type | None = None

# ── Module-level Redis connection pool — initialized once per worker process ──
# Sync pool is safe to share across all task invocations; it does not bind to
# any asyncio event loop.  Async redis clients are created per-task because
# each Celery task's _run_async() call creates a fresh event loop.

_REDIS_POOL: Any = None


def _get_redis_pool() -> Any:
    """Get or create a module-level Redis connection pool."""
    global _REDIS_POOL
    if _REDIS_POOL is None:
        import redis

        _redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _REDIS_POOL = redis.ConnectionPool.from_url(
            _redis_url, decode_responses=True, max_connections=10
        )
    return _REDIS_POOL


def _redacted_error(exc: BaseException, *, limit: int = 1000) -> str:
    """``"<ExceptionClass>: <redacted message>"`` for goal rows, events and results.

    CORE-34: worker-level failures stored and published ``str(exc)``, so a
    credential in exception text reached SSE, the event log and the goal row.
    """
    from app.agent.sanitization import redact_sensitive_text

    return f"{type(exc).__name__}: {redact_sensitive_text(exc)}"[:limit]


def _get_sync_redis() -> Any:
    """Get a synchronous Redis client using the module-level pool."""
    import redis

    return redis.Redis(connection_pool=_get_redis_pool())


# ── Module-level LangGraph checkpointer for Celery workers ────────────────────
# Set once per worker process by _setup_worker_checkpointer (worker_init signal).
# None → AgentGraph falls back to MemorySaver (state lost on worker restart).
_WORKER_CHECKPOINTER: Any = None


def _worker_checkpointer_kind() -> str:
    """The checkpointer worker goals actually use (None → AgentGraph's MemorySaver)."""
    return type(_WORKER_CHECKPOINTER).__name__ if _WORKER_CHECKPOINTER is not None else (
        "MemorySaver"
    )


@_worker_init.connect
def _setup_worker_checkpointer(**kwargs: Any) -> None:
    """Called once when the Celery worker process starts.

    Uses MemorySaver — goals complete fully and state is preserved within
    a single run.  State is NOT persisted across worker restarts / retries,
    but this is acceptable for dev and most prod workloads.

    AsyncRedisSaver requires an async context manager entry which is not
    compatible with the sync worker_init signal. InMemorySaver (LangGraph's
    preferred name for MemorySaver) is the correct choice here.
    """
    import logging as _logging

    _logging.getLogger(__name__).info(
        "celery_worker_checkpointer: using MemorySaver (in-process, no persistence)"
    )
    # _WORKER_CHECKPOINTER stays None → AgentGraph will use MemorySaver()

    # PROV-05: one cost-services resolver per worker process (per-loop controller
    # + ledger tracker), instead of a module global re-installed by every run_goal.
    from app.scaling.worker_cost import install_worker_cost_services

    install_worker_cost_services()
    # PROV-20: routing policies / model overrides are read from the shared store.
    try:
        _wire_worker_model_registry_store()
    except Exception as exc:  # re-tried per goal by _worker_tenant_policy_roles
        _logging.getLogger(__name__).warning("worker_model_registry_store_failed: %s", exc)
    # PROV-11: the worker runs production goals, so it needs the served models'
    # context windows too (they were probed only at API startup).
    _probe_worker_model_windows()


def _probe_worker_model_windows() -> None:
    """Fill this worker's on-prem model context-window map (best effort)."""
    import logging as _logging

    try:
        from app.ai_router import deployment_roles
        from app.core.config import get_settings
        from app.db.session import run_in_fresh_loop

        settings = get_settings()
        if not getattr(settings, "onprem_enabled", False):
            return
        windows = run_in_fresh_loop(deployment_roles.probe_model_windows(settings))
        _logging.getLogger(__name__).info("worker_onprem_model_windows windows=%s", windows)
    except Exception as exc:  # unknown windows keep small models out of roles (fail closed)
        _logging.getLogger(__name__).warning("worker_model_window_probe_failed: %s", exc)


@_task_prerun.connect
def _enter_task_tenant_charge_scope(
    task_id: str | None = None, task: Any = None, args: Any = None, kwargs: Any = None,
    **_: Any,
) -> None:
    """Charge a tenant-serving task's out-of-goal LLM decision calls to that tenant."""
    if task_id and task is not None:
        from app.scaling.worker_cost import enter_task_tenant_scope

        with contextlib.suppress(Exception):
            enter_task_tenant_scope(task_id, task, args, kwargs)


@_task_postrun.connect
def _exit_task_tenant_charge_scope(task_id: str | None = None, **_: Any) -> None:
    if task_id:
        from app.scaling.worker_cost import exit_task_tenant_scope

        exit_task_tenant_scope(task_id)


class _SyncGoalLock:
    """Synchronous Redis-based distributed lock for Celery tasks.

    Uses ``SET NX PX`` for acquisition and a Lua script for atomic
    check-and-delete on release.  All operations are synchronous so they
    can be called directly from a Celery task without creating a new
    asyncio event loop — avoiding the event-loop-mismatch bug where
    ``redis.asyncio`` clients created in one ``_run_async()`` call become
    unusable inside a different loop created by the next call.
    """

    KEY_PREFIX = "goal_lock:"
    _RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

    def __init__(self, redis_client: Any, lock_value: str) -> None:
        self._redis = redis_client
        self._value = lock_value

    @property
    def token(self) -> str:
        """This run's lock value (also recorded as goals.runner_token)."""
        return self._value

    def acquire(self, goal_id: str, ttl_ms: int = 1_800_000) -> bool:
        """Return True if the lock was acquired; False if another worker holds it."""
        key = f"{self.KEY_PREFIX}{goal_id}"
        result = self._redis.set(key, self._value, px=ttl_ms, nx=True)
        return bool(result)

    def release(self, goal_id: str) -> None:
        """Release the lock only if this instance owns it (atomic Lua check-and-delete)."""
        key = f"{self.KEY_PREFIX}{goal_id}"
        with contextlib.suppress(Exception):
            self._redis.eval(self._RELEASE_SCRIPT, 1, key, self._value)


def _goal_lock_client(redis_url: str) -> Any:
    """The synchronous Redis client run_goal's per-goal execution lock uses."""
    import redis

    return redis.from_url(redis_url, decode_responses=True)


def _start_goal_heartbeat(
    goal_id: str, tenant_id: str, lock: _SyncGoalLock | None, *, enabled: bool
) -> Any:
    """Start the durable runner heartbeat for this run (None when it cannot run).

    GOAL-STALL: without it a goal whose worker died stayed ``executing`` for the
    plan's whole goal timeout. Starting it never blocks the goal: a heartbeat
    that cannot be written only means the reaper cannot protect this run.
    """
    if not enabled:
        return None
    import uuid as _uuid

    from app.scaling.goal_watchdog import GoalHeartbeat

    try:
        return GoalHeartbeat(
            goal_id=goal_id,
            tenant_id=tenant_id,
            runner_token=lock.token if lock is not None else _uuid.uuid4().hex,
        ).start()
    except Exception as exc:
        logger.warning("goal_heartbeat_start_failed goal_id=%s: %s", goal_id, exc)
        return None


# True while this worker thread runs a supervisor sub-goal (set by run_goal from
# its ``subgoal`` kwarg on every invocation). A sub-goal runs under its parent's
# concurrent-goal slot and never took one, so it must not release one (CORE-07).
_SUBGOAL_RUN: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "agentverse_subgoal_run", default=False
)
# The goal this worker thread is running (set by run_goal on every invocation):
# its terminal exits release the goal's submission-dedup claim (SVC-01) and its
# concurrent-goal slot, a lease keyed by goal id released by ZREM of exactly
# that id (RATE-04).
_RUN_GOAL_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "agentverse_run_goal_id", default=""
)


async def _decrement_after_completion(
    tenant_id: str, redis_url: str, goal_id: str | None = None
) -> None:
    """Release the goal's concurrent-goal lease after a Celery goal finishes.

    Celery workers never call ``_dispatch_event`` in the API process, so the
    slot must be released explicitly here at every terminal exit of
    ``run_goal``. The release is ``ZREM goal_id`` (RATE-06): when the API's
    cancel handler already released it, this is a no-op instead of freeing
    another goal's slot. A supervisor sub-goal holds no slot of its own
    (unless SUBGOALS_SHARE_PARENT_SLOT=false): nothing to do.

    Also releases the goal's submission-dedup claim: only the API's local event
    dispatch released it, so a worker-run goal kept identical submissions
    deduplicated onto a finished goal for the claim's TTL.
    """
    goal_id = goal_id or _RUN_GOAL_ID.get()
    if not goal_id:
        logger.warning("counter_decrement_skipped_no_goal_id tenant_id=%s", tenant_id)
        return
    await _release_goal_dedup_claim(goal_id, redis_url)
    if _SUBGOAL_RUN.get():
        from app.services.goal_service import subgoals_share_parent_slot

        if subgoals_share_parent_slot():
            return
    try:
        import redis.asyncio as aioredis

        from app.tenancy.limits import decrement_concurrent_goals

        r = aioredis.from_url(redis_url, decode_responses=True)
        try:
            await decrement_concurrent_goals(tenant_id=tenant_id, redis=r, goal_id=goal_id)
        finally:
            await r.aclose()
    except Exception as exc:
        logger.warning("counter_decrement_failed: %s", exc)


async def _release_goal_dedup_claim(goal_id: str, redis_url: str) -> None:
    try:
        import redis.asyncio as aioredis

        from app.services.dedup import release_goal_claim

        r = aioredis.from_url(redis_url, decode_responses=True)
        try:
            await release_goal_claim(r, goal_id)
        finally:
            await r.aclose()
    except Exception as exc:  # the claim still expires with its TTL
        logger.warning("goal_dedup_release_failed: %s", exc)


async def _renew_slot_lease(tenant_id: str, goal_id: str, plan: Any, redis_url: str) -> None:
    """Extend the goal's slot lease to its run window when a worker starts it.

    The lease taken at submit covers the plan timeout + queue headroom; a goal
    that waited in the queue gets a fresh full run window here (only if it
    still holds a lease — a finished/redelivered goal is not re-admitted).
    """
    if _SUBGOAL_RUN.get():
        return
    try:
        import redis.asyncio as aioredis

        from app.tenancy.limits import concurrent_goal_lease_seconds, renew_concurrent_goal_lease

        r = aioredis.from_url(redis_url, decode_responses=True)
        try:
            await renew_concurrent_goal_lease(
                tenant_id,
                r,
                goal_id=goal_id,
                lease_seconds=concurrent_goal_lease_seconds(plan, running=True),
            )
        finally:
            await r.aclose()
    except Exception as exc:
        logger.warning("slot_lease_renew_failed goal_id=%s: %s", goal_id, exc)


# Register builtin MCP handlers in the worker process so that the
# process-local _BUILTIN_HANDLER_REGISTRY is populated. Without this,
# builtin servers (confluence, jira, etc.) read from Redis but lose
# their Python handler after serialization and fall back to HTTP dispatch.
try:
    from app.mcp.registry import MCPRegistry as _MCPRegistry
    from app.mcp.servers.registry_wiring import get_builtin_server_configs as _get_builtins

    for _bc in _get_builtins():
        if _bc.get("handler") is not None:
            _MCPRegistry.register_builtin_handler(_bc["server_id"], _bc["handler"])
except Exception:
    pass


_CELERY_QUEUE_NAMES = ("goals", "schedules", "maintenance")
_SECRET_REDIS_SCHEDULE_FIELDS = frozenset(
    {"webhook_token", "token", "password", "api_key", "secret"}
)


def _monotonic() -> float:
    return time.monotonic()


def _strip_secret_redis_schedule_fields(sched: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in sched.items()
        if key.lower() not in _SECRET_REDIS_SCHEDULE_FIELDS
    }


def _scheduled_goal_id(schedule_key: str, *, fire_instance_id: str | None = None) -> str:
    instance = fire_instance_id or datetime.datetime.now(datetime.UTC).isoformat()
    return "sched_" + hashlib.sha256(f"{schedule_key}:{instance}".encode()).hexdigest()[:26]


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from a sync Celery task.

    Delegates to ``app.db.session.run_in_fresh_loop``: leftover tasks are
    cancelled and awaited (so their sessions roll back and return connections)
    and every engine used in the loop is disposed before the loop closes. Every
    task loop in this module goes through here — a pooled asyncpg connection
    reused on another loop is how the worker leaked "idle in transaction"
    connections (BEGIN sent, reply never read).
    """
    from app.db.session import run_in_fresh_loop

    return run_in_fresh_loop(coro)


def _worker_compliance_ceiling(db_factory: Any, tenant_id: str) -> str:
    """The tenant's compliance autonomy ceiling for a worker goal (fail closed).

    No DB, a lookup error or an unknown value → ``supervised`` (the strictest
    bundle ceiling), exactly like the API path's ``compliance_autonomy_ceiling``.
    """
    from app.services.goal_service import _AUTONOMY_ORDER

    if db_factory is None:
        logger.warning("worker_compliance_ceiling_no_db_fail_closed tenant=%s", tenant_id)
        return "supervised"
    try:
        from app.governance.compliance_bundles import (
            PostgresComplianceBundleStore,
            effective_max_autonomy_for,
        )

        ceiling = _run_async(
            effective_max_autonomy_for(PostgresComplianceBundleStore(db_factory), tenant_id)
        )
    except Exception as exc:
        logger.warning("worker_compliance_ceiling_failed_closed: %s", type(exc).__name__)
        return "supervised"
    ceiling = str(ceiling or "fully-autonomous")
    return ceiling if ceiling in _AUTONOMY_ORDER else "supervised"


async def _charged_to_agent(coro: Any, agent_id: str | None) -> Any:
    """Await *coro* with every cost charge attributed to *agent_id* (COST-02)."""
    from app.governance.cost import bind_cost_agent

    bind_cost_agent(agent_id)
    return await coro


async def _await_then_flush_audit(coro: Any, audit: Any) -> Any:
    """Await *coro*, then every audit write it scheduled, before the loop closes.

    ``_run_async`` cancels leftover tasks when it closes the per-task loop, which
    is how the final step's audit INSERT used to be destroyed on workers (AUDIT-05).
    """
    try:
        return await coro
    finally:
        flush = getattr(audit, "flush", None)
        if flush is not None:
            lost = await flush()
            if lost:
                logger.error("worker_audit_writes_lost count=%s", lost)


async def _load_worker_policy_engine(db_factory: Any, tenant_id: str) -> Any:
    from app.governance.policies import Policy, PolicyEngine

    engine = PolicyEngine()
    try:
        await engine.reload_from_db(db_factory, tenant_id=tenant_id, strict=True)
    except Exception as exc:
        logger.warning("worker_policy_load_failed_closed: %s", type(exc).__name__)
        engine.add_policy(
            Policy(
                name="worker-policy-load-failed",
                tenant_id=tenant_id,
                denied_tools=["*"],
            )
        )
    return engine


def _build_worker_graph_capability(db_factory: Any) -> Any:
    if db_factory is None:
        return None
    from app.rag.gateway import TenantScopedGraphCapabilityAdapter

    return TenantScopedGraphCapabilityAdapter()


async def _finalize_owning_mission(goal_id: str, tenant_id: str) -> None:
    """Reconcile the org mission that dispatched this goal, now that it is terminal.

    An org mission dispatches a single goal and stores its id in
    ``org_missions.extra_data->>'goal_id'``. Nothing else transitions the mission
    when that goal finishes, so without this hook the mission (and its workstream
    subtasks) stays ``active`` forever and its deliverable is never surfaced.

    This closes the loop from the worker that just marked the goal complete/failed:
    find the still-active owning mission and run the same ``finalize_mission``
    reconciliation the manual endpoint uses — marking subtasks done, aggregating
    the goal's deliverable onto the mission, completing it, and emitting
    ``mission.completed`` on the org event channel. A no-op when the goal has no
    owning mission (an ordinary standalone goal). Best-effort and non-fatal.
    """
    try:
        import types

        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context
        from app.db.session import get_session_factory
        from app.org.service import OrgService
        from app.services.event_store import EventStore
        from app.services.goal_service import GoalService
        from app.tenancy.context import PlanTier, TenantContext

        db_factory = get_session_factory()
        async with db_factory() as session, sqlalchemy_rls_context(session, tenant_id):
            # Finalize EVERY still-active mission that owns this goal — not just
            # one. GoalService dedupes identical goal text to a single goal_id, so
            # two missions can share one goal; a LIMIT 1 here left the others stuck
            # 'active' forever after the shared goal completed.
            rows = (
                await session.execute(
                    text(
                        "SELECT id FROM org_missions "
                        "WHERE tenant_id = :t "
                        "AND extra_data->>'goal_id' = :g "
                        "AND status NOT IN ('completed', 'failed', 'cancelled', 'archived')"
                    ),
                    {"t": tenant_id, "g": goal_id},
                )
            ).fetchall()
            if not rows:
                return  # standalone goal — no owning mission to reconcile

            goal_bridge = GoalService(
                db_session_factory=db_factory,
                event_store=EventStore(db_factory),
            )
            tenant_ctx = TenantContext(
                tenant_id=tenant_id,
                plan=PlanTier.PROFESSIONAL,
                api_key_id="worker_mission_finalize",
            )
            svc = OrgService(session, tenant_id)
            to_publish: list[str] = []
            for row in rows:
                mission_id = str(row[0])
                result = await svc.finalize_mission(
                    mission_id,
                    app_state=types.SimpleNamespace(goal_service=goal_bridge),
                    tenant_ctx=tenant_ctx,
                )
                logger.info(
                    "mission_finalized_from_worker",
                    goal_id=goal_id,
                    mission_id=mission_id,
                    finalized=result.get("finalized"),
                    mission_status=result.get("status"),
                )
                # Scheduled→publish hook: a completed mission carrying a publish
                # target either publishes now (approved) or waits at the gate.
                if result.get("status") == "completed":
                    m = await svc.get_mission(mission_id)
                    extra = (m.extra_data if m else None) or {}
                    pub = extra.get("publish")
                    if isinstance(pub, dict) and not extra.get("published"):
                        if pub.get("approved"):
                            to_publish.append(mission_id)
                        elif not extra.get("publish_pending"):
                            # Hold the deliverable — first run for this schedule
                            # needs a one-time publish approval.
                            m.extra_data = {**extra, "publish_pending": True}  # type: ignore[union-attr]
                            await session.flush()
            await session.commit()
            # Enqueue publish only after the finalize commit is durable.
            for mid in to_publish:
                publish_mission_deliverable.apply_async(
                    kwargs={"mission_id": mid, "tenant_id": tenant_id},
                )
    except Exception as exc:  # never let mission reconciliation break the goal task
        logger.warning("worker mission finalize failed (non-fatal): %s", exc)


async def _record_worker_strategy_evidence(
    *,
    tenant_id: str,
    goal_id: str,
    execution: Any,
    runtime_path: str,
    status: str,
    dry_run: bool,
    db_factory: Any,
) -> None:
    """Record strategy certification evidence for a finished worker goal (CORE-16).

    Never raises: evidence is bookkeeping and must not change the goal outcome.
    """
    succeeded = {"complete": True, "completed": True, "failed": False}.get(status)
    if succeeded is None or dry_run or not isinstance(execution, dict):
        return
    try:
        from app.orchestration.strategy_evidence import (
            StrategyEvidenceRecorder,
            record_goal_strategy_evidence,
        )
        from app.orchestration.strategy_registry import build_default_registry

        recorder = StrategyEvidenceRecorder(
            build_default_registry(), db_factory_getter=lambda: db_factory
        )
        await record_goal_strategy_evidence(
            recorder,
            tenant_id=tenant_id,
            goal_id=goal_id,
            execution_context={
                "strategy_execution": execution,
                "strategy_runtime_path": runtime_path,
            },
            succeeded=succeeded,
        )
    except Exception as exc:
        logger.warning("worker_strategy_evidence_failed goal=%s: %s", goal_id, exc)


def _build_worker_retrieval_gateway(dependencies: Any) -> Any:
    from app.rag.gateway import RetrievalGateway

    return RetrievalGateway(dependencies)


def _build_worker_raft_service(settings: Any, db_factory: Any) -> Any:
    from app.rag.raft_wiring import build_raft_service

    return build_raft_service(settings, session_factory=db_factory)


# ── Deployment (on-prem / NVIDIA / hybrid) provider, once per worker process ──
# Old bug: the API builds its provider with build_onprem_provider (a
# MultiEndpointLLMProvider fronting NVIDIA + on-prem Qwen) and GoalService pins a
# per-goal role map, but run_goal only tried the tenant Redis config and the env
# registry — so worker-executed goals never saw the multi-endpoint provider nor
# the role map, and hybrid routing (plan on NVIDIA, execute/verify on Qwen) did
# not apply to anything that went through Celery. The provider is stateless
# config + HTTP clients, so building it once per process and reusing it is safe.
_WORKER_PROVIDER_UNSET: Any = object()
_WORKER_DEPLOYMENT_PROVIDER: Any = _WORKER_PROVIDER_UNSET
_WORKER_PROVIDER_LOCK = threading.Lock()


def _reset_worker_deployment_provider() -> None:
    """Drop the cached deployment provider (tests / settings reload)."""
    global _WORKER_DEPLOYMENT_PROVIDER
    with _WORKER_PROVIDER_LOCK:
        _WORKER_DEPLOYMENT_PROVIDER = _WORKER_PROVIDER_UNSET


def _worker_deployment_provider() -> Any:
    """The same deployment cluster provider the API builds, or None when unconfigured.

    Built at most once per worker process (``None`` is cached too, so an
    unconfigured deployment does not re-read settings on every goal).
    """
    global _WORKER_DEPLOYMENT_PROVIDER
    if _WORKER_DEPLOYMENT_PROVIDER is not _WORKER_PROVIDER_UNSET:
        return _WORKER_DEPLOYMENT_PROVIDER
    with _WORKER_PROVIDER_LOCK:
        if _WORKER_DEPLOYMENT_PROVIDER is _WORKER_PROVIDER_UNSET:
            built: Any = None
            try:
                from app.core.config import get_settings
                from app.providers import onprem as _onprem_mod

                built = _onprem_mod.build_onprem_provider(get_settings())
            except Exception as exc:
                logger.warning("worker_onprem_provider_build_failed: %s", exc)
                built = None
            _WORKER_DEPLOYMENT_PROVIDER = built
        return _WORKER_DEPLOYMENT_PROVIDER


def _worker_role_map(provider: Any) -> dict[str, str]:
    """Per-goal role map for *provider* — identical to GoalService's computation."""
    try:
        from app.ai_router.deployment_roles import deployment_role_models, servable_models

        servable = servable_models(provider)
        if servable:
            return deployment_role_models(servable=servable)
    except Exception as exc:
        logger.warning("worker_model_role_map_failed: %s", exc)
    return {}


def _tv_session_factory() -> Any:
    """Session factory used to load a tenant's envelope key (TENANT-ENVELOPE-ALL)."""
    from app.db.session import get_session_factory

    return get_session_factory()


def _worker_db_session() -> Any:
    """A session from the CURRENT module engine (the worker disposes it per task)."""
    from app.db.session import get_session_factory

    return get_session_factory()()


def _bind_worker_cost_breakdown_db() -> None:
    """Record worker-run goal costs to Postgres, not this worker's memory.

    Old bug: only the API lifespan wired cost-breakdown persistence, so every
    goal executed here recorded its per-role costs into the worker process and
    the API's cost-metrics endpoint showed an empty breakdown. A binding made by
    an in-process API app (eager tasks) is kept.
    """
    try:
        from app.observability import cost_breakdown as _cb

        if _cb._db is None:
            _cb.configure_db(_worker_db_session)
    except Exception as exc:
        logger.warning("worker_cost_breakdown_db_bind_failed: %s", exc)


def _worker_usage_service() -> Any:
    """A DB-backed UsageService for metering worker-run goals (or None).

    Resolves the CURRENT session factory each call (the worker disposes the
    engine after every task). Metering calls flush immediately, so nothing is
    left in this process's buffer when the task ends.
    """
    try:
        from app.db.session import get_session_factory
        from app.services.usage_service import UsageService

        return UsageService(db_factory=get_session_factory())
    except Exception as exc:
        logger.warning("worker_usage_service_unavailable: %s", exc)
        return None


def _record_goal_duration_metric(status: str, *, started_monotonic: float, priority: str) -> None:
    try:
        from app.observability.metrics import record_goal_duration

        record_goal_duration(status, _monotonic() - started_monotonic, priority)
    except Exception as exc:
        logger.warning("Goal duration metric recording skipped: %s", exc)


def _get_llm_provider(tenant_id: str) -> Any:
    """Load the tenant's configured LLM provider from Redis.

    Uses a synchronous Redis client since Celery tasks run in a regular
    (non-async) thread.  Returns *None* only when the tenant verifiably has no
    stored provider config. When the config cannot be READ (Redis cache error
    with no durable store to confirm, or a durable-store read failure) it raises
    TenantProviderError: treating "unknown" as "no BYOK" silently ran the tenant's
    goal on the platform provider, at platform cost.
    """
    import json
    import os

    from app.providers.tenant_provider import TenantProviderError

    config: dict[str, Any] | None = None
    cache_error: Exception | None = None
    try:
        redis_url = os.getenv("REDIS_URL", "")
        if redis_url:
            import redis as sync_redis

            redis_from_url = cast(Any, sync_redis.from_url)
            r = redis_from_url(redis_url, decode_responses=True)
            raw = r.get(f"llm_config:{tenant_id}")
            config = json.loads(raw) if raw is not None else None
    except Exception as exc:
        # Redis is only a cache; the durable store below is authoritative.
        logger.warning("Could not load tenant LLM config from Redis: %s", exc)
        cache_error = exc
    if config is None:
        # Redis is only a cache: the config is durable in tenant_llm_configs.
        # Reading Redis alone lost the tenant's provider whenever the key was
        # evicted/expired (or Redis was flushed).
        try:
            from app.services.llm_config_store import get_or_create_worker_llm_config_store

            store = get_or_create_worker_llm_config_store()
            if store is None:
                # PROV-12: no durable store means "BYOK unknown", not "no BYOK".
                raise RuntimeError(
                    "no durable tenant LLM config store"
                    + (f" (cache read failed: {cache_error})" if cache_error else "")
                )
            config = _run_async(store.get_config(tenant_id, strict=True))
        except Exception as exc:
            raise TenantProviderError(
                f"tenant LLM config could not be read ({type(exc).__name__}); the goal "
                "is not run on the platform provider in its place"
            ) from exc
    if config is None:
        return None

    # Built by the SAME helper as the API path and the workflow steps
    # (app.providers.llm_resolution, BYOK-3). This copy used to send
    # groq/together keys to api.openai.com when base_url was empty, return None
    # (→ platform provider) for gemini/nvidia/openrouter configs, and swallow a
    # decrypt failure into the same platform fallback. A tenant with BYOK now
    # gets its provider or TenantProviderError, which run_goal turns into a
    # failed goal — never silent platform spend.
    from app.providers.llm_resolution import abuild_tenant_byok_provider

    return _run_async(
        abuild_tenant_byok_provider(
            config, tenant_id, embed_model=os.getenv("EMBEDDING_MODEL") or None
        )
    )


_WORKER_SIGNAL_POLL_SECONDS = 5.0


def _pause_gate_host(runner: Any) -> Any:
    """The object in the runner wrapper chain that honours ``_pause_gate``, or None."""
    seen: set[int] = set()
    current = runner
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        attrs = getattr(current, "__dict__", {})
        if "_pause_gate" in attrs:
            return current
        current = attrs.get("_runner") or attrs.get("_inner")
    return None


def _make_worker_pause_gate(
    goal_id: str,
    sync_r: Any,
    event_callback: Any,
    *,
    tenant_id: str | None = None,
    org_id: str | None = None,
    org_unverified: bool = False,
) -> Any:
    """Step-boundary gate for worker runs, driven by the cross-replica Redis flags.

    With *tenant_id* it also enforces the tenant/org emergency stop at every
    step boundary (fail closed on a Redis error) — the worker used to check it
    only once, before the goal started.
    """
    from app.governance.emergency_stop import enforce_emergency_stop_sync
    from app.reliability.goal_lifecycle import GoalCancelledError, is_cancelled_sync, is_paused_sync

    async def _emit(event: dict[str, Any]) -> None:
        if event_callback is not None:
            with contextlib.suppress(Exception):
                await event_callback(event)

    async def _gate() -> None:
        if is_cancelled_sync(goal_id, sync_r):
            raise GoalCancelledError(f"Goal {goal_id} cancelled")
        if tenant_id:
            _stop = enforce_emergency_stop_sync(
                sync_r, tenant_id, org_id, org_unverified=org_unverified
            )
            if _stop:
                raise GoalCancelledError(f"Goal {goal_id} stopped: {_stop}")
        if not is_paused_sync(goal_id, sync_r):
            return
        logger.info("goal_paused_in_worker goal_id=%s", goal_id)
        await _emit({"type": "goal_paused_at_step_boundary"})
        while is_paused_sync(goal_id, sync_r):
            await asyncio.sleep(_WORKER_SIGNAL_POLL_SECONDS)
            if is_cancelled_sync(goal_id, sync_r):
                raise GoalCancelledError(f"Goal {goal_id} cancelled while paused")
        logger.info("goal_resumed_in_worker goal_id=%s", goal_id)
        await _emit({"type": "goal_execution_resumed"})

    return _gate


async def _run_with_signals(
    agent_runner: Any,
    goal: str,
    tenant_ctx: Any,
    event_callback: Any,
    goal_id: str,
    initial_context: dict[str, Any] | None = None,
    *,
    org_id: str | None = None,
    org_unverified: bool = False,
) -> Any:
    """Run agent_runner.run() while observing cross-replica pause/cancel signals.

    *org_id* is the goal's organisation (``goals.execution_context.org_id``,
    resolved by ``run_goal``): an org emergency stop halts the run too; with
    *org_unverified* (the org could not be read) any org stop of the tenant
    does. A stop's pub/sub announcement wakes the poll immediately (INC-04).

    The signals are the Redis flags any API replica sets (``pause_goal`` /
    ``cancel_goal`` / ``resume_goal``, app/reliability/goal_lifecycle.py).

    * Cancel → the run is cancelled and ``GoalCancelledError`` raised (polled
      every ``_WORKER_SIGNAL_POLL_SECONDS``; the pause gate also raises it at
      the next step boundary).
    * Pause → when the runner exposes a step-boundary pause gate (AgentGraph's
      ``_pause_gate``) the worker installs one that blocks between steps until
      the flag is cleared — never mid tool call, and the run continues where it
      stopped. Legacy runners without a gate fall back to cancel-and-rerun.
    """
    sync_r = _get_sync_redis()
    gate_host = _pause_gate_host(agent_runner)
    _tenant_id = getattr(tenant_ctx, "tenant_id", None)
    _org = org_id or (initial_context or {}).get("org_id")
    _org_id = str(_org) if _org else None
    if gate_host is not None and sync_r is not None:
        gate_host._pause_gate = _make_worker_pause_gate(
            goal_id,
            sync_r,
            event_callback,
            tenant_id=_tenant_id,
            org_id=_org_id,
            org_unverified=org_unverified,
        )

    run_task = asyncio.create_task(
        agent_runner.run(
            goal=goal,
            tenant_ctx=tenant_ctx,
            initial_context=initial_context,
            event_callback=event_callback,
            goal_id=goal_id,
        )
    )

    wake = asyncio.Event()
    listener = (
        asyncio.create_task(_listen_for_emergency_stop(_tenant_id, wake))
        if _tenant_id and sync_r is not None
        else None
    )
    try:
        return await _signal_poll_loop(
            run_task,
            wake,
            sync_r=sync_r,
            gate_host=gate_host,
            agent_runner=agent_runner,
            goal=goal,
            tenant_ctx=tenant_ctx,
            event_callback=event_callback,
            goal_id=goal_id,
            initial_context=initial_context,
            tenant_id=_tenant_id,
            org_id=_org_id,
            org_unverified=org_unverified,
        )
    finally:
        if listener is not None:
            listener.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await listener


async def _listen_for_emergency_stop(tenant_id: str, wake: asyncio.Event) -> None:
    """Set *wake* whenever a stop of *tenant_id* (or one of its orgs) is announced.

    Best effort: without Redis pub/sub the run still sees the persisted flag at
    its next poll / step boundary.
    """
    from app.governance.emergency_stop import stop_channel

    redis = _worker_async_redis()
    if redis is None:
        return
    pubsub = None
    try:
        pubsub = redis.pubsub()
        await pubsub.subscribe(stop_channel(tenant_id))
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg is not None and msg.get("type") == "message":
                wake.set()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("emergency_stop_listen_failed: %s", type(exc).__name__)
    finally:
        if pubsub is not None:
            with contextlib.suppress(Exception):
                await pubsub.aclose()
        with contextlib.suppress(Exception):
            await redis.aclose()


# Granularity at which a stop announcement (or the run finishing) cuts the
# signal-poll sleep short.
_WAKE_SLICE_SECONDS = 0.25


async def _sleep_until_wake(run_task: Any, wake: asyncio.Event) -> None:
    """Sleep up to one poll interval, returning early once *wake* is set (a stop
    was announced) or the run finished. Plain ``asyncio.sleep`` slices: always
    yields to the loop, nothing swallowed."""
    remaining = _WORKER_SIGNAL_POLL_SECONDS
    while remaining > 0 and not wake.is_set() and not run_task.done():
        step = min(_WAKE_SLICE_SECONDS, remaining)
        await asyncio.sleep(step)
        remaining -= step
    wake.clear()


async def _signal_poll_loop(
    run_task: Any,
    wake: asyncio.Event,
    *,
    sync_r: Any,
    gate_host: Any,
    agent_runner: Any,
    goal: str,
    tenant_ctx: Any,
    event_callback: Any,
    goal_id: str,
    initial_context: dict[str, Any] | None,
    tenant_id: str | None,
    org_id: str | None,
    org_unverified: bool = False,
) -> Any:
    from app.governance.emergency_stop import enforce_emergency_stop_sync
    from app.reliability.goal_lifecycle import GoalCancelledError, is_cancelled_sync, is_paused_sync

    _tenant_id, _org_id = tenant_id, org_id
    while not run_task.done():
        # Sleep one poll interval — or less, when a stop is announced or the run ends.
        await _sleep_until_wake(run_task, wake)
        # Re-check: task may have completed during the sleep
        if run_task.done():
            break
        if sync_r:
            if is_cancelled_sync(goal_id, sync_r):
                run_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await run_task
                raise GoalCancelledError(f"Goal {goal_id} cancelled during execution")

            # Emergency stop (tenant/org), fail closed like the step gate: it used
            # to be polled nowhere here, so a runner without a step gate — or one
            # stuck in a long step — ran on through a stop (CORE-13).
            _stop = (
                enforce_emergency_stop_sync(
                    sync_r, _tenant_id, _org_id, org_unverified=org_unverified
                )
                if _tenant_id
                else None
            )
            if _stop:
                run_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await run_task
                raise GoalCancelledError(f"Goal {goal_id} stopped: {_stop}")

            if gate_host is None and is_paused_sync(goal_id, sync_r):
                # Pause: cancel current run and wait for resume signal
                run_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await run_task
                logger.info("goal_paused_in_worker goal_id=%s", goal_id)
                while is_paused_sync(goal_id, sync_r):
                    await asyncio.sleep(_WORKER_SIGNAL_POLL_SECONDS)
                    if is_cancelled_sync(goal_id, sync_r):
                        raise GoalCancelledError(f"Goal {goal_id} cancelled while paused")
                logger.info("goal_resumed_in_worker goal_id=%s", goal_id)
                # Re-run from checkpoint (AgentGraph will resume from last durable state)
                run_task = asyncio.create_task(
                    agent_runner.run(
                        goal=goal,
                        tenant_ctx=tenant_ctx,
                        initial_context=initial_context,
                        event_callback=event_callback,
                        goal_id=goal_id,
                    )
                )

    return await run_task


class _WorkerSubgoalService:
    """GoalService facade the worker graph uses to dispatch and await sub-goals.

    ``submit_goal`` persists + enqueues the sub-goal (CeleryGoalTaskQueue), then
    drops the worker-local in-memory record: the sub-goal runs on another worker,
    so its events never reach that record. Without a local record
    ``subscribe_events`` takes GoalService's cross-process path (persisted-event
    replay + Redis ``goal_events:{tenant}:{goal}`` subscription), which is where
    the executing worker publishes.
    """

    def __init__(self, goal_service: Any) -> None:
        self._gs = goal_service

    async def submit_goal(self, **kwargs: Any) -> dict[str, Any]:
        result: dict[str, Any] = await self._gs.submit_goal(**kwargs)
        goal_id = str(result.get("goal_id") or "")
        if goal_id and not result.get("deduplicated"):
            self._gs._goals.pop(goal_id, None)
        return result

    def subscribe_events(
        self, goal_id: str, tenant_ctx: Any, since_sequence: int = 0
    ) -> Any:
        return self._gs.subscribe_events(
            goal_id=goal_id, tenant_ctx=tenant_ctx, since_sequence=since_sequence
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._gs, name)


class _WorkerMCPAgentRunner:
    def __init__(
        self,
        runner: Any,
        context_factory: Any,
        system_prompt: str = "",
        rpa_executor: Any = None,
    ) -> None:
        self._runner = runner
        self._context_factory = context_factory
        self._system_prompt = system_prompt
        # The run's RPAExecutor: vault:// refs resolve through this run's Redis
        # connector secret store, and its browser sessions die with the run.
        self._rpa_executor = rpa_executor

    async def run(
        self,
        *,
        goal: str,
        tenant_ctx: Any,
        initial_context: dict[str, Any] | None = None,
        event_callback: Any = None,
        goal_id: str | None = None,
        attempt: int | None = None,
    ) -> Any:
        redis_client = None
        context = dict(initial_context or {})
        # Inject agent system prompt so the planner uses it
        if self._system_prompt:
            context["system_prompt"] = self._system_prompt
        try:
            redis_client, mcp_client, tool_context = await self._context_factory()
            if mcp_client is not None:
                self._runner._mcp_client = mcp_client
            if self._rpa_executor is not None and redis_client is not None:
                from app.mcp.connector_wiring import build_connector_secret_store

                _rpa_secret_store = build_connector_secret_store(redis_client)
                self._rpa_executor._secret_store_resolver = lambda: _rpa_secret_store
                # The worker's browsers count toward the tenant's global session cap
                # and appear in the shared session registry (RPA-02).
                _rpa_sm = getattr(self._rpa_executor, "_session_manager", None)
                if _rpa_sm is not None:
                    _rpa_sm._redis = redis_client
            if tool_context is not None:
                context.update(
                    {
                        "tool_prompt": tool_context.to_prompt_block(),
                        "tool_context": tool_context,
                    }
                )
            return await self._runner.run(
                goal=goal,
                tenant_ctx=tenant_ctx,
                initial_context=context or None,
                event_callback=event_callback,
                goal_id=goal_id,
                **({"attempt": attempt} if attempt is not None else {}),
            )
        finally:
            if self._rpa_executor is not None:
                # Close every browser/Playwright session the run opened — in this
                # loop, before it is torn down — so no Chromium outlives the task.
                try:
                    await self._rpa_executor.aclose()
                except Exception as _rpa_close_exc:
                    logger.warning("worker_rpa_executor_close_failed: %s", _rpa_close_exc)
            if redis_client is not None:
                await redis_client.aclose()


async def _goal_persistence_settings(
    goal_id: str, tenant_id: str
) -> tuple[bool, dict[str, Any]]:
    """``(persistence_mode, persistence_config)`` from goals.execution_context.

    The API path runs such goals through GoalPersistenceEngine; queued goals —
    every production goal — used to get exactly one attempt in the worker.
    Mirrors GoalService: the admitted runtime profile's
    ``agent_patterns.persistence_mode`` wins over the raw request flag.
    """
    import json as _json

    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context
    from app.db.session import get_session_factory

    db = get_session_factory()
    async with db() as session, sqlalchemy_rls_context(session, tenant_id):
        raw = (
            await session.execute(
                text("SELECT execution_context FROM goals WHERE id = :g AND tenant_id = :t"),
                {"g": goal_id, "t": tenant_id},
            )
        ).scalar()
    ctx = raw if isinstance(raw, dict) else _json.loads(raw) if raw else {}
    if not isinstance(ctx, dict):
        return False, {}
    mode = bool(ctx.get("persistence_mode", False))
    profile = ctx.get("runtime_profile")
    if isinstance(profile, dict):
        patterns = profile.get("agent_patterns")
        if isinstance(patterns, dict) and "persistence_mode" in patterns:
            mode = bool(patterns.get("persistence_mode"))
    cfg = ctx.get("persistence_config")
    return mode, dict(cfg) if isinstance(cfg, dict) else {}


def _worker_persistence_config(cfg: dict[str, Any], goal_timeout_s: float) -> Any:
    """PersistenceConfig for a worker goal, bounded by the goal's hard timeout."""
    from app.agent.persistence import PersistenceConfig

    requested_total = float(cfg.get("total_timeout_seconds", 0.0) or 0.0)
    # The whole run is wrapped in wait_for(goal_timeout_s); keep the engine's own
    # budget inside it so it ends with its real outcome, not a bare timeout.
    ceiling = max(1.0, goal_timeout_s * 0.9)
    total = min(requested_total, ceiling) if requested_total > 0 else ceiling
    return PersistenceConfig(
        max_attempts=int(cfg.get("max_attempts", 10)),
        iterations_per_attempt=int(cfg.get("iterations_per_attempt", 15)),
        base_backoff_seconds=float(cfg.get("base_backoff_seconds", 30.0)),
        max_backoff_seconds=float(cfg.get("max_backoff_seconds", 600.0)),
        strategy_switch_after=int(cfg.get("strategy_switch_after", 2)),
        escalate_after_failures=int(cfg.get("escalate_after_failures", 6)),
        total_timeout_seconds=total,
        decompose_on_failure=bool(cfg.get("decompose_on_failure", True)),
    )


def _worker_reflexion_service(db_factory: Any, embedder_provider: Any) -> Any:
    """DB-backed canonical Reflexion memory for a worker goal (None without a DB)."""
    if db_factory is None:
        return None
    from app.memory.embedding import memory_embedder_from_provider
    from app.memory.postgres_repository import PostgresMemoryRepository
    from app.memory.reflexion import ReflexionService

    return ReflexionService(
        repository=PostgresMemoryRepository(
            db_factory,
            embedder=memory_embedder_from_provider(embedder_provider),
        )
    )


def _timed_out_goal_state(goal: str, tenant_ctx: Any, goal_id: str, timeout_s: Any) -> Any:
    from app.agent.state import AgentState, GoalStatus

    state = AgentState(goal=goal, tenant_ctx=tenant_ctx)
    state.goal_id = goal_id
    state.status = GoalStatus.FAILED
    state.error_message = f"Goal timed out after {timeout_s}s"
    return state


async def _learn_from_worker_goal(
    reflexion_service: Any,
    state: Any,
    *,
    tenant_id: str,
    goal_id: str,
    dry_run: bool,
    agent_id: str | None,
) -> Any:
    """Reflexion learning for a terminal worker goal (bounded; never raises)."""
    from app.memory.goal_learning import learn_from_goal_outcome

    return await learn_from_goal_outcome(
        reflexion_service,
        state,
        tenant_id=tenant_id,
        goal_id=goal_id,
        dry_run=dry_run,
        agent_id=agent_id,
    )


class _PersistentWorkerRunner:
    """Runs a worker goal through GoalPersistenceEngine (retry until success).

    Same contract as the wrapped runner's ``run`` — it returns the final
    attempt's AgentState, or a FAILED state when no attempt succeeded — so the
    worker's terminal bookkeeping is unchanged.
    """

    def __init__(
        self,
        inner: Any,
        *,
        config: Any,
        db: Any = None,
        redis: Any = None,
        hitl_gateway: Any = None,
    ) -> None:
        self._inner = inner
        self._config = config
        self._db = db
        self._redis = redis
        self._hitl = hitl_gateway

    async def run(
        self,
        *,
        goal: str,
        tenant_ctx: Any,
        initial_context: dict[str, Any] | None = None,
        event_callback: Any = None,
        goal_id: str | None = None,
    ) -> Any:
        from app.agent.persistence import GoalPersistenceEngine
        from app.agent.state import AgentState, GoalStatus

        inner = self._inner
        last: dict[str, Any] = {}

        class _Attempt:
            async def run(
                self_inner: _Attempt,  # noqa: N805
                *,
                goal: str,
                tenant_ctx: Any,
                event_callback: Any = None,
                goal_id: str | None = None,
                attempt: int | None = None,
            ) -> Any:
                state = await inner.run(
                    goal=goal,
                    tenant_ctx=tenant_ctx,
                    initial_context=initial_context,
                    event_callback=event_callback,
                    goal_id=goal_id,
                    attempt=attempt,
                )
                last["state"] = state
                return state

        engine = GoalPersistenceEngine(
            config=self._config, db=self._db, redis=self._redis, hitl_gateway=self._hitl
        )
        success, attempts = await engine.run(
            goal=goal,
            agent_factory=_Attempt(),
            tenant_ctx=tenant_ctx,
            event_callback=event_callback,
            goal_id=goal_id or "",
        )
        final = last.get("state")
        if success and final is not None:
            return final
        failed = final if final is not None else AgentState(goal=goal, tenant_ctx=tenant_ctx)
        failed.status = GoalStatus.FAILED
        last_reason = attempts[-1].failure_reason if attempts else ""
        failed.error_message = (
            f"Goal could not be achieved after {len(attempts)} persistence attempt(s)"
            + (f": {last_reason}" if last_reason else "")
        )[:1000]
        return failed


class _WorkerWorkflowRunner:
    """Runs a ``multi_agent`` goal through the static WorkflowPlanner/Executor.

    Parity with the API's in-process path (``GoalService._run_workflow``): with
    a task queue every goal is handed to this worker, which used to ignore
    ``workflow_mode`` and silently run a multi_agent request as a single
    AgentGraph goal. Same ``run`` contract as the graph runner (returns an
    AgentState), so the worker's signals / timeout / terminal bookkeeping apply.
    """

    def __init__(
        self,
        context_factory: Any,
        *,
        tool_gate: Any,
        goal_id: str,
        retrieval_gateway: Any = None,
    ) -> None:
        self._context_factory = context_factory
        self._tool_gate = tool_gate
        self._goal_id = goal_id
        self._retrieval_gateway = retrieval_gateway

    async def run(
        self,
        *,
        goal: str,
        tenant_ctx: Any,
        initial_context: dict[str, Any] | None = None,
        event_callback: Any = None,
        goal_id: str | None = None,
    ) -> Any:
        from app.agent.state import AgentState, GoalStatus
        from app.agent.workflow_executor import WorkflowExecutor
        from app.agent.workflow_planner import build_static_workflow

        async def _emit(event: dict[str, Any]) -> None:
            if event_callback is not None:
                await event_callback(event)

        redis_client, mcp_client, tool_context = await self._context_factory()
        try:
            await _emit({"type": "goal_started", "goal": goal, "workflow_mode": "multi_agent"})
            plan = build_static_workflow(goal)
            executor = WorkflowExecutor(
                mcp_client=mcp_client,
                # Retrieval steps need the gateway (parity with the API path).
                retrieval_gateway=self._retrieval_gateway,
                tool_gate=self._tool_gate,
                goal_id=goal_id or self._goal_id,
            )
            wf_result = await executor.execute(
                plan,
                tenant_ctx,
                tool_context=tool_context,
                event_callback=event_callback,
                goal=goal,
            )
        finally:
            if redis_client is not None:
                with contextlib.suppress(Exception):
                    await redis_client.aclose()
        state = AgentState(goal=goal, tenant_ctx=tenant_ctx)
        state.goal_id = goal_id or self._goal_id
        state.iterations = len(getattr(plan, "steps", []) or [])
        wf_status = str((wf_result or {}).get("status", "complete"))
        if wf_status == "complete":
            state.status = GoalStatus.COMPLETE
            await _emit({"type": "goal_complete"})
        else:
            reason = str(
                (wf_result or {}).get("reason")
                or (wf_result or {}).get("error")
                or f"workflow ended with status {wf_status}"
            )
            state.status = GoalStatus.FAILED
            state.error_message = reason[:1000]
            await _emit({"type": "goal_failed", "reason": reason})
        return state


def _worker_tool_gate(policy: Any, hitl: Any, cost: Any, agent_id: str) -> Any:
    """The governed tool gate for worker workflow runs (same services as the graph).

    RV-05: wired with the same durable, tenant-scoped (RLS) ``PostgresGrantStore``
    and default-deny permission matrix as the API's ``app.state``. Without them
    every tool call of a queued multi_agent goal was denied under the default
    grant enforcement (``grant_store_unavailable``). With no DB factory the store
    stays unset and enforcement still denies — it never fails open.
    """
    import types as _types

    from app.agent.tool_gate import gate_from_app_state
    from app.governance.permissions import build_default_permission_matrix

    db_factory: Any = None
    with contextlib.suppress(Exception):
        from app.db.session import get_session_factory

        db_factory = get_session_factory()
    grant_store: Any = None
    if db_factory is not None:
        try:
            from app.governance.grants.postgres_store import PostgresGrantStore

            grant_store = PostgresGrantStore(db_factory)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("worker_grant_store_wire_failed: %s", exc)
    return gate_from_app_state(
        _types.SimpleNamespace(
            policy_engine=policy,
            hitl_gateway=hitl,
            cost_controller=cost,
            grant_store=grant_store,
            permission_matrix=build_default_permission_matrix(),
            # PERM-01: per-agent permission rules + the shared daily counter.
            db_session_factory=db_factory,
            _redis=_worker_async_redis(),
        ),
        agent_id=agent_id or None,
    )


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.scaling.tasks.execute_org_mission",
    bind=True,
    max_retries=0,
    # At-least-once: ack only after the task finishes, and redeliver (don't drop)
    # if the worker is lost mid-task — so a worker restart/deploy can't leave a
    # mission stuck on "planned". The idempotency guard below makes the redeliver
    # safe (it no-ops once the mission has moved past "planned").
    acks_late=True,
    reject_on_worker_lost=True,
)
def execute_org_mission(
    self: Any,
    mission_id: str,
    tenant_id: str,
    org_id: str,
    objective: str = "",
    title: str = "",
    expected_outcome: str = "",
    dept_id: str | None = None,
    assigned_team_id: str | None = None,
    autonomy_level: int | None = None,
    priority: str = "medium",
) -> dict[str, Any]:
    """Form the team and dispatch an already-created mission, in the worker.

    The mission row is created + committed by the API before this is enqueued;
    here we (LLM) form the team and dispatch the goal. Running in the worker —
    a separate process with its own event loop and DB connections — avoids the
    request-scoped context/pool contention that made in-process background
    execution hang.
    """
    import types

    from app.db.rls import sqlalchemy_rls_context
    from app.db.session import get_session_factory
    from app.org.service import OrgService
    from app.services.event_store import EventStore
    from app.services.goal_service import GoalService
    from app.tenancy.context import PlanTier, TenantContext

    async def _execute() -> None:
        db_factory = get_session_factory()
        # The bridge MUST carry a task queue: form_team_and_dispatch → submit_goal
        # only enqueues the run_goal worker task when task_queue is set. Without
        # it the goal is created but never runs, leaving the mission stuck
        # 'active' forever (and its deliverable never produced, so the
        # finalize→publish hook can never fire). CeleryGoalTaskQueue is a
        # stateless apply_async adapter — safe to build in the worker.
        from app.services.goal_queue import CeleryGoalTaskQueue

        goal_bridge = GoalService(
            db_session_factory=db_factory,
            event_store=EventStore(db_factory),
            task_queue=CeleryGoalTaskQueue(),
        )
        provider: Any = None
        try:
            from app.providers.registry import resolve_provider

            provider = resolve_provider()
        except Exception as prov_exc:  # degrade to heuristic team formation
            logger.warning("execute_org_mission.provider_unavailable", error=str(prov_exc)[:120])
        app_state = types.SimpleNamespace(goal_service=goal_bridge, _app_provider=provider)
        tenant_ctx = TenantContext(
            tenant_id=tenant_id,
            plan=PlanTier.PROFESSIONAL,
            api_key_id="worker_mission_execute",
        )
        from sqlalchemy import text

        async with (
            db_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            # This transaction deliberately spans the LLM-heavy team-formation
            # planning (plan_mission, decompose_and_assign, agent routing) — each
            # a multi-second call during which the transaction sits idle. The
            # engine sets idle_in_transaction_session_timeout=30s by default to
            # reclaim connections from cancelled requests; that timer would kill
            # this connection mid-planning (Postgres then rejects the next
            # statement with "Can't operate on closed transaction"). Disable it
            # for THIS transaction only — SET LOCAL reverts when the transaction
            # ends, so other transactions keep the 30s reclaim guard.
            await session.execute(text("SET LOCAL idle_in_transaction_session_timeout = 0"))
            svc = OrgService(session, tenant_id)
            # Atomic claim: lock the mission row FOR UPDATE and check its status
            # under the lock. Two tasks racing the same mission (a sweep
            # re-enqueue overlapping the original/redelivery) are serialized —
            # the loser blocks on the lock until the winner's transaction commits,
            # then reads the now-dispatched status and skips. A plain
            # read-then-check was a TOCTOU race: the status only flips to 'active'
            # at the END of form_team_and_dispatch (~30-90s later), so both tasks
            # would see 'planned' and each dispatch a duplicate team + goal.
            locked = (
                await session.execute(
                    text("SELECT status FROM org_missions WHERE id = :mid FOR UPDATE"),
                    {"mid": mission_id},
                )
            ).first()
            if locked is None:
                logger.warning("execute_org_mission.mission_missing", mission_id=mission_id)
                return
            if locked.status not in ("planned", "draft"):
                logger.info(
                    "execute_org_mission.already_processed",
                    mission_id=mission_id,
                    status=locked.status,
                )
                return
            mission = await svc.get_mission(mission_id)
            if mission is None:
                logger.warning("execute_org_mission.mission_missing", mission_id=mission_id)
                return
            await svc.form_team_and_dispatch(
                mission=mission,
                org_id=org_id,
                objective=objective,
                title=title,
                expected_outcome=expected_outcome,
                dept_id=dept_id,
                assigned_team_id=assigned_team_id,
                autonomy_level=autonomy_level,
                priority=priority,
                tenant_ctx=tenant_ctx,
                app_state=app_state,
            )

    async def _mark_failed(err: str) -> None:
        with contextlib.suppress(Exception):
            db_factory = get_session_factory()
            async with (
                db_factory() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                svc = OrgService(session, tenant_id)
                m = await svc.get_mission(mission_id)
                # Only fail a mission still awaiting dispatch — a transient error
                # in a duplicate/redelivered task must never clobber a mission
                # that already went active/review/completed.
                if m is not None and m.status in ("planned", "draft"):
                    await svc.update_mission_status(mission_id, "failed")
        logger.error("execute_org_mission.failed", mission_id=mission_id, error=err[:200])

    try:
        _run_async(_execute())
        return {"status": "dispatched", "mission_id": mission_id}
    except Exception as exc:
        _run_async(_mark_failed(str(exc)))
        return {"status": "failed", "mission_id": mission_id, "error": str(exc)[:200]}


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.scaling.tasks.resweep_stuck_missions", bind=True, max_retries=0
)
def resweep_stuck_missions(self: Any) -> dict[str, Any]:
    """Re-enqueue missions stuck in 'planned' past the normal dispatch window.

    A worker crash (hard kill) can drop an in-flight execute_org_mission task —
    the Redis broker only redelivers acks_late tasks after its visibility
    timeout (an hour by default). This sweep is the deterministic safety net:
    any mission still 'planned'/'draft' with no goal after 3 minutes is
    re-enqueued. execute_org_mission's idempotency guard (and GoalService goal
    dedup) make a re-enqueue safe. The 3-minute floor is well beyond a normal
    dispatch (~30-90s) so genuinely in-flight missions are never swept.
    """
    from sqlalchemy import text

    from app.db.rls import system_session
    from app.db.session import get_system_session_factory

    async def _sweep() -> int:
        # Cross-tenant beat scan: the maintenance (BYPASSRLS) role. Under the
        # NOBYPASSRLS application role system_session() fails every statement.
        db = get_system_session_factory()
        async with db() as session, session.begin(), system_session(session):
            rows = (
                await session.execute(
                    text(
                        "SELECT id, tenant_id, org_id, title, objective, "
                        "expected_outcome, dept_id, assigned_team_id, "
                        "autonomy_level, priority "
                        "FROM org_missions "
                        "WHERE status IN ('planned', 'draft') "
                        "AND (extra_data->>'goal_id') IS NULL "
                        "AND created_at < now() - interval '3 minutes' "
                        "LIMIT 50"
                    )
                )
            ).fetchall()
        for r in rows:
            # These columns are UUID type; asyncpg returns them as uuid.UUID.
            # The tenant system keys on the 32-char hex form (no dashes), while
            # org/dept/team ids are used as dashed UUID strings elsewhere.
            execute_org_mission.apply_async(
                kwargs={
                    "mission_id": str(r.id),
                    "tenant_id": r.tenant_id.hex if r.tenant_id else "",
                    "org_id": str(r.org_id),
                    "objective": r.objective or "",
                    "title": r.title or "",
                    "expected_outcome": r.expected_outcome or "",
                    "dept_id": str(r.dept_id) if r.dept_id else None,
                    "assigned_team_id": str(r.assigned_team_id) if r.assigned_team_id else None,
                    "autonomy_level": r.autonomy_level,
                    "priority": r.priority or "medium",
                },
            )
        return len(rows)

    count = _run_async(_sweep())
    if count:
        logger.info("resweep_stuck_missions.reenqueued", count=count)
    return {"reenqueued": count}


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.scaling.tasks.fire_due_org_mission_schedules", bind=True, max_retries=0
)
@beat_task_guard(lock_ttl_seconds=120)
def fire_due_org_mission_schedules(self: Any) -> dict[str, Any]:
    """Launch org missions for every cron schedule that is now due.

    Beat-scheduled (every 60s). Scans org_mission_schedules across tenants,
    creates the mission for each due schedule, advances its next_fire_at, and
    dispatches through the crash-safe execute_org_mission worker. This is how a
    mission runs autonomously with no human in the loop.
    """
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context, system_session
    from app.db.session import get_session_factory, get_system_session_factory
    from app.org.service import OrgService, _next_cron_fire

    async def _fire() -> int:
        # Two roles, deliberately: the cross-tenant claim below is system work
        # (maintenance role); creating each mission is per-tenant work and runs
        # on the application role inside that tenant's RLS context.
        db = get_session_factory()
        system_db = get_system_session_factory()
        # 1. ATOMIC CLAIM (multi-pod at-most-once): select due schedules with
        # FOR UPDATE SKIP LOCKED so no other worker can see them, and advance
        # next_fire_at in the SAME transaction — so by the time the lock is
        # released each row is no longer due and cannot be re-claimed. This makes
        # firing at-most-once per slot even if the @beat_task_guard lock fails open
        # on a Redis error. Trade-off (deliberate): advancing before creating the
        # mission means a crash here SKIPS a fire rather than double-launching an
        # autonomous mission — the safe direction for unattended execution.
        claimed: list[Any] = []
        async with system_db() as s, s.begin(), system_session(s):
            due = (
                await s.execute(
                    text(
                        "SELECT id, tenant_id, org_id, title, objective, priority, "
                        "autonomy_level, dept_id, cron_expression, timezone, "
                        "publish_config "
                        "FROM org_mission_schedules "
                        "WHERE enabled IS TRUE AND next_fire_at IS NOT NULL "
                        "AND next_fire_at <= now() "
                        "ORDER BY next_fire_at LIMIT 100 FOR UPDATE SKIP LOCKED"
                    )
                )
            ).fetchall()
            for r in due:
                next_fire = _next_cron_fire(r.cron_expression, r.timezone or "UTC")
                await s.execute(
                    text(
                        "UPDATE org_mission_schedules "
                        "SET next_fire_at = :n, last_fired_at = now(), "
                        "fire_count = fire_count + 1, updated_at = now() WHERE id = :sid"
                    ),
                    {"n": next_fire, "sid": r.id},
                )
                claimed.append(r)

        fired = 0
        for r in claimed:
            tenant_id = r.tenant_id.hex
            org_id = str(r.org_id)
            dept_id = str(r.dept_id) if r.dept_id else None
            try:
                # 2. Create the mission in the schedule's own tenant context (RLS),
                # then dispatch after commit. The schedule row was already advanced
                # in the claim above; here we only record last_mission_id.
                async with (
                    db() as s2,
                    s2.begin(),
                    sqlalchemy_rls_context(s2, tenant_id),
                ):
                    svc = OrgService(s2, tenant_id)
                    mission = await svc.create_mission(
                        org_id=org_id,
                        title=r.title,
                        objective=r.objective or "",
                        priority=r.priority or "medium",
                        autonomy_level=r.autonomy_level,
                        dept_id=dept_id,
                        source="schedule",
                    )
                    mission_id = str(mission.id)
                    # Stamp the schedule's publish target onto the mission so the
                    # finalize→publish hook knows where to send the deliverable.
                    pub_cfg = r.publish_config if isinstance(r.publish_config, dict) else None
                    if pub_cfg and pub_cfg.get("connector_server_id") and pub_cfg.get("tool_name"):
                        stamped = {**pub_cfg, "schedule_id": str(r.id)}
                        mission.extra_data = {**(mission.extra_data or {}), "publish": stamped}
                        await s2.flush()
                    await svc.update_mission_status(mission_id, "planned")
                    await s2.execute(
                        text(
                            "UPDATE org_mission_schedules "
                            "SET last_mission_id = :m "
                            "WHERE id = :sid AND tenant_id = :tid"
                        ),
                        {"m": mission.id, "sid": r.id, "tid": r.tenant_id},
                    )
                execute_org_mission.apply_async(
                    kwargs={
                        "mission_id": mission_id,
                        "tenant_id": tenant_id,
                        "org_id": org_id,
                        "objective": r.objective or "",
                        "title": r.title,
                        "priority": r.priority or "medium",
                        "autonomy_level": r.autonomy_level,
                        "dept_id": dept_id,
                    },
                    # Stable task id → Celery dedupes a redelivery of this exact fire.
                    task_id=f"orgmission:{mission_id}",
                )
                fired += 1
            except Exception as exc:
                logger.error(
                    "fire_due_org_mission_schedules.failed",
                    schedule_id=str(r.id),
                    error=str(exc)[:200],
                )
        if fired:
            logger.info("fire_due_org_mission_schedules.fired", count=fired)
        return fired

    return {"fired": _run_async(_fire())}


def _deliverable_text(extra: dict[str, Any]) -> str:
    """Best-effort human-readable text of a finalized mission's deliverable."""
    result = extra.get("result") if isinstance(extra, dict) else None
    deliverable: Any = result.get("deliverable") if isinstance(result, dict) else None
    if deliverable is None:
        deliverable = result
    if deliverable is None:
        return ""
    if isinstance(deliverable, str):
        return deliverable
    if isinstance(deliverable, dict):
        # Common shapes: {"output": ...}, {"content": ...}, {"text": ...}.
        for key in ("output", "content", "text", "result", "summary"):
            val = deliverable.get(key)
            if isinstance(val, str) and val.strip():
                return val
        import json as _json

        return _json.dumps(deliverable, ensure_ascii=False, indent=2)
    return str(deliverable)


def _render_publish_args(value: Any, ctx: dict[str, str]) -> Any:
    """Substitute ``{{deliverable}}`` / ``{{title}}`` / ``{{objective}}`` tokens.

    Walks the argument template recursively so a placeholder can live anywhere —
    a top-level string, a nested object field, or a list item.
    """
    if isinstance(value, str):
        out = value
        for token, repl in ctx.items():
            out = out.replace("{{" + token + "}}", repl)
        return out
    if isinstance(value, dict):
        return {k: _render_publish_args(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_render_publish_args(v, ctx) for v in value]
    return value


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.scaling.tasks.publish_mission_deliverable",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def publish_mission_deliverable(
    self: Any,
    mission_id: str,
    tenant_id: str,
) -> dict[str, Any]:
    """Publish a finalized scheduled mission's deliverable via its connector.

    Enqueued by the finalize→publish hook only once the mission's stamped
    ``extra_data['publish']`` is approved (per-schedule one-time gate). Builds a
    worker-local MCP client — builtins and real (Redis/vault-backed) connectors
    both resolve here — calls the configured tool with the deliverable
    substituted into the argument template, and records a receipt on the mission
    (``extra_data['published']``) plus a ``mission.published`` org event.

    Idempotent: a redelivery after a successful publish no-ops on the receipt.
    """
    from app.db.rls import sqlalchemy_rls_context
    from app.db.session import get_session_factory
    from app.org.service import OrgService
    from app.tenancy.context import PlanTier, TenantContext

    async def _publish() -> dict[str, Any]:
        db_factory = get_session_factory()
        tenant_ctx = TenantContext(
            tenant_id=tenant_id,
            plan=PlanTier.PROFESSIONAL,
            api_key_id="worker_mission_publish",
        )
        # ── Phase A: load + validate under RLS, capture what we need to publish ──
        async with (
            db_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            svc = OrgService(session, tenant_id)
            mission = await svc.get_mission(mission_id)
            if mission is None:
                logger.warning("publish_mission.mission_missing", mission_id=mission_id)
                return {"status": "missing"}
            extra = dict(mission.extra_data or {})
            pub = extra.get("publish")
            if not isinstance(pub, dict) or not pub.get("connector_server_id"):
                return {"status": "no_publish_config"}
            if extra.get("published"):
                return {"status": "already_published"}  # idempotent redelivery
            if str(mission.status) != "completed":
                return {"status": "not_completed", "mission_status": str(mission.status)}
            if not pub.get("approved"):
                # Distinguish two cases:
                #  - publish_pending still set → an approve/release just enqueued
                #    us, but its DB commit may not be visible yet (the approve
                #    endpoint commits after it enqueues). Retry to let it land.
                #  - no publish_pending → genuinely unapproved; nothing to do.
                if extra.get("publish_pending"):
                    raise RuntimeError("publish approval commit not yet visible; retrying")
                return {"status": "not_approved"}
            server_id = str(pub["connector_server_id"])
            tool_name = str(pub["tool_name"])
            arg_template = pub.get("arguments") or {}
            ctx = {
                "deliverable": _deliverable_text(extra),
                "title": str(mission.title or ""),
                "objective": str(mission.objective or ""),
            }
            import uuid as _uuid

            org_uuid = cast("_uuid.UUID", mission.org_id)

        arguments = _render_publish_args(arg_template, ctx)

        # ── Phase B: build a worker MCP client and dispatch the tool call ────────
        import redis.asyncio as aioredis

        from app.mcp.client import MCPClient
        from app.mcp.connector_wiring import build_connector_secret_store
        from app.mcp.registry import MCPRegistry
        from app.providers.vault import resolve_connector_secret_ref_for_tenant

        # Builtins are registered at module import; re-register defensively so a
        # fresh worker always has the Python handlers (not just Redis records).
        try:
            from app.mcp.servers.registry_wiring import get_builtin_server_configs

            for _bcfg in get_builtin_server_configs():
                if _bcfg.get("handler") is not None:
                    MCPRegistry.register_builtin_handler(_bcfg["server_id"], _bcfg["handler"])
        except Exception as _bh_exc:
            logger.warning("publish_mission.builtin_restore_failed: %s", _bh_exc)

        redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
        try:
            secret_store = build_connector_secret_store(redis_client)

            async def _resolve_secret(ref: str, tctx: Any = None) -> str | None:
                return await resolve_connector_secret_ref_for_tenant(
                    ref, store=secret_store, tenant_ctx=tctx
                )

            from app.mcp.connector_wiring import build_connector_registry

            registry = build_connector_registry(redis_client)
            mcp_client = MCPClient(
                registry,
                secret_resolver=_resolve_secret,
                redis=redis_client,
            )
            call = await mcp_client.call_tool(
                server_id=server_id,
                tool_name=tool_name,
                arguments=arguments if isinstance(arguments, dict) else {},
                tenant_ctx=tenant_ctx,
            )
        finally:
            with contextlib.suppress(Exception):
                await redis_client.aclose()

        receipt = {
            "server_id": server_id,
            "tool_name": tool_name,
            "success": bool(call.success),
            "error": call.error or "",
            "output": call.output,
            "published_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }

        # ── Phase C: record the receipt + emit the org event (fresh RLS session) ─
        async with (
            db_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            svc = OrgService(session, tenant_id)
            mission = await svc.get_mission(mission_id)
            if mission is not None:
                extra = dict(mission.extra_data or {})
                extra["published"] = receipt
                extra.pop("publish_pending", None)
                mission.extra_data = extra
                await session.flush()
                await svc._emit_event(
                    org_uuid,
                    "mission.published",
                    title=(
                        f"Deliverable published via {tool_name}"
                        if call.success
                        else f"Publish failed via {tool_name}"
                    ),
                    entity_type="mission",
                    entity_id=mission_id,
                    severity="info" if call.success else "warning",
                    payload={"mission_id": mission_id, **receipt},
                    source="orchestrator",
                )

        if not call.success:
            # Retry transient connector failures; the receipt above is overwritten
            # on the next attempt. exc carries the connector error for visibility.
            raise RuntimeError(f"publish tool call failed: {call.error}")
        logger.info(
            "publish_mission.published",
            mission_id=mission_id,
            server_id=server_id,
            tool_name=tool_name,
        )
        return {"status": "published", "receipt": receipt}

    try:
        return cast("dict[str, Any]", _run_async(_publish()))
    except Exception as exc:
        logger.warning("publish_mission.retry", mission_id=mission_id, error=str(exc)[:200])
        raise self.retry(exc=exc) from exc


@celery_app.task(name="app.scaling.tasks.run_goal_dlq", bind=True, max_retries=0)
def run_goal_dlq(
    self: Any,
    goal_id: str = "",
    tenant_id: str = "",
    goal_text: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Dead-letter queue handler for goals that exhausted all retries.

    Marks goal as permanently failed. Operators can inspect and manually re-queue.
    """
    if not goal_id or not tenant_id:
        logger.warning(
            "run_goal_dlq invoked without goal payload; skipping. "
            "This usually means a stale beat/RedBeat schedule still points at "
            "the per-goal DLQ handler."
        )
        return {
            "status": "skipped",
            "reason": "missing_dlq_payload",
        }

    logger.error("Goal %s dead-lettered: %s (tenant: %s)", goal_id, reason, tenant_id)
    try:
        _run_async(_update_goal_dlq(goal_id, tenant_id, reason))
    except Exception as exc:
        logger.warning("DLQ DB update failed: %s", exc)
    return {
        "goal_id": goal_id,
        "status": "dead_lettered",
        "reason": reason,
        "tenant_id": tenant_id,
    }


async def _subgoal_context(goal_id: str, tenant_id: str) -> dict[str, Any] | None:
    """Initial graph context carried from goals.execution_context.

    The supervisor sub-goal marker (no re-decomposition) plus the allow-listed
    pre-execution pattern results (API debate consensus, supervisor fallback) —
    the API path forwards the same keys (goal_service.GRAPH_CONTEXT_KEYS).
    """
    import json as _json

    from app.agent.supervisor import SUBGOAL_MARKER
    from app.services.goal_service import graph_context_from_execution_context

    try:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context
        from app.db.session import get_session_factory

        db = get_session_factory()
        async with db() as session, sqlalchemy_rls_context(session, tenant_id):
            raw = (
                await session.execute(
                    text("SELECT execution_context FROM goals WHERE id = :g AND tenant_id = :t"),
                    {"g": goal_id, "t": tenant_id},
                )
            ).scalar()
    except Exception as exc:
        logger.warning("subgoal_context_lookup_failed goal=%s: %s", goal_id, exc)
        return None
    try:
        ctx = raw if isinstance(raw, dict) else _json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        ctx = {}
    if not isinstance(ctx, dict):
        return None
    out = graph_context_from_execution_context(ctx)
    if ctx.get(SUBGOAL_MARKER):
        out[SUBGOAL_MARKER] = ctx[SUBGOAL_MARKER]
    return out or None


async def _goal_execution_context(goal_id: str, tenant_id: str) -> dict[str, Any]:
    """The goal's persisted ``execution_context`` (runtime profile, pattern flags …).

    Raises on a DB error; the caller decides how to degrade (and records it).
    """
    import json as _json

    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context
    from app.db.session import get_session_factory

    db = get_session_factory()
    async with db() as session, sqlalchemy_rls_context(session, tenant_id):
        raw = (
            await session.execute(
                text("SELECT execution_context FROM goals WHERE id = :g AND tenant_id = :t"),
                {"g": goal_id, "t": tenant_id},
            )
        ).scalar()
    try:
        ctx = raw if isinstance(raw, dict) else _json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        ctx = {}
    return ctx if isinstance(ctx, dict) else {}


def _pattern_flags_from_context(ctx: dict[str, Any]) -> dict[str, bool]:
    """The agent's reasoning-pattern flags snapshotted on the goal at submission."""
    from app.services.goal_service import AGENT_PATTERN_FLAG_KEYS

    raw = ctx.get("agent_pattern_flags") if isinstance(ctx, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {k: bool(raw[k]) for k in AGENT_PATTERN_FLAG_KEYS if k in raw}


def _runtime_profile_from_context(
    ctx: dict[str, Any],
) -> tuple[Any | None, Any | None, dict[str, str] | None]:
    """``(profile_that_drives, observed_profile, downgrade)`` from a persisted goal.

    Mirrors GoalService._build_runtime_profile's rollout decision: the profile
    drives execution only on the ``v2`` strategy-runtime path; it is always the
    observed profile (eval scorecards). A snapshot that cannot be rebuilt here is
    an honest downgrade — never a claim that the requested strategy ran.
    """
    snapshot = ctx.get("runtime_profile") if isinstance(ctx, dict) else None
    if not isinstance(snapshot, dict):
        return None, None, None
    drives = str(ctx.get("strategy_runtime_path") or "legacy") == "v2"
    try:
        from app.orchestration.runtime_profile import GoalRuntimeProfile

        profile = GoalRuntimeProfile.from_dict(snapshot)
    except Exception as exc:
        logger.warning("worker_runtime_profile_rehydrate_failed: %s", exc)
        requested = snapshot.get("primary_strategy")
        requested_id = (
            str(requested.get("strategy_id")) if isinstance(requested, dict) else "unknown"
        )
        downgrade = (
            {
                "strategy_id": requested_id,
                "from": "runtime_profile",
                "to": "agent_graph",
                "reason": "runtime_profile_unavailable_on_worker",
            }
            if drives
            else None
        )
        return None, None, downgrade
    return (profile if drives else None), profile, None


def _worker_bulkhead_registry() -> Any:
    """The distributed per-tenant bulkhead the API path gives its graphs (or None)."""
    try:
        import redis.asyncio as _aioredis_bh

        from app.reliability.bulkhead import RedisBulkheadRegistry

        return RedisBulkheadRegistry(
            redis=_aioredis_bh.from_url(REDIS_URL, decode_responses=True),
            default_max_concurrent=20,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("worker_bulkhead_registry_wire_failed: %s", exc)
        return None


async def _goal_model_override(goal_id: str, tenant_id: str) -> str:
    """The goal-level ``model_override`` persisted in goals.execution_context ("" if none).

    The API path applies it (GoalService); worker-run goals used to ignore it.
    Raises when it cannot be read (PROV-20): never run on a different model.
    """
    return await _read_goal_model_override(goal_id, tenant_id)


async def _read_goal_model_override(goal_id: str, tenant_id: str) -> str:
    try:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context
        from app.db.session import get_session_factory

        db = get_session_factory()
        async with db() as session, sqlalchemy_rls_context(session, tenant_id):
            value = (
                await session.execute(
                    text(
                        "SELECT execution_context::jsonb ->> 'model_override' FROM goals "
                        "WHERE id = :g AND tenant_id = :t"
                    ),
                    {"g": goal_id, "t": tenant_id},
                )
            ).scalar()
    except Exception as exc:
        # Fail the goal rather than run it on a model the caller did not ask for.
        logger.error("goal_model_override_lookup_failed goal=%s: %s", goal_id, exc)
        raise RuntimeError(f"goal model_override could not be read: {exc}") from exc
    return str(value or "")


def _wire_worker_model_registry_store() -> None:
    """Wire the shared (Redis) ModelRegistryStore once per worker process.

    Tenant routing policies and configured-model overrides live there; read
    before it is wired, a fresh worker's first goal saw an empty local copy.
    """
    _connect_model_registry_store()


def _connect_model_registry_store() -> None:
    from app.ai_router.registry_store import (
        ModelRegistryStore,
        get_model_registry_store,
        set_model_registry_store,
    )

    if get_model_registry_store() is not None or not REDIS_URL:
        return
    import redis as _sync_redis_mod

    set_model_registry_store(
        ModelRegistryStore(_sync_redis_mod.from_url(REDIS_URL, decode_responses=True))
    )


def _worker_tenant_policy_roles(tenant_id: str, real_provider: Any) -> dict[str, str]:
    """The tenant's routing-policy role pins for a worker goal — fail closed."""
    from app.ai_router.deployment_roles import servable_models as _sm
    from app.ai_router.registry import tenant_policy_role_models

    _wire_worker_model_registry_store()
    try:
        return tenant_policy_role_models(
            tenant_id, servable=_sm(real_provider) if real_provider else None
        )
    except Exception as exc:
        raise RuntimeError(f"tenant routing policies could not be read: {exc}") from exc


_TERMINAL_GOAL_STATUSES = ("complete", "failed", "cancelled")
# Paused for a human: resumable only through resume_goal, never by a redelivery.
_WAITING_HUMAN_STATUS = "waiting_human"


async def _mark_goal_blocked(goal_id: str, tenant_id: str, reason: str) -> bool:
    """Mark a goal an emergency stop prevented from running as cancelled.

    Conditional: a redelivered message for a goal that already finished must
    not rewrite its terminal status. Returns whether the row was changed.
    """
    from sqlalchemy import update

    from app.db.models.goal import Goal
    from app.db.rls import sqlalchemy_rls_context
    from app.db.session import get_session_factory

    db = get_session_factory()
    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        result = await session.execute(
            update(Goal)
            .where(
                Goal.id == goal_id,
                Goal.tenant_id == tenant_id,
                Goal.status.notin_(_TERMINAL_GOAL_STATUSES),
            )
            .values(status="cancelled", error_message=f"Blocked by emergency stop: {reason}")
        )
    rowcount = getattr(result, "rowcount", None)
    return not isinstance(rowcount, int) or rowcount > 0


async def _claim_goal_for_execution(goal_id: str, tenant_id: str) -> str:
    """Atomically claim a goal row for this worker run.

    One conditional ``UPDATE goals SET status='executing' WHERE status NOT IN
    terminal RETURNING id`` under the tenant's RLS context. Returns
    ``"claimed"`` when this run may execute the goal, otherwise the row's
    terminal status (the goal already finished — a redelivered message).
    Raises ``LookupError`` when the row does not exist and propagates DB
    errors: the caller fails closed rather than running an unverified goal.

    A goal still ``executing`` is claimable: concurrent runs are excluded by the
    per-goal execution lock taken before this, so a claim of a running goal only
    succeeds when its previous worker died (crash recovery via acks_late).

    A goal ``waiting_human`` is NOT claimable (WF-18): a redelivered or stale
    message must not un-pause a goal awaiting a human. resume_goal moves the row
    to ``executing`` before it re-enqueues run_goal, so a real relaunch claims.
    """
    from sqlalchemy import select, update

    from app.db.models.goal import Goal
    from app.db.rls import sqlalchemy_rls_context
    from app.db.session import get_session_factory

    db = get_session_factory()
    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        claimed = (
            await session.execute(
                update(Goal)
                .where(
                    Goal.id == goal_id,
                    Goal.tenant_id == tenant_id,
                    Goal.status.notin_((*_TERMINAL_GOAL_STATUSES, _WAITING_HUMAN_STATUS)),
                )
                .values(status="executing")
                .returning(Goal.id)
            )
        ).scalar()
        if claimed is not None:
            return "claimed"
        existing = (
            await session.execute(
                select(Goal.status).where(Goal.id == goal_id, Goal.tenant_id == tenant_id)
            )
        ).scalar()
    if existing is None:
        raise LookupError(f"goal row {goal_id} not found")
    return str(existing)


async def _current_goal_status(goal_id: str, tenant_id: str) -> str | None:
    """The goal row's status under the tenant's RLS scope (None: no row).

    Raises on a DB error — the caller decides what an unreadable status means.
    """
    from sqlalchemy import select

    from app.db.models.goal import Goal
    from app.db.rls import sqlalchemy_rls_context
    from app.db.session import get_session_factory

    db = get_session_factory()
    async with db() as session, sqlalchemy_rls_context(session, tenant_id):
        status = (
            await session.execute(
                select(Goal.status).where(Goal.id == goal_id, Goal.tenant_id == tenant_id)
            )
        ).scalar()
    return None if status is None else str(status)


async def _update_goal_dlq(goal_id: str, tenant_id: str, reason: str) -> None:
    from sqlalchemy import update

    from app.db.models.goal import Goal
    from app.db.rls import sqlalchemy_rls_context
    from app.db.session import get_session_factory as _get_fresh_db

    try:
        # Per-tenant, not system work: the goal and its tenant are known, so this
        # runs on the application role inside that tenant's RLS context. (It used
        # to open system_session — an RLS bypass for a single-tenant write, which
        # under the NOBYPASSRLS role failed outright and left the goal "running".)
        db = _get_fresh_db()
        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            await session.execute(
                update(Goal)
                .where(
                    Goal.id == goal_id,
                    Goal.tenant_id == tenant_id,
                    # NF-10: never rewrite a goal that completed / was cancelled.
                    Goal.status.notin_(("complete", "cancelled")),
                )
                .values(status="failed", error_message=f"Dead lettered: {reason}")
            )
    except Exception as exc:
        logger.warning("DLQ DB update failed: %s", exc)


@celery_app.task(name="app.scaling.tasks.run_goal", bind=True, max_retries=3)  # type: ignore[untyped-decorator]
def run_goal(
    self: Any,
    goal_id: str,
    tenant_id: str,
    goal_text: str = "",
    priority: str = "normal",
    dry_run: bool = False,
    agent_id: str = "",
    connector_ids: list[str] | None = None,
    workflow_mode: str = "single_agent",
    goal_template: str = "",
    plan: str = "free",
    trigger_chain_depth: int = 0,
    source_trigger_id: str = "",
    subgoal: bool = False,
) -> dict[str, Any]:
    """Run a submitted goal in a worker and return its local result.

    ``subgoal`` marks a supervisor's sub-goal (dispatched to goals.subgoals.*):
    it runs under its parent's concurrent-goal slot, so no slot is released.

    Through the goal status bridge (``goal_bridge``) the worker claims the goal
    row, updates its DB status and appends its lifecycle events, so the API
    process sees the worker's progress. A per-goal Redis lock excludes
    concurrent runs of the same goal; every exit path releases it.
    """
    from app.providers.fake import FakeProvider
    from app.reliability.result_processor import ResultProcessor
    from app.tenancy.context import TenantContext

    logger.info("Running goal %s for tenant %s", goal_id, tenant_id)
    # Log which queue tier this task was dispatched to for observability
    _dispatch_queue = goal_queue_for(plan, subgoal=bool(subgoal))
    logger.info(
        "run_goal_queue_selected",
        goal_id=goal_id,
        plan=plan,
        queue=_dispatch_queue,
    )
    started_monotonic = _monotonic()
    effective_goal = goal_text or goal_template
    # Set on EVERY invocation (the worker thread is reused across tasks).
    _SUBGOAL_RUN.set(bool(subgoal))
    _RUN_GOAL_ID.set(goal_id)

    # The tenant's plan is what the API enqueued with the goal. It used to be
    # read from a "plan" field of the LLM-config cache that nothing writes, so
    # every worker-run goal got PROFESSIONAL limits (goal timeout etc.)
    # whatever the tenant's tier. An unknown value falls back to the most
    # restrictive tier, never a paid one.
    from app.tenancy.context import PlanTier

    try:
        plan = PlanTier(plan)
    except ValueError:
        logger.warning("run_goal_unknown_plan goal_id=%s plan=%s", goal_id, plan)
        plan = PlanTier.FREE

    tenant_ctx = TenantContext(
        tenant_id=tenant_id,
        plan=plan,
        api_key_id="celery-worker",
    )

    # Phase 11: Check emergency stop before executing — honours operator kill switch
    # Tenant stops (emergency_stop:{tenant}) AND org stops
    # (emergency_stop:{tenant}:{org}, written by the org endpoint and previously
    # never read) — the goal's org comes from goals.execution_context.org_id.
    _lock_r = _get_sync_redis()
    from app.db.session import get_session_factory as _es_sf
    from app.governance import emergency_stop as _es

    # The goal's org, resolved by the start check when an org of the tenant is
    # stopped; the run's step gate / signal poll get it too (INC-04).
    _goal_org_id: str | None = None

    def _resolve_goal_org() -> str | None:
        nonlocal _goal_org_id
        _goal_org_id = _run_async(_es.goal_org_id(_es_sf(), tenant_id, goal_id))
        return _goal_org_id

    # WF-16: an unreadable stop state is not "not stopped". Retry the task with
    # backoff; after the last retry record the goal as blocked — never run it.
    _es_error: Exception | None = None
    _stop_reason: str | None = None
    try:
        _stop_reason = _es.emergency_stop_reason_sync(
            _lock_r,
            tenant_id,
            resolve_org_id=_resolve_goal_org,
        )
    except Exception as _es_exc:
        logger.error("emergency_stop_check_failed goal_id=%s: %s", goal_id, _es_exc)
        _es_error = _es_exc
    if _es_error is not None:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=_es_error, countdown=2**self.request.retries) from _es_error
        _stop_reason = _es.UNVERIFIABLE_REASON
    if _stop_reason:
        logger.warning(
            "goal_blocked_by_emergency_stop goal_id=%s tenant_id=%s reason=%s",
            goal_id,
            tenant_id,
            _stop_reason,
        )
        # Record it: the goal row used to stay "queued" forever and keep its
        # concurrency slot, so the stop looked like a hang.
        _blocked: Any = None
        with contextlib.suppress(Exception):
            _blocked = _run_async(_mark_goal_blocked(goal_id, tenant_id, _stop_reason))
        # A redelivered message for an already-finished goal changes nothing
        # and must not free a slot a running goal holds.
        if _blocked is not False:
            with contextlib.suppress(Exception):
                _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
        return {"status": "blocked", "reason": _stop_reason}

    db_factory: Any = None
    goal_bridge: Any = None
    event_store: Any = None
    try:
        from app.db.session import get_session_factory
        from app.services.event_store import EventStore
        from app.services.goal_service import GoalService

        def _make_worker_goal_bridge() -> tuple[Any, Any, Any]:
            fresh_db = get_session_factory()
            fresh_event_store = EventStore(fresh_db)
            fresh_goal_bridge = GoalService(
                db_session_factory=fresh_db, event_store=fresh_event_store
            )
            return fresh_db, fresh_event_store, fresh_goal_bridge

        db_factory, event_store, goal_bridge = _make_worker_goal_bridge()
    except Exception as exc:
        logger.warning("Goal %s status bridge unavailable: %s", goal_id, exc)

    # P8-1: screen this goal against the tenant's PERSISTED guardrail rules, as
    # the API does. The lifespan that binds the rule repository never runs in a
    # worker, so a tenant's own rules (e.g. a PII redact rule) never applied
    # here — only the in-memory baseline did. Raises rather than run unscreened.
    from app.guardrails_v2.worker_binding import bind_worker_guardrail_rules

    bind_worker_guardrail_rules()

    async def update_submitted_goal_status(
        status: str,
        *,
        error_message: str = "",
        iterations: int = 0,
        only_if_active: bool = True,
    ) -> None:
        # Conditional by default: a worker never rewrites a goal that already
        # reached a terminal status (an operator cancel landing mid-run, or a
        # redelivered message for a finished goal).
        if goal_bridge is None:
            return
        try:
            _, _, bridge = _make_worker_goal_bridge()
            await bridge._db_update_goal_status(
                goal_id,
                tenant_id,
                status,
                error_message=error_message,
                iterations=iterations,
                **({"only_if_active": True} if only_if_active else {}),
            )
        except Exception as db_exc:
            logger.warning("DB status update failed (non-fatal): %s", db_exc)

    from app.triggers.bus import publish_trigger_event_sync
    from app.triggers.consumers.chain import CHAIN_CHANNEL_FOR_EVENT, build_chain_event

    _chain_published: set[str] = set()

    async def append_submitted_goal_event(event: dict[str, Any]) -> None:
        # CORE-34: sanitized (credential redaction, size caps) before it reaches
        # Redis (SSE) or the event store, like every AgentGraph event.
        from app.agent.sanitization import sanitize_event
        from app.guardrails_v2.output_screening import screen_goal_event

        event = sanitize_event(event)
        # P8b-1: the same output screening as the API (PII / secrets + the
        # tenant's output rules) before the event is stored or published.
        _screened = await screen_goal_event(event, tenant_id)
        if _screened is None:
            return  # a live token chunk this tenant's output rules do not allow
        event = _screened
        # ── Persist first, so the live event carries its durable sequence ─────
        # SVC-05: that sequence is the SSE id / Last-Event-ID resume cursor; an
        # event published before it was stored had none, so the id fell back to
        # a per-connection counter. SVC-08: a failed append is buffered in the
        # Redis outbox (replayed by the drain-goal-event-outbox beat task), never
        # silently dropped, and the event is still published (without a sequence).
        _seq: int | None = None
        if event_store is not None:
            try:
                _, fresh_event_store, _ = _make_worker_goal_bridge()
                _appended = await fresh_event_store.append_event(
                    goal_id, event, tenant_ctx=tenant_ctx
                )
                if isinstance(_appended, int) and not isinstance(_appended, bool):
                    _seq = _appended
            except Exception as db_exc:
                logger.warning("DB event append failed, buffering: %s", db_exc)
                from app.services.event_store import buffer_failed_event_via_settings

                await buffer_failed_event_via_settings(
                    tenant_id=tenant_id, goal_id=goal_id, event=event
                )

        # ── ALWAYS publish to Redis pub/sub (SSE real-time feed) ──────────────
        # This must happen regardless of DB availability. Previously the function
        # returned early when event_store was None, silently dropping all events
        # from the SSE stream. Now Redis publish runs unconditionally.
        try:
            import json as _json

            _r = _get_sync_redis()
            if _r is not None:
                _envelope: dict[str, Any] = {
                    "goal_id": goal_id,
                    "tenant_id": tenant_id,
                    "type": event.get("type", ""),
                    "payload": event,
                }
                if _seq is not None:
                    _envelope["_seq"] = _seq
                _r.publish(f"goal_events:{tenant_id}:{goal_id}", _json.dumps(_envelope))
        except Exception as _pub_exc:
            logger.debug("redis_event_publish_failed (non-fatal): %s", _pub_exc)

        # ── Goal-chain lifecycle channel (goal.completed / goal.failed) ───────
        # ChainTriggerConsumer listens on these; nothing published them, so goal-
        # chain triggers never fired for worker-run goals.
        _chain_channel = CHAIN_CHANNEL_FOR_EVENT.get(str(event.get("type", "")))
        if _chain_channel and not dry_run and _chain_channel not in _chain_published:
            try:
                _rc = _get_sync_redis()
                if _rc is not None:
                    # Stream XADD (+ legacy pub/sub while dual publish is on), TRG-18.
                    publish_trigger_event_sync(
                        _rc,
                        _chain_channel,
                        build_chain_event(
                            channel=_chain_channel,
                            tenant_id=tenant_id,
                            goal_id=goal_id,
                            agent_id=agent_id or "",
                            status="complete" if _chain_channel == "goal.completed" else "failed",
                            tenant_plan=getattr(plan, "value", str(plan)),
                            trigger_chain_depth=trigger_chain_depth,
                            source_trigger_id=source_trigger_id,
                        ),
                    )
                    _chain_published.add(_chain_channel)
            except Exception as _chain_exc:
                logger.warning("goal_chain_event_publish_failed: %s", _chain_exc)
            if _chain_channel == "goal.completed":
                # A12: the tenant's agent_generated Sources index the answer now.
                from app.db.session import get_session_factory as _agen_factory
                from app.ingestion.agent_generated_events import notify_agent_generated

                await notify_agent_generated(
                    tenant_id, "goal_output", goal_id, db_factory=_agen_factory()
                )

        # ── Usage metering: one tool_calls record per completed tool call ─────
        if not dry_run and event.get("type") == "tool_call_complete":
            from app.services.usage_metering import meter_tool_call

            await meter_tool_call(
                _worker_usage_service(), tenant_id=tenant_id, goal_id=goal_id, event=event
            )

    async def ensure_submitted_goal_row() -> None:
        if goal_bridge is None:
            return
        try:
            _, _, bridge = _make_worker_goal_bridge()
            await bridge._db_ensure_goal_row(
                goal_id=goal_id,
                tenant_id=tenant_id,
                goal_text=effective_goal,
                status="planning",
                priority=priority,
                dry_run=dry_run,
                agent_id=agent_id or None,
                workflow_mode=workflow_mode,
                # The submitted context is not delivered to the worker; say so
                # instead of writing a blank {} that reads like "no context".
                execution_context={
                    "execution_context_source": "worker_recreated_row",
                    "execution_context_note": (
                        "goal row was missing when the worker started; the submitted "
                        "execution context was not available"
                    ),
                },
            )
        except Exception as db_exc:
            logger.warning("DB ensure goal row failed (non-fatal): %s", db_exc)

    async def mark_worker_started() -> None:
        await update_submitted_goal_status("executing")
        await append_submitted_goal_event(
            {"type": "worker_started", "goal": effective_goal, "worker": "celery"}
        )

    async def meter_worker_goal(status: str) -> None:
        # Usage metering for the goal itself (once per goal: deterministic ids).
        if dry_run or status not in {"complete", "failed", "cancelled"}:
            return
        from app.services.usage_metering import meter_goal_completion

        await meter_goal_completion(
            _worker_usage_service(), tenant_id=tenant_id, goal_id=goal_id, status=status
        )

    async def mark_worker_complete(status: str, iterations: int) -> None:
        await update_submitted_goal_status(status, iterations=iterations)
        await append_submitted_goal_event(
            {
                "type": "worker_complete",
                "status": status,
                "iterations": iterations,
            }
        )
        await meter_worker_goal(status)
        # Close the org loop: if a mission dispatched this goal, reconcile it now
        # (mark subtasks done, aggregate the deliverable, complete the mission).
        # Skipped for dry runs — they must not finalize a real mission.
        if not dry_run:
            await _finalize_owning_mission(goal_id, tenant_id)

    async def mark_worker_failed(exc: Exception) -> None:
        # Exception class + redacted message (CORE-34), never the raw text.
        _reason = _redacted_error(exc)
        await update_submitted_goal_status("failed", error_message=_reason)
        await append_submitted_goal_event({"type": "worker_failed", "reason": _reason})
        await meter_worker_goal("failed")
        if not dry_run:
            await _finalize_owning_mission(goal_id, tenant_id)

    # ── Distributed lock: at-most-once execution per goal ─────────────────────
    # Use a synchronous lock (_SyncGoalLock) to avoid event-loop-mismatch bugs:
    # each _run_async() call creates a fresh event loop, so an async Redis client
    # created during acquire() would be bound to a different loop from the one
    # used during release(), silently breaking the release.
    _lock: _SyncGoalLock | None = None
    try:
        _broker_url = str(celery_app.conf.broker_url or "")
        # The lock lives in Redis: the broker when it is Redis, else REDIS_URL.
        # Neither configured (eager/test mode) is the only lock-less path.
        _redis_url = (
            _broker_url
            if _broker_url.startswith(("redis://", "rediss://", "unix://"))
            else (os.getenv("REDIS_URL", "") if _broker_url else "")
        )
        if _redis_url:
            import uuid as _uuid

            _lock = _SyncGoalLock(_goal_lock_client(_redis_url), _uuid.uuid4().hex)
            # The lock TTL must cover the goal's *entire* allowed execution
            # window (per-plan goal_timeout_seconds below, in the isolation
            # check further down — free=1h, starter=2h, professional=8h,
            # enterprise=24h), plus headroom for setup/teardown. A fixed
            # 30-minute TTL used to be shorter than every plan's timeout, so
            # Redis would silently expire and delete the lock key while the
            # goal was still genuinely executing — a second worker (e.g. after
            # a duplicate submission or broker redelivery) could then acquire
            # the now-free lock and start executing the SAME goal
            # concurrently with the still-running original (split-brain).
            from app.tenancy.context import PLAN_LIMITS as _LOCK_PLAN_LIMITS

            _lock_plan_timeout_s = getattr(
                _LOCK_PLAN_LIMITS.get(plan), "goal_timeout_seconds", 1_800
            )
            _lock_ttl_ms = (_lock_plan_timeout_s + 300) * 1_000  # +5 min headroom
            _acquired = _lock.acquire(goal_id, ttl_ms=_lock_ttl_ms)
            if not _acquired:
                logger.warning("Goal %s already executing in another worker — skipping", goal_id)
                return {
                    "status": "skipped",
                    "goal_id": goal_id,
                    "reason": "already_executing",
                }
            _run_async(_renew_slot_lease(tenant_id, goal_id, plan, REDIS_URL))
    except Exception as _lock_exc:
        # Fail closed: running without the lock let a redelivered/duplicated
        # task execute the same goal concurrently. Retry; after the last retry
        # record the goal as failed (and release its slot) instead.
        logger.error("goal_execution_lock_unavailable goal_id=%s: %s", goal_id, _lock_exc)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=_lock_exc, countdown=2**self.request.retries) from _lock_exc
        _lock_reason = "Goal execution lock unavailable (Redis); not run to avoid duplicates"
        with contextlib.suppress(Exception):
            _run_async(update_submitted_goal_status("failed", error_message=_lock_reason))
        with contextlib.suppress(Exception):
            _run_async(append_submitted_goal_event({"type": "worker_failed",
                                                    "reason": _lock_reason}))
        if not dry_run:
            with contextlib.suppress(Exception):
                _run_async(_finalize_owning_mission(goal_id, tenant_id))
        _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
        return {
            "status": "failed",
            "goal_id": goal_id,
            "reason": "execution_lock_unavailable",
        }

    try:
        _run_async(ensure_submitted_goal_row())
    except Exception as db_exc:
        logger.warning("DB operation failed (non-fatal): %s", db_exc)

    # ── Terminal-state check + atomic claim ───────────────────────────────────
    # With acks_late, Celery redelivers a message whose run outlived the broker
    # visibility timeout (or whose worker died). The lock above excludes a
    # concurrent run; this conditional UPDATE excludes RE-running a goal that
    # already reached a terminal status. Fail closed when it cannot be verified.
    if goal_bridge is not None:
        try:
            _claim = _run_async(_claim_goal_for_execution(goal_id, tenant_id))
        except Exception as _claim_exc:
            logger.error("goal_claim_unavailable goal_id=%s: %s", goal_id, _claim_exc)
            if _lock:
                with contextlib.suppress(Exception):
                    _lock.release(goal_id)
            if self.request.retries < self.max_retries:
                raise self.retry(
                    exc=_claim_exc, countdown=2**self.request.retries
                ) from _claim_exc
            _claim_reason = "Goal could not be claimed for execution; not run to avoid duplicates"
            with contextlib.suppress(Exception):
                _run_async(update_submitted_goal_status("failed", error_message=_claim_reason))
            with contextlib.suppress(Exception):
                _run_async(
                    append_submitted_goal_event({"type": "worker_failed", "reason": _claim_reason})
                )
            _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
            return {"status": "failed", "goal_id": goal_id, "reason": "goal_claim_unavailable"}
        if _claim != "claimed":
            _waiting = _claim == _WAITING_HUMAN_STATUS
            logger.warning(
                "goal_not_claimable_skipping goal_id=%s status=%s", goal_id, _claim
            )
            if _lock:
                with contextlib.suppress(Exception):
                    _lock.release(goal_id)
            # No counter decrement: the run that finished it released the slot,
            # and a goal waiting for a human released it when it was suspended.
            return {
                "status": "skipped",
                "goal_id": goal_id,
                "reason": "waiting_for_human" if _waiting else "already_terminal",
                "goal_status": _claim,
            }

    try:
        _run_async(mark_worker_started())
    except Exception as db_exc:
        logger.warning("DB operation failed (non-fatal): %s", db_exc)


    if dry_run:
        _run_async(mark_worker_complete("complete", 0))
        _record_goal_duration_metric(
            "complete", started_monotonic=started_monotonic, priority=priority
        )
        # Release distributed lock before early return
        if _lock:
            with contextlib.suppress(Exception):
                _lock.release(goal_id)
        # Decrement concurrent-goal counter — dry-run goals still terminate
        _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
        return {
            "status": "complete",
            "goal_id": goal_id,
            "agent_id": agent_id,
            "workflow_mode": workflow_mode,
            "priority": priority,
            "dry_run": True,
            "iterations": 0,
            "result_scope": "submitted_goal" if goal_bridge is not None else "worker_only",
            "submitted_goal_status": "complete" if goal_bridge is not None else "not_updated",
            "status_bridge": "updated" if goal_bridge is not None else "unavailable",
        }

    # Try tenant-specific provider from Redis first, then fall back to
    # process-wide env-var providers, then the non-durable FakeProvider.
    _bind_worker_cost_breakdown_db()
    from app.providers.tenant_provider import TenantProviderError

    try:
        real_provider = _get_llm_provider(tenant_id)
    except TenantProviderError as _byok_exc:
        # The tenant configured BYOK but it is unusable: fail the goal rather
        # than run it on the deployment/platform provider below.
        _run_async(mark_worker_failed(_byok_exc))
        _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
        # WF-19: this early return is outside the try/finally that releases
        # the goal lock — without this a retry was refused for the plan's
        # goal timeout + 5 min.
        if _lock:
            _lock.release(goal_id)
        return {
            "status": "failed",
            "goal_id": goal_id,
            "reason": "tenant_llm_provider_unavailable",
            "message": str(_byok_exc),
        }

    if real_provider is None:
        # Same precedence as the API (_app_provider = onprem cluster or registry):
        # the deployment's on-prem/NVIDIA/hybrid cluster before the env registry.
        real_provider = _worker_deployment_provider()

    if real_provider is None:
        # Reuse the process-wide registry resolver so the worker honours the SAME
        # env config as the API (OPENAI_BASE_URL + OPENAI_MODEL/DEFAULT_MODEL for a
        # self-hosted endpoint). The previous ad-hoc `OpenAICompatibleProvider(
        # api_key=...)` ignored base_url and model, so a self-hosted deployment hit
        # the official OpenAI API with a bogus "gpt-5.2" default and 404'd.
        from app.providers.fake import FakeProvider as _RegFake
        from app.providers.registry import resolve_provider as _resolve_provider

        _resolved = _resolve_provider()
        real_provider = None if isinstance(_resolved, _RegFake) else _resolved

    used_fake_provider = real_provider is None
    provider = real_provider or FakeProvider(
        responses=[
            '{"steps": ["Execute the goal autonomously"]}',
            "Goal executed via Celery worker",
            '{"success": true, "reason": "Completed by worker"}',
        ]
    )

    # The worker has one execution kernel. Assembly failures fail explicitly.
    _loop_is_patched = False
    # The goal's AuditLog: flushed before the run loop closes (AUDIT-05).
    _worker_audit: Any = None

    # Resolve the agent's autonomy_mode from the DB so that fully-autonomous
    # agents bypass the HITL gate on write_high tool calls.
    _agent_autonomy_mode = "bounded-autonomous"
    _agent_max_iterations: int | None = None  # None = use graph default (100)
    _agent_system_prompt: str = ""
    _agent_collection_ids: list[str] = []
    # The agent's pinned model. Only the API path applied it (GoalService ->
    # ModelRouter.with_override); worker-run goals silently ignored it.
    _agent_model_override: str = ""
    if agent_id and db_factory is not None:
        try:
            from sqlalchemy import text as _sa_text

            from app.db.rls import sqlalchemy_rls_context as _rls

            async def _lookup_agent_config() -> tuple[str, int | None, str, list[str], str]:
                async with db_factory() as _sess, _rls(_sess, tenant_id):
                    row = (
                        await _sess.execute(
                            _sa_text(
                                "SELECT autonomy_mode, max_iterations, system_prompt, "
                                "allowed_collection_ids, model_override FROM agents "
                                "WHERE id = :aid AND tenant_id = :tid LIMIT 1"
                            ),
                            {"aid": agent_id, "tid": tenant_id},
                        )
                    ).fetchone()
                    if row:
                        mode = str(row[0]) if row[0] else "bounded-autonomous"
                        iters = int(row[1]) if row[1] else None
                        sys_prompt = str(row[2]) if row[2] else ""
                        collection_ids = list(row[3] or []) if len(row) > 3 else []
                        override = str(row[4] or "") if len(row) > 4 else ""
                        return mode, iters, sys_prompt, collection_ids, override
                    return "bounded-autonomous", None, "", [], ""

            (
                _agent_autonomy_mode,
                _agent_max_iterations,
                _agent_system_prompt,
                _agent_collection_ids,
                _agent_model_override,
            ) = _run_async(_lookup_agent_config())
            logger.info(
                "worker_agent_config goal=%s agent=%s mode=%s max_iter=%s",
                goal_id,
                agent_id,
                _agent_autonomy_mode,
                _agent_max_iterations,
            )
        except Exception as _ae:
            logger.debug("worker_agent_config_lookup_failed: %s", _ae)

    # ── Runtime profile + pattern flags persisted on the goal ────────────────
    # The worker used to build a plain loop whatever the goal's persisted runtime
    # profile / agent pattern flags said (while the goal was recorded as running
    # the profile's strategy). Read them back and build the graph like GoalService.
    _worker_exec_ctx: dict[str, Any] = {}
    _worker_ctx_unreadable = False
    if goal_bridge is not None:
        try:
            _worker_exec_ctx = _run_async(_goal_execution_context(goal_id, tenant_id)) or {}
        except Exception as _ctx_exc:
            _worker_ctx_unreadable = True
            logger.warning("worker_execution_context_lookup_failed goal=%s: %s", goal_id, _ctx_exc)
    # An agent-scoped API key's tool restriction (AGKEY-01) travels on the goal;
    # an unreadable context denies every tool rather than run unrestricted.
    from app.auth.agent_credentials import apply_goal_agent_key

    tenant_ctx = apply_goal_agent_key(
        tenant_ctx, _worker_exec_ctx, unreadable=_worker_ctx_unreadable
    )
    _worker_pattern_flags = _pattern_flags_from_context(_worker_exec_ctx)
    (
        _worker_profile,
        _worker_observed_profile,
        _worker_profile_downgrade,
    ) = _runtime_profile_from_context(_worker_exec_ctx)
    if _worker_ctx_unreadable and not _worker_profile_downgrade:
        # A plain graph runs because the goal's profile / pattern flags could not
        # be read: record why instead of skipping the v2 profile silently (CORE-20).
        _worker_profile_downgrade = {
            "strategy_id": "unknown",
            "from": "runtime_profile",
            "to": "agent_graph",
            "reason": "profile_unreadable",
        }

    # What the worker's graph actually runs, for certification evidence at the end.
    _worker_strategy_run: dict[str, Any] = {}

    async def _record_worker_strategy_execution(execution: dict[str, Any]) -> None:
        """Persist which strategy the worker actually runs (goals.execution_context)."""
        _worker_strategy_run["execution"] = execution
        if goal_bridge is None:
            return
        try:
            _, _, _bridge = _make_worker_goal_bridge()
            await _bridge._db_merge_context_key(
                goal_id, tenant_id, "strategy_execution", execution
            )
        except Exception as _se_exc:
            logger.warning("worker_strategy_execution_persist_failed: %s", _se_exc)

    _agent_runner: Any = None
    _use_agent_graph = False
    # Canonical Reflexion memory (recall in the planner, learning after the goal).
    _reflexion_service: Any = None

    async def _build_worker_mcp_context() -> tuple[Any, Any, Any]:
        import redis.asyncio as aioredis

        from app.mcp.client import MCPClient
        from app.mcp.connector_wiring import build_connector_secret_store
        from app.mcp.registry import MCPRegistry
        from app.providers.vault import resolve_connector_secret_ref_for_tenant

        # Ensure all builtin tool handlers are registered in this worker process.
        # The web process registers them at startup; the Celery worker process
        # starts fresh and needs to re-register so that `cfg.builtin_handler`
        # is not None when `discover_tools` checks it.
        try:
            from app.mcp.servers.registry_wiring import get_builtin_server_configs

            for _bcfg in get_builtin_server_configs():
                MCPRegistry.register_builtin_handler(_bcfg["server_id"], _bcfg["handler"])
        except Exception as _bh_exc:
            logger.warning("worker_builtin_handler_restore_failed: %s", _bh_exc)

        redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
        secret_store = build_connector_secret_store(redis_client)

        async def _resolve_secret(ref: str, tenant_ctx: Any = None) -> str | None:
            return await resolve_connector_secret_ref_for_tenant(
                ref, store=secret_store, tenant_ctx=tenant_ctx
            )

        from app.mcp.connector_wiring import build_connector_registry

        registry = build_connector_registry(redis_client)
        # Wire the LLM provider so SelfHealingToolCaller can fix argument errors
        # real_provider is captured from the outer run_goal() scope via closure
        mcp_client = MCPClient(
            registry,
            secret_resolver=_resolve_secret,
            redis=redis_client,
            llm_provider=real_provider,  # type: ignore[name-defined]
        )
        # OAuth connectors: the worker never ran the OAuth flow, so without a
        # manager reading the durable oauth_tokens store it sent no Bearer token.
        try:
            from app.mcp.oauth import build_worker_oauth_manager

            mcp_client._oauth_manager = build_worker_oauth_manager(
                db_factory, redis=redis_client
            )
        except Exception as _oauth_exc:
            logger.warning("worker_oauth_manager_wire_failed: %s", _oauth_exc)
        # One builder with the in-process path (TOOLCTX-01..07): no connector ids
        # (goal without an agent) = the tenant's connectors, bounded; an
        # unreachable connector is recorded instead of failing the goal; RPA
        # tools only where Playwright runs; the same tiered ToolSelector.
        from app.agent.tool_context_builder import (
            build_goal_tool_context,
            build_worker_tool_selector,
        )

        worker_connector_ids = [str(item) for item in (connector_ids or [])]
        tool_context = await build_goal_tool_context(
            registry=registry,
            mcp_client=mcp_client,
            tenant_ctx=tenant_ctx,
            connector_ids=worker_connector_ids or None,
            goal=effective_goal,
            tool_selector=build_worker_tool_selector(),
        )
        return redis_client, mcp_client, tool_context

    if not _loop_is_patched:
        # Production path: Try AgentGraph first (full capabilities)
        try:
            from app.governance.audit import AuditLog
            from app.governance.cost import CostController, RedisCostController
            from app.governance.hitl import HITLGateway
            from app.intelligence.eval_runner import EvalRunner
            from app.intelligence.guardrails import GuardrailChecker
            from app.memory.execution import ExecutionMemory
            from app.reliability.dedup import DeduplicationCache
            from app.reliability.rollback import RollbackEngine

            _audit = AuditLog(db_session_factory=db_factory)
            _worker_audit = _audit
            # Durable + cross-process: gates raised here are persisted (so the
            # API can find and resolve them) and the waiter also listens on the
            # Redis BLPOP result key the API publishes to. A bare HITLGateway()
            # kept the gate in worker memory, where no approval could reach it.
            _hitl = HITLGateway(db_session_factory=db_factory)
            _hitl._redis = _worker_async_redis()
            _cost = CostController()
            _policy = _run_async(_load_worker_policy_engine(db_factory, tenant_id))
            _ltm = _worker_long_term_memory()
            _eval = EvalRunner()
            _exec_mem = ExecutionMemory()

            # Wire Redis into CostController for distributed rate-limiting
            import os as _os_cw

            _redis_url_cw = _os_cw.getenv("REDIS_URL", "")
            if _redis_url_cw:
                try:
                    import redis.asyncio as _aioredis_cw

                    _cost = RedisCostController(
                        redis=_aioredis_cw.from_url(
                            _redis_url_cw,
                            decode_responses=True,
                        )
                    )
                except Exception:
                    pass
            # Workers enforce the tenant's configured budget_configs row too.
            if db_factory is not None:
                _cost.set_budget_db(db_factory)
            # Decision calls outside the goal scope (e.g. post-run eval scoring)
            # charge the worker's per-task cost services installed once at
            # worker start (app.scaling.worker_cost) — never a per-run global.

            # Build a model router matched to the provider type so the graph
            # uses the correct model names (e.g. gpt-4-turbo not claude-opus-4-8).
            _model_router = None
            try:
                from app.agent.model_router import ModelRouter

                _provider_name = getattr(real_provider, "_provider_name", None)
                if _provider_name is None:
                    # Detect provider type from class name
                    _cls = type(real_provider).__name__
                    if "Anthropic" in _cls:
                        _provider_name = "anthropic"
                    elif "OpenAI" in _cls or "Compatible" in _cls:
                        _provider_name = "openai"
                    elif "Gemini" in _cls:
                        _provider_name = "gemini"
                # The multi-endpoint cluster provider steers the router to its
                # matching profile (onprem / nvidia / hybrid), like the API.
                if _provider_name is None:
                    _ptype = getattr(real_provider, "_agentverse_provider_type", None)
                    _provider_name = _ptype if isinstance(_ptype, str) and _ptype else None
                if _provider_name:
                    _model_router = ModelRouter(provider_name=_provider_name)
            except Exception:
                pass

            # Per-goal role map, restricted to what THIS goal's provider serves
            # (empty for a tenant-configured single provider) — the same map
            # GoalService.set_role_map applies on the API path.
            _worker_roles = _worker_role_map(real_provider) if real_provider else {}
            # Tenant routing policies (PUT /models/routing-policies) — same as the
            # API path; they were stored in one API process and never applied.
            # PROV-20: store wired first; an unreadable store fails the goal
            # instead of running it while ignoring the tenant's policy.
            _worker_roles = {
                **_worker_roles,
                **_worker_tenant_policy_roles(tenant_id, real_provider),
            }
            if _worker_roles:
                try:
                    if _model_router is None:
                        from app.agent.model_router import ModelRouter

                        _model_router = ModelRouter(provider_name="onprem")
                    _model_router.set_role_map(_worker_roles)
                except Exception as _rm_exc:
                    logger.warning("worker_model_role_map_apply_failed: %s", _rm_exc)
            # A goal-level model_override (POST /goals body) wins over the agent's.
            _goal_level_override = _run_async(_goal_model_override(goal_id, tenant_id))
            _effective_override = _goal_level_override or _agent_model_override
            if _effective_override:
                try:
                    if _model_router is None:
                        from app.agent.model_router import ModelRouter

                        _model_router = ModelRouter()
                    _model_router = _model_router.with_override(_effective_override)
                    if hasattr(_model_router, "set_plan_tier"):  # PROV-18: plan caps the pin
                        _model_router.set_plan_tier(tenant_ctx.plan)
                except Exception as _mo_exc:
                    if _goal_level_override:
                        # Explicitly requested: never silently run on another model.
                        raise
                    logger.warning("worker_model_override_apply_failed: %s", _mo_exc)

            # Build LLM response cache and semantic cache for the worker.
            # Use the Celery broker Redis URL as fallback for REDIS_URL so
            # the LLM cache uses Redis (durable, shared across processes)
            # instead of in-process memory (which persists stale responses
            # across multiple goal runs in the same worker process and causes
            # the executor/verifier to return stale cached responses).
            _llm_response_cache = None
            _semantic_cache_worker = None
            _redis_for_worker = None
            try:
                from app.rag.llm_response_cache import LLMResponseCache

                _redis_url_worker = os.getenv("REDIS_URL", "") or celery_app.conf.broker_url or ""
                if _redis_url_worker:
                    import redis.asyncio as _aioredis_worker

                    _redis_for_worker = _aioredis_worker.from_url(
                        _redis_url_worker, decode_responses=False
                    )
                _llm_response_cache = LLMResponseCache(redis=_redis_for_worker)
            except Exception:
                pass

            try:
                from app.rag.semantic_cache import SemanticCache

                _semantic_cache_worker = SemanticCache(redis=_redis_for_worker)
            except Exception:
                pass

            # Build separate verifier for cross-model verification (reduces self-confirmation bias)
            _verifier_for_graph = provider  # default: same as executor
            # With a role map the verifier MUST stay on the cluster provider: a
            # cross-model verifier (e.g. Anthropic) cannot serve the mapped
            # verification model id. The API path also verifies on `provider`.
            if not _worker_roles:
                try:
                    from app.main import _build_verifier_provider as _bvp

                    _vp = _bvp()
                    if _vp is not None:
                        _verifier_for_graph = _vp
                        logger.info("Goal %s: cross-model verifier active", goal_id)
                except Exception as _vp_exc:
                    logger.warning("verifier_provider_build_failed: %s", _vp_exc)

            # Phase 3 services — grounding, consensus, synthesis, calibration
            _phase3_grounding = None
            _phase3_synthesizer = None
            _phase3_calibration = None
            _phase3_consensus = None
            try:
                from app.agent.grounding import GroundingChecker

                _phase3_grounding = GroundingChecker()
            except Exception as _p3g_exc:
                logger.debug("phase3_grounding_unavailable: %s", _p3g_exc)
            try:
                from app.agent.synthesis import AnswerSynthesizer

                _phase3_synthesizer = AnswerSynthesizer(llm_provider=provider)
            except Exception as _p3s_exc:
                logger.debug("phase3_synthesizer_unavailable: %s", _p3s_exc)
            try:
                from app.intelligence.verifier_calibration import _default_calibration_store

                _phase3_calibration = _default_calibration_store
            except Exception as _p3c_exc:
                logger.debug("phase3_calibration_unavailable: %s", _p3c_exc)
            try:
                from app.agent.consensus import ConsensusVerifier

                _phase3_consensus = ConsensusVerifier(primary_verifier=provider)
            except Exception as _p3cv_exc:
                logger.debug("phase3_consensus_unavailable: %s", _p3cv_exc)

            # Build embedder for pgvector LTM recall + RAG: the SAME shared
            # selection the API uses (embedder_factory), so goal-time query
            # vectors live in the same space as the documents they search. This
            # used to re-implement the priority (Voyage before the dedicated
            # endpoint, no NVIDIA/on-prem, no local model) and diverge from it.
            _embedder_for_graph = None
            try:
                from app.providers.embedder_factory import (
                    build_query_embedder as _build_query_embedder,
                )

                _embedder_for_graph = _build_query_embedder()
            except Exception as _emb_exc:
                logger.error("worker_embedder_build_failed: %s", _emb_exc)

            # Wire KnowledgeStore so RAG context is retrieved before planning.
            # Without this, the rag_retrieval node is a no-op in the worker.
            _knowledge_store_worker = None
            try:
                from app.rag.store import KnowledgeStore as _KnowledgeStore  # correct path

                if db_factory is not None:
                    _knowledge_store_worker = _KnowledgeStore(db_session_factory=db_factory)
            except Exception as _ks_exc:
                logger.debug("knowledge_store_worker_unavailable: %s", _ks_exc)

            from app.core.config import get_settings
            from app.rag.agentic.patterns.web_augmented import (
                build_safe_web_search_capability,
                parse_allowed_domains,
            )
            from app.rag.gateway import (
                KnowledgeStoreCollectionAuthorizer,
                ResolvedLLM,
                RetrievalDependencies,
                SQLCollectionAuthorizer,
                core_strategy_capabilities,
            )

            async def _resolve_worker_retrieval_llm(
                tenant_context: TenantContext,
                strategy: Any,
            ) -> ResolvedLLM | None:
                del strategy
                if tenant_context.tenant_id != tenant_id:
                    return None
                provider_default = getattr(provider, "_default_model", "")
                model = provider_default.strip() if isinstance(provider_default, str) else ""
                if not model and isinstance(provider, FakeProvider):
                    model = "fake-provider"
                if not model:
                    return None
                return ResolvedLLM(
                    provider=provider,
                    model=model,
                    provider_type=str(getattr(provider, "_agentverse_provider_type", "")),
                )

            collection_authorizer = (
                SQLCollectionAuthorizer()
                if db_factory is not None
                else KnowledgeStoreCollectionAuthorizer(_knowledge_store_worker)
                if _knowledge_store_worker is not None
                else SQLCollectionAuthorizer()
            )
            worker_settings = get_settings()
            from app.rag.catalogue import RAGAdapterConfiguration

            worker_rag_adapter_configuration = RAGAdapterConfiguration(
                colbert_checkpoint=worker_settings.colbert_checkpoint
            )
            worker_web_search = build_safe_web_search_capability(
                searxng_url=worker_settings.searxng_url,
                policy_services=(_policy, _cost, _hitl),
                allowed_domains=parse_allowed_domains(worker_settings.web_search_allowed_domains),
            )
            _retrieval_gateway_worker = _build_worker_retrieval_gateway(
                RetrievalDependencies(
                    session_factory=db_factory,
                    embedder=_embedder_for_graph,
                    llm_resolver=_resolve_worker_retrieval_llm,
                    graph_capability=_build_worker_graph_capability(db_factory),
                    search_capability=worker_web_search,
                    policy_services=(_policy, _cost, _hitl),
                    cost_controller=_cost,
                    collection_authorizer=collection_authorizer,
                    strategy_capabilities=core_strategy_capabilities(
                        worker_rag_adapter_configuration
                    ),
                    # Same RAFT wiring as the API: deployed fine-tuned models are
                    # served here too (it was absent, so RAFT was never ready).
                    raft_service=_build_worker_raft_service(worker_settings, db_factory),
                    colbert_checkpoint=worker_settings.colbert_checkpoint,
                )
            )

            # Adaptive strategy: wire the capability tracker so the engine LEARNS
            # each model's real capabilities from observation (best-effort; a Redis
            # error just falls back to the static seed). Strategy C (fast-verifier
            # routing) is auto-derived from the role models' latency tiers — no
            # flag or env var required.
            _capability_tracker = None
            try:
                import redis.asyncio as _aioredis_ct

                from app.agent.strategy_adaptivity import RedisCapabilityTracker

                _ct_redis = _aioredis_ct.from_url(REDIS_URL, decode_responses=True)
                _capability_tracker = RedisCapabilityTracker(_ct_redis)
            except Exception as _ct_exc:  # pragma: no cover - defensive
                logger.warning("capability_tracker_wire_failed: %s", _ct_exc)

            # Wire the model-registry override store (sync redis) + re-seed so this
            # worker's cost-aware model selection reflects env + UI overrides,
            # including any registered since the worker started.
            try:
                from app.ai_router.seeder import seed_registry_from_config

                _wire_worker_model_registry_store()
                seed_registry_from_config()
            except Exception as _mr_exc:  # pragma: no cover - defensive
                logger.warning("model_registry_store_wire_failed: %s", _mr_exc)

            # Governance parity with the in-process path (goal_service): Grantex
            # tool-grant enforcement and the tenant's compliance autonomy ceiling.
            # This path — which runs every QUEUED (production) goal — built the
            # graph without either, so grants were never enforced and a HIPAA/SOX
            # "supervised" ceiling never bound a worker goal.
            _worker_grant_store: Any = None
            _worker_enforce_grants = False
            try:
                from app.services.goal_service import (
                    _agent_grants_enforced,
                    clamp_autonomy_mode,
                )

                # TRUST-05: the ceiling binds first and fails CLOSED (supervised)
                # on any lookup error or a missing DB, as on the API path — it
                # used to be skipped, leaving e.g. a fully-autonomous agent of a
                # HIPAA tenant unclamped during a DB blip.
                _agent_autonomy_mode = clamp_autonomy_mode(
                    _agent_autonomy_mode, _worker_compliance_ceiling(db_factory, tenant_id)
                )
                _worker_enforce_grants = _agent_grants_enforced()
                if db_factory is not None:
                    from app.governance.grants.postgres_store import PostgresGrantStore

                    _worker_grant_store = PostgresGrantStore(db_factory)
            except Exception as _gov_exc:
                # Fail toward the restrictive side: no store under enforcement
                # means tool calls are denied (enforce_tool_call has no grants).
                logger.warning("worker_governance_wire_failed: %s", _gov_exc)

            # MEM-02: the same DB-wired memory services the API path gets
            # (episodic, procedural, tool reliability) — built by one helper.
            _worker_memory_services: dict[str, Any] = {}
            try:
                from app.memory.runtime_services import build_memory_graph_services

                _worker_memory_services = build_memory_graph_services(
                    db_factory, _embedder_for_graph
                )
            except Exception as _wm_exc:
                logger.warning("worker_memory_services_wire_failed: %s", _wm_exc)

            # RV-09: the same default-deny permission matrix as the API path
            # (goal_service wires app.state.permission_matrix), so destructive
            # tool globs are DENIED unless the tenant has an explicit ALLOW rule.
            # Built outside any suppress: if it cannot be built, AgentGraph
            # assembly fails and the goal fails closed instead of running
            # without the executor's per-tool permission check.
            from app.governance import permissions as _permissions_mod

            _worker_permission_matrix = _permissions_mod.build_default_permission_matrix()

            _worker_graph_services: dict[str, Any] = dict(
                **_worker_memory_services,
                planner=provider,
                executor=provider,
                permission_matrix=_worker_permission_matrix,
                verifier=_verifier_for_graph,
                model_router=_model_router,
                autonomy_mode=_agent_autonomy_mode,
                grant_store=_worker_grant_store,
                enforce_grants=_worker_enforce_grants,
                capability_tracker=_capability_tracker,
                max_iterations=_agent_max_iterations if _agent_max_iterations is not None else 100,
                result_processor=ResultProcessor(),
                dedup_cache=DeduplicationCache(),
                rollback_engine=RollbackEngine(),
                guardrail_checker=GuardrailChecker(),
                audit_log=_audit,
                hitl_gateway=_hitl,
                cost_controller=_cost,
                policy_engine=_policy,
                exec_memory=_exec_mem,
                long_term_memory=_ltm,
                embedder=_embedder_for_graph,
                eval_runner=_eval,
                cost_tracker=None,
                llm_response_cache=_llm_response_cache,
                semantic_cache=_semantic_cache_worker,
                knowledge_store=_knowledge_store_worker,
                retrieval_gateway=_retrieval_gateway_worker,
                # Redis-backed checkpointer set by worker_init signal; None → MemorySaver
                checkpointer=_WORKER_CHECKPOINTER,
                # Phase 3 services — grounding, consensus, synthesis, calibration
                grounding_checker=_phase3_grounding,
                answer_synthesizer=_phase3_synthesizer,
                calibration_store=_phase3_calibration,
                consensus_verifier=_phase3_consensus,
                # Distributed per-tenant concurrency bulkhead — same registry the
                # API path gives its graphs (tool-call concurrency per tenant).
                bulkhead_registry=_worker_bulkhead_registry(),
                # The agent's reasoning-pattern flags (snapshotted on the goal at
                # submission — the worker has no in-memory agent store).
                **_worker_pattern_flags,
            )
            # Same assembly as GoalService: the persisted runtime profile is
            # compiled (GraphFactory) when the rollout lets it drive; what runs —
            # including any downgrade — is recorded on the goal.
            from app.orchestration.profiled_graph import build_profiled_graph

            def _worker_coordination_loop() -> Any:
                # GOAL-STRATEGIES: a profile whose primary is a coordination pattern
                # (magentic, MoA, CAMEL, generative, swarm, auction) runs on the
                # pattern runtime against Postgres here too, instead of a downgrade.
                import redis.asyncio as _aioredis_coord

                from app.coordination.pattern_runs.goal_bridge import (
                    build_worker_distributed_loop,
                )

                return build_worker_distributed_loop(
                    _worker_profile,
                    db_factory=db_factory,
                    provider=real_provider,
                    cost_controller=_cost,
                    hitl_gateway=_hitl,
                    redis=_aioredis_coord.from_url(REDIS_URL, decode_responses=True),
                    agent_id=agent_id,
                )

            _agent_runner, _worker_strategy_execution = build_profiled_graph(
                _worker_profile,
                _worker_graph_services,
                dict(_worker_pattern_flags),
                distributed_loop_builder=_worker_coordination_loop,
            )
            if _worker_profile_downgrade:
                _worker_strategy_execution.setdefault("downgrades", []).append(
                    _worker_profile_downgrade
                )
            # Scorecards read the observed profile (set for non-v2 tenants too).
            with contextlib.suppress(Exception):
                _agent_runner._observed_runtime_profile = _worker_observed_profile
            _run_async(_record_worker_strategy_execution(_worker_strategy_execution))
            # RPA parity with the in-process path (goal_service sets
            # graph._rpa_executor from app.state): without it every rpa_* tool
            # call on a queued goal fell through to "Tool not found". Same
            # builder as the API lifespan — session manager + browser SSRF guard,
            # artifact store, RPA_SSRF_ALLOWED_DOMAINS. Without Playwright the
            # executor fails each call honestly (NOT IMPLEMENTED).
            from app.rpa import executor as _rpa_exec_mod

            _worker_rpa_vision = (
                _embedder_for_graph is not None
                and hasattr(_embedder_for_graph, "supports_vision")
                and _embedder_for_graph.supports_vision()
            )
            _worker_rpa_executor = _rpa_exec_mod.build_rpa_executor(
                vision_provider=_embedder_for_graph if _worker_rpa_vision else None
            )
            _agent_runner._rpa_executor = _worker_rpa_executor
            if db_factory is not None:
                _agent_runner._db_session_factory = db_factory
            _agent_runner._agent_collection_ids = list(_agent_collection_ids)
            # Grants are keyed by agent id.
            _agent_runner._agent_id = agent_id
            # Sub-goal dispatch (in-graph supervisor, civilization spawn). The API
            # path sets graph._goal_service; the worker never did, so the
            # supervisor node silently no-op'd and every spawn failed here.
            try:
                _subgoal_gs, _ = _build_worker_goal_service()
                if _subgoal_gs is not None:
                    _subgoal_gs._redis_url_for_pubsub = REDIS_URL
                    _agent_runner._goal_service = _WorkerSubgoalService(_subgoal_gs)
            except Exception as _sgs_exc:
                logger.warning("worker_subgoal_service_wire_failed: %s", _sgs_exc)
            # Wire SelfOptimizer and PromptOptimizer so A/B testing and
            # failure suggestions run during real goal execution.
            try:
                from app.intelligence.prompt_optimizer import _default_optimizer as _prompt_opt
                from app.intelligence.self_optimization import SelfOptimizer

                _self_opt = SelfOptimizer()
                if db_factory is not None:
                    _self_opt._db = db_factory
                _agent_runner._self_optimizer = _self_opt
                # Prompt variants are read from ``prompt_variants`` per tenant (DB
                # mode). The factory is per task loop (see dispose_task_engine),
                # so it is bound here rather than once at worker start — which
                # also replaces the old worker_init hook that loaded every
                # tenant's variants into each worker process.
                if db_factory is not None:
                    _prompt_opt.set_db(db_factory)
                _agent_runner._prompt_optimizer = _prompt_opt
            except Exception as _opt_exc:
                logger.warning("optimizer_wire_failed: %s", _opt_exc)

            # Wire the closed-loop self-improvement services onto the worker graph
            # so the SAME arm-injection (initialize node), A/B result recording +
            # on_goal_completed (verify node), and structured reflexion recall
            # (plan node) that the in-process API path gets also fire for goals
            # executed by this Celery worker. The mixins read
            # ``self._app_state.self_optimizer_v2`` / ``self._app_state.prompt_optimizer``
            # and ``self._agent_id`` (initialize/verify) and ``self._reflexion_service``
            # (plan). Without this the worker — the path that actually runs
            # production goals — never drove any of them, so self-improvement was
            # inert. Best-effort: any wiring failure degrades to the prior behaviour
            # (LTM / winning-plan ExecutionMemory / Eval scoring are wired above and
            # are unaffected).
            try:
                import types as _types

                from app.core.runtime_flags import get_runtime_flags as _get_rt_flags
                from app.intelligence.self_optimizer_v2 import SelfOptimizerV2

                # DB-backed SelfOptimizerV2 (Bayesian A/B + auto-apply). Uses a
                # dedicated string-decoded async Redis for its per-tenant experiment
                # state namespace.
                _so_v2_redis: Any = None
                try:
                    import redis.asyncio as _aioredis_so

                    _so_v2_redis = _aioredis_so.from_url(REDIS_URL, decode_responses=True)
                except Exception as _so_redis_exc:  # pragma: no cover - defensive
                    logger.warning("self_optimizer_v2_redis_wire_failed: %s", _so_redis_exc)

                _self_opt_v2 = SelfOptimizerV2(
                    redis=_so_v2_redis,
                    db_factory=db_factory,
                    llm_provider_factory=lambda: provider,
                    auto_apply=_get_rt_flags().enable_self_improvement_auto_apply,
                )

                # DB-backed reflexion recall (evidence-backed lessons in the planner).
                _reflexion_service = None
                try:
                    _reflexion_service = _worker_reflexion_service(
                        db_factory, _embedder_for_graph
                    )
                except Exception as _refl_exc:  # pragma: no cover - defensive
                    logger.warning("worker_reflexion_wire_failed: %s", _refl_exc)

                # The mixins reach the self-optimizer + prompt-optimizer through
                # ``_app_state``. The worker has no FastAPI app, so expose a minimal
                # namespace carrying exactly the attributes the agent nodes read.
                _agent_runner._app_state = _types.SimpleNamespace(
                    self_optimizer_v2=_self_opt_v2,
                    prompt_optimizer=_prompt_opt,
                    reflexion_service=_reflexion_service,
                    rpa_executor=_worker_rpa_executor,
                    # The graph's start-of-goal emergency-stop check reads
                    # ``_app_state._redis``; without it the check was inert here.
                    _redis=_worker_async_redis(),
                )
                _agent_runner._agent_id = agent_id
                _agent_runner._reflexion_service = _reflexion_service
            except Exception as _si_exc:
                logger.warning("self_improvement_wire_failed: %s", _si_exc)
            _agent_runner = _WorkerMCPAgentRunner(
                _agent_runner,
                _build_worker_mcp_context,
                system_prompt=_agent_system_prompt,
                rpa_executor=_worker_rpa_executor,
            )
            _use_agent_graph = True
            logger.info("Goal %s will run with AgentGraph (full capabilities)", goal_id)
        except Exception as _ag_exc:
            logger.error(
                "canonical_agentgraph_assembly_failed error_type=%s error=%s",
                type(_ag_exc).__name__,
                str(_ag_exc)[:300],
                exc_info=True,
            )
            sanitized = RuntimeError("Canonical AgentGraph assembly failed")
            _run_async(mark_worker_failed(sanitized))
            _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
            if _lock:  # WF-19: outside the lock-releasing try/finally
                _lock.release(goal_id)
            return {
                "status": "failed",
                "goal_id": goal_id,
                "reason": "agentgraph_assembly_failed",
                "message": "Canonical AgentGraph assembly failed",
            }

    # NF-10: set once the run recorded its terminal status / released its slot,
    # so an error raised AFTER that is never retried (a rerun would repeat the
    # goal's side effects) and the slot is never released twice.
    _terminal_recorded: str | None = None
    _slot_released = False
    # GOAL-STALL: keep goals.heartbeat_at fresh while this run owns the goal, so
    # the beat reaper can tell a dead / wedged runner from a live one. Started
    # right here — immediately before the try whose finally stops it — so no early
    # return can leave the thread running (it exits on its own only once it reads
    # the goal as terminal, never while the DB is unreachable).
    _heartbeat = _start_goal_heartbeat(goal_id, tenant_id, _lock, enabled=goal_bridge is not None)
    try:
        # Block fake execution outside development/test — a real LLM provider
        # (the tenant's own key or the platform's) is required. BYOK-3: this used
        # to block only ENVIRONMENT == "production"; staging ran canned answers.
        from app.providers.llm_resolution import fake_llm_allowed, no_provider_message

        if used_fake_provider and not fake_llm_allowed():
            _no_llm_msg = no_provider_message(tenant_id)
            _run_async(mark_worker_failed(RuntimeError(_no_llm_msg)))
            _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
            return {
                "status": "failed",
                "goal_id": goal_id,
                "reason": "no_llm_provider",
                "message": _no_llm_msg,
            }

        async def worker_event_callback(event: dict[str, Any]) -> None:
            await append_submitted_goal_event(event)

        import asyncio as _asyncio

        # ── Pre-execution cancel check (cross-process signal) ──────────────────
        try:
            from app.reliability.goal_lifecycle import is_cancelled_sync as _is_cancelled

            _pre_sync_r = _get_sync_redis()
            if _pre_sync_r and _is_cancelled(goal_id, _pre_sync_r):
                logger.warning(
                    "goal_cancelled_before_execution goal_id=%s tenant_id=%s",
                    goal_id,
                    tenant_id,
                )
                _run_async(
                    update_submitted_goal_status(
                        "cancelled", error_message="Cancelled before execution"
                    )
                )
                _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
                return {
                    "status": "cancelled",
                    "goal_id": goal_id,
                    "reason": "cancelled_before_execution",
                }
        except Exception as _cancel_check_exc:
            logger.warning("cancel_pre_check_failed: %s", _cancel_check_exc)

        from app.tenancy.context import PLAN_LIMITS as _PLAN_LIMITS

        goal_timeout_s = (
            getattr(_PLAN_LIMITS.get(plan, None), "goal_timeout_seconds", 1800)
            if hasattr(plan, "value")
            else 1800
        )

        try:
            # ── Isolation routing ────────────────────────────────────────────────
            # Check flag first — zero overhead when ISOLATED_AGENT_EXECUTION=false
            _use_isolation = False
            _iso_required = False
            try:
                from app.core.runtime_flags import get_runtime_flags as _get_rtflags

                _rt = _get_rtflags()
                _use_isolation = _rt.isolated_agent_execution
                _iso_required = _rt.isolated_execution_required
            except Exception as _iso_flag_exc:
                logger.warning("isolation_flag_check_failed: %s", _iso_flag_exc)

            if _use_isolation and workflow_mode == "multi_agent":
                # The isolated runner executes a single agent; routing a
                # multi_agent workflow there would silently downgrade it. Like
                # the in-process path, the workflow runs on the worker — unless
                # isolation is REQUIRED, in which case it fails explicitly.
                if _iso_required:
                    _wf_iso_reason = (
                        "multi_agent workflows cannot run in the isolated execution "
                        "environment, which is required"
                    )
                    _run_async(mark_worker_failed(RuntimeError(_wf_iso_reason)))
                    _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
                    return {
                        "status": "failed",
                        "goal_id": goal_id,
                        "reason": "workflow_mode_unsupported_in_isolation",
                        "message": _wf_iso_reason,
                    }
                _use_isolation = False

            if _use_isolation:
                # Build and dispatch an ExecutionEnvelope instead of running in-process
                _RunnerUnavail: type | None = None  # noqa: N806  # holds a class (exception type) for isinstance checks below
                try:
                    from app.execution_environment.envelope import build_envelope as _build_env
                    from app.execution_environment.models import RunnerType
                    from app.execution_environment.scheduler import (
                        ExecutionEnvironmentScheduler as _Scheduler,
                    )
                    from app.execution_environment.scheduler import (
                        RunnerUnavailableError as _RunnerUnavail,
                    )

                    _iso_flags = _rt  # reuse already-fetched flags (G-44)
                    if _iso_flags.isolated_execution_kubernetes_runner:
                        _iso_runner_type = RunnerType.KUBERNETES
                    elif _iso_flags.isolated_execution_local_runner:
                        _iso_runner_type = RunnerType.LOCAL
                    else:
                        _iso_runner_type = RunnerType.FAKE

                    # Build feature flags snapshot for the envelope (G-28)
                    _iso_feature_flags = {
                        "isolated_agent_execution": _iso_flags.isolated_agent_execution,
                        "isolated_execution_required": _iso_flags.isolated_execution_required,
                        "isolated_execution_local_runner": _iso_flags.isolated_execution_local_runner,  # noqa: E501
                        "isolated_execution_kubernetes_runner": _iso_flags.isolated_execution_kubernetes_runner,  # noqa: E501
                        "dynamic_orchestration": _iso_flags.dynamic_orchestration,
                        "agentic_rag": _iso_flags.agentic_rag,
                    }

                    # Resolve scoped LLM key (G-28). Fail CLOSED: this used to log
                    # and continue with "", so a BYOK tenant whose key could not be
                    # read ran on the platform key. '' (no BYOK configured) is fine.
                    try:
                        from app.services.llm_config_store import aget_llm_api_key_for_tenant

                        _iso_llm_key = _run_async(aget_llm_api_key_for_tenant(tenant_id))
                    except Exception as _key_exc:
                        logger.error("isolated_llm_key_resolve_failed: %s", _key_exc)
                        raise RuntimeError(
                            "tenant BYOK LLM key could not be resolved; refusing to run "
                            "the goal on the platform key"
                        ) from _key_exc

                    _iso_envelope = _build_env(
                        tenant_id=tenant_id,
                        goal_id=goal_id,
                        goal_text=effective_goal,
                        agent_id=agent_id or "",
                        dry_run=dry_run,
                        workflow_mode=workflow_mode,
                        priority=priority,
                        runner_type=_iso_runner_type,
                        feature_flags=_iso_feature_flags,
                        scoped_llm_api_key=_iso_llm_key,
                    )
                    _iso_scheduler = _Scheduler.from_flags(
                        isolated_execution_local_runner=_iso_flags.isolated_execution_local_runner,
                        isolated_execution_kubernetes_runner=_iso_flags.isolated_execution_kubernetes_runner,
                    )

                    async def _iso_event_cb(event: dict[str, Any]) -> None:
                        await append_submitted_goal_event(event)

                    _iso_result = _run_async(
                        _iso_scheduler.schedule(_iso_envelope, event_callback=_iso_event_cb)
                    )
                    _run_async(mark_worker_complete(_iso_result.status, _iso_result.iterations))
                    _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
                    return {
                        "status": _iso_result.status,
                        "goal_id": goal_id,
                        "agent_id": agent_id,
                        "workflow_mode": workflow_mode,
                        "priority": priority,
                        "dry_run": dry_run,
                        "iterations": _iso_result.iterations,
                        "runner_type": _iso_result.runner_type,
                        "capsule_id": _iso_result.capsule_id,
                        "execution_time_ms": _iso_result.execution_time_ms,
                        "result_scope": "isolated",
                    }
                except Exception as _iso_exc:
                    # Structured handling: RunnerUnavailableError vs generic (G-29)
                    _is_runner_unavail = _RunnerUnavail is not None and isinstance(
                        _iso_exc, _RunnerUnavail
                    )
                    if _is_runner_unavail:
                        logger.error(
                            "isolated_runner_unavailable goal_id=%s reason=%s",
                            goal_id,
                            str(_iso_exc)[:200],
                        )
                        _run_async(mark_worker_failed(_iso_exc))
                        _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
                        _fr = getattr(_iso_exc, "failure_reason", None)
                        return {
                            "status": "failed",
                            "goal_id": goal_id,
                            "reason": str(_iso_exc),
                            "failure_reason": _fr.value if _fr else "runner_unavailable",
                            "_isolation_error": True,
                        }
                    # Generic error in isolation path
                    logger.exception("isolated_execution_unexpected_error goal_id=%s", goal_id)
                    _run_async(mark_worker_failed(_iso_exc))
                    _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
                    return {
                        "status": "failed",
                        "goal_id": goal_id,
                        "reason": str(_iso_exc),
                        "_isolation_error": True,
                    }
            # ── End isolation routing ────────────────────────────────────────────

            # Persistence mode (retry until success) — parity with the API path,
            # which runs it through GoalPersistenceEngine. (Like the sub-goal and
            # model-override lookups, an unreadable goal row degrades to a single
            # attempt — loudly: the goal's own DB writes fail in that case anyway.)
            try:
                _persist_mode, _persist_cfg = _run_async(
                    _goal_persistence_settings(goal_id, tenant_id)
                )
            except Exception as _pm_exc:
                logger.warning(
                    "persistence_settings_lookup_failed goal=%s (single attempt): %s",
                    goal_id,
                    _pm_exc,
                )
                _persist_mode, _persist_cfg = False, {}
            if _persist_mode and _use_agent_graph:
                logger.info("Goal %s runs in persistence mode on the worker", goal_id)
                _agent_runner = _PersistentWorkerRunner(
                    _agent_runner,
                    config=_worker_persistence_config(_persist_cfg, float(goal_timeout_s)),
                    db=db_factory,
                    redis=_worker_async_redis(),
                    # ESCALATE asks a human (durable, cross-replica gateway).
                    hitl_gateway=_hitl,
                )
            # Honour workflow_mode like the in-process path: a multi_agent goal
            # runs the static workflow, never a silent single-agent downgrade.
            # (supervisor / debate are resolved by the API before submission
            # and run as single goals carrying their results — same as in-process.)
            if workflow_mode == "multi_agent":
                logger.info("Goal %s runs the multi_agent workflow on the worker", goal_id)
                _agent_runner = _WorkerWorkflowRunner(
                    _build_worker_mcp_context,
                    tool_gate=_worker_tool_gate(_policy, _hitl, _cost, agent_id),
                    goal_id=goal_id,
                    retrieval_gateway=_retrieval_gateway_worker,
                )

            from app.providers.rate_limit import run_with_llm_deadline

            _goal_run = _asyncio.wait_for(
                # P5-1: provider-throttling backoff never waits past the goal budget.
                run_with_llm_deadline(
                    _run_with_signals(
                        _agent_runner,
                        effective_goal,
                        tenant_ctx,
                        worker_event_callback,
                        goal_id,
                        initial_context=_run_async(_subgoal_context(goal_id, tenant_id)),
                        org_id=_goal_org_id or _worker_exec_ctx.get("org_id"),
                        org_unverified=_worker_ctx_unreadable and not _goal_org_id,
                    ),
                    float(goal_timeout_s),
                ),
                timeout=float(goal_timeout_s),
            )
            state = _run_async(
                _await_then_flush_audit(
                    _charged_to_agent(
                        # The heartbeat is withheld when this loop stops making progress.
                        _heartbeat.run_with_progress(_goal_run)
                        if _heartbeat is not None
                        else _goal_run,
                        agent_id,
                    ),
                    _worker_audit,
                )
            )
        except TimeoutError:
            _run_async(mark_worker_failed(TimeoutError(f"Goal timed out after {goal_timeout_s}s")))
            _run_async(
                _learn_from_worker_goal(
                    _reflexion_service,
                    _timed_out_goal_state(effective_goal, tenant_ctx, goal_id, goal_timeout_s),
                    tenant_id=tenant_id,
                    goal_id=goal_id,
                    dry_run=dry_run,
                    agent_id=agent_id or None,
                )
            )
            _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
            return {
                "status": "failed",
                "goal_id": goal_id,
                "reason": f"timeout after {goal_timeout_s}s",
                "result_scope": "worker_only",
            }
        _run_async(mark_worker_complete(state.status.value, state.iterations))
        if state.status.value in {"complete", "failed", "cancelled", "waiting_human"}:
            _terminal_recorded = state.status.value
        if state.status.value == "complete" and not dry_run:
            _publish_worker_score_below(
                state,
                tenant_id=tenant_id,
                goal_id=goal_id,
                agent_id=agent_id,
                plan=getattr(plan, "value", str(plan)),
                trigger_chain_depth=trigger_chain_depth,
                source_trigger_id=source_trigger_id,
            )
        # Certification evidence for what ran (only in-process goals recorded it).
        _run_async(
            _record_worker_strategy_evidence(
                tenant_id=tenant_id,
                goal_id=goal_id,
                execution=_worker_strategy_run.get("execution"),
                runtime_path=str(_worker_exec_ctx.get("strategy_runtime_path") or "legacy"),
                status=state.status.value,
                dry_run=dry_run,
                db_factory=db_factory,
            )
        )
        # After the terminal status is recorded: learning never delays completion.
        _run_async(
            _learn_from_worker_goal(
                _reflexion_service,
                state,
                tenant_id=tenant_id,
                goal_id=goal_id,
                dry_run=dry_run,
                agent_id=agent_id or None,
            )
        )
        if state.status.value == "waiting_human" and goal_bridge is not None:
            # Supervised mode: the graph ENDED waiting for approvals. Mark the
            # goal suspended so resume_goal relaunches it (from its step
            # checkpoints) — nothing is left running to continue it otherwise.
            try:
                _, _, _bridge = _make_worker_goal_bridge()
                _run_async(_bridge._db_set_suspended(goal_id, tenant_id, True))
            except Exception as _susp_exc:
                logger.warning("mark_suspended_failed goal=%s: %s", goal_id, _susp_exc)
        if state.status.value in {"complete", "failed"}:
            _record_goal_duration_metric(
                state.status.value,
                started_monotonic=started_monotonic,
                priority=priority,
            )
        # Decrement concurrent-goal counter — goal has reached terminal state
        _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
        _slot_released = True
        result = {
            "status": state.status.value,
            "goal_id": goal_id,
            "agent_id": agent_id,
            "connector_ids": connector_ids or [],
            "workflow_mode": workflow_mode,
            "priority": priority,
            "dry_run": dry_run,
            "iterations": state.iterations,
            "result_scope": "submitted_goal" if goal_bridge is not None else "worker_only",
            "submitted_goal_status": (
                state.status.value if goal_bridge is not None else "not_updated"
            ),
            "status_bridge": "updated" if goal_bridge is not None else "unavailable",
        }
        if used_fake_provider:
            result["provider"] = "fake"
            result["warning"] = (
                "No real LLM provider configured; FakeProvider result is not durable goal "
                "execution, but submitted goal status/events were bridged when DB was available."
            )
        return result
    except GoalCancelledError as exc:
        # An operator cancel from any replica (Redis flag). It used to fall into
        # the generic handler below, which scheduled a Celery RETRY of the
        # cancelled goal (and on the last attempt marked it failed/DLQ'd).
        logger.info("goal_cancelled_in_worker goal_id=%s: %s", goal_id, exc)
        _record_goal_duration_metric(
            "cancelled", started_monotonic=started_monotonic, priority=priority
        )
        with contextlib.suppress(Exception):
            # Conditional: a HITL rejection already recorded FAILED — keep it.
            _run_async(
                update_submitted_goal_status(
                    "cancelled", error_message=str(exc), only_if_active=True
                )
            )
        with contextlib.suppress(Exception):
            _run_async(append_submitted_goal_event({"type": "goal_cancelled"}))
        with contextlib.suppress(Exception):
            _run_async(meter_worker_goal("cancelled"))
        _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
        return {"status": "cancelled", "goal_id": goal_id, "reason": "cancelled_by_operator"}
    except PermissionError as exc:
        # A governance denial (an approval-required step outside supervised
        # mode, a policy DENY, a rejected or timed-out approval) is final.
        # Retrying re-ran the same denial and then dead-lettered the goal as
        # "exceeded max retries", burying the real reason (CORE-01).
        logger.info("goal_denied_by_governance goal_id=%s: %s", goal_id, _redacted_error(exc))
        _record_goal_duration_metric(
            "failed", started_monotonic=started_monotonic, priority=priority
        )
        _run_async(mark_worker_failed(exc))
        _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
        return {"status": "failed", "goal_id": goal_id, "reason": _redacted_error(exc)}
    except Exception as exc:
        logger.error("Goal %s failed: %s", goal_id, exc)
        # NF-10 (1): the goal already finished (or is parked for a human) — this
        # run recorded it, or another attempt / replica did. Retrying would
        # re-run its side effects and failing it would rewrite an honest
        # terminal status; neither happens. An unreadable status is "unknown":
        # a transient error still retries and the next attempt's atomic claim
        # refuses a finished goal.
        _final_status = _terminal_recorded
        if _final_status is None and goal_bridge is not None:
            try:
                _seen = _run_async(_current_goal_status(goal_id, tenant_id))
            except Exception as _status_exc:
                logger.warning(
                    "goal_status_unreadable_after_error goal_id=%s: %s", goal_id, _status_exc
                )
            else:
                if _seen in (*_TERMINAL_GOAL_STATUSES, _WAITING_HUMAN_STATUS):
                    _final_status = _seen
        if _final_status is not None:
            logger.error(
                "goal_error_after_terminal_status goal_id=%s status=%s error=%s",
                goal_id,
                _final_status,
                _redacted_error(exc),
            )
            if _terminal_recorded is not None and not _slot_released:
                _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
            return {
                "status": _final_status,
                "goal_id": goal_id,
                "reason": "error_after_terminal_status",
                "error": _redacted_error(exc),
                "retryable": False,
            }
        _record_goal_duration_metric(
            "failed", started_monotonic=started_monotonic, priority=priority
        )
        # NF-10 (2): only a transient infrastructure failure (DB/Redis
        # connection, timeout, provider 429/5xx) is retried. A programming
        # error or any other permanent failure fails ONCE with its real
        # (redacted) reason — no retry storm, no "exceeded max retries" DLQ.
        if not is_transient_infra_error(exc):
            logger.error(
                "goal_failed_non_retryable goal_id=%s error=%s", goal_id, _redacted_error(exc)
            )
            _run_async(mark_worker_failed(exc))
            _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
            return {
                "status": "failed",
                "goal_id": goal_id,
                "reason": _redacted_error(exc),
                "retryable": False,
            }
        # Decide BEFORE calling self.retry() whether this is the final,
        # unrecoverable attempt. Celery's Task.retry(exc=exc, ...) re-raises the
        # *original* exception once retries are exhausted whenever `exc` is
        # passed while an exception is active (see
        # celery.app.task.raise_with_context: it does a bare ``raise`` when
        # ``sys.exc_info()[1] is exc``) instead of raising
        # MaxRetriesExceededError. That means the ``except
        # self.MaxRetriesExceededError`` handler below never actually fires —
        # so DLQ enqueue, the terminal DB status update, and the tenant
        # concurrent-goal counter decrement were silently skipped on every
        # goal that truly exhausted its retries (a permanently-broken goal
        # would fail as a bare Celery task exception with none of that
        # bookkeeping, and the per-tenant concurrency counter would leak
        # forever). Checking self.request.retries here is the only reliable
        # way to detect exhaustion.
        _retries_exhausted = self.request.retries >= self.max_retries
        if _retries_exhausted:
            # Terminal failure: do the once-only bookkeeping now and return,
            # instead of calling self.retry() (which would just re-raise
            # `exc` and skip everything below).
            with contextlib.suppress(Exception):
                run_goal_dlq.delay(
                    goal_id=goal_id,
                    tenant_id=tenant_id,
                    goal_text=effective_goal,
                    reason="max_retries_exceeded",
                )
            _run_async(
                mark_worker_failed(
                    RuntimeError(f"Goal {goal_id} exceeded max retries, routed to DLQ")
                )
            )
            # Decrement counter — goal is permanently done
            _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
            return {"status": "dead_lettered", "goal_id": goal_id}

        # Not yet exhausted: this goal is about to be retried, so it is NOT
        # terminally failed. Previously `mark_worker_failed(exc)` ran
        # unconditionally here, which set the goal's DB status to "failed"
        # and (via _finalize_owning_mission) permanently finalized any owning
        # org mission on a purely transient error (rate limit, network blip,
        # etc.) before the retry had a chance to succeed. finalize_mission
        # only reconciles missions that are still "active", so a later
        # successful retry's own mark_worker_complete -> _finalize_owning_
        # mission call became a silent no-op — the mission stayed wrongly
        # "failed" forever even though the goal went on to complete.
        logger.warning(
            "goal_transient_failure_will_retry goal_id=%s attempt=%s/%s error=%s",
            goal_id,
            self.request.retries + 1,
            self.max_retries,
            exc,
        )
        raise self.retry(exc=exc, countdown=2**self.request.retries) from exc
    finally:
        if _heartbeat is not None:
            with contextlib.suppress(Exception):
                _heartbeat.stop()
        # Release distributed lock
        if _lock:
            with contextlib.suppress(Exception):
                _lock.release(goal_id)


@celery_app.task(name="app.scaling.tasks.run_scheduled_goal", bind=True, max_retries=3)  # type: ignore[untyped-decorator]
def run_scheduled_goal(
    self: Any,
    schedule_id: str,
    tenant_id: str,
    goal_template: str,
    agent_id: str = "",
    fire_instance_id: str = "",
    trigger_type: str = "cron",
    condition: str = "",
    max_firings_per_hour: int = 0,
    tenant_plan: str = "",
    event_payload: dict[str, Any] | None = None,
    condition_expression: str = "",
    expires_at_iso: str = "",
) -> dict[str, Any]:
    """Execute a scheduled goal trigger.

    WT-9: routes through the TriggerDispatcher so scheduled fires get the same
    dedup / rate-limit / circuit-breaker / condition governance as every other
    trigger type, instead of enqueueing ``run_goal`` directly. The dispatcher
    creates the goal via ``GoalService.create_goal``.

    ``trigger_type`` / ``condition`` / ``max_firings_per_hour`` / ``tenant_plan``
    used to be dropped here (every fire was dispatched as a plain ``cron`` with
    no condition), and the beat's polling families (file_drop, rss_feed,
    api_poll, db_row_change, alert webhooks) bypassed this task entirely.
    ``event_payload`` carries what the poll observed (file, entry, value, alert)
    so the condition and ``{{payload.*}}`` template see it. ``tenant_plan`` is
    accepted for already-queued messages but ignored: the plan is read from the
    tenant record at fire time (TRG-06).
    """
    logger.info("Firing schedule %s for tenant %s", schedule_id, tenant_id)
    try:
        event = _run_async(
            _run_scheduled_goal_governed(
                schedule_id,
                tenant_id,
                goal_template,
                agent_id,
                fire_instance_id,
                trigger_type=trigger_type,
                condition=condition,
                max_firings_per_hour=max_firings_per_hour,
                event_payload=event_payload,
                condition_expression=condition_expression,
                expires_at_iso=expires_at_iso,
            )
        )
    except Exception as exc:
        logger.warning("Scheduled goal dispatch failed for %s: %s", schedule_id, exc)
        raise self.retry(exc=exc, countdown=2**self.request.retries) from exc

    goal_created = bool(getattr(event, "goal_created", False))
    skip_reason = getattr(event, "skip_reason", None)
    return {
        "status": "dispatched" if goal_created else "skipped",
        "schedule_id": schedule_id,
        "goal_id": getattr(event, "goal_id", None),
        "skip_reason": skip_reason,
    }


def _scheduled_goal_kwargs(
    schedule_key: str,
    sched: dict[str, Any],
    *,
    fire_instance_id: str | None = None,
) -> dict[str, Any] | None:
    goal_text = str(sched.get("goal_template") or sched.get("goal_id") or "")
    tenant_id = str(sched.get("tenant_id") or "")
    agent_id = str(sched.get("agent_id") or "")
    # Fire when there is EITHER a goal template OR a referenced agent (whose own
    # goal will be run). Previously an agent-only trigger — no goal template —
    # was skipped entirely, so referencing an agent never fired.
    if (not goal_text and not agent_id) or not tenant_id:
        return None

    payload = {
        "goal_id": _scheduled_goal_id(
            schedule_key,
            fire_instance_id=fire_instance_id,
        ),
        "goal_text": goal_text,
        "goal_template": goal_text,
        "tenant_id": tenant_id,
    }
    if agent_id:
        payload["agent_id"] = agent_id
    return payload


def _dispatch_due_schedule(
    schedule_key: str,
    sched: dict[str, Any],
    *,
    via_scheduled_task: bool,
    fire_instance_id: str,
) -> dict[str, Any] | None:
    goal_kwargs = _scheduled_goal_kwargs(
        schedule_key,
        sched,
        fire_instance_id=fire_instance_id,
    )
    if goal_kwargs is None:
        return None
    # WT-9: both DB- and Redis-backed schedules dispatch through run_scheduled_goal,
    # which routes the fire through the governed TriggerDispatcher. via_scheduled_task
    # is retained for callers/telemetry that distinguish the two schedule sources.
    _ = via_scheduled_task
    _enqueue_governed_fire(
        schedule_key,
        sched,
        goal_template=str(goal_kwargs["goal_template"]),
        tenant_id=str(goal_kwargs["tenant_id"]),
        agent_id=str(goal_kwargs.get("agent_id") or ""),
        fire_instance_id=fire_instance_id,
    )
    return goal_kwargs


def _enqueue_governed_fire(
    schedule_key: str,
    sched: dict[str, Any],
    *,
    goal_template: str,
    tenant_id: str,
    agent_id: str,
    fire_instance_id: str,
    event_payload: dict[str, Any] | None = None,
) -> None:
    """Enqueue ``run_scheduled_goal`` — the ONE governed path for beat fires.

    Carries the schedule's real trigger type, condition, rate cap and plan so
    the dispatcher applies them (they used to be dropped), plus the observed
    ``event_payload`` for the polling/alert families.
    """
    kwargs: dict[str, Any] = {
        "schedule_id": schedule_key,
        "tenant_id": tenant_id,
        "goal_template": goal_template,
        "agent_id": agent_id,
        "fire_instance_id": fire_instance_id,
        "trigger_type": str(sched.get("trigger_type") or "cron"),
        "condition": str(sched.get("condition") or ""),
        "max_firings_per_hour": int(sched.get("max_firings_per_hour") or 0),
        # No tenant_plan: the governed dispatch resolves it from the tenant record.
    }
    # TRG-07: the API stores ``condition_cel`` as ``condition_expression`` (via
    # the config JSONB); forwarding only ``condition`` made a CEL-gated trigger
    # fire unconditionally.
    condition_expression = str(sched.get("condition_expression") or "")
    if condition_expression:
        kwargs["condition_expression"] = condition_expression
    # TRG-09: the dispatcher re-checks expiry when the task runs (it may be
    # queued past the deadline).
    expires_at_iso = str(sched.get("expires_at_iso") or "")
    if expires_at_iso:
        kwargs["expires_at_iso"] = expires_at_iso
    if event_payload is not None:
        kwargs["event_payload"] = event_payload
    run_scheduled_goal.apply_async(kwargs=kwargs, queue="schedules")


def _dispatch_beat_event_fire(
    schedule_key: str,
    sched: dict[str, Any],
    *,
    goal_text: str,
    fire_instance_id: str,
    event_payload: dict[str, Any],
) -> bool:
    """Route a polling/alert beat fire (file_drop, rss_feed, api_poll,
    db_row_change, alertmanager/datadog/pagerduty) through the dispatcher.

    These branches used to call ``run_goal.apply_async`` directly, bypassing
    the TriggerDispatcher: no durable dedup, no rate limit / circuit breaker /
    bulkhead, the schedule's condition was ignored, and no ``trigger_events``
    audit row was written (so schedule history never showed them). The
    ``fire_instance_id`` (file name / entry id / observed value) becomes the
    dispatcher's idempotency input, so the same file or entry fires once even
    across replicas and after the Redis dedup window expires.
    """
    tenant_id = str(sched.get("tenant_id") or "")
    if not tenant_id or not goal_text:
        return False
    _enqueue_governed_fire(
        schedule_key,
        sched,
        goal_template=goal_text,
        tenant_id=tenant_id,
        agent_id=str(sched.get("agent_id") or ""),
        fire_instance_id=fire_instance_id,
        event_payload=event_payload,
    )
    return True


def _bare_schedule_id(schedule_key: str) -> str:
    """``schedule:{tenant}:{id}`` → ``{id}`` (other keys are returned as-is).

    The beat used the full Redis key as the dispatcher's ``trigger_id``. That is
    74 characters, but ``trigger_events.trigger_id`` is VARCHAR(36): every beat
    fire's audit INSERT failed (swallowed as ``persist_event_failed``), so beat
    fires had no audit trail, the durable post-TTL dedup never saw them, and
    ``GET /triggers/{id}/events`` / schedule history (keyed by the bare id)
    could not find them. The bare id also matches the API fire path's
    ``trigger_id``, so rate limits / circuit breakers are shared per trigger.
    """
    parts = schedule_key.split(":", 2)
    if len(parts) == 3 and parts[0] == "schedule" and parts[2]:
        return parts[2]
    return schedule_key


def _build_scheduled_trigger_spec(schedule_key: str, sched: dict[str, Any]) -> Any:
    """Map a schedule dict onto a TriggerSpec for governed dispatch (WT-9)."""
    from app.triggers.models import TriggerSpec, TriggerType

    raw_type = str(sched.get("trigger_type") or "cron")
    try:
        trigger_type = TriggerType(raw_type)
    except ValueError:
        trigger_type = TriggerType.ONCE

    goal_text = str(sched.get("goal_template") or sched.get("goal_id") or "")
    spec = TriggerSpec(
        trigger_type=trigger_type,
        goal_template=goal_text,
        condition=str(sched.get("condition") or ""),
        # TRG-07: the dispatcher requires BOTH gates to hold.
        condition_expression=str(sched.get("condition_expression") or ""),
        expires_at_iso=str(sched.get("expires_at_iso") or ""),
        max_firings_per_hour=int(sched.get("max_firings_per_hour") or 0),
        watch_agent_id=str(sched.get("agent_id") or ""),
    )
    # trigger_id is an instance attribute (not a dataclass field) — see dispatcher.
    spec.trigger_id = _bare_schedule_id(schedule_key)  # type: ignore[attr-defined]
    return spec


async def _dispatch_scheduled_via_dispatcher(
    schedule_key: str,
    sched: dict[str, Any],
    *,
    fire_instance_id: str,
    goal_service: Any = None,
    redis: Any = None,
    db_factory: Any = None,
    dispatcher: Any = None,
) -> Any:
    """Route a scheduled fire through the TriggerDispatcher for governance parity.

    The dispatcher applies the same dedup / rate-limit / circuit-breaker /
    condition pipeline as every other trigger type. ``scheduled_fire_time`` (the
    fire instance id) makes the idempotency key stable per fire, so a duplicate
    beat tick for the same instant is deduped instead of creating a second goal.

    Returns the ``TriggerEvent`` / ``SimulatedTriggerResult`` from the dispatcher,
    or ``None`` when the schedule has no usable goal/tenant.
    """
    from types import SimpleNamespace

    goal_kwargs = _scheduled_goal_kwargs(schedule_key, sched, fire_instance_id=fire_instance_id)
    if goal_kwargs is None:
        return None

    spec = _build_scheduled_trigger_spec(schedule_key, sched)
    # TRG-06: the plan is the tenant's REAL plan, read from the tenant record at
    # fire time. Schedule payloads never carried one, so every beat fire ran as
    # FREE (10 fires/h, 2 bulkhead slots, the free queue); a plan stored in the
    # schedule is client-supplied or stale after a plan change and is ignored.
    # It is a PlanTier enum: goal creation reads ``tenant_ctx.plan.value``.
    from app.tenancy.plan_resolver import resolve_tenant_plan

    tenant_id = str(sched.get("tenant_id") or "")
    tenant_ctx = SimpleNamespace(
        tenant_id=tenant_id,
        plan=await resolve_tenant_plan(tenant_id, db_factory=db_factory),
    )

    if dispatcher is None:
        from app.triggers.dispatcher import TriggerDispatcher

        dispatcher = TriggerDispatcher(
            goal_service=goal_service,
            db_session_factory=db_factory,
            redis=redis,
        )

    # The observed event (file / entry / polled value / alert) is the payload the
    # condition and ``{{payload.*}}`` template evaluate; the schedule's own goal
    # fields win on key collisions so an external payload cannot rewrite them.
    event_payload = sched.get("event_payload")
    payload: dict[str, Any] = {**event_payload} if isinstance(event_payload, dict) else {}
    payload.update(goal_kwargs)
    return await dispatcher.dispatch(
        spec,
        payload,
        tenant_ctx,
        scheduled_fire_time=fire_instance_id,
        # Data-family triggers (file_drop, rss_feed, db_row_change) derive their
        # idempotency key from txn_id: the file / entry / count being fired on.
        txn_id=fire_instance_id,
    )


def _build_worker_goal_service() -> tuple[Any, Any]:
    """Construct a worker-local GoalService + DB factory (Celery worker context).

    Mirrors the pattern used by the email/IMAP task: a fresh async session
    factory feeds an EventStore-backed GoalService. Returns ``(goal_service,
    db_factory)``; on failure returns ``(None, None)`` so the caller can degrade.
    """
    try:
        from app.db.session import get_session_factory
        from app.services.event_store import EventStore
        from app.services.goal_queue import CeleryGoalTaskQueue
        from app.services.goal_service import GoalService

        db_factory = get_session_factory()
        # task_queue is REQUIRED here: without it submit_goal runs the goal as an
        # asyncio task inside _run_async's throwaway loop, which is closed as soon
        # as this Celery task returns — the scheduled goal was silently killed.
        # With it the goal is persisted and handed to run_goal (per-plan queue).
        goal_service = GoalService(
            db_session_factory=db_factory,
            event_store=EventStore(db_factory),
            task_queue=CeleryGoalTaskQueue(),
        )
        return goal_service, db_factory
    except Exception as exc:
        logger.warning("worker_goal_service_build_failed", error=str(exc)[:120])
        return None, None


def _publish_worker_score_below(
    state: Any,
    *,
    tenant_id: str,
    goal_id: str,
    agent_id: str,
    plan: str,
    trigger_chain_depth: int,
    source_trigger_id: str,
) -> bool:
    """Publish ``goal.score_below`` for a worker-run goal from its scorecard.

    The verifier scores a completed goal and leaves the scorecard on
    ``state.context["eval_scorecard"]``. ``goal.score_below`` used to be
    published only by GoalService's in-process eval path, so goal_score_below
    triggers never fired for worker-run goals (TRG-22). Like that path, the score
    is always published and ChainTriggerConsumer compares it with each trigger's
    threshold; the deterministic ``completion_event_id`` makes a Celery retry or
    an API relay of the same goal dispatch once.
    """
    context = getattr(state, "context", None)
    scorecard = context.get("eval_scorecard") if isinstance(context, dict) else None
    average = getattr(scorecard, "average_score", None)
    if not callable(average):
        return False
    try:
        score = float(average())
    except Exception:
        return False
    try:
        redis_client = _get_sync_redis()
        if redis_client is None:
            return False
        from app.triggers.bus import publish_trigger_event_sync
        from app.triggers.consumers.chain import build_chain_event

        publish_trigger_event_sync(
            redis_client,
            "goal.score_below",
            build_chain_event(
                channel="goal.score_below",
                tenant_id=tenant_id,
                goal_id=goal_id,
                agent_id=agent_id or "",
                status="complete",
                tenant_plan=plan,
                trigger_chain_depth=trigger_chain_depth,
                score=score,
                source_trigger_id=source_trigger_id,
            ),
        )
        return True
    except Exception as exc:
        logger.warning("goal_score_below_publish_failed goal=%s: %s", goal_id, exc)
        return False


def _worker_long_term_memory() -> Any:
    """The worker's LongTermMemoryStore, able to publish ``memory.created``.

    A bare store has no event Redis, so memories written by worker-run goals
    (i.e. production goals) never fired MEMORY_CREATED triggers (TRG-21).
    """
    from app.memory.long_term import LongTermMemoryStore

    store = LongTermMemoryStore()
    store.set_event_redis(_worker_async_redis())
    return store


def _worker_async_redis() -> Any:
    """Best-effort async Redis client for the worker (or ``None``)."""
    redis_url = os.getenv("REDIS_URL", "") or celery_app.conf.broker_url or ""
    if not redis_url:
        return None
    try:
        import redis.asyncio as aioredis

        return aioredis.from_url(redis_url, decode_responses=True)
    except Exception as exc:
        logger.warning("worker_async_redis_failed", error=str(exc)[:120])
        return None


async def _run_scheduled_goal_governed(
    schedule_id: str,
    tenant_id: str,
    goal_template: str,
    agent_id: str,
    fire_instance_id: str,
    *,
    trigger_type: str = "cron",
    condition: str = "",
    max_firings_per_hour: int = 0,
    event_payload: dict[str, Any] | None = None,
    condition_expression: str = "",
    expires_at_iso: str = "",
) -> Any:
    """Async body of ``run_scheduled_goal`` — governed scheduled dispatch (WT-9)."""
    goal_service, db_factory = _build_worker_goal_service()
    redis = _worker_async_redis()
    sched: dict[str, Any] = {
        "trigger_type": trigger_type or "cron",
        "goal_template": goal_template,
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "condition": condition,
        "condition_expression": condition_expression,
        "expires_at_iso": expires_at_iso,
        "max_firings_per_hour": max_firings_per_hour,
    }
    if event_payload is not None:
        sched["event_payload"] = event_payload
    try:
        return await _dispatch_scheduled_via_dispatcher(
            schedule_id,
            sched,
            fire_instance_id=fire_instance_id,
            goal_service=goal_service,
            redis=redis,
            db_factory=db_factory,
        )
    finally:
        if redis is not None:
            with contextlib.suppress(Exception):
                await redis.aclose()


async def _build_goal_kwargs_for_alert(
    sched: dict,
    trigger_type: str,
    alert_context: dict,
    goal_service: Any,
    tenant_ctx: Any,
    default_priority: str = "high",
) -> dict | None:
    """Build goal submission kwargs for an external alert trigger.

    Returns a dict suitable for dispatching via run_goal.apply_async, or None
    if a goal cannot be derived from the schedule and alert context.
    """
    try:
        goal_text = sched.get("goal_text") or sched.get("goal") or sched.get("goal_template") or ""
        if not goal_text:
            if trigger_type == "alertmanager":
                alert_name = alert_context.get("alertname", "unknown")
                severity = alert_context.get("severity", "warning")
                goal_text = (
                    f"Investigate and resolve {severity} alert: {alert_name}. "
                    f"{alert_context.get('description', '')}"
                )
            elif trigger_type == "datadog":
                monitor_name = alert_context.get("monitor_name", "unknown")
                goal_text = (
                    f"Investigate Datadog alert: {monitor_name}. "
                    f"Status: {alert_context.get('status', 'triggered')}"
                )
            elif trigger_type == "pagerduty":
                incident_title = alert_context.get("incident_title", "unknown")
                goal_text = (
                    f"Respond to PagerDuty incident: {incident_title}. "
                    f"Urgency: {alert_context.get('urgency', 'high')}"
                )
            else:
                goal_text = f"Handle {trigger_type} trigger event"

        if not goal_text:
            return None

        if alert_context:
            context_str = "\n".join(f"  {k}: {v}" for k, v in list(alert_context.items())[:5])
            goal_text = f"{goal_text}\n\nAlert context:\n{context_str}"

        agent_id = sched.get("agent_id")
        priority = sched.get("priority", default_priority)

        return {
            "goal": goal_text[:500],
            "priority": priority,
            "dry_run": False,
            "tenant_ctx": tenant_ctx,
            "agent_id": agent_id,
            "execution_context": {
                "trigger_type": trigger_type,
                "trigger_source": "automated",
                "alert_context": alert_context,
            },
        }
    except Exception as exc:
        logger.warning(
            "build_goal_kwargs_for_alert_failed",
            trigger_type=trigger_type,
            error=str(exc)[:80],
        )
        return None


def _consume_alert_payload(r: Any, cache_key: str) -> dict[str, Any] | None:
    """Atomically take (read + delete) a cached alert payload, or None.

    ``GETDEL`` makes exactly one beat replica the consumer. On a Redis without
    GETDEL (< 6.2) it falls back to GET then DEL, where only the caller whose
    DEL actually removed the key (returns 1) consumes it.
    """
    import json as _json

    if r is None:
        return None
    raw: Any
    try:
        raw = r.getdel(cache_key)
    except Exception:
        try:
            raw = r.get(cache_key)
            if raw is None or int(r.delete(cache_key) or 0) != 1:
                return None
        except Exception as exc:
            logger.warning("alert_payload_consume_failed key=%s: %s", cache_key, exc)
            return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    if not isinstance(raw, str) or not raw:
        return None
    try:
        data = _json.loads(raw)
    except ValueError:
        logger.warning("alert_payload_not_json key=%s", cache_key)
        return None
    if not isinstance(data, dict):
        data = {"data": data}
    return data or None


def _schedule_key(tenant_id: str, schedule_id: str) -> str:
    return f"schedule:{tenant_id}:{schedule_id}"


def _datetime_to_naive_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        dt = value
        if dt.tzinfo is not None:
            dt = dt.astimezone(datetime.UTC).replace(tzinfo=None)
        return dt.isoformat()
    return str(value)


def _schedule_datetime(value: Any) -> datetime.datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        dt = value
    else:
        dt = datetime.datetime.fromisoformat(str(value))
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.UTC).replace(tzinfo=None)
    return dt


# Upper bound on how many missed fires a single schedule may replay in one beat
# cycle after an outage. Prevents a high-frequency schedule (e.g. every minute)
# that has been dark for hours from flooding the queue with thousands of goals.
_MISSED_FIRE_CAP = 60


def _to_utc_naive(dt: datetime.datetime, assume_tz: datetime.tzinfo) -> datetime.datetime:
    """Return ``dt`` as a UTC-naive datetime.

    Naive inputs are assumed to already be wall-clock time in ``assume_tz``.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=assume_tz)
    return dt.astimezone(datetime.UTC).replace(tzinfo=None)


def _norm_utc_naive(dt: datetime.datetime | None) -> datetime.datetime | None:
    """Normalise an already-UTC datetime (naive or aware) to UTC-naive."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(datetime.UTC).replace(tzinfo=None)
    return dt


def _resolve_tz(tz_name: str) -> datetime.tzinfo:
    if not tz_name or tz_name.upper() == "UTC":
        return datetime.UTC
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tz_name)
    except Exception:
        return datetime.UTC


def _cron_missed_runs_utc(
    cron_expr: str,
    last_fired_utc: datetime.datetime | None,
    now_utc: datetime.datetime,
    tz_name: str = "UTC",
    cap: int = _MISSED_FIRE_CAP,
) -> list[datetime.datetime]:
    """Return every cron fire slot that is due but unfired, as UTC-naive datetimes.

    The cron expression is evaluated on wall-clock time in ``tz_name``. Results are
    returned oldest-first and cover the window ``(last_fired_utc, now_utc]``:

    * ``last_fired_utc is None`` — the schedule has never fired: only the single
      most-recent slot at/before ``now_utc`` is returned (no history backfill).
    * otherwise — every slot strictly after ``last_fired_utc`` and at/before
      ``now_utc`` is returned, capped to the most-recent ``cap`` slots.

    Raises whatever ``croniter`` raises for an invalid expression (callers catch
    and skip the schedule).
    """
    import croniter as _croniter_pkg  # type: ignore[import-untyped]

    tz = _resolve_tz(tz_name)
    now_naive = _norm_utc_naive(now_utc)
    assert now_naive is not None
    now_local = now_naive.replace(tzinfo=datetime.UTC).astimezone(tz)

    if last_fired_utc is None:
        itr = _croniter_pkg.croniter(cron_expr, now_local + datetime.timedelta(seconds=1))
        prev = cast(datetime.datetime, itr.get_prev(datetime.datetime))
        return [_to_utc_naive(prev, tz)]

    last_naive = _norm_utc_naive(last_fired_utc)
    itr = _croniter_pkg.croniter(cron_expr, now_local + datetime.timedelta(seconds=1))
    runs: list[datetime.datetime] = []
    while len(runs) < cap:
        prev_local = cast(datetime.datetime, itr.get_prev(datetime.datetime))
        prev_naive = _to_utc_naive(prev_local, tz)
        if last_naive is not None and prev_naive <= last_naive:
            break
        runs.append(prev_naive)
    runs.reverse()
    return runs


def _naive(dt: datetime.datetime) -> datetime.datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _relative_delay_due_utc(
    fire_at_iso: str,
    offset_seconds: int,
    now: datetime.datetime,
    last_fired: datetime.datetime | None,
) -> datetime.datetime | None:
    """RELATIVE_DELAY: fire once at (base + offset). ``base`` is ``fire_at_iso``;
    a positive offset fires after it, negative before. Returns the fire instant
    (UTC-naive) if due and unfired, else None."""
    base = _schedule_datetime(fire_at_iso)
    if base is None or last_fired is not None:
        return None
    target = base + datetime.timedelta(seconds=int(offset_seconds or 0))
    return target if _naive(now) >= _naive(target) else None


def _deadline_due_utc(
    fire_at_iso: str,
    warning_seconds: int,
    now: datetime.datetime,
    last_fired: datetime.datetime | None,
) -> datetime.datetime | None:
    """DEADLINE: fire once ``warning_seconds`` before the deadline in
    ``fire_at_iso``."""
    base = _schedule_datetime(fire_at_iso)
    if base is None or last_fired is not None:
        return None
    target = base - datetime.timedelta(seconds=int(warning_seconds or 0))
    return target if _naive(now) >= _naive(target) else None


_INTERVAL_EPOCH = datetime.datetime(1970, 1, 1)


def _interval_due_slot_utc(
    interval_seconds: int,
    last_fired_utc: datetime.datetime | None,
    now_utc: datetime.datetime,
) -> datetime.datetime | None:
    """INTERVAL: return the deterministic fire slot if the schedule is due, else None.

    The slot MUST NOT be derived from wall-clock ``now_utc`` directly (e.g. via
    ``now_utc.isoformat()``), because it doubles as the trigger's idempotency key
    (``derive_idempotency_key`` / ``scheduled_fire_time``). Two concurrent
    executions of ``fire_due_schedules`` (overlapping beat ticks after the
    ``beat_task_guard`` lock expires under load, or a retry racing the prior
    attempt) each capture their own ``now_utc`` a few milliseconds apart, so a
    wall-clock-based key would differ between the two runs and the Redis
    dedup (keyed on that value) would never see a collision — letting the same
    interval firing dispatch twice. Bucketing to a fixed epoch-aligned slot
    ("this interval's slot"), like the cron slot helper already does, keeps
    the key identical across concurrent, near-simultaneous evaluations.
    """
    if interval_seconds <= 0:
        return None
    if last_fired_utc is not None and (now_utc - last_fired_utc).total_seconds() < interval_seconds:
        return None
    elapsed = (now_utc - _INTERVAL_EPOCH).total_seconds()
    slot_index = int(elapsed // interval_seconds)
    return _INTERVAL_EPOCH + datetime.timedelta(seconds=slot_index * interval_seconds)


def _is_business_time(dt_utc: datetime.datetime, tz_name: str = "UTC") -> bool:
    """True when the instant is Mon-Fri, 09:00-17:00 (local wall-clock in tz)."""
    tz = _resolve_tz(tz_name)
    aware = dt_utc.replace(tzinfo=datetime.UTC) if dt_utc.tzinfo is None else dt_utc
    local = aware.astimezone(tz)
    return local.weekday() < 5 and 9 <= local.hour < 17


def _business_calendar_slots(
    cron_expr: str,
    last_fired: datetime.datetime | None,
    now: datetime.datetime,
    tz_name: str = "UTC",
) -> list[datetime.datetime]:
    """BUSINESS_CALENDAR: cron slots that fall within business hours only."""
    if not cron_expr:
        return []
    slots = _cron_missed_runs_utc(cron_expr, last_fired, now, tz_name)
    return [s for s in slots if _is_business_time(s, tz_name)]


_SAFE_TABLE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _db_row_change_allowlist() -> frozenset[str]:
    """Operator-configured allowlist of tables DB_ROW_CHANGE may poll."""
    from app.core.config import get_settings

    raw = getattr(get_settings(), "db_row_change_tables", "") or ""
    return frozenset(t.strip() for t in raw.split(",") if t.strip())


def _safe_db_table(name: str, allowlist: frozenset[str]) -> bool:
    """A table is pollable only if it is a bare identifier AND allowlisted —
    so a tenant-supplied db_table can never inject SQL or read an off-limits table."""
    return bool(name) and bool(_SAFE_TABLE.match(name)) and name in allowlist


def _row_change_fires(current_count: int, last_count: int | None) -> bool:
    """DB_ROW_CHANGE fires when the tenant's row count in the watched table has
    grown since the last poll (first observation establishes a baseline)."""
    if last_count is None:
        return False
    return current_count > last_count


async def _count_tenant_rows(table: str, tenant_id: str, allowlist: frozenset[str]) -> int | None:
    """Count a tenant's rows in an allowlisted table (RLS-scoped). Returns None
    on error. The table name is re-validated here (defense-in-depth) so it can
    only ever be a vetted, allowlisted identifier before interpolation."""
    if not _safe_db_table(table, allowlist):
        return None
    from sqlalchemy import text as _sa_text

    from app.db.rls import sqlalchemy_rls_context
    from app.db.session import get_session_factory as _get_fresh_db

    db = _get_fresh_db()
    async with db() as session, sqlalchemy_rls_context(session, tenant_id):
        # `table` is validated (allowlist + safe-identifier regex) above, so this
        # interpolation cannot inject SQL or reach an off-limits table.
        result = await session.execute(
            _sa_text(f"SELECT COUNT(*) FROM {table} WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        )
        return int(result.scalar_one())


def _db_schedule_payload(row: Any) -> dict[str, Any]:
    goal_template = str(getattr(row, "goal_id_template", "") or "")
    tenant_id = str(getattr(row, "tenant_id", "") or "")
    schedule_id = str(getattr(row, "id", "") or "")
    # Family-specific fields (file_watch_path, rss_url, poll_url, …) live in the
    # schedules.config JSONB; merge them so the beat branches can read them.
    config = getattr(row, "config", None) or {}
    payload = {
        "schedule_id": schedule_id,
        "tenant_id": tenant_id,
        "goal_id": goal_template,
        "agent_id": str(getattr(row, "agent_id", "") or ""),
        "goal_template": goal_template,
        "trigger_type": str(getattr(row, "trigger_type", "") or ""),
        "cron_expression": str(getattr(row, "cron_expression", "") or ""),
        "timezone": str(getattr(row, "timezone", "") or "UTC"),
        "interval_seconds": int(getattr(row, "interval_seconds", 0) or 0),
        "webhook_token": str(getattr(row, "webhook_token", "") or ""),
        "event_channel": str(getattr(row, "event_channel", "") or ""),
        "fire_at_iso": str(getattr(row, "fire_at_iso", "") or ""),
        "condition": str(getattr(row, "condition", "") or ""),
        "description": str(getattr(row, "description", "") or ""),
        "paused": bool(getattr(row, "paused", False)),
        "last_fired_at": _datetime_to_naive_iso(getattr(row, "last_fired_at", None)),
        "next_fire_at": _datetime_to_naive_iso(getattr(row, "next_fire_at", None)),
    }
    if isinstance(config, dict):
        payload.update(config)
    return payload


# Types the beat loop evaluates (BEAT_TYPES plus the cached-alert branches).
_BEAT_DISCOVERY_TYPES: tuple[str, ...] = (
    "cron",
    "interval",
    "once",
    "relative_delay",
    "deadline",
    "business_calendar",
    "file_drop",
    "rss_feed",
    "api_poll",
    "db_row_change",
    "alertmanager",
    "datadog",
    "pagerduty",
)
# Upper bound on due rows claimed per tick across ALL tenants (oldest due first;
# the rest are next tick's). BEAT_DUE_BATCH overrides.
_DUE_BATCH = 5000
# How long a claimed row stays invisible to the next claim; the beat writes the
# real next_fire_at well within it (and fire_due_schedules' guard is 300s).
_CLAIM_LEASE_SECONDS = 600
# "Never again" for fired one-shot schedules (Python cannot hold 'infinity').
_NEVER = datetime.datetime(9999, 1, 1, tzinfo=datetime.UTC)


def _next_evaluation_at(
    sched: dict[str, Any], now: datetime.datetime
) -> datetime.datetime | None:
    """When the beat next needs to look at *sched* (TRG-15), UTC-aware.

    ``None`` means "every tick": polling / alert families, and anything whose
    next time cannot be computed (a create/edit/resume also resets it to None
    so the change is evaluated on the next tick).
    """
    trigger_type = str(sched.get("trigger_type") or "")
    now_aware = now.replace(tzinfo=datetime.UTC) if now.tzinfo is None else now
    last = _schedule_datetime(sched.get("last_fired_at"))

    def _utc(dt: datetime.datetime) -> datetime.datetime:
        return dt.replace(tzinfo=datetime.UTC) if dt.tzinfo is None else dt

    if trigger_type in ("cron", "business_calendar"):
        expr = str(sched.get("cron_expression") or "")
        if not expr:
            return None
        from croniter import croniter

        tz = _resolve_tz(str(sched.get("timezone") or "UTC"))
        nxt = croniter(expr, now_aware.astimezone(tz)).get_next(datetime.datetime)
        return _utc(nxt).astimezone(datetime.UTC)
    if trigger_type == "interval":
        seconds = int(sched.get("interval_seconds") or 0)
        if seconds <= 0 or last is None:
            return None
        return _utc(last + datetime.timedelta(seconds=seconds))
    if trigger_type in _POLL_TRIGGER_TYPES:
        # TRG-54: polled every interval, not loaded on every tick.
        return now_aware + datetime.timedelta(seconds=_poll_interval_seconds(sched))
    if trigger_type in ("once", "relative_delay", "deadline"):
        if last is not None:
            return _NEVER
        base = _schedule_datetime(sched.get("fire_at_iso"))
        if base is None:
            return None
        if trigger_type == "relative_delay":
            base += datetime.timedelta(seconds=int(sched.get("relative_offset_seconds") or 0))
        elif trigger_type == "deadline":
            base -= datetime.timedelta(seconds=int(sched.get("deadline_warning_seconds") or 0))
        return _utc(base)
    return None


async def _persist_next_evaluations(
    updates: list[tuple[str, str, datetime.datetime | None]],
) -> None:
    """Write ``schedules.next_fire_at`` for every evaluated schedule in ONE
    statement (TRG-15: it was one transaction per schedule), through the
    maintenance (BYPASSRLS) session with an explicit (id, tenant_id) match."""
    rows = [(sid, tid, nxt) for tid, sid, nxt in updates if tid and sid]
    if not rows:
        return
    try:
        from sqlalchemy import text

        from app.db.rls import system_session
        from app.db.session import get_system_session_factory

        db_factory = get_system_session_factory()
        async with db_factory() as session, session.begin(), system_session(session):
            await session.execute(
                text(
                    "UPDATE schedules AS s SET next_fire_at = v.nxt "
                    "FROM unnest(CAST(:ids AS varchar[]), CAST(:tids AS varchar[]), "
                    "CAST(:nxts AS timestamptz[])) AS v(id, tid, nxt) "
                    "WHERE s.id = v.id AND s.tenant_id = v.tid"
                ),
                {
                    "ids": [r[0] for r in rows],
                    "tids": [r[1] for r in rows],
                    "nxts": [_aware_utc(r[2]) for r in rows],
                },
            )
    except Exception as exc:
        # Harmless: a claimed row whose next time is not written becomes due
        # again when its lease expires (_CLAIM_LEASE_SECONDS).
        logger.warning("next_fire_at_update_failed: %s", exc)


def _aware_utc(dt: datetime.datetime | None) -> datetime.datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=datetime.UTC) if dt.tzinfo is None else dt.astimezone(datetime.UTC)


async def _load_db_schedules(
    now: datetime.datetime | None = None,
    *,
    limit: int | None = None,
) -> dict[str, dict[str, Any]] | None:
    """Claim the due beat schedules of EVERY tenant in one statement (TRG-15).

    This used to list every active tenant and run one RLS query per tenant on
    every tick - O(tenants) round trips a minute. Now a single cross-tenant
    ``UPDATE ... FROM (SELECT ... ORDER BY next_fire_at LIMIT n FOR UPDATE SKIP
    LOCKED) RETURNING`` on the maintenance (BYPASSRLS) session picks the due
    rows (partial index ``ix_schedules_due_global``) and leases them by pushing
    ``next_fire_at`` ``_CLAIM_LEASE_SECONDS`` ahead, so a concurrent claimer
    skips them and a crashed beat's rows come back after the lease; the beat
    then writes each row's real next time in one batched UPDATE. Rows beyond
    ``limit`` are simply next tick's (oldest due first). ``None`` when the DB
    is unreachable (the caller falls back to the Redis mirror).
    """
    due_at = now or datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    due_at = due_at.replace(tzinfo=datetime.UTC) if due_at.tzinfo is None else due_at
    lease_until = due_at + datetime.timedelta(seconds=_CLAIM_LEASE_SECONDS)
    try:
        from sqlalchemy import text

        from app.db.rls import system_session
        from app.db.session import get_system_session_factory

        db_factory = get_system_session_factory()
        schedules: dict[str, dict[str, Any]] = {}
        async with db_factory() as session, session.begin(), system_session(session):
            result = await session.execute(
                text(
                    "UPDATE schedules AS s SET next_fire_at = :lease "
                    "FROM (SELECT d.id FROM schedules d "
                    "JOIN tenants t ON t.id = d.tenant_id AND t.is_active "
                    "WHERE NOT d.paused AND d.trigger_type = ANY(CAST(:types AS varchar[])) "
                    "AND (d.next_fire_at IS NULL OR d.next_fire_at <= :due) "
                    "ORDER BY d.next_fire_at ASC NULLS FIRST LIMIT :lim "
                    "FOR UPDATE OF d SKIP LOCKED) AS due "
                    "WHERE s.id = due.id RETURNING s.*"
                ),
                {
                    "lease": lease_until,
                    "types": list(_BEAT_DISCOVERY_TYPES),
                    "due": due_at,
                    "lim": int(limit or _due_batch_size()),
                },
            )
            rows = result.mappings().all()
        for row in rows:
            payload = _db_schedule_payload(_RowAttrs(row))
            schedule_id = str(payload.get("schedule_id") or "")
            row_tenant_id = str(payload.get("tenant_id") or "")
            if not schedule_id or not row_tenant_id or payload.get("paused"):
                continue
            schedules[_schedule_key(row_tenant_id, schedule_id)] = payload
        return schedules
    except Exception as exc:
        logger.warning("DB schedule discovery failed: %s", exc)
        return None


class _RowAttrs:
    """Attribute view of a RETURNING row for :func:`_db_schedule_payload`."""

    def __init__(self, mapping: Any) -> None:
        self._m = mapping

    def __getattr__(self, name: str) -> Any:
        return self._m.get(name)


def _due_batch_size() -> int:
    import os

    try:
        return max(1, int(os.getenv("BEAT_DUE_BATCH", "") or _DUE_BATCH))
    except ValueError:
        return _DUE_BATCH


async def _update_db_schedule_last_fired_at(
    tenant_id: str,
    schedule_id: str,
    fired_at: datetime.datetime,
) -> None:
    try:
        from sqlalchemy import update

        from app.db.models.scheduling import Schedule
        from app.db.rls import sqlalchemy_rls_context
        from app.db.session import get_session_factory as _get_fresh_db

        db_factory = _get_fresh_db()
        async with db_factory() as session:
            async with sqlalchemy_rls_context(session, tenant_id):
                await session.execute(
                    update(Schedule)
                    .where(Schedule.tenant_id == tenant_id, Schedule.id == schedule_id)
                    .values(last_fired_at=fired_at)
                )
            commit = getattr(session, "commit", None)
            if commit is not None:
                await commit()
    except Exception as exc:
        logger.warning(
            "DB schedule last_fired_at update failed for %s/%s: %s",
            tenant_id,
            schedule_id,
            exc,
        )
        raise


async def _pause_db_schedule(tenant_id: str, schedule_id: str) -> None:
    """Durably pause an expired schedule (TRG-09); failures are logged, the
    beat still skips the schedule because it re-checks expiry every tick."""
    try:
        from sqlalchemy import update

        from app.db.models.scheduling import Schedule
        from app.db.rls import sqlalchemy_rls_context
        from app.db.session import get_session_factory as _get_fresh_db

        db_factory = _get_fresh_db()
        async with db_factory() as session:
            async with sqlalchemy_rls_context(session, tenant_id):
                await session.execute(
                    update(Schedule)
                    .where(Schedule.tenant_id == tenant_id, Schedule.id == schedule_id)
                    .values(paused=True)
                )
            commit = getattr(session, "commit", None)
            if commit is not None:
                await commit()
    except Exception as exc:
        logger.warning(
            "expired_schedule_pause_failed",
            tenant_id=tenant_id,
            schedule_id=schedule_id,
            error=str(exc)[:200],
        )


def _db_schedule_discovery_enabled() -> bool:
    import os

    # TRG-15: on by default (Postgres is the source of truth; every deployment
    # config already set it). "false" keeps the Redis-mirror-only mode.
    return os.getenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "true").lower() in {
        "1",
        "true",
        "yes",
    }


def _record_schedule_fire_metric(status: str) -> None:
    try:
        from app.observability.metrics import record_schedule_fire

        record_schedule_fire(status)
    except Exception as exc:
        logger.warning("Schedule fire metric recording skipped: %s", exc)


@celery_app.task(name="app.scaling.tasks.record_queue_depths", bind=True, max_retries=3)  # type: ignore[untyped-decorator]
@beat_task_guard(lock_ttl_seconds=120)
def record_queue_depths(self: Any) -> dict[str, Any]:
    """Record Celery Redis queue depths for autoscaling and dashboards."""
    import math
    import os

    redis_url = os.getenv("REDIS_URL", "")
    if not redis_url:
        logger.info("record_queue_depths: no REDIS_URL configured")
        return {"status": "skipped", "queues_recorded": 0, "depths": {}}

    try:
        import redis as sync_redis

        from app.observability.metrics import record_queue_depth
        from app.scaling.celery_app import PLAN_QUEUE_MAP

        redis_from_url = cast(Any, sync_redis.from_url)
        r = redis_from_url(redis_url, decode_responses=True)
        depths: dict[str, int] = {}
        for queue in _CELERY_QUEUE_NAMES:
            depth = int(r.llen(queue))
            depths[queue] = depth
            record_queue_depth(queue, float(depth))

        # Per-plan autoscale signal: compute desired worker count per plan queue
        tasks_per_worker = int(os.getenv("TASKS_PER_WORKER", "4"))
        plan_depths: dict[str, int] = {}
        for plan, queue_name in PLAN_QUEUE_MAP.items():
            plan_depth = int(r.llen(queue_name))
            plan_depths[plan] = plan_depth
            depths[queue_name] = plan_depth
        for plan, depth in plan_depths.items():
            desired = max(1, math.ceil(depth / tasks_per_worker))
            try:
                from app.observability.metrics import record_desired_workers

                record_desired_workers(plan=plan, count=desired)
            except Exception:
                pass

        return {
            "status": "ok",
            "queues_recorded": len(depths),
            "depths": depths,
        }
    except Exception as exc:
        logger.warning("Queue depth recording failed: %s", exc)
        raise self.retry(exc=exc, countdown=2**self.request.retries) from exc


def _classify_health(status_code: int) -> str:
    """HTTP status → connector health. Any response used to count as 'ok' (even 5xx)."""
    from app.mcp.health_sweep import classify_health

    return classify_health(status_code)


async def _persist_health_snapshots(snapshots: list[dict[str, Any]]) -> int:
    """Write one ``connector_health_snapshots`` row per check, per tenant under RLS.

    Nothing wrote this table before, so ``GET /connectors/{id}/health`` history
    was always empty. Returns the number of rows written (0 when no DB).
    """
    if not snapshots:
        return 0
    try:
        from app.db.models.mcp import ConnectorHealthSnapshot
        from app.db.rls import sqlalchemy_rls_context
        from app.db.session import get_session_factory

        factory = get_session_factory()
    except Exception as exc:
        logger.warning("mcp_health_snapshot_db_unavailable: %s", exc)
        return 0
    by_tenant: dict[str, list[dict[str, Any]]] = {}
    for snap in snapshots:
        by_tenant.setdefault(str(snap["tenant_id"]), []).append(snap)
    written = 0
    for tenant_id, rows in by_tenant.items():
        try:
            async with (
                factory() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                for row in rows:
                    session.add(
                        ConnectorHealthSnapshot(
                            server_id=str(row["server_id"])[:64],
                            tenant_id=tenant_id,
                            status=str(row["status"])[:20],
                            latency_ms=row.get("latency_ms"),
                            error=row.get("error"),
                        )
                    )
            written += len(rows)
        except Exception as exc:
            logger.warning("mcp_health_snapshot_write_failed tenant=%s: %s", tenant_id, exc)
    return written


@celery_app.task(name="app.scaling.tasks.check_mcp_health")  # type: ignore[untyped-decorator]
def check_mcp_health() -> dict[str, Any]:
    """Periodic connector health sweep over the durable registry (a02-F034-N1).

    Connectors are read from Postgres ``mcp_servers`` (keyset pages, maintenance
    session) — the legacy ``mcp:servers:*`` Redis keys this used to scan are no
    longer written since MCPREG-01. Probes run with bounded concurrency and
    timeouts inside a budget shorter than the beat interval; a shared Redis
    cursor lets consecutive runs continue where the last stopped and a Redis
    lock keeps runs from overlapping. See ``app.mcp.health_sweep``.
    """

    async def _run() -> dict[str, Any]:
        from app.db.session import get_system_session_factory
        from app.mcp import health_sweep

        redis_url = os.getenv("REDIS_URL", "")
        r: Any = None
        if redis_url:
            import redis.asyncio as aioredis

            r = aioredis.from_url(redis_url, decode_responses=True)
        try:
            return await health_sweep.run_health_sweep(
                factory=get_system_session_factory(),
                redis=r,
                persist=_persist_health_snapshots,
                probe=health_sweep.probe_connector,
                fetch=health_sweep.fetch_connector_page,
            )
        finally:
            if r is not None:
                await r.aclose()

    checked_at = datetime.datetime.now(datetime.UTC).isoformat()
    try:
        result = _run_async(_run())
    except Exception as exc:
        # Honest failure: the registry could not be read (no DB, RLS role
        # misconfigured, Redis down) — never "ok" with zero servers.
        logger.error("mcp_health_sweep_failed error=%s", exc)
        return {
            "status": "error",
            "reason": str(exc)[:200],
            "checked_at": checked_at,
            "servers_checked": 0,
            "results": [],
        }

    return {
        "status": result.get("status", "ok"),
        "checked_at": checked_at,
        "servers_checked": result.get("servers_checked", 0),
        "snapshots_persisted": result.get("snapshots_persisted", 0),
        "completed_pass": result.get("completed_pass", False),
        "results": result.get("results", []),
    }


# Backward-compatible alias (tests reference this name)
health_check_mcp = check_mcp_health


@celery_app.task(name="agentverse.maintenance.prune_connector_health_snapshots")  # type: ignore[untyped-decorator]
def prune_connector_health_snapshots() -> dict[str, Any]:
    """Delete connector health snapshots past retention (HEALTH-06), in batches.

    Cross-tenant, so it runs on the BYPASSRLS maintenance factory; a failure
    raises so Celery records it instead of reporting zero rows pruned.
    """

    async def _run() -> dict[str, Any]:
        from app.db.session import get_system_session_factory
        from app.mcp.health_sweep import prune_health_snapshots, snapshot_retention_days

        days = snapshot_retention_days()
        pruned = await prune_health_snapshots(
            get_system_session_factory(), retention_days=days
        )
        return {"pruned_count": pruned, "retention_days": days}

    result: dict[str, Any] = _run_async(_run())
    return result


@celery_app.task(name="agentverse.maintenance.prune_user_sessions")  # type: ignore[untyped-decorator]
def prune_user_sessions() -> dict[str, Any]:
    """Delete SSO user sessions expired for over a week (SAML-01), in batches.

    Cross-tenant, so it runs on the BYPASSRLS maintenance factory; a failure
    raises so Celery records it instead of reporting zero rows pruned.
    """

    async def _run() -> dict[str, Any]:
        from app.auth import user_sessions
        from app.db import session as db_session

        pruned = await user_sessions.prune_user_sessions(
            db_session.get_system_session_factory()
        )
        return {"pruned_count": pruned}

    result: dict[str, Any] = _run_async(_run())
    return result


def _run_poll_trigger(
    key: str, sched: dict[str, Any], r: Any, now: datetime.datetime
) -> int:
    """Fetch one polling trigger and fire its governed goals (TRG-54).

    Runs in the ``poll_trigger`` worker task, never in the beat. Returns the
    number of goals dispatched. Dedup state lives in Redis (``r``).
    """
    fired = 0
    trigger_type = str(sched.get("trigger_type") or "")
    # ── RSS_FEED trigger ──────────────────────────────────────────
    if trigger_type == "rss_feed":
        # Poll the configured feed, dispatch one goal per *new* entry
        # (deduped by entry id in Redis), capped at 5 per cycle.
        try:
            import json as _json_rss

            from app.triggers.rss import fetch_rss_entries, new_entries

            rss_url = sched.get("rss_url", "")
            _tenant_id_rss = str(sched.get("tenant_id") or "")
            if rss_url and _tenant_id_rss:
                processed_key = f"processed_rss:{key}"
                processed_rss: set[str] = set()
                if r is not None:
                    _rss_raw = r.get(processed_key)
                    if _rss_raw:
                        processed_rss = set(_json_rss.loads(_rss_raw))
                try:
                    entries = fetch_rss_entries(rss_url)
                except Exception as _rss_fetch_exc:
                    logger.warning(
                        "rss_fetch_error url=%s error=%s",
                        rss_url,
                        str(_rss_fetch_exc)[:100],
                    )
                    entries = []

                fresh_entries = new_entries(entries, processed_rss)
                from app.tenancy.context import (
                    PlanTier as _PT_rss,
                )
                from app.tenancy.context import (
                    TenantContext as _TC_rss,
                )

                _tenant_ctx_rss = _TC_rss(
                    tenant_id=_tenant_id_rss,
                    plan=_PT_rss.PROFESSIONAL,
                    api_key_id="trigger-rss",
                )
                for _entry in fresh_entries[:5]:
                    _rss_alert = {
                        "entry_id": _entry.entry_id,
                        "title": _entry.title,
                        "link": _entry.link,
                        "rss_url": rss_url,
                    }
                    _rss_kw = _run_async(
                        _build_goal_kwargs_for_alert(
                            sched,
                            "rss_feed",
                            _rss_alert,
                            goal_service=None,
                            tenant_ctx=_tenant_ctx_rss,
                        )
                    )
                    # Governed dispatch (was a direct run_goal).
                    if _rss_kw and _dispatch_beat_event_fire(
                        key,
                        sched,
                        goal_text=str(_rss_kw["goal"]),
                        fire_instance_id=f"rss:{_entry.entry_id}",
                        event_payload=_rss_alert,
                    ):
                        fired += 1

                if entries and r is not None:
                    _all_rss = list(
                        processed_rss | {e.entry_id for e in entries}
                    )
                    r.set(
                        processed_key,
                        _json_rss.dumps(_all_rss[-1000:]),
                        ex=604800,
                    )
                if fresh_entries:
                    logger.info(
                        "rss_trigger_fired",
                        new_entries=len(fresh_entries),
                        schedule_id=sched.get("schedule_id", key),
                    )
        except Exception as _rss_exc:
            logger.warning(
                "rss_trigger_error",
                error=str(_rss_exc)[:100],
                schedule_id=sched.get("schedule_id", key),
            )

    # ── API_POLL trigger ──────────────────────────────────────────
    elif trigger_type == "api_poll":
        # Poll a JSON endpoint; fire when the extracted value changes
        # (and, if set, matches poll_expected_value). Deduped by the
        # last-seen value in Redis.
        try:
            from app.triggers.polling import (
                extract_path,
                fetch_json,
                poll_should_fire,
            )

            poll_url = sched.get("poll_url", "")
            _tenant_id_ap = str(sched.get("tenant_id") or "")
            # The poll slot (TRG-33 interval) was claimed by the beat.
            if poll_url and _tenant_id_ap:
                last_key = f"api_poll_last:{key}"
                last_value: Any = None
                if r is not None:
                    _lv = r.get(last_key)
                    if _lv is not None:
                        last_value = _lv.decode() if isinstance(_lv, bytes) else _lv
                try:
                    data = fetch_json(
                        poll_url, method=sched.get("poll_method", "GET")
                    )
                except Exception as _ap_fetch_exc:
                    logger.warning(
                        "api_poll_fetch_error url=%s error=%s",
                        poll_url,
                        str(_ap_fetch_exc)[:100],
                    )
                    data = None

                if data is not None:
                    current = extract_path(data, sched.get("poll_jsonpath", ""))
                    if poll_should_fire(
                        current,
                        last_value,
                        sched.get("poll_expected_value", ""),
                    ):
                        # Only the goal text is used; the governed
                        # dispatch resolves the real plan (TRG-06).
                        _ap_alert = {
                            "poll_url": poll_url,
                            "value": current,
                            "jsonpath": sched.get("poll_jsonpath", ""),
                        }
                        _ap_kw = _run_async(
                            _build_goal_kwargs_for_alert(
                                sched,
                                "api_poll",
                                _ap_alert,
                                goal_service=None,
                                tenant_ctx=None,
                            )
                        )
                        # Governed dispatch (was a direct run_goal).
                        if _ap_kw and _dispatch_beat_event_fire(
                            key,
                            sched,
                            goal_text=str(_ap_kw["goal"]),
                            fire_instance_id=f"apipoll:{current}",
                            event_payload=_ap_alert,
                        ):
                            fired += 1
                            logger.info(
                                "api_poll_trigger_fired",
                                schedule_id=sched.get("schedule_id", key),
                            )
                    if r is not None and current is not None:
                        r.set(last_key, str(current), ex=604800)
        except Exception as _ap_err:
            logger.warning(
                "api_poll_trigger_error",
                error=str(_ap_err)[:100],
                schedule_id=sched.get("schedule_id", key),
            )

    # ── DB_ROW_CHANGE trigger ─────────────────────────────────────
    elif trigger_type == "db_row_change":
        # Poll an allowlisted table's tenant row count; fire when it
        # grows. Fail-closed: a table not in the allowlist never runs.
        try:
            table = str(sched.get("db_table", "") or "")
            _tenant_id_db = str(sched.get("tenant_id") or "")
            allow = _db_row_change_allowlist()
            if _tenant_id_db and _safe_db_table(table, allow):
                count_key = f"db_row_count:{key}"
                last_count: int | None = None
                if r is not None:
                    _lc = r.get(count_key)
                    if _lc is not None:
                        try:
                            last_count = int(_lc)
                        except (TypeError, ValueError):
                            last_count = None
                current = _run_async(
                    _count_tenant_rows(table, _tenant_id_db, allow)
                )
                if current is not None:
                    if _row_change_fires(current, last_count):
                        from app.tenancy.context import (
                            PlanTier as _PT_db,
                        )
                        from app.tenancy.context import (
                            TenantContext as _TC_db,
                        )

                        _tc_db = _TC_db(
                            tenant_id=_tenant_id_db,
                            plan=_PT_db.PROFESSIONAL,
                            api_key_id="trigger-db-row-change",
                        )
                        _db_alert = {
                            "db_table": table,
                            "row_count": current,
                            "previous_count": last_count,
                        }
                        _db_kw = _run_async(
                            _build_goal_kwargs_for_alert(
                                sched,
                                "db_row_change",
                                _db_alert,
                                goal_service=None,
                                tenant_ctx=_tc_db,
                            )
                        )
                        # Governed dispatch (was a direct run_goal).
                        if _db_kw and _dispatch_beat_event_fire(
                            key,
                            sched,
                            goal_text=str(_db_kw["goal"]),
                            fire_instance_id=f"dbrow:{current}",
                            event_payload=_db_alert,
                        ):
                            fired += 1
                            logger.info(
                                "db_row_change_trigger_fired",
                                schedule_id=sched.get("schedule_id", key),
                            )
                    if r is not None:
                        r.set(count_key, str(current), ex=604800)
        except Exception as _db_err:
            logger.warning(
                "db_row_change_error",
                error=str(_db_err)[:100],
                schedule_id=sched.get("schedule_id", key),
            )

    return fired


_POLL_TRIGGER_TYPES: tuple[str, ...] = ("rss_feed", "api_poll", "db_row_change")
# Seconds between polls when the trigger has no interval of its own.
_DEFAULT_POLL_INTERVAL_SECONDS = 60


def _poll_interval_seconds(sched: dict[str, Any]) -> int:
    """How often a polling trigger is fetched (beat cadence floor of 60s).

    Only api_poll has its own interval (TRG-33); rss_feed / db_row_change poll at
    the beat cadence."""
    if str(sched.get("trigger_type") or "") == "api_poll":
        return max(int(sched.get("poll_interval_seconds") or 0), _DEFAULT_POLL_INTERVAL_SECONDS)
    return _DEFAULT_POLL_INTERVAL_SECONDS


def _poll_claim_key(key: str, sched: dict[str, Any]) -> str:
    # api_poll keeps its TRG-33 key so an in-flight claim survives the deploy.
    if str(sched.get("trigger_type") or "") == "api_poll":
        return f"api_poll_claim:{key}"
    return f"poll_claim:{key}"


def _enqueue_poll_trigger(
    key: str, sched: dict[str, Any], r: Any, now: datetime.datetime
) -> bool:
    """Claim this trigger's poll slot and enqueue its poll task (TRG-54).

    ``SET NX EX`` (interval - 5s) is an atomic per-trigger claim shared by every
    beat replica, so a due poll is enqueued once per interval. Without Redis
    every tick enqueues (the poll task's own dedup state still applies). A
    failed enqueue releases the claim so the next tick retries.
    """
    if not str(sched.get("tenant_id") or ""):
        return False
    claim = _poll_claim_key(key, sched)
    if r is not None:
        try:
            if not r.set(
                claim, now.isoformat(), nx=True, ex=max(_poll_interval_seconds(sched) - 5, 1)
            ):
                return False
        except Exception as exc:
            logger.warning("poll_claim_failed", schedule=key, error=str(exc)[:100])
    try:
        poll_trigger.apply_async(kwargs={"key": key, "sched": sched}, queue="triggers.poll")
    except Exception as exc:
        logger.warning("poll_enqueue_failed", schedule=key, error=str(exc)[:120])
        if r is not None:
            with contextlib.suppress(Exception):
                r.delete(claim)
        return False
    return True


@celery_app.task(name="app.scaling.tasks.poll_trigger", bind=True, max_retries=0)  # type: ignore[untyped-decorator]
def poll_trigger(self: Any, key: str, sched: dict[str, Any]) -> dict[str, Any]:
    """Poll ONE rss_feed / api_poll / db_row_change trigger (TRG-54).

    Enqueued by ``fire_due_schedules`` on the dedicated ``triggers.poll`` queue,
    so fetches run in parallel on workers instead of serially inside the beat.
    """
    import os

    r: Any | None = None
    redis_url = os.getenv("REDIS_URL", "")
    if redis_url:
        try:
            import redis as sync_redis

            r = cast(Any, sync_redis.from_url)(redis_url, decode_responses=True)
        except Exception as exc:
            logger.warning("poll_trigger_redis_unavailable: %s", exc)
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    fired = _run_poll_trigger(key, sched, r, now)
    return {"status": "ok", "schedule": key, "fired": fired}


@celery_app.task(name="app.scaling.tasks.fire_due_schedules", bind=True, max_retries=3)  # type: ignore[untyped-decorator]
@beat_task_guard(lock_ttl_seconds=300)
def fire_due_schedules(self: Any) -> dict[str, Any]:
    """Fire all cron/interval schedules that are due within the current minute."""
    import json
    import os

    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    fired = 0
    polls_enqueued = 0

    try:
        redis_url = os.getenv("REDIS_URL", "")
        r: Any | None = None
        schedules: dict[str, dict[str, Any]] = {}
        db_schedule_keys: set[str] = set()
        # TRG-15: Postgres is the source of truth. Only DUE schedules are loaded
        # (indexed next_fire_at predicate); the Redis SCAN of every schedule:*
        # key is only a fallback when DB discovery is off or fails, so an
        # evicted Redis key no longer stops a schedule from firing.
        db_discovered: dict[str, dict[str, Any]] | None = None
        if _db_schedule_discovery_enabled():
            db_discovered = cast(
                "dict[str, dict[str, Any]] | None", _run_async(_load_db_schedules(now))
            )
            if db_discovered is None:
                logger.warning("fire_due_schedules: DB discovery failed; using the Redis mirror")
        if redis_url:
            try:
                import redis as sync_redis

                redis_from_url = cast(Any, sync_redis.from_url)
                r = redis_from_url(redis_url, decode_responses=True)

                # Scan for all schedule keys written by ScheduleStore: schedule:{tenant}:{id}
                schedule_keys = (
                    list(r.scan_iter(match="schedule:*", count=100))
                    if db_discovered is None
                    else []
                )
                for key in schedule_keys:
                    try:
                        raw = r.get(key)
                        if raw is None:
                            continue
                        raw_payload = cast(dict[str, Any], json.loads(raw))
                        sanitized_payload = _strip_secret_redis_schedule_fields(raw_payload)
                        if sanitized_payload != raw_payload:
                            r.set(key, json.dumps(sanitized_payload))
                        schedules[str(key)] = sanitized_payload
                    except Exception as exc:
                        logger.warning("Error loading schedule key %s: %s", key, exc)
            except Exception as exc:
                logger.warning("Redis schedule discovery skipped: %s", exc)
        else:
            logger.info("fire_due_schedules: no REDIS_URL configured, checking DB schedules only")

        if db_discovered is not None:
            for key, sched in db_discovered.items():
                db_schedule_keys.add(key)
                schedules[key] = sched
        elif not _db_schedule_discovery_enabled():
            logger.info("fire_due_schedules: DB schedule discovery disabled")

        def mark_schedule_fired(
            key: str,
            sched: dict[str, Any],
            *,
            tenant_id: str,
            fired_at: datetime.datetime,
        ) -> None:
            sched["last_fired_at"] = fired_at.isoformat()
            if key in db_schedule_keys:
                schedule_id = str(sched.get("schedule_id") or "")
                if schedule_id:
                    _run_async(
                        _update_db_schedule_last_fired_at(
                            tenant_id,
                            schedule_id,
                            fired_at,
                        )
                    )
            elif r is not None:
                r.set(key, json.dumps(_strip_secret_redis_schedule_fields(sched)))

        def pause_expired_schedule(key: str, sched: dict[str, Any]) -> None:
            sched["paused"] = True
            tenant_id = str(sched.get("tenant_id") or "")
            schedule_id = str(sched.get("schedule_id") or "")
            if key in db_schedule_keys and tenant_id and schedule_id:
                _run_async(_pause_db_schedule(tenant_id, schedule_id))
            if r is not None:
                # xx: only rewrite a Redis-backed schedule, never create one.
                r.set(key, json.dumps(_strip_secret_redis_schedule_fields(sched)), xx=True)
            logger.info("schedule_expired_auto_paused", schedule=key)

        def advance_and_dispatch_schedule(
            key: str,
            sched: dict[str, Any],
            *,
            fired_at: datetime.datetime,
            fire_instance_id: str,
        ) -> dict[str, Any] | None:
            goal_kwargs = _scheduled_goal_kwargs(
                key,
                sched,
                fire_instance_id=fire_instance_id,
            )
            if goal_kwargs is None:
                return None
            try:
                _dispatch_due_schedule(
                    key,
                    sched,
                    via_scheduled_task=key in db_schedule_keys,
                    fire_instance_id=fire_instance_id,
                )
                mark_schedule_fired(
                    key,
                    sched,
                    tenant_id=str(goal_kwargs["tenant_id"]),
                    fired_at=fired_at,
                )
            except Exception:
                _record_schedule_fire_metric("error")
                raise
            _record_schedule_fire_metric("success")
            return goal_kwargs

        def dispatch_missed_slots(
            key: str,
            sched: dict[str, Any],
            slots: list[datetime.datetime],
            *,
            kind: str,
        ) -> int:
            """Fire each missed slot (oldest first), or just the last one when the
            schedule opts into ``coalesce_missed_runs``. Returns the number fired."""
            if not slots:
                return 0
            if sched.get("coalesce_missed_runs"):
                slots = [slots[-1]]
            count = 0
            for slot in slots:
                goal_kwargs = advance_and_dispatch_schedule(
                    key,
                    sched,
                    fired_at=slot,
                    fire_instance_id=slot.isoformat(),
                )
                if goal_kwargs is not None:
                    count += 1
                    logger.info(
                        "Fired %s schedule %s for tenant %s (slot %s)",
                        kind,
                        key,
                        goal_kwargs["tenant_id"],
                        slot.isoformat(),
                    )
            return count

        logger.info("fire_due_schedules: checking %d schedule keys", len(schedules))

        for key, sched in schedules.items():
            try:
                if sched.get("paused"):
                    continue

                # TRG-09: an expired trigger stops firing and is auto-paused so
                # the UI/API show it as no longer active.
                from app.triggers.validation import is_trigger_expired

                if is_trigger_expired(sched.get("expires_at_iso"), now=now):
                    pause_expired_schedule(key, sched)
                    continue

                trigger_type = sched.get("trigger_type", "")

                # ── CRON schedules ────────────────────────────────────────────
                if trigger_type == "cron":
                    cron_expr = sched.get("cron_expression", "")
                    if cron_expr:
                        tz_name = sched.get("timezone") or "UTC"
                        last_fired_dt = _schedule_datetime(sched.get("last_fired_at"))
                        try:
                            missed = _cron_missed_runs_utc(cron_expr, last_fired_dt, now, tz_name)
                        except Exception as cron_exc:
                            logger.warning("Cron parse error for %s: %s", key, cron_exc)
                            continue
                        fired += dispatch_missed_slots(key, sched, missed, kind="cron")

                # ── INTERVAL schedules ────────────────────────────────────────
                elif trigger_type == "interval":
                    interval_s: int = sched.get("interval_seconds", 0)
                    if interval_s > 0:
                        last_fired = sched.get("last_fired_at")
                        last_dt = _schedule_datetime(last_fired) if last_fired is not None else None
                        slot = _interval_due_slot_utc(interval_s, last_dt, now)

                        if slot is not None:
                            goal_kwargs = advance_and_dispatch_schedule(
                                key,
                                sched,
                                fired_at=slot,
                                fire_instance_id=slot.isoformat(),
                            )
                            if goal_kwargs is not None:
                                fired += 1
                                logger.info(
                                    "Fired interval schedule %s for tenant %s",
                                    key,
                                    goal_kwargs["tenant_id"],
                                )

                # ── ONCE schedules ────────────────────────────────────────────
                elif trigger_type == "once":
                    fire_at = _schedule_datetime(sched.get("fire_at_iso"))
                    last_fired = sched.get("last_fired_at")
                    if fire_at and last_fired is None:
                        # Compare as UTC-naive to avoid tz issues
                        now_ts = now.replace(tzinfo=None) if now.tzinfo else now
                        fire_at_ts = fire_at.replace(tzinfo=None) if fire_at.tzinfo else fire_at
                        if now_ts >= fire_at_ts:
                            goal_kwargs = advance_and_dispatch_schedule(
                                key, sched, fired_at=fire_at, fire_instance_id=fire_at.isoformat()
                            )
                            if goal_kwargs is not None:
                                fired += 1
                                logger.info("Fired once schedule %s", key)

                # ── RELATIVE_DELAY (fire once at base + offset) ────────────────
                elif trigger_type == "relative_delay":
                    last_dt = _schedule_datetime(sched.get("last_fired_at"))
                    due_at = _relative_delay_due_utc(
                        sched.get("fire_at_iso", ""),
                        int(sched.get("relative_offset_seconds", 0) or 0),
                        now,
                        last_dt,
                    )
                    if due_at is not None:
                        goal_kwargs = advance_and_dispatch_schedule(
                            key, sched, fired_at=due_at, fire_instance_id=due_at.isoformat()
                        )
                        if goal_kwargs is not None:
                            fired += 1
                            logger.info("Fired relative_delay schedule %s", key)

                # ── DEADLINE (fire once, warning_seconds before deadline) ──────
                elif trigger_type == "deadline":
                    last_dt = _schedule_datetime(sched.get("last_fired_at"))
                    due_at = _deadline_due_utc(
                        sched.get("fire_at_iso", ""),
                        int(sched.get("deadline_warning_seconds", 0) or 0),
                        now,
                        last_dt,
                    )
                    if due_at is not None:
                        goal_kwargs = advance_and_dispatch_schedule(
                            key, sched, fired_at=due_at, fire_instance_id=due_at.isoformat()
                        )
                        if goal_kwargs is not None:
                            fired += 1
                            logger.info("Fired deadline schedule %s", key)

                # ── BUSINESS_CALENDAR (cron, business hours only) ─────────────
                elif trigger_type == "business_calendar":
                    cron_expr = sched.get("cron_expression", "")
                    if cron_expr:
                        tz_name = sched.get("timezone") or "UTC"
                        last_dt = _schedule_datetime(sched.get("last_fired_at"))
                        try:
                            slots = _business_calendar_slots(cron_expr, last_dt, now, tz_name)
                        except Exception as bc_exc:
                            logger.warning("business_calendar parse error %s: %s", key, bc_exc)
                            continue
                        fired += dispatch_missed_slots(key, sched, slots, kind="business_calendar")

                # ── FILE_DROP trigger ─────────────────────────────────────────
                elif trigger_type == "file_drop":
                    # FILE_DROP: scan a configured watch path for new files and
                    # submit one goal per new file (capped at 5 per cycle).
                    try:
                        import fnmatch as _fnmatch
                        import json as _json_fd
                        import os as _os_fd

                        watch_path = sched.get("file_watch_path") or sched.get("watch_path", "")
                        watch_pattern = sched.get("file_pattern", "*")
                        processed_key = f"processed_files:{key}"
                        processed: set[str] = set()
                        new_files: list[str] = []

                        # TRG-32: the tenant-supplied path is confined to
                        # FILE_DROP_ROOT/<tenant_id> (realpath, so symlinks and
                        # paths stored before validation cannot escape it); it
                        # used to be listed as-is (/etc, other tenants' folders).
                        from app.triggers.validation import (
                            is_within as _is_within_fd,
                        )
                        from app.triggers.validation import (
                            resolve_file_drop_dir as _resolve_fd,
                        )

                        _watch_dir = (
                            _resolve_fd(str(sched.get("tenant_id") or ""), str(watch_path))
                            if watch_path
                            else None
                        )
                        if watch_path and _watch_dir is None:
                            logger.warning(
                                "file_drop_path_refused", schedule=key, path=str(watch_path)[:200]
                            )
                        if _watch_dir is not None:
                            try:
                                if _os_fd.path.isdir(_watch_dir):
                                    all_files = _os_fd.listdir(_watch_dir)
                                    if r is not None:
                                        _proc_raw = r.get(processed_key)
                                        if _proc_raw:
                                            processed = set(_json_fd.loads(_proc_raw))
                                    new_files = [
                                        _os_fd.path.join(_watch_dir, f)
                                        for f in all_files
                                        if _fnmatch.fnmatch(f, watch_pattern)
                                        and f not in processed
                                        and _is_within_fd(
                                            _os_fd.path.realpath(_os_fd.path.join(_watch_dir, f)),
                                            _watch_dir,
                                        )
                                    ]
                            except OSError as _os_err:
                                logger.warning(
                                    "file_drop_watch_path_error",
                                    path=watch_path,
                                    error=str(_os_err),
                                )

                        for _file_path in new_files[:5]:  # cap at 5 files per cycle
                            _file_name = _os_fd.path.basename(_file_path)
                            _tenant_id_fd = str(sched.get("tenant_id") or "")
                            if not _tenant_id_fd:
                                continue
                            # Only the goal text is taken from _alert_kw; the
                            # fire runs through the governed dispatch, which
                            # resolves the tenant's real plan (TRG-06 — this
                            # used to hard-code PROFESSIONAL).
                            _file_alert = {
                                "file_path": _file_path,
                                "file_name": _file_name,
                                "watch_path": watch_path,
                            }
                            _alert_kw = _run_async(
                                _build_goal_kwargs_for_alert(
                                    sched,
                                    "file_drop",
                                    _file_alert,
                                    goal_service=None,
                                    tenant_ctx=None,
                                )
                            )
                            if _alert_kw:
                                # Claim the file atomically BEFORE firing.
                                #
                                # The `processed_files` blob above is a
                                # read-modify-write (GET the JSON set, compute
                                # new_files, SET it back at the end of the
                                # cycle), so two beat ticks — or two replicas —
                                # both read the same set, both see the same new
                                # file, and both submit a goal for it. SET NX is
                                # atomic, so exactly one claimant proceeds.
                                #
                                # It also fixes two re-fire bugs in that blob:
                                # it is truncated to the last 500 names, so a
                                # busy watch path silently forgets older files
                                # and re-fires them; and it carries a 24h TTL,
                                # so a directory whose files are not removed
                                # re-fires in full every day.
                                _claim_fd = f"filedrop:claimed:{key}:{_file_name}"
                                if r is not None:
                                    try:
                                        if not r.set(
                                            _claim_fd, "1", nx=True, ex=7 * 86400
                                        ):
                                            continue  # another tick/replica has it
                                    except Exception as _claim_err:
                                        logger.warning(
                                            "file_drop_claim_failed",
                                            file=_file_name,
                                            error=str(_claim_err)[:120],
                                        )
                                # Keyed on the FILE, not on the evaluating
                                # process's wall clock. `now.isoformat()` differs
                                # between two racing evaluations, so the derived
                                # goal id differed too and nothing downstream
                                # could dedup them — the same recurring-key bug
                                # already fixed for interval schedules.
                                # Governed dispatch (was a direct run_goal).
                                if _dispatch_beat_event_fire(
                                    key,
                                    sched,
                                    goal_text=str(_alert_kw["goal"]),
                                    fire_instance_id=f"filedrop:{_file_name}",
                                    event_payload=_file_alert,
                                ):
                                    fired += 1

                        if new_files:
                            if r is not None:
                                _all_proc = list(
                                    processed | {_os_fd.path.basename(f) for f in new_files}
                                )
                                r.set(
                                    processed_key,
                                    _json_fd.dumps(_all_proc[-500:]),
                                    ex=86400,
                                )
                            logger.info(
                                "file_drop_trigger_fired",
                                files_found=len(new_files),
                                schedule_id=sched.get("schedule_id", key),
                            )
                        else:
                            logger.debug(
                                "file_drop_no_new_files",
                                watch_path=watch_path,
                                schedule_id=sched.get("schedule_id", key),
                            )
                    except Exception as _fd_exc:
                        logger.warning(
                            "file_drop_trigger_error",
                            error=str(_fd_exc)[:100],
                            schedule_id=sched.get("schedule_id", key),
                        )

                # ── Polling triggers (TRG-54): claimed here, fetched by a worker ─
                elif trigger_type in _POLL_TRIGGER_TYPES:
                    # The beat never does the (blocking, up to 10s) fetch: it
                    # claims the poll slot and enqueues one poll_trigger task on
                    # the triggers.poll queue, so slow feeds cannot starve other
                    # schedules or outlive the beat guard.
                    if _enqueue_poll_trigger(key, sched, r, now):
                        polls_enqueued += 1

                # ── External alert triggers (Alertmanager / Datadog / PagerDuty) ─
                elif trigger_type in ("alertmanager", "datadog", "pagerduty"):
                    # External alert triggers fire ONLY on a real alert payload
                    # cached by POST /webhooks/alerts/{type}, consumed exactly once.
                    #
                    # This used to synthesize a fake alert ({"status": "firing",
                    # "alertname": "PrometheusAlert", ...}) whenever no payload was
                    # cached — i.e. on EVERY 60s beat tick — so each alert trigger
                    # launched an autonomous "investigate and resolve" goal every
                    # minute for an incident that never happened. The GET + DELETE
                    # consume was also non-atomic, so two beat replicas could both
                    # read (and both fire) the same real alert.
                    try:
                        _tenant_id_alert = str(sched.get("tenant_id") or "")
                        if not _tenant_id_alert:
                            logger.warning(
                                "alert_trigger_missing_tenant_id",
                                trigger_type=trigger_type,
                                key=key,
                            )
                        else:
                            # Tenant-scoped (matches POST /webhooks/alerts/{type}).
                            alert_cache_key = (
                                f"alert_payload:{_tenant_id_alert}:{trigger_type}:"
                                f"{sched.get('schedule_id', key)}"
                            )
                            alert_data = _consume_alert_payload(r, alert_cache_key)
                            if alert_data is not None:
                                from app.tenancy.context import (
                                    PlanTier as _PT_alert,
                                )
                                from app.tenancy.context import (
                                    TenantContext as _TC_alert,
                                )

                                _tenant_ctx_alert = _TC_alert(
                                    tenant_id=_tenant_id_alert,
                                    plan=_PT_alert.PROFESSIONAL,
                                    api_key_id="trigger-alert",
                                )
                                _alert_kwargs = _run_async(
                                    _build_goal_kwargs_for_alert(
                                        sched,
                                        trigger_type=trigger_type,
                                        alert_context=alert_data,
                                        goal_service=None,
                                        tenant_ctx=_tenant_ctx_alert,
                                        default_priority="high",
                                    )
                                )
                                # Governed dispatch (was a direct run_goal). Each
                                # consumed payload is its own firing.
                                if _alert_kwargs and _dispatch_beat_event_fire(
                                    key,
                                    sched,
                                    goal_text=str(_alert_kwargs["goal"]),
                                    fire_instance_id=f"{trigger_type}:{uuid.uuid4().hex}",
                                    event_payload=alert_data,
                                ):
                                    fired += 1
                                    logger.info(
                                        "external_alert_trigger_fired",
                                        trigger_type=trigger_type,
                                        schedule_id=sched.get("schedule_id", key),
                                    )
                    except Exception as _alert_exc:
                        logger.warning(
                            "external_alert_trigger_error",
                            trigger_type=trigger_type,
                            error=str(_alert_exc)[:100],
                        )

            except Exception as exc:
                logger.warning("Error processing schedule key %s: %s", key, exc)
                continue

        # TRG-15: record when each DB schedule next needs evaluating, so the
        # next tick's indexed query skips it until then.
        _next_updates: list[tuple[str, str, datetime.datetime | None]] = []
        for key in db_schedule_keys:
            sched = schedules.get(key) or {}
            if sched.get("paused"):
                continue
            try:
                nxt = _next_evaluation_at(sched, now)
            except Exception as nxt_exc:
                logger.warning("next_evaluation_failed schedule=%s: %s", key, nxt_exc)
                nxt = None
            if _norm_utc_naive(nxt) != _schedule_datetime(sched.get("next_fire_at")):
                _next_updates.append(
                    (str(sched.get("tenant_id") or ""), str(sched.get("schedule_id") or ""), nxt)
                )
        if _next_updates:
            _run_async(_persist_next_evaluations(_next_updates))

        return {
            "status": "ok",
            "checked_at": now.isoformat(),
            "schedules_fired": fired,
            "schedules_checked": len(schedules),
            "polls_enqueued": polls_enqueued,
        }
    except Exception as exc:
        logger.error("fire_due_schedules failed: %s", exc)
        raise self.retry(exc=exc, countdown=2**self.request.retries) from exc


@celery_app.task(name="app.scaling.tasks.reap_stale_goal_runners", bind=True, max_retries=0)
def reap_stale_goal_runners(self: Any) -> dict[str, Any]:
    """GOAL-STALL: requeue / fail goals whose runner heartbeat went stale.

    Unlike ``detect_stuck_goals`` (plan goal timeout, 1-24 h) this catches a
    dead or wedged worker within ``goal_heartbeat_stale_seconds``. Errors are
    raised, so a reaper that cannot run is a FAILED task, never a quiet success.
    """
    result: dict[str, Any] = _run_async(_reap_stale_goal_runners())
    return result


async def _reap_stale_goal_runners() -> dict[str, Any]:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core.config import get_settings
    from app.scaling.goal_watchdog import reap_stale_goal_runners as _reap
    from app.services.goal_queue import CeleryGoalTaskQueue
    from app.services.goal_service import _subgoal_queue_kwargs

    settings = get_settings()
    # A dedicated engine, created and disposed on this loop: the module-level
    # engines handed connections across task loops ("attached to a different
    # loop"), which is how detect_stuck_goals intermittently did nothing.
    url = (settings.maintenance_database_url or "").strip() or str(settings.database_url)
    engine = create_async_engine(url, pool_size=1, max_overflow=1)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    redis_client = _get_sync_redis()
    queue = CeleryGoalTaskQueue()

    def _enqueue(goal: dict[str, Any]) -> None:
        queue.enqueue_goal(
            goal_id=goal["goal_id"],
            tenant_id=goal["tenant_id"],
            goal_text=goal["goal_text"],
            priority=goal["priority"],
            dry_run=goal["dry_run"],
            agent_id=goal["agent_id"] or None,
            connector_ids=[],
            workflow_mode=goal["workflow_mode"],
            goal_template="",
            plan=goal["plan"],
            **_subgoal_queue_kwargs(goal["execution_context"]),
        )

    async def _release_slot(
        tenant_id: str, goal_id: str, execution_context: dict[str, Any]
    ) -> None:
        from app.services.goal_service import _holds_concurrency_slot

        if _holds_concurrency_slot(execution_context):
            await _decrement_after_completion(tenant_id, REDIS_URL, goal_id)

    def _publish(tenant_id: str, goal_id: str, event: dict[str, Any]) -> None:
        import json as _json

        if redis_client is not None:
            redis_client.publish(
                f"goal_events:{tenant_id}:{goal_id}",
                _json.dumps(
                    {"goal_id": goal_id, "tenant_id": tenant_id, "type": event.get("type", ""),
                     "payload": event}
                ),
            )

    try:
        return await _reap(
            factory,
            redis_client=redis_client,
            enqueue=_enqueue,
            release_slot=_release_slot,
            publish=_publish,
        )
    finally:
        await engine.dispose()


@celery_app.task(name="app.scaling.tasks.detect_stuck_goals", bind=True, max_retries=0)
def detect_stuck_goals(self: Any) -> dict[str, Any]:
    """Fail goals idle in executing/planning past their PLAN's goal timeout."""
    return _run_async(_find_and_fail_stuck_goals())


def _plan_timeout_case_sql() -> str:
    """``CASE t.plan_tier ...`` → the plan's goal_timeout_seconds (unknown → free).

    Built from PLAN_LIMITS (trusted constants, not user input). This scan used a
    flat 60 minutes, so a professional/enterprise goal legitimately running for
    hours (8h / 24h plan timeouts) was killed as "stuck" after one idle hour.
    """
    from app.tenancy.context import PLAN_LIMITS, PlanTier

    whens = " ".join(
        f"WHEN '{tier.value}' THEN {int(limits.goal_timeout_seconds)}"
        for tier, limits in PLAN_LIMITS.items()
    )
    default = int(PLAN_LIMITS[PlanTier.FREE].goal_timeout_seconds)
    return f"(CASE t.plan_tier {whens} ELSE {default} END)"


async def _find_and_fail_stuck_goals() -> dict[str, Any]:
    timeout_sql = _plan_timeout_case_sql()
    try:
        from sqlalchemy import text

        from app.db.rls import system_session
        from app.db.session import get_system_session_factory

        # Cross-tenant beat scan → maintenance (BYPASSRLS) role.
        db = get_system_session_factory()
        async with db() as session, session.begin(), system_session(session):
            result = await session.execute(
                text(f"""UPDATE goals
                        SET status='failed',
                            error_message='Stuck goal: exceeded the plan goal timeout',
                            updated_at=NOW()
                        FROM tenants t
                        WHERE t.id = goals.tenant_id
                          AND goals.status IN ('executing','planning')
                          AND goals.updated_at
                              < NOW() - make_interval(secs => {timeout_sql})
                          AND NOT EXISTS (
                              SELECT 1
                              FROM goal_events ge
                              WHERE ge.goal_id = goals.id
                                AND ge.tenant_id = goals.tenant_id
                                AND ge.event_type IN (
                                    'goal_complete',
                                    'worker_complete',
                                    'goal_failed',
                                    'worker_failed',
                                    'goal_cancelled'
                                )
                          )
                        RETURNING goals.id"""),
            )
            stuck_ids = [r[0] for r in result.fetchall()]
        return {"stuck_goals_failed": len(stuck_ids), "goal_ids": stuck_ids[:20]}
    except Exception as exc:
        return {"error": str(exc), "stuck_goals_failed": 0}


@celery_app.task(name="app.scaling.tasks.execute_retention_policy", bind=True, max_retries=1)
def execute_retention_policy(self: Any) -> dict[str, Any]:
    """Delete records older than DATA_RETENTION_DAYS (default 90)."""
    import os

    retention_days = int(os.getenv("DATA_RETENTION_DAYS", "90"))
    result: dict[str, Any] = _run_async(_delete_expired_records(retention_days))
    _fail_task_on_errors("execute_retention_policy", result)
    return result


def _fail_task_on_errors(task: str, result: dict[str, Any]) -> None:
    """Raise so Celery records FAILURE when a maintenance run hit errors.

    The helpers report per-table / per-partition problems as ``"error: ..."``
    strings (and a top-level ``{"error": ...}``) so one failure does not stop
    the rest — but the tasks then returned that dict, which Celery records as
    SUCCESS, so a retention or partition job that never worked looked healthy.
    """
    problems: list[str] = []
    if result.get("error"):
        problems.append(str(result["error"]))
    for label, value in (result.get("deleted") or {}).items():
        if isinstance(value, str) and value.startswith("error"):
            problems.append(f"{label}: {value}")
    for name, err in (result.get("errors") or {}).items():
        problems.append(f"{name}: {err}")
    if problems:
        logger.error(f"{task}_incomplete", problems=problems[:20])
        raise RuntimeError(f"{task} incomplete: " + "; ".join(problems[:5])[:1000])


# Rows per retention DELETE statement. Each batch is its own short transaction:
# one table-wide DELETE over a large append-only table is a single huge
# transaction (WAL spike, lock hold, table bloat) that grows with the table.
_RETENTION_BATCH = 5000
_RETENTION_MAX_BATCHES = 2000  # per table per run; the next run continues


# Tenants under a tenant-wide legal hold (POST /governance/legal-hold) are
# exempt from retention deletion. The sweeps below are fleet-wide and never
# consulted legal_holds, so a hold placed "to prevent retention deletion"
# prevented nothing.
_TENANT_HOLD_EXEMPT = (
    " AND tenant_id NOT IN (SELECT lh.tenant_id FROM legal_holds lh"
    " WHERE lh.status = 'active' AND lh.resource_type = 'tenant'"
    " AND (lh.expires_at IS NULL OR lh.expires_at > NOW()))"
)
_ANY_TENANT_HOLD_SQL = (
    "SELECT EXISTS (SELECT 1 FROM legal_holds WHERE status = 'active' "
    "AND resource_type = 'tenant' AND (expires_at IS NULL OR expires_at > NOW()))"
)

# (label, batched DELETE). Each selects at most :lim victims by an indexed column.
_RETENTION_DELETES: tuple[tuple[str, str], ...] = (
    (
        "goal_events",
        "DELETE FROM goal_events WHERE (id, created_at) IN ("
        f"SELECT id, created_at FROM goal_events WHERE created_at < :c{_TENANT_HOLD_EXEMPT} "
        "LIMIT :lim)",
    ),
    (
        "decision_traces",
        "DELETE FROM decision_traces WHERE id IN ("
        f"SELECT id FROM decision_traces WHERE created_at < :c{_TENANT_HOLD_EXEMPT} LIMIT :lim)",
    ),
    (
        # The trigger audit/idempotency log had no retention at all. Its
        # (tenant_id, idempotency_key) uniqueness is the durable replay gate, so
        # a firing replayed after the retention window is no longer recognised —
        # far beyond any real redelivery horizon.
        "trigger_events",
        "DELETE FROM trigger_events WHERE id IN ("
        f"SELECT id FROM trigger_events WHERE fired_at < :c_naive{_TENANT_HOLD_EXEMPT} "
        "LIMIT :lim)",
    ),
    (
        # D-18: each memory record carries its own deadline in expires_at.
        "memory_records",
        "DELETE FROM memory_records WHERE id IN ("
        "SELECT id FROM memory_records WHERE expires_at IS NOT NULL AND expires_at < NOW()"
        f"{_TENANT_HOLD_EXEMPT} LIMIT :lim)",
    ),
)


async def _batched_delete(db: Any, sql: str, params: dict[str, Any]) -> int:
    from sqlalchemy import text

    from app.db.rls import system_session

    total = 0
    for _ in range(_RETENTION_MAX_BATCHES):
        async with db() as session, session.begin(), system_session(session):
            result = await session.execute(text(sql), {**params, "lim": _RETENTION_BATCH})
        deleted = int(result.rowcount or 0)
        total += deleted
        if deleted < _RETENTION_BATCH:
            break
    return total


async def _delete_expired_records(retention_days: int) -> dict[str, Any]:
    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    counts: dict[str, Any] = {}
    try:
        from sqlalchemy import text

        from app.db.rls import system_session
        from app.db.session import get_system_session_factory

        # Fleet-wide retention → maintenance (BYPASSRLS) role.
        db = get_system_session_factory()
        # goal_events is range-partitioned by month (migration c5d6e7f8a9b0):
        # whole expired months are detached and dropped — O(partitions), no
        # WAL/bloat — before the batched DELETEs trim the partial month and the
        # DEFAULT partition.
        try:
            async with db() as session, session.begin(), system_session(session):
                # A dropped partition holds every tenant's rows for that month, so
                # it cannot exempt a held tenant: skip the drop while any
                # tenant-wide hold is in force (the batched DELETE below still
                # trims the non-held tenants).
                held = bool((await session.execute(text(_ANY_TENANT_HOLD_SQL))).scalar())
                dropped = (
                    [] if held else await _drop_expired_partitions(session, "goal_events", cutoff)
                )
            if held:
                counts["goal_events_partitions_dropped"] = "skipped: tenant legal hold active"
            elif dropped:
                counts["goal_events_partitions_dropped"] = dropped
        except Exception as exc:
            counts["goal_events_partitions_dropped"] = f"error: {exc}"
        # trigger_events.fired_at is a naive (UTC) timestamp column.
        params = {"c": cutoff, "c_naive": cutoff.replace(tzinfo=None)}
        for label, sql in _RETENTION_DELETES:
            # One table's failure (missing optional table, permissions) must not
            # stop the others; each batch is its own transaction.
            try:
                counts[label] = await _batched_delete(db, sql, params)
            except Exception as exc:
                counts[label] = f"error: {exc}"
        return {"retention_days": retention_days, "cutoff": cutoff.isoformat(), "deleted": counts}
    except Exception as exc:
        return {"error": str(exc)}


async def _drop_expired_partitions(session: Any, table: str, cutoff: Any) -> list[str]:
    """Detach and drop the range partitions of *table* that end at or before *cutoff*.

    Only bounded monthly partitions are considered (never DEFAULT); a partition
    is dropped only when its whole range is older than the cutoff.
    """
    from datetime import datetime

    from sqlalchemy import text

    rows = (
        await session.execute(
            text(
                "SELECT c.relname, pg_get_expr(c.relpartbound, c.oid) "
                "FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "WHERE i.inhparent = CAST(:t AS regclass)"
            ),
            {"t": table},
        )
    ).fetchall()
    dropped: list[str] = []
    for name, bound in rows:
        if not isinstance(name, str) or not isinstance(bound, str):
            continue
        match = re.search(r"TO \('([^']+)'\)", bound or "")
        if not match:
            continue  # DEFAULT or MAXVALUE-bounded partition
        upper = datetime.fromisoformat(match.group(1).replace(" ", "T"))
        if upper.tzinfo is None:
            upper = upper.replace(tzinfo=cutoff.tzinfo)
        if upper > cutoff:
            continue
        if not re.fullmatch(r"[a-z0-9_]+", name):
            continue
        await session.execute(text(f"ALTER TABLE {table} DETACH PARTITION {name}"))
        await session.execute(text(f"DROP TABLE {name}"))
        dropped.append(name)
    return dropped


# Tables created as ``PARTITION BY RANGE (created_at)`` with only a fixed set of
# monthly partitions pre-created by their migration (0055_guardrails,
# 0056_governance_v2, 0057_audit_rails_v2, 0058_cost_optimization). Each also has
# (or, as of 3f2bbce84e68, now has) a DEFAULT partition as a hard-failure safety
# net, but rows landing in DEFAULT are not covered by monthly pruning and degrade
# toward an unindexed-by-time scan as they accumulate there. This task keeps
# ahead of the calendar so DEFAULT stays empty in the steady state.
_RANGE_PARTITIONED_TABLES: tuple[str, ...] = (
    "goal_events",
    "cost_ledger",
    "audit_events",
    "policy_evaluations",
    "guardrail_violations",
)


_MONTHS_AHEAD = 6


async def _ensure_future_partitions() -> dict[str, Any]:
    from datetime import UTC, datetime

    created: dict[str, list[str]] = {t: [] for t in _RANGE_PARTITIONED_TABLES}
    errors: dict[str, str] = {}
    try:
        from sqlalchemy import text

        from app.db.rls import system_session
        from app.db.session import get_system_session_factory

        # Schema maintenance is system work: it runs on the maintenance role,
        # never on the NOBYPASSRLS application role (which also lacks the
        # ownership CREATE TABLE ... PARTITION OF requires). The maintenance role
        # must therefore be able to create partitions of these parents — i.e. be
        # a member of the role that owns them; per-partition failures (including
        # "must be owner of table") are reported in ``errors``, not swallowed.
        db = get_system_session_factory()
        now = datetime.now(UTC)
        # Month index 0..N relative to the current month, in calendar order.
        months: list[tuple[int, int]] = []
        yr, mo = now.year, now.month
        for _ in range(_MONTHS_AHEAD + 1):
            months.append((yr, mo))
            mo += 1
            if mo > 12:
                mo = 1
                yr += 1

        async with db() as session, session.begin(), system_session(session):
            for table in _RANGE_PARTITIONED_TABLES:
                for yr2, mo2 in months:
                    start = f"{yr2}-{mo2:02d}-01"
                    next_mo = mo2 + 1 if mo2 < 12 else 1
                    next_yr = yr2 if mo2 < 12 else yr2 + 1
                    end = f"{next_yr}-{next_mo:02d}-01"
                    tname = f"{table}_{yr2}_{mo2:02d}"
                    try:
                        async with session.begin_nested():
                            await session.execute(
                                text(
                                    f"CREATE TABLE IF NOT EXISTS {tname} "
                                    f"PARTITION OF {table} "
                                    f"FOR VALUES FROM ('{start}') TO ('{end}')"
                                )
                            )
                            # A new partition does not inherit the parent's RLS:
                            # queried by name it would be readable across
                            # tenants. Copy ENABLE/FORCE + policies onto it
                            # (function from migration b4c5d6e7f8a9).
                            await session.execute(
                                text("SELECT app_apply_parent_rls(CAST(:t AS regclass))"),
                                {"t": tname},
                            )
                            created[table].append(tname)
                    except Exception as exc:
                        # A DEFAULT partition already claiming this range (or any
                        # other per-table hiccup) must not abort the rest of the
                        # loop — each attempt runs in its own SAVEPOINT.
                        errors[tname] = str(exc)
        return {"created": created, "errors": errors}
    except Exception as exc:
        return {"error": str(exc)}


@celery_app.task(name="app.scaling.tasks.expire_hitl_approvals", bind=True, max_retries=0)
def expire_hitl_approvals(self: Any) -> dict[str, Any]:
    """Auto-reject HITL approval requests that have passed their expires_at.

    G-12/G-16: After marking each request as timed_out, fire
    NotificationService.notify_approval_timeout() so users are alerted that
    their action was auto-rejected.
    """
    from datetime import UTC, datetime

    expired_count = 0
    released = 0
    parked_failed: list[dict[str, Any]] = []
    notified: list[str] = []
    try:
        expired_ids, parked_failed = _run_async(_expire_db_approvals_and_fail_parked())
        expired_count = len(expired_ids)
        if parked_failed:
            # NF-11: parked supervised goals were failed with the approvals; tell
            # every replica (SSE / in-memory record) and close owning missions.
            try:
                _run_async(_announce_parked_goal_failures(parked_failed))
            except Exception as _a_exc:
                logger.warning("expire_hitl_approvals_announce_failed: %s", _a_exc)
        if expired_ids:
            # P5-4: wake the goals blocked on these approvals (on any replica) so
            # they fail or replan with the "expired" outcome instead of hanging.
            try:
                released = _run_async(_release_expired_approval_waiters(expired_ids))
            except Exception as _r_exc:
                logger.warning("expire_hitl_approvals_release_failed: %s", _r_exc)
        # G-12: notify on each expired request
        if expired_ids:
            try:
                notified = _run_async(_notify_expired_approvals(expired_ids))
            except Exception as _n_exc:
                logger.warning("expire_hitl_approvals_notify_failed: %s", _n_exc)
    except Exception as exc:
        logger.warning("expire_hitl_approvals failed: %s", exc)
    return {
        "expired": expired_count,
        "waiters_released": released,
        "parked_goals_failed": len(parked_failed),
        "notified": len(notified),
        "checked_at": datetime.now(UTC).isoformat(),
    }


def _hitl_release_redis() -> Any:
    """Async Redis client for releasing approval waiters (closed by the caller)."""
    import redis.asyncio as _aioredis

    return _aioredis.from_url(REDIS_URL)


async def _release_expired_approval_waiters(expired_ids: list[str]) -> int:
    from app.governance.hitl import release_expired_waiters

    client = _hitl_release_redis()
    try:
        return await release_expired_waiters(client, [str(i) for i in expired_ids])
    finally:
        with contextlib.suppress(Exception):
            await client.aclose()


async def _notify_expired_approvals(expired_ids: list[str]) -> list[str]:
    """G-12: Send notification for each expired approval request."""
    if not expired_ids:
        return []
    try:
        from sqlalchemy import text

        from app.db.rls import system_session
        from app.db.session import get_system_session_factory

        # The expired ids span every tenant (see _expire_db_approvals), so this
        # read is system work too. It used to run on the application role with
        # no RLS context: under the NOBYPASSRLS role it matched zero rows, and
        # no timeout notification was ever sent.
        db = get_system_session_factory()
        notified: list[str] = []
        async with db() as session, session.begin(), system_session(session):
            rows = (
                await session.execute(
                    text(
                        "SELECT id, tenant_id, goal_id, action FROM approval_requests "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": list(expired_ids)},
                )
            ).fetchall()
        try:
            from app.main import app as _app  # type: ignore[attr-defined]

            _notif = getattr(getattr(_app, "state", None), "notification_service", None)
        except Exception:
            _notif = None
        if _notif is None or not hasattr(_notif, "notify_approval_timeout"):
            return []
        for row in rows:
            req_id, tenant_id, goal_id, action = row
            try:
                await _notif.notify_approval_timeout(
                    request_id=str(req_id),
                    goal_id=str(goal_id),
                    action=str(action),
                    tenant_id=str(tenant_id),
                )
                notified.append(str(req_id))
            except Exception as exc:
                logger.warning("notify_expired_failed id=%s: %s", req_id, exc)
        return notified
    except Exception as exc:
        logger.warning("_notify_expired_approvals failed: %s", exc)
        return []


async def _expire_db_approvals() -> list[str]:
    ids, _parked = await _expire_db_approvals_and_fail_parked()
    return ids


async def _expire_db_approvals_and_fail_parked() -> tuple[list[str], list[dict[str, Any]]]:
    """Expire overdue approvals and fail the goals parked on them, atomically.

    NF-11: a supervised goal parked in ``waiting_human`` (no live waiter) whose
    approval expires is failed with "approval expired" in the SAME transaction,
    so an expired approval never leaves its goal parked forever.
    """
    try:
        from sqlalchemy import text

        from app.db.rls import system_session
        from app.db.session import get_system_session_factory
        from app.governance.hitl_expiry import fail_goals_parked_on_expired

        # Cross-tenant beat scan → maintenance (BYPASSRLS) role.
        db = get_system_session_factory()
        async with db() as session, session.begin(), system_session(session):
            result = await session.execute(
                text(
                    """UPDATE approval_requests
                            SET status='timed_out', resolved_at=NOW()
                            WHERE status='pending'
                              AND expires_at IS NOT NULL
                              AND expires_at < NOW()
                            RETURNING id"""
                )
            )
            ids = [row[0] for row in result.fetchall()]
            parked = await fail_goals_parked_on_expired(session, [str(i) for i in ids])
            return ids, parked
    except Exception as exc:
        logger.warning("expire_db_approvals failed: %s", exc)
        return [], []


async def _announce_parked_goal_failures(parked: list[dict[str, Any]]) -> int:
    """goal_failed events + SSE publish + mission finalize for NF-11 failures."""
    import json as _json

    from app.db.session import get_session_factory
    from app.governance.hitl_expiry import announce_parked_goal_failures

    redis_client = _get_sync_redis()

    def _publish(tenant_id: str, goal_id: str, event: dict[str, Any]) -> None:
        if redis_client is None:
            return
        envelope: dict[str, Any] = {
            "goal_id": goal_id,
            "tenant_id": tenant_id,
            "type": event.get("type", ""),
            "payload": event,
        }
        if "_seq" in event:
            envelope["_seq"] = event["_seq"]
        redis_client.publish(f"goal_events:{tenant_id}:{goal_id}", _json.dumps(envelope))

    return await announce_parked_goal_failures(
        get_session_factory(), parked, publish=_publish, finalize=_finalize_owning_mission
    )


# G-16: Register the HITL expiry task in Celery's beat schedule so approvals
# auto-expire every 60 seconds without manual intervention. Also routes it to
# the governance queue so it never competes with tenant goal throughput.
try:
    celery_app.conf.beat_schedule["expire-hitl-approvals-every-60s"] = {
        "task": "app.scaling.tasks.expire_hitl_approvals",
        "schedule": 60.0,
        "options": {"queue": "governance"},
    }
    celery_app.conf.task_routes.update(
        {"app.scaling.tasks.expire_hitl_approvals": {"queue": "governance"}}
    )
    logger.info("beat_schedule_registered: expire-hitl-approvals-every-60s")
except Exception as _hitl_beat_exc:
    logger.warning("Failed to register expire_hitl_approvals beat schedule: %s", _hitl_beat_exc)


@celery_app.task(name="app.scaling.tasks.check_email_goals", bind=True, max_retries=1)
def check_email_goals(self: Any) -> dict[str, Any]:
    """Check IMAP mailbox and submit new emails as goals."""
    import os

    if os.getenv("IMAP_ENABLED", "false").lower() not in {"true", "1"}:
        return {"status": "disabled", "processed": 0}

    return _run_async(_do_check_email_goals())


async def _do_check_email_goals() -> dict[str, Any]:
    import os

    from app.integrations.email.imap_listener import check_and_process_emails

    try:
        from app.db.session import get_session_factory as _get_fresh_db
        from app.services.event_store import EventStore
        from app.services.goal_service import GoalService
        from app.tenancy.context import TenantContext
        from app.tenancy.plan_resolver import resolve_tenant_plan

        db_factory = _get_fresh_db()
        event_store = EventStore(db_factory)
        from app.services.goal_queue import CeleryGoalTaskQueue

        # Enqueue via run_goal: an in-process asyncio task would die with
        # _run_async's event loop the moment this Celery task returns.
        goal_service = GoalService(
            db_session_factory=db_factory,
            event_store=event_store,
            task_queue=CeleryGoalTaskQueue(),
        )

        email_tenant_id = os.getenv("IMAP_TENANT_ID", "email-default")
        ctx = TenantContext(
            tenant_id=email_tenant_id,
            # TRG-06: the tenant's real plan (was hard-coded PROFESSIONAL).
            plan=await resolve_tenant_plan(email_tenant_id, db_factory=db_factory),
            api_key_id="email-listener",
        )

        count = await check_and_process_emails(goal_service, ctx)
        return {"status": "ok", "processed": count}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "processed": 0}


@celery_app.task(name="agentverse.maintenance.consolidate_memories")
def consolidate_memories_task() -> dict:
    """Consolidate (dedup) and expire long_term_memory, per tenant (MEM-17).

    Tenant scan on the maintenance (BYPASSRLS) factory; every DELETE on the
    app factory under the tenant's RLS, bounded per transaction, skipping
    legal-holds and honouring each tenant's retention_days (see
    ``app.memory.ltm_maintenance``). An error FAILS the task — it used to be
    folded into ``results["error"]`` and returned as a success.
    """

    async def _run() -> dict:
        from app.db.session import get_session_factory, get_system_session_factory
        from app.memory.ltm_maintenance import consolidate_long_term_memory

        return await consolidate_long_term_memory(
            system_db=get_system_session_factory(), app_db=get_session_factory()
        )

    try:
        return _run_async(_run())
    except Exception:
        logger.exception("consolidate_memories_failed")
        raise


# Register consolidate_memories in the Celery beat schedule (3 AM UTC daily)
try:
    from celery.schedules import crontab as _crontab

    celery_app.conf.beat_schedule["consolidate-memories-daily"] = {
        "task": "agentverse.maintenance.consolidate_memories",
        "schedule": _crontab(hour=3, minute=0),
        "options": {"queue": "maintenance"},
    }
    celery_app.conf.task_routes.update(
        {"agentverse.maintenance.consolidate_memories": {"queue": "maintenance"}}
    )
except Exception as _sched_exc:
    logger.warning("Failed to register consolidate_memories beat schedule: %s", _sched_exc)


@celery_app.task(name="agentverse.maintenance.backfill_canonical_memory")
def backfill_canonical_memory(
    tenant_id: str, batch_size: int = 100, max_rows: int = 1_000, reset: bool = False
) -> dict[str, Any]:
    """Backfill one tenant's legacy Reflexion lessons into canonical memory.

    Bounded (``max_rows`` per run, clamped) and resumable (keyset checkpoint in
    ``memory_backfill_checkpoints``); re-enqueue until ``completed`` is true.
    Runs under the tenant's RLS; see app.memory.backfill_runner.
    """

    async def _run() -> dict[str, Any]:
        from dataclasses import asdict

        from app.db.session import get_session_factory as _get_fresh_db
        from app.memory.backfill_runner import run_reflexion_lessons_backfill

        db = _get_fresh_db()
        result = await run_reflexion_lessons_backfill(
            db,
            _canonical_memory_repository(db),
            tenant_id=tenant_id,
            batch_size=batch_size,
            max_rows=max_rows,
            reset=reset,
        )
        return asdict(result)

    return cast(dict[str, Any], _run_async(_run()))


def _canonical_memory_repository(db: Any) -> Any:
    """The canonical memory repository WITH the shared resolved embedder (MEM-10).

    The backfill used to build it without one, so every backfilled lesson was
    stored vector-less (lexical recall only).
    """
    from app.memory.embedding import memory_embedder_from_provider
    from app.memory.postgres_repository import PostgresMemoryRepository
    from app.providers.embedder_factory import build_query_embedder

    return PostgresMemoryRepository(
        db, embedder=memory_embedder_from_provider(build_query_embedder())
    )


@celery_app.task(name="agentverse.maintenance.canonical_memory_maintenance")
def canonical_memory_maintenance(max_rows: int = 1_000, reembed_limit: int = 200) -> dict:
    """Daily: finish every tenant's legacy backfill and re-embed vector-less or
    stale-model canonical records (MEM-10). Tenants are found on the
    maintenance factory; all work runs per tenant under its RLS. Bounded per
    tenant per run; a tenant failure fails the task after the others ran.
    """

    async def _run() -> dict:
        from app.db.session import get_session_factory, get_system_session_factory
        from app.memory.backfill_runner import (
            canonical_maintenance_tenant_pages,
            run_reflexion_lessons_backfill,
        )

        db = get_session_factory()
        repo = _canonical_memory_repository(db)
        totals = {"tenants": 0, "backfilled": 0, "reembedded": 0, "failed": 0}
        # MEM-39: keyset pages over tenants, not a scan of every memory record.
        async for page in canonical_maintenance_tenant_pages(get_system_session_factory()):
            totals["tenants"] += len(page)
            for tid in page:
                try:
                    result = await run_reflexion_lessons_backfill(
                        db, repo, tenant_id=tid, max_rows=max_rows
                    )
                    totals["backfilled"] += result.written
                    totals["reembedded"] += await repo.reembed_pending(
                        tid, limit=reembed_limit
                    )
                except Exception as exc:
                    totals["failed"] += 1
                    logger.warning(
                        "canonical_memory_maintenance_failed tenant=%s: %s", tid, exc
                    )
        if totals["failed"]:
            raise RuntimeError(
                f"canonical memory maintenance failed for {totals['failed']} tenants"
            )
        return totals

    return cast(dict, _run_async(_run()))


try:
    from celery.schedules import crontab as _cm_crontab

    celery_app.conf.beat_schedule["canonical-memory-maintenance-daily"] = {
        "task": "agentverse.maintenance.canonical_memory_maintenance",
        "schedule": _cm_crontab(hour=3, minute=30),
        "options": {"queue": "maintenance"},
    }
    celery_app.conf.task_routes.update(
        {"agentverse.maintenance.canonical_memory_maintenance": {"queue": "maintenance"}}
    )
except Exception as _cm_sched_exc:  # pragma: no cover - defensive
    logger.warning("canonical_memory_maintenance beat registration failed: %s", _cm_sched_exc)


async def _submit_intention_as_goal(item: Any, plan: str) -> dict[str, Any]:
    """Run a due prospective intention as a goal for its tenant (MEM-16).

    RV-07: the goal runs as the API key that scheduled the intention, re-checked
    NOW against Postgres (tenant RLS): the key must still be active/unexpired
    and still hold goals:write. A denial is audited and raised as
    ``IntentionNotAuthorizedError`` (the intention is marked failed, never
    retried); an unverifiable principal raises and is retried — nothing runs
    under a synthetic context any more.
    """
    from app.db.session import get_session_factory
    from app.memory import prospective_auth
    from app.memory.prospective_auth import IntentionNotAuthorizedError, IntentionPrincipal
    from app.tenancy.context import PlanTier, TenantContext

    db_factory = get_session_factory()
    stored = IntentionPrincipal.from_snapshot(item.policy_snapshot)
    try:
        principal = await prospective_auth.authorize_intention_principal(
            db_factory, item.tenant_id, stored
        )
    except IntentionNotAuthorizedError as exc:
        await prospective_auth.audit_intention_denied(
            db_factory,
            tenant_id=item.tenant_id,
            memory_id=item.memory_id,
            api_key_id=stored.api_key_id if stored else None,
            reason=exc.reason,
        )
        raise
    goal_service, _ = _build_worker_goal_service()
    if goal_service is None:
        raise RuntimeError("goal service unavailable on this worker")
    try:
        tier = PlanTier(plan)
    except ValueError:
        tier = PlanTier.FREE  # least privilege for an unknown plan value
    ctx = TenantContext(
        tenant_id=item.tenant_id,
        plan=tier,
        api_key_id=principal.api_key_id,
        roles=principal.roles,
        scopes=principal.scopes,
    )
    result = await goal_service.submit_goal(
        goal=f"Deferred intention: {item.intention}",
        priority="normal",
        dry_run=False,
        tenant_ctx=ctx,
        agent_id=(item.policy_snapshot or {}).get("agent_id"),
        execution_context={
            "source": "prospective_memory",
            "prospective_memory_id": item.memory_id,
            "source_goal_id": item.source_goal_id,
            "submitted_by_api_key_id": principal.api_key_id,
        },
    )
    return {"goal_id": str((result or {}).get("goal_id") or "")}


@celery_app.task(name="agentverse.memory.process_due_prospective")
def process_due_prospective_memories(max_per_tenant: int = 50) -> dict:
    """Fire due prospective intentions (MEM-16).

    Active tenants with due intentions are found on the maintenance factory;
    per tenant the Postgres service (tenant RLS) leases the due items with
    fencing tokens, each is submitted as a goal for that tenant and marked
    completed with the goal id. A failed submission stays leased and is
    retried after the lease expires. A scan failure fails the task.
    """

    async def _run() -> dict:
        from sqlalchemy import text

        from app.db.rls import system_session
        from app.db.session import get_session_factory, get_system_session_factory
        from app.memory.prospective_postgres import PostgresProspectiveMemoryService
        from app.memory.prospective_runtime import fire_due_intentions

        system_db = get_system_session_factory()
        async with system_db() as session, session.begin(), system_session(session):
            due = (
                await session.execute(
                    text(
                        "SELECT DISTINCT p.tenant_id, t.plan_tier FROM prospective_memory p "
                        "JOIN tenants t ON t.id = p.tenant_id "
                        "WHERE t.is_active AND p.state IN ('pending', 'leased') "
                        "AND p.due_at <= now()"
                    )
                )
            ).fetchall()
        service = PostgresProspectiveMemoryService(get_session_factory())
        totals = {"tenants": len(due), "fired": 0, "failed_tenants": 0}
        for tenant_id, plan in due:
            plan_value = str(plan or "free")

            async def _submit(item: Any, _plan: str = plan_value) -> dict[str, Any]:
                return await _submit_intention_as_goal(item, _plan)

            try:
                fired = await fire_due_intentions(
                    service,
                    tenant_id=str(tenant_id),
                    submit=_submit,
                    maximum_items=max_per_tenant,
                )
                totals["fired"] += len(fired)
            except Exception as exc:
                totals["failed_tenants"] += 1
                logger.warning("prospective_tenant_failed tenant=%s: %s", tenant_id, exc)
        return totals

    return cast(dict, _run_async(_run()))


@celery_app.task(name="agentverse.memory.purge_expired_canonical")
def purge_expired_canonical_memories() -> dict:
    """Hard-delete expired canonical memory records per tenant (retention)."""

    async def _run() -> dict:
        from sqlalchemy import text

        from app.db.rls import system_session
        from app.db.session import get_session_factory, get_system_session_factory
        from app.scaling.memory_tasks import purge_expired_memories

        system_db = get_system_session_factory()
        async with system_db() as session, session.begin(), system_session(session):
            tenants = [
                str(r[0])
                for r in (
                    await session.execute(
                        text(
                            "SELECT DISTINCT tenant_id FROM memory_records "
                            "WHERE expires_at IS NOT NULL AND expires_at <= now()"
                        )
                    )
                ).fetchall()
            ]
        repo = _canonical_memory_repository(get_session_factory())
        purged = 0
        for tenant_id in tenants:
            purged += await purge_expired_memories(repo, tenant_id=tenant_id)
        # MEM-44: terminal prospective intentions past retention, batched.
        from datetime import UTC, datetime

        from app.memory.prospective_postgres import purge_terminal_prospective

        prospective_purged = await purge_terminal_prospective(
            system_db, now=datetime.now(UTC)
        )
        return {
            "tenants": len(tenants),
            "purged": purged,
            "prospective_purged": prospective_purged,
        }

    return cast(dict, _run_async(_run()))


try:
    from celery.schedules import crontab as _pm_crontab

    celery_app.conf.beat_schedule["process-due-prospective-memories"] = {
        "task": "agentverse.memory.process_due_prospective",
        "schedule": _pm_crontab(minute="*/5"),
        "options": {"queue": "maintenance"},
    }
    celery_app.conf.beat_schedule["purge-expired-canonical-memories-daily"] = {
        "task": "agentverse.memory.purge_expired_canonical",
        "schedule": _pm_crontab(hour=4, minute=15),
        "options": {"queue": "maintenance"},
    }
    celery_app.conf.task_routes.update(
        {
            "agentverse.memory.process_due_prospective": {"queue": "maintenance"},
            "agentverse.memory.purge_expired_canonical": {"queue": "maintenance"},
        }
    )
except Exception as _pm_sched_exc:  # pragma: no cover - defensive
    logger.warning("prospective memory beat registration failed: %s", _pm_sched_exc)


@celery_app.task(name="agentverse.maintenance.reindex_stale_knowledge")
def reindex_stale_knowledge() -> dict:
    """Retired — deliberately does nothing and is NOT on the beat schedule.

    It used to ``UPDATE documents SET needs_reindex = TRUE`` hourly, which was a
    no-op dressed up as maintenance:

    * ``KnowledgeStore`` never writes the legacy ``documents`` table — chunks
      live in ``knowledge_chunks_<dim>`` — so there was nothing to mark;
    * nothing anywhere reads ``needs_reindex``, so a mark triggered nothing;
    * it ran on the application role without an RLS context against a
      FORCE-RLS table, so under the production role it matched zero rows anyway.

    Real freshness is handled elsewhere: each Source is re-synced on its
    ``sync_interval_seconds`` by ``ingestion.dispatch_due_sources`` (content-hash
    dedup skips unchanged documents), retrieval already excludes chunks past
    ``expires_at``, and a model change is re-embedded explicitly via
    ``re_embed_collection``. The task name stays registered only so messages
    already queued by an older beat drain harmlessly.
    """
    logger.info("reindex_stale_knowledge_retired")
    return {"status": "retired", "marked_for_reindex": 0}


@celery_app.task(name="agentverse.maintenance.purge_expired_artifacts")
def purge_expired_artifacts() -> dict:
    """Delete artifacts past their expiry date from DB (MinIO lifecycle handles storage)."""

    async def _run() -> dict:
        from sqlalchemy import text

        from app.db.session import get_session_factory as _get_fresh_db

        db = _get_fresh_db()
        async with db() as session, session.begin():
            result = await session.execute(
                text("DELETE FROM artifacts WHERE expires_at IS NOT NULL AND expires_at < NOW()")
            )
            return {"purged_count": result.rowcount}

    return _run_async(_run())


@celery_app.task(name="agentverse.maintenance.purge_expired_chat_artifacts")
def purge_expired_chat_artifacts() -> dict:
    """Delete chat-generated documents past retention (ORG-42), in bounded batches.

    Cross-tenant, so it runs on the BYPASSRLS maintenance factory; a failure raises
    so Celery records it instead of reporting zero rows purged.
    """

    async def _run() -> dict:
        from app.chat.artifact_store import purge_expired_chat_artifacts as _purge
        from app.db.session import get_system_session_factory

        return {"purged_count": await _purge(get_system_session_factory())}

    return _run_async(_run())


@celery_app.task(name="agentverse.maintenance.purge_expired_chat_sessions")
def purge_expired_chat_sessions() -> dict:
    """Enforce chat_sessions.ttl_days (CHAT-SEC-3): delete expired, unpinned,
    unheld sessions with their messages and artifacts, in bounded batches.

    Cross-tenant, so it runs on the BYPASSRLS maintenance factory. An owned
    session's transcript removal is queued (ingestion.chat_transcripts_purge)
    before the session is deleted; when it cannot be queued the batch is kept
    and the task raises, so Celery records the failure instead of a silent 0.
    """

    async def _run() -> dict:
        import asyncio as _asyncio

        from app.chat.retention import purge_expired_chat_sessions as _purge
        from app.db.session import get_system_session_factory
        from app.services.chat_knowledge import enqueue_purge_continuation

        async def _transcripts(tenant_id: str, owner: str, session_ids: list[str]) -> None:
            await _asyncio.to_thread(
                enqueue_purge_continuation, tenant_id, user_id=owner,
                session_ids=session_ids, unconsented_only=False,
            )

        report = await _purge(get_system_session_factory(), on_owned_sessions=_transcripts)
        out = report.as_dict()
        logger.info("chat_sessions_ttl_purged", **out)
        return out

    return _run_async(_run())


@celery_app.task(name="agentverse.maintenance.purge_expired_org_attachments")
def purge_expired_org_attachments() -> dict:
    """Delete mission attachments past retention (a08-F177-01), in bounded batches.

    Cross-tenant, so it runs on the BYPASSRLS maintenance factory; failures raise.
    """

    async def _run() -> dict:
        from app.db.session import get_system_session_factory
        from app.org.attachments import purge_expired_org_attachments as _purge

        return {"purged_count": await _purge(get_system_session_factory())}

    return _run_async(_run())


# a09-F212-03: the async GDPR export reads every row in keyset pages over the
# indexed (tenant_id, created_at) — never a silently truncating LIMIT — and a
# section larger than the ceiling fails the job ("too_large") instead of
# shipping part of the tenant's data as a complete export.
GDPR_EXPORT_PAGE_SIZE = 2_000
GDPR_EXPORT_MAX_ROWS = 100_000


class GdprExportTooLargeError(RuntimeError):
    """A GDPR export section exceeds :data:`GDPR_EXPORT_MAX_ROWS`."""


async def _gdpr_export_section(
    session: Any, *, table: str, columns: tuple[str, ...], tenant_id: str
) -> list[Any]:
    """Every row of *table* for *tenant_id*, keyset-paged on ``(created_at, id)``.

    *columns* must start with ``id`` and end with ``created_at`` (the cursor).
    Raises :class:`GdprExportTooLargeError` past the ceiling; read errors propagate.
    """
    from sqlalchemy import text

    cols = ", ".join(columns)
    rows: list[Any] = []
    cursor: tuple[Any, Any] | None = None
    while True:
        params: dict[str, Any] = {"tid": tenant_id, "lim": GDPR_EXPORT_PAGE_SIZE}
        where = "tenant_id = :tid"
        if cursor is not None:
            where += " AND (created_at, id) > (:c_at, :c_id)"
            params["c_at"], params["c_id"] = cursor
        page = (
            await session.execute(
                text(
                    f"SELECT {cols} FROM {table} WHERE {where} "
                    "ORDER BY created_at, id LIMIT :lim"
                ),
                params,
            )
        ).fetchall()
        rows.extend(page)
        if len(rows) > GDPR_EXPORT_MAX_ROWS:
            raise GdprExportTooLargeError(
                f"too_large: {table} has more than {GDPR_EXPORT_MAX_ROWS} rows for this tenant"
            )
        if len(page) < GDPR_EXPORT_PAGE_SIZE:
            return rows
        cursor = (page[-1][-1], page[-1][0])


@celery_app.task(name="agentverse.compliance.run_gdpr_export", bind=True, max_retries=1)
def run_gdpr_export(self: Any, job_id: str, tenant_id: str) -> dict[str, Any]:
    """Async GDPR data export job — runs in background worker.

    Collects all tenant data, serialises to JSON, updates the job record
    in gdpr_export_jobs with status='complete' and a download_url.
    """

    async def _run() -> dict[str, Any]:
        from sqlalchemy import text

        from app.db.session import get_session_factory as _get_fresh_db

        db = _get_fresh_db()
        try:
            # Collect all tenant data. goals and audit_log both have FORCE ROW
            # LEVEL SECURITY (migrations 0004 / 0005), so this read must set the
            # app.tenant_id GUC via sqlalchemy_rls_context -- without it, under any
            # non-BYPASSRLS role the policy silently filters every row out and the
            # "export" would always be empty regardless of the persistence fix below.
            from app.db.rls import sqlalchemy_rls_context

            # Every row, keyset-paged; an audit read error or an over-ceiling
            # section fails the job (a09-F212-03) — never a partial "complete".
            async with db() as session, sqlalchemy_rls_context(session, tenant_id):
                goals = await _gdpr_export_section(
                    session,
                    table="goals",
                    columns=("id", "goal_text", "status", "created_at"),
                    tenant_id=tenant_id,
                )
                audit = await _gdpr_export_section(
                    session,
                    table="audit_log",
                    columns=("id", "goal_id", "tool_name", "outcome", "action_level", "created_at"),
                    tenant_id=tenant_id,
                )

            import json
            from datetime import datetime

            def _iso(value: Any) -> str | None:
                return value.isoformat() if hasattr(value, "isoformat") else None

            export_data = {
                "tenant_id": tenant_id,
                "exported_at": datetime.now(UTC).isoformat(),
                "complete": True,
                "goals": [
                    {
                        "id": str(r[0]),
                        "text": str(r[1]),
                        "status": str(r[2]),
                        "created_at": _iso(r[3]),
                    }
                    for r in goals
                ],
                "audit_entries": [
                    {
                        "id": str(r[0]),
                        "goal_id": str(r[1]),
                        "tool": str(r[2]),
                        "outcome": str(r[3]),
                        "action_level": str(r[4]),
                        "created_at": _iso(r[5]),
                    }
                    for r in audit
                ],
            }

            # Persist the export content itself (not just a job-status row), and
            # reuse this job's own job_id as the download URL's id, rather than an
            # unrelated uuid nobody stores anywhere. The download endpoint (GET
            # /compliance/export/{request_id}/download in app/api/enterprise.py)
            # resolves via ComplianceController.get_export_status(), which reads
            # the compliance_requests table by request_id + tenant_id -- so the
            # export must land there for the link to ever serve real data instead
            # of 404ing forever. compliance_requests has FORCE ROW LEVEL SECURITY
            # (migration 0026 / 767fe9d87bfe), so the write must set the
            # app.tenant_id GUC via sqlalchemy_rls_context (app/db/rls.py; already
            # imported above for the goals/audit_log read).

            # NOTE: the download endpoint (download_export in app/api/enterprise.py)
            # is registered on the "/enterprise"-prefixed router, not the
            # "/compliance"-prefixed compliance_router this task's job lives under
            # -- so the URL must carry that prefix to actually resolve.
            download_url = f"/enterprise/compliance/export/{job_id}/download"

            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    text(
                        """INSERT INTO compliance_requests
                           (request_id, tenant_id, status, download_url, payload, created_at)
                           VALUES (:rid, :tid, 'ready', :url, CAST(:payload AS jsonb), NOW())
                           ON CONFLICT (request_id) DO UPDATE
                             SET status = EXCLUDED.status,
                                 download_url = EXCLUDED.download_url,
                                 payload = EXCLUDED.payload"""
                    ),
                    {
                        "rid": job_id,
                        "tid": tenant_id,
                        "url": download_url,
                        "payload": json.dumps(export_data, default=str),
                    },
                )

            # gdpr_export_jobs is tenant-isolated by RLS. This task works for ONE
            # tenant (it is enqueued by that tenant's own request), so it runs
            # under that tenant's context — not the maintenance role. Unscoped,
            # the UPDATE matched zero rows under a NOBYPASSRLS role and the job
            # sat at 'pending' forever although the export had been written.
            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    text("""
                    UPDATE gdpr_export_jobs
                    SET status = 'complete', completed_at = NOW(), download_url = :url
                    WHERE id = :jid AND tenant_id = :tid
                """),
                    {"url": download_url, "jid": job_id, "tid": tenant_id},
                )

            return {"status": "complete", "job_id": job_id, "download_url": download_url}

        except Exception as exc:
            try:
                from app.db.rls import sqlalchemy_rls_context as _rls

                async with db() as session, session.begin(), _rls(session, tenant_id):
                    await session.execute(
                        text(
                            "UPDATE gdpr_export_jobs SET status = 'failed', "
                            "error_message = :err WHERE id = :jid AND tenant_id = :tid"
                        ),
                        {"err": str(exc)[:500], "jid": job_id, "tid": tenant_id},
                    )
            except Exception:
                pass
            raise

    return _run_async(_run())


# ─────────────────────────────────────────────────────────────────────────────
# CIVILIZATION TASKS
# ─────────────────────────────────────────────────────────────────────────────


_CIV_TICK_EXPIRES_S = 30  # one discovery interval (beat: civilization-discovery-every-30s)
_CIV_TICK_PENDING_TTL_S = 120  # self-heals if a worker dies before clearing the marker


def _civ_tick_pending_key(civilization_id: str) -> str:
    return f"civ:tick:pending:{civilization_id}"


@celery_app.task(name="app.scaling.tasks.civilization_tick")
def civilization_tick(civilization_id: str, tenant_id: str) -> dict:
    try:
        return _civilization_tick(civilization_id, tenant_id)
    finally:
        with contextlib.suppress(Exception):
            _get_sync_redis().delete(_civ_tick_pending_key(civilization_id))


def _civilization_tick(civilization_id: str, tenant_id: str) -> dict:
    """Periodic tick for a civilization — breach check, auto-retire, learning step."""

    async def _run() -> dict:
        try:
            import json
            import os

            from app.civilization.blackboard import Blackboard
            from app.civilization.bus import CivilizationBus
            from app.civilization.governor import Governor
            from app.civilization.learning import LearningPipeline
            from app.civilization.models import Constitution
            from app.civilization.orchestrator import CivilizationOrchestrator
            from app.civilization.society import Society
            from app.db.session import get_session_factory as _get_fresh_db

            db = _get_fresh_db()
            redis_url = os.getenv("REDIS_URL", "")
            redis = None
            if redis_url:
                import redis.asyncio as aioredis

                redis = aioredis.from_url(redis_url, decode_responses=True)

            # Load constitution from DB
            constitution = Constitution()  # defaults; overridden by DB data below
            try:
                from sqlalchemy import text

                from app.db.rls import sqlalchemy_rls_context

                # Under the tenant's RLS context: civilizations is FORCE-RLS, so
                # without it this read matched nothing and every tick silently
                # ran on the default constitution.
                async with (
                    db() as session,
                    session.begin(),
                    sqlalchemy_rls_context(session, tenant_id),
                ):
                    row = (
                        await session.execute(
                            text(
                                "SELECT constitution FROM civilizations WHERE id=:id AND tenant_id=:tid"  # noqa: E501
                            ),
                            {"id": civilization_id, "tid": tenant_id},
                        )
                    ).fetchone()
                if row and row[0]:
                    data = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                    constitution = Constitution.from_dict(data)
            except Exception:
                pass

            from app.tenancy.context import PlanTier, TenantContext

            tenant_ctx = TenantContext(
                tenant_id=tenant_id, plan=PlanTier.ENTERPRISE, api_key_id="tick"
            )

            governor = Governor(
                constitution=constitution,
                civilization_id=civilization_id,
                tenant_id=tenant_id,
                db_session_factory=db,
                redis=redis,
            )
            society = Society(
                civilization_id=civilization_id,
                tenant_id=tenant_id,
                db_session_factory=db,
            )
            bus = CivilizationBus(
                civilization_id=civilization_id,
                tenant_id=tenant_id,
                db_session_factory=db,
                redis=redis,
            )
            blackboard = Blackboard(
                civilization_id=civilization_id,
                tenant_id=tenant_id,
                db_session_factory=db,
                bus=bus,
            )
            learning = LearningPipeline(
                civilization_id=civilization_id,
                tenant_id=tenant_id,
                db_session_factory=db,
                redis=redis,
            )

            orchestrator = CivilizationOrchestrator(
                civilization_id=civilization_id,
                tenant_id=tenant_id,
                constitution=constitution,
                governor=governor,
                society=society,
                bus=bus,
                blackboard=blackboard,
                learning_pipeline=learning,
                db_session_factory=db,
                redis=redis,
                tenant_ctx=tenant_ctx,
            )

            result = await orchestrator.tick()
            if redis:
                await redis.aclose()
            return result
        except Exception as exc:
            import logging

            logging.getLogger(__name__).error("civilization_tick_failed", extra={"error": str(exc)})
            return {"error": str(exc)}

    return _run_async(_run())


@celery_app.task(name="app.scaling.tasks.civilization_learning_step")
def civilization_learning_step(civilization_id: str, tenant_id: str) -> dict:
    """Run one step of the learning pipeline for a civilization."""

    async def _run() -> dict:
        try:
            from app.civilization.learning import LearningPipeline
            from app.db.session import get_session_factory as _get_fresh_db

            db = _get_fresh_db()
            pipeline = LearningPipeline(
                civilization_id=civilization_id,
                tenant_id=tenant_id,
                db_session_factory=db,
            )
            return await pipeline.run_step()
        except Exception as exc:
            import logging

            logging.getLogger(__name__).error(
                "civilization_learning_failed", extra={"error": str(exc)}
            )
            return {"error": str(exc)}

    return _run_async(_run())


# ── M-1: New maintenance tasks wired into beat schedule ───────────────────────


@celery_app.task(name="app.scaling.tasks.warm_jwks_cache", queue="maintenance")
def warm_jwks_cache() -> dict:
    """Warm the JWKS Redis cache every 9 minutes to avoid cache misses."""
    import json
    import os

    async def _run() -> dict:
        try:
            from app.auth.agent_identity import _build_jwks  # type: ignore[import]
            from app.db.session import get_system_session_factory

            # Cross-tenant read of FORCE-RLS agent_credentials: the maintenance
            # (BYPASSRLS) factory. The request factory saw zero rows under the
            # NOBYPASSRLS role, and this task then cached that EMPTY set.
            db = get_system_session_factory()
            jwks_keys = await _build_jwks(db)
            if not jwks_keys:
                return {"warmed": 0}
            import redis as _redis

            r = _redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
            r.setex("jwks:cache", 600, json.dumps({"keys": jwks_keys}))
            return {"warmed": len(jwks_keys)}
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("warm_jwks_cache_failed: %s", exc)
            return {"error": str(exc)}

    return _run_async(_run())


@celery_app.task(name="app.scaling.tasks.create_guardrail_partitions", queue="maintenance")
def create_guardrail_partitions() -> dict[str, Any]:
    """Pre-create the next few months' partitions for RANGE-partitioned tables.

    Previously this unconditionally returned a static placeholder result —
    scheduled monthly in beat (``create-guardrail-partitions``) since M-1, but
    never actually did anything. Every one of ``_RANGE_PARTITIONED_TABLES`` (cost_ledger,
    audit_events, policy_evaluations, guardrail_violations) is
    ``PARTITION BY RANGE (created_at)`` with only whatever partitions its
    migration happened to pre-create for a fixed calendar window. Once real
    time passes that window, new rows fall through to the DEFAULT partition
    (migration 3f2bbce84e68) — or, before that migration existed, the INSERT
    hard-failed outright. This creates partitions for the current month plus
    the next ``_MONTHS_AHEAD`` months so provisioning always stays well ahead
    of need even if a scheduled run is skipped or delayed, keeping DEFAULT
    empty in the steady state.
    """
    result: dict[str, Any] = _run_async(_ensure_future_partitions())
    _fail_task_on_errors("ensure_future_partitions", result)
    return result


@celery_app.task(name="app.scaling.tasks.enforce_hitl_sla", queue="governance")
def enforce_hitl_sla() -> dict:
    """Escalate / auto-deny pending HITL approvals that breached their SLA.

    Acts on ``approval_requests`` (the table the HITL gateway writes) joined to
    ``approval_sla_configs`` — see app/governance/hitl_sla.py. It used to scan
    ``hitl_approval_requests``, which nothing writes, so it never did anything.
    Cross-tenant beat scan → maintenance (BYPASSRLS) session.
    """

    async def _run() -> dict:
        from app.db.rls import system_session
        from app.db.session import get_system_session_factory
        from app.governance.hitl_sla import enforce_sla

        redis = None
        try:
            import redis.asyncio as aioredis

            redis = aioredis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
        except Exception as exc:
            logger.warning("enforce_hitl_sla_redis_unavailable: %s", exc)
        try:
            db = get_system_session_factory()
            async with db() as session, session.begin(), system_session(session):
                result = await enforce_sla(session, redis=redis)
            return {**result, "enforced": result["auto_denied"] + result["escalated"]}
        except Exception as exc:
            logger.error("enforce_hitl_sla_failed: %s", exc)
            return {"error": str(exc), "enforced": 0}
        finally:
            if redis is not None:
                with contextlib.suppress(Exception):
                    await redis.aclose()

    return _run_async(_run())


@celery_app.task(name="app.scaling.tasks.flush_audit_wal", queue="maintenance")
@beat_task_guard(lock_ttl_seconds=180)
def flush_audit_wal() -> dict:
    """Drain Redis WAL buffer to Postgres audit_events table."""

    async def _run() -> dict:
        try:
            import redis.asyncio as aioredis

            r = aioredis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
            from app.db.session import get_session_factory as _get_fresh_db
            from app.governance.audit_v3 import AuditFlusher

            flusher = AuditFlusher(redis=r, db_factory=_get_fresh_db())
            flushed = await flusher.flush()
            await r.aclose()
            return {"flushed": flushed}
        except Exception as exc:
            return {"error": str(exc), "flushed": 0}

    return _run_async(_run())


@celery_app.task(name="app.scaling.tasks.forward_siem_outbox", queue="maintenance")
@beat_task_guard(lock_ttl_seconds=120)
def forward_siem_outbox() -> dict[str, Any]:
    """Ship due ``audit_siem_outbox`` rows to the configured SIEM (AUDIT-06).

    Covers API and worker audit events alike; failures retry with backoff and
    rows that keep failing are parked as ``dead`` (never dropped).
    """
    from app.governance.siem_outbox import siem_config_from_settings

    config = siem_config_from_settings()
    if config is None:
        return {"status": "skipped", "reason": "no SIEM configured"}

    async def _run() -> dict[str, Any]:
        from app.db.session import get_system_session_factory
        from app.governance.siem_adapters import build_siem_adapter
        from app.governance.siem_outbox import drain_siem_outbox

        adapter = build_siem_adapter(config.siem_type)
        return dict(await drain_siem_outbox(get_system_session_factory(), adapter, config))

    result: dict[str, Any] = _run_async(_run())
    return result


@celery_app.task(
    name="app.scaling.tasks.cancel_goals_for_emergency_stop",
    queue="maintenance",
    bind=True,
    max_retries=5,
)  # type: ignore[untyped-decorator]
def cancel_goals_for_emergency_stop(
    self: Any, tenant_id: str, org_id: str | None = None
) -> dict[str, Any]:
    """Cancel every non-terminal goal of a stopped tenant / org, in keyset batches.

    Enqueued by the emergency-stop endpoints so a tenant with many goals is not
    cancelled inside the HTTP request. Idempotent (re-running skips terminal
    goals); a DB error retries with backoff.
    """

    async def _run() -> dict[str, Any]:
        from app.db.session import get_session_factory
        from app.governance.emergency_stop import cancel_goals_under_stop

        redis = _worker_async_redis()
        try:
            return await cancel_goals_under_stop(get_session_factory(), redis, tenant_id, org_id)
        finally:
            if redis is not None:
                with contextlib.suppress(Exception):
                    await redis.aclose()

    try:
        result: dict[str, Any] = _run_async(_run())
    except Exception as exc:
        logger.warning("emergency_stop_cancel_task_failed: %s", type(exc).__name__)
        raise self.retry(exc=exc, countdown=min(60, 2 ** (self.request.retries + 1))) from exc
    return result


@celery_app.task(name="app.scaling.tasks.scan_cost_anomalies", queue="maintenance")
def scan_cost_anomalies() -> dict:
    """Hourly anomaly scan for all tenants with recent cost activity."""

    async def _run() -> dict:
        try:
            import redis.asyncio as aioredis

            r = aioredis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
            from app.intelligence.cost_tracker import CostTracker

            tracker = CostTracker(redis=r)
            anomalies_found = 0

            # Discover tenants with recent cost activity via Redis key scan
            keys = await r.keys("cost:daily:*")
            tenant_ids: set[str] = set()
            for key in keys:
                parts = key.decode().split(":") if isinstance(key, bytes) else key.split(":")
                if len(parts) >= 3:
                    tenant_ids.add(parts[2])

            for tenant_id in list(tenant_ids)[:50]:  # cap at 50 tenants per run
                try:
                    anomalies = await tracker.detect_anomaly(tenant_id)
                    anomalies_found += len(anomalies)
                except Exception:
                    pass

            await r.aclose()
            return {"tenants_scanned": len(tenant_ids), "anomalies_found": anomalies_found}
        except Exception as exc:
            return {"error": str(exc), "anomalies_found": 0}

    return _run_async(_run())


@celery_app.task(name="app.scaling.tasks.embed_marketplace_templates", queue="maintenance")
def embed_marketplace_templates() -> dict:
    """Embed new unembedded marketplace templates for semantic search."""

    async def _run() -> dict:
        try:
            from sqlalchemy import text

            from app.db.session import get_session_factory as _get_fresh_db

            db = _get_fresh_db()
            async with db() as session:
                result = await session.execute(
                    text("SELECT COUNT(*) FROM marketplace_templates WHERE embedding IS NULL")
                )
                pending = result.scalar() or 0
                return {"status": "ok", "pending_embeddings": pending}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    return _run_async(_run())


@celery_app.task(name="app.scaling.tasks.conclude_stale_experiments", queue="maintenance")
def conclude_stale_experiments() -> dict:
    """Conclude A/B optimization experiments older than 30 days."""

    async def _run() -> dict:
        try:
            from sqlalchemy import text

            from app.db.rls import system_session
            from app.db.session import get_system_session_factory

            # prompt_variants is FORCE RLS and this sweep is cross-tenant: it runs
            # on the maintenance (BYPASSRLS) factory under system_session (which
            # fails loudly on a NOBYPASSRLS role instead of matching no rows).
            # The trial counters are win_count / loss_count (migration 0029);
            # the query used "wins + losses" and failed on every run.
            db = get_system_session_factory()
            async with db() as session, session.begin(), system_session(session):
                result = await session.execute(
                    text(
                        "UPDATE prompt_variants SET updated_at = NOW() "
                        "WHERE updated_at < NOW() - INTERVAL '30 days' "
                        "AND win_count + loss_count >= 20 RETURNING id"
                    )
                )
                concluded = len(result.fetchall())
            return {"status": "ok", "concluded": concluded}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    return _run_async(_run())


@celery_app.task(
    name="app.scaling.tasks.expire_stale_documents",
    queue="maintenance",
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_backoff_max=1800,
    max_retries=3,
)
def expire_stale_documents() -> dict:
    """Expire documents past the retention window, and the graph derived from them."""
    from app.core.config import get_settings

    retention_days = getattr(get_settings(), "data_retention_days", 90)
    return _run_async(_expire_stale_documents(retention_days))


async def _expire_stale_documents(retention_days: int) -> dict:
    """Delete expired document chunks AND the knowledge-graph rows extracted from them.

    Two bugs this replaces:

    1. **The DELETE never ran.** ``documents`` is ENABLE + FORCE ROW LEVEL
       SECURITY, and this task opened a plain session with no RLS context, so
       under any real least-privilege (non-BYPASSRLS) role the statement matched
       zero rows — and the task cheerfully reported ``{"status": "ok",
       "deleted": 0}``. Maintenance tasks in this module are already required to
       use ``system_session`` (see ``_delete_expired_records`` and
       ``tests/scaling/test_maintenance_rls_fix.py``); this one was missed.

    2. **The knowledge graph outlived its sources.** ``knowledge_nodes.source_id``
       holds the ``documents.id`` the node was extracted from, but nothing ever
       removed those nodes or their edges — the ONLY delete path for the KG
       tables was ``KnowledgeGraphStore.delete_tenant_graph``, a whole-tenant
       wipe. So expiring a document left the entities and relations extracted
       from it in place forever: unbounded growth, and — worse — Graph RAG kept
       returning evidence derived from content that retention had already
       deleted, silently defeating the retention policy for anything that
       reached the graph.

    This is not age-expiry of curated knowledge for its own sake: it deletes
    exactly the graph rows whose source document is being deleted by the
    retention policy that already exists, and nothing else.
    """
    try:
        from sqlalchemy import text

        from app.db.rls import system_session
        from app.db.session import get_system_session_factory

        # Fleet-wide retention → maintenance (BYPASSRLS) role.
        db = get_system_session_factory()
        async with db() as session, session.begin(), system_session(session):
            # make_interval(days => :days) rather than interpolating the value
            # into the SQL string.
            expired = (
                await session.execute(
                    text(
                        "DELETE FROM documents "
                        "WHERE created_at < NOW() - make_interval(days => :days)"
                        f"{_TENANT_HOLD_EXEMPT} "
                        "RETURNING id"
                    ),
                    {"days": int(retention_days)},
                )
            ).fetchall()
            doc_ids = [row[0] for row in expired]

            nodes_deleted = 0
            edges_deleted = 0
            if doc_ids:
                node_ids = [
                    row[0]
                    for row in (
                        await session.execute(
                            text(
                                "SELECT id FROM knowledge_nodes "
                                "WHERE source_id = ANY(:ids)"
                            ),
                            {"ids": doc_ids},
                        )
                    ).fetchall()
                ]
                if node_ids:
                    # Edges first: they reference the nodes by id (no FK, so
                    # nothing would cascade for us).
                    edges_deleted = (
                        await session.execute(
                            text(
                                "DELETE FROM knowledge_edges "
                                "WHERE source_node_id = ANY(:ids) "
                                "   OR target_node_id = ANY(:ids)"
                            ),
                            {"ids": node_ids},
                        )
                    ).rowcount
                    nodes_deleted = (
                        await session.execute(
                            text("DELETE FROM knowledge_nodes WHERE id = ANY(:ids)"),
                            {"ids": node_ids},
                        )
                    ).rowcount

        # The legacy ``documents`` table above is never written by
        # KnowledgeStore; the real knowledge lives in knowledge_chunks_<dim>,
        # whose chunks carry their own ``expires_at`` and were never deleted.
        # Bounded batches: maintenance-role scan, per-tenant RLS deletes (plus
        # the collection counters and the graph extracted from each document).
        from app.rag.retention import expire_knowledge_chunks

        knowledge = await expire_knowledge_chunks(system_db=db)

        return {
            "status": "ok",
            "deleted": len(doc_ids),
            "graph_nodes_deleted": nodes_deleted + knowledge["graph_nodes_deleted"],
            "graph_edges_deleted": edges_deleted + knowledge["graph_edges_deleted"],
            "knowledge_chunks_expired": knowledge["knowledge_chunks_expired"],
            "knowledge_documents_expired": knowledge["knowledge_documents_expired"],
        }
    except Exception:
        # KB-55: never swallowed into a returned dict (Celery recorded that as a
        # success and nothing was logged). Logged, counted, re-raised: the task
        # fails, autoretries with backoff, and celery_task_failed_total feeds the
        # KnowledgeRetentionFailing alert.
        logger.exception("knowledge_retention_failed", retention_days=retention_days)
        with contextlib.suppress(Exception):
            from app.observability.metrics import KNOWLEDGE_FAILURE_TOTAL

            KNOWLEDGE_FAILURE_TOTAL.labels("retention", "expire_stale_documents").inc()
        raise


# Erasure jobs that cannot be claimed again for this long are assumed to belong
# to a worker that died mid-run and are reclaimed (erasure is idempotent).
_ERASURE_RECLAIM_AFTER = "1 hour"
_STALE_CLAIM_SQL = (
    f"(claimed_at IS NULL OR claimed_at < now() - interval '{_ERASURE_RECLAIM_AFTER}')"
)
# Genuine failures (not legal holds) are retried up to this many times, then left
# in 'failed' for an operator (the status endpoint shows last_error).
_TENANT_ERASURE_MAX_ATTEMPTS = 10


async def _process_dpdp_erasures_async(
    *, db: Any = None, system_db: Any = None, batch: int = 50
) -> dict[str, Any]:
    """Claim and execute due DPDP erasure requests.

    ``dpdp_erasure_requests`` is FORCE-RLS. The previous version scanned it on
    the application session with no tenant context, so the policy hid every row
    and the task "processed" 0 requests forever (it was also never scheduled).
    Now: the cross-tenant SCAN + CLAIM runs as the maintenance role
    (``system_session``); each erasure and its status update run on the app role
    inside that request's tenant RLS context.
    """
    from datetime import UTC, datetime

    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context, system_session
    from app.db.session import get_session_factory, get_system_session_factory
    from app.governance.audit_v3 import AuditV3
    from app.lifecycle.deletion_orchestrator import DeletionOrchestrator

    db = db or get_session_factory()
    system_db = system_db or get_system_session_factory()

    # 1. ATOMIC CLAIM (multi-replica): SKIP LOCKED + status flip in one txn, so no
    # two workers run the same request; a stale 'processing' claim is reclaimed.
    async with system_db() as s, s.begin(), system_session(s):
        rows = (
            await s.execute(
                text(
                    "SELECT id, tenant_id, data_principal_id FROM dpdp_erasure_requests "
                    "WHERE status = 'pending' OR (status = 'processing' AND "
                    f"{_STALE_CLAIM_SQL}) "
                    "ORDER BY requested_at LIMIT :lim FOR UPDATE SKIP LOCKED"
                ),
                {"lim": batch},
            )
        ).fetchall()
        if rows:
            await s.execute(
                text(
                    "UPDATE dpdp_erasure_requests SET status = 'processing', claimed_at = now() "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": [r[0] for r in rows]},
            )

    orchestrator = DeletionOrchestrator(db_factory=db, audit=AuditV3(db_factory=db))
    processed = 0
    failed = 0
    for req_id, tenant_id, dpid in rows:
        try:
            # Real, verifiable erasure cascade (suspends on active legal hold).
            receipt = await orchestrator.execute_deletion(tenant_id, dpid)
            # A residue-positive run (a concurrent writer re-created a row after
            # its store's DELETE pass — see verify_deleted) must not read as
            # "completed"; surface it as its own status.
            if receipt.suspended:
                new_status = "suspended"
            elif not receipt.verified:
                new_status = "verification_failed"
                logger.warning(
                    "dpdp_erasure_residue_detected",
                    req_id=req_id,
                    tenant_id=tenant_id,
                    residue=receipt.residue,
                )
            else:
                new_status = "completed"
        except Exception as exc:
            # Back to 'pending' so the next run retries it.
            logger.warning("dpdp_erasure_failed", req_id=req_id, error=str(exc))
            new_status = "pending"
            failed += 1
        try:
            async with db() as s2, s2.begin(), sqlalchemy_rls_context(s2, tenant_id):
                await s2.execute(
                    text(
                        "UPDATE dpdp_erasure_requests SET status = :st, claimed_at = NULL, "
                        "completed_at = CASE WHEN :done THEN NOW() ELSE NULL END "
                        "WHERE id = :rid AND tenant_id = :tid"
                    ),
                    {
                        "st": new_status,
                        "done": new_status != "pending",
                        "rid": req_id,
                        "tid": tenant_id,
                    },
                )
        except Exception as exc:
            # Left 'processing'; reclaimed after _ERASURE_RECLAIM_AFTER.
            logger.error("dpdp_erasure_status_update_failed", req_id=req_id, error=str(exc))
            failed += 1
            continue
        if new_status != "pending":
            processed += 1
    return {
        "status": "ok",
        "processed": processed,
        "failed": failed,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@celery_app.task(name="agentverse.process_dpdp_erasures", bind=True, max_retries=3)
def process_dpdp_erasures(self: Any) -> dict:
    """Process pending DPDP erasure requests (beat: ``process-dpdp-erasures``)."""
    try:
        return _run_async(_process_dpdp_erasures_async())  # type: ignore[no-any-return]
    except Exception as exc:
        logger.error("process_dpdp_erasures_failed", error=str(exc))
        return {"status": "error", "error": str(exc)}


async def _process_tenant_erasures_async(
    *, db: Any = None, system_db: Any = None, redis: Any = None, batch: int = 10
) -> dict[str, Any]:
    """Execute due GDPR tenant erasures (``deleted_tenants`` job rows).

    ``POST /enterprise/compliance/delete`` records the job; before this task
    nothing ever called ``execute_data_deletion_async``, so the API promised an
    erasure that never happened. Scan + claim + status bookkeeping run as the
    maintenance role (the table deliberately has no tenant UPDATE policy); the
    erasure itself runs on the app role under the tenant's RLS context. An
    active legal hold leaves the job 'on_hold' (re-checked every run, never
    deleted); a genuine failure leaves it 'failed' with last_error and is
    retried up to ``_TENANT_ERASURE_MAX_ATTEMPTS`` times.
    """
    import json as _json
    from datetime import UTC, datetime

    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context, system_session
    from app.db.session import get_session_factory, get_system_session_factory
    from app.enterprise.compliance import ComplianceController
    from app.tenancy.context import PlanTier, TenantContext

    db = db or get_session_factory()
    system_db = system_db or get_system_session_factory()

    async with system_db() as s, s.begin(), system_session(s):
        claimed = [
            r[0]
            for r in (
                await s.execute(
                    text(
                        "SELECT tenant_id FROM deleted_tenants "
                        "WHERE scheduled_for <= now() AND attempts < :max_attempts AND ("
                        "status IN ('pending', 'on_hold', 'failed') OR (status = 'processing' "
                        f"AND {_STALE_CLAIM_SQL})) "
                        "ORDER BY scheduled_for LIMIT :lim FOR UPDATE SKIP LOCKED"
                    ),
                    {"lim": batch, "max_attempts": _TENANT_ERASURE_MAX_ATTEMPTS},
                )
            ).fetchall()
        ]
        if claimed:
            await s.execute(
                text(
                    "UPDATE deleted_tenants SET status = 'processing', claimed_at = now() "
                    "WHERE tenant_id = ANY(:ids)"
                ),
                {"ids": claimed},
            )

    controller = ComplianceController()
    counts = {"completed": 0, "on_hold": 0, "failed": 0}
    for tenant_id in claimed:
        result: dict[str, Any] = {}
        error: str | None = None
        key_hashes: list[str] = []
        try:
            # api_keys is FORCE-RLS: read the hashes (for cache invalidation) in
            # the tenant's context before the erasure removes the rows.
            async with db() as s2, s2.begin(), sqlalchemy_rls_context(s2, tenant_id):
                key_hashes = [
                    str(r[0])
                    for r in (
                        await s2.execute(
                            text("SELECT key_hash FROM api_keys WHERE tenant_id = :tid"),
                            {"tid": tenant_id},
                        )
                    ).fetchall()
                ]
            result = await controller.execute_data_deletion_async(
                tenant_ctx=TenantContext(
                    tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="gdpr-erasure-job"
                ),
                db=db,
            )
            if result.get("blocked") == "legal_hold":
                status = "on_hold"
            elif result.get("complete"):
                status = "completed"
            else:
                status = "failed"
                error = "tables failed: " + ", ".join(result.get("failed_tables", []))
        except Exception as exc:
            status, error = "failed", str(exc)[:2000]
        counts[status] += 1
        try:
            async with system_db() as s3, s3.begin(), system_session(s3):
                await s3.execute(
                    text(
                        "UPDATE deleted_tenants SET status = :st, claimed_at = NULL, "
                        "last_error = :err, result = CAST(:res AS jsonb), "
                        "attempts = attempts + :inc, "
                        "completed_at = CASE WHEN :done THEN now() ELSE NULL END "
                        "WHERE tenant_id = :tid"
                    ),
                    {
                        "st": status,
                        "inc": 1 if status == "failed" else 0,
                        "done": status == "completed",
                        "err": error,
                        "res": _json.dumps(result, default=str),
                        "tid": tenant_id,
                    },
                )
        except Exception as exc:
            # Left 'processing'; reclaimed (and, being idempotent, re-run) later.
            logger.error(
                "tenant_erasure_status_update_failed", tenant_id=tenant_id, error=str(exc)
            )
        if status == "completed":
            # The tenant's keys no longer exist in the DB; drop the shared resolution
            # caches so no replica keeps authenticating them for the 300 s TTL.
            try:
                r = redis if redis is not None else _get_sync_redis()
                keys = [f"tenant:{tenant_id}", *(f"api_key:{h}" for h in key_hashes)]
                maybe = r.delete(*keys)
                if asyncio.iscoroutine(maybe):
                    await maybe
            except Exception as exc:
                logger.warning(
                    "tenant_erasure_cache_invalidation_failed", tenant_id=tenant_id, error=str(exc)
                )
    return {"status": "ok", **counts, "timestamp": datetime.now(UTC).isoformat()}


@celery_app.task(
    name="agentverse.maintenance.process_tenant_erasures",
    queue="maintenance",
    bind=True,
    max_retries=0,
)
def process_tenant_erasures(self: Any) -> dict:
    """Execute due GDPR tenant erasures (beat: ``process-tenant-erasures``)."""
    try:
        return _run_async(_process_tenant_erasures_async())  # type: ignore[no-any-return]
    except Exception as exc:
        logger.error("process_tenant_erasures_failed", error=str(exc))
        return {"status": "error", "error": str(exc)}


@celery_app.task(name="app.scaling.tasks.discover_and_tick_civilizations", queue="maintenance")
def discover_and_tick_civilizations() -> dict:
    """Discover all active civilizations and enqueue tick tasks for each."""

    async def _run() -> dict:
        from sqlalchemy import text

        from app.db.rls import system_session
        from app.db.session import get_system_session_factory

        # Cross-tenant scan: ``civilizations`` is FORCE-RLS, so a plain app-role
        # session with no tenant GUC saw zero rows and no civilization ever
        # ticked. Run it as the BYPASSRLS maintenance role; each tick then works
        # under its own tenant's RLS context.
        system_db = get_system_session_factory()
        async with system_db() as session, session.begin(), system_session(session):
            rows = (
                await session.execute(
                    text("SELECT id, tenant_id FROM civilizations WHERE status = 'active'")
                )
            ).fetchall()

        # Back-pressure: at most one queued tick per civilization, and a tick that
        # waited longer than one discovery interval expires unexecuted. Without it
        # the maintenance queue grew without bound (2,699 civilizations every 30 s
        # reached 3.17M messages / 4 GB and got Redis OOM-killed).
        redis_client = None
        try:
            redis_client = _get_sync_redis()
        except Exception as exc:
            logger.warning("civilization_tick_pending_marker_unavailable", error=str(exc))
        count = skipped = 0
        for civ_id, tenant_id in rows:
            if redis_client is not None:
                try:
                    claimed = redis_client.set(
                        _civ_tick_pending_key(civ_id), "1", nx=True, ex=_CIV_TICK_PENDING_TTL_S
                    )
                except Exception:
                    claimed = True  # marker store down: fall back to expiry alone
                if not claimed:
                    skipped += 1
                    continue
            civilization_tick.apply_async(
                args=[civ_id, tenant_id], expires=_CIV_TICK_EXPIRES_S
            )
            count += 1
        return {"civilizations_ticked": count, "skipped_pending": skipped}

    try:
        return cast("dict[Any, Any]", _run_async(_run()))
    except Exception as exc:
        # Fail the task (it used to return {"error": ...}, which Celery records
        # as a success, so a broken scan was invisible).
        logger.error("civilization_discovery_failed", error=str(exc))
        raise


async def re_embed_collection_async(
    tenant_id: str,
    collection_id: str,
    model_key: str | None = None,
    job_id: str | None = None,
) -> dict:
    """Re-embed every chunk of a collection with the deployment's embedder (KB-25).

    The embedder is built here from :func:`resolve_embedder` — the ONE embedder
    queries are embedded with. (This used the global ``embedding_router``, whose
    provider is never set in the worker, and refused any dimension change.) The
    job itself lives in :mod:`app.rag.reembed`: same-dimension rewrites happen in
    place; a new dimension moves the rows to the matching chunk table and flips
    the collection only once every row is re-embedded.

    ``model_key`` is an optional guard: when given it must name the configured
    embedder, otherwise the run is refused (vectors from another model would not
    match query vectors). Progress is written to Redis for
    ``GET /knowledge/collections/{id}/re-embed``; completion publishes
    ``knowledge.updated``. Returns ``{"error": ...}`` on failure — the Celery
    entry point turns that into a failed task.
    """
    import json

    import redis.asyncio as aioredis

    from app.db.session import get_session_factory as _get_fresh_db
    from app.providers.base import EmbedRequest
    from app.providers.embedder_factory import resolve_embedder
    from app.rag import reembed

    job = job_id or uuid.uuid4().hex
    redis_client: Any = None
    try:
        redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    except Exception as exc:
        logger.warning("re_embed_progress_redis_unavailable", error=str(exc))
    progress = reembed.ReembedProgress(redis_client, tenant_id, collection_id, job)
    embedder: Any = None
    try:
        resolution = resolve_embedder()
        embedder = resolution.embedder
        if embedder is None:
            raise reembed.ReembedError(f"no embedding provider: {resolution.reason()}")
        resolved_key = f"{resolution.provider}/{resolution.model}"
        if model_key and model_key not in (resolved_key, resolution.model):
            raise reembed.ReembedError(
                f"requested model {model_key!r} is not the configured embedder "
                f"({resolved_key}); re-embedding with it would not match query vectors"
            )

        from app.embedding.metering import embed_metered
        from app.tenancy.context import PlanTier, TenantContext

        tenant_ctx = TenantContext(
            tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="re-embed"
        )

        async def _embed_once(texts: list[str]) -> list[list[float]]:
            response = await embedder.embed(EmbedRequest(texts=texts))
            return [list(vec) for vec in response.embeddings]

        batch_seq = itertools.count()

        async def _embed(texts: list[str]) -> list[list[float]]:
            # KB-49: every batch is reserved against the tenant's budget (the
            # worker's registered controller) before it is sent and recorded as
            # embedding usage. A refusal raises, failing the job; a dimension
            # move then never flips, so the collection stays on its old table.
            return await embed_metered(
                texts,
                _embed_once,
                tenant_ctx=tenant_ctx,
                model=resolved_key,
                operation_id=f"{job}:{next(batch_seq)}",
                label="re-embed",
                batch_size=max(len(texts), 1),
            )

        result = await reembed.re_embed_collection(
            db=_get_fresh_db(),
            embed=_embed,
            model_key=resolved_key,
            tenant_id=tenant_id,
            collection_id=collection_id,
            progress=progress,
        )
        await progress.update(
            status="completed",
            finished_at=datetime.datetime.now(UTC).isoformat(),
            processed=result["re_embedded"],
            dimension=result["dimension"],
            previous_dimension=result["previous_dimension"],
        )
        if redis_client is not None:
            try:
                await redis_client.publish(
                    "knowledge.updated",
                    json.dumps(
                        {
                            "event": "collection_re_embedded",
                            "tenant_id": tenant_id,
                            "collection_id": collection_id,
                            "chunks_re_embedded": result["re_embedded"],
                            "model": result["model"],
                            "dimension": result["dimension"],
                        }
                    ),
                )
            except Exception as exc:
                logger.warning("re_embed_publish_failed", error=str(exc))
        return {**result, "job_id": job}
    except Exception as exc:
        logger.error(
            "re_embed_collection_failed",
            collection_id=collection_id,
            tenant_id=tenant_id,
            error=str(exc),
        )
        await progress.update(
            status="failed",
            error=str(exc)[:500],
            finished_at=datetime.datetime.now(UTC).isoformat(),
        )
        return {
            "error": str(exc),
            "collection_id": collection_id,
            "re_embedded": 0,
            "job_id": job,
        }
    finally:
        if redis_client is not None:
            with contextlib.suppress(Exception):
                await reembed.release_lock(redis_client, tenant_id, collection_id, job)
            with contextlib.suppress(Exception):
                await redis_client.aclose()
        close = getattr(embedder, "aclose", None)
        if close is not None:
            with contextlib.suppress(Exception):
                await close()


@celery_app.task(name="app.scaling.tasks.re_embed_collection", queue="maintenance")
def re_embed_collection(
    tenant_id: str,
    collection_id: str,
    model_key: str | None = None,
    job_id: str | None = None,
) -> dict:
    """Celery entry point for :func:`re_embed_collection_async`.

    A failed re-embed fails the task (it used to return an error dict that
    Celery recorded as a success).
    """
    result = _run_async(re_embed_collection_async(tenant_id, collection_id, model_key, job_id))
    if "error" in result:
        raise RuntimeError(f"re-embed of collection {collection_id} failed: {result['error']}")
    return cast("dict[Any, Any]", result)


# ---------------------------------------------------------------------------
# Daily feedback processing — reads goal_feedback and derives lessons
# ---------------------------------------------------------------------------


@celery_app.task(  # type: ignore[untyped-decorator]
    name="agentverse.maintenance.process_feedback_batch",
    queue="maintenance",
    bind=True,
    max_retries=1,
)
def process_feedback_batch(self: Any) -> dict[str, Any]:  # type: ignore[misc]
    """Read unprocessed goal_feedback rows and store improvement lessons.

    Scheduled daily. Idempotent — already-processed rows are marked and skipped.
    """
    try:
        return _run_async(_process_feedback_batch_async())  # type: ignore[no-any-return]
    except Exception as exc:
        return {"error": str(exc)}


async def _process_feedback_batch_async(
    *, db: Any = None, system_db: Any = None
) -> dict[str, Any]:
    """``goal_feedback`` is FORCE-RLS. The tenant scan used to run on a raw app
    engine with no tenant context, so the policy hid every row: 0 tenants, 0
    feedback processed, forever (and the ad-hoc engine was never disposed). The
    cross-tenant DISTINCT scan now runs as the maintenance role; each tenant's
    batch runs on the app role under that tenant's RLS context (inside
    ``SelfImprovementEngine.process_feedback_batch``)."""
    from sqlalchemy import text

    from app.db.rls import system_session
    from app.db.session import get_session_factory, get_system_session_factory
    from app.evals.self_improvement_engine import SelfImprovementEngine

    db = db or get_session_factory()
    system_db = system_db or get_system_session_factory()
    async with system_db() as session, session.begin(), system_session(session):
        rows = (
            await session.execute(
                text(
                    "SELECT DISTINCT tenant_id FROM goal_feedback "
                    "WHERE processed_at IS NULL LIMIT 500"
                )
            )
        ).fetchall()
    tenant_ids = [str(r[0]) for r in rows]

    total_processed = 0
    total_actions = 0
    engine_svc = SelfImprovementEngine()
    # Lessons get the platform's shared embedder (same selection as the API),
    # so they are found by semantic recall, not only by keyword.
    embedder: Any = None
    try:
        from app.providers.embedder_factory import build_query_embedder

        embedder = build_query_embedder()
    except Exception as exc:
        logger.warning("feedback_lesson_embedder_unavailable", error=str(exc)[:200])
    for tid in tenant_ids:
        result = await engine_svc.process_feedback_batch(
            db_session_factory=db, tenant_id=tid, embedder=embedder
        )
        total_processed += result.get("processed", 0)
        total_actions += result.get("actions_derived", 0)
    return {"processed": total_processed, "actions_derived": total_actions}


# Register beat schedule for feedback processing
with contextlib.suppress(Exception):
    celery_app.conf.beat_schedule["process-feedback-daily"] = {
        "task": "agentverse.maintenance.process_feedback_batch",
        "schedule": 86400.0,  # Every 24 hours
        "options": {"queue": "maintenance"},
    }


# ---------------------------------------------------------------------------
# Delta re-ingest — re-ingests files from configured sources when triggered
# ---------------------------------------------------------------------------


def _webhook_reingest_lock_ttl() -> int:
    """TTL of the webhook re-ingest's sync lock (renewed every third of it)."""
    from app.core.config import get_settings

    return max(5, int(get_settings().ingestion_sync_lock_ttl_seconds))


@celery_app.task(  # type: ignore[untyped-decorator]
    name="agentverse.maintenance.delta_reingest_files",
    queue="maintenance",
    bind=True,
    max_retries=2,
)
def delta_reingest_files(
    self: Any,
    tenant_id: str,
    collection_id: str,
    source_type: str,
    source_config: dict[str, Any],
) -> dict[str, Any]:  # type: ignore[misc]
    """Trigger delta re-ingestion for a collection from an external source.

    Called by webhook handlers when a source signals new/updated content.
    source_type: any registered connector (e.g. 'github', 'confluence',
    'notion', 'gdrive', 'sharepoint').
    source_config: connector connection_config (api_key, folder_id, etc.)

    Honesty (WS-12): routes through the REAL registered connector +
    ``IngestionPipeline`` and reports the actual indexed/skipped/failed tallies.
    An unknown source_type returns an explicit ``unsupported`` status — never a
    fabricated success count.
    """

    async def _run() -> dict[str, Any]:
        from app.ingestion.connector_registry import get_connector, load_all_connectors
        from app.ingestion.source_config import SourceConfig, SourceFamily

        load_all_connectors()
        try:
            connector_cls = get_connector(source_type)
        except KeyError:
            return {
                "status": "unsupported",
                "source_type": source_type,
                "reason": f"no registered connector for source_type={source_type!r}",
                "tenant_id": tenant_id,
                "collection_id": collection_id,
            }

        family_map = {
            "notion": SourceFamily.DOCUMENT_STORE,
            "gdrive": SourceFamily.DOCUMENT_STORE,
            "sharepoint": SourceFamily.DOCUMENT_STORE,
            "confluence": SourceFamily.DOCUMENT_STORE,
            "github": SourceFamily.CODE_REPOSITORY,
            "gitlab": SourceFamily.CODE_REPOSITORY,
        }
        config = SourceConfig(
            source_id=f"webhook-{source_type}",
            tenant_id=tenant_id,
            name=f"{source_type} delta re-ingest",
            family=family_map.get(source_type, SourceFamily.WEB),
            source_type=source_type,
            connection_config=source_config,
            collection_id=collection_id,
        )
        connector = connector_cls()
        # A bare ``IngestionPipeline()`` has no knowledge store / embedder, so
        # every document was skipped (``no_embedder``) while this reported
        # ``status: ok``. Use the worker's fully-wired pipeline (store, embedder,
        # PII, quota) like the scheduled sync does.
        from app.ingestion.job_tracker import SyncLockLostError, SyncLockUnavailableError
        from app.ingestion.scheduler import _build_worker_ingestion

        tracker, pipeline, _store = _build_worker_ingestion()
        base = {"source_type": source_type, "tenant_id": tenant_id, "collection_id": collection_id}

        # NF-18: the same shared (Redis, cross-process) per-source lock every
        # sync takes — a burst of webhooks used to run full re-reads of the same
        # source concurrently on every worker. Fail closed when it cannot be
        # checked; skip (honestly) while another run holds it.
        try:
            lease = await tracker.hold_without_fence(
                config.source_id, tenant_id, ttl_seconds=_webhook_reingest_lock_ttl()
            )
        except SyncLockUnavailableError as exc:
            return {**base, "status": "error", "error": f"sync lock unavailable: {exc}"[:300]}
        if lease is None:
            return {**base, "status": "skipped", "reason": "already_running"}

        indexed = skipped = failed = 0
        try:
            async for raw_doc, _cursor in connector.get_delta(config, None):
                lease.check()  # stop at once if this run lost the lock
                result = await pipeline.ingest(raw_doc, config)
                if result.success:
                    indexed += 1
                elif result.skipped:
                    skipped += 1
                else:
                    failed += 1
            lease.check()
        except SyncLockLostError as exc:
            return {
                **base,
                "status": "error",
                "reason": "lock_lost",
                "error": str(exc)[:300],
                "docs_indexed": indexed,
                "docs_skipped": skipped,
                "docs_failed": failed,
            }
        except Exception as exc:
            return {
                "status": "error",
                "source_type": source_type,
                "error": str(exc)[:300],
                "docs_indexed": indexed,
                "docs_skipped": skipped,
                "docs_failed": failed,
                "tenant_id": tenant_id,
                "collection_id": collection_id,
            }
        finally:
            await lease.release()
        return {
            "status": "ok",
            "source_type": source_type,
            "docs_indexed": indexed,
            "docs_skipped": skipped,
            "docs_failed": failed,
            "tenant_id": tenant_id,
            "collection_id": collection_id,
        }

    return _run_async(_run())


# ── N8: Org Autonomous Operating Loop (runs every 5 min via Celery Beat) ─────

_BRAIN_TICK_ZERO: dict[str, int] = {"proposed": 0, "executed": 0, "blocked": 0}


async def _brain_tick_for_org(
    *,
    db_factory: Any,
    redis: Any,
    org_id: Any,
    tenant_id: Any,
    autonomy_level: int,
) -> dict[str, int]:
    """Run one ``OrgBrain`` SENSE/DECIDE/GUARD/ACT/NARRATE tick for a single org.

    Short-circuits (returns ``{"proposed": 0, "executed": 0, "blocked": 0}``
    without touching the DB or Redis beyond the lock check) when:
      * the ``org_autonomy_enabled`` feature flag is off for this tenant, or
      * the org's autonomy level is below 3, or
      * the org's per-tick Redis lock (``BrainCounters.acquire_tick_lock``)
        is already held (a concurrent beat/worker run is mid-tick).

    Otherwise builds the real ``OrgBrain`` dependencies inside an RLS-scoped
    session (mirroring the health-check block this replaces) and runs the
    tick. ``session.begin()`` commits automatically on clean exit, so the
    decision rows (``BrainDecisionStore``) and any created/executed missions
    persist without an explicit ``session.commit()``.
    """
    from app.org.brain_counters import BrainCounters

    if not is_feature_enabled("org_autonomy_enabled", str(tenant_id)):
        return dict(_BRAIN_TICK_ZERO)
    if int(autonomy_level) < 3:
        return dict(_BRAIN_TICK_ZERO)

    counters = BrainCounters(redis, str(org_id))
    if not await counters.acquire_tick_lock():
        return dict(_BRAIN_TICK_ZERO)

    from app.db.rls import sqlalchemy_rls_context
    from app.org.autonomy import AutonomyEnforcer
    from app.org.brain import OrgBrain
    from app.org.brain_planner import make_planner
    from app.org.brain_store import BrainDecisionStore
    from app.org.goal_refinement import GoalRefinementPipeline
    from app.org.loop_detector import OrgLoopDetector
    from app.org.service import OrgService

    # Buffer dispatches raised during the tick instead of firing them inline.
    # ``execute_org_mission.apply_async`` publishes to the Celery broker
    # immediately; a fast worker can then run its ``SELECT ... FOR UPDATE``
    # claim (see ``execute_org_mission`` above) before THIS tick's outer
    # transaction (below) has committed the mission row, hit
    # ``mission_missing``, and no-op — silently deferring the mission to the
    # 3-minute ``resweep_stuck_missions`` fallback. Buffering here and
    # flushing only after the ``async with`` block exits (i.e. only on a
    # successful commit) mirrors the reference pattern in
    # ``fire_due_org_mission_schedules``: "create + commit the mission ...
    # then dispatch after commit".
    _pending_dispatch: list[dict[str, Any]] = []

    async with (
        db_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, str(tenant_id)),
    ):
        svc = OrgService(session, str(tenant_id))
        org = await svc.get_organization(str(org_id))
        if org is None:
            return dict(_BRAIN_TICK_ZERO)

        brain = OrgBrain(
            org_service=svc,
            brain_store=BrainDecisionStore(session),
            counters=counters,
            enforcer=AutonomyEnforcer(),
            loop_detector=OrgLoopDetector(),
            planner=make_planner(GoalRefinementPipeline()),
            # Dispatch autonomous "execute" missions on the SAME worker-wired
            # path the SCHEDULE path (``fire_due_org_mission_schedules``) and
            # the crash-recovery resweep (``resweep_stuck_missions``) use —
            # ``execute_org_mission`` builds its own GoalService/app_state
            # inside the worker, so the mission actually runs instead of
            # sitting at "planned" until the 3-minute resweep catches it.
            # NOTE: this only buffers the kwargs — it must NOT publish to the
            # broker here, since we're still inside the uncommitted outer
            # transaction. See the flush after this ``async with`` block.
            dispatcher=lambda kw: _pending_dispatch.append(kw),
        )
        result = await brain.run_tick(
            org_id=str(org_id),
            tenant_id=str(tenant_id),
            autonomy_level=int(autonomy_level),
            org_settings=dict(org.settings or {}),
            monthly_budget_usd=float(org.monthly_budget_usd or 0.0),
            org_goals=list(org.goals or []),
            org_mission=str(org.mission or ""),
        )

    # Only reached on a clean (committed) exit of the block above — an
    # exception propagates out of the ``async with`` and skips this flush,
    # so a rolled-back tick never dispatches a mission that doesn't exist.
    for _kw in _pending_dispatch:
        execute_org_mission.apply_async(kwargs=_kw)
    return result


def _org_loop_factories() -> tuple[Any, Any]:
    """(tenant session factory, BYPASSRLS maintenance session factory)."""
    from app.db.session import get_session_factory, get_system_session_factory

    return get_session_factory(), get_system_session_factory()


async def _active_orgs_for_maintenance(system_db: Any, *, limit: int = 100) -> list[Any]:
    """Cross-tenant scan of active orgs, as the maintenance role.

    ``organizations`` is FORCE-RLS: a plain app-role session with no tenant GUC
    sees zero rows, so the scan must run under :func:`system_session`.
    """
    from sqlalchemy import select

    from app.db.rls import system_session
    from app.org.models import Organization

    async with system_db() as session, session.begin(), system_session(session):
        result = await session.execute(
            select(Organization.id, Organization.tenant_id, Organization.autonomy_level)
            .where(Organization.status == "active")
            .limit(limit)
        )
        return list(result.all())


def _worker_llm_provider() -> Any:
    """The worker's real LLM provider, or None (never the no-key FakeProvider)."""
    try:
        from app.providers.fake import FakeProvider
        from app.providers.registry import resolve_provider

        provider = resolve_provider()
    except Exception as exc:
        logger.warning("worker_llm_provider_unavailable", error=str(exc)[:160])
        return None
    return None if isinstance(provider, FakeProvider) else provider


@celery_app.task(name="app.scaling.tasks.org_brain_loop", queue="maintenance")
def org_brain_loop() -> dict[str, int]:
    """N8 — Autonomous Operating Loop: SENSE → DECIDE → GUARD → ACT → NARRATE.

    Runs every 5 minutes via Celery Beat. Drives ``OrgBrain.run_tick`` (via
    ``_brain_tick_for_org``) for every active org, gated by the
    ``org_autonomy_enabled`` feature flag, autonomy level (L3+), and a
    per-org Redis tick lock.
    """

    async def _run() -> dict[str, int]:
        import structlog as _slog
        from opentelemetry import trace as _trace

        _log = _slog.get_logger(__name__)
        tracer = _trace.get_tracer(__name__)

        with tracer.start_as_current_span("org_brain.autonomous_loop") as span:
            processed = 0
            triggered = 0
            proposed_total = 0
            executed_total = 0
            blocked_total = 0
            try:
                # This read ``app.main.app.state.db_factory``, which nothing sets
                # (and the worker has no lifespan), so every tick returned zero.
                # The cross-tenant org scan runs as the BYPASSRLS maintenance
                # role; each org's tick runs under that tenant's RLS context.
                db_factory, system_db = _org_loop_factories()
                orgs = await _active_orgs_for_maintenance(system_db)

                import redis.asyncio as _aioredis

                redis = _aioredis.from_url(REDIS_URL, decode_responses=True)
                try:
                    for org_id, tenant_id, autonomy_level in orgs:
                        processed += 1
                        try:
                            tick_result = await _brain_tick_for_org(
                                db_factory=db_factory,
                                redis=redis,
                                org_id=org_id,
                                tenant_id=tenant_id,
                                autonomy_level=autonomy_level,
                            )
                            proposed_total += tick_result.get("proposed", 0)
                            executed_total += tick_result.get("executed", 0)
                            blocked_total += tick_result.get("blocked", 0)
                            if tick_result.get("executed", 0) or tick_result.get("proposed", 0):
                                triggered += 1
                                _log.info(
                                    "org_brain.work_triggered",
                                    org_id=str(org_id),
                                    tenant_id=str(tenant_id),
                                    **tick_result,
                                )
                        except Exception as exc:
                            _log.warning(
                                "org_brain.org_error", org_id=str(org_id), error=str(exc)
                            )
                finally:
                    await redis.aclose()

                span.set_attribute("orgs_processed", processed)
                span.set_attribute("triggered", triggered)
                span.set_attribute("proposed", proposed_total)
                span.set_attribute("executed", executed_total)
                span.set_attribute("blocked", blocked_total)
                _log.info("org_brain.loop_done", processed=processed, triggered=triggered)
            except Exception as exc:
                # Re-raise: a failed org scan (e.g. the maintenance role lacks
                # BYPASSRLS) must fail the run, not report all-zero totals that
                # are indistinguishable from "no active orgs". Per-org errors
                # are caught above and never reach here.
                logger.error("org_brain.loop_failed", error=str(exc))
                raise
        return {
            "processed": processed,
            "triggered": triggered,
            "proposed": proposed_total,
            "executed": executed_total,
            "blocked": blocked_total,
        }

    return _run_async(_run())


# ── Task 9: Ambient Collaboration Tick ("the team talks") ───────────────────
# Runs every 15 minutes via Celery Beat, independently of ``org_brain_loop``.
# Emits capped, low-cost "status chatter" org events from department leads
# for active L3+ orgs that opted into ``collaboration_enabled``.

_COLLABORATION_LEADS_QUERY_LIMIT = 8


async def _collaboration_tick_for_org(
    *,
    db_factory: Any,
    redis: Any,
    llm_provider: Any,
    org_id: Any,
    tenant_id: Any,
    autonomy_level: int,
) -> int:
    """Run one ``CollaborationTick`` for a single org. Returns messages emitted.

    Short-circuits to ``0`` without touching the DB when:
      * the ``org_autonomy_enabled`` feature flag is off for this tenant,
      * the org's autonomy level is below 3, or
      * no real ``llm_provider`` is wired into this worker process (fail
        closed rather than emit chatter with no model behind it — see the
        Task 9 report for how this is resolved from ``app.state``).

    Otherwise resolves the org's ``AutonomySettings`` (which also gates on
    ``collaboration_enabled``), derives a small, cheap ``leads`` list from
    the org's active departments (department lead agent, else department
    name — capped at ``_COLLABORATION_LEADS_QUERY_LIMIT``), and delegates to
    ``CollaborationTick.run``.
    """
    from app.org.brain_counters import BrainCounters

    if not is_feature_enabled("org_autonomy_enabled", str(tenant_id)):
        return 0
    if int(autonomy_level) < 3:
        return 0
    if llm_provider is None:
        return 0

    counters = BrainCounters(redis, str(org_id))
    day_spend_usd, _count, _since = await counters.snapshot()

    from sqlalchemy import select

    from app.db.rls import sqlalchemy_rls_context
    from app.org.brain_collaboration import CollaborationTick, LLMProviderCollaborationGateway
    from app.org.brain_settings import resolve_autonomy_settings
    from app.org.events import OrgEventPublisher, get_org_event_publisher
    from app.org.model_gateway import get_gateway
    from app.org.models import OrgDepartment
    from app.org.service import OrgService

    async with (
        db_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, str(tenant_id)),
    ):
        svc = OrgService(session, str(tenant_id))
        org = await svc.get_organization(str(org_id))
        if org is None:
            return 0

        settings = resolve_autonomy_settings(
            dict(org.settings or {}), float(org.monthly_budget_usd or 0.0)
        )
        if not settings.collaboration_enabled:
            return 0

        dept_rows = await session.execute(
            select(OrgDepartment.name, OrgDepartment.manager_agent_id)
            .where(OrgDepartment.org_id == org_id, OrgDepartment.status == "active")
            .limit(_COLLABORATION_LEADS_QUERY_LIMIT)
        )
        leads: list[str] = []
        for name, manager_agent_id in dept_rows.all():
            lead = str(manager_agent_id or name or "").strip()
            if lead and lead not in leads:
                leads.append(lead)
        if not leads:
            return 0

        publisher: OrgEventPublisher = get_org_event_publisher()
        gateway = LLMProviderCollaborationGateway(llm_provider, gateway=get_gateway())
        # ``org_service=svc`` persists each emitted message to ``org_events``
        # using this same RLS-scoped session/transaction, so history commits
        # atomically with the rest of the tick. SSE still goes solely through
        # ``publisher`` above -- see ``CollaborationEventRecorder``'s
        # docstring for why persistence is a separate injection point.
        tick = CollaborationTick(gateway, publisher, counters, org_service=svc)
        return await tick.run(
            org_id=str(org_id),
            tenant_id=str(tenant_id),
            settings=settings,
            autonomy_level=int(autonomy_level),
            leads=leads,
            day_spend_usd=day_spend_usd,
        )


@celery_app.task(name="app.scaling.tasks.org_collaboration_loop", queue="maintenance")
def org_collaboration_loop() -> dict[str, int]:
    """Task 9 — ambient collaboration tick ("the team talks").

    Runs every 15 minutes via Celery Beat. Drives ``CollaborationTick.run``
    (via ``_collaboration_tick_for_org``) for every active org, gated by the
    ``org_autonomy_enabled`` feature flag, autonomy level (L3+), and the
    org's own ``collaboration_enabled`` autonomy setting. Isolated per-org
    try/except mirrors ``org_brain_loop`` so one org's failure never blocks
    the rest of the batch.
    """

    zero_totals = {"processed": 0, "orgs_with_chatter": 0, "messages_emitted": 0}

    async def _run() -> dict[str, int]:
        import structlog as _slog
        from opentelemetry import trace as _trace

        _log = _slog.get_logger(__name__)
        tracer = _trace.get_tracer(__name__)

        with tracer.start_as_current_span("org_collaboration.ambient_loop") as span:
            processed = 0
            orgs_with_chatter = 0
            messages_emitted = 0
            try:
                # Resolved in the worker (app.main.app.state.db_factory /
                # llm_provider were read here, and nothing ever set them).
                llm_provider = _worker_llm_provider()
                if llm_provider is None:
                    # No real LLM provider configured for this worker -- fail
                    # closed rather than emit chatter with no model behind it.
                    _log.info("org_collaboration.no_llm_provider_skipping")
                    return dict(zero_totals)

                db_factory, system_db = _org_loop_factories()
                orgs = await _active_orgs_for_maintenance(system_db)

                import redis.asyncio as _aioredis

                redis = _aioredis.from_url(REDIS_URL, decode_responses=True)
                try:
                    for org_id, tenant_id, autonomy_level in orgs:
                        processed += 1
                        try:
                            emitted = await _collaboration_tick_for_org(
                                db_factory=db_factory,
                                redis=redis,
                                llm_provider=llm_provider,
                                org_id=org_id,
                                tenant_id=tenant_id,
                                autonomy_level=autonomy_level,
                            )
                            messages_emitted += emitted
                            if emitted:
                                orgs_with_chatter += 1
                        except Exception as exc:
                            _log.warning(
                                "org_collaboration.org_error",
                                org_id=str(org_id),
                                error=str(exc),
                            )
                finally:
                    await redis.aclose()

                span.set_attribute("orgs_processed", processed)
                span.set_attribute("orgs_with_chatter", orgs_with_chatter)
                span.set_attribute("messages_emitted", messages_emitted)
                _log.info(
                    "org_collaboration.loop_done",
                    processed=processed,
                    orgs_with_chatter=orgs_with_chatter,
                    messages_emitted=messages_emitted,
                )
            except Exception as exc:
                # Re-raise (see org_brain_loop): never report a failed scan as
                # "no orgs had chatter".
                logger.error("org_collaboration.loop_failed", error=str(exc))
                raise
        return {
            "processed": processed,
            "orgs_with_chatter": orgs_with_chatter,
            "messages_emitted": messages_emitted,
        }

    return _run_async(_run())


# ── PART 43: Org Intelligence + Digest + Twin Sync Cron Tasks ─────────────────


@celery_app.task(name="app.scaling.tasks.org_intelligence_cron", queue="maintenance")
def org_intelligence_cron() -> dict:
    """
    PART 43: org-intelligence-cron — runs every 15 minutes.
    Detects bottlenecks, generates insights, updates org health scores.
    """
    from app.org.feature_flags import _run_org_intelligence_cron, is_feature_enabled

    if not is_feature_enabled("org_analytics_enabled"):
        return {"status": "disabled"}
    try:
        return _run_async(_run_org_intelligence_cron())
    except Exception as exc:
        logger.error("org_intelligence_cron failed: %s", exc)
        return {"error": str(exc)}


@celery_app.task(name="app.scaling.tasks.org_digest_cron", queue="maintenance")
def org_digest_cron() -> dict:
    """
    PART 43: org-digest-cron — runs daily at 06:00 UTC.
    Generates "While You Were Away" digests for all active orgs.
    """
    from app.org.feature_flags import _run_org_digest_cron, is_feature_enabled

    if not is_feature_enabled("org_digest_enabled"):
        return {"status": "disabled"}
    try:
        return _run_async(_run_org_digest_cron())
    except Exception as exc:
        logger.error("org_digest_cron failed: %s", exc)
        return {"error": str(exc)}


@celery_app.task(name="app.scaling.tasks.org_twin_sync", queue="maintenance")
def org_twin_sync(event: dict) -> dict:
    """
    PART 43: org-twin-sync — event-driven, triggered on org events.
    Updates digital twin state to reflect real-world org changes.
    """
    from app.org.feature_flags import _run_org_twin_sync

    try:
        _run_async(_run_org_twin_sync(event))
        return {"status": "ok", "event_type": event.get("event_type", "unknown")}
    except Exception as exc:
        logger.warning("org_twin_sync failed: %s", exc)
        return {"error": str(exc)}


# Register org cron tasks in Celery beat schedule
try:
    from celery.schedules import crontab as _crontab

    celery_app.conf.beat_schedule["org-intelligence-every-15min"] = {
        "task": "app.scaling.tasks.org_intelligence_cron",
        "schedule": 900,  # 15 minutes
        "options": {"queue": "maintenance"},
    }
    celery_app.conf.beat_schedule["org-digest-daily-6am"] = {
        "task": "app.scaling.tasks.org_digest_cron",
        "schedule": _crontab(hour=6, minute=0),
        "options": {"queue": "maintenance"},
    }
except Exception as _org_sched_exc:
    logger.warning("Failed to register org cron schedules: %s", _org_sched_exc)


# ── AI-Ops dataset runs (MEM-25) ──────────────────────────────────────────────


def _ai_ops_worker_platform_provider() -> Any:
    """The platform LLM for AI-Ops judges on a worker (same precedence as run_goal)."""
    provider = _worker_deployment_provider()
    if provider is not None:
        return provider
    from app.providers.fake import FakeProvider as _RegFake
    from app.providers.registry import resolve_provider as _resolve_provider

    resolved = _resolve_provider()
    return None if isinstance(resolved, _RegFake) else resolved


def _ai_ops_worker_deps() -> tuple[Any, Any, Any, str]:
    """``(store, goal_service, provider, redis_url)`` for a worker-side dataset run."""
    from app.evals.ai_ops_store import AIOpsStore

    goal_service, db_factory = _build_worker_goal_service()
    if goal_service is None or db_factory is None:
        raise RuntimeError("worker goal service / database unavailable")
    redis_url = os.getenv("REDIS_URL", "") or str(celery_app.conf.broker_url or "")
    return AIOpsStore(db_factory), goal_service, _ai_ops_worker_platform_provider(), redis_url


async def _run_ai_ops_dataset_async(tenant_id: str, plan: str, result_id: str) -> dict[str, Any]:
    """One non-blocking step of an AI-Ops dataset run (P7-1).

    Polls the run's case goals through the durable goal row / event store — it
    never subscribes and waits on a goal, so it holds its worker slot only for
    the few seconds a step takes.
    """
    from app.evals.ai_ops_jobs import run_step
    from app.tenancy.context import PlanTier, TenantContext

    store, goal_service, provider, redis_url = _ai_ops_worker_deps()
    try:
        tier = PlanTier(plan)
    except ValueError:
        tier = PlanTier.FREE
    tenant_ctx = TenantContext(tenant_id=tenant_id, plan=tier, api_key_id="ai-ops-run")
    redis = None
    if redis_url:
        # Cancelling a timed-out case goal reaches its runner (another worker)
        # through the Redis cancel flag.
        redis = _worker_async_redis()
        goal_service._redis = redis
    try:
        return await run_step(
            store=store,
            tenant_ctx=tenant_ctx,
            result_id=result_id,
            goal_service=goal_service,
            provider=provider,
            owner=f"celery-{uuid.uuid4().hex[:12]}",
        )
    finally:
        for task in list(getattr(goal_service, "_background_tasks", ())):
            task.cancel()
        if redis is not None:
            with contextlib.suppress(Exception):
                await redis.aclose()


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.scaling.tasks.run_ai_ops_dataset",
    bind=True,
    max_retries=0,
    # At-least-once: a worker lost mid-step has the message redelivered; the run
    # resumes from its persisted cases and in-flight goal ids (a recorded goal is
    # polled, never resubmitted). The stalled-run sweeper re-dispatches a run
    # whose step chain died.
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_ai_ops_dataset(self: Any, tenant_id: str, plan: str, result_id: str) -> dict[str, Any]:
    """One non-blocking step of an AI-Ops dataset run; re-enqueues the next step.

    A step never waits on a case goal (the goals need worker slots themselves —
    on a two-slot worker an inline wait starved them, P7-1): it polls the
    in-flight goals, submits up to the run's concurrency, and schedules the next
    step after ``ai_ops_poll_seconds``.
    """
    from app.core.config import get_settings

    result: dict[str, Any] = _run_async(_run_ai_ops_dataset_async(tenant_id, plan, result_id))
    if result.get("status") == "running":
        run_ai_ops_dataset.apply_async(
            kwargs={"tenant_id": tenant_id, "plan": plan, "result_id": result_id},
            countdown=float(get_settings().ai_ops_poll_seconds),
            queue="maintenance",
        )
    return result


async def _resume_stalled_ai_ops_runs_async(
    resume_after_seconds: float | None = None,
) -> dict[str, Any]:
    """Re-dispatch AI-Ops runs whose step chain stopped (no heartbeat, lease free)."""
    from sqlalchemy import text

    from app.core.config import get_settings
    from app.db.rls import system_session
    from app.db.session import get_system_session_factory

    after = float(
        resume_after_seconds
        if resume_after_seconds is not None
        else get_settings().ai_ops_resume_after_seconds
    )
    # Cross-tenant scan → maintenance (BYPASSRLS) role, bounded by the partial
    # index on active runs. Bumping heartbeat_at claims the row so two sweeps
    # never dispatch it twice.
    db = get_system_session_factory()
    async with db() as session, session.begin(), system_session(session):
        rows = (
            await session.execute(
                text(
                    "WITH s AS MATERIALIZED (SELECT tenant_id, id FROM ai_ops_eval_results "
                    "      WHERE payload->>'status' IN ('queued', 'running') "
                    "        AND COALESCE(CAST(payload->>'lease_until' AS float8), 0) "
                    "            < extract(epoch FROM clock_timestamp()) "
                    "        AND COALESCE(CAST(payload->>'heartbeat_at' AS timestamptz), "
                    "                     created_at) < now() - make_interval(secs => :after) "
                    "      ORDER BY created_at LIMIT 100 FOR UPDATE SKIP LOCKED) "
                    "UPDATE ai_ops_eval_results r SET payload = r.payload || "
                    "  jsonb_build_object('heartbeat_at', to_jsonb(now())) "
                    "FROM s WHERE r.tenant_id = s.tenant_id AND r.id = s.id "
                    "RETURNING r.tenant_id, r.id, COALESCE(r.payload->>'plan', 'free')"
                ),
                {"after": after},
            )
        ).all()
    for tenant_id, result_id, plan in rows:
        run_ai_ops_dataset.apply_async(
            kwargs={"tenant_id": str(tenant_id), "plan": str(plan), "result_id": str(result_id)},
            queue="maintenance",
        )
        logger.warning("ai_ops_run_resumed", tenant_id=tenant_id, result_id=result_id)
    return {"resumed_runs": len(rows)}


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.scaling.tasks.resume_stalled_ai_ops_runs", bind=True, max_retries=0
)
def resume_stalled_ai_ops_runs(self: Any) -> dict[str, Any]:
    """Beat: resume AI-Ops dataset runs whose step chain died."""
    result: dict[str, Any] = _run_async(_resume_stalled_ai_ops_runs_async())
    return result


# ── Durable eval-suite runs (MEM-53) ──────────────────────────────────────────


async def _run_eval_suite_worker_async(
    tenant_id: str, plan: str, run_id: str, worker_no: int
) -> dict[str, Any]:
    from app.api.agents import AgentStore
    from app.intelligence.eval_suite import platform_judge
    from app.intelligence.eval_suite_jobs import run_step
    from app.intelligence.eval_suite_post_run import on_run_completed
    from app.intelligence.eval_suite_store import EvalSuiteStore
    from app.tenancy.context import PlanTier, TenantContext

    goal_service, db_factory = _build_worker_goal_service()
    if goal_service is None or db_factory is None:
        raise RuntimeError("worker goal service / database unavailable")
    try:
        tier = PlanTier(plan)
    except ValueError:
        tier = PlanTier.FREE
    tenant_ctx = TenantContext(tenant_id=tenant_id, plan=tier, api_key_id="eval-suite-run")
    redis = None
    redis_url = os.getenv("REDIS_URL", "") or str(celery_app.conf.broker_url or "")
    if redis_url:
        # Cancelling a timed-out golden goal reaches its runner (another worker)
        # through the Redis cancel flag.
        redis = _worker_async_redis()
        goal_service._redis = redis
    agent_store = AgentStore(db_factory)

    async def _load_agent(agent_id: str) -> dict[str, Any] | None:
        found: dict[str, Any] | None = await agent_store.get_async(agent_id, tenant_ctx=tenant_ctx)
        return found

    try:
        return await run_step(
            store=EvalSuiteStore(db_factory, tenant_id),
            run_id=run_id,
            goal_service=goal_service,
            tenant_ctx=tenant_ctx,
            judge=platform_judge(_ai_ops_worker_platform_provider()),
            agent_loader=_load_agent,
            on_completed=on_run_completed,
            owner=f"celery-{worker_no}-{uuid.uuid4().hex[:8]}",
        )
    finally:
        for task in list(getattr(goal_service, "_background_tasks", ())):
            task.cancel()
        if redis is not None:
            with contextlib.suppress(Exception):
                await redis.aclose()


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.scaling.tasks.run_eval_suite_worker",
    bind=True,
    max_retries=0,
    # At-least-once: a worker lost mid-step has the message redelivered; the run
    # resumes from its persisted per-task rows (expired leases are re-claimed and
    # a recorded goal is polled, never resubmitted). The stalled-run sweeper
    # re-dispatches if the message itself is lost.
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_eval_suite_worker(
    self: Any, tenant_id: str, plan: str, run_id: str, worker_no: int = 0
) -> dict[str, Any]:
    """One non-blocking step of a durable eval-suite run; re-enqueues the next step.

    A step never waits on a golden goal (the goals need worker slots themselves):
    it polls the due goals, submits up to the run's concurrency, and schedules
    the next step after ``eval_suite_goal_poll_seconds``.
    """
    from app.core.config import get_settings

    result: dict[str, Any] = _run_async(
        _run_eval_suite_worker_async(tenant_id, plan, run_id, worker_no)
    )
    if result.get("status") == "running":
        run_eval_suite_worker.apply_async(
            args=[tenant_id, plan, run_id, worker_no],
            countdown=float(get_settings().eval_suite_goal_poll_seconds),
            queue="maintenance",
        )
    return result


async def _resume_stalled_eval_suite_runs_async(
    resume_after_seconds: float | None = None,
) -> dict[str, Any]:
    """Re-dispatch workers for running runs that made no progress for a while."""
    from sqlalchemy import text

    from app.core.config import get_settings
    from app.db.rls import system_session
    from app.db.session import get_system_session_factory

    settings = get_settings()
    after = float(
        resume_after_seconds
        if resume_after_seconds is not None
        else settings.eval_suite_resume_after_seconds
    )
    # Cross-tenant scan → maintenance (BYPASSRLS) role. Claiming the row by
    # bumping last_progress_at keeps two sweeps from dispatching it twice.
    db = get_system_session_factory()
    async with db() as session, session.begin(), system_session(session):
        rows = (
            await session.execute(
                text(
                    # MATERIALIZED keeps the LIMIT/SKIP LOCKED scan from being
                    # re-run per joined row (which over-claims).
                    "WITH s AS MATERIALIZED (SELECT tenant_id, id FROM eval_suite_results "
                    "      WHERE status = 'running' "
                    "        AND COALESCE(last_progress_at, run_at) "
                    "            < now() - make_interval(secs => :after) "
                    "      ORDER BY COALESCE(last_progress_at, run_at) LIMIT 100 "
                    "      FOR UPDATE SKIP LOCKED) "
                    "UPDATE eval_suite_results r SET last_progress_at = now() "
                    "FROM s "
                    "WHERE r.tenant_id = s.tenant_id AND r.id = s.id "
                    "RETURNING r.tenant_id, r.id, COALESCE(r.tenant_plan, 'free')"
                ),
                {"after": after},
            )
        ).all()
    dispatched = 0
    for tenant_id, run_id, plan in rows:
        # One step chain per run; the step itself bounds the in-flight goals.
        run_eval_suite_worker.apply_async(
            args=[str(tenant_id), str(plan), str(run_id), 0], queue="maintenance"
        )
        dispatched += 1
        logger.warning("eval_suite_run_resumed", tenant_id=tenant_id, run_id=run_id)
    return {"resumed_runs": len(rows), "workers_dispatched": dispatched}


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.scaling.tasks.resume_stalled_eval_suite_runs", bind=True, max_retries=0
)
def resume_stalled_eval_suite_runs(self: Any) -> dict[str, Any]:
    """Beat: resume eval-suite runs whose workers died (no progress heartbeat)."""
    result: dict[str, Any] = _run_async(_resume_stalled_eval_suite_runs_async())
    return result
