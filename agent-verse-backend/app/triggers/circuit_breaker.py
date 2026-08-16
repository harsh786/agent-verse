"""Per-trigger circuit breaker state machine.

States: closed → open → half_open → closed
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

_log = logging.getLogger(__name__)


@dataclass
class TriggerCircuitBreaker:
    trigger_id:            str
    state:                 str = "closed"      # closed | open | half_open
    failure_count:         int = 0
    success_count:         int = 0
    failure_threshold:     int = 5             # consecutive failures → open
    success_threshold:     int = 2             # successes in half_open → closed
    open_duration_seconds: int = 60            # probe after this delay
    last_failure_at:       float | None = None
    last_state_change_at:  float = field(default_factory=time.time)

    def is_open(self) -> bool:
        """Return True if the circuit is open (blocking)."""
        if self.state == "closed":
            return False
        if self.state == "open":
            # Check if we should transition to half_open for a probe
            if (time.time() - (self.last_failure_at or 0)) >= self.open_duration_seconds:
                self.state = "half_open"
                self.success_count = 0
                self.last_state_change_at = time.time()
                _log.info("circuit_half_open trigger_id=%s", self.trigger_id)
                return False  # allow the probe
            return True
        # half_open: allow probe
        return False

    def record_success(self) -> None:
        if self.state == "half_open":
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self.state = "closed"
                self.failure_count = 0
                self.last_state_change_at = time.time()
                _log.info("circuit_closed trigger_id=%s", self.trigger_id)
        elif self.state == "closed":
            self.failure_count = 0

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_at = time.time()
        if self.state == "half_open":
            self.state = "open"
            self.last_state_change_at = time.time()
            _log.warning("circuit_open trigger_id=%s (half_open probe failed)", self.trigger_id)
        elif self.state == "closed" and self.failure_count >= self.failure_threshold:
            self.state = "open"
            self.last_state_change_at = time.time()
            _log.warning(
                "circuit_open trigger_id=%s failures=%d",
                self.trigger_id,
                self.failure_count,
            )

    def prometheus_state_value(self) -> int:
        """0=closed, 1=half_open, 2=open — for Prometheus gauge."""
        return {"closed": 0, "half_open": 1, "open": 2}.get(self.state, 0)


class CircuitBreakerRegistry:
    """In-process registry of per-trigger circuit breakers."""

    def __init__(self) -> None:
        self._breakers: dict[str, TriggerCircuitBreaker] = {}

    def get(self, trigger_id: str) -> TriggerCircuitBreaker:
        if trigger_id not in self._breakers:
            self._breakers[trigger_id] = TriggerCircuitBreaker(trigger_id=trigger_id)
        return self._breakers[trigger_id]
