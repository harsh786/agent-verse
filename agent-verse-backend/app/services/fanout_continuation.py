"""Fan-out continuations: park a parent, wake it exactly once, sweep overdue children.

a01-F006-05 / a01-F007-01. A supervisor / goal-tree parent used to hold its
Celery slot while it streamed each sub-goal's events (``asyncio.timeout(300)``
per sub-task). Now:

* :func:`park_parent` — the parent's run ended with its children dispatched as
  real goals (``goals.parent_goal_id``, durable ``goal_fanout_ledger`` rows): its
  row moves to ``waiting_children`` and the worker is released. The parent keeps
  its tenant concurrency slot (its sub-goals run under it).
* :func:`wake_parent` — ONE conditional ``UPDATE ... WHERE status =
  'waiting_children' AND NOT EXISTS (a non-terminal child)`` moves the parent to
  ``planning`` and re-enqueues ``run_goal``. Two children finishing at once both
  try it; the row lock + re-check let exactly one through (the other matches no
  row). Called by every child run as it ends, by the API's cancel path, by the
  parker itself right after parking (a child that finished before the park was
  visible), and by the sweeper as a safety net (HITL expiry, the stuck-goal /
  stale-runner reapers, a lost enqueue). An enqueue that fails puts the parent
  back to ``waiting_children`` for the next sweep.
* :func:`sweep_fanout` — beat (with ``reap_stale_goal_runners``): a dispatched
  child past its ``deadline_at`` is cancelled (its ledger row fails with
  ``Timeout after Ns``, its goal is cancelled + signalled) and its parent woken.
  A child waiting for a human is not timed out: its deadline moves out by its
  timeout (the approval's own expiry governs it).

Every tenant-scoped statement carries an explicit ``tenant_id`` predicate; the
sweeper's cross-tenant scan runs on the maintenance (BYPASSRLS) session factory.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.agent.fanout_ledger import FANOUT_WAIT_KEY
from app.observability.logging import get_logger

_log = get_logger(__name__)

WAITING_CHILDREN = "waiting_children"
_TERMINAL_SQL = "('complete', 'failed', 'cancelled')"
# A parent parks only out of an active status (never over a terminal one, nor
# over a cancel / HITL pause that landed meanwhile).
_PARKABLE_SQL = "('executing', 'planning', 'verifying')"
_SWEEP_BATCH = 100
_WAKES_KEY = "_fanout_wakes"

EnqueueFn = Callable[[dict[str, Any]], None]
# (tenant_id, goal_id, execution_context)
ReleaseSlotFn = Callable[[str, str, dict[str, Any]], Awaitable[None]]
PublishFn = Callable[[str, str, dict[str, Any]], None]


def _ctx(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value) if value else {}
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _tenant_session(factory: Any, tenant_id: str) -> Any:
    from contextlib import AsyncExitStack, asynccontextmanager

    from app.db.rls import sqlalchemy_rls_context

    @asynccontextmanager
    async def _cm() -> Any:
        async with AsyncExitStack() as stack:
            session = await stack.enter_async_context(factory())
            await stack.enter_async_context(session.begin())
            await stack.enter_async_context(sqlalchemy_rls_context(session, tenant_id))
            yield session

    return _cm()


async def park_parent(
    factory: Any,
    *,
    tenant_id: str,
    goal_id: str,
    kind: str,
    plan: str,
    connector_ids: list[str] | None = None,
    trigger_chain_depth: int = 0,
    source_trigger_id: str = "",
) -> str:
    """Move an active parent to ``waiting_children``; ``"parked"`` or its real status.

    The wait record keeps what the re-queue needs to run the goal exactly as it
    was submitted (plan tier, connectors, trigger chain).
    """
    from sqlalchemy import text

    wait = {
        "kind": kind,
        "plan": plan,
        "connector_ids": [str(c) for c in (connector_ids or [])],
        "trigger_chain_depth": int(trigger_chain_depth or 0),
        "source_trigger_id": source_trigger_id or "",
        "parked_at": datetime.now(UTC).isoformat(),
    }
    async with _tenant_session(factory, tenant_id) as session:
        parked = (
            await session.execute(
                text(
                    "UPDATE goals SET status = 'waiting_children', heartbeat_at = NULL, "
                    "runner_token = NULL, updated_at = now(), "
                    "execution_context = (COALESCE(execution_context::jsonb, '{}'::jsonb) "
                    "|| jsonb_build_object(CAST(:k AS text), CAST(:wait AS jsonb)))::json "
                    "WHERE id = :g AND tenant_id = :t "
                    f"AND status IN {_PARKABLE_SQL} RETURNING id"
                ),
                {"k": FANOUT_WAIT_KEY, "wait": json.dumps(wait), "g": goal_id, "t": tenant_id},
            )
        ).scalar()
        if parked is not None:
            return "parked"
        status = (
            await session.execute(
                text("SELECT status FROM goals WHERE id = :g AND tenant_id = :t"),
                {"g": goal_id, "t": tenant_id},
            )
        ).scalar()
    return str(status) if status is not None else "missing"


def default_enqueue(goal: dict[str, Any]) -> None:
    """Re-enqueue ``run_goal`` for a woken parent on its plan's queue."""
    from app.services.goal_queue import CeleryGoalTaskQueue
    from app.services.goal_service import _subgoal_queue_kwargs

    CeleryGoalTaskQueue().enqueue_goal(
        goal_id=goal["goal_id"],
        tenant_id=goal["tenant_id"],
        goal_text=goal["goal_text"],
        priority=goal["priority"],
        dry_run=goal["dry_run"],
        agent_id=goal["agent_id"] or None,
        connector_ids=list(goal.get("connector_ids") or []),
        workflow_mode=goal["workflow_mode"],
        goal_template="",
        plan=goal["plan"],
        trigger_chain_depth=int(goal.get("trigger_chain_depth") or 0),
        source_trigger_id=str(goal.get("source_trigger_id") or ""),
        **_subgoal_queue_kwargs(goal["execution_context"]),
    )


