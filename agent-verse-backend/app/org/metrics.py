"""PART 22 — Org-level Prometheus Metrics.

Exposes org-level Prometheus counters/gauges/histograms for:
  - Mission success/failure rates per department
  - Agent utilization per role profile
  - Cross-department message volume
  - Approval wait time (bottleneck detection)
  - Model quality score per role profile
  - Knowledge hit rate per collection
  - Cost per department per mission type
  - Blocked task age

These extend the existing OTEL infrastructure (PART 22 observability level 3).
"""
from __future__ import annotations

from typing import Any

import structlog

_log = structlog.get_logger(__name__)

# ── Prometheus metrics (lazy import to avoid hard dependency) ─────────────────

try:
    from prometheus_client import Counter, Gauge, Histogram, REGISTRY

    ORG_MISSION_TOTAL = Counter(
        "agentverse_org_mission_total",
        "Total org missions by outcome and department",
        ["org_id", "department", "outcome"],  # outcome: completed|failed|cancelled
    )
    ORG_MISSION_DURATION_SECONDS = Histogram(
        "agentverse_org_mission_duration_seconds",
        "Mission duration from start to completion",
        ["org_id", "department", "risk_level"],
        buckets=[300, 900, 1800, 3600, 7200, 14400, 28800, 86400],
    )
    ORG_AGENT_ACTIVE = Gauge(
        "agentverse_org_agents_active",
        "Number of currently active agents",
        ["org_id", "department", "status"],
    )
    ORG_APPROVAL_WAIT_SECONDS = Histogram(
        "agentverse_org_approval_wait_seconds",
        "Time waiting for human approval (bottleneck detector)",
        ["org_id", "action_type"],
        buckets=[60, 300, 900, 1800, 3600, 14400, 28800],
    )
    ORG_MODEL_COST_USD = Counter(
        "agentverse_org_model_cost_usd_total",
        "Total LLM cost in USD by org and model profile",
        ["org_id", "model_profile", "department"],
    )
    ORG_MODEL_QUALITY = Histogram(
        "agentverse_org_model_quality_score",
        "Quality scores from gate evaluations",
        ["org_id", "model_profile", "gate"],
        buckets=[0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0],
    )
    ORG_KNOWLEDGE_HITS = Counter(
        "agentverse_org_knowledge_hits_total",
        "Knowledge base search hits and misses",
        ["org_id", "collection_id", "result"],  # result: hit|miss
    )
    ORG_CROSS_DEPT_MESSAGES = Counter(
        "agentverse_org_cross_dept_messages_total",
        "Cross-department message volume",
        ["org_id", "from_dept", "to_dept"],
    )
    ORG_BLOCKED_TASKS = Gauge(
        "agentverse_org_blocked_tasks",
        "Number of currently blocked tasks",
        ["org_id", "department"],
    )
    ORG_HEALTH_SCORE = Gauge(
        "agentverse_org_health_score",
        "Composite org health score (0-100)",
        ["org_id"],
    )
    ORG_BUDGET_USED_PCT = Gauge(
        "agentverse_org_budget_used_pct",
        "Budget utilization percentage (0-1)",
        ["org_id", "department"],
    )
    _METRICS_AVAILABLE = True

except Exception:
    _METRICS_AVAILABLE = False
    _log.warning("prometheus_client not available — org metrics disabled")

    # Stub classes so callers don't need try/except
    class _Stub:
        def labels(self, **kw: Any) -> "_Stub": return self
        def inc(self, amount: float = 1.0) -> None: pass
        def observe(self, amount: float) -> None: pass
        def set(self, value: float) -> None: pass

    ORG_MISSION_TOTAL             = _Stub()  # type: ignore[assignment]
    ORG_MISSION_DURATION_SECONDS  = _Stub()  # type: ignore[assignment]
    ORG_AGENT_ACTIVE              = _Stub()  # type: ignore[assignment]
    ORG_APPROVAL_WAIT_SECONDS     = _Stub()  # type: ignore[assignment]
    ORG_MODEL_COST_USD            = _Stub()  # type: ignore[assignment]
    ORG_MODEL_QUALITY             = _Stub()  # type: ignore[assignment]
    ORG_KNOWLEDGE_HITS            = _Stub()  # type: ignore[assignment]
    ORG_CROSS_DEPT_MESSAGES       = _Stub()  # type: ignore[assignment]
    ORG_BLOCKED_TASKS             = _Stub()  # type: ignore[assignment]
    ORG_HEALTH_SCORE              = _Stub()  # type: ignore[assignment]
    ORG_BUDGET_USED_PCT           = _Stub()  # type: ignore[assignment]


# ── Helper functions ──────────────────────────────────────────────────────────

def record_mission_completed(
    org_id: str, department: str, outcome: str, duration_seconds: float,
    risk_level: str = "medium",
) -> None:
    ORG_MISSION_TOTAL.labels(org_id=org_id, department=department, outcome=outcome).inc()
    ORG_MISSION_DURATION_SECONDS.labels(
        org_id=org_id, department=department, risk_level=risk_level
    ).observe(duration_seconds)


def record_model_cost(org_id: str, model_profile: str, department: str, cost_usd: float) -> None:
    ORG_MODEL_COST_USD.labels(
        org_id=org_id, model_profile=model_profile, department=department
    ).inc(cost_usd)


def record_model_quality(org_id: str, model_profile: str, gate: str, score: float) -> None:
    ORG_MODEL_QUALITY.labels(
        org_id=org_id, model_profile=model_profile, gate=gate
    ).observe(score)


def record_approval_wait(org_id: str, action_type: str, wait_seconds: float) -> None:
    ORG_APPROVAL_WAIT_SECONDS.labels(org_id=org_id, action_type=action_type).observe(wait_seconds)


def record_knowledge_search(org_id: str, collection_id: str, hit: bool) -> None:
    ORG_KNOWLEDGE_HITS.labels(
        org_id=org_id, collection_id=collection_id, result="hit" if hit else "miss"
    ).inc()


def record_cross_dept_message(org_id: str, from_dept: str, to_dept: str) -> None:
    ORG_CROSS_DEPT_MESSAGES.labels(org_id=org_id, from_dept=from_dept, to_dept=to_dept).inc()


def set_blocked_tasks(org_id: str, department: str, count: int) -> None:
    ORG_BLOCKED_TASKS.labels(org_id=org_id, department=department).set(count)


def set_health_score(org_id: str, score: float) -> None:
    ORG_HEALTH_SCORE.labels(org_id=org_id).set(score)


def set_budget_used(org_id: str, department: str, pct: float) -> None:
    ORG_BUDGET_USED_PCT.labels(org_id=org_id, department=department).set(pct)
