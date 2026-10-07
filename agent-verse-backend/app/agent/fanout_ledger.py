"""Durable fan-out ledger for supervisor sub-goals and goal-tree children.

A parent goal that fans out (the in-graph supervisor, CORE-31; the goal tree,
CORE-10) used to keep its decomposition and children only in memory: a parent
redelivered after a worker crash decomposed again and re-ran children whose
side effects had already happened. ``goal_fanout_ledger`` (Postgres, tenant
RLS) records, per planned child, the decomposition, the dispatched child goal
id and the final status/result, so a resumed parent re-attaches to what it
already started and reuses what already finished.

Every write is awaited. ``plan`` raises when the decomposition cannot be made
durable, so the caller can refuse to fan out (fail closed) instead of
launching children it could not account for after a crash.

Continuations (a01-F006-05 / a01-F007-01): on a worker, a parent no longer waits
for its children in its Celery slot. It dispatches them as real goals (each
with a per-child ``timeout_s`` / ``deadline_at``), parks in ``waiting_children``
and is re-queued when the last child is terminal. On re-entry
:func:`reconcile_children` folds every finished child's outcome (read from its
``goals`` row and persisted events) into the ledger, so the parent continues
from the durable state instead of streaming events.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# A parent never plans more than this many children (supervisor: 6, goal tree:
# a few dozen); the bound keeps every ledger read O(1).
MAX_LEDGER_ENTRIES = 64

TERMINAL_STATUSES = frozenset({"complete", "failed"})
# A child GOAL's terminal statuses (goals.status).
TERMINAL_GOAL_STATUSES = frozenset({"complete", "failed", "cancelled"})

# goals.execution_context key of a parked parent: which fan-out it waits on.
FANOUT_WAIT_KEY = "_fanout_wait"
# execution_context key a fanned-out child carries: the fan-out kind that made it.
FANOUT_KIND_KEY = "_fanout_kind"

# Child timeouts are bounded below so a bad value never cancels every child at once.
MIN_CHILD_TIMEOUT_S = 30.0
_FALLBACK_CHILD_TIMEOUT_S = 1800.0

# Events a child's outcome is read from (persisted goal_events), bounded.
_CHILD_EVENT_TYPES = ("step_complete", "goal_complete", "goal_failed", "goal_cancelled",
                      "worker_failed")
_CHILD_EVENT_LIMIT = 500


class FanoutParked(Exception):  # noqa: N818 - a control-flow signal, not an error
    """A fan-out parent ended its run waiting for its sub-goals (not a failure).

    Raised only where a wrapper would otherwise treat the parked state as a failed
    attempt (the persistence engine); carries the parked ``AgentState``.
    """

    def __init__(self, state: Any) -> None:
        super().__init__("fan-out parent parked waiting for its sub-goals")
        self.state = state


@dataclass
class ChildGoalState:
    """A dispatched child's ``goals`` row as the parent sees it."""

    status: str
    error_message: str = ""

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_GOAL_STATUSES


# (child goal status, child error_message, child events) -> (status, result, error)
OutcomeFn = Callable[[str, str, list[dict[str, Any]]], tuple[str, str, str]]


@dataclass
class LedgerEntry:
    task_key: str
    position: int
    spec: dict[str, Any] = field(default_factory=dict)
    child_goal_id: str | None = None
    status: str = "planned"
    result: str = ""
    error: str = ""

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL_STATUSES


