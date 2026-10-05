"""P5-1: provider 429s are retried with backoff and never trip the circuit breaker.

P0 baseline §4.5/§4.13: an ``openai.RateLimitError`` escaped the planner and the
verifier (they caught only RuntimeError/TimeoutError) and counted as a breaker
failure, so a few throttled calls refused every LLM caller in the process for 60 s.
"""

from __future__ import annotations

_ISOLATE_PROVIDER_ENV = True

from typing import Any

import httpx
import openai
import pytest

from app.providers import circuit_breaker as cb
from app.providers import rate_limit as rl
from app.providers.base import CompletionRequest, Message
from app.providers.tenant_provider import tenant_circuit_scope


def _429(retry_after: str | None = None) -> openai.RateLimitError:
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    response = httpx.Response(
        429, headers=headers, request=httpx.Request("POST", "https://llm.test/v1/chat")
    )
    return openai.RateLimitError("Error code: 429 - too many requests", response=response, body=None)


class _Provider:
    _default_model = "shared-model"

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls = 0

    async def complete(self, request: Any) -> Any:
        self.calls += 1
        item = self.script.pop(0) if self.script else "ok"
        if isinstance(item, BaseException):
            raise item
        return item


def _req() -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content="hi")], model="shared-model")


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    monkeypatch.setattr(cb, "_provider_cb", cb.ProviderCircuitBreaker(failure_threshold=2))
    slept: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(rl, "_sleep", _fake_sleep)
    monkeypatch.setattr(
        rl, "current_policy", lambda: rl.RateLimitPolicy(max_retries=4, max_total_wait=120.0)
    )
    return slept


async def test_429_then_success_is_retried(_fast: list[float]) -> None:
    p = _Provider([_429(), "answer"])
    assert await cb.complete_with_failover(p, _req()) == "answer"
    assert p.calls == 2
    assert len(_fast) == 1 and _fast[0] > 0


async def test_retry_after_header_is_honoured(_fast: list[float]) -> None:
    p = _Provider([_429("7"), "answer"])
    assert await cb.complete_with_failover(p, _req()) == "answer"
    assert _fast[0] >= 7.0


async def test_backoff_grows_exponentially_without_a_hint(_fast: list[float]) -> None:
    p = _Provider([_429(), _429(), _429(), "answer"])
    assert await cb.complete_with_failover(p, _req()) == "answer"
    assert len(_fast) == 3
    # full jitter within [ceil/2, ceil] with ceil = base * 2**attempt
    assert 0.5 <= _fast[0] <= 1.0
    assert 1.0 <= _fast[1] <= 2.0
    assert 2.0 <= _fast[2] <= 4.0


async def test_429s_do_not_trip_the_breaker(_fast: list[float]) -> None:
    p = _Provider([_429()] * 20)
    for _ in range(3):
        with pytest.raises(rl.ProviderRateLimitedError):
            await cb.complete_with_failover(p, _req())
    key = cb.breaker_key(p, _req())
    assert not cb._provider_cb.is_open(key)
    # the very next healthy call goes through (no "circuit open" fast-fail)
    p.script = ["fine"]
    assert await cb.complete_with_failover(p, _req()) == "fine"


async def test_exhaustion_is_a_runtime_error_the_planner_and_verifier_catch(
    _fast: list[float],
) -> None:
    p = _Provider([_429()] * 20)
    with pytest.raises(RuntimeError) as info:
        await cb.complete_with_failover(p, _req())
    assert isinstance(info.value, rl.ProviderRateLimitedError)
    assert isinstance(info.value.__cause__, openai.RateLimitError)
    assert p.calls == 5  # 1 + max_retries


async def test_retry_after_beyond_goal_budget_fails_fast(_fast: list[float]) -> None:
    p = _Provider([_429("30"), "answer"])
    async with rl.llm_deadline(5.0):
        with pytest.raises(rl.ProviderRateLimitedError, match="time budget"):
            await cb.complete_with_failover(p, _req())
    assert _fast == []
    assert p.calls == 1


async def test_one_tenants_throttling_does_not_block_another(_fast: list[float]) -> None:
    throttled = _Provider([_429()] * 50)
    throttled._circuit_scope = tenant_circuit_scope("tenant-a")  # type: ignore[attr-defined]
    healthy = _Provider([])
    healthy._circuit_scope = tenant_circuit_scope("tenant-b")  # type: ignore[attr-defined]
    for _ in range(3):
        with pytest.raises(rl.ProviderRateLimitedError):
            await cb.complete_with_failover(throttled, _req())
    _fast.clear()
    assert await cb.complete_with_failover(healthy, _req()) == "ok"
    assert await cb.complete_with_failover(_Provider([]), _req()) == "ok"
    assert _fast == []  # nobody else waited


async def test_real_failures_still_open_the_breaker(_fast: list[float]) -> None:
    p = _Provider([RuntimeError("500 upstream")] * 5)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="500"):
            await cb.complete_with_failover(p, _req())
    with pytest.raises(cb.ProviderCircuitOpenError):
        await cb.complete_with_failover(p, _req())
    assert _fast == []  # non-429 errors are not retried here


def test_breaker_key_includes_provider_identity() -> None:
    from app.observability.traced_provider import TracedProvider

    class _Nim(_Provider):
        provider_name = "nvidia_nim"

    assert cb.breaker_key(_Nim([]), _req()) == "llm:nvidia_nim/shared-model"
    traced = TracedProvider(_Provider([]), provider_system="openai", default_role="planner")
    assert cb.breaker_key(traced, _req()) == "llm:openai/shared-model"


def test_retry_after_ms_and_http_date_parsing() -> None:
    response = httpx.Response(
        429, headers={"retry-after-ms": "1500"}, request=httpx.Request("GET", "https://x.test")
    )
    exc = openai.RateLimitError("429", response=response, body=None)
    assert rl.retry_after_seconds(exc) == pytest.approx(1.5)
    assert rl._parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0
    assert rl.is_rate_limit_error(exc)
    assert not rl.is_rate_limit_error(RuntimeError("boom"))
