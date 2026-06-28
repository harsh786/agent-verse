"""Circuit breaker — protects against cascading failures in tool calls.

States:
  CLOSED   → normal operation; failures are counted
  OPEN     → all calls blocked; opened after failure_threshold failures
  HALF_OPEN → one probe call allowed after cooldown; success resets to CLOSED

In production, state is stored in Redis (per-tenant, per-tool) so circuit state
is shared across workers. This in-memory version is used in tests.
"""

from __future__ import annotations

import enum
import time


class CircuitState(enum.StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-tool circuit breaker.

    Args:
        failure_threshold: Number of consecutive failures to open the circuit.
        cooldown_seconds: Seconds to wait before allowing a probe (HALF_OPEN).
    """

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 60.0) -> None:
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._failure_count = 0
        self._state = CircuitState.CLOSED
        self._opened_at: float = 0.0

    @property
    def state(self) -> CircuitState:
        return self._state

    def is_closed(self) -> bool:
        return self._state == CircuitState.CLOSED

    def allows_probe(self) -> bool:
        return self._state == CircuitState.OPEN and (
            time.monotonic() - self._opened_at >= self._cooldown
        )

    def can_call(self) -> bool:
        """Return True if a call is allowed now (handles HALF_OPEN probe window)."""
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.OPEN:
            if self.allows_probe():
                self._state = CircuitState.HALF_OPEN
                return True
            return False
        if self._state == CircuitState.HALF_OPEN:
            return True
        return False

    def record_failure(self) -> None:
        self._failure_count += 1
        if self._failure_count >= self._threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

    def record_success(self) -> None:
        self._failure_count = 0
        self._state = CircuitState.CLOSED
        self._opened_at = 0.0

    # ── Async wrappers — same interface as RedisCircuitBreaker ─────────────────

    async def can_call_async(self) -> bool:
        """Async-compatible wrapper — delegates to the synchronous can_call()."""
        return self.can_call()

    async def record_failure_async(self) -> None:
        """Async-compatible wrapper — delegates to record_failure()."""
        self.record_failure()

    async def record_success_async(self) -> None:
        """Async-compatible wrapper — delegates to record_success()."""
        self.record_success()