class FanoutLedger:
    """One parent goal's ledger of one fan-out ``kind`` (``supervisor``/``goal_tree``)."""

    def __init__(
        self, session_factory: Any, *, tenant_id: str, parent_goal_id: str, kind: str
    ) -> None:
        self._factory = session_factory
        self._tenant_id = tenant_id
        self._parent = parent_goal_id
        self._kind = kind

    def _session(self) -> Any:
        from contextlib import AsyncExitStack, asynccontextmanager

        from app.db.rls import sqlalchemy_rls_context

        @asynccontextmanager
        async def _cm() -> Any:
            async with AsyncExitStack() as stack:
                session = await stack.enter_async_context(self._factory())
                await stack.enter_async_context(session.begin())
                await stack.enter_async_context(
                    sqlalchemy_rls_context(session, self._tenant_id)
                )
                yield session

        return _cm()

    def _keys(self) -> dict[str, Any]:
        return {"tid": self._tenant_id, "pid": self._parent, "kind": self._kind}

    async def load(self) -> list[LedgerEntry]:
        """The parent's planned children, in plan order (bounded)."""
        from sqlalchemy import text

        async with self._session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT task_key, position, spec, child_goal_id, status, result, error "
                        "FROM goal_fanout_ledger "
                        "WHERE tenant_id = :tid AND parent_goal_id = :pid AND kind = :kind "
                        "ORDER BY position, task_key LIMIT :lim"
                    ),
                    {**self._keys(), "lim": MAX_LEDGER_ENTRIES},
                )
            ).mappings().all()
        out: list[LedgerEntry] = []
        for row in rows:
            spec = row["spec"]
            if isinstance(spec, str):
                spec = json.loads(spec)
            out.append(
                LedgerEntry(
                    task_key=str(row["task_key"]),
                    position=int(row["position"]),
                    spec=dict(spec or {}),
                    child_goal_id=row["child_goal_id"],
                    status=str(row["status"]),
                    result=str(row["result"] or ""),
                    error=str(row["error"] or ""),
                )
            )
        return out

    async def plan(self, entries: list[LedgerEntry]) -> list[LedgerEntry]:
        """Record the decomposition (first writer wins) and return what is stored.

        ``ON CONFLICT DO NOTHING`` + re-read: two concurrent deliveries of the same
        parent converge on one plan instead of each launching its own children.
        Raises when the plan cannot be written.
        """
        from sqlalchemy import text

        async with self._session() as session:
            for entry in entries[:MAX_LEDGER_ENTRIES]:
                await session.execute(
                    text(
                        "INSERT INTO goal_fanout_ledger "
                        "(tenant_id, parent_goal_id, kind, task_key, position, spec) "
                        "VALUES (:tid, :pid, :kind, :key, :pos, CAST(:spec AS JSONB)) "
                        "ON CONFLICT (tenant_id, parent_goal_id, kind, task_key) DO NOTHING"
                    ),
                    {
                        **self._keys(),
                        "key": entry.task_key[:64],
                        "pos": entry.position,
                        "spec": json.dumps(entry.spec, default=str),
                    },
                )
        return await self.load()

    async def mark_dispatched(
        self, task_key: str, child_goal_id: str, *, timeout_s: float | None = None
    ) -> None:
        """Record the child's goal id; with ``timeout_s`` also its deadline (sweeper)."""
        if timeout_s is None:
            await self._update(task_key, child_goal_id=child_goal_id, status="dispatched")
            return
        from sqlalchemy import text

        async with self._session() as session:
            await session.execute(
                text(
                    "UPDATE goal_fanout_ledger SET child_goal_id = :child, "
                    "status = 'dispatched', timeout_s = CAST(:secs AS integer), "
                    "deadline_at = now() + make_interval(secs => CAST(:secs AS integer)), "
                    "updated_at = now() "
                    "WHERE tenant_id = :tid AND parent_goal_id = :pid AND kind = :kind "
                    "AND task_key = :key"
                ),
                {**self._keys(), "key": task_key, "child": child_goal_id,
                 "secs": int(timeout_s)},
            )

    async def mark_finished(
        self, task_key: str, *, status: str, result: str = "", error: str = ""
    ) -> None:
        await self._update(task_key, status=status, result=result, error=error)

    async def _update(self, task_key: str, **values: Any) -> None:
        from sqlalchemy import text

        sets = ", ".join(f"{col} = :{col}" for col in values)
        async with self._session() as session:
            await session.execute(
                text(
                    f"UPDATE goal_fanout_ledger SET {sets}, updated_at = now() "
                    "WHERE tenant_id = :tid AND parent_goal_id = :pid AND kind = :kind "
                    "AND task_key = :key"
                ),
                {**self._keys(), "key": task_key, **values},
            )

    async def child_goal_states(self) -> dict[str, ChildGoalState]:
        """task_key -> the dispatched child's goals row (status, error), this tenant only."""
        from sqlalchemy import text

        async with self._session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT l.task_key, g.status, g.error_message "
                        "FROM goal_fanout_ledger l "
                        "JOIN goals g ON g.id = l.child_goal_id AND g.tenant_id = l.tenant_id "
                        "WHERE l.tenant_id = :tid AND l.parent_goal_id = :pid "
                        "AND l.kind = :kind AND l.child_goal_id IS NOT NULL "
                        "LIMIT :lim"
                    ),
                    {**self._keys(), "lim": MAX_LEDGER_ENTRIES},
                )
            ).all()
        return {
            str(r[0]): ChildGoalState(status=str(r[1]), error_message=str(r[2] or ""))
            for r in rows
        }

    async def child_events(self, child_goal_id: str) -> list[dict[str, Any]]:
        """The child's outcome-relevant persisted events, in sequence order (bounded)."""
        from sqlalchemy import text

        from app.guardrails_v2.output_screening import redact_legacy_event

        async with self._session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT payload FROM goal_events "
                        "WHERE tenant_id = :tid AND goal_id = :gid "
                        "AND event_type = ANY(CAST(:types AS text[])) "
                        "ORDER BY sequence LIMIT :lim"
                    ),
                    {"tid": self._tenant_id, "gid": child_goal_id,
                     "types": list(_CHILD_EVENT_TYPES), "lim": _CHILD_EVENT_LIMIT},
                )
            ).all()
        out: list[dict[str, Any]] = []
        for (payload,) in rows:
            if isinstance(payload, str):
                payload = json.loads(payload)
            if isinstance(payload, dict):
                out.append(redact_legacy_event(dict(payload)))
        return out

    async def find_child_goal(self, task_key: str) -> str | None:
        """A sub-goal row already created for *task_key* (by a crashed earlier run).

        ``submit_goal`` inserts the child's ``goals`` row (``parent_goal_id`` set,
        ``_fanout_task_key`` in its execution context) before the ledger learns
        its id; a crash in between must not launch the child twice.
        """
        from sqlalchemy import text

        async with self._session() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id FROM goals "
                        "WHERE tenant_id = :tid AND parent_goal_id = :pid "
                        "AND execution_context ->> :marker = :key "
                        "ORDER BY created_at LIMIT 1"
                    ),
                    {
                        "tid": self._tenant_id,
                        "pid": self._parent,
                        "marker": FANOUT_TASK_KEY,
                        "key": task_key,
                    },
                )
            ).first()
        return str(row[0]) if row is not None else None


