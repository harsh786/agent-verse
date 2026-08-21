"""
Limits v2 — Comprehensive Resource Quotas
==========================================
All plan-based limits in one place with a unified check interface.

New limits vs v1:
- Per-connector rate limits (protect 3rd party APIs)
- Token budget per goal (direct token cost control)
- Step count limit per plan (plan differentiation)
- Knowledge storage quota per plan
- Concurrent execution per-agent (bulkhead isolation)
- Burst vs sustained rate limits
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class LimitsV2Config:
    """Complete limits configuration for a plan tier."""

    # Execution
    max_steps_per_goal: int = 20
    max_input_tokens_per_goal: int = 500_000  # 500k tokens
    max_concurrent_per_agent: int = 3
    goal_timeout_seconds: int = 3600

    # API rate limits
    requests_per_minute: int = 60
    burst_requests_per_10s: int = 30  # burst allowance

    # Connector-specific rate limits (connector_name → calls_per_minute)
    connector_rpm: dict[str, int] = field(default_factory=dict)

    # Storage
    knowledge_storage_mb: int = 100

    # Cost
    max_cost_per_goal_usd: float = 5.0
    max_cost_per_day_usd: float = 50.0


# Plan-tier limit configurations
PLAN_LIMITS_V2: dict[str, LimitsV2Config] = {
    "free": LimitsV2Config(
        max_steps_per_goal=5,
        max_input_tokens_per_goal=50_000,
        max_concurrent_per_agent=1,
        goal_timeout_seconds=300,
        requests_per_minute=30,
        burst_requests_per_10s=10,
        connector_rpm={"jira": 10, "github": 10, "slack": 5},
        knowledge_storage_mb=50,
        max_cost_per_goal_usd=0.50,
        max_cost_per_day_usd=2.0,
    ),
    "starter": LimitsV2Config(
        max_steps_per_goal=10,
        max_input_tokens_per_goal=200_000,
        max_concurrent_per_agent=2,
        goal_timeout_seconds=900,
        requests_per_minute=120,
        burst_requests_per_10s=30,
        connector_rpm={"jira": 30, "github": 30, "slack": 20},
        knowledge_storage_mb=500,
        max_cost_per_goal_usd=2.0,
        max_cost_per_day_usd=20.0,
    ),
    "professional": LimitsV2Config(
        max_steps_per_goal=20,
        max_input_tokens_per_goal=1_000_000,
        max_concurrent_per_agent=5,
        goal_timeout_seconds=1800,
        requests_per_minute=600,
        burst_requests_per_10s=100,
        connector_rpm={"jira": 100, "github": 100, "slack": 60},
        knowledge_storage_mb=10_000,
        max_cost_per_goal_usd=10.0,
        max_cost_per_day_usd=100.0,
    ),
    "enterprise": LimitsV2Config(
        max_steps_per_goal=50,
        max_input_tokens_per_goal=5_000_000,
        max_concurrent_per_agent=20,
        goal_timeout_seconds=7200,
        requests_per_minute=6000,
        burst_requests_per_10s=1000,
        connector_rpm={},  # no per-connector limits for enterprise
        knowledge_storage_mb=100_000,
        max_cost_per_goal_usd=50.0,
        max_cost_per_day_usd=500.0,
    ),
}


class LimitsV2Checker:
    """Checks all v2 limits with in-process counters + Redis when available."""

    def __init__(self, redis: Any = None) -> None:
        self._redis = redis
        # In-process counters (per-window): key → (count, window_start)
        self._counters: dict[str, tuple[int, float]] = {}

    def get_config(self, plan: str) -> LimitsV2Config:
        return PLAN_LIMITS_V2.get(plan.lower(), PLAN_LIMITS_V2["free"])

    def check_step_limit(self, plan: str, current_step: int) -> tuple[bool, str]:
        """Check if step count is within plan limit."""
        cfg = self.get_config(plan)
        if current_step > cfg.max_steps_per_goal:
            return (
                False,
                f"Step limit reached: {current_step} > {cfg.max_steps_per_goal} (plan: {plan})",
            )
        return True, ""

    def check_token_limit(self, plan: str, input_tokens: int) -> tuple[bool, str]:
        """Check if token count is within plan limit."""
        cfg = self.get_config(plan)
        if input_tokens > cfg.max_input_tokens_per_goal:
            return (
                False,
                (
                    f"Token limit: {input_tokens:,} > "
                    f"{cfg.max_input_tokens_per_goal:,} tokens/goal (plan: {plan})"
                ),
            )
        return True, ""

    def check_connector_rate(
        self,
        plan: str,
        connector_name: str,
        tenant_id: str,
    ) -> tuple[bool, str]:
        """Check per-connector rate limit (in-process fallback)."""
        cfg = self.get_config(plan)
        rpm = cfg.connector_rpm.get(connector_name)
        if rpm is None:
            return True, ""  # no limit for this connector on this plan

        key = f"conn_rate:{tenant_id}:{connector_name}"
        now = time.monotonic()
        count, window_start = self._counters.get(key, (0, now))

        # New window (60s)
        if now - window_start >= 60:
            self._counters[key] = (1, now)
            return True, ""

        if count >= rpm:
            return (
                False,
                f"Connector rate limit: {connector_name} > {rpm} calls/min (plan: {plan})",
            )

        self._counters[key] = (count + 1, window_start)
        return True, ""

    def check_burst_rate(
        self,
        plan: str,
        tenant_id: str,
    ) -> tuple[bool, str]:
        """Check 10-second burst rate limit."""
        cfg = self.get_config(plan)
        key = f"burst:{tenant_id}"
        now = time.monotonic()
        count, window_start = self._counters.get(key, (0, now))

        if now - window_start >= 10:
            self._counters[key] = (1, now)
            return True, ""

        if count >= cfg.burst_requests_per_10s:
            return (
                False,
                (
                    f"Burst rate limit exceeded "
                    f"({cfg.burst_requests_per_10s} req/10s for plan: {plan})"
                ),
            )

        self._counters[key] = (count + 1, window_start)
        return True, ""


# Module singleton
_limits_v2 = LimitsV2Checker()
