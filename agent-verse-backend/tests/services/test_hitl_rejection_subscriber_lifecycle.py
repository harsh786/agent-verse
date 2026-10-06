"""The HITL rejection subscriber is owned, supervised and stopped cleanly.

The backend log showed "Task was destroyed but it is pending!" for
``hitl-rejection-subscriber``: it was created with ``loop.create_task`` and no
reference kept, so the garbage collector could destroy it while running and
rejection notes silently stopped reaching the next plan cycle. Nothing
restarted it and nothing cancelled it on shutdown.
"""

from __future__ import annotations

import asyncio
import gc
import json
from typing import Any

import fakeredis.aioredis
import pytest
import redis.asyncio as aioredis

from app.services.goal_service import GoalRecord, GoalService, GoalStatus

pytestmark = pytest.mark.asyncio


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> Any:
    server = fakeredis.FakeServer()

    def _from_url(*_a: Any, **_k: Any) -> Any:
        return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

    monkeypatch.setattr(aioredis, "from_url", _from_url)
    return _from_url


def _service_with_goal(goal_id: str) -> GoalService:
    svc = GoalService()
    svc._goals[goal_id] = GoalRecord(
        goal_id=goal_id,
        goal_text="g",
        status=GoalStatus.WAITING_HUMAN,
        tenant_id="t1",
        priority="normal",
        dry_run=False,
        created_at="",
    )
    return svc


async def _wait_subscribed(client: Any) -> None:
    for _ in range(100):
        if await client.execute_command("PUBSUB", "NUMPAT") >= 1:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("subscriber never subscribed")


async def test_subscriber_survives_gc_and_still_delivers_a_rejection(fake_redis: Any) -> None:
    svc = _service_with_goal("g1")
    svc.start_hitl_rejection_subscriber("redis://fake/0")
    task = svc._hitl_rejection_task
    assert task is not None and task in svc._background_tasks  # strongly held
    del task
    publisher = fake_redis()
    await _wait_subscribed(publisher)

    gc.collect()
    await asyncio.sleep(0)
    gc.collect()

    await publisher.publish("hitl_rejected:g1", json.dumps({"goal_id": "g1", "note": "no"}))
    for _ in range(100):
        if svc._goals["g1"].hitl_rejection_note == "no":
            break
        await asyncio.sleep(0.02)
    assert svc._goals["g1"].hitl_rejection_note == "no"
    await svc.stop_background_subscribers()


async def test_subscriber_is_restarted_after_an_unexpected_exit(
    fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _service_with_goal("g2")
    calls: list[int] = []
    real = GoalService._subscribe_hitl_rejections

    async def flaky(self: GoalService, redis_url: str) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("subscriber crashed")
        await real(self, redis_url)

    monkeypatch.setattr(GoalService, "_subscribe_hitl_rejections", flaky)
    monkeypatch.setattr("app.services.goal_service._SUBSCRIBER_RESTART_DELAY_S", 0.01)
    svc.start_hitl_rejection_subscriber("redis://fake/0")
    publisher = fake_redis()
    await _wait_subscribed(publisher)
    assert len(calls) == 2
    await svc.stop_background_subscribers()


async def test_stop_cancels_the_subscribers_and_does_not_restart(fake_redis: Any) -> None:
    svc = _service_with_goal("g3")
    svc.start_hitl_rejection_subscriber("redis://fake/0")
    await _wait_subscribed(fake_redis())
    tasks = set(svc._background_tasks)
    assert len(tasks) == 1  # no fleet-wide goal_events bridge any more (a08-F190-07)

    await svc.stop_background_subscribers()
    await asyncio.sleep(0.05)
    assert all(t.cancelled() or t.done() for t in tasks)
    assert svc._background_tasks == set()
    assert svc._hitl_rejection_task is None


async def test_starting_twice_keeps_one_subscriber(fake_redis: Any) -> None:
    svc = _service_with_goal("g4")
    svc.start_hitl_rejection_subscriber("redis://fake/0")
    first = svc._hitl_rejection_task
    svc.start_hitl_rejection_subscriber("redis://fake/0")
    assert svc._hitl_rejection_task is first
    await svc.stop_background_subscribers()
