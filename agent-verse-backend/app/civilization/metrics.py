"""Prometheus metrics for civilization operations.

Registered lazily to avoid import-time metric conflicts with the main
REGISTRY in app.observability.metrics (which pre-registers platform counters
at import time). Civilization metrics use their own lazy registry.
"""
from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Metric registry — lazy-initialized on first access
_metrics: dict[str, Any] = {}


def _get(
    name: str,
    metric_type: str,
    description: str,
    labels: list[str] | None = None,
) -> Any:
    """Get or create a Prometheus metric lazily.

    Falls back to a no-op _NullMetric when prometheus_client is unavailable
    or when the metric has already been registered under the same name
    (common in test suites that import the module multiple times).
    """
    if name not in _metrics:
        try:
            import prometheus_client as prom  # type: ignore[import]

            label_names = labels or []
            if metric_type == "Counter":
                _metrics[name] = prom.Counter(name, description, label_names)
            elif metric_type == "Gauge":
                _metrics[name] = prom.Gauge(name, description, label_names)
            elif metric_type == "Histogram":
                _metrics[name] = prom.Histogram(name, description, label_names)
            else:
                _metrics[name] = _NullMetric()
        except Exception as exc:
            logger.debug("civilization_metric_init_failed", name=name, error=str(exc))
            _metrics[name] = _NullMetric()
    return _metrics[name]


class _NullMetric:
    """No-op metric — used when prometheus_client is unavailable or metric creation fails."""

    def labels(self, **_: Any) -> _NullMetric:
        return self

    def inc(self, amount: float = 1) -> None:
        pass

    def dec(self, amount: float = 1) -> None:
        pass

    def set(self, value: float) -> None:
        pass

    def observe(self, value: float) -> None:
        pass


# ── Metric accessors ──────────────────────────────────────────────────────────

def civ_agents_active() -> Any:
    """Gauge: number of active agents in all civilizations."""
    return _get(
        "civ_agents_active",
        "Gauge",
        "Number of active agents in all civilizations",
        ["tenant_id", "civilization_id"],
    )


def civ_spawns_total() -> Any:
    """Counter: total spawn requests by decision."""
    return _get(
        "civ_spawns_total",
        "Counter",
        "Total spawn requests",
        ["tenant_id", "decision"],
    )


def civ_spawn_denied_total() -> Any:
    """Counter: total denied spawn requests by reason category."""
    return _get(
        "civ_spawn_denied_total",
        "Counter",
        "Total denied spawn requests",
        ["tenant_id", "reason_category"],
    )


def civ_budget_spent_usd() -> Any:
    """Gauge: total USD spent by civilization."""
    return _get(
        "civ_budget_spent_usd",
        "Gauge",
        "Total USD spent by civilization",
        ["tenant_id", "civilization_id"],
    )


def civ_debates_total() -> Any:
    """Counter: total debates conducted."""
    return _get(
        "civ_debates_total",
        "Counter",
        "Total debates conducted",
        ["tenant_id"],
    )


def civ_learnings_promoted_total() -> Any:
    """Counter: total learnings promoted to LTM."""
    return _get(
        "civ_learnings_promoted_total",
        "Counter",
        "Total learnings promoted to LTM",
        ["tenant_id"],
    )


def civ_learnings_rejected_total() -> Any:
    """Counter: total learnings rejected."""
    return _get(
        "civ_learnings_rejected_total",
        "Counter",
        "Total learnings rejected",
        ["tenant_id"],
    )


# ── Recording helpers ─────────────────────────────────────────────────────────

def record_spawn(
    *,
    tenant_id: str,
    civilization_id: str,
    decision: str,
) -> None:
    """Record a spawn attempt (approved or denied)."""
    try:
        civ_spawns_total().labels(tenant_id=tenant_id, decision=decision).inc()
        if decision == "denied":
            civ_spawn_denied_total().labels(
                tenant_id=tenant_id, reason_category="policy"
            ).inc()
    except Exception:
        pass


def record_agents_active(
    *,
    tenant_id: str,
    civilization_id: str,
    count: int,
) -> None:
    """Update active agent gauge for a civilization."""
    try:
        civ_agents_active().labels(
            tenant_id=tenant_id, civilization_id=civilization_id
        ).set(count)
    except Exception:
        pass


def record_budget_spent(
    *,
    tenant_id: str,
    civilization_id: str,
    amount_usd: float,
) -> None:
    """Update budget-spent gauge for a civilization."""
    try:
        civ_budget_spent_usd().labels(
            tenant_id=tenant_id, civilization_id=civilization_id
        ).set(amount_usd)
    except Exception:
        pass


def record_debate(*, tenant_id: str) -> None:
    """Increment the debate counter."""
    try:
        civ_debates_total().labels(tenant_id=tenant_id).inc()
    except Exception:
        pass


def record_learning_outcome(*, tenant_id: str, outcome: str) -> None:
    """Record learning pipeline outcome (promoted | rejected | validated)."""
    try:
        if outcome == "promoted":
            civ_learnings_promoted_total().labels(tenant_id=tenant_id).inc()
        elif outcome == "rejected":
            civ_learnings_rejected_total().labels(tenant_id=tenant_id).inc()
        # "validated" (above rejection floor but below promotion floor) — not tracked
        # separately to keep cardinality low; it will show up in the absence of
        # promoted/rejected increments.
    except Exception:
        pass
