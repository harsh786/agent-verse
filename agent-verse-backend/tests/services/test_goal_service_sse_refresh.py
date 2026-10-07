"""SSE of a worker-run goal held by this replica streams from the goal's channel.

a08-F190-07: every API replica PSUBSCRIBEd goal_events:* and decoded every
worker event of every tenant, just to feed the few goals it held.
a08-F190-08: what it fed local SSE queues was the worker's
``{type, payload, goal_id, tenant_id}`` envelope, not the event, so the
same goal streamed different shapes on different replicas.

Now a goal this replica does not run (task is None) streams from
``goal_events:{tenant}:{goal}`` only (the per-goal SUBSCRIBE path), normalised.
(The earlier regression here, a subscriber lost on a DB refresh of the record,
was about the bridge's local queues, which no longer exist for such goals.)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import fakeredis
import fakeredis.aioredis
import pytest

from app.agent.state import GoalStatus
from app.services.goal_service import SSE_HEARTBEAT_TYPE, GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")


def _record() -> GoalRecord:
    return GoalRecord(
        goal_id="g1", goal_text="do it", status=GoalStatus.EXECUTING, tenant_id="t1",
        priority="normal", dry_run=False, created_at=datetime.now(UTC).isoformat(),
    )


@pytest.mark.asyncio
async def test_worker_goal_streams_normalised_events_from_its_own_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import redis.asyncio as aioredis

    server = fakeredis.FakeServer()
    monkeypatch.setattr(
        aioredis,
        "from_url",
        lambda *_a, **_k: fakeredis.aioredis.FakeRedis(server=server, decode_responses=True),
    )
    svc = GoalService(db_session_factory=object())
    svc._redis_url_for_pubsub = "redis://fake"
    local = _record()  # held here (submitted on this replica), run by a worker
    svc._goals["g1"] = local

    async def _row(goal_id: str, tenant_ctx: TenantContext) -> GoalRecord:
        return _record()

    async def _nothing(*_a: Any, **_k: Any) -> AsyncGenerator[dict[str, Any], None]:
        return
        yield {}

    svc._db_get_goal_record = _row  # type: ignore[method-assign]
    svc._replay_events = _nothing  # type: ignore[method-assign]
    svc._persisted_events_after = _nothing  # type: ignore[method-assign]

    received: list[dict[str, Any]] = []

    async def _consume() -> None:
        async for ev in svc.subscribe_events("g1", CTX):
            if ev.get("type") != SSE_HEARTBEAT_TYPE:
                received.append(ev)

    consumer = asyncio.create_task(_consume())
    pub = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    channel = "goal_events:t1:g1"
    for _ in range(100):  # until the stream has SUBSCRIBEd
        if (await pub.pubsub_numsub(channel))[0][1]:
            break
        await asyncio.sleep(0.01)
    assert local.subscribers == []  # no local queue: nothing to bridge into
    # Only the goal's own channel: nobody listens to other goals' events.
    assert await pub.pubsub_numpat() == 0

    def envelope(event: dict[str, Any], seq: int) -> str:
        return json.dumps(
            {"goal_id": "g1", "tenant_id": "t1", "type": event["type"],
             "payload": event, "_seq": seq}
        )

    await pub.publish(channel, envelope({"type": "step_complete", "step": 1}, 4))
    await pub.publish(channel, envelope({"type": "goal_complete", "result": "ok"}, 5))
    await asyncio.wait_for(consumer, timeout=3.0)

    assert received == [
        {"type": "step_complete", "step": 1, "_seq": 4},
        {"type": "goal_complete", "result": "ok", "_seq": 5},
    ]


def test_locally_running_and_finished_goals_keep_the_local_path() -> None:
    svc = GoalService(db_session_factory=object())
    svc._redis_url_for_pubsub = "redis://fake"
    rec = _record()
    assert svc._streams_from_goal_channel(rec) is True
    rec.status = GoalStatus.COMPLETE
    assert svc._streams_from_goal_channel(rec) is False
    rec.status = GoalStatus.EXECUTING
    rec.dry_run = True
    assert svc._streams_from_goal_channel(rec) is False
    rec.dry_run = False
    svc._redis_url_for_pubsub = ""
    assert svc._streams_from_goal_channel(rec) is False
