"""Circuit breaker for LLM provider calls."""

from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict
from typing import Any


class ProviderCircuitBreaker:
    """Per-provider circuit breaker to prevent cascading LLM failures."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max: int = 1,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max = half_open_max
        self._failures: dict[str, int] = defaultdict(int)
        self._last_failure: dict[str, float] = {}
        self._state: dict[str, str] = {}  # "closed" | "open" | "half-open"
        self._half_open_calls: dict[str, int] = defaultdict(int)

    def is_open(self, provider_name: str) -> bool:
        """Return True if the circuit is open (provider unavailable)."""
        state = self._state.get(provider_name, "closed")
        if state == "closed":
            return False
        if state == "open":
            elapsed = time.monotonic() - self._last_failure.get(provider_name, 0)
            if elapsed >= self._recovery_timeout:
                self._state[provider_name] = "half-open"
                self._half_open_calls[provider_name] = 0
                return False
            return True
        if state == "half-open":
            return self._half_open_calls[provider_name] >= self._half_open_max
        return False

    def record_success(self, provider_name: str) -> None:
        """Reset failure count and close the circuit."""
        self._failures[provider_name] = 0
        self._state[provider_name] = "closed"
        self._half_open_calls.pop(provider_name, None)

    def record_failure(self, provider_name: str) -> None:
        """Increment failure count; open the circuit when threshold is reached."""
        self._failures[provider_name] += 1
        self._last_failure[provider_name] = time.monotonic()
        state = self._state.get(provider_name, "closed")
        if state == "half-open" or self._failures[provider_name] >= self._failure_threshold:
            self._state[provider_name] = "open"

    def before_call(self, provider_name: str) -> None:
        """Track half-open probe calls."""
        if self._state.get(provider_name) == "half-open":
            self._half_open_calls[provider_name] += 1


# Module-level singleton shared across all graph instances.
_provider_cb = ProviderCircuitBreaker()


async def call_with_circuit_breaker(
    provider: Any,
    method_name: str,
    *args: Any,
    provider_name: str = "llm",
    timeout_seconds: float | None = None,
    **kwargs: Any,
) -> Any:
    """Wrap a provider call with circuit breaker protection.

    Raises ``RuntimeError`` when the circuit is open so callers can fail fast
    without hitting a broken downstream provider.
    """
    if _provider_cb.is_open(provider_name):
        raise RuntimeError(
            f"LLM provider circuit open for {provider_name}. Too many recent failures."
        )

    _provider_cb.before_call(provider_name)
    try:
        timeout = timeout_seconds
        if timeout is None:
            timeout = float(os.getenv("AGENTVERSE_LLM_CALL_TIMEOUT_SECONDS", "60"))
        result = await asyncio.wait_for(
            getattr(provider, method_name)(*args, **kwargs),
            timeout=timeout,
        )
        _provider_cb.record_success(provider_name)
        return result
    except TimeoutError as exc:
        _provider_cb.record_failure(provider_name)
        raise TimeoutError(
            f"LLM provider call timed out for {provider_name} after {timeout}s"
        ) from exc
    except Exception:
        _provider_cb.record_failure(provider_name)
        raise
