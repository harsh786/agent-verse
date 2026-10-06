"""Plan limit enforcement helpers.

Raises PlanLimitExceededError (HTTP 429) when a tenant exceeds their plan's
resource limits.
"""

from __future__ import annotations

import time
from typing import Any

from app.core.errors import PlatformError
from app.observability.logging import get_logger
from app.tenancy.context import PLAN_LIMITS, TenantContext

logger = get_logger(__name__)


class PlanLimitExceededError(PlatformError):
    """Raised when a tenant exceeds their plan's resource limits."""

    http_status = 429
    severity = None  # Use PlatformError default

    def __init__(self, message: str) -> None:
        super().__init__(
            message=message,
            code="PLAN_LIMIT_EXCEEDED",
        )


class ConcurrencyLimitUnavailableError(PlatformError):
    """The concurrent-goal counter (Redis) is unreachable — refuse, don't allow."""

    http_status = 503

    def __init__(self) -> None:
        super().__init__(
            message="Concurrent-goal limit could not be checked; retry shortly.",
            code="CONCURRENCY_LIMIT_UNAVAILABLE",
        )


def check_daily_goal_limit(
    tenant_ctx: TenantContext,
    current_daily_count: int,
) -> None:
    """Raise if tenant has hit their daily goal submission limit."""
    limits = PLAN_LIMITS[tenant_ctx.plan]
    if current_daily_count >= limits.goals_per_day:
        raise PlanLimitExceededError(
            f"Daily goal limit ({limits.goals_per_day}) reached for plan "
            f"'{tenant_ctx.plan}'. Upgrade your plan or wait until tomorrow."
        )


def check_agent_limit(
    tenant_ctx: TenantContext,
    current_count: int,
) -> None:
    """Raise if tenant has hit their max agent limit."""
    limits = PLAN_LIMITS[tenant_ctx.plan]
    if current_count >= limits.max_agents:
        raise PlanLimitExceededError(
            f"Agent limit ({limits.max_agents}) reached for plan '{tenant_ctx.plan}'. "
            f"Upgrade your plan to create more agents."
        )


def check_api_key_limit(
    tenant_ctx: TenantContext,
    current_count: int,
) -> None:
    """Raise if tenant has hit their max API key limit."""
    limits = PLAN_LIMITS[tenant_ctx.plan]
    if current_count >= limits.max_api_keys:
        raise PlanLimitExceededError(
            f"API key limit ({limits.max_api_keys}) reached for plan '{tenant_ctx.plan}'."
        )


def check_knowledge_collection_limit(
    tenant_ctx: TenantContext,
    current_count: int,
) -> None:
    """Raise if tenant has hit their max knowledge collection limit."""
    limits = PLAN_LIMITS[tenant_ctx.plan]
    if current_count >= limits.max_knowledge_collections:
        raise PlanLimitExceededError(
            f"Knowledge collection limit ({limits.max_knowledge_collections}) "
            f"reached for plan '{tenant_ctx.plan}'."
        )


# ── Concurrent-goal slots: per-goal leases in a Redis sorted set ──────────────
#
# Key ``concurrent_goal_leases:{tenant}``: member = goal_id, score = lease
# expiry (epoch ms, Redis server time). Acquire is idempotent per goal and
# reclaims expired leases first; release is ``ZREM goal_id``, so a second
# release (API cancel handler + worker exit) is a no-op instead of freeing
# another goal's slot. The lease covers the plan's whole goal timeout plus a
# queue-wait headroom and is renewed when a worker starts the goal; the key
# expires with its longest lease. (The old per-tenant INCR counter with a
# 3600 s TTL refreshed on every INCR let >1 h goals stop counting and never
# decayed leaked slots for a busy tenant.)

_CONCURRENT_LIMITS = {
    "free": 2,
    "starter": 5,
    "professional": 20,
    "enterprise": 100,
}
# Queue wait a submitted goal may spend before a worker starts it (and renews
# the lease to its timeout + _RUN_HEADROOM_S).
_QUEUE_HEADROOM_S = 3600
_RUN_HEADROOM_S = 300

