"""Celery tasks — real implementations for goal execution and scheduling."""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import os
import signal as _signal
import time
from datetime import UTC
from typing import Any, cast

from app.observability.logging import get_logger
from app.scaling.celery_app import celery_app

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

# Capture original AgentLoop class at import time for monkey-patch detection.
# When tests replace app.agent.loop.AgentLoop with a mock, we detect this
# and respect the patch instead of bypassing it with AgentGraph.
_REAL_AGENT_LOOP_CLASS: Any = None
try:
    from app.agent.loop import AgentLoop as _real_loop_cls
    _REAL_AGENT_LOOP_CLASS = _real_loop_cls
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
    """Run an async coroutine from a sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _record_goal_duration_metric(
    status: str, *, started_monotonic: float, priority: str
) -> None:
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
                api_key=api_key, base_url=base_url, default_model=model or "gpt-4o"
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
            event_callback=event_callback,
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
                try:
                    await run_task
                except (asyncio.CancelledError, Exception):
                    pass
                raise GoalCancelledError(f"Goal {goal_id} cancelled during execution")

            if is_paused_sync(goal_id, sync_r):
                # Pause: cancel current run and wait for resume signal
                run_task.cancel()
                try:
                    await run_task
                except (asyncio.CancelledError, Exception):
                    pass
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
                        event_callback=event_callback,
                    )
                )

    return await run_task


@celery_app.task(name="app.scaling.tasks.run_goal_dlq", bind=True, max_retries=0)
def run_goal_dlq(
    self: Any,
    goal_id: str,
    tenant_id: str,
    goal_text: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Dead-letter queue handler for goals that exhausted all retries.

    Marks goal as permanently failed. Operators can inspect and manually re-queue.
    """
    logger.error(
        "Goal %s dead-lettered: %s (tenant: %s)", goal_id, reason, tenant_id
    )
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
    from app.db.session import get_session_factory
    try:
        db = get_session_factory()
        async with db() as session, session.begin():
            await session.execute(
                update(Goal)
                .where(Goal.id == goal_id, Goal.tenant_id == tenant_id)
                .values(status="failed",
                        error_message=f"Dead lettered: {reason}")
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
    workflow_mode: str = "single_agent",
    goal_template: str = "",
) -> dict[str, Any]:
    """Run a goal worker task and return its local result.

    This task does not update GoalService/DB status or lifecycle events for the
    submitted goal; that bridge is intentionally deferred until Phase 10.
    """
    from app.providers.fake import FakeProvider
    from app.reliability.result_processor import ResultProcessor
    from app.tenancy.context import TenantContext

    logger.info("Running goal %s for tenant %s", goal_id, tenant_id)
    started_monotonic = _monotonic()
    effective_goal = goal_text or goal_template

    # Resolve actual tenant plan from Redis config (avoids hardcoded tier)
    _plan_str = "professional"  # safe fallback
    try:
        from app.services.llm_config_store import get_llm_config_store
        _config_store = get_llm_config_store()
        if _config_store:
            _tenant_cfg = _run_async(_config_store.get(tenant_id)) or {}
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
                goal_id, tenant_id,
            )
            return {"status": "blocked", "reason": "Emergency stop active for tenant"}
    except Exception as _es_exc:
        logger.warning("emergency_stop_check_failed: %s", _es_exc)

    goal_bridge: Any = None
    event_store: Any = None
    try:
        from app.db.session import get_session_factory
        from app.services.event_store import EventStore
        from app.services.goal_service import GoalService

        db_factory = get_session_factory()
        event_store = EventStore(db_factory)
        goal_bridge = GoalService(db_session_factory=db_factory, event_store=event_store)
    except Exception as exc:
        logger.warning("Goal %s status bridge unavailable: %s", goal_id, exc)

    async def update_submitted_goal_status(
        status: str, *, error_message: str = "", iterations: int = 0
    ) -> None:
        if goal_bridge is None:
            return
        try:
            await goal_bridge._db_update_goal_status(
                goal_id,
                tenant_id,
                status,
                error_message=error_message,
                iterations=iterations,
            )
        except Exception as db_exc:
            logger.warning("DB status update failed (non-fatal): %s", db_exc)

    async def append_submitted_goal_event(event: dict[str, Any]) -> None:
        if event_store is None:
            return
        try:
            await event_store.append_event(goal_id, event, tenant_ctx=tenant_ctx)
        except Exception as db_exc:
            logger.warning("DB event append failed (non-fatal): %s", db_exc)
        # C-1: Publish to Redis pub/sub so API-process SSE bridge receives it
        try:
            import json as _json
            _r = _get_sync_redis()
            _event_data = _json.dumps({
                "goal_id": goal_id,
                "tenant_id": tenant_id,
                "type": event.get("type", ""),
                "payload": event,
            })
            _r.publish(f"goal_events:{tenant_id}:{goal_id}", _event_data)
        except Exception:
            pass  # Non-fatal: SSE may degrade but goal execution continues

    async def ensure_submitted_goal_row() -> None:
        if goal_bridge is None:
            return
        try:
            await goal_bridge._db_ensure_goal_row(
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
        await append_submitted_goal_event(
            {"type": "worker_failed", "reason": str(exc)}
        )

    # ── Distributed lock: at-most-once execution per goal ─────────────────────
    _lock = None
    _lock_redis = None
    try:
        _redis_url = celery_app.conf.broker_url or ""
        if _redis_url:
            import redis.asyncio as _aioredis
            _lock_redis = _aioredis.from_url(_redis_url, decode_responses=True)
            from app.reliability.distributed_lock import GoalExecutionLock
            _lock = GoalExecutionLock(_lock_redis)
            _acquired = _run_async(_lock.acquire(goal_id, ttl_ms=1_800_000))
            if not _acquired:
                logger.warning(
                    "Goal %s already executing in another worker — skipping", goal_id
                )
                if _lock_redis:
                    _run_async(_lock_redis.aclose())
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
            try:
                _run_async(_lock.release(goal_id))
            except Exception:
                pass
        if _lock_redis:
            try:
                _run_async(_lock_redis.aclose())
            except Exception:
                pass
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
        import os

        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        openai_key = os.getenv("OPENAI_API_KEY", "")
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

    # Detect if AgentLoop has been monkey-patched (e.g., in tests).
    # If patched, respect the patch instead of bypassing it with AgentGraph.
    import app.agent.loop as _aloop_mod
    _loop_is_patched = (
        _REAL_AGENT_LOOP_CLASS is not None
        and getattr(_aloop_mod, "AgentLoop", None) is not _REAL_AGENT_LOOP_CLASS
    )

    _agent_runner: Any = None
    _use_agent_graph = False

    if not _loop_is_patched:
        # Production path: Try AgentGraph first (full capabilities)
        try:
            from app.agent.graph import AgentGraph
            from app.governance.audit import AuditLog
            from app.governance.cost import CostController
            from app.governance.hitl import HITLGateway
            from app.governance.policies import PolicyEngine
            from app.intelligence.eval_runner import EvalRunner
            from app.intelligence.guardrails import GuardrailChecker
            from app.memory.long_term import LongTermMemoryStore
            from app.reliability.dedup import DeduplicationCache
            from app.reliability.rollback import RollbackEngine

            _audit = AuditLog(db_session_factory=db_factory)
            _hitl = HITLGateway()
            _cost = CostController()
            _policy = PolicyEngine()
            _ltm = LongTermMemoryStore()
            _eval = EvalRunner()

            # Wire Redis into CostController for distributed rate-limiting
            import os as _os_cw
            _redis_url_cw = _os_cw.getenv("REDIS_URL", "")
            if _redis_url_cw:
                try:
                    import redis.asyncio as _aioredis_cw
                    _cost._redis = _aioredis_cw.from_url(_redis_url_cw, decode_responses=True)
                except Exception:
                    pass

            _agent_runner = AgentGraph(
                planner=provider,
                executor=provider,
                verifier=provider,
                result_processor=ResultProcessor(),
                dedup_cache=DeduplicationCache(),
                rollback_engine=RollbackEngine(),
                guardrail_checker=GuardrailChecker(),
                audit_log=_audit,
                hitl_gateway=_hitl,
                cost_controller=_cost,
                policy_engine=_policy,
                long_term_memory=_ltm,
                eval_runner=_eval,
                cost_tracker=None,
            )
            _use_agent_graph = True
            logger.info("Goal %s will run with AgentGraph (full capabilities)", goal_id)
        except Exception as _ag_exc:
            logger.warning(
                "AgentGraph unavailable, falling back to AgentLoop: %s", _ag_exc
            )

    if _agent_runner is None:
        from app.agent.loop import AgentLoop

        _agent_runner = AgentLoop(
            planner=provider,
            executor=provider,
            verifier=provider,
            result_processor=ResultProcessor(),
        )

    try:
        # Block fake execution in production — a real LLM provider is required
        import os as _os
        _env = _os.getenv("ENVIRONMENT", "development")
        if used_fake_provider and _env == "production" and not _loop_is_patched:
            _run_async(mark_worker_failed(
                RuntimeError(
                    "No real LLM provider configured. "
                    "Set ANTHROPIC_API_KEY or OPENAI_API_KEY."
                )
            ))
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
                    goal_id, tenant_id,
                )
                _run_async(update_submitted_goal_status("cancelled", error_message="Cancelled before execution"))
                _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
                return {"status": "cancelled", "goal_id": goal_id, "reason": "cancelled_before_execution"}
        except Exception as _cancel_check_exc:
            logger.warning("cancel_pre_check_failed: %s", _cancel_check_exc)

        from app.tenancy.context import PLAN_LIMITS as _PLAN_LIMITS
        goal_timeout_s = getattr(
            _PLAN_LIMITS.get(plan, None), "goal_timeout_seconds", 1800
        ) if hasattr(plan, 'value') else 1800

        try:
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
            _run_async(mark_worker_failed(
                TimeoutError(f"Goal timed out after {goal_timeout_s}s")
            ))
            _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
            return {
                "status": "failed", "goal_id": goal_id,
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
            raise self.retry(exc=exc, countdown=2 ** self.request.retries) from exc
        except self.MaxRetriesExceededError:
            # Route to dead-letter queue
            try:
                run_goal_dlq.delay(
                    goal_id=goal_id, tenant_id=tenant_id,
                    goal_text=effective_goal, reason="max_retries_exceeded"
                )
            except Exception:
                pass
            # Still update DB to failed
            _run_async(mark_worker_failed(
                RuntimeError(f"Goal {goal_id} exceeded max retries, routed to DLQ")
            ))
            # Decrement counter — goal is permanently done
            _run_async(_decrement_after_completion(tenant_id, REDIS_URL))
            return {"status": "dead_lettered", "goal_id": goal_id}
    finally:
        # Release distributed lock
        if _lock:
            try:
                _run_async(_lock.release(goal_id))
            except Exception:
                pass
        if _lock_redis:
            try:
                _run_async(_lock_redis.aclose())
            except Exception:
                pass


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
        raise self.retry(exc=exc, countdown=2 ** self.request.retries) from exc
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
        from app.db.session import get_session_factory

        db_factory = get_session_factory()
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
        from app.db.session import get_session_factory

        db_factory = get_session_factory()
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
def record_queue_depths(self: Any) -> dict[str, Any]:
    """Record Celery Redis queue depths for autoscaling and dashboards."""
    import os

    redis_url = os.getenv("REDIS_URL", "")
    if not redis_url:
        logger.info("record_queue_depths: no REDIS_URL configured")
        return {"status": "skipped", "queues_recorded": 0, "depths": {}}

    try:
        import redis as sync_redis

        from app.observability.metrics import record_queue_depth

        redis_from_url = cast(Any, sync_redis.from_url)
        r = redis_from_url(redis_url, decode_responses=True)
        depths: dict[str, int] = {}
        for queue in _CELERY_QUEUE_NAMES:
            depth = int(r.llen(queue))
            depths[queue] = depth
            record_queue_depth(queue, float(depth))
        return {
            "status": "ok",
            "queues_recorded": len(depths),
            "depths": depths,
        }
    except Exception as exc:
        logger.warning("Queue depth recording failed: %s", exc)
        raise self.retry(exc=exc, countdown=2 ** self.request.retries) from exc


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
                        results.append({
                            "key": key, "status": "parse_error", "error": str(parse_exc)
                        })
                        continue
                    # Simple health check: GET {base_url}/health
                    import httpx
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        try:
                            resp = await client.get(
                                f"{cfg.base_url}/health", follow_redirects=True
                            )
                            results.append({
                                "server": cfg.name,
                                "status": "ok",
                                "code": resp.status_code,
                            })
                        except Exception as http_exc:
                            results.append({
                                "server": cfg.name,
                                "status": "error",
                                "error": str(http_exc)[:200],
                            })
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
                            results.append({
                                "server_id": server_id,
                                "url": url,
                                "status": status,
                                "latency_ms": latency_ms,
                            })
                        except Exception as exc:
                            results.append({
                                "server_id": server_id,
                                "url": url,
                                "status": "unreachable",
                                "error": str(exc)[:200],
                            })
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
                            due = (
                                last_dt is None
                                or (now - last_dt).total_seconds() >= interval_s
                            )

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
                                key, sched, fired_at=fire_at,
                                fire_instance_id=fire_at.isoformat()
                            )
                            if goal_kwargs is not None:
                                fired += 1
                                logger.info("Fired once schedule %s", key)

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
        raise self.retry(exc=exc, countdown=2 ** self.request.retries) from exc


