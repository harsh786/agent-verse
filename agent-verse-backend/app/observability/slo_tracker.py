"""SLO Burn-Rate Tracker — track error budgets and burn rates per tenant.

An SLO (Service Level Objective) defines a target reliability (e.g. 99.9%
success rate over a 24-hour window). The burn rate measures how fast the
error budget is being consumed relative to the expected pace.

burn_rate = 1.0  → budget depleting exactly at the allowed pace
burn_rate > 1.0  → budget will be exhausted before the window ends
"""

from __future__ import annotations

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
    """In-memory SLO burn-rate tracker.

    Uses a simple in-memory time-series of events (tuples of timestamp+success).
    For production use, replace the in-memory store with Redis sorted sets.
    """

    def __init__(self) -> None:
        # {(tenant_id, slo_name): [(timestamp, success_bool), ...]}
        self._events: dict[tuple[str, str], list[tuple[float, bool]]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_event(
        self,
        success: bool,
        slo: SLODefinition,
    ) -> None:
        """Record a single success or failure event for *slo*."""
        key = (slo.tenant_id, slo.name)
        self._events.setdefault(key, [])
        self._events[key].append((time.time(), success))
        # Prune events older than the SLO window
        cutoff = time.time() - slo.window_hours * 3600
        self._events[key] = [e for e in self._events[key] if e[0] >= cutoff]

    def burn_rate(self, slo: SLODefinition) -> SLOStatus:
        """Compute the current SLO status and burn rate for *slo*."""
        key = (slo.tenant_id, slo.name)
        events = self._events.get(key, [])
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
        for (tid, slo_name), events in self._events.items():
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
