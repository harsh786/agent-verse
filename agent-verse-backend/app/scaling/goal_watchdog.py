"""Durable goal-runner watchdog (GOAL-STALL / RW-21).

Root cause seen on the live stack: a worker process running a goal died
(``objc ... fork() ... Crashing instead`` -> SIGABRT, WorkerLostError). Celery
redelivered the task, but the redelivery found the dead runner's per-goal Redis
lock (TTL = plan goal timeout + 5 min) and skipped as "already executing", and
the only reaper (``detect_stuck_goals``) waits out the plan's goal timeout
(1-24 h). The goal sat ``executing`` with no events until someone cancelled it.

Two pieces, both Postgres-backed so they work across replicas:

* :class:`GoalHeartbeat` — while ``run_goal`` executes a goal, a daemon thread
  writes ``goals.heartbeat_at`` (and the run's lock token, ``runner_token``)
  every ``goal_heartbeat_interval_seconds``. It stops beating when the goal's
  event loop has made no progress for ``goal_loop_stall_seconds`` (a wedged
  worker), so a hung process is treated like a dead one.
* :func:`reap_stale_goal_runners` — a beat job claims (one conditional UPDATE,
  so exactly one replica wins each goal) active goals whose heartbeat is older
  than ``goal_heartbeat_stale_seconds``, releases the dead runner's Redis lock
  (compare-and-delete on its token, never another run's lock) and then either
  requeues the goal — only when it never ran a tool or passed an approval (a
  re-run cannot repeat a side effect) and has requeues left — or fails it with an
  honest ``runner_lost`` reason and frees its concurrency slot.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)

GOAL_LOCK_PREFIX = "goal_lock:"  # == app.scaling.tasks._SyncGoalLock.KEY_PREFIX
_ACTIVE_STATUSES = ("executing", "planning")
# Events after which a goal is never re-run automatically: something with an
# external effect may already have happened.
_SIDE_EFFECT_EVENTS = (
    "tool_call_complete",
    "tool_call_failed",
    "tool_call_auto_approved",
    "approval_granted",
)
_RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""
_REAP_BATCH = 100


def _settings_value(name: str, default: float) -> float:
    try:
        from app.core.config import get_settings

        return float(getattr(get_settings(), name))
    except Exception:
        return default


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


class GoalHeartbeat:
    """Write ``goals.heartbeat_at`` for one running goal from a daemon thread."""

    def __init__(
        self,
        *,
        goal_id: str,
        tenant_id: str,
        runner_token: str,
        database_url: str | None = None,
        interval_s: float | None = None,
        loop_stall_s: float | None = None,
    ) -> None:
        self.goal_id = goal_id
        self.tenant_id = tenant_id
        self.runner_token = runner_token
        if database_url is None:
            from app.core.config import get_settings

            database_url = str(get_settings().database_url)
        self._dsn = _asyncpg_dsn(database_url)
        self.interval_s = float(
            interval_s
            if interval_s is not None
            else _settings_value("goal_heartbeat_interval_seconds", 15.0)
        )
        self.loop_stall_s = float(
            loop_stall_s
            if loop_stall_s is not None
            else _settings_value("goal_loop_stall_seconds", 600.0)
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_progress = time.monotonic()
        self._stall_logged = False
        self.beats = 0
        self.failures = 0

    # ── event-loop progress ────────────────────────────────────────────────
    def touch(self) -> None:
        """Record that the goal's event loop is making progress."""
        self._last_progress = time.monotonic()
        self._stall_logged = False

    async def run_with_progress(self, coro: Awaitable[Any]) -> Any:
        """Await ``coro`` while a ticker in the same loop calls :meth:`touch`."""

        async def _ticker() -> None:
            while True:
                self.touch()
                await asyncio.sleep(max(0.05, min(self.interval_s, 5.0)))

        ticker = asyncio.ensure_future(_ticker())
        try:
            return await coro
        finally:
            ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await ticker

    # ── thread ──────────────────────────────────────────────────────────────
    def start(self) -> GoalHeartbeat:
        self._thread = threading.Thread(
            target=self._run, name=f"goal-heartbeat-{self.goal_id}", daemon=True
        )
        self._thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        conn: Any = None
        try:
            while True:
                if time.monotonic() - self._last_progress > self.loop_stall_s:
                    if not self._stall_logged:
                        self._stall_logged = True
                        _log.error(
                            "goal_heartbeat_withheld_loop_stalled",
                            goal_id=self.goal_id,
                            stalled_s=round(time.monotonic() - self._last_progress, 1),
                        )
                else:
                    try:
                        conn, still_ours = loop.run_until_complete(self._beat(conn))
                        self.beats += 1
                        if not still_ours:
                            # Finished, cancelled, or taken over by another run
                            # (the reaper requeued it): nothing left to keep alive.
                            break
                    except Exception as exc:
                        self.failures += 1
                        _log.warning(
                            "goal_heartbeat_write_failed",
                            goal_id=self.goal_id,
                            error=f"{type(exc).__name__}: {str(exc)[:160]}",
                        )
                        conn = loop.run_until_complete(_close(conn))
                if self._stop.wait(self.interval_s):
                    break
        finally:
            with contextlib.suppress(Exception):
                loop.run_until_complete(_close(conn))
            loop.close()

    async def _beat(self, conn: Any) -> tuple[Any, bool]:
        import asyncpg

        if conn is None or conn.is_closed():
            conn = await asyncpg.connect(self._dsn, timeout=10)
        # The first beat takes the row over (this run holds the goal's execution
        # lock, so any older token belongs to a dead run); later beats only renew
        # this run's own claim, so a run the reaper replaced stops beating.
        ownership = "" if self.beats == 0 else " AND runner_token = $3"
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", self.tenant_id)
            status = await conn.execute(
                "UPDATE goals SET heartbeat_at = now(), runner_token = $3 "
                "WHERE id = $1 AND tenant_id = $2 "
                "AND status IN ('executing', 'planning', 'waiting_human')" + ownership,
                self.goal_id,
                self.tenant_id,
                self.runner_token,
                timeout=10,
            )
        return conn, str(status).strip() != "UPDATE 0"