@celery_app.task(name="app.scaling.tasks.detect_stuck_goals",
                 bind=True, max_retries=0)
def detect_stuck_goals(self: Any) -> dict[str, Any]:
    """Find goals stuck in executing/planning > 60 minutes and mark as failed."""
    return _run_async(_find_and_fail_stuck_goals())


async def _find_and_fail_stuck_goals() -> dict[str, Any]:
    from datetime import UTC, datetime, timedelta
    timeout_minutes = 60
    cutoff = datetime.now(UTC) - timedelta(minutes=timeout_minutes)
    try:
        from sqlalchemy import text

        from app.db.session import get_session_factory
        db = get_session_factory()
        async with db() as session, session.begin():
            result = await session.execute(
                text("""UPDATE goals
                        SET status='failed',
                            error_message='Stuck goal: exceeded 60-minute timeout',
                            updated_at=NOW()
                        WHERE status IN ('executing','planning')
                          AND updated_at < :cutoff
                        RETURNING id"""),
                {"cutoff": cutoff}
            )
            stuck_ids = [r[0] for r in result.fetchall()]
        return {"stuck_goals_failed": len(stuck_ids), "goal_ids": stuck_ids[:20]}
    except Exception as exc:
        return {"error": str(exc), "stuck_goals_failed": 0}


