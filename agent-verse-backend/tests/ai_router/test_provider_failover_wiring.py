"""D-13: the model-router circuit breaker must actually trip from live provider
outcomes and route selection away from an unhealthy provider.

The executor records each real LLM call outcome via ``_record_provider_health`` →
``router.record_provider_result``. This proves that path opens the circuit and
that ``_with_failover`` then avoids the failed provider (previously the recording
never took effect, so failover never fired).
"""

from __future__ import annotations

import time

from app.agent.nodes.executor_mixin import ExecutorMixin
from app.ai_router.model_orchestrator import ModelOrchestrator, ModelOrchestratorAdapter


class _Exec(ExecutorMixin):
    """Minimal holder exposing just the router attribute the helper reads."""

    def __init__(self, router: object) -> None:
        self._model_router = router


def test_adapter_records_failures_and_opens_circuit() -> None:
    orch = ModelOrchestrator()
    adapter = ModelOrchestratorAdapter(orchestrator=orch)

    # Baseline: openai is healthy, so gpt-4o is selected as-is.
    assert orch._with_failover("gpt-4o") == "gpt-4o"

    # Five failures (0.1 error-rate each) trip the breaker at 0.5.
    for _ in range(5):
        adapter.record_provider_result("gpt-4o", False, 100.0)

    assert orch._provider_open("openai") is True
    routed = orch._with_failover("gpt-4o")
    assert routed != "gpt-4o", "failover must route away from the open provider"
    assert orch.provider_for_model(routed) != "openai"


def test_success_recovers_the_circuit() -> None:
    orch = ModelOrchestrator()
    adapter = ModelOrchestratorAdapter(orchestrator=orch)
    for _ in range(5):
        adapter.record_provider_result("gpt-4o", False, 100.0)
    assert orch._provider_open("openai") is True

    # Enough successes pull error-rate below the recovery threshold (<0.2).
    for _ in range(10):
        adapter.record_provider_result("gpt-4o", True, 50.0)
    assert orch._provider_open("openai") is False


def test_live_executor_helper_opens_circuit() -> None:
    """The real executor call site (_record_provider_health) — positional args to
    the adapter — must actually record (previously a signature mismatch made every
    call raise and get swallowed)."""
    orch = ModelOrchestrator()
    ex = _Exec(ModelOrchestratorAdapter(orchestrator=orch))

    start = time.monotonic() - 0.1
    for _ in range(5):
        ex._record_provider_health("gpt-4o", ok=False, start=start)

    assert orch._provider_open("openai") is True
    assert orch._with_failover("gpt-4o") != "gpt-4o"


def test_helper_is_noop_without_router() -> None:
    ex = _Exec(None)
    # Must not raise when no router is wired.
    ex._record_provider_health("gpt-4o", ok=False, start=time.monotonic())