async def wake_parent(
    factory: Any,
    *,
    tenant_id: str,
    parent_goal_id: str,
    enqueue: EnqueueFn | None = None,
) -> bool:
    """Re-queue a parked parent once none of its children is still running.

    True only for the ONE caller whose conditional UPDATE moved the row (and
    whose enqueue succeeded). Safe to call from anywhere, any number of times.
    """
    from sqlalchemy import text

    async with _tenant_session(factory, tenant_id) as session:
        row = (
            await session.execute(
                text(
                    "UPDATE goals SET status = 'planning', heartbeat_at = NULL, "
                    "runner_token = NULL, updated_at = now(), "
                    "execution_context = (COALESCE(execution_context::jsonb, '{}'::jsonb) "
                    "|| jsonb_build_object(CAST(:wk AS text), "
                    "COALESCE((execution_context::jsonb ->> CAST(:wk AS text))::int, 0) + 1)"
                    ")::json "
                    "WHERE id = :p AND tenant_id = :t AND status = 'waiting_children' "
                    "AND NOT EXISTS (SELECT 1 FROM goals c WHERE c.tenant_id = :t "
                    f"AND c.parent_goal_id = :p AND c.status NOT IN {_TERMINAL_SQL}) "
                    "RETURNING id, goal_text, priority, dry_run, agent_id, workflow_mode, "
                    "execution_context, "
                    "(SELECT plan_tier FROM tenants WHERE tenants.id = goals.tenant_id)"
                ),
                {"wk": _WAKES_KEY, "p": parent_goal_id, "t": tenant_id},
            )
        ).first()
    if row is None:
        return False
    ctx = _ctx(row[6])
    wait = ctx.get(FANOUT_WAIT_KEY) if isinstance(ctx.get(FANOUT_WAIT_KEY), dict) else {}
    goal = {
        "goal_id": str(row[0]),
        "tenant_id": tenant_id,
        "goal_text": str(row[1] or ""),
        "priority": str(row[2] or "normal"),
        "dry_run": bool(row[3]),
        "agent_id": str(row[4] or ""),
        "workflow_mode": str(row[5] or "single_agent"),
        "execution_context": ctx,
        "plan": str(wait.get("plan") or row[7] or "free"),
        "connector_ids": list(wait.get("connector_ids") or []),
        "trigger_chain_depth": int(wait.get("trigger_chain_depth") or 0),
        "source_trigger_id": str(wait.get("source_trigger_id") or ""),
    }
    try:
        (enqueue or default_enqueue)(goal)
    except Exception as exc:
        _log.error(
            "fanout_parent_requeue_failed", goal_id=parent_goal_id, error=str(exc)[:200]
        )
        # Nothing will run it: park it again for the sweeper's next wake.
        with contextlib.suppress(Exception):
            async with _tenant_session(factory, tenant_id) as session:
                await session.execute(
                    text(
                        "UPDATE goals SET status = 'waiting_children', updated_at = now() "
                        "WHERE id = :p AND tenant_id = :t AND status = 'planning'"
                    ),
                    {"p": parent_goal_id, "t": tenant_id},
                )
        return False
    _log.info("fanout_parent_requeued", goal_id=parent_goal_id, tenant_id=tenant_id)
    return True


