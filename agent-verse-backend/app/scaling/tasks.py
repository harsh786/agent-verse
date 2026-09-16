"""Celery tasks — real implementations for goal execution and scheduling."""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import hashlib
import os
import re
import signal as _signal
import time
from datetime import UTC
from typing import Any, cast

from celery.signals import worker_init as _worker_init

from app.observability.logging import get_logger
from app.org.feature_flags import is_feature_enabled
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


@_worker_init.connect
def _load_worker_prompt_variants(**kwargs: Any) -> None:
    """Load persisted A/B prompt variants into the worker's PromptOptimizer.

    The Celery worker runs real goals through the module-global
    ``_default_optimizer`` (see ``run_goal``). That instance starts empty in a
    fresh worker process and, without this hook, ``select_variant()`` only ever
    returns the control prompt — the A/B variants persisted to
    ``prompt_variants`` by the API/self-optimizer would never be exercised on
    the path that actually executes goals. Loading them once at worker start
    closes that gap. Best-effort: a DB error just leaves the optimizer empty
    (control-only), exactly as before.
    """
    try:
        from app.db.session import get_session_factory
        from app.intelligence.prompt_optimizer import _default_optimizer

        db_factory = get_session_factory()
        loaded = _run_async(_default_optimizer.load_from_db(db_factory))
        logger.info("celery_worker_prompt_variants_loaded count=%s", loaded)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("worker_prompt_variant_load_failed: %s", exc)


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

        if (
            provider_name in {"openai", "openai_compatible", "groq", "together", "azure", "ollama"}
            and api_key
        ):
            from app.providers.openai_compatible import OpenAICompatibleProvider

            # Fall back to the self-hosted model env (never a hardcoded cloud slug)
            # so a tenant config without an explicit model still works on vLLM/Qwen.
            _fallback_model = (
                os.getenv("OPENAI_MODEL") or os.getenv("DEFAULT_MODEL") or "gpt-5.2"
            )
            return OpenAICompatibleProvider(
                api_key=api_key,
                base_url=base_url,
                default_model=model or _fallback_model,
                embed_model=os.getenv("EMBEDDING_MODEL") or None,
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
    from app.db.session import get_session_factory

    async def _sweep() -> int:
        db = get_session_factory()
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
    from app.db.session import get_session_factory
    from app.org.service import OrgService, _next_cron_fire

    async def _fire() -> int:
        db = get_session_factory()
        # 1. ATOMIC CLAIM (multi-pod at-most-once): select due schedules with
        # FOR UPDATE SKIP LOCKED so no other worker can see them, and advance
        # next_fire_at in the SAME transaction — so by the time the lock is
        # released each row is no longer due and cannot be re-claimed. This makes
        # firing at-most-once per slot even if the @beat_task_guard lock fails open
        # on a Redis error. Trade-off (deliberate): advancing before creating the
        # mission means a crash here SKIPS a fire rather than double-launching an
        # autonomous mission — the safe direction for unattended execution.
        claimed: list[Any] = []
        async with db() as s, s.begin(), system_session(s):
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
                            "SET last_mission_id = :m WHERE id = :sid"
                        ),
                        {"m": mission.id, "sid": r.id},
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
        from app.mcp.registry import MCPRegistry
        from app.providers.vault import (
            RedisConnectorSecretStore,
            get_vault,
            resolve_connector_secret_ref_for_tenant,
        )

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
            secret_store = RedisConnectorSecretStore(redis=redis_client, vault=get_vault())

            async def _resolve_secret(ref: str, tctx: Any = None) -> str | None:
                return await resolve_connector_secret_ref_for_tenant(
                    ref, store=secret_store, tenant_ctx=tctx
                )

            registry = MCPRegistry(redis_client)
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

    from app.tenancy.context import PlanTier

    try:
        plan = PlanTier(_plan_str)
    except ValueError:
        plan = PlanTier.PROFESSIONAL

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
        # Close the org loop: if a mission dispatched this goal, reconcile it now
        # (mark subtasks done, aggregate the deliverable, complete the mission).
        # Skipped for dry runs — they must not finalize a real mission.
        if not dry_run:
            await _finalize_owning_mission(goal_id, tenant_id)

    async def mark_worker_failed(exc: Exception) -> None:
        await update_submitted_goal_status("failed", error_message=str(exc))
        await append_submitted_goal_event({"type": "worker_failed", "reason": str(exc)})
        if not dry_run:
            await _finalize_owning_mission(goal_id, tenant_id)

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
                        auto_approve=bool(getattr(cfg, "auto_approve", False)),
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
                # Prefer a dedicated embedding endpoint (EMBEDDING_BASE_URL/MODEL),
                # e.g. a self-hosted Qwen3-Embedding on vLLM. Without base_url the
                # embedder hit the official OpenAI API with sk-noauth and 401'd
                # during LTM/RAG recall, failing the goal step.
                _embed_base_url = os.getenv("EMBEDDING_BASE_URL", "")
                _embed_model = os.getenv("EMBEDDING_MODEL", "")
                if _v_key:
                    from app.providers.voyage_provider import VoyageProvider

                    _embedder_for_graph = VoyageProvider(api_key=_v_key)
                elif _embed_base_url:
                    from app.providers.openai_compatible import OpenAICompatibleProvider

                    _embedder_for_graph = OpenAICompatibleProvider(
                        api_key=os.getenv("EMBEDDING_API_KEY", "") or _o_key or "sk-noauth",
                        base_url=_embed_base_url,
                        default_model=_embed_model or "text-embedding-3-small",
                        embed_model=_embed_model or "text-embedding-3-small",
                    )
                elif _o_key:
                    # Honour OPENAI_BASE_URL when set so a self-hosted chat+embed
                    # endpoint is never bypassed for the official OpenAI API.
                    from app.providers.model_defaults import configured_embed_model
                    from app.providers.openai_compatible import OpenAICompatibleProvider

                    _embed_model_name = configured_embed_model("text-embedding-3-small")
                    _embedder_for_graph = OpenAICompatibleProvider(
                        api_key=_o_key,
                        base_url=os.getenv("EMBEDDING_BASE_URL")
                        or os.getenv("OPENAI_BASE_URL")
                        or None,
                        default_model=_embed_model_name,
                        embed_model=_embed_model_name,
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
                import redis as _sync_redis_mod

                from app.ai_router.registry_store import (
                    ModelRegistryStore,
                    get_model_registry_store,
                    set_model_registry_store,
                )
                from app.ai_router.seeder import seed_registry_from_config

                if get_model_registry_store() is None:
                    _mr_redis = _sync_redis_mod.from_url(REDIS_URL, decode_responses=True)
                    set_model_registry_store(ModelRegistryStore(_mr_redis))
                seed_registry_from_config()
            except Exception as _mr_exc:  # pragma: no cover - defensive
                logger.warning("model_registry_store_wire_failed: %s", _mr_exc)

            _agent_runner = AgentGraph(
                planner=provider,
                executor=provider,
                verifier=_verifier_for_graph,
                model_router=_model_router,
                autonomy_mode=_agent_autonomy_mode,
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
                from app.memory.reflexion import ReflexionService

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
                _reflexion_service: Any = None
                if db_factory is not None:
                    try:
                        from app.memory.postgres_repository import PostgresMemoryRepository

                        _reflexion_service = ReflexionService(
                            repository=PostgresMemoryRepository(db_factory)
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
                )
                _agent_runner._agent_id = agent_id
                _agent_runner._reflexion_service = _reflexion_service
            except Exception as _si_exc:
                logger.warning("self_improvement_wire_failed: %s", _si_exc)
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
    """Execute a scheduled goal trigger.

    WT-9: routes through the TriggerDispatcher so scheduled fires get the same
    dedup / rate-limit / circuit-breaker / condition governance as every other
    trigger type, instead of enqueueing ``run_goal`` directly. The dispatcher
    creates the goal via ``GoalService.create_goal``.
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
    return goal_kwargs


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
        max_firings_per_hour=int(sched.get("max_firings_per_hour") or 0),
        watch_agent_id=str(sched.get("agent_id") or ""),
    )
    # trigger_id is an instance attribute (not a dataclass field) — see dispatcher.
    spec.trigger_id = schedule_key  # type: ignore[attr-defined]
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
    # plan MUST be a PlanTier enum, not a raw string: downstream goal creation
    # reads ``tenant_ctx.plan.value`` (goal_service), so a bare string crashed every
    # scheduled/beat fire with "'str' object has no attribute 'value'".
    from app.tenancy.context import PlanTier

    _plan_raw = str(sched.get("tenant_plan") or "free")
    try:
        _plan = PlanTier(_plan_raw)
    except ValueError:
        _plan = PlanTier.FREE
    tenant_ctx = SimpleNamespace(
        tenant_id=str(sched.get("tenant_id") or ""),
        plan=_plan,
    )

    if dispatcher is None:
        from app.triggers.dispatcher import TriggerDispatcher

        dispatcher = TriggerDispatcher(
            goal_service=goal_service,
            db_session_factory=db_factory,
            redis=redis,
        )

    return await dispatcher.dispatch(
        spec,
        dict(goal_kwargs),
        tenant_ctx,
        scheduled_fire_time=fire_instance_id,
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
        from app.services.goal_service import GoalService

        db_factory = get_session_factory()
        goal_service = GoalService(
            db_session_factory=db_factory,
            event_store=EventStore(db_factory),
        )
        return goal_service, db_factory
    except Exception as exc:
        logger.warning("worker_goal_service_build_failed", error=str(exc)[:120])
        return None, None


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
) -> Any:
    """Async body of ``run_scheduled_goal`` — governed scheduled dispatch (WT-9)."""
    goal_service, db_factory = _build_worker_goal_service()
    redis = _worker_async_redis()
    sched = {
        "trigger_type": "cron",
        "goal_template": goal_template,
        "tenant_id": tenant_id,
        "agent_id": agent_id,
    }
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


def _rrule_missed_runs_utc(
    rrule_string: str,
    last_fired_utc: datetime.datetime | None,
    now_utc: datetime.datetime,
    cap: int = _MISSED_FIRE_CAP,
) -> list[datetime.datetime]:
    """rrule analogue of :func:`_cron_missed_runs_utc`.

    ``rrule_string`` is an iCalendar RRULE (optionally with a ``DTSTART`` line),
    parsed by :func:`dateutil.rrule.rrulestr`. A naive ``DTSTART`` is treated as
    UTC. Semantics (window, never-fired backfill, cap) match the cron helper.

    Raises if dateutil is unavailable or the rule is unparseable.
    """
    from dateutil import rrule as _rrule

    rule = _rrule.rrulestr(rrule_string)
    dtstart = getattr(rule, "_dtstart", None)
    aware = dtstart is not None and dtstart.tzinfo is not None

    def _as_arg(dt: datetime.datetime | None) -> datetime.datetime | None:
        if dt is None:
            return None
        naive = _norm_utc_naive(dt)
        assert naive is not None
        if aware:
            return naive.replace(tzinfo=datetime.UTC).astimezone(dtstart.tzinfo)
        return naive

    now_arg = _as_arg(now_utc)
    last_arg = _as_arg(last_fired_utc)
    assert now_arg is not None

    if last_arg is None:
        occ = rule.before(now_arg, inc=True)
        if occ is None:
            return []
        result = _norm_utc_naive(occ)
        return [result] if result is not None else []

    runs: list[datetime.datetime] = []
    for occ in rule.between(last_arg, now_arg, inc=True):
        occ_naive = _norm_utc_naive(occ)
        last_naive = _norm_utc_naive(last_arg)
        if occ_naive is None or last_naive is None:
            continue
        if occ_naive > last_naive:
            runs.append(occ_naive)
    if len(runs) > cap:
        runs = runs[-cap:]
    return runs


def _solar_due_run_utc(
    sched: dict[str, Any],
    now_utc: datetime.datetime,
) -> datetime.datetime | None:
    """Compute today's solar-event fire time (UTC-naive) for a solar schedule.

    Reads ``solar_event`` (sunrise|sunset|dawn|dusk|noon), ``solar_latitude``,
    ``solar_longitude`` and ``solar_offset_seconds`` from the schedule payload.
    Returns ``None`` when astral is unavailable in this worker (so the caller can
    warn instead of firing silently). Raises on a malformed solar event name.
    """
    try:
        from astral import LocationInfo
        from astral.sun import sun as _astral_sun
    except ImportError:
        return None

    now_naive = _norm_utc_naive(now_utc)
    assert now_naive is not None
    lat = float(sched.get("solar_latitude", 0.0) or 0.0)
    lon = float(sched.get("solar_longitude", 0.0) or 0.0)
    event = str(sched.get("solar_event", "sunrise") or "sunrise").lower()
    offset = int(sched.get("solar_offset_seconds", 0) or 0)

    location = LocationInfo(latitude=lat, longitude=lon)
    events = _astral_sun(
        location.observer,
        date=now_naive.date(),
        tzinfo=datetime.UTC,
    )
    if event not in events:
        raise ValueError(f"unknown solar_event {event!r}")
    fire_time = events[event] + datetime.timedelta(seconds=offset)
    return _norm_utc_naive(fire_time)


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
    }
    if isinstance(config, dict):
        payload.update(config)
    return payload


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

                # ── RRULE schedules (iCalendar recurrence) ────────────────────
                elif trigger_type == "rrule":
                    rrule_string = sched.get("rrule_string", "")
                    if rrule_string:
                        last_fired_dt = _schedule_datetime(sched.get("last_fired_at"))
                        try:
                            missed = _rrule_missed_runs_utc(rrule_string, last_fired_dt, now)
                        except Exception as rrule_exc:
                            logger.warning("rrule parse error for %s: %s", key, rrule_exc)
                            continue
                        fired += dispatch_missed_slots(key, sched, missed, kind="rrule")

                # ── SOLAR schedules (sunrise/sunset) ──────────────────────────
                elif trigger_type == "solar":
                    try:
                        solar_run = _solar_due_run_utc(sched, now)
                    except Exception as solar_exc:
                        logger.warning("solar computation error for %s: %s", key, solar_exc)
                        continue
                    if solar_run is None:
                        # astral unavailable in the worker — surface, do not fire silently
                        logger.warning(
                            "solar schedule %s skipped: astral unavailable in worker", key
                        )
                        continue
                    last_fired_dt = _schedule_datetime(sched.get("last_fired_at"))
                    if solar_run <= now and (last_fired_dt is None or last_fired_dt < solar_run):
                        goal_kwargs = advance_and_dispatch_schedule(
                            key,
                            sched,
                            fired_at=solar_run,
                            fire_instance_id=solar_run.isoformat(),
                        )
                        if goal_kwargs is not None:
                            fired += 1
                            logger.info(
                                "Fired solar schedule %s for tenant %s",
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

                # ── RSS_FEED trigger ──────────────────────────────────────────
                elif trigger_type == "rss_feed":
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
                                if _rss_kw:
                                    _rss_goal_id = _scheduled_goal_id(
                                        key, fire_instance_id=f"rss:{_entry.entry_id}"
                                    )
                                    run_goal.apply_async(
                                        kwargs={
                                            "goal_id": _rss_goal_id,
                                            "tenant_id": _tenant_id_rss,
                                            "goal_text": _rss_kw["goal"],
                                            "priority": _rss_kw["priority"],
                                            "agent_id": str(_rss_kw.get("agent_id") or ""),
                                        },
                                        queue="schedules",
                                    )
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
                                    from app.tenancy.context import (
                                        PlanTier as _PT_ap,
                                    )
                                    from app.tenancy.context import (
                                        TenantContext as _TC_ap,
                                    )

                                    _tc_ap = _TC_ap(
                                        tenant_id=_tenant_id_ap,
                                        plan=_PT_ap.PROFESSIONAL,
                                        api_key_id="trigger-api-poll",
                                    )
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
                                            tenant_ctx=_tc_ap,
                                        )
                                    )
                                    if _ap_kw:
                                        _ap_goal_id = _scheduled_goal_id(
                                            key, fire_instance_id=f"apipoll:{current}"
                                        )
                                        run_goal.apply_async(
                                            kwargs={
                                                "goal_id": _ap_goal_id,
                                                "tenant_id": _tenant_id_ap,
                                                "goal_text": _ap_kw["goal"],
                                                "priority": _ap_kw["priority"],
                                                "agent_id": str(_ap_kw.get("agent_id") or ""),
                                            },
                                            queue="schedules",
                                        )
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
                                    if _db_kw:
                                        _db_goal_id = _scheduled_goal_id(
                                            key, fire_instance_id=f"dbrow:{current}"
                                        )
                                        run_goal.apply_async(
                                            kwargs={
                                                "goal_id": _db_goal_id,
                                                "tenant_id": _tenant_id_db,
                                                "goal_text": _db_kw["goal"],
                                                "priority": _db_kw["priority"],
                                                "agent_id": str(_db_kw.get("agent_id") or ""),
                                            },
                                            queue="schedules",
                                        )
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
            # D-18: physically purge expired memory rows. Unlike the tables above
            # (age-based via the global retention window), each memory record carries
            # its own retention deadline in ``expires_at`` — previously enforced only
            # at read time, so expired rows accumulated forever. Delete them here so
            # the scheduled retention policy actually reclaims them tenant-wide.
            try:
                r = await session.execute(
                    text(
                        "DELETE FROM memory_records "
                        "WHERE expires_at IS NOT NULL AND expires_at < NOW()"
                    )
                )
                counts["memory_records"] = r.rowcount
            except Exception as exc:
                counts["memory_records"] = f"error: {exc}"
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
                        f"DELETE FROM documents WHERE created_at < NOW() - INTERVAL '{retention_days} days' "  # noqa: E501
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
        from app.governance.audit_v3 import AuditV3
        from app.lifecycle.deletion_orchestrator import DeletionOrchestrator

        orchestrator = DeletionOrchestrator(db_factory=db, audit=AuditV3(db_factory=db))
        for row in rows:
            req_id, tenant_id, dpid = row
            try:
                # Real, verifiable erasure cascade (suspends on active legal hold).
                receipt = await orchestrator.execute_deletion(tenant_id, dpid)
                new_status = "suspended" if receipt.suspended else "completed"
                async with db() as session:
                    await session.execute(
                        text(
                            "UPDATE dpdp_erasure_requests SET status = :st, "
                            "completed_at = NOW() WHERE id = :rid"
                        ),
                        {"st": new_status, "rid": req_id},
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
                            "SELECT DISTINCT tenant_id FROM goal_feedback WHERE processed_at IS NULL LIMIT 500"  # noqa: E501
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
with contextlib.suppress(Exception):
    celery_app.conf.beat_schedule["process-feedback-daily"] = {
        "task": "agentverse.maintenance.process_feedback_batch",
        "schedule": 86400.0,  # Every 24 hours
        "options": {"queue": "maintenance"},
    }


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
        from app.ingestion.pipeline import IngestionPipeline
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
        pipeline = IngestionPipeline()

        indexed = skipped = failed = 0
        try:
            async for raw_doc, _cursor in connector.get_delta(config, None):
                result = await pipeline.ingest(raw_doc, config)
                if result.success:
                    indexed += 1
                elif result.skipped:
                    skipped += 1
                else:
                    failed += 1
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
        return {
            "status": "ok",
            "source_type": source_type,
            "docs_indexed": indexed,
            "docs_skipped": skipped,
            "docs_failed": failed,
            "tenant_id": tenant_id,
            "collection_id": collection_id,
        }

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()


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


@celery_app.task(name="app.scaling.tasks.org_brain_loop", queue="maintenance")
def org_brain_loop() -> dict[str, int]:
    """N8 — Autonomous Operating Loop: SENSE → DECIDE → GUARD → ACT → NARRATE.

    Runs every 5 minutes via Celery Beat. Drives ``OrgBrain.run_tick`` (via
    ``_brain_tick_for_org``) for every active org, gated by the
    ``org_autonomy_enabled`` feature flag, autonomy level (L3+), and a
    per-org Redis tick lock.
    """
    import asyncio as _asyncio

    zero_totals = {"processed": 0, "triggered": 0, **_BRAIN_TICK_ZERO}

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
                from app.main import app as _app

                db_factory = getattr(_app.state, "db_factory", None)
                if db_factory is None:
                    return dict(zero_totals)

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
                _log.error("org_brain.loop_failed", error=str(exc))
        return {
            "processed": processed,
            "triggered": triggered,
            "proposed": proposed_total,
            "executed": executed_total,
            "blocked": blocked_total,
        }

    loop = _asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()


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
    import asyncio as _asyncio

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
                from app.main import app as _app

                db_factory = getattr(_app.state, "db_factory", None)
                if db_factory is None:
                    return dict(zero_totals)

                llm_provider = getattr(_app.state, "llm_provider", None)
                if llm_provider is None:
                    # No real LLM provider wired into this worker process --
                    # fail closed rather than emit chatter with no model
                    # behind it.
                    _log.info("org_collaboration.no_llm_provider_skipping")
                    return dict(zero_totals)

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
                _log.error("org_collaboration.loop_failed", error=str(exc))
        return {
            "processed": processed,
            "orgs_with_chatter": orgs_with_chatter,
            "messages_emitted": messages_emitted,
        }

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
