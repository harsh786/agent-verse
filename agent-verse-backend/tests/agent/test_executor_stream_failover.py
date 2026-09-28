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