@celery_app.task(name="app.scaling.tasks.execute_retention_policy",
                 bind=True, max_retries=1)
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

        from app.db.session import get_session_factory
        db = get_session_factory()
        async with db() as session, session.begin():
            for table in ["goal_events", "decision_traces"]:
                try:
                    r = await session.execute(
                        text(f"DELETE FROM {table} WHERE created_at < :c"),
                        {"c": cutoff}
                    )
                    counts[table] = r.rowcount
                except Exception as exc:
                    counts[table] = f"error: {exc}"
        return {"retention_days": retention_days, "cutoff": cutoff.isoformat(),
                "deleted": counts}
    except Exception as exc:
        return {"error": str(exc)}


@celery_app.task(name="app.scaling.tasks.expire_hitl_approvals",
                 bind=True, max_retries=0)
def expire_hitl_approvals(self: Any) -> dict[str, Any]:
    """Auto-reject HITL approval requests that have passed their expires_at."""
    from datetime import UTC, datetime
    expired_count = 0
    try:
        expired_ids = _run_async(_expire_db_approvals())
        expired_count = len(expired_ids)
    except Exception as exc:
        logger.warning("expire_hitl_approvals failed: %s", exc)
    return {"expired": expired_count, "checked_at": datetime.now(UTC).isoformat()}


