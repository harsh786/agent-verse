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
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# A parent never plans more than this many children (supervisor: 6, goal tree:
# a few dozen); the bound keeps every ledger read O(1).
MAX_LEDGER_ENTRIES = 64

TERMINAL_STATUSES = frozenset({"complete", "failed"})


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

    async def mark_dispatched(self, task_key: str, child_goal_id: str) -> None:
        await self._update(task_key, child_goal_id=child_goal_id, status="dispatched")

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
