"""Celery tasks — real implementations for goal execution and scheduling."""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import time
from typing import Any, cast

from app.observability.logging import get_logger
from app.scaling.celery_app import celery_app

logger = get_logger(__name__)

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
    from app.agent.loop import AgentLoop
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

    loop = AgentLoop(
        planner=provider,
        executor=provider,
        verifier=provider,
        result_processor=ResultProcessor(),
    )

    try:
        async def worker_event_callback(event: dict[str, Any]) -> None:
            await append_submitted_goal_event(event)

        import asyncio as _asyncio

        from app.tenancy.context import PLAN_LIMITS as _PLAN_LIMITS
        goal_timeout_s = getattr(
            _PLAN_LIMITS.get(plan, None), "goal_timeout_seconds", 1800
        ) if hasattr(plan, 'value') else 1800

        try:
            state = _run_async(
                _asyncio.wait_for(
                    loop.run(
                        goal=effective_goal,
                        tenant_ctx=tenant_ctx,
                        event_callback=worker_event_callback,
                    ),
                    timeout=float(goal_timeout_s)
                )
            )
        except TimeoutError:
            _run_async(mark_worker_failed(
                TimeoutError(f"Goal timed out after {goal_timeout_s}s")
            ))
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

    async def _check_servers() -> list[dict[str, Any]]:
        """Ping every registered MCP server across all active tenants."""
        results: list[dict[str, Any]] = []
        try:
            _redis_url = celery_app.conf.broker_url or ""
            if not _redis_url:
                return [{"status": "skipped", "reason": "no Redis configured"}]

            import json as _json
            import time as _time

            import redis as _sync_redis

            client = _sync_redis.from_url(_redis_url, decode_responses=True)
            # Find all registered MCP server keys: mcp:servers:{tenant_id}:{server_id}
            # or mcp:servers:{tenant_id} (registry stores a JSON map)
            keys = client.keys("mcp:servers:*")

            import httpx as _httpx
            for key in keys[:50]:  # Cap at 50 to avoid overload
                parts = key.split(":")
                tenant_id = parts[2] if len(parts) >= 3 else "unknown"

                raw = client.get(key)
                if not raw:
                    continue

                try:
                    data = _json.loads(raw)
                except Exception:
                    continue

                if not isinstance(data, dict):
                    continue

                # Registry stores: {server_id: {url, name, status, ...}}
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
                        error = None
                    except Exception as exc:
                        latency_ms = round((_time.monotonic() - t0) * 1000)
                        status = "unreachable"
                        error = str(exc)[:200]

                    results.append({
                        "server_id": server_id,
                        "tenant_id": tenant_id,
                        "url": url,
                        "status": status,
                        "latency_ms": latency_ms,
                        "error": error,
                        "name": sdata.get("name", server_id) if isinstance(sdata, dict) else server_id,
                    })

            client.close()
        except Exception as exc:
            results.append({"status": "error", "reason": str(exc)})
        return results

    event_loop = asyncio.new_event_loop()
    try:
        results = event_loop.run_until_complete(_check_servers())
    finally:
        event_loop.close()

    return {
        "status": "ok",
        "checked_at": datetime.datetime.utcnow().isoformat(),
        "servers_checked": len(results),
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
                raise

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