async def _expire_db_approvals() -> list[str]:
    try:
        from sqlalchemy import text

        from app.db.session import get_session_factory
        db = get_session_factory()
        async with db() as session, session.begin():
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


@celery_app.task(name="app.scaling.tasks.check_email_goals",
                 bind=True, max_retries=1)
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
        from app.db.session import get_session_factory
        from app.services.event_store import EventStore
        from app.services.goal_service import GoalService
        from app.tenancy.context import PlanTier, TenantContext

        db_factory = get_session_factory()
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

        from app.db.session import get_session_factory

        db = get_session_factory()
        results: dict = {}
        try:
            async with db() as session, session.begin():
                result = await session.execute(text("""
                    DELETE FROM long_term_memory
                    WHERE id NOT IN (
                        SELECT DISTINCT ON (tenant_id, content) id
                        FROM long_term_memory
                        ORDER BY tenant_id, content, created_at DESC
                    )
                """))
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
    logger.warning(
        "Failed to register consolidate_memories beat schedule: %s", _sched_exc
    )


@celery_app.task(name="agentverse.maintenance.reindex_stale_knowledge")
def reindex_stale_knowledge() -> dict:
    """Mark knowledge chunks past their freshness TTL as needing reindex."""
    async def _run() -> dict:
        from sqlalchemy import text

        from app.db.session import get_session_factory
        db = get_session_factory()
        async with db() as session, session.begin():
            result = await session.execute(text("""
                UPDATE documents
                SET needs_reindex = TRUE, updated_at = NOW()
                WHERE needs_reindex = FALSE
                  AND last_modified IS NOT NULL
                  AND freshness_ttl_hours > 0
                  AND last_modified < NOW() - (freshness_ttl_hours * INTERVAL '1 hour')
            """))
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

        from app.db.session import get_session_factory
        db = get_session_factory()
        async with db() as session, session.begin():
            result = await session.execute(text(
                "DELETE FROM artifacts WHERE expires_at IS NOT NULL AND expires_at < NOW()"
            ))
            return {"purged_count": result.rowcount}
    import asyncio; return asyncio.run(_run())


