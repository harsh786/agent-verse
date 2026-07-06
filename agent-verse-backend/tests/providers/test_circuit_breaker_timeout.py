from __future__ import annotations

import asyncio

import pytest


class SlowProvider:
    async def complete(self) -> str:
        await asyncio.sleep(1)
        return "done"


async def test_call_with_circuit_breaker_times_out_slow_provider_call() -> None:
    from app.providers import circuit_breaker as cb_mod
    from app.providers.circuit_breaker import ProviderCircuitBreaker, call_with_circuit_breaker

    cb_mod._provider_cb = ProviderCircuitBreaker()

    with pytest.raises(TimeoutError, match="timed out"):
        await call_with_circuit_breaker(
            SlowProvider(),
            "complete",
            provider_name="slow-provider-test",
            timeout_seconds=0.01,
        )

    assert cb_mod._provider_cb._failures["slow-provider-test"] == 1
