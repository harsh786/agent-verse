"""Per-tenant bulkhead: limits concurrent in-flight trigger goals.

a06-F103-02: two counts make up "in flight". The Redis counter covers the
dispatches running right now (a slot is held from the gate to the enqueue, so
concurrent dispatches on different replicas cannot all slip under the cap); the
tenant's trigger goals that are still running come from Postgres
(:func:`read_in_flight_trigger_goals`, the same on every replica). The Redis slot
alone was released right after the enqueue, so it bounded dispatch calls and a
free tenant could have any number of trigger goals running.
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)

PLAN_CONCURRENCY: dict[str, int] = {
    "free": 2,
    "starter": 10,
    "professional": 100,
    "enterprise": 999,
}


# A goal still non-terminal this long after its trigger fired is past its plan's
# goal timeout (plus a margin): a stuck row, not work in flight.
IN_FLIGHT_WINDOW_MARGIN_S = 3600
_TERMINAL_GOAL_STATUSES = ("complete", "failed", "cancelled")


def in_flight_window_seconds(plan: str) -> int:
    """How far back a firing's goal can still be in flight for *plan*."""
    from app.tenancy.context import PLAN_LIMITS, PlanTier

    try:
        tier = PlanTier(plan)
    except ValueError:
        tier = PlanTier.FREE
    return PLAN_LIMITS[tier].goal_timeout_seconds + IN_FLIGHT_WINDOW_MARGIN_S


async def read_in_flight_trigger_goals(
    db_factory: Any, tenant_id: str, window_seconds: int
) -> int:
    """The tenant's trigger-created goals that have not finished (RLS + tenant predicate)."""
    from sqlalchemy import bindparam, text

    from app.db.rls import sqlalchemy_rls_context

    async with (
        db_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        result = await session.execute(
            text(
                "SELECT count(*) FROM trigger_events te "
                "JOIN goals g ON g.id = te.goal_id AND g.tenant_id = te.tenant_id "
                "WHERE te.tenant_id = :t AND te.goal_created "
                "AND te.fired_at >= NOW() - make_interval(secs => :w) "
                "AND g.status NOT IN :terminal"
            ).bindparams(bindparam("terminal", expanding=True)),
            {"t": tenant_id, "w": int(window_seconds), "terminal": list(_TERMINAL_GOAL_STATUSES)},
        )
        return int(result.scalar() or 0)


class TriggerBulkhead:
    """Redis-backed per-tenant concurrency limiter."""

    def __init__(self, redis: object | None = None) -> None:
        self._redis = redis

    def _key(self, tenant_id: str) -> str:
        return f"trigger_bulkhead:{tenant_id}"

    async def acquire(self, tenant_id: str, plan: str = "free", *, in_flight: int = 0) -> bool:
        """Return True if the slot was acquired (trigger can proceed).

        ``in_flight`` is the tenant's trigger goals still running (see
        :func:`read_in_flight_trigger_goals`); they count against the cap
        together with the dispatches holding a slot right now.
        """
        cap = PLAN_CONCURRENCY.get(plan, 2)
        if self._redis is None:
            return True

        key = self._key(tenant_id)
        try:
            current = await self._redis.incr(key)
            # Set expiry on first increment to prevent leaks
            if current == 1:
                await self._redis.expire(key, 300)  # 5 min safety TTL
            if current + max(0, in_flight) > cap:
                await self._redis.decr(key)
                return False
            return True
        except Exception as exc:
            # Fail CLOSED (TRG-14), like dedup: an uncheckable bulkhead used to
            # admit every firing.
            from app.triggers.rate_limiter import TriggerGateUnavailableError

            _log.warning("bulkhead_redis_error tenant_id=%s", tenant_id)
            raise TriggerGateUnavailableError(f"bulkhead unavailable: {exc}") from exc

    async def release(self, tenant_id: str) -> None:
        """Release a bulkhead slot."""
        if self._redis is None:
            return
        key = self._key(tenant_id)
        try:
            val = await self._redis.decr(key)
            if val < 0:
                await self._redis.set(key, 0)
        except Exception:
            _log.warning("bulkhead_release_error tenant_id=%s", tenant_id)
