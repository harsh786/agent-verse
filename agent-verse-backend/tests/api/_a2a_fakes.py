"""Shared fake GoalService for the A2A API tests (A2A-05: no goal service = 503)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any


class FakeGoalService:
    """Accepts every goal and reports it complete with a fixed summary."""

    def __init__(self, *, status: str = "complete", summary: str = "done") -> None:
        self.submitted: list[dict[str, Any]] = []
        self._status = status
        self._summary = summary

    async def submit_goal(self, **kwargs: Any) -> dict[str, Any]:
        goal_id = uuid.uuid4().hex
        self.submitted.append({"goal_id": goal_id, **kwargs})
        return {"goal_id": goal_id, "status": "queued"}

    async def subscribe_events(
        self, goal_id: str, tenant_ctx: Any
    ) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "goal_complete", "goal_id": goal_id}

    async def get_goal(self, goal_id: str, tenant_ctx: Any) -> dict[str, Any]:
        return {
            "goal_id": goal_id,
            "status": self._status,
            "result_artifact": {"summary": self._summary},
        }
