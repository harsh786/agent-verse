"""SVC-06 integration (Redis): closing a cross-replica stream releases its pub/sub
subscription, and a worker_failed envelope ends the stream.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/services/test_cross_replica_stream_integration.py -m integration --no-cov
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.services import goal_service as gs_mod
from app.services.goal_service import GoalRecord, GoalService, GoalStatus
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration

CTX = TenantContext(tenant_id="t-xr", plan=PlanTier.FREE, api_key_id="k")


def _svc(redis_url: str, goal_id: str) -> GoalService:
    svc = GoalService()
    svc._redis_url_for_pubsub = redis_url
    svc._db_get_goal_record = AsyncMock(  # type: ignore[method-assign]
        return_value=GoalRecord(
            goal_id=goal_id,
            goal_text="g",
            status=GoalStatus.WAITING_HUMAN,
            tenant_id=CTX.tenant_id,
            priority="normal",
            dry_run=False,
            created_at=datetime.now(UTC).isoformat(),
        )
    )
    svc._list_persisted_events = AsyncMock(return_value=[])  # type: ignore[method-assign]
    return svc


async def test_closed_stream_releases_its_subscription(
    redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import redis.asyncio as aioredis

    monkeypatch.setattr(gs_mod, "_SSE_HEARTBEAT_SECONDS", 0.1)
    gid = uuid.uuid4().hex
    channel = f"goal_events:{CTX.tenant_id}:{gid}"
    probe = aioredis.from_url(redis_url, decode_responses=True)
    try:
        gen = _svc(redis_url, gid).subscribe_events(gid, CTX)
        assert (await asyncio.wait_for(gen.__anext__(), 5))["type"] == "_sse_heartbeat"
        assert dict(await probe.pubsub_numsub(channel))[channel] == 1
        await gen.aclose()  # what the endpoint does when the client disconnects
        await asyncio.sleep(0.2)
        assert dict(await probe.pubsub_numsub(channel))[channel] == 0
    finally:
        await probe.aclose()


async def test_worker_failed_envelope_ends_stream(redis_url: str) -> None:
    import redis.asyncio as aioredis

    gid = uuid.uuid4().hex
    channel = f"goal_events:{CTX.tenant_id}:{gid}"
    svc = _svc(redis_url, gid)
    pub = aioredis.from_url(redis_url, decode_responses=True)

    async def _publish() -> None:
        await asyncio.sleep(0.5)
        await pub.publish(
            channel,
            json.dumps(
                {
                    "goal_id": gid,
                    "tenant_id": CTX.tenant_id,
                    "type": "worker_failed",
                    "payload": {"type": "worker_failed", "error": "lock lost"},
                }
            ),
        )

    task = asyncio.create_task(_publish())
    try:
        events = await asyncio.wait_for(_collect(svc, gid), timeout=10)
    finally:
        await task
        await pub.aclose()
    assert events[-1] == {"type": "worker_failed", "error": "lock lost"}


async def _collect(svc: GoalService, gid: str) -> list[dict[str, object]]:
    return [e async for e in svc.subscribe_events(gid, CTX) if e["type"] != "_sse_heartbeat"]
