"""a08-F196-05 on a real Redis (testcontainer): two replicas' consumers read the
goal lifecycle stream through one consumer group, and a goal outcome published
twice (in-process relay + worker, or a redelivery) is notified exactly once.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/services/test_goal_notifications_redis.py -m integration --no-cov
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.integration


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def notify_goal_outcome(self, **kw: Any) -> dict[str, Any]:
        self.sent.append(kw)
        return {"sent": 1, "channels": [{"channel_id": "c", "status": "sent"}]}


async def test_two_replicas_one_notification_per_goal_outcome(
    redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import redis.asyncio as aioredis

    from app.core.config import get_settings
    from app.services.goal_notifications import GoalNotificationConsumer
    from app.services.notification_prefs import prefs_key, serialize_prefs
    from app.triggers.bus import publish_trigger_event
    from app.triggers.consumers.chain import build_chain_event

    suffix = uuid.uuid4().hex[:8]
    monkeypatch.setenv("TRIGGER_BUS_BLOCK_MS", "100")
    monkeypatch.setenv("TRIGGER_BUS_STREAM_GOAL", f"trigger:stream:goal:it-{suffix}")
    get_settings.cache_clear()
    client = aioredis.from_url(redis_url, decode_responses=True)
    rec = _Recorder()
    replicas = [
        GoalNotificationConsumer(redis=client, notification_service=rec) for _ in range(2)
    ]
    tasks: list[asyncio.Task[Any]] = []
    try:
        tenant, other = f"t-{suffix}", f"o-{suffix}"
        await client.set(prefs_key(tenant), serialize_prefs({"goalFailed": True}))
        tasks = [asyncio.create_task(r.start()) for r in replicas]
        await asyncio.sleep(0.3)  # both joined the group
        for _ in range(2):  # the same outcome published twice
            await publish_trigger_event(
                client,
                "goal.failed",
                build_chain_event(channel="goal.failed", tenant_id=tenant, goal_id="g1"),
            )
        # A tenant that never opted in, and an outcome the tenant did not opt into.
        await publish_trigger_event(
            client, "goal.failed", build_chain_event(channel="goal.failed", tenant_id=other, goal_id="g2")
        )
        await publish_trigger_event(
            client,
            "goal.completed",
            build_chain_event(channel="goal.completed", tenant_id=tenant, goal_id="g3"),
        )
        for _ in range(50):
            pending = await client.xpending(
                f"trigger:stream:goal:it-{suffix}", "notifications:goal-outcome"
            )
            groups = await client.xinfo_groups(f"trigger:stream:goal:it-{suffix}")
            if groups and int(groups[0].get("lag") or 0) == 0 and not pending["pending"]:
                break
            await asyncio.sleep(0.1)
        assert [(s["tenant_id"], s["goal_id"], s["status"]) for s in rec.sent] == [
            (tenant, "g1", "failed")
        ]
    finally:
        for r in replicas:
            await r.stop()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.aclose()
        get_settings.cache_clear()