# execution_context key a fanned-out sub-goal carries: its ledger task key.
FANOUT_TASK_KEY = "_fanout_task_key"


def ledger_for(
    session_factory: Any, *, tenant_id: str | None, parent_goal_id: str | None, kind: str
) -> FanoutLedger | None:
    """A ledger when the parent is a real persisted goal and a DB is wired, else None."""
    if session_factory is None or not tenant_id or not parent_goal_id:
        return None
    if len(str(parent_goal_id)) > 32:  # a synthetic (goal-tree child) id: no goals row
        return None
    return FanoutLedger(
        session_factory, tenant_id=tenant_id, parent_goal_id=str(parent_goal_id), kind=kind
    )


def goal_child_timeout_default(context: Any, graph_default: Any) -> float | None:
    """A goal's default per-child timeout: its ``subgoal_timeout_seconds``, else the
    graph's (the parent's effective goal timeout, set by the worker), else None."""
    requested = context.get("subgoal_timeout_seconds") if isinstance(context, dict) else None
    for value in (requested, graph_default):
        if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
            return float(value)
    return None


def child_timeout_seconds(
    *, requested: Any = None, default: Any = None, tenant_ctx: Any = None
) -> float:
    """A fan-out child's timeout (replaces the fixed 300 s in-slot wait).

    ``requested`` (the plan's per-task ``timeout_seconds``) wins, then ``default``
    (the goal's ``subgoal_timeout_seconds`` or the parent's effective goal timeout:
    the agent's ``timeout_seconds`` capped by the plan), else the tenant plan's goal
    timeout. Always within [``MIN_CHILD_TIMEOUT_S``, plan goal timeout].
    """
    cap = _FALLBACK_CHILD_TIMEOUT_S
    plan = getattr(tenant_ctx, "plan", None)
    if plan is not None:
        from app.tenancy.context import PLAN_LIMITS

        limits = PLAN_LIMITS.get(plan)
        if limits is not None:
            cap = float(limits.goal_timeout_seconds)

    def _num(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, int | float | str):
            return None
        try:
            number = float(value)
        except ValueError:
            return None
        return number if number > 0 else None

    chosen = _num(requested) or _num(default) or cap
    return max(MIN_CHILD_TIMEOUT_S, min(cap, chosen))


async def reconcile_children(
    ledger: Any,
    entries: list[LedgerEntry],
    outcome: OutcomeFn,
) -> list[LedgerEntry]:
    """Fold every dispatched child that reached a terminal goal status into the ledger.

    Returns the entries finished by THIS call (in plan order). A dispatched child
    whose goals row is gone (erased) is failed rather than waited on forever.
    """
    pending = [e for e in entries if not e.finished and e.child_goal_id]
    if not pending:
        return []
    states: dict[str, ChildGoalState] = await ledger.child_goal_states()
    finished: list[LedgerEntry] = []
    for entry in pending:
        state = states.get(entry.task_key)
        if state is None:
            status, result, error = "failed", "", "sub-goal row no longer exists"
        elif not state.terminal:
            continue
        else:
            events = await ledger.child_events(str(entry.child_goal_id))
            status, result, error = outcome(state.status, state.error_message, events)
        await ledger.mark_finished(
            entry.task_key, status=status, result=result, error=error[:2000]
        )
        entry.status, entry.result, entry.error = status, result, error
        finished.append(entry)
    return finished


def _bridged(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return event
    merged = dict(payload)
    for key, value in event.items():
        if key != "payload":
            merged.setdefault(key, value)
    return merged


def failure_reason(goal_status: str, error_message: str, events: list[dict[str, Any]]) -> str:
    """Why a child that did not complete ended (its row's error, else its last event)."""
    if error_message.strip():
        return error_message.strip()
    for raw in reversed(events):
        evt = _bridged(raw)
        if evt.get("type") in ("goal_failed", "goal_cancelled", "worker_failed"):
            return str(evt.get("reason") or evt.get("type"))
    return f"sub-goal {goal_status}"
