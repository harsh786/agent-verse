"""SLO Burn-Rate Tracker — track error budgets and burn rates per tenant.

An SLO (Service Level Objective) defines a target reliability (e.g. 99.9%
success rate over a 24-hour window). The burn rate measures how fast the
error budget is being consumed relative to the expected pace.

burn_rate = 1.0  → budget depleting exactly at the allowed pace
burn_rate > 1.0  → budget will be exhausted before the window ends
"""

from __future__ import annotations

import contextlib
import json
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class SLODefinition:
    name: str
    target: float  # e.g. 0.999 for 99.9% success
    window_hours: int  # observation window
    tenant_id: str = ""


@dataclass
class SLOStatus:
    slo_name: str
    target: float
    current_success_rate: float
    error_budget_remaining: float  # fraction of budget left (1.0 = full, 0.0 = exhausted)
    burn_rate_multiple: float  # 1.0 = on pace, >1.0 = burning faster than budget
    minutes_to_exhaustion: float  # ∞ if burn_rate <= 1.0
    window_hours: int
    total_events: int
    successful_events: int

    @property
    def is_breaching(self) -> bool:
        return self.burn_rate_multiple > 1.0 or self.current_success_rate < self.target


class SLOTracker:
    """SLO burn-rate tracker with optional Redis-backed persistence.

    Uses a simple time-series of events (tuples of timestamp+success). By default the
    series lives purely in-memory and therefore resets on process restart.

    Pass a *redis* client (any object exposing synchronous ``get(key)`` / ``set(key, value)``
    — e.g. ``redis.Redis``) to persist the series so burn-rate / error-budget state survives
    a restart: every mutation writes the full state as a JSON blob, and reads load it back,
    so a fresh ``SLOTracker`` bound to the same backend sees the same events. When *redis* is
    absent (or unreachable) the tracker degrades gracefully to the in-memory series.

    NOTE: the JSON-blob-per-mutation scheme is a persistence stopgap; a high-throughput
    deployment should move to Redis sorted sets keyed per ``(tenant, slo)``.
    """

    _STATE_SEP = "\x1f"  # unit separator — safe delimiter for (tenant, slo) redis members

    def __init__(self, redis: Any | None = None, *, key_prefix: str = "slo") -> None:
        # {(tenant_id, slo_name): [(timestamp, success_bool), ...]}
        self._events: dict[tuple[str, str], list[tuple[float, bool]]] = {}
        self._redis = redis
        self._state_key = f"{key_prefix}:state"

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load_state(self) -> dict[tuple[str, str], list[tuple[float, bool]]]:
        """Return the authoritative event map (Redis when configured, else in-memory)."""
        if self._redis is None:
            return self._events
        try:
            raw = self._redis.get(self._state_key)
        except Exception:
            # Backend unreachable — fall back to whatever we have locally.
            return self._events
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        state: dict[tuple[str, str], list[tuple[float, bool]]] = {}
        for member, events in data.items():
            tenant, _, slo_name = member.partition(self._STATE_SEP)
            state[(tenant, slo_name)] = [(float(ts), bool(ok)) for ts, ok in events]
        return state

    def _persist_state(self, state: dict[tuple[str, str], list[tuple[float, bool]]]) -> None:
        # Always keep the in-memory copy current so a broken backend still tracks locally.
        self._events = state
        if self._redis is None:
            return
        payload = {
            f"{tenant}{self._STATE_SEP}{slo_name}": [[ts, ok] for ts, ok in events]
            for (tenant, slo_name), events in state.items()
        }
        # Persistence is best-effort; never let a Redis hiccup break event recording.
        with contextlib.suppress(Exception):
            self._redis.set(self._state_key, json.dumps(payload))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_event(
        self,
        success: bool,
        slo: SLODefinition,
    ) -> None:
        """Record a single success or failure event for *slo*."""
        state = self._load_state()
        key = (slo.tenant_id, slo.name)
        events = state.setdefault(key, [])
        events.append((time.time(), success))
        # Prune events older than the SLO window
        cutoff = time.time() - slo.window_hours * 3600
        state[key] = [e for e in events if e[0] >= cutoff]
        self._persist_state(state)

    def burn_rate(self, slo: SLODefinition) -> SLOStatus:
        """Compute the current SLO status and burn rate for *slo*."""
        key = (slo.tenant_id, slo.name)
        events = self._load_state().get(key, [])
        cutoff = time.time() - slo.window_hours * 3600
        window_events = [(ts, ok) for ts, ok in events if ts >= cutoff]

        total = len(window_events)
        successes = sum(1 for _, ok in window_events if ok)
        failures = total - successes

        current_rate = 1.0 if total == 0 else successes / total
        error_budget = 1.0 - slo.target  # allowed failure fraction
        actual_error_rate = failures / max(total, 1)

        if error_budget <= 0:
            burn_rate_multiple = float("inf") if actual_error_rate > 0 else 1.0
        else:
            burn_rate_multiple = actual_error_rate / error_budget

        budget_remaining = max(0.0, 1.0 - (actual_error_rate / max(error_budget, 1e-9)))

        if burn_rate_multiple > 1.0 and total > 0:
            # Minutes until error budget is exhausted at current burn rate
            window_minutes = slo.window_hours * 60
            budget_consumed = actual_error_rate / error_budget if error_budget > 0 else 1.0
            minutes_to_exhaustion = (
                window_minutes * (1.0 - budget_consumed) / max(burn_rate_multiple - 1.0, 1e-6)
            )
        else:
            minutes_to_exhaustion = float("inf")

        return SLOStatus(
            slo_name=slo.name,
            target=slo.target,
            current_success_rate=round(current_rate, 5),
            error_budget_remaining=round(budget_remaining, 4),
            burn_rate_multiple=round(burn_rate_multiple, 3),
            minutes_to_exhaustion=round(minutes_to_exhaustion, 1),
            window_hours=slo.window_hours,
            total_events=total,
            successful_events=successes,
        )

    def summary(self, tenant_id: str = "") -> list[dict[str, Any]]:
        """Return a summary of all tracked SLOs for *tenant_id*."""
        results = []
        for (tid, slo_name), events in self._load_state().items():
            if tenant_id and tid != tenant_id:
                continue
            total = len(events)
            successes = sum(1 for _, ok in events if ok)
            results.append(
                {
                    "tenant_id": tid,
                    "slo_name": slo_name,
                    "total_events": total,
                    "success_rate": round(successes / max(total, 1), 5),
                }
            )
        return results


# Global singleton for the platform
platform_slo_tracker = SLOTracker()
