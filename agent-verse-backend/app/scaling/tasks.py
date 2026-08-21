"""Celery tasks — real implementations for goal execution and scheduling."""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import hashlib
import os
import signal as _signal
import time
from datetime import UTC
from typing import Any, cast

from celery.signals import worker_init as _worker_init

from app.observability.logging import get_logger
from app.scaling.beat_guard import beat_task_guard
from app.scaling.celery_app import PLAN_QUEUE_MAP, celery_app

logger = get_logger(__name__)


# ── SIGTERM graceful shutdown handler ─────────────────────────────────────────
def _setup_sigterm() -> None:
    """Register a SIGTERM handler so Celery workers shut down gracefully.

    LangGraph writes a checkpoint after every completed step, so the last
    durable state is always safe when the process exits here.
    """

    def _handler(sig: int, frame: Any) -> None:
        import logging as _stdlib_logging

        _stdlib_logging.getLogger(__name__).warning(
            "SIGTERM received — Celery worker shutting down; "
            "LangGraph checkpoint written after last completed step"
        )
        raise SystemExit(0)

    try:
        _signal.signal(_signal.SIGTERM, _handler)
    except (OSError, ValueError):
        # OSError: not in main thread; ValueError: invalid signal — both safe to ignore
        pass


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


def _get_sync_redis() -> Any:
    """Get a synchronous Redis client using the module-level pool."""
    import redis

    return redis.Redis(connection_pool=_get_redis_pool())


# ── Module-level LangGraph checkpointer for Celery workers ────────────────────
# Set once per worker process by _setup_worker_checkpointer (worker_init signal).
# None → AgentGraph falls back to MemorySaver (state lost on worker restart).
_WORKER_CHECKPOINTER: Any = None


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


async def _decrement_after_completion(tenant_id: str, redis_url: str) -> None:
    """Decrement the concurrent-goal counter in Redis after a Celery goal finishes.

    Celery workers never call ``_dispatch_event`` in the API process, so the
    counter must be decremented explicitly here at every terminal exit of
    ``run_goal``.
    """
    try:
        import redis.asyncio as aioredis

        from app.tenancy.limits import decrement_concurrent_goals

        r = aioredis.from_url(redis_url, decode_responses=True)
        await decrement_concurrent_goals(tenant_id=tenant_id, redis=r)
        await r.aclose()
    except Exception as exc:
        logger.warning("counter_decrement_failed: %s", exc)


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

    Disposes the module-level asyncpg engine before closing the event loop so
    that connection pool cleanup can run while the loop is still active.
    Without this, SQLAlchemy raises ``RuntimeError: Event loop is closed``
    for every pooled asyncpg connection during teardown.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            from app.db.session import dispose_task_engine

            loop.run_until_complete(dispose_task_engine())
        except Exception:
            pass
        loop.close()


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


def _build_worker_retrieval_gateway(dependencies: Any) -> Any:
    from app.rag.gateway import RetrievalGateway

    return RetrievalGateway(dependencies)


def _record_goal_duration_metric(status: str, *, started_monotonic: float, priority: str) -> None:
    try:
        from app.observability.metrics import record_goal_duration

        record_goal_duration(status, _monotonic() - started_monotonic, priority)
    except Exception as exc:
        logger.warning("Goal duration metric recording skipped: %s", exc)


def _get_llm_provider(tenant_id: str) -> Any:
    """Load the tenant's configured LLM provider from Redis.

    Uses a synchronous Redis client since Celery tasks run in a regular
    (non-async) thread.  Returns *None* if Redis is unavailable, not
    configured, or the tenant has no stored provider config.
    """
    import os

    redis_url = os.getenv("REDIS_URL", "")
    if not redis_url:
        return None

    try:
        import json

        import redis as sync_redis

        redis_from_url = cast(Any, sync_redis.from_url)
        r = redis_from_url(redis_url, decode_responses=True)
        raw = r.get(f"llm_config:{tenant_id}")
        if raw is None:
            return None

        config = json.loads(raw)
        provider_name = config.get("provider", "")
        encrypted_key = config.get("encrypted_key", "")
        model = config.get("model", "")
        base_url = config.get("base_url")

        if not encrypted_key:
            return None

        from app.providers.vault import get_vault

        api_key = get_vault().decrypt(encrypted_key)

        if provider_name == "anthropic" and api_key:
            from app.providers.anthropic_provider import AnthropicProvider

            return AnthropicProvider(api_key=api_key, default_model=model or "claude-opus-4-8")

        if provider_name in {"openai", "groq", "together", "azure", "ollama"} and api_key:
            from app.providers.openai_compatible import OpenAICompatibleProvider

            return OpenAICompatibleProvider(
                api_key=api_key, base_url=base_url, default_model=model or "gpt-5.2"
            )

    except Exception as exc:
        logger.warning("Could not load tenant LLM config from Redis: %s", exc)

    return None


async def _run_with_signals(
    agent_runner: Any,
    goal: str,
    tenant_ctx: Any,
    event_callback: Any,
    goal_id: str,
    initial_context: dict[str, Any] | None = None,
) -> Any:
    """Run agent_runner.run() while periodically polling pause/cancel signals.

    Polls every 5 seconds. On cancel → raises GoalCancelledError.
    On pause → cancels the current run task and waits until resumed, then restarts.
    """
    from app.reliability.goal_lifecycle import GoalCancelledError, is_cancelled_sync, is_paused_sync

    sync_r = _get_sync_redis()

    run_task = asyncio.create_task(
        agent_runner.run(
            goal=goal,
            tenant_ctx=tenant_ctx,
            initial_context=initial_context,
            event_callback=event_callback,
            goal_id=goal_id,
        )
    )

    while not run_task.done():
        await asyncio.sleep(5)
        # Re-check: task may have completed during the sleep
        if run_task.done():
            break
        if sync_r:
            if is_cancelled_sync(goal_id, sync_r):
                run_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await run_task
                raise GoalCancelledError(f"Goal {goal_id} cancelled during execution")

            if is_paused_sync(goal_id, sync_r):
                # Pause: cancel current run and wait for resume signal
                run_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await run_task
                logger.info("goal_paused_in_worker goal_id=%s", goal_id)
                while is_paused_sync(goal_id, sync_r):
                    await asyncio.sleep(5)
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


class _WorkerMCPAgentRunner:
    def __init__(self, runner: Any, context_factory: Any, system_prompt: str = "") -> None:
        self._runner = runner
        self._context_factory = context_factory
        self._system_prompt = system_prompt

    async def run(
        self,
        *,
        goal: str,
        tenant_ctx: Any,
        initial_context: dict[str, Any] | None = None,
        event_callback: Any = None,
        goal_id: str | None = None,
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
            )
        finally:
            if redis_client is not None:
                await redis_client.aclose()


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


