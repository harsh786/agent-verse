"""Prometheus metrics exposition.

Defines the platform's core metrics (registered lazily) and renders the exposition format
for the ``/metrics`` endpoint. Per the monitoring rules: histograms for latency (not
averages), counters for totals, gauges for in-flight state, all namespaced ``agentverse_``.
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client import generate_latest as _generate_latest

# A dedicated registry keeps test isolation clean and avoids clobbering the global default.
REGISTRY = CollectorRegistry()
_LABEL_CATEGORIES = frozenset(
    {"jira", "rpa", "confluence", "email", "policy", "rag", "unknown"}
)
_QUEUE_LABELS = frozenset({"goals", "schedules", "maintenance", "default", "unknown"})
_PROVIDER_LABELS = frozenset(
    {"openai", "azure_openai", "anthropic", "google", "local", "unknown"}
)
_PROVIDER_LABEL_ALIASES = {
    "azure": "azure_openai",
    "azure-openai": "azure_openai",
    "azure openai": "azure_openai",
    "ollama": "local",
}
_MODEL_HINTS = (
    ("gpt-4o-mini", frozenset({"gpt-4o-mini"})),
    ("gpt-4o", frozenset({"gpt-4o"})),
    ("gpt-5-mini", frozenset({"gpt-5-mini"})),
    ("gpt-5", frozenset({"gpt-5"})),
    ("claude-sonnet", frozenset({"claude", "sonnet"})),
    ("claude-haiku", frozenset({"claude", "haiku"})),
    ("gemini", frozenset({"gemini"})),
    ("embedding", frozenset({"embedding", "embed"})),
    ("local", frozenset({"local", "ollama"})),
)
_TOKEN_TYPE_LABELS = frozenset(
    {"prompt", "completion", "cached", "reasoning", "total", "unknown"}
)
_TOKEN_TYPE_LABEL_ALIASES = {
    "input": "prompt",
    "output": "completion",
    "cache": "cached",
}
_COST_SCOPE_LABELS = frozenset({"goal", "tool", "llm", "workflow", "queue", "unknown"})
_COST_SCOPE_LABEL_ALIASES = {
    "tool_call": "tool",
    "tool-call": "tool",
    "model": "llm",
}
_PRIORITY_LABELS = frozenset({"low", "normal", "high", "urgent"})
_PRIORITY_LABEL_ALIASES = {
    "background": "low",
    "critical": "urgent",
}
_STATUS_LABELS = frozenset(
    {
        "started",
        "complete",
        "failed",
        "cancelled",
        "denied",
        "approval_required",
        "cache_hit",
        "circuit_open",
        "success",
        "error",
        "unknown",
    }
)
_STATUS_LABEL_ALIASES = {
    "approval": "approval_required",
    "auth_failed": "error",
    "bad_response": "error",
    "completed": "complete",
    "executing": "started",
    "healthy": "success",
    "pending_approval": "approval_required",
    "planning": "started",
    "running": "started",
    "unreachable": "error",
    "verifying": "started",
    "waiting_human": "approval_required",
}
_TOOL_HINTS = (
    ("jira", frozenset({"atlassian", "issue", "ticket", "jql", "jira"})),
    ("rpa", frozenset({"browser", "playwright", "rpa", "robot", "ui"})),
    ("confluence", frozenset({"confluence", "page", "space", "wiki"})),
    ("email", frozenset({"email", "gmail", "mail", "outlook", "smtp"})),
    ("rag", frozenset({"rag", "vector", "embedding", "retrieval"})),
)

GOAL_DURATION = Histogram(
    "agentverse_goal_duration_seconds",
    "Goal execution duration, labelled only by bounded dimensions.",
    labelnames=("status", "priority"),
    registry=REGISTRY,
)

GOAL_TOTAL = Counter(
    "agentverse_goal_total",
    "Total goals submitted, labelled by bounded dimensions.",
    labelnames=("status", "priority"),
    registry=REGISTRY,
)

TOOL_CALL_TOTAL = Counter(
    "agentverse_tool_call_total",
    "Total structured tool calls by bounded tool, connector, and status labels.",
    labelnames=("tool", "connector", "status"),
    registry=REGISTRY,
)

TOOL_CALL_DURATION = Histogram(
    "agentverse_tool_call_duration_seconds",
    "Duration of structured tool calls by bounded tool, connector, and status labels.",
    labelnames=("tool", "connector", "status"),
    registry=REGISTRY,
)

QUEUE_DEPTH = Gauge(
    "agentverse_queue_depth",
    "Queue depth by bounded queue label.",
    labelnames=("queue",),
    registry=REGISTRY,
)

LLM_TOKENS_TOTAL = Counter(
    "agentverse_llm_tokens_total",
    "LLM tokens consumed by bounded provider, model, and token type labels.",
    labelnames=("provider", "model", "type"),
    registry=REGISTRY,
)

COST_USD_TOTAL = Counter(
    "agentverse_cost_usd_total",
    "Estimated platform cost in USD by bounded scope label.",
    labelnames=("scope",),
    registry=REGISTRY,
)

APPROVAL_WAIT = Histogram(
    "agentverse_approval_wait_seconds",
    "Duration spent waiting for human approval.",
    registry=REGISTRY,
)

PLAN_DURATION = Histogram(
    "agentverse_plan_duration_seconds",
    "Planning phase duration per iteration",
    labelnames=("iteration",),
    registry=REGISTRY,
)

VERIFY_DURATION = Histogram(
    "agentverse_verify_duration_seconds",
    "Verification phase duration per iteration",
    registry=REGISTRY,
)

QUEUE_WAIT_DURATION = Histogram(
    "agentverse_queue_wait_seconds",
    "Time from goal submission to execution start",
    labelnames=("priority",),
    registry=REGISTRY,
)

SCHEDULE_FIRE_TOTAL = Counter(
    "agentverse_schedule_fire_total",
    "Total schedule fire attempts by bounded status label.",
    labelnames=("status",),
    registry=REGISTRY,
)


def render_metrics() -> tuple[bytes, str]:
    """Return (body, content_type) for the metrics endpoint."""
    return _generate_latest(REGISTRY), CONTENT_TYPE_LATEST


# ── Recording helpers ─────────────────────────────────────────────────────────

@asynccontextmanager
async def track_tool_call(
    tool_name: str, tenant_id: str | None = None, connector_name: str = "llm"
) -> AsyncGenerator[None, None]:
    """Async context manager that records tool-call metrics.

    ``tenant_id`` is accepted for compatibility with older call sites but is
    intentionally not used as a metric label to avoid high-cardinality series.
    """
    _ = tenant_id
    start = time.monotonic()
    try:
        yield
    except Exception:
        record_tool_call(tool_name, connector_name, "failed", time.monotonic() - start)
        raise
    record_tool_call(tool_name, connector_name, "success", time.monotonic() - start)


def _non_negative(seconds: float) -> float:
    return max(0.0, seconds)


def _metric_label_bucket(*values: str) -> str:
    text = " ".join(values).lower()
    for category in _LABEL_CATEGORIES:
        if category != "unknown" and category in text:
            return category
    for category, hints in _TOOL_HINTS:
        if any(hint in text for hint in hints):
            return category
    return "unknown"


def _normalize_priority_label(priority: str) -> str:
    normalized = priority.strip().lower()
    normalized = _PRIORITY_LABEL_ALIASES.get(normalized, normalized)
    if normalized in _PRIORITY_LABELS:
        return normalized
    return "unknown"


def _normalize_status_label(value: str) -> str:
    normalized = value.strip().lower()
    normalized = _STATUS_LABEL_ALIASES.get(normalized, normalized)
    if normalized in _STATUS_LABELS:
        return normalized
    return "unknown"


def _normalize_exact_label(
    value: str,
    allowed: frozenset[str],
    aliases: dict[str, str] | None = None,
) -> str:
    normalized = value.strip().lower()
    if aliases is not None:
        normalized = aliases.get(normalized, normalized)
    if normalized in allowed:
        return normalized
    return "unknown"


def _normalize_model_label(model: str) -> str:
    normalized = model.strip().lower()
    for label, hints in _MODEL_HINTS:
        if all(hint in normalized for hint in hints):
            return label
    return "unknown"


def record_goal_duration(
    status: str, duration_seconds: float, priority: str = "normal"
) -> None:
    """Record terminal goal count and duration without tenant labels."""
    priority_label = _normalize_priority_label(priority)
    status_label = _normalize_status_label(status)
    GOAL_TOTAL.labels(status=status_label, priority=priority_label).inc()
    GOAL_DURATION.labels(status=status_label, priority=priority_label).observe(
        _non_negative(duration_seconds)
    )


def record_tool_call(
    tool_name: str, connector_name: str, status: str, duration_seconds: float
) -> None:
    """Record structured tool-call count and duration without tenant labels."""
    tool_label = _metric_label_bucket(tool_name, connector_name)
    connector_label = _metric_label_bucket(connector_name)
    status_label = _normalize_status_label(status)
    TOOL_CALL_TOTAL.labels(
        tool=tool_label,
        connector=connector_label,
        status=status_label,
    ).inc()
    TOOL_CALL_DURATION.labels(
        tool=tool_label, connector=connector_label, status=status_label
    ).observe(_non_negative(duration_seconds))


def record_queue_depth(queue: str, depth: float) -> None:
    """Record current queue depth using a bounded queue label."""
    QUEUE_DEPTH.labels(queue=_normalize_exact_label(queue, _QUEUE_LABELS)).set(
        _non_negative(depth)
    )


def record_llm_tokens(provider: str, model: str, token_type: str, count: int) -> None:
    """Record LLM token usage without exposing raw provider or model strings."""
    LLM_TOKENS_TOTAL.labels(
        provider=_normalize_exact_label(
            provider, _PROVIDER_LABELS, _PROVIDER_LABEL_ALIASES
        ),
        model=_normalize_model_label(model),
        type=_normalize_exact_label(
            token_type, _TOKEN_TYPE_LABELS, _TOKEN_TYPE_LABEL_ALIASES
        ),
    ).inc(_non_negative(float(count)))


def record_cost_usd(scope: str, amount: float) -> None:
    """Record estimated cost without tenant or agent labels."""
    COST_USD_TOTAL.labels(
        scope=_normalize_exact_label(scope, _COST_SCOPE_LABELS, _COST_SCOPE_LABEL_ALIASES)
    ).inc(_non_negative(amount))


def record_approval_wait(seconds: float) -> None:
    """Record how long a request waited for human approval."""
    APPROVAL_WAIT.observe(_non_negative(seconds))


def record_schedule_fire(status: str) -> None:
    """Record a schedule fire attempt using a bounded status label."""
    SCHEDULE_FIRE_TOTAL.labels(status=_normalize_status_label(status)).inc()


def record_goal_started(tenant_id: str, priority: str = "normal") -> None:
    """Increment the goals counter for a newly-started goal."""
    GOAL_TOTAL.labels(
        status=_normalize_status_label("started"),
        priority=_normalize_priority_label(priority),
    ).inc()


def record_goal_completed(tenant_id: str, priority: str = "normal") -> None:
    """Increment the goals counter when a goal completes successfully."""
    GOAL_TOTAL.labels(
        status=_normalize_status_label("complete"),
        priority=_normalize_priority_label(priority),
    ).inc()


def record_goal_failed(tenant_id: str, priority: str = "normal") -> None:
    """Increment the goals counter when a goal fails."""
    GOAL_TOTAL.labels(
        status=_normalize_status_label("failed"),
        priority=_normalize_priority_label(priority),
    ).inc()


def record_plan_duration(iteration: int, seconds: float) -> None:
    PLAN_DURATION.labels(iteration=str(min(iteration, 15))).observe(max(0.0, seconds))


def record_verify_duration(seconds: float) -> None:
    VERIFY_DURATION.observe(max(0.0, seconds))


def record_queue_wait(priority: str, seconds: float) -> None:
    QUEUE_WAIT_DURATION.labels(
        priority=_normalize_priority_label(priority)
    ).observe(max(0.0, seconds))
