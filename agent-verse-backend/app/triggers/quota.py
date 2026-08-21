"""Trigger quota enforcement — checked at CREATE time."""

from __future__ import annotations

PLAN_MAX_TRIGGERS: dict[str, int] = {
    "free": 5,
    "starter": 25,
    "professional": 200,
    "enterprise": 999_999,
}


class TriggerQuotaExceeded(Exception):  # noqa: N818
    pass


class TriggerQuotaEnforcer:
    """Check whether a tenant can create another trigger given their plan."""

    def check_create(self, current_count: int, plan: str) -> None:
        """Raise TriggerQuotaExceeded if the tenant has hit their limit."""
        limit = PLAN_MAX_TRIGGERS.get(plan, 5)
        if current_count >= limit:
            raise TriggerQuotaExceeded(
                f"Trigger quota exceeded: plan '{plan}' allows {limit} triggers "
                f"(current: {current_count})"
            )
