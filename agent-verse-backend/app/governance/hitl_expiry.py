"""Fail goals parked for a human whose approval expired (NF-11).

A supervised-mode goal whose graph ENDED waiting for approvals is parked in
``waiting_human`` (``goal_service._suspend_for_approval`` / ``run_goal``): no
task holds it, so there is no live waiter for the P5-4 wake
(``hitl.release_expired_waiters``) to reach. When its approval expired, the beat
marked the request ``timed_out`` and the goal stayed ``waiting_human`` forever,
until someone called ``POST /goals/{id}/resume``.

The ``expire_hitl_approvals`` beat now fails those goals in the SAME transaction
that expires the approvals (so a crash between the two can never leave an
expired approval with a goal still parked on it), with the reason
``approval expired``. The UPDATE is conditional on ``status = 'waiting_human'``
and joins on the approval's tenant, so a goal resumed meanwhile on another
replica, or one belonging to another tenant, is never touched; exactly one beat
run wins each goal. Events (``goal_failed``) are appended and published after
commit so every replica's SSE stream and in-memory record see the failure.

A goal ``waiting_human`` in the DATABASE is always parked: while an executor
blocks on a live approval wait the row stays ``executing`` (only the in-memory
record shows ``waiting_human``), and that waiter is released by P5-4.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)

APPROVAL_EXPIRED_REASON = "approval expired"
APPROVAL_EXPIRED_CODE = "approval_expired"
# == app.services.goal_service._SUSPENDED_KEY (cleared: the goal is no longer parked)
_SUSPENDED_KEY = "_suspended_for_approval"

PublishFn = Callable[[str, str, dict[str, Any]], None]


_SUFFIX = (
    " was not decided before it expired while the goal waited for a human "
    "(supervised mode). Resubmit the goal to retry."
)


def _message(request_id: str) -> str:
    return f"{APPROVAL_EXPIRED_REASON}: approval request {request_id}{_SUFFIX}"


async def fail_goals_parked_on_expired(
    session: Any, expired_request_ids: list[str]
) -> list[dict[str, Any]]:
    """Fail every goal parked in ``waiting_human`` on one of these expired requests.

    Runs inside the caller's (system, cross-tenant) transaction. Returns one
    ``{goal_id, tenant_id, request_id, reason, execution_context}`` per goal failed.
    """
    if not expired_request_ids:
        return []
    from sqlalchemy import text

    rows = (
        await session.execute(
            text(
                """
                UPDATE goals AS g
                SET status = 'failed',
                    error_message = :prefix || ': approval request ' || ar.id || :suffix,
                    updated_at = now(),
                    completed_at = now(),
                    execution_context = (
                        COALESCE(g.execution_context::jsonb, '{}'::jsonb) - CAST(:k AS text)
                    )::json
                FROM approval_requests AS ar
                WHERE ar.id = ANY(CAST(:ids AS text[]))
                  AND ar.status = 'timed_out'
                  AND g.id = ar.goal_id
                  AND g.tenant_id = ar.tenant_id
                  AND g.status = 'waiting_human'
                RETURNING g.id, g.tenant_id, ar.id, g.execution_context, g.agent_id, g.dry_run
                """
            ),
            {"ids": [str(i) for i in expired_request_ids], "k": _SUSPENDED_KEY,
             "prefix": APPROVAL_EXPIRED_REASON, "suffix": _SUFFIX},
        )
    ).fetchall()
    failed: list[dict[str, Any]] = []
    for row in rows:
        ctx = row[3]
        if not isinstance(ctx, dict):
            import json

            try:
                ctx = json.loads(ctx or "{}")
            except (TypeError, ValueError):
                ctx = {}
        failed.append(
            {
                "goal_id": str(row[0]),
                "tenant_id": str(row[1]),
                "request_id": str(row[2]),
                "reason": _message(str(row[2])),
                "execution_context": ctx,
                "agent_id": str(row[4] or ""),
                "dry_run": bool(row[5]),
            }
        )
    if failed:
        _log.warning(
            "parked_goals_failed_on_expired_approval",
            goals=[f["goal_id"] for f in failed],
        )
    return failed


async def announce_parked_goal_failures(
    db_factory: Any,
    failed: list[dict[str, Any]],
    *,
    publish: PublishFn | None = None,
    finalize: Callable[[str, str], Awaitable[None]] | None = None,
    on_failed: Callable[[dict[str, Any]], Any] | None = None,
) -> int:
    """Append + publish ``goal_failed`` for each goal failed above (after commit).

    ``on_failed`` is called with each goal (B7-2: the worker publishes
    ``goal.failed`` for goal_failed triggers from it).

    ``db_factory`` is the APPLICATION session factory: each append runs in its
    goal's tenant RLS scope. Best effort per goal — the status is already durable.
    Returns how many goal_failed events were stored.
    """
    from app.services.event_store import EventStore
    from app.tenancy.context import PlanTier, TenantContext

    stored = 0
    store = EventStore(db_factory)
    for goal in failed:
        event = {
            "type": "goal_failed",
            "reason": goal["reason"],
            "failure_reason": APPROVAL_EXPIRED_CODE,
            "request_id": goal["request_id"],
        }
        ctx = TenantContext(
            tenant_id=goal["tenant_id"], plan=PlanTier.FREE, api_key_id="hitl-expiry"
        )
        seq: int | None = None
        try:
            seq = await store.append_event(goal["goal_id"], event, tenant_ctx=ctx)
            stored += 1
        except Exception as exc:
            _log.warning(
                "parked_goal_failed_event_append_failed",
                goal_id=goal["goal_id"],
                error=str(exc)[:200],
            )
        if publish is not None:
            with contextlib.suppress(Exception):
                publish(
                    goal["tenant_id"],
                    goal["goal_id"],
                    {**event, **({"_seq": seq} if seq is not None else {})},
                )
        if on_failed is not None:
            try:
                on_failed(goal)
            except Exception as exc:
                _log.warning(
                    "parked_goal_failed_callback_error",
                    goal_id=goal["goal_id"],
                    error=str(exc)[:200],
                )
        if finalize is not None:
            with contextlib.suppress(Exception):
                await finalize(goal["goal_id"], goal["tenant_id"])
    return stored