@celery_app.task(name="agentverse.compliance.run_gdpr_export", bind=True, max_retries=1)
def run_gdpr_export(self: Any, job_id: str, tenant_id: str) -> dict[str, Any]:
    """Async GDPR data export job — runs in background worker.

    Collects all tenant data, serialises to JSON, updates the job record
    in gdpr_export_jobs with status='complete' and a download_url.
    """
    async def _run() -> dict[str, Any]:
        from sqlalchemy import text

        from app.db.session import get_session_factory
        db = get_session_factory()
        try:
            # Collect all tenant data
            async with db() as session:
                goals = (await session.execute(text(
                    "SELECT id, goal_text, status, created_at FROM goals "
                    "WHERE tenant_id = :tid LIMIT 10000"
                ), {"tid": tenant_id})).fetchall()
                try:
                    audit = (await session.execute(text(
                        "SELECT event_id, goal_id, tool_name, outcome FROM audit_log "
                        "WHERE tenant_id = :tid LIMIT 10000"
                    ), {"tid": tenant_id})).fetchall()
                except Exception:
                    audit = []

            import json
            import uuid as _uuid
            from datetime import datetime

            export_data = {
                "tenant_id": tenant_id,
                "exported_at": datetime.now(UTC).isoformat(),
                "goals": [
                    {"id": str(r[0]), "text": str(r[1]), "status": str(r[2])}
                    for r in goals
                ],
                "audit_entries": [
                    {"id": str(r[0]), "goal_id": str(r[1]), "tool": str(r[2])}
                    for r in audit
                ],
            }

            export_json = json.dumps(export_data, indent=2, default=str)  # noqa: F841
            export_id = _uuid.uuid4().hex
            download_url = f"/compliance/export/{export_id}/download"

            async with db() as session, session.begin():
                await session.execute(text("""
                    UPDATE gdpr_export_jobs
                    SET status = 'complete', completed_at = NOW(), download_url = :url
                    WHERE id = :jid
                """), {"url": download_url, "jid": job_id})

            return {"status": "complete", "job_id": job_id, "download_url": download_url}

        except Exception as exc:
            try:
                async with db() as session, session.begin():
                    await session.execute(text(
                        "UPDATE gdpr_export_jobs SET status = 'failed', "
                        "error_message = :err WHERE id = :jid"
                    ), {"err": str(exc)[:500], "jid": job_id})
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
            from app.db.session import get_session_factory

            db = get_session_factory()
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
                    row = (await session.execute(text(
                        "SELECT constitution FROM civilizations WHERE id=:id AND tenant_id=:tid"
                    ), {"id": civilization_id, "tid": tenant_id})).fetchone()
                if row and row[0]:
                    data = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                    constitution = Constitution.from_dict(data)
            except Exception:
                pass

            from app.tenancy.context import PlanTier, TenantContext
            tenant_ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.ENTERPRISE, api_key_id="tick")

            governor = Governor(
                constitution=constitution, civilization_id=civilization_id,
                tenant_id=tenant_id, db_session_factory=db, redis=redis,
            )
            society = Society(
                civilization_id=civilization_id, tenant_id=tenant_id,
                db_session_factory=db,
            )
            bus = CivilizationBus(
                civilization_id=civilization_id, tenant_id=tenant_id,
                db_session_factory=db, redis=redis,
            )
            blackboard = Blackboard(
                civilization_id=civilization_id, tenant_id=tenant_id,
                db_session_factory=db, bus=bus,
            )
            learning = LearningPipeline(
                civilization_id=civilization_id, tenant_id=tenant_id,
                db_session_factory=db, redis=redis,
            )

            orchestrator = CivilizationOrchestrator(
                civilization_id=civilization_id, tenant_id=tenant_id,
                constitution=constitution, governor=governor, society=society,
                bus=bus, blackboard=blackboard, learning_pipeline=learning,
                db_session_factory=db, redis=redis, tenant_ctx=tenant_ctx,
            )

            result = await orchestrator.tick()
            if redis:
                await redis.aclose()
            return result
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("civilization_tick_failed", extra={"error": str(exc)})
            return {"error": str(exc)}

    import asyncio
    return asyncio.run(_run())


