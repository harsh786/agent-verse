"""In-memory stand-in for app.agent.fanout_ledger.FanoutLedger (same interface)."""

from __future__ import annotations

from app.agent.fanout_ledger import LedgerEntry


class MemoryLedger:
    def __init__(self) -> None:
        self.entries: dict[str, LedgerEntry] = {}
        self.goal_rows: dict[str, str] = {}  # task_key -> child goal id (goals table)

    async def load(self) -> list[LedgerEntry]:
        return sorted(
            (LedgerEntry(**vars(e)) for e in self.entries.values()), key=lambda e: e.position
        )

    async def plan(self, entries: list[LedgerEntry]) -> list[LedgerEntry]:
        for e in entries:
            self.entries.setdefault(e.task_key, LedgerEntry(**vars(e)))
        return await self.load()

    async def mark_dispatched(self, task_key: str, child_goal_id: str) -> None:
        self.entries[task_key].child_goal_id = child_goal_id
        self.entries[task_key].status = "dispatched"

    async def mark_finished(
        self, task_key: str, *, status: str, result: str = "", error: str = ""
    ) -> None:
        e = self.entries[task_key]
        e.status, e.result, e.error = status, result, error

    async def find_child_goal(self, task_key: str) -> str | None:
        return self.goal_rows.get(task_key)