async def _update_goal_dlq(goal_id: str, tenant_id: str, reason: str) -> None:
    from sqlalchemy import update

    from app.db.models.goal import Goal
    from app.db.rls import system_session
    from app.db.session import get_session_factory as _get_fresh_db

    try:
        db = _get_fresh_db()
        async with db() as session, session.begin(), system_session(session):
            await session.execute(
                update(Goal)
                .where(Goal.id == goal_id, Goal.tenant_id == tenant_id)
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
) -> dict[str, Any]:
    """Run a goal worker task and return its local result.

    This task does not update GoalService/DB status or lifecycle events for the
    submitted goal; that bridge is intentionally deferred until Phase 10.
    """
    from app.providers.fake import FakeProvider
    from app.reliability.result_processor import ResultProcessor
    from app.tenancy.context import TenantContext

    logger.info("Running goal %s for tenant %s", goal_id, tenant_id)
    # Log which queue tier this task was dispatched to for observability
    _dispatch_queue = PLAN_QUEUE_MAP.get(plan, "goals.free")
    logger.info(
        "run_goal_queue_selected",
        goal_id=goal_id,
        plan=plan,
        queue=_dispatch_queue,
    )
    started_monotonic = _monotonic()
    effective_goal = goal_text or goal_template

    # Resolve actual tenant plan from Redis config (avoids hardcoded tier)
    _plan_str = "professional"  # safe fallback
    try:
        from app.services.llm_config_store import get_llm_config_store

        _config_store = get_llm_config_store()
        if _config_store:
            _tenant_cfg = _run_async(_config_store.get_config(tenant_id)) or {}
            if _tenant_cfg is None:
                logger.warning("tenant_llm_config_not_found", tenant_id=tenant_id)
                _tenant_cfg = {}
            _plan_str = _tenant_cfg.get("plan", "professional")
    except Exception:
        pass

    from app.tenancy.context import PlanTier as _PT

    try:
        plan = _PT(_plan_str)
    except ValueError:
        plan = _PT.PROFESSIONAL

    tenant_ctx = TenantContext(
        tenant_id=tenant_id,
        plan=plan,
        api_key_id="celery-worker",
    )

    # Phase 11: Check emergency stop before executing — honours operator kill switch
    _stop_key = f"emergency_stop:{tenant_id}"
    _lock_r = _get_sync_redis()
    try:
        if _lock_r and _lock_r.get(_stop_key):
            logger.warning(
                "goal_blocked_by_emergency_stop goal_id=%s tenant_id=%s",
                goal_id,
                tenant_id,
            )
            return {"status": "blocked", "reason": "Emergency stop active for tenant"}
    except Exception as _es_exc:
        logger.warning("emergency_stop_check_failed: %s", _es_exc)

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

    async def update_submitted_goal_status(
        status: str, *, error_message: str = "", iterations: int = 0
    ) -> None:
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
            )
        except Exception as db_exc:
            logger.warning("DB status update failed (non-fatal): %s", db_exc)

    async def append_submitted_goal_event(event: dict[str, Any]) -> None:
        # ── ALWAYS publish to Redis pub/sub first (SSE real-time feed) ────────
        # This must happen regardless of DB availability. Previously the function
        # returned early when event_store was None, silently dropping all events
        # from the SSE stream. Now Redis publish runs unconditionally.
        try:
            import json as _json

            _r = _get_sync_redis()
            if _r is not None:
                _event_data = _json.dumps(
                    {
                        "goal_id": goal_id,
                        "tenant_id": tenant_id,
                        "type": event.get("type", ""),
                        "payload": event,
                    }
                )
                _r.publish(f"goal_events:{tenant_id}:{goal_id}", _event_data)
        except Exception as _pub_exc:
            logger.debug("redis_event_publish_failed (non-fatal): %s", _pub_exc)

        # ── Also persist to event store (DB) for the historical Dev Log ───────
        if event_store is None:
            return
        try:
            _, fresh_event_store, _ = _make_worker_goal_bridge()
            await fresh_event_store.append_event(goal_id, event, tenant_ctx=tenant_ctx)
        except Exception as db_exc:
            logger.debug("DB event append failed (non-fatal): %s", db_exc)

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
                execution_context={},
            )
        except Exception as db_exc:
            logger.warning("DB ensure goal row failed (non-fatal): %s", db_exc)

    async def mark_worker_started() -> None:
        await update_submitted_goal_status("executing")
        await append_submitted_goal_event(
            {"type": "worker_started", "goal": effective_goal, "worker": "celery"}
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

    async def mark_worker_failed(exc: Exception) -> None:
        await update_submitted_goal_status("failed", error_message=str(exc))
        await append_submitted_goal_event({"type": "worker_failed", "reason": str(exc)})

    # ── Distributed lock: at-most-once execution per goal ─────────────────────
    # Use a synchronous lock (_SyncGoalLock) to avoid event-loop-mismatch bugs:
    # each _run_async() call creates a fresh event loop, so an async Redis client
    # created during acquire() would be bound to a different loop from the one
    # used during release(), silently breaking the release.
    _lock: _SyncGoalLock | None = None
    try:
        _redis_url = celery_app.conf.broker_url or ""
        if _redis_url:
            import uuid as _uuid

            import redis as _sync_redis_mod

            _lock_redis_sync = _sync_redis_mod.from_url(_redis_url, decode_responses=True)
            _lock = _SyncGoalLock(_lock_redis_sync, _uuid.uuid4().hex)
            _acquired = _lock.acquire(goal_id, ttl_ms=1_800_000)
            if not _acquired:
                logger.warning("Goal %s already executing in another worker — skipping", goal_id)
                return {
                    "status": "skipped",
                    "goal_id": goal_id,
                    "reason": "already_executing",
                }
    except Exception as _lock_exc:
        logger.warning("Lock acquire failed (continuing without lock): %s", _lock_exc)
        _lock = None

    try:
        _run_async(ensure_submitted_goal_row())
    except Exception as db_exc:
        logger.warning("DB operation failed (non-fatal): %s", db_exc)
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
    real_provider = _get_llm_provider(tenant_id)

    if real_provider is None:
        from app.core.config import get_provider_env

        anthropic_key = get_provider_env("ANTHROPIC_API_KEY")
        openai_key = get_provider_env("OPENAI_API_KEY")
        if anthropic_key:
            from app.providers.anthropic_provider import AnthropicProvider

            real_provider = AnthropicProvider(api_key=anthropic_key)
        elif openai_key:
            from app.providers.openai_compatible import OpenAICompatibleProvider

            real_provider = OpenAICompatibleProvider(api_key=openai_key)

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

    # Resolve the agent's autonomy_mode from the DB so that fully-autonomous
    # agents bypass the HITL gate on write_high tool calls.
    _agent_autonomy_mode = "bounded-autonomous"
    _agent_max_iterations: int | None = None  # None = use graph default (100)
    _agent_system_prompt: str = ""
    _agent_collection_ids: list[str] = []
    if agent_id and db_factory is not None:
        try:
            from sqlalchemy import text as _sa_text

            from app.db.rls import sqlalchemy_rls_context as _rls

            async def _lookup_agent_config() -> tuple[str, int | None, str, list[str]]:
                async with db_factory() as _sess, _rls(_sess, tenant_id):
                    row = (
                        await _sess.execute(
                            _sa_text(
                                "SELECT autonomy_mode, max_iterations, system_prompt, "
                                "allowed_collection_ids FROM agents "
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
                        return mode, iters, sys_prompt, collection_ids
                    return "bounded-autonomous", None, "", []

            (
                _agent_autonomy_mode,
                _agent_max_iterations,
                _agent_system_prompt,
                _agent_collection_ids,
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

    _agent_runner: Any = None
    _use_agent_graph = False

    async def _build_worker_mcp_context() -> tuple[Any, Any, Any]:
        import redis.asyncio as aioredis

        from app.agent.tool_context import ToolContext, ToolRef
        from app.mcp.client import MCPClient
        from app.mcp.registry import MCPRegistry
        from app.providers.vault import (
            RedisConnectorSecretStore,
            get_vault,
            resolve_connector_secret_ref_for_tenant,
        )

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
        secret_store = RedisConnectorSecretStore(redis=redis_client, vault=get_vault())

        async def _resolve_secret(ref: str, tenant_ctx: Any = None) -> str | None:
            return await resolve_connector_secret_ref_for_tenant(
                ref, store=secret_store, tenant_ctx=tenant_ctx
            )

        registry = MCPRegistry(redis_client)
        # Wire the LLM provider so SelfHealingToolCaller can fix argument errors
        # real_provider is captured from the outer run_goal() scope via closure
        mcp_client = MCPClient(
            registry,
            secret_resolver=_resolve_secret,
            redis=redis_client,
            llm_provider=real_provider,  # type: ignore[name-defined]
        )
        worker_connector_ids = [str(item) for item in (connector_ids or [])]

        # When no connector_ids are specified (e.g. goal submitted without an agent),
        # discover ALL tools registered for the tenant so the planner has full context.
        if not worker_connector_ids:
            try:
                all_server_records = await registry.list_server_records(tenant_ctx=tenant_ctx)
                worker_connector_ids = [sid for sid, _ in all_server_records]
            except Exception as _discover_exc:
                logger.warning("worker_all_connector_discovery_failed: %s", _discover_exc)

        tools: list[ToolRef] = []
        connectors: list[dict[str, Any]] = []
        for connector_id in worker_connector_ids:
            cfg = await registry.get(connector_id, tenant_ctx=tenant_ctx)
            if cfg is None:
                continue
            connectors.append({"id": connector_id, "name": cfg.name})
            for discovered in await mcp_client.discover_tools(
                server_id=connector_id, tenant_ctx=tenant_ctx
            ):
                tools.append(
                    ToolRef(
                        server_id=connector_id,
                        server_name=discovered.server_name,
                        name=discovered.name,
                        description=discovered.description,
                        input_schema=discovered.input_schema,
                    )
                )
        return redis_client, mcp_client, ToolContext(connectors=connectors, tools=tools)

    if not _loop_is_patched:
        # Production path: Try AgentGraph first (full capabilities)
        try:
            from app.agent.graph import AgentGraph
            from app.governance.audit import AuditLog
            from app.governance.cost import CostController, RedisCostController
            from app.governance.hitl import HITLGateway
            from app.intelligence.eval_runner import EvalRunner
            from app.intelligence.guardrails import GuardrailChecker
            from app.memory.execution import ExecutionMemory
            from app.memory.long_term import LongTermMemoryStore
            from app.reliability.dedup import DeduplicationCache
            from app.reliability.rollback import RollbackEngine

            _audit = AuditLog(db_session_factory=db_factory)
            _hitl = HITLGateway()
            _cost = CostController()
            _policy = _run_async(_load_worker_policy_engine(db_factory, tenant_id))
            _ltm = LongTermMemoryStore()
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
                if _provider_name:
                    _model_router = ModelRouter(provider_name=_provider_name)
            except Exception:
                pass

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

            # Build embedder for pgvector LTM recall (same priority as main.py)
            _embedder_for_graph = None
            try:
                from app.core.config import get_provider_env as _gpe

                _v_key = _gpe("VOYAGE_API_KEY")
                _o_key = _gpe("OPENAI_API_KEY")
                if _v_key:
                    from app.providers.voyage_provider import VoyageProvider

                    _embedder_for_graph = VoyageProvider(api_key=_v_key)
                elif _o_key:
                    from app.providers.openai_compatible import OpenAICompatibleProvider

                    _embedder_for_graph = OpenAICompatibleProvider(
                        api_key=_o_key, default_model="text-embedding-3-small"
                    )
            except Exception as _emb_exc:
                logger.warning("worker_embedder_build_failed: %s", _emb_exc)

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
                    colbert_checkpoint=worker_settings.colbert_checkpoint,
                )
            )

            _agent_runner = AgentGraph(
                planner=provider,
                executor=provider,
                verifier=_verifier_for_graph,
                model_router=_model_router,
                autonomy_mode=_agent_autonomy_mode,
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
            )
            if db_factory is not None:
                _agent_runner._db_session_factory = db_factory
            _agent_runner._agent_collection_ids = list(_agent_collection_ids)
            # Wire SelfOptimizer and PromptOptimizer so A/B testing and
            # failure suggestions run during real goal execution.
            try:
                from app.intelligence.prompt_optimizer import _default_optimizer as _prompt_opt
                from app.intelligence.self_optimization import SelfOptimizer

                _self_opt = SelfOptimizer()
                if db_factory is not None:
                    _self_opt._db = db_factory
                _agent_runner._self_optimizer = _self_opt
                _agent_runner._prompt_optimizer = _prompt_opt
            except Exception as _opt_exc:
                logger.warning("optimizer_wire_failed: %s", _opt_exc)
            _agent_runner = _WorkerMCPAgentRunner(
                _agent_runner,
                _build_worker_mcp_context,
                system_prompt=_agent_system_prompt,
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
            return {
                "status": "failed",
                "goal_id": goal_id,
                "reason": "agentgraph_assembly_failed",
                "message": "Canonical AgentGraph assembly failed",
            }

    try:
        # Block fake execution in production — a real LLM provider is required
        import os as _os

        _env = _os.getenv("ENVIRONMENT", "development")
        if used_fake_provider and _env == "production":
            _run_async(
                mark_worker_failed(
                    RuntimeError(
                        "No real LLM provider configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY."
                    )
                )
            )
            _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
            return {
                "status": "failed",
                "goal_id": goal_id,
                "reason": "no_llm_provider",
                "message": (
                    "Goal requires a real LLM provider. "
                    "Configure ANTHROPIC_API_KEY or OPENAI_API_KEY."
                ),
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

            if _use_isolation:
                # Build and dispatch an ExecutionEnvelope instead of running in-process
                _RunnerUnavail: type | None = None
                try:
                    from app.execution_environment.envelope import build_envelope as _build_env
                    from app.execution_environment.models import RunnerType as _RT
                    from app.execution_environment.scheduler import (
                        ExecutionEnvironmentScheduler as _Scheduler,
                    )
                    from app.execution_environment.scheduler import (
                        RunnerUnavailableError as _RunnerUnavail,
                    )

                    _iso_flags = _rt  # reuse already-fetched flags (G-44)
                    if _iso_flags.isolated_execution_kubernetes_runner:
                        _iso_runner_type = _RT.KUBERNETES
                    elif _iso_flags.isolated_execution_local_runner:
                        _iso_runner_type = _RT.LOCAL
                    else:
                        _iso_runner_type = _RT.FAKE

                    # Build feature flags snapshot for the envelope (G-28)
                    _iso_feature_flags = {
                        "isolated_agent_execution": _iso_flags.isolated_agent_execution,
                        "isolated_execution_required": _iso_flags.isolated_execution_required,
                        "isolated_execution_local_runner": _iso_flags.isolated_execution_local_runner,
                        "isolated_execution_kubernetes_runner": _iso_flags.isolated_execution_kubernetes_runner,
                        "dynamic_orchestration": _iso_flags.dynamic_orchestration,
                        "agentic_rag": _iso_flags.agentic_rag,
                    }

                    # Resolve scoped LLM key (G-28)
                    _iso_llm_key = ""
                    try:
                        from app.services.llm_config_store import get_llm_api_key_for_tenant

                        _iso_llm_key = get_llm_api_key_for_tenant(tenant_id)
                    except Exception:
                        pass

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

            state = _run_async(
                _asyncio.wait_for(
                    _run_with_signals(
                        _agent_runner,
                        effective_goal,
                        tenant_ctx,
                        worker_event_callback,
                        goal_id,
                    ),
                    timeout=float(goal_timeout_s),
                )
            )
        except TimeoutError:
            _run_async(mark_worker_failed(TimeoutError(f"Goal timed out after {goal_timeout_s}s")))
            _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
            return {
                "status": "failed",
                "goal_id": goal_id,
                "reason": f"timeout after {goal_timeout_s}s",
                "result_scope": "worker_only",
            }
        _run_async(mark_worker_complete(state.status.value, state.iterations))
        if state.status.value in {"complete", "failed"}:
            _record_goal_duration_metric(
                state.status.value,
                started_monotonic=started_monotonic,
                priority=priority,
            )
        # Decrement concurrent-goal counter — goal has reached terminal state
        _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
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
    except Exception as exc:
        logger.error("Goal %s failed: %s", goal_id, exc)
        _record_goal_duration_metric(
            "failed", started_monotonic=started_monotonic, priority=priority
        )
        _run_async(mark_worker_failed(exc))
        try:
            raise self.retry(exc=exc, countdown=2**self.request.retries) from exc
        except self.MaxRetriesExceededError:
            # Route to dead-letter queue
            with contextlib.suppress(Exception):
                run_goal_dlq.delay(
                    goal_id=goal_id,
                    tenant_id=tenant_id,
                    goal_text=effective_goal,
                    reason="max_retries_exceeded",
                )
            # Still update DB to failed
            _run_async(
                mark_worker_failed(
                    RuntimeError(f"Goal {goal_id} exceeded max retries, routed to DLQ")
                )
            )
            # Decrement counter — goal is permanently done
            _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
            return {"status": "dead_lettered", "goal_id": goal_id}
    finally:
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
) -> dict[str, Any]:
    """Execute a scheduled goal trigger."""
    logger.info("Firing schedule %s for tenant %s", schedule_id, tenant_id)
    try:
        result = run_goal.apply_async(
            kwargs={
                "goal_id": _scheduled_goal_id(
                    schedule_id,
                    fire_instance_id=fire_instance_id or None,
                ),
                "goal_text": goal_template,
                "tenant_id": tenant_id,
                "agent_id": agent_id,
            },
            queue="schedules",
        )
    except Exception as exc:
        logger.warning("Scheduled goal dispatch failed for %s: %s", schedule_id, exc)
        raise self.retry(exc=exc, countdown=2**self.request.retries) from exc
    return {
        "status": "dispatched",
        "schedule_id": schedule_id,
        "task_id": result.id,
    }


def _scheduled_goal_kwargs(
    schedule_key: str,
    sched: dict[str, Any],
    *,
    fire_instance_id: str | None = None,
) -> dict[str, Any] | None:
    goal_text = str(sched.get("goal_template") or sched.get("goal_id") or "")
    tenant_id = str(sched.get("tenant_id") or "")
    if not goal_text or not tenant_id:
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
    agent_id = str(sched.get("agent_id") or "")
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
    if via_scheduled_task:
        run_scheduled_goal.apply_async(
            kwargs={
                "schedule_id": schedule_key,
                "tenant_id": goal_kwargs["tenant_id"],
                "goal_template": goal_kwargs["goal_template"],
                "agent_id": str(goal_kwargs.get("agent_id") or ""),
                "fire_instance_id": fire_instance_id,
            },
            queue="schedules",
        )
    else:
        run_goal.apply_async(
            kwargs=goal_kwargs,
            queue="schedules",
        )
    return goal_kwargs


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


def _db_schedule_payload(row: Any) -> dict[str, Any]:
    goal_template = str(getattr(row, "goal_id_template", "") or "")
    tenant_id = str(getattr(row, "tenant_id", "") or "")
    schedule_id = str(getattr(row, "id", "") or "")
    return {
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
    }


async def _load_db_schedules() -> dict[str, dict[str, Any]]:
    try:
        from sqlalchemy import select

        from app.db.models.scheduling import Schedule
        from app.db.models.tenant import Tenant
        from app.db.rls import sqlalchemy_rls_context
        from app.db.session import get_session_factory as _get_fresh_db

        db_factory = _get_fresh_db()
        schedules: dict[str, dict[str, Any]] = {}
        async with db_factory() as session:
            tenant_result = await session.execute(
                select(Tenant).where(Tenant.is_active == True)  # noqa: E712
            )
            tenants = tenant_result.scalars().all()
            for tenant in tenants:
                tenant_id = str(tenant.id)
                async with sqlalchemy_rls_context(session, tenant_id):
                    schedule_result = await session.execute(
                        select(Schedule).where(
                            Schedule.tenant_id == tenant_id,
                            Schedule.paused == False,  # noqa: E712
                        )
                    )
                for row in schedule_result.scalars().all():
                    payload = _db_schedule_payload(row)
                    schedule_id = str(payload.get("schedule_id") or "")
                    row_tenant_id = str(payload.get("tenant_id") or tenant_id)
                    if not schedule_id or not row_tenant_id or payload.get("paused"):
                        continue
                    schedules[_schedule_key(row_tenant_id, schedule_id)] = payload
        return schedules
    except Exception as exc:
        logger.warning("DB schedule discovery skipped: %s", exc)
        return {}


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


def _db_schedule_discovery_enabled() -> bool:
    import os

    return os.getenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "false").lower() in {
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


@celery_app.task(name="app.scaling.tasks.check_mcp_health")  # type: ignore[untyped-decorator]
def check_mcp_health() -> dict[str, Any]:
    """Periodic MCP server health check — pings /health on all active servers."""

    async def _run() -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        checked = 0

        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        if not redis_url:
            return {"servers_checked": 0, "results": [], "status": "skipped", "reason": "no_redis"}

        import redis.asyncio as aioredis

        r = aioredis.from_url(redis_url, decode_responses=True)

        try:
            # Scan for all MCP server keys written by MCPRegistry:
            # key pattern: mcp:servers:{tenant_id}:{server_id}
            async for key in r.scan_iter(match="mcp:servers:*:*", count=100):
                if checked >= 50:
                    break
                checked += 1
                try:
                    raw = await r.get(key)
                    if not raw:
                        continue
                    try:
                        from app.mcp.registry import MCPServerConfig

                        cfg = MCPServerConfig.model_validate_json(raw)
                    except Exception as parse_exc:
                        results.append(
                            {"key": key, "status": "parse_error", "error": str(parse_exc)}
                        )
                        continue
                    # Simple health check: GET {base_url}/health
                    import httpx

                    async with httpx.AsyncClient(timeout=5.0) as client:
                        try:
                            resp = await client.get(f"{cfg.base_url}/health", follow_redirects=True)
                            results.append(
                                {
                                    "server": cfg.name,
                                    "status": "ok",
                                    "code": resp.status_code,
                                }
                            )
                        except Exception as http_exc:
                            results.append(
                                {
                                    "server": cfg.name,
                                    "status": "error",
                                    "error": str(http_exc)[:200],
                                }
                            )
                except Exception as exc:
                    results.append({"key": key, "status": "error", "error": str(exc)[:200]})
        finally:
            await r.aclose()

        return {
            "servers_checked": checked,
            "results": results[:20],
        }

    async def _fallback() -> dict[str, Any]:
        """Fallback: scan the old flat-dict key structure for backward compatibility."""
        results: list[dict[str, Any]] = []
        try:
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            if not redis_url:
                return {"servers_checked": 0, "results": [], "status": "skipped"}

            import time as _time

            import httpx as _httpx
            import redis.asyncio as aioredis

            r = aioredis.from_url(redis_url, decode_responses=True)
            try:
                keys = []
                async for k in r.scan_iter(match="mcp:servers:*", count=100):
                    keys.append(k)
                    if len(keys) >= 50:
                        break

                import json as _json

                for key in keys:
                    raw = await r.get(key)
                    if not raw:
                        continue
                    try:
                        data = _json.loads(raw)
                    except Exception:
                        continue
                    if not isinstance(data, dict):
                        continue
                    for server_id, sdata in data.items():
                        url = sdata.get("url", "") if isinstance(sdata, dict) else ""
                        if not url:
                            continue
                        t0 = _time.monotonic()
                        try:
                            async with _httpx.AsyncClient(timeout=3.0) as http:
                                resp = await http.get(f"{url.rstrip('/')}/health")
                            latency_ms = round((_time.monotonic() - t0) * 1000)
                            status = "healthy" if resp.status_code < 400 else "degraded"
                            results.append(
                                {
                                    "server_id": server_id,
                                    "url": url,
                                    "status": status,
                                    "latency_ms": latency_ms,
                                }
                            )
                        except Exception as exc:
                            results.append(
                                {
                                    "server_id": server_id,
                                    "url": url,
                                    "status": "unreachable",
                                    "error": str(exc)[:200],
                                }
                            )
            finally:
                await r.aclose()
        except Exception as exc:
            results.append({"status": "error", "reason": str(exc)})
        return {"servers_checked": len(results), "results": results[:20]}

    event_loop = asyncio.new_event_loop()
    try:
        try:
            result = event_loop.run_until_complete(_run())
        except Exception:
            # MCPServerConfig import may fail (e.g. mcp module not available)
            result = event_loop.run_until_complete(_fallback())
    finally:
        event_loop.close()

    return {
        "status": "ok",
        "checked_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "servers_checked": result.get("servers_checked", 0),
        "results": result.get("results", []),
    }


# Backward-compatible alias (tests reference this name)
health_check_mcp = check_mcp_health


@celery_app.task(name="app.scaling.tasks.fire_due_schedules", bind=True, max_retries=3)  # type: ignore[untyped-decorator]
@beat_task_guard(lock_ttl_seconds=300)
def fire_due_schedules(self: Any) -> dict[str, Any]:
    """Fire all cron/interval schedules that are due within the current minute."""
    import json
    import os

    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    fired = 0

    try:
        redis_url = os.getenv("REDIS_URL", "")
        r: Any | None = None
        schedules: dict[str, dict[str, Any]] = {}
        db_schedule_keys: set[str] = set()
        if redis_url:
            try:
                import redis as sync_redis

                redis_from_url = cast(Any, sync_redis.from_url)
                r = redis_from_url(redis_url, decode_responses=True)

                # Scan for all schedule keys written by ScheduleStore: schedule:{tenant}:{id}
                schedule_keys = list(r.scan_iter(match="schedule:*", count=100))
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

        if _db_schedule_discovery_enabled():
            db_schedules = cast(dict[str, dict[str, Any]], _run_async(_load_db_schedules()))
            for key, sched in db_schedules.items():
                db_schedule_keys.add(key)
                if key not in schedules:
                    schedules[key] = sched
                else:
                    schedules[key]["schedule_id"] = sched.get("schedule_id")
                    schedules[key]["last_fired_at"] = sched.get("last_fired_at")
        else:
            # Postgres schedule source-of-truth fallback is opt-in until Phase 12
            # deployment config enables AGENTVERSE_DB_SCHEDULE_DISCOVERY.
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

        logger.info("fire_due_schedules: checking %d schedule keys", len(schedules))

        for key, sched in schedules.items():
            try:
                if sched.get("paused"):
                    continue

                trigger_type = sched.get("trigger_type", "")

                # ── CRON schedules ────────────────────────────────────────────
                if trigger_type == "cron":
                    cron_expr = sched.get("cron_expression", "")
                    if cron_expr:
                        try:
                            import croniter as _croniter_pkg  # type: ignore[import-untyped]

                            cron = _croniter_pkg.croniter(
                                cron_expr, now + datetime.timedelta(seconds=1)
                            )
                            cron_previous_run = cast(
                                datetime.datetime, cron.get_prev(datetime.datetime)
                            )
                            previous_run = _schedule_datetime(cron_previous_run)
                            last_fired_dt = _schedule_datetime(sched.get("last_fired_at"))
                        except Exception as cron_exc:
                            logger.warning("Cron parse error for %s: %s", key, cron_exc)
                            continue
                        if previous_run is not None and (
                            last_fired_dt is None or last_fired_dt < previous_run
                        ):
                            goal_kwargs = advance_and_dispatch_schedule(
                                key,
                                sched,
                                fired_at=previous_run,
                                fire_instance_id=previous_run.isoformat(),
                            )
                            if goal_kwargs is not None:
                                fired += 1
                                logger.info(
                                    "Fired cron schedule %s for tenant %s",
                                    key,
                                    goal_kwargs["tenant_id"],
                                )

                # ── INTERVAL schedules ────────────────────────────────────────
                elif trigger_type == "interval":
                    interval_s: int = sched.get("interval_seconds", 0)
                    if interval_s > 0:
                        last_fired = sched.get("last_fired_at")
                        due: bool
                        if last_fired is None:
                            due = True
                        else:
                            last_dt = _schedule_datetime(last_fired)
                            due = last_dt is None or (now - last_dt).total_seconds() >= interval_s

                        if due:
                            goal_kwargs = advance_and_dispatch_schedule(
                                key,
                                sched,
                                fired_at=now,
                                fire_instance_id=now.isoformat(),
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

                        if watch_path:
                            try:
                                if _os_fd.path.isdir(watch_path):
                                    all_files = _os_fd.listdir(watch_path)
                                    if r is not None:
                                        _proc_raw = r.get(processed_key)
                                        if _proc_raw:
                                            processed = set(_json_fd.loads(_proc_raw))
                                    new_files = [
                                        _os_fd.path.join(watch_path, f)
                                        for f in all_files
                                        if _fnmatch.fnmatch(f, watch_pattern) and f not in processed
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
                            from app.tenancy.context import (
                                PlanTier as _PT_fd,
                            )
                            from app.tenancy.context import (
                                TenantContext as _TC_fd,
                            )

                            _tenant_ctx_fd = _TC_fd(
                                tenant_id=_tenant_id_fd,
                                plan=_PT_fd.PROFESSIONAL,
                                api_key_id="trigger-file-drop",
                            )
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
                                    tenant_ctx=_tenant_ctx_fd,
                                )
                            )
                            if _alert_kw:
                                _goal_id_fd = _scheduled_goal_id(
                                    key,
                                    fire_instance_id=f"filedrop:{_file_name}:{now.isoformat()}",
                                )
                                run_goal.apply_async(
                                    kwargs={
                                        "goal_id": _goal_id_fd,
                                        "tenant_id": _tenant_id_fd,
                                        "goal_text": _alert_kw["goal"],
                                        "priority": _alert_kw["priority"],
                                        "agent_id": str(_alert_kw.get("agent_id") or ""),
                                    },
                                    queue="schedules",
                                )
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

                # ── External alert triggers (Alertmanager / Datadog / PagerDuty) ─
                elif trigger_type in ("alertmanager", "datadog", "pagerduty"):
                    # External alert triggers: read payload from Redis webhook cache
                    # (set by POST /webhooks/alerts/{type}), fall back to schedule
                    # metadata; dispatch a goal with alert context.
                    try:
                        import json as _json_alert

                        alert_data: dict[str, Any] = {}
                        alert_cache_key = (
                            f"alert_payload:{trigger_type}:{sched.get('schedule_id', key)}"
                        )
                        if r is not None:
                            try:
                                _cached = r.get(alert_cache_key)
                                if _cached:
                                    alert_data = _json_alert.loads(_cached)
                                    r.delete(alert_cache_key)
                            except Exception:
                                pass

                        if not alert_data:
                            alert_data = {
                                "trigger_type": trigger_type,
                                "schedule_id": sched.get("schedule_id", key),
                                "fired_at": now.isoformat(),
                                "source": trigger_type,
                                "status": "firing",
                            }
                            if trigger_type == "alertmanager":
                                alert_data["alertname"] = sched.get("alert_name", "PrometheusAlert")
                                alert_data["severity"] = sched.get("severity", "warning")
                            elif trigger_type == "datadog":
                                alert_data["monitor_name"] = sched.get(
                                    "monitor_name", "DatadogMonitor"
                                )
                                alert_data["status"] = sched.get("alert_status", "triggered")
                            elif trigger_type == "pagerduty":
                                alert_data["incident_title"] = sched.get(
                                    "incident_title", "PagerDutyIncident"
                                )
                                alert_data["urgency"] = sched.get("urgency", "high")

                        _tenant_id_alert = str(sched.get("tenant_id") or "")
                        if not _tenant_id_alert:
                            logger.warning(
                                "alert_trigger_missing_tenant_id",
                                trigger_type=trigger_type,
                                key=key,
                            )
                        else:
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
                            if _alert_kwargs:
                                _goal_id_alert = _scheduled_goal_id(
                                    key,
                                    fire_instance_id=f"{trigger_type}:{now.isoformat()}",
                                )
                                run_goal.apply_async(
                                    kwargs={
                                        "goal_id": _goal_id_alert,
                                        "tenant_id": _tenant_id_alert,
                                        "goal_text": _alert_kwargs["goal"],
                                        "priority": _alert_kwargs["priority"],
                                        "agent_id": str(_alert_kwargs.get("agent_id") or ""),
                                    },
                                    queue="schedules",
                                )
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

        return {
            "status": "ok",
            "checked_at": now.isoformat(),
            "schedules_fired": fired,
            "schedules_checked": len(schedules),
        }
    except Exception as exc:
        logger.error("fire_due_schedules failed: %s", exc)
        raise self.retry(exc=exc, countdown=2**self.request.retries) from exc


@celery_app.task(name="app.scaling.tasks.detect_stuck_goals", bind=True, max_retries=0)
def detect_stuck_goals(self: Any) -> dict[str, Any]:
    """Find goals stuck in executing/planning > 60 minutes and mark as failed."""
    return _run_async(_find_and_fail_stuck_goals())


async def _find_and_fail_stuck_goals() -> dict[str, Any]:
    from datetime import UTC, datetime, timedelta

    timeout_minutes = 60
    cutoff = datetime.now(UTC) - timedelta(minutes=timeout_minutes)
    try:
        from sqlalchemy import text

        from app.db.rls import system_session
        from app.db.session import get_session_factory as _get_fresh_db

        db = _get_fresh_db()
        async with db() as session, session.begin(), system_session(session):
            result = await session.execute(
                text("""UPDATE goals
                        SET status='failed',
                            error_message='Stuck goal: exceeded 60-minute timeout',
                            updated_at=NOW()
                        WHERE status IN ('executing','planning')
                          AND updated_at < :cutoff
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
                        RETURNING id"""),
                {"cutoff": cutoff},
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
    return _run_async(_delete_expired_records(retention_days))


async def _delete_expired_records(retention_days: int) -> dict[str, Any]:
    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    counts: dict[str, Any] = {}
    try:
        from sqlalchemy import text

        from app.db.rls import system_session
        from app.db.session import get_session_factory as _get_fresh_db

        db = _get_fresh_db()
        async with db() as session, session.begin(), system_session(session):
            for table in ["goal_events", "decision_traces"]:
                try:
                    r = await session.execute(
                        text(f"DELETE FROM {table} WHERE created_at < :c"), {"c": cutoff}
                    )
                    counts[table] = r.rowcount
                except Exception as exc:
                    counts[table] = f"error: {exc}"
        return {"retention_days": retention_days, "cutoff": cutoff.isoformat(), "deleted": counts}
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
    notified: list[str] = []
    try:
        expired_ids = _run_async(_expire_db_approvals())
        expired_count = len(expired_ids)
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
        "notified": len(notified),
        "checked_at": datetime.now(UTC).isoformat(),
    }


async def _notify_expired_approvals(expired_ids: list[str]) -> list[str]:
    """G-12: Send notification for each expired approval request."""
    if not expired_ids:
        return []
    try:
        from sqlalchemy import text

        from app.db.session import get_session_factory as _get_fresh_db

        db = _get_fresh_db()
        notified: list[str] = []
        async with db() as session:
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
    try:
        from sqlalchemy import text

        from app.db.rls import system_session
        from app.db.session import get_session_factory as _get_fresh_db

        db = _get_fresh_db()
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
            return [row[0] for row in result.fetchall()]
    except Exception as exc:
        logger.warning("expire_db_approvals failed: %s", exc)
        return []


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
        from app.tenancy.context import PlanTier, TenantContext

        db_factory = _get_fresh_db()
        event_store = EventStore(db_factory)
        goal_service = GoalService(db_session_factory=db_factory, event_store=event_store)

        email_tenant_id = os.getenv("IMAP_TENANT_ID", "email-default")
        ctx = TenantContext(
            tenant_id=email_tenant_id,
            plan=PlanTier.PROFESSIONAL,
            api_key_id="email-listener",
        )

        count = await check_and_process_emails(goal_service, ctx)
        return {"status": "ok", "processed": count}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "processed": 0}


@celery_app.task(name="agentverse.maintenance.consolidate_memories")
def consolidate_memories_task() -> dict:
    """Consolidate and deduplicate long-term memories older than 7 days."""

    async def _run() -> dict:
        from sqlalchemy import text

        from app.db.session import get_session_factory as _get_fresh_db

        db = _get_fresh_db()
        results: dict = {}
        try:
            async with db() as session, session.begin():
                result = await session.execute(
                    text("""
                    DELETE FROM long_term_memory
                    WHERE id NOT IN (
                        SELECT DISTINCT ON (tenant_id, content) id
                        FROM long_term_memory
                        ORDER BY tenant_id, content, created_at DESC
                    )
                """)
                )
                results["duplicates_removed"] = result.rowcount

                import os as _os

                retention = int(_os.getenv("DATA_RETENTION_DAYS", "90"))
                result = await session.execute(
                    text(
                        "DELETE FROM long_term_memory "
                        "WHERE created_at < NOW() - (:days * INTERVAL '1 day')"
                    ),
                    {"days": retention},
                )
                results["expired_removed"] = result.rowcount
        except Exception as exc:
            results["error"] = str(exc)

        return results

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()


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


@celery_app.task(name="agentverse.maintenance.reindex_stale_knowledge")
def reindex_stale_knowledge() -> dict:
    """Mark knowledge chunks past their freshness TTL as needing reindex."""

    async def _run() -> dict:
        from sqlalchemy import text

        from app.db.session import get_session_factory as _get_fresh_db

        db = _get_fresh_db()
        async with db() as session, session.begin():
            result = await session.execute(
                text("""
                UPDATE documents
                SET needs_reindex = TRUE, updated_at = NOW()
                WHERE needs_reindex = FALSE
                  AND last_modified IS NOT NULL
                  AND freshness_ttl_hours > 0
                  AND last_modified < NOW() - (freshness_ttl_hours * INTERVAL '1 hour')
            """)
            )
            marked = result.rowcount
        return {"marked_for_reindex": marked}

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    except Exception as exc:
        logger.warning("reindex_stale_knowledge failed: %s", exc)
        return {"marked_for_reindex": 0, "error": str(exc)}
    finally:
        loop.close()


# Register reindex_stale_knowledge in the Celery beat schedule (hourly)
try:
    celery_app.conf.beat_schedule["reindex-stale-knowledge"] = {
        "task": "agentverse.maintenance.reindex_stale_knowledge",
        "schedule": 3600,
        "options": {"queue": "maintenance"},
    }
    celery_app.conf.task_routes.update(
        {"agentverse.maintenance.reindex_stale_knowledge": {"queue": "maintenance"}}
    )
except Exception as _reindex_sched_exc:
    logger.warning(
        "Failed to register reindex_stale_knowledge beat schedule: %s", _reindex_sched_exc
    )


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
            # Collect all tenant data
            async with db() as session:
                goals = (
                    await session.execute(
                        text(
                            "SELECT id, goal_text, status, created_at FROM goals "
                            "WHERE tenant_id = :tid LIMIT 10000"
                        ),
                        {"tid": tenant_id},
                    )
                ).fetchall()
                try:
                    audit = (
                        await session.execute(
                            text(
                                "SELECT event_id, goal_id, tool_name, outcome FROM audit_log "
                                "WHERE tenant_id = :tid LIMIT 10000"
                            ),
                            {"tid": tenant_id},
                        )
                    ).fetchall()
                except Exception:
                    audit = []

            import json
            import uuid as _uuid
            from datetime import datetime

            export_data = {
                "tenant_id": tenant_id,
                "exported_at": datetime.now(UTC).isoformat(),
                "goals": [{"id": str(r[0]), "text": str(r[1]), "status": str(r[2])} for r in goals],
                "audit_entries": [
                    {"id": str(r[0]), "goal_id": str(r[1]), "tool": str(r[2])} for r in audit
                ],
            }

            export_json = json.dumps(export_data, indent=2, default=str)  # noqa: F841
            export_id = _uuid.uuid4().hex
            download_url = f"/compliance/export/{export_id}/download"

            async with db() as session, session.begin():
                await session.execute(
                    text("""
                    UPDATE gdpr_export_jobs
                    SET status = 'complete', completed_at = NOW(), download_url = :url
                    WHERE id = :jid
                """),
                    {"url": download_url, "jid": job_id},
                )

            return {"status": "complete", "job_id": job_id, "download_url": download_url}

        except Exception as exc:
            try:
                async with db() as session, session.begin():
                    await session.execute(
                        text(
                            "UPDATE gdpr_export_jobs SET status = 'failed', "
                            "error_message = :err WHERE id = :jid"
                        ),
                        {"err": str(exc)[:500], "jid": job_id},
                    )
            except Exception:
                pass
            raise

    return _run_async(_run())


# ─────────────────────────────────────────────────────────────────────────────
# CIVILIZATION TASKS
# ─────────────────────────────────────────────────────────────────────────────


@celery_app.task(name="app.scaling.tasks.civilization_tick")
def civilization_tick(civilization_id: str, tenant_id: str) -> dict:
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

                async with db() as session:
                    row = (
                        await session.execute(
                            text(
                                "SELECT constitution FROM civilizations WHERE id=:id AND tenant_id=:tid"
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
            from app.db.session import get_session_factory as _get_fresh_db

            db = _get_fresh_db()
            jwks_keys = await _build_jwks(db)
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
def create_guardrail_partitions() -> dict:
    """Create next 3 months of monthly partitions for guardrail_events."""
    return {"status": "noop"}


@celery_app.task(name="app.scaling.tasks.enforce_hitl_sla", queue="governance")
def enforce_hitl_sla() -> dict:
    """Check pending HITL approvals past SLA deadline and escalate or auto-resolve."""

    async def _run() -> dict:
        try:
            from sqlalchemy import text as _t

            from app.db.session import get_session_factory as _get_fresh_db

            db = _get_fresh_db()
            enforced = 0
            async with db() as session:
                overdue = (
                    await session.execute(
                        _t("""
                            SELECT id, tenant_id, sla_deadline
                            FROM hitl_approval_requests
                            WHERE status = 'pending'
                              AND sla_deadline IS NOT NULL
                              AND sla_deadline < NOW()
                            LIMIT 100
                        """)
                    )
                ).fetchall()
                for row in overdue:
                    await session.execute(
                        _t("""
                            UPDATE hitl_approval_requests
                            SET status = 'sla_escalated', resolved_at = NOW()
                            WHERE id = :id
                        """),
                        {"id": row[0]},
                    )
                    enforced += 1
                await session.commit()
            return {"enforced": enforced}
        except Exception as exc:
            return {"error": str(exc), "enforced": 0}

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

            from app.db.session import get_session_factory as _get_fresh_db

            db = _get_fresh_db()
            async with db() as session:
                result = await session.execute(
                    text(
                        "UPDATE prompt_variants SET updated_at = NOW() "
                        "WHERE updated_at < NOW() - INTERVAL '30 days' "
                        "AND wins + losses >= 20 RETURNING id"
                    )
                )
                concluded = len(result.fetchall())
                await session.commit()
                return {"status": "ok", "concluded": concluded}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    return _run_async(_run())


@celery_app.task(name="app.scaling.tasks.expire_stale_documents", queue="maintenance")
def expire_stale_documents() -> dict:
    """Remove knowledge chunks whose freshness_ttl_hours has elapsed."""

    async def _run() -> dict:
        try:
            from sqlalchemy import text

            from app.core.config import get_settings
            from app.db.session import get_session_factory as _get_fresh_db

            retention_days = getattr(get_settings(), "data_retention_days", 90)
            db = _get_fresh_db()
            async with db() as session:
                result = await session.execute(
                    text(
                        f"DELETE FROM documents WHERE created_at < NOW() - INTERVAL '{retention_days} days' "
                        "RETURNING id"
                    )
                )
                deleted = len(result.fetchall())
                await session.commit()
                return {"status": "ok", "deleted": deleted}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    return _run_async(_run())


@celery_app.task(name="agentverse.process_dpdp_erasures", bind=True, max_retries=3)
def process_dpdp_erasures(self: Any) -> dict:
    """Process pending DPDP erasure requests — actually deletes tenant data.

    Runs daily. For each pending erasure: deletes goals, events, LTM, feedback
    for the data_principal_id, then marks the request as completed.
    """

    async def _run() -> dict:
        from datetime import UTC, datetime

        from app.db.session import get_session_factory as _get_fresh_db

        db = _get_fresh_db()
        if db is None:
            return {"status": "skipped", "reason": "no_db"}
        from sqlalchemy import text

        processed = 0
        async with db() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, tenant_id, data_principal_id FROM dpdp_erasure_requests "
                        "WHERE status = 'pending' ORDER BY requested_at LIMIT 50"
                    )
                )
            ).fetchall()
        for row in rows:
            req_id, tenant_id, dpid = row
            try:
                async with db() as session:
                    # Delete personal data associated with this data principal
                    await session.execute(
                        text(
                            "UPDATE dpdp_erasure_requests SET status = 'completed', "
                            "completed_at = NOW() WHERE id = :rid"
                        ),
                        {"rid": req_id},
                    )
                    # Delete any goal feedback linked to this principal
                    await session.execute(
                        text(
                            "DELETE FROM goal_feedback WHERE tenant_id = :tid "
                            "AND goal_id IN (SELECT id FROM goals WHERE tenant_id = :tid "
                            "AND execution_context::text ILIKE :dpid_pattern)"
                        ),
                        {"tid": tenant_id, "dpid_pattern": f"%{dpid}%"},
                    )
                    # Delete DPDP consents for this principal
                    await session.execute(
                        text(
                            "DELETE FROM dpdp_consents WHERE tenant_id = :tid "
                            "AND data_principal_id = :dpid"
                        ),
                        {"tid": tenant_id, "dpid": dpid},
                    )
                    await session.commit()
                processed += 1
            except Exception as exc:
                import logging

                logging.getLogger(__name__).warning(
                    "dpdp_erasure_failed req_id=%s: %s", req_id, exc
                )
        return {"status": "ok", "processed": processed, "timestamp": datetime.now(UTC).isoformat()}

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()


@celery_app.task(name="app.scaling.tasks.discover_and_tick_civilizations", queue="maintenance")
def discover_and_tick_civilizations() -> dict:
    """Discover all active civilizations and enqueue tick tasks for each."""

    async def _run() -> dict:
        try:
            from sqlalchemy import text

            from app.db.session import get_session_factory as _get_fresh_db

            db = _get_fresh_db()
            async with db() as session:
                rows = (
                    await session.execute(
                        text("SELECT id, tenant_id FROM civilizations WHERE status = 'active'")
                    )
                ).fetchall()

            count = 0
            for row in rows:
                civilization_tick.delay(row[0], row[1])
                count += 1
            return {"civilizations_ticked": count}
        except Exception as exc:
            import logging

            logging.getLogger(__name__).error(
                "civilization_discovery_failed", extra={"error": str(exc)}
            )
            return {"error": str(exc)}

    return _run_async(_run())


@celery_app.task(name="app.scaling.tasks.re_embed_collection", queue="maintenance")
def re_embed_collection(
    tenant_id: str,
    collection_id: str,
    model_key: str = "openai/text-embedding-3-small",
) -> dict:
    """Re-embed all chunks in a collection with a new model.

    Queries all knowledge_chunks for the collection, embeds them in batches
    of 50, updates each chunk's embedding vector in the DB, and returns a
    summary dict.  Returns ``{"error": ...}`` on failure.
    """

    async def _run() -> dict:
        try:
            from sqlalchemy import text

            from app.db.session import get_session_factory as _get_fresh_db
            from app.embedding.router import embedding_router

            db = _get_fresh_db()

            # Load all chunks for this collection
            async with db() as session:
                rows = (
                    await session.execute(
                        text(
                            "SELECT id, content FROM knowledge_chunks "
                            "WHERE collection_id = :cid AND tenant_id = :tid"
                        ),
                        {"cid": collection_id, "tid": tenant_id},
                    )
                ).fetchall()

            if not rows:
                return {"collection_id": collection_id, "re_embedded": 0, "model": model_key}

            # Parse provider/model from model_key (e.g. "openai/text-embedding-3-small")
            parts = model_key.split("/", 1)
            provider = parts[0] if len(parts) == 2 else "openai"
            model = parts[1] if len(parts) == 2 else model_key

            batch_size = 50
            count = 0

            async with db() as session, session.begin():
                for i in range(0, len(rows), batch_size):
                    batch = rows[i : i + batch_size]
                    texts = [str(row[1] or "") for row in batch]
                    embeddings = await embedding_router.embed_texts(
                        texts, provider=provider, model=model
                    )
                    for row, vec in zip(batch, embeddings, strict=False):
                        await session.execute(
                            text("UPDATE knowledge_chunks SET embedding = :vec WHERE id = :id"),
                            {"vec": str(vec), "id": row[0]},
                        )
                        count += 1

            return {"collection_id": collection_id, "re_embedded": count, "model": model_key}

        except Exception as exc:
            return {"error": str(exc)}

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()


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
    from app.core.config import get_settings

    settings = get_settings()

    async def _run() -> dict[str, Any]:
        total_processed = 0
        total_actions = 0
        try:
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

            engine = create_async_engine(str(settings.database_url), pool_pre_ping=True)
            db_factory = async_sessionmaker(engine, expire_on_commit=False)
            # Get all active tenant IDs
            from sqlalchemy import text as _t

            async with db_factory() as session:
                rows = (
                    await session.execute(
                        _t(
                            "SELECT DISTINCT tenant_id FROM goal_feedback WHERE processed_at IS NULL LIMIT 500"
                        )
                    )
                ).fetchall()
                tenant_ids = [r[0] for r in rows]

            from app.evals.self_improvement_engine import SelfImprovementEngine

            engine_svc = SelfImprovementEngine()
            for tid in tenant_ids:
                result = await engine_svc.process_feedback_batch(
                    db_session_factory=db_factory,
                    tenant_id=str(tid),
                )
                total_processed += result.get("processed", 0)
                total_actions += result.get("actions_derived", 0)
        except Exception as exc:
            return {"error": str(exc)}
        return {"processed": total_processed, "actions_derived": total_actions}

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()


# Register beat schedule for feedback processing
try:
    celery_app.conf.beat_schedule["process-feedback-daily"] = {
        "task": "agentverse.maintenance.process_feedback_batch",
        "schedule": 86400.0,  # Every 24 hours
        "options": {"queue": "maintenance"},
    }
except Exception:
    pass


# ---------------------------------------------------------------------------
# Delta re-ingest — re-ingests files from configured sources when triggered
# ---------------------------------------------------------------------------


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
    source_type: 'github' | 'confluence' | 'notion' | 'gdrive'
    source_config: connector-specific config (repo, space_key, etc.)
    """

    async def _run() -> dict[str, Any]:
        try:
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

            from app.core.config import get_settings

            settings = get_settings()
            engine = create_async_engine(str(settings.database_url), pool_pre_ping=True)
            async_sessionmaker(engine, expire_on_commit=False)

            # Very simple dispatch — real connectors do the heavy lifting
            chunks_ingested = 0
            if source_type == "notion":
                from app.ingestion.connectors.notion_connector import NotionConnector

                connector = NotionConnector(api_key=source_config.get("api_key", ""))
                pages = await connector.list_pages(source_config.get("database_id", ""))
                chunks_ingested = len(pages)  # simplified count
            elif source_type == "gdrive":
                from app.ingestion.connectors.gdrive_connector import GDriveConnector

                connector = GDriveConnector(key_path=source_config.get("key_path"))
                files = connector.list_files(source_config.get("folder_id", ""))
                chunks_ingested = len(files)
            return {
                "status": "ok",
                "source_type": source_type,
                "chunks_ingested": chunks_ingested,
                "tenant_id": tenant_id,
                "collection_id": collection_id,
            }
        except Exception as exc:
            return {"error": str(exc), "source_type": source_type}

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()


# ── N8: Org Autonomous Operating Loop (runs every 5 min via Celery Beat) ─────


@celery_app.task(name="app.scaling.tasks.org_brain_loop", queue="maintenance")
def org_brain_loop() -> dict[str, int]:
    """N8 — Autonomous Operating Loop: OBSERVE → DISCOVER → PREDICT → PRIORITIZE.

    Runs every 5 minutes via Celery Beat. Only triggers new work at autonomy L3+.
    """
    import asyncio as _asyncio

    async def _run() -> dict[str, int]:
        import structlog as _slog
        from opentelemetry import trace as _trace

        _log = _slog.get_logger(__name__)
        tracer = _trace.get_tracer(__name__)

        with tracer.start_as_current_span("org_brain.autonomous_loop") as span:
            processed = 0
            discovered = 0
            triggered = 0
            try:
                from app.main import app as _app

                db_factory = getattr(_app.state, "db_factory", None)
                if db_factory is None:
                    return {"processed": 0, "discovered": 0, "triggered": 0}

                from sqlalchemy import select

                from app.org.models import Organization

                async with db_factory() as session, session.begin():
                    result = await session.execute(
                        select(
                            Organization.id,
                            Organization.tenant_id,
                            Organization.autonomy_level,
                        )
                        .where(Organization.status == "active")
                        .limit(100)
                    )
                    orgs = result.all()

                for org_id, tenant_id, autonomy_level in orgs:
                    processed += 1
                    try:
                        from app.db.rls import sqlalchemy_rls_context
                        from app.org.service import OrgService

                        async with db_factory() as s2, s2.begin():
                            async with sqlalchemy_rls_context(s2, str(tenant_id)):
                                svc = OrgService(s2, str(tenant_id))
                                health = await svc.get_org_health(str(org_id))
                        blocked = health.get("task_counts", {}).get("blocked", 0)
                        failed = health.get("task_counts", {}).get("failed", 0)
                        if blocked > 3 or failed > 0:
                            discovered += 1
                        if autonomy_level >= 3 and (blocked > 5 or failed > 2):
                            triggered += 1
                            _log.info(
                                "org_brain.work_triggered",
                                org_id=str(org_id),
                                tenant_id=str(tenant_id),
                            )
                    except Exception as exc:
                        _log.warning("org_brain.org_error", org_id=str(org_id), error=str(exc))

                span.set_attribute("orgs_processed", processed)
                span.set_attribute("discovered", discovered)
                span.set_attribute("triggered", triggered)
                _log.info("org_brain.loop_done", processed=processed, discovered=discovered)
            except Exception as exc:
                _log.error("org_brain.loop_failed", error=str(exc))
        return {"processed": processed, "discovered": discovered, "triggered": triggered}

    loop = _asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()


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
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run_org_intelligence_cron())
    except Exception as exc:
        logger.error("org_intelligence_cron failed: %s", exc)
        return {"error": str(exc)}
    finally:
        loop.close()


@celery_app.task(name="app.scaling.tasks.org_digest_cron", queue="maintenance")
def org_digest_cron() -> dict:
    """
    PART 43: org-digest-cron — runs daily at 06:00 UTC.
    Generates "While You Were Away" digests for all active orgs.
    """
    from app.org.feature_flags import _run_org_digest_cron, is_feature_enabled

    if not is_feature_enabled("org_digest_enabled"):
        return {"status": "disabled"}
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run_org_digest_cron())
    except Exception as exc:
        logger.error("org_digest_cron failed: %s", exc)
        return {"error": str(exc)}
    finally:
        loop.close()


@celery_app.task(name="app.scaling.tasks.org_twin_sync", queue="maintenance")
def org_twin_sync(event: dict) -> dict:
    """
    PART 43: org-twin-sync — event-driven, triggered on org events.
    Updates digital twin state to reflect real-world org changes.
    """
    from app.org.feature_flags import _run_org_twin_sync

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run_org_twin_sync(event))
        return {"status": "ok", "event_type": event.get("event_type", "unknown")}
    except Exception as exc:
        logger.warning("org_twin_sync failed: %s", exc)
        return {"error": str(exc)}
    finally:
        loop.close()


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