_ACQUIRE_LUA = """
local key = KEYS[1]
local member = ARGV[1]
local lease_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
local held = redis.call('ZSCORE', key, member)
if (not held) and redis.call('ZCARD', key) >= limit then
    return 0
end
redis.call('ZADD', key, now + lease_ms, member)
local top = redis.call('ZRANGE', key, -1, -1, 'WITHSCORES')
redis.call('PEXPIREAT', key, tonumber(top[2]))
return redis.call('ZCARD', key)
"""

_RENEW_LUA = """
local key = KEYS[1]
local member = ARGV[1]
local lease_ms = tonumber(ARGV[2])
if not redis.call('ZSCORE', key, member) then
    return 0
end
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
redis.call('ZADD', key, now + lease_ms, member)
local top = redis.call('ZRANGE', key, -1, -1, 'WITHSCORES')
redis.call('PEXPIREAT', key, tonumber(top[2]))
return 1
"""


def concurrent_goal_lease_key(tenant_id: str) -> str:
    return f"concurrent_goal_leases:{tenant_id}"


def effective_goal_timeout(
    plan_timeout_s: float, agent_timeout_s: Any = None
) -> tuple[float, str]:
    """The wall-clock budget one goal run gets, and which limit set it.

    The plan's ``goal_timeout_seconds`` is the ceiling; an agent's own
    ``timeout_seconds`` (``agents.timeout_seconds``; 0, the default, = no agent
    limit) can only shorten it: effective = min(plan, agent). The agent field used to be stored,
    returned by the API and never read, so an agent configured for a 60 s budget
    ran for the whole plan budget (30 min - 24 h). A missing, non-numeric,
    non-positive or NaN agent value means "no agent limit".

    Returns ``(seconds, source)`` with ``source`` = ``"agent"`` when the agent
    limit is the binding one, else ``"plan"``.
    """
    plan_s = float(plan_timeout_s)
    # Only a real number counts (a bool, a string or a test double never does).
    is_number = isinstance(agent_timeout_s, int | float) and not isinstance(agent_timeout_s, bool)
    agent_s = float(agent_timeout_s) if is_number else 0.0
    if not agent_s > 0 or agent_s == float("inf"):
        return _tidy_seconds(plan_s), "plan"
    if agent_s < plan_s:
        return _tidy_seconds(agent_s), "agent"
    return _tidy_seconds(plan_s), "plan"


def _tidy_seconds(value: float) -> float:
    """``300.0`` -> ``300`` so "timed out after 300s" reads as it always did."""
    return int(value) if value.is_integer() else value


def concurrent_goal_limit(plan: Any) -> int:
    plan_str = plan.value if hasattr(plan, "value") else str(plan)
    return _CONCURRENT_LIMITS.get(plan_str, _CONCURRENT_LIMITS["free"])


def concurrent_goal_lease_seconds(plan: Any, *, running: bool = False) -> int:
    """Lease for a goal slot: the plan's goal timeout + queue (or run) headroom."""
    from app.tenancy.context import PlanTier

    try:
        tier = plan if isinstance(plan, PlanTier) else PlanTier(str(plan))
    except ValueError:
        tier = PlanTier.FREE
    timeout = PLAN_LIMITS[tier].goal_timeout_seconds
    return timeout + (_RUN_HEADROOM_S if running else _QUEUE_HEADROOM_S)


def _is_unreachable(exc: BaseException) -> bool:
    return isinstance(exc, ConnectionError | TimeoutError | OSError) or type(exc).__name__ in {
        "ConnectionError",
        "TimeoutError",
        "BusyLoadingError",
    }


