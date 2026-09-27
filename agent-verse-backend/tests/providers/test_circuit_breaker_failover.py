"""Per-model circuits and ordered model failover for LLM role calls.

Found running against real providers: the planner's hosted reasoning model
(kimi-k3 on NVIDIA, ~100 s per call) exceeded the 60 s call timeout and the goal
failed with "Planning unavailable" although a healthy on-prem model was
configured. The circuit key was also the wrapper class name, shared by every
model.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.providers.base import CompletionRequest, CompletionResponse, Message
from app.providers.circuit_breaker import _provider_cb, breaker_key, complete_with_failover


@dataclass
class _Routed:
    """Routes by request.model like the on-prem dispatcher."""

    slow: set[str] = field(default_factory=set)
    broken: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)
    _default_model: str = "primary"

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.calls.append(request.model)
        if request.model in self.slow:
            await asyncio.sleep(5)
        if request.model in self.broken:
            raise ConnectionError("endpoint down")
        return CompletionResponse(content=f"from {request.model}", model=request.model,
                                  input_tokens=1, output_tokens=1)


def _req(model: str) -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content="plan")], model=model)


@pytest.fixture(autouse=True)
def _reset_breakers() -> Any:
    yield
    _provider_cb._state.clear()
    _provider_cb._failures.clear() if hasattr(_provider_cb, "_failures") else None


async def test_slow_primary_fails_over_to_the_next_model() -> None:
    p = _Routed(slow={"kimi"})
    resp = await complete_with_failover(
        p, _req("kimi"), fallback_models=["qwen"], timeout_seconds=0.05
    )
    assert resp.content == "from qwen"
    assert p.calls == ["kimi", "qwen"]


async def test_last_error_is_raised_unchanged_when_all_fail() -> None:
    p = _Routed(broken={"a", "b"})
    with pytest.raises(ConnectionError):
        await complete_with_failover(p, _req("a"), fallback_models=["b", "a", ""])
    assert p.calls == ["a", "b"]  # deduplicated, empties skipped


async def test_circuits_are_per_model() -> None:
    p = _Routed()
    assert breaker_key(p, _req("kimi")) != breaker_key(p, _req("qwen"))
    assert breaker_key(p, _req("")) == "llm:primary"