async def _close(conn: Any) -> None:
    if conn is not None:
        with contextlib.suppress(Exception):
            await conn.close(timeout=2)
    return None


def release_dead_runner_lock(redis_client: Any, goal_id: str, runner_token: str | None) -> bool:
    """Delete the goal's Redis lock only if it still holds the dead run's token."""
    if redis_client is None or not runner_token:
        return False
    try:
        return bool(
            redis_client.eval(_RELEASE_LOCK_SCRIPT, 1, f"{GOAL_LOCK_PREFIX}{goal_id}", runner_token)
        )
    except Exception as exc:
        _log.warning("goal_lock_release_failed", goal_id=goal_id, error=str(exc)[:160])
        return False


EnqueueFn = Callable[[dict[str, Any]], None]
# (tenant_id, goal_id, execution_context): the slot is a lease keyed by goal id.
ReleaseSlotFn = Callable[[str, str, dict[str, Any]], Awaitable[None]]
PublishFn = Callable[[str, str, dict[str, Any]], None]


async def reap_stale_goal_runners(
    db_factory: Any,
    *,
    redis_client: Any,
    enqueue: EnqueueFn,
    release_slot: ReleaseSlotFn,
    publish: PublishFn | None = None,
    stale_s: float | None = None,
    max_requeues: int | None = None,
) -> dict[str, Any]:
    """Requeue or fail every active goal whose runner heartbeat went stale.

    ``db_factory`` must be a SYSTEM (BYPASSRLS) session factory: the scan is
    cross-tenant. Each goal is claimed by one conditional UPDATE, so concurrent
    reapers on several replicas never handle the same goal twice.
    """
    from sqlalchemy import text

    from app.db.rls import system_session

    stale = float(
        stale_s if stale_s is not None else _settings_value("goal_heartbeat_stale_seconds", 120.0)
    )
    limit = int(
        max_requeues
        if max_requeues is not None
        else _settings_value("goal_watchdog_max_requeues", 1)
    )
    async with db_factory() as session, session.begin(), system_session(session):
        rows = (
            await session.execute(
                text("""
                    WITH stale AS (
                        SELECT id, runner_token FROM goals
                        WHERE status IN ('executing', 'planning')
                          AND heartbeat_at IS NOT NULL
                          AND heartbeat_at < now() - make_interval(secs => :stale)
                        ORDER BY heartbeat_at
                        LIMIT :batch
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE goals AS g
                    SET heartbeat_at = NULL, runner_token = NULL, updated_at = now()
                    FROM stale, tenants AS t
                    WHERE g.id = stale.id AND t.id = g.tenant_id
                    RETURNING g.id, g.tenant_id, stale.runner_token, g.goal_text, g.agent_id,
                              g.priority, g.dry_run, g.workflow_mode, g.execution_context,
                              t.plan_tier
                """),
                {"stale": stale, "batch": _REAP_BATCH},
            )
        ).fetchall()
        claimed: list[dict[str, Any]] = []
        for r in rows:
            ctx = r[8] if isinstance(r[8], dict) else json.loads(r[8] or "{}")
            side_effects = bool(
                (
                    await session.execute(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM goal_events "
                            "WHERE goal_id = :gid AND tenant_id = :tid "
                            "AND event_type = ANY(CAST(:types AS text[])))"
                        ),
                        {"gid": r[0], "tid": r[1], "types": list(_SIDE_EFFECT_EVENTS)},
                    )
                ).scalar()
            )
            claimed.append(
                {
                    "goal_id": str(r[0]),
                    "tenant_id": str(r[1]),
                    "runner_token": r[2],
                    "goal_text": str(r[3] or ""),
                    "agent_id": str(r[4] or ""),
                    "priority": str(r[5] or "normal"),
                    "dry_run": bool(r[6]),
                    "workflow_mode": str(r[7] or "single_agent"),
                    "execution_context": ctx,
                    "plan": str(r[9] or "free"),
                    "side_effects": side_effects,
                }
            )

    requeued: list[str] = []
    failed: list[str] = []
    for goal in claimed:
        release_dead_runner_lock(redis_client, goal["goal_id"], goal["runner_token"])
        done = int(goal["execution_context"].get("watchdog_requeues", 0) or 0)
        if not goal["side_effects"] and done < limit:
            if await _requeue(db_factory, goal, done + 1, enqueue, publish, stale):
                requeued.append(goal["goal_id"])
                continue
            reason = "its requeue could not be enqueued"
        elif goal["side_effects"]:
            reason = "it had already run a tool or passed an approval"
        else:
            reason = f"it was already requeued {done} time(s)"
        if await _fail(db_factory, goal, reason, publish, stale):
            failed.append(goal["goal_id"])
            with contextlib.suppress(Exception):
                await release_slot(goal["tenant_id"], goal["goal_id"], goal["execution_context"])
    if requeued or failed:
        _log.warning("stale_goal_runners_reaped", requeued=requeued, failed=failed)
    return {"requeued": requeued, "failed": failed, "stale_after_s": stale}


