"""
Time-Based Governance Rules
==============================
Prevents destructive operations during business-off hours.

Rules:
- "no destructive/delete/drop tools between 22:00-06:00 UTC"
- "no production deployments on weekends"
- "blackout windows" (e.g., during maintenance, earnings season)
- Configurable per tenant via policy rules

All times in UTC. Fails closed: if timezone/time parsing fails → DENY.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from app.observability.logging import get_logger

logger = get_logger(__name__)


_DESTRUCTIVE_TOOLS = frozenset({
    "delete", "drop", "truncate", "destroy", "wipe", "purge",
    "nuke", "remove_all", "clear_all",
})

_PROD_DEPLOY_TOOLS = frozenset({
    "deploy", "kubectl_apply", "terraform_apply", "push_to_prod",
    "release", "publish",
})


@dataclass
class TimeRule:
    """A time-based governance rule."""

    name: str
    tool_patterns: list[str]
    blocked_hours_utc: tuple[int, int] | None = None  # (start_hour, end_hour) UTC
    blocked_weekdays: list[int] = field(default_factory=list)  # 0=Mon, 6=Sun
    reason: str = ""
    require_hitl_override: bool = True  # if True, HITL can override time block


# Platform-wide defaults (all tenants)
_DEFAULT_TIME_RULES: list[TimeRule] = [
    TimeRule(
        name="no_destructive_overnight",
        tool_patterns=["delete_*", "drop_*", "truncate_*", "destroy_*", "wipe_*"],
        blocked_hours_utc=(22, 6),
        reason="Destructive operations blocked 22:00-06:00 UTC. Submit for HITL approval.",
        require_hitl_override=True,
    ),
    TimeRule(
        name="no_prod_deploy_weekend",
        tool_patterns=["deploy_*", "*_to_prod", "kubectl_apply", "terraform_apply"],
        blocked_weekdays=[5, 6],  # Saturday, Sunday
        reason="Production deployments blocked on weekends. Request emergency override.",
        require_hitl_override=True,
    ),
]


class TimePolicyEngine:
    """Evaluates time-based governance rules for tool calls."""

    def __init__(self, custom_rules: list[TimeRule] | None = None) -> None:
        self._rules = list(_DEFAULT_TIME_RULES)
        if custom_rules:
            self._rules.extend(custom_rules)
        # Blackout windows: list of (start_dt, end_dt, reason)
        self._blackouts: list[tuple[datetime.datetime, datetime.datetime, str]] = []

    def add_blackout(
        self,
        start: datetime.datetime,
        end: datetime.datetime,
        reason: str,
    ) -> None:
        """Add a blackout window (no operations allowed)."""
        self._blackouts.append((start, end, reason))

    def check_tool(
        self,
        tool_name: str,
        *,
        now: datetime.datetime | None = None,
    ) -> tuple[bool, str]:
        """
        Check if a tool call is allowed at the current time.
        Returns (allowed: bool, reason: str).
        Fails closed: any exception → denied.
        """
        try:
            now = now or datetime.datetime.now(datetime.UTC)
            tool_lower = tool_name.lower()

            # Check blackout windows
            for start, end, reason in self._blackouts:
                if start <= now <= end:
                    logger.warning(
                        "tool_blocked_blackout",
                        tool=tool_name,
                        reason=reason,
                        until=end.isoformat(),
                    )
                    return False, f"Blackout window active: {reason}. Until {end.isoformat()}"

            # Check time rules
            for rule in self._rules:
                if not self._tool_matches_patterns(tool_lower, rule.tool_patterns):
                    continue

                # Check hour range
                if rule.blocked_hours_utc is not None:
                    start_h, end_h = rule.blocked_hours_utc
                    h = now.hour
                    if start_h > end_h:  # wraps midnight
                        if h >= start_h or h < end_h:
                            return False, rule.reason
                    else:
                        if start_h <= h < end_h:
                            return False, rule.reason

                # Check weekday
                if rule.blocked_weekdays and now.weekday() in rule.blocked_weekdays:
                    return False, rule.reason

            return True, ""

        except Exception as exc:
            logger.warning("time_policy_check_failed_closed", error=str(exc)[:80])
            return False, "Time policy check failed — denying for safety"

    @staticmethod
    def _tool_matches_patterns(tool_name: str, patterns: list[str]) -> bool:
        for p in patterns:
            if p.endswith("*") and tool_name.startswith(p[:-1]):
                return True
            if p.startswith("*") and tool_name.endswith(p[1:]):
                return True
            if tool_name == p:
                return True
            # Match base verb: "delete_*" matches any tool starting with "delete"
            base = p.rstrip("_*")
            if tool_name.startswith(base):
                return True
        return False


# Module singleton
_time_policy = TimePolicyEngine()