async def _acquire_without_lua(
    redis: Any, key: str, goal_id: str, lease_ms: int, limit: int
) -> bool:
    """Conservative non-atomic path for a Redis-compatible store without EVAL.

    Add first, then count: concurrent acquirers can only over-REFUSE (each sees
    the others' members), never over-admit.
    """
    now = int(time.time() * 1000)
    await redis.zremrangebyscore(key, "-inf", now)
    held = await redis.zscore(key, goal_id) is not None
    await redis.zadd(key, {goal_id: now + lease_ms})
    if not held and int(await redis.zcard(key)) > limit:
        await redis.zrem(key, goal_id)
        return False
    await redis.pexpireat(key, now + lease_ms)
    return True


async def check_and_increment_concurrent_goals(
    tenant_ctx: TenantContext,
    redis: Any,
    *,
    goal_id: str,
    lease_seconds: int | None = None,
) -> None:
    """Take (or re-take, idempotently) ``goal_id``'s concurrent-goal slot.

    Raises PlanLimitExceededError (429) when the tenant already holds its plan's
    limit of OTHER live leases, ConcurrencyLimitUnavailableError (503) when Redis
    cannot be reached (fail closed). Atomic on Redis (Lua, server time).
    """
    limit = concurrent_goal_limit(tenant_ctx.plan)
    plan_str = tenant_ctx.plan.value if hasattr(tenant_ctx.plan, "value") else str(tenant_ctx.plan)
    if redis is None:
        return  # no shared store wired (single-process dev / unit tests)
    key = concurrent_goal_lease_key(tenant_ctx.tenant_id)
    lease_ms = int((lease_seconds or concurrent_goal_lease_seconds(tenant_ctx.plan)) * 1000)
    over = PlanLimitExceededError(
        f"Concurrent goal limit ({limit}) reached for plan '{plan_str}'. "
        f"Wait for a running goal to complete before submitting another."
    )
    try:
        result = await redis.eval(_ACQUIRE_LUA, 1, key, goal_id, lease_ms, limit)
    except Exception as lua_exc:
        if _is_unreachable(lua_exc):
            raise ConcurrencyLimitUnavailableError() from lua_exc
        try:
            admitted = await _acquire_without_lua(redis, key, goal_id, lease_ms, limit)
        except Exception as exc:
            raise ConcurrencyLimitUnavailableError() from exc
        if not admitted:
            raise over from None
        return
    if int(result) == 0:
        raise over


async def renew_concurrent_goal_lease(
    tenant_id: str, redis: Any, *, goal_id: str, lease_seconds: int
) -> bool:
    """Extend ``goal_id``'s lease (only if it still holds one). Never raises."""
    if redis is None:
        return False
    key = concurrent_goal_lease_key(tenant_id)
    lease_ms = int(lease_seconds * 1000)
    try:
        try:
            return int(await redis.eval(_RENEW_LUA, 1, key, goal_id, lease_ms)) == 1
        except Exception as lua_exc:
            if _is_unreachable(lua_exc):
                raise
            if await redis.zscore(key, goal_id) is None:
                return False
            expiry = int(time.time() * 1000) + lease_ms
            await redis.zadd(key, {goal_id: expiry}, xx=True)
            if int(await redis.pttl(key)) < lease_ms:
                await redis.pexpireat(key, expiry)
            return True
    except Exception as exc:
        logger.warning(
            "concurrent_goal_renew_failed",
            tenant_id=tenant_id,
            goal_id=goal_id,
            error=str(exc)[:200],
        )
        return False


async def decrement_concurrent_goals(tenant_id: str, redis: Any, *, goal_id: str) -> bool:
    """Release ``goal_id``'s slot. Idempotent: True only if a lease was removed.

    Never raises (terminal paths must finish); a failure is logged and the
    lease decays at its expiry.
    """
    if redis is None:
        return False
    try:
        return int(await redis.zrem(concurrent_goal_lease_key(tenant_id), goal_id)) > 0
    except Exception as exc:
        logger.warning(
            "concurrent_goal_release_failed",
            tenant_id=tenant_id,
            goal_id=goal_id,
            error=str(exc)[:200],
        )
        return False
