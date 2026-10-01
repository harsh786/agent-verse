"""SVC-07 integration (Redis): the Celery->SSE bridge keeps replica memory flat.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/services/test_sse_bridge_memory_integration.py -m integration --no-cov
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import suppress

import pytest

from app.services.goal_service import GoalRecord, GoalService, GoalStatus

pytestmark = pytest.mark.integration


async def test_bridge_tracks_only_locally_known_goals(redis_url: str) -> None:
    import redis.asyncio as aioredis

    svc = GoalService()
    watched = GoalRecord(
        goal_id=uuid.uuid4().hex,
        goal_text="x",
        status=GoalStatus.EXECUTING,
        tenant_id="t-bridge",
        priority="normal",
        dry_run=False,
        created_at="",
    )
    q: asyncio.Queue = asyncio.Queue()
    watched.subscribers.append(q)
    svc._goals[watched.goal_id] = watched

    task = asyncio.create_task(svc._subscribe_celery_goal_events(redis_url))
    pub = aioredis.from_url(redis_url, decode_responses=True)
    try:
        await asyncio.sleep(0.5)  # let the psubscribe land
        for i in range(2_000):
            gid = f"other-{i}"
            await pub.publish(
                f"goal_events:t-x:{gid}",
                json.dumps({"goal_id": gid, "tenant_id": "t-x", "type": "step_started"}),
            )
        await pub.publish(
            f"goal_events:t-bridge:{watched.goal_id}",
            json.dumps(
                {"goal_id": watched.goal_id, "tenant_id": "t-bridge", "type": "goal_complete"}
            ),
        )
        first = await asyncio.wait_for(q.get(), timeout=10)
        assert first["type"] == "goal_complete"
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await pub.aclose()
    assert list(svc._goals) == [watched.goal_id]
    assert watched.status == GoalStatus.COMPLETE and watched.completed_at
