"""Prometheus metrics exposition.

Defines the platform's core metrics (registered lazily) and renders the exposition format
for the ``/metrics`` endpoint. Per the monitoring rules: histograms for latency (not
averages), counters for totals, gauges for in-flight state, all namespaced ``agentverse_``.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram
from prometheus_client import generate_latest as _generate_latest

# A dedicated registry keeps test isolation clean and avoids clobbering the global default.
REGISTRY = CollectorRegistry()

GOALS_TOTAL = Counter(
    "agentverse_goals_total",
    "Total goals submitted, labelled by terminal status.",
    labelnames=("status",),
    registry=REGISTRY,
)

TOOL_CALL_DURATION = Histogram(
    "agentverse_tool_call_duration_seconds",
    "Duration of MCP tool calls.",
    labelnames=("tool",),
    registry=REGISTRY,
)


def render_metrics() -> tuple[bytes, str]:
    """Return (body, content_type) for the metrics endpoint."""
    return _generate_latest(REGISTRY), CONTENT_TYPE_LATEST