async def _append_event(
    db_factory: Any, goal: dict[str, Any], event: dict[str, Any], publish: PublishFn | None
) -> None:
    from app.services.event_store import EventStore
    from app.tenancy.context import PlanTier, TenantContext

    ctx = TenantContext(
        tenant_id=goal["tenant_id"], plan=PlanTier.FREE, api_key_id="goal-watchdog"
    )
    seq = await EventStore(db_factory).append_event(goal["goal_id"], event, tenant_ctx=ctx)
    if publish is not None:
        with contextlib.suppress(Exception):
            # SVC-05: the live copy carries its durable sequence (the SSE id).
            publish(goal["tenant_id"], goal["goal_id"], {**event, "_seq": seq})


async def _requeue(
    db_factory: Any,
    goal: dict[str, Any],
    attempt: int,
    enqueue: EnqueueFn,
    publish: PublishFn | None,
    stale: float,
) -> bool:
    from sqlalchemy import text

    from app.db.rls import system_session

    async with db_factory() as session, session.begin(), system_session(session):
        moved = (
            await session.execute(
                text(
                    "UPDATE goals SET status = 'planning', updated_at = now(), "
                    "execution_context = (COALESCE(execution_context::jsonb, '{}'::jsonb) "
                    "|| jsonb_build_object('watchdog_requeues', CAST(:n AS int)))::json "
                    "WHERE id = :gid AND tenant_id = :tid "
                    "AND status IN ('executing', 'planning') RETURNING id"
                ),
                {"gid": goal["goal_id"], "tid": goal["tenant_id"], "n": attempt},
            )
        ).scalar()
    if moved is None:
        return True  # finished / cancelled meanwhile: nothing to do
    try:
        enqueue(goal)
    except Exception as exc:
        _log.error("stale_goal_requeue_failed", goal_id=goal["goal_id"], error=str(exc)[:200])
        return False
    with contextlib.suppress(Exception):
        await _append_event(
            db_factory,
            goal,
            {
                "type": "goal_runner_lost",
                "action": "requeued",
                "attempt": attempt,
                "reason": f"no runner heartbeat for over {stale:g}s; re-run from the start "
                "(no tool had run yet)",
            },
            publish,
        )
    return True


async def _fail(
    db_factory: Any, goal: dict[str, Any], why: str, publish: PublishFn | None, stale: float
) -> bool:
    from sqlalchemy import text

    from app.db.rls import system_session

    message = (
        f"Goal runner lost: no heartbeat for over {stale:g}s (the worker died or hung). "
        f"Not re-run automatically because {why}; resubmit the goal to retry."
    )
    async with db_factory() as session, session.begin(), system_session(session):
        updated = (
            await session.execute(
                text(
                    "UPDATE goals SET status = 'failed', error_message = :msg, "
                    "updated_at = now(), completed_at = now() "
                    "WHERE id = :gid AND tenant_id = :tid "
                    "AND status IN ('executing', 'planning') RETURNING id"
                ),
                {"gid": goal["goal_id"], "tid": goal["tenant_id"], "msg": message},
            )
        ).scalar()
    if updated is None:
        return False
    for event in (
        {"type": "goal_runner_lost", "action": "failed", "reason": message},
        {"type": "goal_failed", "reason": message, "failure_reason": "runner_lost"},
    ):
        with contextlib.suppress(Exception):
            await _append_event(db_factory, goal, event, publish)
    return True
