"""TRG-22: worker-run goals publish ``goal.score_below`` from their scorecard.

``goal.score_below`` was published only from GoalService's in-process eval
path, which needs the goal record in that replica's memory; the worker published
only completed/failed, so goal_score_below triggers never fired for production
(worker-run) goals. The worker now publishes it from the scorecard the verifier
persisted on the final state, with the deterministic completion id that dedups
a Celery retry or an API relay of the same goal.

Does not drive the whole run_goal task: that path opens real Postgres/Redis.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from typing import Any

from app.tenancy.context import PlanTier, TenantContext
from app.triggers.consumers.chain import ChainTriggerConsumer
from app.triggers.dispatcher import TriggerDispatcher
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore

CTX = TenantContext(tenant_id="tenant-1", plan=PlanTier.FREE, api_key_id="k")


class _SyncRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def publish(self, channel: str, data: str) -> int:
        self.published.append((channel, data))
        return 1


class _DedupRedis:
    def __init__(self) -> None:
        self.keys: set[str] = set()

    async def set(self, key: str, value: Any, ex: int = 0, nx: bool = False) -> Any:
        if nx and key in self.keys:
            return None
        self.keys.add(key)
        return True


class _GoalService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_goal(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"goal_id": f"created-{len(self.calls)}"}


def _state(score: float) -> Any:
    return SimpleNamespace(context={"eval_scorecard": SimpleNamespace(average_score=lambda: score)})


def _publish(monkeypatch: Any, score: float) -> list[str]:
    from app.scaling import tasks

    redis = _SyncRedis()
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: redis)
    for _ in range(2):  # a Celery retry / API relay publishes the same event again
        tasks._publish_worker_score_below(
            _state(score), tenant_id="tenant-1", goal_id="goal-w1", agent_id="agent-a",
            plan="free", trigger_chain_depth=0, source_trigger_id="",
        )
    return [d for c, d in redis.published if c == "goal.score_below"]


async def test_low_scoring_worker_goal_fires_goal_score_below_exactly_once(monkeypatch) -> None:
    raws = _publish(monkeypatch, 0.4)
    assert raws and json.loads(raws[0])["score"] == 0.4
    assert json.loads(raws[0])["completion_event_id"] == "goal-w1:goal.score_below"

    store = ScheduleStore()
    store.create(
        spec=TriggerSpec(trigger_type=TriggerType.GOAL_SCORE_BELOW, score_threshold=0.7),
        tenant_ctx=CTX, goal_id="", goal_template="investigate the low score",
    )
    gs = _GoalService()
    consumer = ChainTriggerConsumer(
        trigger_store=store,
        dispatcher=TriggerDispatcher(goal_service=gs, redis=_DedupRedis()),
    )
    for raw in raws:
        await consumer._handle({"type": "message", "channel": "goal.score_below", "data": raw})
    assert len(gs.calls) == 1


async def test_high_scoring_worker_goal_does_not_fire(monkeypatch) -> None:
    raws = _publish(monkeypatch, 0.95)
    store = ScheduleStore()
    store.create(
        spec=TriggerSpec(trigger_type=TriggerType.GOAL_SCORE_BELOW, score_threshold=0.7),
        tenant_ctx=CTX, goal_id="", goal_template="x",
    )
    gs = _GoalService()
    consumer = ChainTriggerConsumer(
        trigger_store=store, dispatcher=TriggerDispatcher(goal_service=gs, redis=_DedupRedis())
    )
    await consumer._handle({"type": "message", "channel": "goal.score_below", "data": raws[0]})
    assert gs.calls == []


def test_no_scorecard_publishes_nothing(monkeypatch) -> None:
    from app.scaling import tasks

    redis = _SyncRedis()
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: redis)
    assert tasks._publish_worker_score_below(
        SimpleNamespace(context={}), tenant_id="t", goal_id="g", agent_id="",
        plan="free", trigger_chain_depth=0, source_trigger_id="",
    ) is False
    assert redis.published == []


def test_run_goal_publishes_score_below_after_completion() -> None:
    from app.scaling import tasks

    assert "_publish_worker_score_below(" in inspect.getsource(tasks.run_goal.run)
