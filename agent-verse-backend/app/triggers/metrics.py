"""Prometheus metrics for the trigger framework."""

from __future__ import annotations

try:
    from prometheus_client import Counter, Gauge, Histogram

    TRIGGER_FIRED_TOTAL = Counter(
        "agentverse_trigger_fired_total",
        "Total trigger firings",
        ["trigger_type", "tenant_plan", "result"],
    )

    TRIGGER_FIRE_LATENCY = Histogram(
        "agentverse_trigger_fire_latency_seconds",
        "Time from event receipt to goal enqueue",
        ["trigger_type"],
        buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5],
    )

    TRIGGER_GOAL_CREATED_TOTAL = Counter(
        "agentverse_trigger_goal_created_total",
        "Goals created by triggers",
        ["trigger_type", "tenant_plan"],
    )

    TRIGGER_CIRCUIT_STATE = Gauge(
        "agentverse_trigger_circuit_state",
        "0=closed 1=half_open 2=open",
        ["trigger_id", "tenant_id"],
    )

    TRIGGER_DLQ_DEPTH = Gauge(
        "agentverse_trigger_dlq_depth",
        "DLQ entries",
        ["trigger_type", "tenant_id"],
    )

    TRIGGER_RATE_LIMIT_DROPS_TOTAL = Counter(
        "agentverse_trigger_rate_limit_drops_total",
        "Trigger firings dropped due to rate limiting",
        ["trigger_type", "tenant_id"],
    )

    METRICS_AVAILABLE = True

except ImportError:
    METRICS_AVAILABLE = False

    class _Noop:
        def labels(self, **_: object) -> _Noop:
            return self

        def inc(self, *_: object) -> None: ...
        def observe(self, *_: object) -> None: ...
        def set(self, *_: object) -> None: ...

    TRIGGER_FIRED_TOTAL = _Noop()  # type: ignore[assignment]
    TRIGGER_FIRE_LATENCY = _Noop()  # type: ignore[assignment]
    TRIGGER_GOAL_CREATED_TOTAL = _Noop()  # type: ignore[assignment]
    TRIGGER_CIRCUIT_STATE = _Noop()  # type: ignore[assignment]
    TRIGGER_DLQ_DEPTH = _Noop()  # type: ignore[assignment]
    TRIGGER_RATE_LIMIT_DROPS_TOTAL = _Noop()  # type: ignore[assignment]
