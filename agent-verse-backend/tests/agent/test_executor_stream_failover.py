"""Executor LLM calls time out and fail over to the next configured model."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.agent.graph import AgentGraph
from app.providers.base import CompletionRequest, CompletionResponse, Message
from app.providers.fake import FakeProvider


class _Routed:
    _default_model = "kimi"

    def __init__(self, bad: set[str], slow: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        self.bad, self.slow, self.calls = bad, set(slow), []

    async def stream_tokens(self, request: CompletionRequest, on_token: Any) -> Any:
        self.calls.append(request.model)
        await on_token("partial ")
        if request.model in self.slow:
            await asyncio.sleep(5)
        if request.model in self.bad:
            raise RuntimeError("empty completion")
        return CompletionResponse(content=f"ok:{request.model}", model=request.model,
                                  input_tokens=1, output_tokens=1)


def _graph(executor: Any) -> AgentGraph:
    fake = FakeProvider()
    g = AgentGraph(planner=fake, executor=fake, verifier=fake)
    g._executor = executor
    g._role_fallback_models = lambda: ["qwen", "kimi"]  # type: ignore[method-assign]
    return g


async def test_failed_model_falls_over_and_discards_partial_tokens() -> None:
    ex = _Routed(bad={"kimi"})
    buf: list[str] = []

    async def on_token(t: str) -> None:
        buf.append(t)

    resp = await _graph(ex)._stream_with_failover(
        CompletionRequest(messages=[Message(role="user", content="x")], model="kimi"), on_token, buf
    )
    assert resp.content == "ok:qwen" and ex.calls == ["kimi", "qwen"]
    assert buf == ["partial "]  # only the successful attempt's tokens remain


async def test_hung_model_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTVERSE_EXECUTOR_CALL_TIMEOUT_SECONDS", "0.05")
    ex = _Routed(bad=set(), slow={"kimi"})
    buf: list[str] = []

    async def on_token(t: str) -> None:
        buf.append(t)

    resp = await _graph(ex)._stream_with_failover(
        CompletionRequest(messages=[Message(role="user", content="x")], model="kimi"), on_token, buf
    )
    assert resp.content == "ok:qwen"


async def test_throttled_stream_is_retried_and_does_not_trip_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P5-1: a 429 on the executor stream retries the same model; no breaker hit."""
    import httpx
    import openai

    from app.providers import circuit_breaker as cb
    from app.providers import rate_limit as rl

    monkeypatch.setattr(cb, "_provider_cb", cb.ProviderCircuitBreaker(failure_threshold=1))
    slept: list[float] = []

    async def _no_sleep(d: float) -> None:
        slept.append(d)

    monkeypatch.setattr(rl, "_sleep", _no_sleep)

    class _Throttled:
        _default_model = "kimi"

        def __init__(self) -> None:
            self.calls: list[str] = []

        async def stream_tokens(self, request: CompletionRequest, on_token: Any) -> Any:
            self.calls.append(request.model)
            if len(self.calls) == 1:
                resp = httpx.Response(
                    429, headers={"retry-after": "3"}, request=httpx.Request("POST", "https://x.t")
                )
                raise openai.RateLimitError("429", response=resp, body=None)
            await on_token("hi")
            return CompletionResponse(
                content=f"ok:{request.model}", model=request.model, input_tokens=1, output_tokens=1
            )

    ex = _Throttled()
    buf: list[str] = []

    async def on_token(t: str) -> None:
        buf.append(t)

    resp = await _graph(ex)._stream_with_failover(
        CompletionRequest(messages=[Message(role="user", content="x")], model="kimi"), on_token, buf
    )
    assert resp.content == "ok:kimi" and ex.calls == ["kimi", "kimi"]
    assert slept and slept[0] >= 3.0
    assert not cb._provider_cb.is_open(cb.breaker_key(ex, CompletionRequest(messages=[], model="kimi")))
