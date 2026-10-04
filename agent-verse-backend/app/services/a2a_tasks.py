"""Durable A2A task lifecycle: outcome reconciliation and callback delivery (A2A-01).

An inbound A2A task used to be executed by an untracked ``asyncio`` task in the
API process after the 202: a restart (or a replica dying) stranded the task as
``accepted`` with no goal and no callback. Now:

* the API submits the goal BEFORE answering 202 and stores ``goal_id`` on the
  ``a2a_tasks`` row (status ``working``);
* :func:`finalize_open_tasks` (beat, any replica) moves every open task whose
  goal reached a terminal state to ``complete`` / ``failed`` / ``canceled``
  with a conditional UPDATE (exactly one writer wins each transition), and
  errors tasks whose goal never started or no longer exists after a grace
  period; :func:`reconcile_task` does the same for one task on read, so a
  polling peer sees the outcome without waiting for the beat;
* :func:`claim_due_callbacks` atomically claims undelivered callbacks
  (``FOR UPDATE SKIP LOCKED``, exponential backoff, bounded attempts) and
  :func:`deliver_callback` sends one, recording ``callback_delivered_at`` only
  on a 2xx. A failed delivery is retried by a later claim — nothing lives in
  process memory. ``callback_next_at`` is NULL whenever no delivery is pending
  (none requested, delivered, or attempts exhausted), so the due-callback
  partial index only ever holds pending deliveries.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

OPEN_STATUSES = ("accepted", "working")
_GOAL_TERMINAL_TO_A2A = {
    "complete": "complete",
    "failed": "failed",
    "cancelled": "canceled",
    "canceled": "canceled",
}
CALLBACK_MAX_ATTEMPTS = 8
_CALLBACK_MAX_BACKOFF_S = 3600
_CALLBACK_BASE_BACKOFF_S = 30
STALE_UNSTARTED_SECONDS = 600

ResultTextFn = Callable[[str, str], Awaitable[str]]
SendFn = Callable[[str, str, str, str], Awaitable[bool]]

# Set on the terminal transition: a callback is due now only if one was asked for.
_CALLBACK_DUE_SQL = "CASE WHEN COALESCE(callback_url, '') <> '' THEN now() END"


def a2a_status_for_goal(goal_status: str) -> str | None:
    """The A2A status for a terminal goal status, ``None`` while it is running."""
    return _GOAL_TERMINAL_TO_A2A.get((goal_status or "").lower())


def event_result_text(factory: Any) -> ResultTextFn:
    """Result text of a completed goal: its result-artifact summary from the event log."""

    async def _text(tenant_id: str, goal_id: str) -> str:
        from app.services.event_store import EventStore
        from app.services.result_artifacts import build_result_artifact
        from app.tenancy.context import PlanTier, TenantContext

        # Only the tenant id is used (RLS scope + explicit predicate).
        ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="a2a-reconcile")
        events = await EventStore(factory).list_events(goal_id, tenant_ctx=ctx)
        artifact = build_result_artifact(goal="", status="complete", events=events)
        return str(artifact.get("summary") or "")

    return _text


async def _outcome(
    tenant_id: str,
    goal_id: str,
    goal_status: str,
    error_message: str | None,
    result_text: ResultTextFn,
) -> tuple[str, str] | None:
    status = a2a_status_for_goal(goal_status)
    if status is None:
        return None
    if status == "complete":
        try:
            return status, await result_text(tenant_id, goal_id)
        except Exception as exc:  # the outcome is still known; the text is best effort
            logger.warning("a2a_result_text_failed", goal_id=goal_id, error=str(exc)[:200])
            return status, ""
    if status == "canceled":
        return status, error_message or f"Goal {goal_id} was cancelled"
    return status, error_message or f"Goal {goal_id} failed"


async def finalize_task(
    session: Any, *, task_id: str, tenant_id: str, status: str, result: str
) -> bool:
    """Move one OPEN task to a terminal *status* (conditional: one writer wins)."""
    from sqlalchemy import text

    row = (
        await session.execute(
            text(
                "UPDATE a2a_tasks SET status = :status, result = :result, "
                f"updated_at = now(), callback_next_at = {_CALLBACK_DUE_SQL} "
                "WHERE id = :id AND tenant_id = :tid AND status IN ('accepted', 'working') "
                "RETURNING id"
            ),
            {"id": task_id, "tid": tenant_id, "status": status, "result": (result or "")[:10000]},
        )
    ).fetchone()
    return row is not None


async def finalize_open_tasks(
    system_factory: Any, *, result_text: ResultTextFn, limit: int = 500
) -> dict[str, list[str]]:
    """Reconcile open tasks with their goals (system scope, every tenant; bounded)."""
    from sqlalchemy import text

    from app.db.rls import system_session

    finalized: list[str] = []
    params = {"stale": STALE_UNSTARTED_SECONDS, "lim": limit}
    async with system_factory() as session, session.begin(), system_session(session):
        # Accepted but never bound to a goal (the API died between accepting and
        # submitting), or bound to a goal that no longer exists: the outcome is
        # unknowable, so the task errors — after a grace period, never instantly.
        errored = (
            await session.execute(
                text(
                    "UPDATE a2a_tasks SET status = 'error', updated_at = now(), "
                    f"callback_next_at = {_CALLBACK_DUE_SQL}, "
                    "result = CASE WHEN goal_id IS NULL "
                    "THEN 'Task was accepted but its goal was never started' "
                    "ELSE 'The task''s goal no longer exists' END "
                    "WHERE id IN (SELECT t.id FROM a2a_tasks t "
                    "WHERE t.status IN ('accepted', 'working') "
                    "AND t.created_at < now() - make_interval(secs => :stale) "
                    "AND (t.goal_id IS NULL OR NOT EXISTS (SELECT 1 FROM goals g "
                    "WHERE g.id = t.goal_id AND g.tenant_id = t.tenant_id)) "
                    "ORDER BY t.created_at LIMIT :lim FOR UPDATE SKIP LOCKED) "
                    "AND status IN ('accepted', 'working') "
                    "RETURNING id"
                ),
                params,
            )
        ).fetchall()
        done = (
            await session.execute(
                text(
                    "SELECT t.id, t.tenant_id, t.goal_id, g.status, g.error_message "
                    "FROM a2a_tasks t JOIN goals g "
                    "ON g.id = t.goal_id AND g.tenant_id = t.tenant_id "
                    "WHERE t.status IN ('accepted', 'working') "
                    "AND g.status IN ('complete', 'failed', 'cancelled') "
                    "ORDER BY t.created_at LIMIT :lim"
                ),
                params,
            )
        ).fetchall()
    for task_id, tenant_id, goal_id, goal_status, error_message in done:
        outcome = await _outcome(tenant_id, goal_id, goal_status, error_message, result_text)
        if outcome is None:
            continue
        async with system_factory() as session, session.begin(), system_session(session):
            if await finalize_task(
                session, task_id=task_id, tenant_id=tenant_id, status=outcome[0], result=outcome[1]
            ):
                finalized.append(task_id)
    errored_ids = [r[0] for r in errored]
    if finalized or errored_ids:
        logger.info("a2a_tasks_reconciled", finalized=len(finalized), errored=len(errored_ids))
    return {"finalized": finalized, "errored": errored_ids}


async def reconcile_task(
    app_factory: Any, *, task_id: str, tenant_id: str, result_text: ResultTextFn
) -> bool:
    """Finalise one open task from its goal under the tenant's RLS scope (on read)."""
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with (
        app_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        row = (
            await session.execute(
                text(
                    "SELECT t.goal_id, g.status, g.error_message FROM a2a_tasks t "
                    "JOIN goals g ON g.id = t.goal_id AND g.tenant_id = t.tenant_id "
                    "WHERE t.id = :id AND t.tenant_id = :tid "
                    "AND t.status IN ('accepted', 'working')"
                ),
                {"id": task_id, "tid": tenant_id},
            )
        ).fetchone()
    if row is None:
        return False
    outcome = await _outcome(tenant_id, row[0], row[1], row[2], result_text)
    if outcome is None:
        return False
    async with (
        app_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        return await finalize_task(
            session, task_id=task_id, tenant_id=tenant_id, status=outcome[0], result=outcome[1]
        )


async def claim_due_callbacks(system_factory: Any, *, limit: int = 200) -> list[tuple[str, str]]:
    """Claim terminal tasks whose callback is due; push their next attempt out.

    The claim bumps ``callback_attempts`` and sets the next retry time before
    the send, so a delivery that dies mid-way is retried later, and concurrent
    claimers on other replicas skip the locked rows. The final attempt clears
    ``callback_next_at`` (given up: logged, never retried again).
    """
    from sqlalchemy import text

    from app.db.rls import system_session

    async with system_factory() as session, session.begin(), system_session(session):
        rows = (
            await session.execute(
                text(
                    "UPDATE a2a_tasks SET callback_attempts = callback_attempts + 1, "
                    "callback_next_at = CASE WHEN callback_attempts + 1 >= :max THEN NULL "
                    "ELSE now() + make_interval(secs => LEAST(:cap, "
                    ":base * power(2, callback_attempts))) END "
                    "WHERE id IN (SELECT id FROM a2a_tasks "
                    "WHERE callback_next_at IS NOT NULL AND callback_next_at <= now() "
                    "AND status NOT IN ('accepted', 'working') "
                    "AND COALESCE(callback_url, '') <> '' "
                    "AND callback_delivered_at IS NULL "
                    "AND callback_attempts < :max "
                    "ORDER BY callback_next_at LIMIT :lim FOR UPDATE SKIP LOCKED) "
                    "RETURNING id, tenant_id, callback_attempts"
                ),
                {
                    "cap": float(_CALLBACK_MAX_BACKOFF_S),
                    "base": float(_CALLBACK_BASE_BACKOFF_S),
                    "max": CALLBACK_MAX_ATTEMPTS,
                    "lim": limit,
                },
            )
        ).fetchall()
    for task_id, _tenant, attempts in rows:
        if attempts >= CALLBACK_MAX_ATTEMPTS:
            logger.warning("a2a_callback_final_attempt", task_id=task_id, attempts=attempts)
    return [(r[0], r[1]) for r in rows]


async def deliver_callback(app_factory: Any, task_id: str, tenant_id: str, send: SendFn) -> bool:
    """Send one task's callback; record delivery only on success."""
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with (
        app_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        row = (
            await session.execute(
                text(
                    "SELECT status, result, callback_url, callback_delivered_at "
                    "FROM a2a_tasks WHERE id = :id AND tenant_id = :tid"
                ),
                {"id": task_id, "tid": tenant_id},
            )
        ).fetchone()
    if row is None or not row[2]:
        return False
    if row[3] is not None:
        return True  # already delivered (a duplicate claim)
    if row[0] in OPEN_STATUSES:
        return False
    if not await send(str(row[2]), task_id, str(row[0]), str(row[1] or "")):
        return False
    async with (
        app_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        await session.execute(
            text(
                "UPDATE a2a_tasks SET callback_delivered_at = now(), callback_next_at = NULL, "
                "updated_at = now() "
                "WHERE id = :id AND tenant_id = :tid AND callback_delivered_at IS NULL"
            ),
            {"id": task_id, "tid": tenant_id},
        )
    return True
