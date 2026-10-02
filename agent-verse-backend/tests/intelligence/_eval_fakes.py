"""A scripted goal service for durable eval-suite run tests."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

_EVENT = {"complete": "goal_complete", "failed": "goal_failed", "cancelled": "goal_cancelled"}


class FakeGoals:
    """submit_goal / get_goal / get_events / cancel_goal over an in-memory table.

    ``outcome(goal_text, agent_id)`` decides each goal's terminal status;
    ``running_polls`` keeps a goal "executing" for that many get_goal polls.
    """

    def __init__(
        self,
        outcome: Callable[[str, str | None], str] | None = None,
        *,
        running_polls: int = 0,
        tool: str = "t",
    ) -> None:
        self.outcome = outcome or (lambda _goal, _agent: "complete")
        self.running_polls = running_polls
        self.tool = tool
        self.submits: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.goals: dict[str, dict[str, Any]] = {}
        self.lock = asyncio.Lock()

    async def submit_goal(self, **kw: Any) -> dict[str, Any]:
        async with self.lock:
            gid = uuid.uuid4().hex
            self.submits.append(kw)
            self.goals[gid] = {
                "final": self.outcome(kw["goal"], kw.get("agent_id")),
                "polls": 0,
                "agent_id": kw.get("agent_id"),
            }
            return {"goal_id": gid}

    async def get_goal(self, goal_id: str, tenant_ctx: Any) -> dict[str, Any]:
        g = self.goals[goal_id]
        g["polls"] += 1
        if goal_id in self.cancelled:
            return {"status": "cancelled"}
        if g["polls"] <= self.running_polls:
            return {"status": "executing"}
        return {"status": g["final"]}

    async def get_events(self, goal_id: str, tenant_ctx: Any) -> list[dict[str, Any]]:
        g = self.goals[goal_id]
        status = "cancelled" if goal_id in self.cancelled else g["final"]
        return [
            {"type": "tool_call_complete", "tool_name": self.tool, "output": "the answer"},
            {"type": _EVENT[status]},
        ]

    async def cancel_goal(self, goal_id: str, tenant_ctx: Any) -> dict[str, Any]:
        self.cancelled.append(goal_id)
        return {"goal_id": goal_id, "status": "cancelled"}


class FastSettings:
    """RunSettings source with tiny timings for tests."""

    eval_suite_lease_seconds = 30.0
    eval_suite_task_seconds_per_iteration = 20.0
    eval_suite_task_timeout_max_seconds = 1800.0
    eval_suite_goal_poll_seconds = 0.01
    eval_suite_max_task_attempts = 3
    eval_suite_run_concurrency = 4
