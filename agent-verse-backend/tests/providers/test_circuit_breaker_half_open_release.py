"""PROV-28: a half-open probe that ends without a result must not wedge the circuit.

call_with_circuit_breaker recorded outcomes only for Exception, so a cancelled
probe (CancelledError is a BaseException) — or a caller-side refusal that is not
a provider failure — left the breaker half-open with its single probe slot used:
is_open() stayed True until the process restarted.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from app.providers import circuit_breaker as cb_mod
from app.providers.circuit_breaker import (
    ProviderCircuitBreaker,
    ProviderCircuitOpenError,
    call_with_circuit_breaker,
)


@pytest.fixture
def breaker(monkeypatch: pytest.MonkeyPatch) -> ProviderCircuitBreaker:
    b = ProviderCircuitBreaker(failure_threshold=1, recovery_timeout=0.05)
    monkeypatch.setattr(cb_mod, "_provider_cb", b)
    return b


class _Slow:
    async def complete(self, *_a: Any) -> str:
        await asyncio.sleep(10)
        return "late"


class _Ok:
    async def complete(self, *_a: Any) -> str:
        return "ok"


class _RefusedError(Exception):
    provider_failure = False


class _Refuser:
    async def complete(self, *_a: Any) -> str:
        raise _RefusedError("budget")


def _open(b: ProviderCircuitBreaker, name: str) -> None:
    b.record_failure(name)
    assert b.is_open(name)


async def test_cancelled_probe_releases_half_open_slot(breaker: ProviderCircuitBreaker) -> None:
    _open(breaker, "p")
    await asyncio.sleep(0.06)  # recovery timeout elapses -> half-open
    probe = asyncio.create_task(call_with_circuit_breaker(_Slow(), "complete", provider_name="p"))
    await asyncio.sleep(0.01)
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe
    # The next call is admitted as a new probe, and its success closes the circuit.
    assert await call_with_circuit_breaker(_Ok(), "complete", provider_name="p") == "ok"
    assert not breaker.is_open("p")


async def test_non_provider_failure_probe_releases_slot(breaker: ProviderCircuitBreaker) -> None:
    _open(breaker, "q")
    await asyncio.sleep(0.06)
    with pytest.raises(_RefusedError):
        await call_with_circuit_breaker(_Refuser(), "complete", provider_name="q")
    assert await call_with_circuit_breaker(_Ok(), "complete", provider_name="q") == "ok"


def test_stuck_half_open_probe_times_out() -> None:
    """A probe whose outcome never arrives (lost task) frees its slot after the
    recovery timeout instead of holding the circuit open forever."""
    b = ProviderCircuitBreaker(failure_threshold=1, recovery_timeout=0.05)
    _open(b, "r")
    time.sleep(0.06)
    assert not b.is_open("r")  # -> half-open
    b.before_call("r")  # probe in flight, never reports
    assert b.is_open("r")
    time.sleep(0.06)
    assert not b.is_open("r")


async def test_open_circuit_still_fails_fast(breaker: ProviderCircuitBreaker) -> None:
    _open(breaker, "s")
    with pytest.raises(ProviderCircuitOpenError):
        await call_with_circuit_breaker(_Ok(), "complete", provider_name="s")
