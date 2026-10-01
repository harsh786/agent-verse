"""SVC-01 integration: 20 concurrent identical submits on real Redis -> one goal.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/services/test_goal_dedup_atomic_integration.py -m integration --no-cov
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.dedup import GoalDeduplicator
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis_client(redis_url: str) -> AsyncIterator[Any]:
    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url, decode_responses=True)
    yield client
    await client.aclose()


async def test_twenty_concurrent_identical_submits_create_one_goal(redis_client: Any) -> None:
    tenant = TenantContext(
        tenant_id=f"t-{uuid.uuid4().hex[:8]}", plan=PlanTier.PROFESSIONAL, api_key_id="k"
    )
    dedup = GoalDeduplicator(redis=redis_client)
    # Two replicas sharing Redis.
    replicas = [GoalService(task_queue=MagicMock()), GoalService(task_queue=MagicMock())]
    with (
        patch("app.services.dedup._default_deduplicator", dedup),
        patch("app.tenancy.limits.check_and_increment_concurrent_goals", AsyncMock()),
    ):
        results = await asyncio.gather(
            *(
                replicas[i % 2].submit_goal(
                    goal="Rotate the staging certs",
                    priority="normal",
                    dry_run=False,
                    tenant_ctx=tenant,
                )
                for i in range(20)
            )
        )
    ran = {r["goal_id"] for r in results if not r.get("deduplicated")}
    assert len(ran) == 1
    assert {r["goal_id"] for r in results} == ran

    # Terminal release (compare-and-delete via Lua on real Redis) frees the key.
    (winner,) = ran
    dedup_key = await redis_client.get(f"goal_dedup_owner:{winner}")
    assert dedup_key and await redis_client.get(dedup_key) == winner
    await dedup.release_goal(f"not-{winner}")
    assert await redis_client.get(dedup_key) == winner
    await dedup.release_goal(winner)
    assert await redis_client.get(dedup_key) is None
    assert await redis_client.get(f"goal_dedup_owner:{winner}") is None
