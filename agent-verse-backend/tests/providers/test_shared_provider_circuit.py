"""a01-F023-02: provider circuits are shared by every replica and worker (Redis).

Each test simulates several processes by swapping the module-level local breaker
(``circuit_breaker._provider_cb`` — one per process) while they share one Redis.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis

from app.providers import circuit_breaker as cb_mod
from app.providers import shared_circuit
from app.providers.circuit_breaker import (
    ProviderCircuitBreaker,
    ProviderCircuitOpenError,
    call_with_circuit_breaker,
)

_KEY = "llm:vendor/model-shared-test"


class _Provider:
    def __init__(self, *, fail: bool = False, gate: asyncio.Event | None = None) -> None:
        self.fail = fail
        self.calls = 0
        self.gate = gate

    async def complete(self) -> str:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("endpoint down")
        return "ok"


@pytest.fixture
def redis() -> Iterator[FakeAsyncRedis]:
    client = FakeAsyncRedis(decode_responses=True)
    shared_circuit.configure_shared_circuit_redis(client)
    saved = cb_mod._provider_cb
    try:
        yield client
    finally:
        shared_circuit.configure_shared_circuit_redis(None)
        cb_mod._provider_cb = saved


def _new_process(threshold: int = 5) -> ProviderCircuitBreaker:
    """A fresh process: its own in-memory breaker, the fleet's Redis."""
    cb_mod._provider_cb = ProviderCircuitBreaker(failure_threshold=threshold)
    return cb_mod._provider_cb


async def _fail(n: int) -> None:
    for _ in range(n):
        with pytest.raises(RuntimeError):
            await call_with_circuit_breaker(_Provider(fail=True), "complete", provider_name=_KEY)


async def test_failures_on_one_replica_open_the_circuit_on_another(redis: Any) -> None:
    _new_process()
    await _fail(5)

    _new_process()  # another replica: it never saw a failure itself
    healthy = _Provider()
    with pytest.raises(ProviderCircuitOpenError):
        await call_with_circuit_breaker(healthy, "complete", provider_name=_KEY)
    assert healthy.calls == 0


async def test_failures_split_across_replicas_add_up(redis: Any) -> None:
    _new_process()
    await _fail(3)
    _new_process()
    await _fail(2)

    _new_process()
    with pytest.raises(ProviderCircuitOpenError):
        await call_with_circuit_breaker(_Provider(), "complete", provider_name=_KEY)


async def test_a_success_anywhere_closes_the_circuit_everywhere(redis: Any) -> None:
    _new_process()
    await _fail(4)
    _new_process()
    assert await call_with_circuit_breaker(_Provider(), "complete", provider_name=_KEY) == "ok"
    _new_process()
    await _fail(4)  # the count restarted: 4 more do not open it
    assert await call_with_circuit_breaker(_Provider(), "complete", provider_name=_KEY) == "ok"


async def _age_open_circuit(redis: Any, seconds: float) -> None:
    await redis.hset(f"agentverse:provider_cb:{_KEY}", "opened_at", repr(time.time() - seconds))


async def test_half_open_admits_exactly_one_probe_across_the_fleet(redis: Any) -> None:
    _new_process()
    await _fail(5)
    await _age_open_circuit(redis, 61)  # cool-down over

    _new_process()
    gate = asyncio.Event()
    probe_provider = _Provider(gate=gate)
    probe = asyncio.create_task(
        call_with_circuit_breaker(probe_provider, "complete", provider_name=_KEY)
    )
    await asyncio.sleep(0.01)
    assert probe_provider.calls == 1

    _new_process()  # another replica while the probe is in flight
    other = _Provider()
    with pytest.raises(ProviderCircuitOpenError):
        await call_with_circuit_breaker(other, "complete", provider_name=_KEY)
    assert other.calls == 0

    gate.set()
    assert await probe == "ok"
    _new_process()
    assert await call_with_circuit_breaker(_Provider(), "complete", provider_name=_KEY) == "ok"


async def test_a_failed_probe_reopens_the_circuit_for_everyone(redis: Any) -> None:
    _new_process()
    await _fail(5)
    await _age_open_circuit(redis, 61)

    _new_process()
    await _fail(1)  # the probe

    _new_process()
    with pytest.raises(ProviderCircuitOpenError):
        await call_with_circuit_breaker(_Provider(), "complete", provider_name=_KEY)


async def test_a_cancelled_probe_frees_the_fleet_probe_slot(redis: Any) -> None:
    _new_process()
    await _fail(5)
    await _age_open_circuit(redis, 61)

    _new_process()
    probe = asyncio.create_task(
        call_with_circuit_breaker(_Provider(gate=asyncio.Event()), "complete", provider_name=_KEY)
    )
    await asyncio.sleep(0.01)
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe

    _new_process()
    assert await call_with_circuit_breaker(_Provider(), "complete", provider_name=_KEY) == "ok"


async def test_the_probe_slot_expires_with_the_recovery_timeout(redis: Any) -> None:
    _new_process()
    await _fail(5)
    await _age_open_circuit(redis, 61)
    verdict = await shared_circuit.shared_admit(_KEY, recovery_timeout=60.0)
    assert verdict.probe_token is not None
    ttl = await redis.ttl(f"agentverse:provider_cb:{_KEY}:probe")
    assert 0 < ttl <= 60


async def test_streaming_executor_skips_a_model_open_elsewhere(redis: Any) -> None:
    _new_process()
    await _fail(5)
    _new_process()
    assert await cb_mod.circuit_open_anywhere(_KEY)


class _BrokenRedis:
    async def hget(self, *_a: Any, **_k: Any) -> Any:
        raise ConnectionError("redis down")

    hincrby = expire = hset = delete = get = set = exists = hget


async def test_a_redis_outage_never_refuses_a_call_and_local_protection_stays() -> None:
    shared_circuit.configure_shared_circuit_redis(_BrokenRedis())
    saved = cb_mod._provider_cb
    try:
        local = _new_process()
        assert await call_with_circuit_breaker(_Provider(), "complete", provider_name=_KEY) == "ok"
        await _fail(5)
        assert local.is_open(_KEY)
        with pytest.raises(ProviderCircuitOpenError):
            await call_with_circuit_breaker(_Provider(), "complete", provider_name=_KEY)
    finally:
        shared_circuit.configure_shared_circuit_redis(None)
        cb_mod._provider_cb = saved


async def test_tenant_scoped_circuits_stay_separate_in_redis(redis: Any) -> None:
    _new_process()
    for _ in range(5):
        with pytest.raises(RuntimeError):
            await call_with_circuit_breaker(
                _Provider(fail=True), "complete", provider_name=f"tenant-a:{_KEY}"
            )
    _new_process()
    assert (
        await call_with_circuit_breaker(_Provider(), "complete", provider_name=f"tenant-b:{_KEY}")
        == "ok"
    )
