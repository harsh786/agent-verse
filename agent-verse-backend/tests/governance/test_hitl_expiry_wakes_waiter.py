"""P5-4: an approval that expires wakes the goal waiting on it (any replica).

P0 baseline §4.6: the beat task ``expire_hitl_approvals`` only UPDATEd the row to
``timed_out`` and never pushed ``hitl_result:{id}``, so a goal blocked in the
cross-replica BLPOP wait sat ``executing`` for its full 3600 s HITL timeout.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import fakeredis.aioredis
import pytest

from app.governance.hitl import ApprovalStatus, HITLGateway, release_expired_waiters
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="p5-4-tenant", plan=PlanTier.ENTERPRISE, api_key_id="k1")


def _gateway(redis: Any) -> HITLGateway:
    gw = HITLGateway(timeout_seconds=3600)
    gw._redis = redis
    return gw


async def test_expiry_release_wakes_a_waiter_on_another_replica() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    replica_a = _gateway(redis)
    request_id = await replica_a.request_approval_async(
        goal_id="g-exp", action="deploy", tenant_ctx=T
    )
    waiter = asyncio.create_task(
        replica_a.wait_for_approval(request_id, tenant_ctx=T, timeout=3600)
    )
    await asyncio.sleep(0.1)
    assert not waiter.done()

    # The beat (another process) expired the row and releases the waiter.
    released = await release_expired_waiters(redis, [request_id])
    assert released == 1

    started = time.monotonic()
    status = await asyncio.wait_for(waiter, timeout=10)
    assert status is ApprovalStatus.TIMED_OUT
    assert time.monotonic() - started < 10


async def test_expired_coordination_goal_fails_honestly_instead_of_hanging() -> None:
    from types import SimpleNamespace

    from app.coordination.pattern_runs.goal_bridge import (
        CoordinationGoalBridge,
        PatternGoalFailedError,
    )

    redis = fakeredis.aioredis.FakeRedis()
    gw = _gateway(redis)
    bridge = CoordinationGoalBridge(lambda: None, hitl_timeout_seconds=3600)
    events: list[dict[str, Any]] = []

    async def emit(evt: dict[str, Any]) -> None:
        events.append(evt)

    gate = asyncio.create_task(
        bridge._require_approval(
            SimpleNamespace(hitl_gateway=gw), T, "g-moa", emit, action="Run mixture_of_agents"
        )
    )
    for _ in range(50):
        if events:
            break
        await asyncio.sleep(0.05)
    request_id = events[0]["request_id"]
    await release_expired_waiters(redis, [request_id])

    with pytest.raises(PatternGoalFailedError) as info:
        await asyncio.wait_for(gate, timeout=10)
    assert info.value.reason_code == "approval_timed_out"


async def test_release_payload_and_ttl() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    assert await release_expired_waiters(redis, ["r1", "r2"]) == 2
    raw = await redis.lpop("hitl_result:r1")
    assert json.loads(raw)["action"] == "expired"
    assert 0 < await redis.ttl("hitl_result:r2") <= 86400
    assert await release_expired_waiters(None, ["r3"]) == 0


def test_beat_expiry_task_releases_waiters(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scaling import tasks

    import fakeredis

    server = fakeredis.FakeServer()

    async def _expired() -> list[str]:
        return ["req-1", "req-2"]

    async def _notified(ids: list[str]) -> list[str]:
        return []

    monkeypatch.setattr(tasks, "_expire_db_approvals", _expired)
    monkeypatch.setattr(tasks, "_notify_expired_approvals", _notified)
    monkeypatch.setattr(
        tasks, "_hitl_release_redis", lambda: fakeredis.aioredis.FakeRedis(server=server)
    )

    out = tasks.expire_hitl_approvals.run()

    assert out["expired"] == 2
    assert out["waiters_released"] == 2

    reader = fakeredis.FakeRedis(server=server)
    payloads = [reader.lpop(f"hitl_result:{i}") for i in ("req-1", "req-2")]
    assert all(json.loads(p)["action"] == "expired" for p in payloads)
