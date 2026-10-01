"""ProviderHealthPolicy — tracks per-provider health and circuit-breaker state.

An open circuit used to close only on ``record_success`` — but a provider whose
circuit is open is never selected, so it stayed open forever. After a cool-down
the circuit is now *half-open*: :meth:`check` lets exactly one probe through;
its success closes the circuit, its failure re-opens it for another cool-down.

State is shared through the Redis ModelRegistryStore when it is wired, so every
API replica and worker agrees on which providers are open (timestamps are wall
clock for that reason); otherwise it is per process.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

_log = logging.getLogger(__name__)
_DEFAULT_COOLDOWN_S = 60.0


@dataclass
class ProviderHealthStatus:
    provider: str
    healthy: bool = True
    circuit_open: bool = False
    error_rate: float = 0.0
    avg_latency_ms: float = 500.0
    opened_at: float = 0.0
    probe_in_flight: bool = False


def _cooldown_from_env() -> float:
    try:
        return float(os.getenv("AGENTVERSE_PROVIDER_CIRCUIT_COOLDOWN_SECONDS", _DEFAULT_COOLDOWN_S))
    except ValueError:
        return _DEFAULT_COOLDOWN_S


class ProviderHealthPolicy:
    def __init__(
        self,
        *,
        cooldown_seconds: float | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._status: dict[str, ProviderHealthStatus] = {}
        self._cooldown = _cooldown_from_env() if cooldown_seconds is None else cooldown_seconds
        self._clock = clock

    # ── state (shared store when wired, else this process) ────────────────────

    @staticmethod
    def _store() -> object | None:
        from app.ai_router.registry_store import get_model_registry_store

        return get_model_registry_store()

    def _load(self, provider: str) -> ProviderHealthStatus:
        store = self._store()
        if store is not None:
            try:
                data = store.get_provider_circuit(provider)  # type: ignore[attr-defined]
            except Exception as exc:  # keep routing on the local view
                _log.warning("provider_circuit_store_read_failed: %s", str(exc)[:160])
            else:
                if data:
                    fields = {f.name for f in dataclasses.fields(ProviderHealthStatus)}
                    status = ProviderHealthStatus(
                        **{k: v for k, v in data.items() if k in fields}
                    )
                    self._status[provider] = status
                    return status
        return self._status.setdefault(provider, ProviderHealthStatus(provider=provider))

    def _save(self, status: ProviderHealthStatus) -> None:
        self._status[status.provider] = status
        store = self._store()
        if store is not None:
            try:
                store.set_provider_circuit(  # type: ignore[attr-defined]
                    status.provider, dataclasses.asdict(status)
                )
            except Exception as exc:
                _log.warning("provider_circuit_store_write_failed: %s", str(exc)[:160])

    # ── policy ─────────────────────────────────────────────────────────────────

    def check(self, provider: str) -> ProviderHealthStatus:
        s = self._load(provider)
        if (
            s.circuit_open
            and not s.probe_in_flight
            and self._clock() - s.opened_at >= self._cooldown
        ):
            # Half-open: let exactly one attempt through to test the provider.
            s.probe_in_flight = True
            self._save(s)
            return dataclasses.replace(s, circuit_open=False)
        return dataclasses.replace(s)

    def record_failure(self, provider: str) -> None:
        s = self._load(provider)
        s.error_rate = min(1.0, s.error_rate + 0.1)
        if s.probe_in_flight or s.error_rate >= 0.5:
            # A failed half-open probe re-opens for a fresh cool-down.
            s.circuit_open = True
            s.healthy = False
            s.opened_at = self._clock()
            s.probe_in_flight = False
        self._save(s)

    def record_success(self, provider: str, latency_ms: float) -> None:
        s = self._load(provider)
        s.avg_latency_ms = 0.9 * s.avg_latency_ms + 0.1 * latency_ms
        if s.probe_in_flight:
            # The half-open probe succeeded: close the circuit.
            s.error_rate = min(s.error_rate, 0.1)
            s.probe_in_flight = False
        else:
            s.error_rate = max(0.0, s.error_rate - 0.05)
        if s.error_rate < 0.2:
            s.circuit_open = False
            s.healthy = True
        self._save(s)
