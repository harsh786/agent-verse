"""Integration (Redis): worker goal events reach SSE without a fleet-wide fan-in.

SVC-07 kept replica memory flat under the old goal_events:* bridge;
a08-F190-07 removes the bridge itself: a replica streaming a worker goal
SUBSCRIBEs to that goal's channel only, so other goals' events (any tenant)
are never delivered to, or decoded by, it.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/services/test_sse_bridge_memory_integration.py -m integration --no-cov
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from app.services.goal_service import SSE_HEARTBEAT_TYPE, GoalRecord, GoalService, GoalStatus
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration


def _rec(gid: str) -> GoalRecord:
    return GoalRecord(
        goal_id=gid, goal_text="x", status=GoalStatus.EXECUTING, tenant_id="t-bridge",
        priority="normal", dry_run=False, created_at="",
    )


async def test_a_worker_goal_stream_hears_only_its_own_channel(redis_url: str) -> None:
    import redis.asyncio as aioredis

    gid = uuid.uuid4().hex
    ctx = TenantContext(tenant_id="t-bridge", plan=PlanTier.FREE, api_key_id="k")
    svc = GoalService(db_session_factory=object())
    svc._redis_url_for_pubsub = redis_url
    svc._goals[gid] = _rec(gid)  # held here, run by a worker (no local task)

    async def _row(goal_id: str, tenant_ctx: TenantContext) -> GoalRecord:
        return _rec(goal_id)

    async def _nothing(*_a: Any, **_k: Any) -> AsyncGenerator[dict[str, Any], None]:
        return
        yield {}

    svc._db_get_goal_record = _row  # type: ignore[method-assign]
    svc._replay_events = _nothing  # type: ignore[method-assign]
    svc._persisted_events_after = _nothing  # type: ignore[method-assign]

    received: list[dict[str, Any]] = []

    async def _consume() -> None:
        async for ev in svc.subscribe_events(gid, ctx):
            if ev.get("type") != SSE_HEARTBEAT_TYPE:
                received.append(ev)

    consumer = asyncio.create_task(_consume())
    pub = aioredis.from_url(redis_url, decode_responses=True)
    channel = f"goal_events:t-bridge:{gid}"
    try:
        for _ in range(200):
            if (await pub.pubsub_numsub(channel))[0][1]:
                break
            await asyncio.sleep(0.02)
        assert await pub.pubsub_numpat() == 0  # no goal_events:* pattern subscriber
        for i in range(2_000):
            other = f"other-{i}"
            await pub.publish(
                f"goal_events:t-x:{other}",
                json.dumps({"goal_id": other, "tenant_id": "t-x", "type": "step_started"}),
            )
        await pub.publish(
            channel,
            json.dumps(
                {"goal_id": gid, "tenant_id": "t-bridge", "type": "goal_complete",
                 "payload": {"type": "goal_complete"}, "_seq": 3}
            ),
        )
        await asyncio.wait_for(consumer, timeout=10)
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        await pub.aclose()
    assert received == [{"type": "goal_complete", "_seq": 3}]
    assert list(svc._goals) == [gid]
