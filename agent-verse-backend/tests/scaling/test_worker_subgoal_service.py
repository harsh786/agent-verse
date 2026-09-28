"""Regression: the worker graph never had a GoalService (graph._goal_service).

The API path wires ``graph._goal_service`` so the in-graph supervisor node and the
civilization spawn tool can dispatch sub-goals; the Celery worker — which runs
every queued (production) goal — never did, so the supervisor silently no-op'd
and every spawn failed on the worker.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from app.scaling import tasks
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="wsg-t", plan=PlanTier.ENTERPRISE, api_key_id="k")


class _FakeGoalService:
    def __init__(self) -> None:
        self._goals: dict[str, Any] = {}
        self.subscribed: list[dict[str, Any]] = []
        self.other = "delegated"

    async def submit_goal(self, **kwargs: Any) -> dict[str, Any]:
        self._goals["sub-1"] = object()
        return {"goal_id": "sub-1", "status": "planning"}

    async def subscribe_events(self, goal_id: str, tenant_ctx: Any, since_sequence: int = 0):
        self.subscribed.append({"goal_id": goal_id, "since": since_sequence})
        yield {"type": "goal_complete", "goal_id": goal_id}


@pytest.mark.asyncio
async def test_submit_drops_local_record_so_subscribe_uses_cross_process_path() -> None:
    gs = _FakeGoalService()
    svc = tasks._WorkerSubgoalService(gs)

    result = await svc.submit_goal(goal="sub", priority="normal", dry_run=False, tenant_ctx=T)

    assert result["goal_id"] == "sub-1"
    # The sub-goal runs on another worker: a local record would never receive its
    # events, so it is dropped and subscribe_events goes cross-process.
    assert "sub-1" not in gs._goals
    events = [e async for e in svc.subscribe_events(goal_id="sub-1", tenant_ctx=T)]
    assert events[-1]["type"] == "goal_complete"
    assert gs.subscribed == [{"goal_id": "sub-1", "since": 0}]
    assert svc.other == "delegated"


def test_run_goal_wires_goal_service_onto_the_worker_graph() -> None:
    src = inspect.getsource(tasks.run_goal)
    assert "_agent_runner._goal_service = _WorkerSubgoalService(" in src
    assert "_redis_url_for_pubsub = REDIS_URL" in src
