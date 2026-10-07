"""In-memory stand-in for app.agent.fanout_ledger.FanoutLedger (same interface)."""

from __future__ import annotations

from typing import Any

from app.agent.fanout_ledger import ChildGoalState, LedgerEntry


class MemoryLedger:
    def __init__(self) -> None:
        self.entries: dict[str, LedgerEntry] = {}
        self.goal_rows: dict[str, str] = {}  # task_key -> child goal id (goals table)
        # Continuations: the child goals' rows and persisted events.
        self.child_status: dict[str, ChildGoalState] = {}  # child goal id -> row
        self.events: dict[str, list[dict[str, Any]]] = {}  # child goal id -> events
        self.timeouts: dict[str, float] = {}  # task_key -> dispatched timeout_s

    async def load(self) -> list[LedgerEntry]:
        return sorted(
            (LedgerEntry(**vars(e)) for e in self.entries.values()), key=lambda e: e.position
        )

    async def plan(self, entries: list[LedgerEntry]) -> list[LedgerEntry]:
        for e in entries:
            self.entries.setdefault(e.task_key, LedgerEntry(**vars(e)))
        return await self.load()

    async def mark_dispatched(
        self, task_key: str, child_goal_id: str, *, timeout_s: float | None = None
    ) -> None:
        self.entries[task_key].child_goal_id = child_goal_id
        self.entries[task_key].status = "dispatched"
        if timeout_s is not None:
            self.timeouts[task_key] = timeout_s

    async def mark_finished(
        self, task_key: str, *, status: str, result: str = "", error: str = ""
    ) -> None:
        e = self.entries[task_key]
        e.status, e.result, e.error = status, result, error

    async def find_child_goal(self, task_key: str) -> str | None:
        return self.goal_rows.get(task_key)

    async def child_goal_states(self) -> dict[str, ChildGoalState]:
        return {
            key: self.child_status[e.child_goal_id]
            for key, e in self.entries.items()
            if e.child_goal_id and e.child_goal_id in self.child_status
        }

    async def child_events(self, child_goal_id: str) -> list[dict[str, Any]]:
        return list(self.events.get(child_goal_id, []))

    # test helper: a child goal reached a terminal status with these events
    def finish_child(
        self, child_goal_id: str, status: str, events: list[dict[str, Any]], error: str = ""
    ) -> None:
        self.child_status[child_goal_id] = ChildGoalState(status=status, error_message=error)
        self.events[child_goal_id] = events