async def wake_parent_of(
    factory: Any,
    *,
    tenant_id: str,
    child_goal_id: str,
    enqueue: EnqueueFn | None = None,
) -> bool:
    """:func:`wake_parent` for the parent of ``child_goal_id`` (no-op without one)."""
    from sqlalchemy import text

    async with _tenant_session(factory, tenant_id) as session:
        parent = (
            await session.execute(
                text(
                    "SELECT p.id FROM goals c JOIN goals p "
                    "ON p.id = c.parent_goal_id AND p.tenant_id = c.tenant_id "
                    "WHERE c.id = :c AND c.tenant_id = :t AND p.status = 'waiting_children'"
                ),
                {"c": child_goal_id, "t": tenant_id},
            )
        ).scalar()
    if parent is None:
        return False
    return await wake_parent(
        factory, tenant_id=tenant_id, parent_goal_id=str(parent), enqueue=enqueue
    )


async def active_child_ids(factory: Any, *, tenant_id: str, parent_goal_id: str) -> list[str]:
    """The parent's sub-goals that are not terminal yet (bounded)."""
    from sqlalchemy import text

    async with _tenant_session(factory, tenant_id) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id FROM goals WHERE tenant_id = :t AND parent_goal_id = :p "
                    f"AND status NOT IN {_TERMINAL_SQL} ORDER BY created_at LIMIT 64"
                ),
                {"t": tenant_id, "p": parent_goal_id},
            )
        ).all()
    return [str(r[0]) for r in rows]


async def _append_event(
    factory: Any, tenant_id: str, goal_id: str, event: dict[str, Any], publish: PublishFn | None
) -> None:
    from app.services.event_store import EventStore
    from app.tenancy.context import PlanTier, TenantContext

    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="fanout-sweeper")
    seq = await EventStore(factory).append_event(goal_id, event, tenant_ctx=ctx)
    if publish is not None:
        with contextlib.suppress(Exception):
            publish(tenant_id, goal_id, {**event, "_seq": seq})


async def _cancel_overdue_child(
    factory: Any,
    child: dict[str, Any],
    *,
    redis_client: Any,
    release_slot: ReleaseSlotFn | None,
    publish: PublishFn | None,
) -> bool:
    """Stop a child its parent no longer waits for: signal its runner, record cancelled."""
    from sqlalchemy import text

    from app.reliability.goal_lifecycle import signal_cancel_sync

    goal_id, tenant_id = child["child_goal_id"], child["tenant_id"]
    reason = f"Sub-goal timed out after {child['timeout_s']}s (its parent stopped waiting)"
    # The runner (a worker, any replica) polls this flag and stops at once.
    signal_cancel_sync(goal_id, redis_client)
    async with _tenant_session(factory, tenant_id) as session:
        changed = (
            await session.execute(
                text(
                    "UPDATE goals SET status = 'cancelled', error_message = :msg, "
                    "updated_at = now() WHERE id = :g AND tenant_id = :t "
                    f"AND status NOT IN {_TERMINAL_SQL} RETURNING id"
                ),
                {"msg": reason, "g": goal_id, "t": tenant_id},
            )
        ).scalar()
    if changed is None:
        return False
    with contextlib.suppress(Exception):
        await _append_event(
            factory,
            tenant_id,
            goal_id,
            {"type": "goal_cancelled", "reason": reason, "failure_reason": "subgoal_timeout"},
            publish,
        )
    if release_slot is not None:
        with contextlib.suppress(Exception):
            await release_slot(tenant_id, goal_id, child["execution_context"])
    return True


