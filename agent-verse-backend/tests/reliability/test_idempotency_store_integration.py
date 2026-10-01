"""SVC-02 integration: owner-checked idempotency claims on real Redis (WATCH/MULTI).

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/reliability/test_idempotency_store_integration.py -m integration --no-cov
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.reliability.idempotency import IdempotencyStore

pytestmark = pytest.mark.integration


@pytest.fixture
async def store(redis_url: str) -> AsyncIterator[IdempotencyStore]:
    import redis.asyncio as aioredis

    client: Any = aioredis.from_url(redis_url, decode_responses=True)
    yield IdempotencyStore(client)
    await client.aclose()


async def test_heartbeat_keeps_claim_and_only_owner_completes(store: IdempotencyStore) -> None:
    key, tenant = uuid.uuid4().hex, "t-idem-int"
    assert await store.claim(key, tenant, owner="A", body_hash="h", pending_ttl_seconds=0.5) is None
    for _ in range(4):  # 1.2 s total, beyond the 0.5 s pending TTL
        await asyncio.sleep(0.3)
        assert await store.extend(key, tenant, owner="A", body_hash="h", ttl_seconds=0.5)
    retry = await store.claim(key, tenant, owner="B", body_hash="h")
    assert retry is not None and retry["state"] == "pending"
    assert not await store.extend(key, tenant, owner="B", body_hash="h", ttl_seconds=5)
    assert await store.complete(key, tenant, {"goal_id": "gA"}, owner="B", body_hash="h") is False
    assert await store.complete(key, tenant, {"goal_id": "gA"}, owner="A", body_hash="h") is True
    done = await store.claim(key, tenant, owner="C", body_hash="other")
    assert done is not None and done["response"] == {"goal_id": "gA"} and done["body"] == "h"


async def test_release_is_owner_checked(store: IdempotencyStore) -> None:
    key, tenant = uuid.uuid4().hex, "t-idem-int"
    assert await store.claim(key, tenant, owner="A", body_hash="h") is None
    await store.release(key, tenant, owner="B", body_hash="h")
    assert await store.claim(key, tenant, owner="B", body_hash="h") is not None
    await store.release(key, tenant, owner="A", body_hash="h")
    assert await store.claim(key, tenant, owner="B", body_hash="h") is None