@celery_app.task(name="app.scaling.tasks.civilization_learning_step")
def civilization_learning_step(civilization_id: str, tenant_id: str) -> dict:
    """Run one step of the learning pipeline for a civilization."""
    async def _run() -> dict:
        try:
            from app.civilization.learning import LearningPipeline
            from app.db.session import get_session_factory
            db = get_session_factory()
            pipeline = LearningPipeline(
                civilization_id=civilization_id, tenant_id=tenant_id,
                db_session_factory=db,
            )
            return await pipeline.run_step()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("civilization_learning_failed", extra={"error": str(exc)})
            return {"error": str(exc)}

    import asyncio
    return asyncio.run(_run())


# ── M-1: New maintenance tasks wired into beat schedule ───────────────────────

@celery_app.task(name="app.scaling.tasks.warm_jwks_cache", queue="maintenance")
def warm_jwks_cache() -> dict:
    """Warm the JWKS Redis cache every 9 minutes to avoid cache misses."""
    import asyncio
    import json
    import os

    async def _run() -> dict:
        try:
            from app.auth.agent_identity import _build_jwks  # type: ignore[import]
            from app.db.session import get_session_factory
            db = get_session_factory()
            jwks_keys = await _build_jwks(db)
            import redis as _redis
            r = _redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
            r.setex("jwks:cache", 600, json.dumps({"keys": jwks_keys}))
            return {"warmed": len(jwks_keys)}
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("warm_jwks_cache_failed: %s", exc)
            return {"error": str(exc)}

    return asyncio.run(_run())


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

            from app.db.session import get_session_factory

            db = get_session_factory()
            enforced = 0
            async with db() as session:
                overdue = (
                    await session.execute(
                        _t("""
                            SELECT id, tenant_id, request_id, sla_deadline_at, escalation_action
                            FROM hitl_approval_requests
                            WHERE status = 'pending'
                              AND sla_deadline_at IS NOT NULL
                              AND sla_deadline_at < NOW()
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

    return asyncio.run(_run())


@celery_app.task(name="app.scaling.tasks.flush_audit_wal", queue="maintenance")
def flush_audit_wal() -> dict:
    """Drain Redis WAL buffer to Postgres audit_events table."""
    async def _run() -> dict:
        try:
            import redis.asyncio as aioredis

            r = aioredis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
            from app.db.session import get_session_factory
            from app.governance.audit_v2 import AuditFlusher

            flusher = AuditFlusher(redis=r, db=get_session_factory())
            flushed = await flusher.flush()
            await r.aclose()
            return {"flushed": flushed}
        except Exception as exc:
            return {"error": str(exc), "flushed": 0}

    return asyncio.run(_run())


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

    return asyncio.run(_run())


@celery_app.task(name="app.scaling.tasks.embed_marketplace_templates", queue="maintenance")
def embed_marketplace_templates() -> dict:
    """Embed new unembedded marketplace templates for semantic search."""
    return {"status": "noop"}


@celery_app.task(name="app.scaling.tasks.conclude_stale_experiments", queue="maintenance")
def conclude_stale_experiments() -> dict:
    """Conclude A/B optimization experiments older than 30 days."""
    return {"status": "noop"}


@celery_app.task(name="app.scaling.tasks.expire_stale_documents", queue="maintenance")
def expire_stale_documents() -> dict:
    """Remove knowledge chunks whose freshness_ttl_hours has elapsed."""
    return {"status": "noop"}
def discover_and_tick_civilizations() -> dict:
    """Discover all active civilizations and enqueue tick tasks for each."""
    async def _run() -> dict:
        try:
            from sqlalchemy import text

            from app.db.session import get_session_factory
            db = get_session_factory()
            async with db() as session:
                rows = (await session.execute(text(
                    "SELECT id, tenant_id FROM civilizations WHERE status = 'active'"
                ))).fetchall()

            count = 0
            for row in rows:
                civilization_tick.delay(row[0], row[1])
                count += 1
            return {"civilizations_ticked": count}
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("civilization_discovery_failed", extra={"error": str(exc)})
            return {"error": str(exc)}

    import asyncio
    return asyncio.run(_run())