async def sweep_fanout(
    system_factory: Any,
    *,
    redis_client: Any,
    enqueue: EnqueueFn | None = None,
    release_slot: ReleaseSlotFn | None = None,
    publish: PublishFn | None = None,
    batch: int = _SWEEP_BATCH,
) -> dict[str, Any]:
    """Cancel overdue fan-out children and wake every parent with nothing left to wait on.

    ``system_factory`` must be the maintenance (BYPASSRLS) session factory: both
    scans are cross-tenant. Each overdue row is claimed under ``FOR UPDATE SKIP
    LOCKED``, so concurrent sweepers never time out the same child twice.
    """
    from sqlalchemy import text

    from app.db.rls import system_session

    timed_out: list[dict[str, Any]] = []
    extended = 0
    async with system_factory() as session, session.begin(), system_session(session):
        rows = (
            await session.execute(
                text(
                    "SELECT l.tenant_id, l.parent_goal_id, l.kind, l.task_key, "
                    "l.child_goal_id, l.timeout_s, g.status, g.execution_context "
                    "FROM goal_fanout_ledger l "
                    "LEFT JOIN goals g ON g.id = l.child_goal_id AND g.tenant_id = l.tenant_id "
                    "WHERE l.status = 'dispatched' AND l.deadline_at IS NOT NULL "
                    "AND l.deadline_at < now() "
                    "ORDER BY l.deadline_at LIMIT :batch FOR UPDATE OF l SKIP LOCKED"
                ),
                {"batch": batch},
            )
        ).all()
        for r in rows:
            keys = {"t": r[0], "p": r[1], "k": r[2], "key": r[3]}
            where = (
                "WHERE tenant_id = :t AND parent_goal_id = :p AND kind = :k AND task_key = :key"
            )
            status = None if r[6] is None else str(r[6])
            timeout_s = int(r[5] or 0) or 300
            if status == "waiting_human":
                # Time spent waiting for a human does not count against the child.
                await session.execute(
                    text(
                        "UPDATE goal_fanout_ledger SET deadline_at = now() + "
                        "make_interval(secs => CAST(:secs AS double precision)), "
                        f"updated_at = now() {where}"
                    ),
                    {**keys, "secs": timeout_s},
                )
                extended += 1
            elif status is None or status in ("complete", "failed", "cancelled"):
                # Finished (or erased): the parent's re-entry folds it in.
                await session.execute(
                    text(f"UPDATE goal_fanout_ledger SET deadline_at = NULL {where}"), keys
                )
            else:
                await session.execute(
                    text(
                        "UPDATE goal_fanout_ledger SET status = 'failed', error = :err, "
                        f"deadline_at = NULL, updated_at = now() {where}"
                    ),
                    {**keys, "err": f"Timeout after {timeout_s}s"},
                )
                timed_out.append(
                    {
                        "tenant_id": str(r[0]),
                        "parent_goal_id": str(r[1]),
                        "kind": str(r[2]),
                        "task_key": str(r[3]),
                        "child_goal_id": str(r[4]),
                        "timeout_s": timeout_s,
                        "execution_context": _ctx(r[7]),
                    }
                )

    cancelled: list[str] = []
    for child in timed_out:
        try:
            if await _cancel_overdue_child(
                system_factory,
                child,
                redis_client=redis_client,
                release_slot=release_slot,
                publish=publish,
            ):
                cancelled.append(child["child_goal_id"])
        except Exception as exc:
            _log.warning(
                "fanout_child_cancel_failed", goal_id=child["child_goal_id"], error=str(exc)[:200]
            )

    async with system_factory() as session, session.begin(), system_session(session):
        parked = (
            await session.execute(
                text(
                    "SELECT p.id, p.tenant_id FROM goals p "
                    "WHERE p.status = 'waiting_children' AND NOT EXISTS ("
                    "SELECT 1 FROM goals c WHERE c.tenant_id = p.tenant_id "
                    f"AND c.parent_goal_id = p.id AND c.status NOT IN {_TERMINAL_SQL}) "
                    "ORDER BY p.updated_at LIMIT :batch"
                ),
                {"batch": batch},
            )
        ).all()
    woken: list[str] = []
    for parent_id, tenant_id in parked:
        try:
            if await wake_parent(
                system_factory,
                tenant_id=str(tenant_id),
                parent_goal_id=str(parent_id),
                enqueue=enqueue,
            ):
                woken.append(str(parent_id))
        except Exception as exc:
            _log.warning("fanout_parent_wake_failed", goal_id=str(parent_id), error=str(exc)[:200])
    if timed_out or woken or extended:
        _log.warning(
            "fanout_swept",
            timed_out=[c["child_goal_id"] for c in timed_out],
            cancelled=cancelled,
            woken=woken,
            deadlines_extended=extended,
        )
    return {
        "children_timed_out": [c["child_goal_id"] for c in timed_out],
        "children_cancelled": cancelled,
        "deadlines_extended": extended,
        "parents_woken": woken,
    }
