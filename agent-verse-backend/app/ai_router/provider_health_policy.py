"""ProviderHealthPolicy — tracks per-provider health and circuit-breaker state."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProviderHealthStatus:
    provider: str
    healthy: bool = True
    circuit_open: bool = False
    error_rate: float = 0.0
    avg_latency_ms: float = 500.0


class ProviderHealthPolicy:
    def __init__(self) -> None:
        self._status: dict[str, ProviderHealthStatus] = {}

    def check(self, provider: str) -> ProviderHealthStatus:
        return self._status.get(provider, ProviderHealthStatus(provider=provider))

    def record_failure(self, provider: str) -> None:
        s = self._status.setdefault(provider, ProviderHealthStatus(provider=provider))
        s.error_rate = min(1.0, s.error_rate + 0.1)
        if s.error_rate >= 0.5:
            s.circuit_open = True
            s.healthy = False

    def record_success(self, provider: str, latency_ms: float) -> None:
        s = self._status.setdefault(provider, ProviderHealthStatus(provider=provider))
        s.error_rate = max(0.0, s.error_rate - 0.05)
        s.avg_latency_ms = 0.9 * s.avg_latency_ms + 0.1 * latency_ms
        if s.error_rate < 0.2:
            s.circuit_open = False
            s.healthy = True
