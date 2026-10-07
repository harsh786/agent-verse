"""a08-F198-02: a HALF_OPEN whose probe never reports cannot wedge the breaker.

OPEN -> HALF_OPEN was a plain SET (no TTL; it also dropped the TTL
record_failure had put on the key) and the HALF_OPEN branch refused every
caller unconditionally. A prober that died without recording success or
failure left the breaker HALF_OPEN for the whole fleet, forever: the claim key
expired, but nobody could take a new probe.
"""

from __future__ import annotations

import time
from typing import Any

import fakeredis.aioredis
import pytest

from app.reliability.circuit_breaker import CircuitState
from app.reliability.redis_circuit_breaker import RedisCircuitBreaker

pytestmark = pytest.mark.asyncio


def _breaker(redis: Any) -> RedisCircuitBreaker:
    return RedisCircuitBreaker(
        redis_client=redis,
        tenant_id="t1",
        tool_name="mcp:jira",
        failure_threshold=2,
        cooldown_seconds=10,
    )


async def _open_and_cool_down(redis: Any, breaker: RedisCircuitBreaker) -> None:
    await breaker.record_failure_async()
    await breaker.record_failure_async()
    assert await breaker.get_state() == CircuitState.OPEN
    # Cooldown elapsed.
    await redis.set("cb:t1:mcp:jira:opened_at", str(time.time() - 11), ex=20)


async def test_half_open_state_key_expires() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    breaker = _breaker(redis)
    await _open_and_cool_down(redis, breaker)

    assert await breaker.can_call_async() is True  # the probe
    assert await breaker.get_state() == CircuitState.HALF_OPEN
    ttl = await redis.ttl("cb:t1:mcp:jira:state")
    assert 0 < ttl <= 20


async def test_a_lost_probe_lets_the_next_caller_probe_after_its_claim_expires() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    replica_a, replica_b = _breaker(redis), _breaker(redis)
    await _open_and_cool_down(redis, replica_a)

    assert await replica_a.can_call_async() is True  # replica A's probe ...
    assert await replica_b.can_call_async() is False  # ... is the only one in flight

    # Replica A dies mid-probe: no success/failure is ever recorded. Its claim
    # key expires (its own short TTL).
    await redis.delete("cb:t1:mcp:jira:half_open_claim")

    assert await replica_b.can_call_async() is True  # a fresh probe, not a wedge
    assert await replica_a.can_call_async() is False  # still one probe at a time
    await replica_b.record_success_async()
    assert await replica_a.can_call_async() is True  # closed again


@pytest.mark.integration
async def test_lost_probe_recovery_on_a_real_redis(redis_url: str) -> None:
    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url, decode_responses=True)
    try:
        await client.delete(*[f"cb:t1:mcp:jira:{k}" for k in
                              ("state", "failures", "opened_at", "half_open_claim")])
        replica_a, replica_b = _breaker(client), _breaker(client)
        await _open_and_cool_down(client, replica_a)
        assert await replica_a.can_call_async() is True
        assert 0 < await client.ttl("cb:t1:mcp:jira:state") <= 20
        assert await replica_b.can_call_async() is False
        await client.delete("cb:t1:mcp:jira:half_open_claim")  # the prober died
        assert await replica_b.can_call_async() is True
    finally:
        await client.aclose()
