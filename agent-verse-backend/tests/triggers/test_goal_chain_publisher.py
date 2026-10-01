"""Regression: goal-chain (Family B) triggers never fired.

ChainTriggerConsumer subscribes to ``goal.completed`` / ``goal.failed`` /
``goal.score_below``, but nothing in the app published to those channels. The
GoalService now publishes them on terminal events, once per goal, with a
deterministic completion id and the goal's chain depth (so chains stay bounded).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.agent.state import GoalStatus
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext
from app.triggers.consumers.chain import MAX_CHAIN_DEPTH, ChainTriggerConsumer

CTX = TenantContext(tenant_id="t-chain", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _record(goal_id: str, **ctx: Any) -> GoalRecord:
    return GoalRecord(
        goal_id=goal_id,
        goal_text="g",
        status=GoalStatus.EXECUTING,
        tenant_id=CTX.tenant_id,
        priority="normal",
        dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
        agent_id="agent-1",
        execution_context=dict(ctx),
    )


def _published(redis: AsyncMock, channel: str) -> list[dict[str, Any]]:
    return [
        json.loads(c.args[1]) for c in redis.publish.call_args_list if c.args[0] == channel
    ]


@pytest.mark.asyncio
async def test_goal_complete_publishes_goal_completed_once() -> None:
    svc = GoalService()
    redis = AsyncMock()
    svc._redis = redis
    svc._goals["g1"] = _record("g1", trigger_chain_depth=2)
    await svc._dispatch_event("g1", {"type": "goal_complete"}, tenant_ctx=CTX)
    await svc._dispatch_event("g1", {"type": "goal_complete"}, tenant_ctx=CTX)  # relay
    events = _published(redis, "goal.completed")
    assert len(events) == 1
    ev = events[0]
    assert ev["tenant_id"] == CTX.tenant_id and ev["goal_id"] == "g1"
    assert ev["agent_id"] == "agent-1"
    assert ev["trigger_chain_depth"] == 2
    assert ev["completion_event_id"] == "g1:goal.completed"
    assert ev["tenant_plan"] == "professional"


@pytest.mark.asyncio
async def test_goal_failed_publishes_goal_failed() -> None:
    svc = GoalService()
    redis = AsyncMock()
    svc._redis = redis
    svc._goals["g2"] = _record("g2")
    await svc._dispatch_event("g2", {"type": "goal_failed", "reason": "x"}, tenant_ctx=CTX)
    assert [e["goal_id"] for e in _published(redis, "goal.failed")] == ["g2"]
    assert _published(redis, "goal.completed") == []


@pytest.mark.asyncio
async def test_dry_run_goals_publish_nothing() -> None:
    svc = GoalService()
    redis = AsyncMock()
    svc._redis = redis
    rec = _record("g3")
    rec.dry_run = True
    svc._goals["g3"] = rec
    await svc._dispatch_event("g3", {"type": "goal_complete"}, tenant_ctx=CTX)
    assert _published(redis, "goal.completed") == []


class _Store:
    def __init__(self, specs: list[Any]) -> None:
        self._specs = specs

    async def find_by_type_async(
        self, trigger_type: str, tenant_id: str, strict: bool = False
    ) -> list[dict]:
        return [{"spec": s} for s in self._specs]


@pytest.mark.asyncio
async def test_published_event_drives_the_chain_consumer() -> None:
    """End to end: the payload GoalService publishes fires a goal_completed trigger."""
    svc = GoalService()
    redis = AsyncMock()
    svc._redis = redis
    svc._goals["g4"] = _record("g4")
    await svc._dispatch_event("g4", {"type": "goal_complete"}, tenant_ctx=CTX)
    (raw,) = [c.args[1] for c in redis.publish.call_args_list if c.args[0] == "goal.completed"]

    spec = SimpleNamespace(watch_agent_id="agent-1", watch_goal_id="", trigger_id="tr")
    dispatcher = SimpleNamespace(dispatch=AsyncMock())
    consumer = ChainTriggerConsumer(trigger_store=_Store([spec]), dispatcher=dispatcher)
    await consumer._handle({"type": "message", "channel": "goal.completed", "data": raw})
    dispatcher.dispatch.assert_awaited_once()
    args, kwargs = dispatcher.dispatch.await_args
    assert args[1]["trigger_chain_depth"] == 1
    assert kwargs["source_goal_id"] == "g4"
    assert kwargs["completion_event_id"] == "g4:goal.completed"


@pytest.mark.asyncio
async def test_chain_depth_is_carried_into_the_created_goal() -> None:
    """A chained goal records its depth so its own completion re-publishes it."""
    svc = GoalService()
    svc.submit_goal = AsyncMock(return_value={"goal_id": "new"})  # type: ignore[method-assign]
    await svc.create_goal(tenant_ctx=CTX, goal_text="next", trigger_chain_depth=3)
    ctx = svc.submit_goal.await_args.kwargs["execution_context"]
    assert ctx["trigger_chain_depth"] == 3


@pytest.mark.asyncio
async def test_dispatcher_passes_chain_depth_only_for_chained_events() -> None:
    from app.triggers.dispatcher import TriggerDispatcher

    gs = SimpleNamespace(create_goal=AsyncMock(return_value={"goal_id": "x"}))
    d = TriggerDispatcher.__new__(TriggerDispatcher)
    d._goal_service = gs
    spec = SimpleNamespace(agent_id="", watch_agent_id="")
    await d._create_goal(spec, "t", CTX, "idem", chain_depth=4)
    assert gs.create_goal.await_args.kwargs["trigger_chain_depth"] == 4
    await d._create_goal(spec, "t", CTX, "idem2")
    assert "trigger_chain_depth" not in gs.create_goal.await_args.kwargs


@pytest.mark.asyncio
async def test_max_depth_stops_the_chain() -> None:
    dispatcher = SimpleNamespace(dispatch=AsyncMock())
    consumer = ChainTriggerConsumer(
        trigger_store=_Store([SimpleNamespace(watch_agent_id="", watch_goal_id="")]),
        dispatcher=dispatcher,
    )
    data = json.dumps({"tenant_id": "t", "goal_id": "g", "trigger_chain_depth": MAX_CHAIN_DEPTH})
    await consumer._handle({"type": "message", "channel": "goal.completed", "data": data})
    dispatcher.dispatch.assert_not_awaited()


def test_celery_queue_sends_chain_depth_only_when_chained() -> None:
    from unittest.mock import patch

    from app.services.goal_queue import CeleryGoalTaskQueue

    base = dict(goal_id="g", tenant_id="t", goal_text="x", priority="normal", dry_run=False)
    with patch("app.scaling.tasks.run_goal.apply_async") as aa:
        CeleryGoalTaskQueue().enqueue_goal(**base, trigger_chain_depth=2)
        assert aa.call_args.kwargs["kwargs"]["trigger_chain_depth"] == 2
        CeleryGoalTaskQueue().enqueue_goal(**base)
        assert "trigger_chain_depth" not in aa.call_args.kwargs["kwargs"]


def test_run_goal_accepts_chain_depth() -> None:
    import inspect

    from app.scaling.tasks import run_goal

    params = inspect.signature(run_goal.run).parameters
    assert params["trigger_chain_depth"].default == 0
