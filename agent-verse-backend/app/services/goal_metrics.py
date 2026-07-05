"""Goal execution metrics collection.

Extracted from GoalService. Handles cost tracking, duration recording,
terminal metric collection, and Prometheus counters.
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def record_goal_completion_metrics(
    goal_id: str,
    tenant_id: str,
    status: str,
    duration_s: float,
    cost_usd: float = 0.0,
    iterations: int = 0,
) -> None:
    """Record goal completion metrics to Prometheus and structured logs.

    This function consolidates the metric recording that was scattered
    across _record_terminal_goal_metrics and _dispatch_event in GoalService.
    """
    try:
        from app.observability.metrics import (
            record_goal_completed,
            record_goal_duration,
            record_goal_failed,
        )

        record_goal_duration(
            status=status,
            duration_seconds=duration_s,
            priority="normal",
        )
        if status == "complete":
            record_goal_completed(tenant_id=tenant_id)
        elif status in ("failed", "cancelled"):
            record_goal_failed(tenant_id=tenant_id)
    except Exception as exc:
        _log.debug("Failed to record goal metrics: %s", exc)
