"""PROV-21: executor streaming honours the per-model circuit; failures hit the right models.

``_stream_with_failover`` wrapped ``stream_tokens`` only in ``asyncio.wait_for``:
it never consulted or fed the per-model provider circuit, and on an all-fail
run only ``_exec_model`` was marked failed, not the models actually attempted.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.graph import AgentGraph
from app.providers.base import CompletionRequest, CompletionResponse, Message
from app.providers.circuit_breaker import ProviderCircuitBreaker, breaker_key


class _Exec:
    def __init__(self, failing: set[str]) -> None:
        self.failing = failing
        self.attempts: list[str] = []

    async def stream_tokens(self, request: Any, on_token: Any) -> CompletionResponse:
        self.attempts.append(request.model)
        if request.model in self.failing:
            raise ConnectionError(f"{request.model} down")
        await on_token("ok")
        return CompletionResponse(content="ok", model=request.model, input_tokens=1, output_tokens=1)


class _Host:
    def __init__(self, executor: _Exec, fallbacks: list[str]) -> None:
        self._executor = executor
        self._fallbacks = fallbacks
        self._logger = SimpleNamespace(warning=lambda *a, **k: None, info=lambda *a, **k: None)
        self.events: list[dict[str, Any]] = []

    def _role_fallback_models(self) -> list[str]:
        return self._fallbacks

    async def _emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


@pytest.fixture
def breaker(monkeypatch: pytest.MonkeyPatch) -> ProviderCircuitBreaker:
    import app.providers.circuit_breaker as cb

    fresh = ProviderCircuitBreaker(failure_threshold=1, recovery_timeout=600)
    monkeypatch.setattr(cb, "_provider_cb", fresh)
    return fresh


def _req(model: str) -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content="x")], model=model)


async def _on_token(chunk: str) -> None:
    return None


async def test_open_circuit_on_primary_skips_to_fallback(breaker: ProviderCircuitBreaker) -> None:
    executor = _Exec(failing=set())
    host = _Host(executor, ["fallback-m"])
    breaker.record_failure(breaker_key(executor, _req("primary-m")))  # opens (threshold 1)

    resp = await AgentGraph._stream_with_failover(host, _req("primary-m"), _on_token, [])  # type: ignore[arg-type]
    assert resp.model == "fallback-m"
    assert executor.attempts == ["fallback-m"]  # primary never called while open


async def test_stream_failure_opens_that_models_circuit(breaker: ProviderCircuitBreaker) -> None:
    executor = _Exec(failing={"primary-m"})
    host = _Host(executor, ["fallback-m"])
    await AgentGraph._stream_with_failover(host, _req("primary-m"), _on_token, [])  # type: ignore[arg-type]
    assert breaker.is_open(breaker_key(executor, _req("primary-m")))
    assert not breaker.is_open(breaker_key(executor, _req("fallback-m")))


async def test_all_fail_marks_each_attempted_model_failed() -> None:
    from app.agent.nodes.executor_mixin import ExecutorMixin

    recorded: list[tuple[str, bool]] = []
    host = SimpleNamespace(
        _failed_models=["primary-m", "fallback-m"],
        _record_provider_health=lambda model, *, ok, start: recorded.append((model, ok)),
    )
    ExecutorMixin._record_stream_failure(host, "primary-m", time.monotonic())  # type: ignore[arg-type]
    assert recorded == [("primary-m", False), ("fallback-m", False)]
