"""Plan limit enforcement helpers.

Raises PlanLimitExceededError (HTTP 429) when a tenant exceeds their plan's
resource limits.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import PlatformError
from app.tenancy.context import PLAN_LIMITS, TenantContext


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


async def check_and_increment_concurrent_goals(
    tenant_ctx: TenantContext,
    redis: Any,
) -> None:
    """Raise PlanLimitExceededError if tenant is at concurrent goal limit.
    Atomically increments counter if OK.

    Uses an atomic Lua script (INCR → check → DECR if over limit) to prevent
    the TOCTOU race that exists between a non-atomic GET and INCR.
    """
    _concurrent_limits = {
        "free": 2,
        "starter": 5,
        "professional": 20,
        "enterprise": 100,
    }
    plan_str = tenant_ctx.plan.value if hasattr(tenant_ctx.plan, "value") else str(tenant_ctx.plan)
    limit = _concurrent_limits.get(plan_str, 20)
    key = f"concurrent_goals:{tenant_ctx.tenant_id}"

    # Atomic Lua: INCR first, set TTL, then DECR and return 0 if over limit.
    # This eliminates the race window that existed between GET and INCR.
    _lua_script = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local current = tonumber(redis.call('INCR', key))
redis.call('EXPIRE', key, ttl)
if current > limit then
    redis.call('DECR', key)
    return 0
end
return current
"""
    if redis is None:
        return  # no shared counter wired (single-process dev / unit tests)
    over = PlanLimitExceededError(
        f"Concurrent goal limit ({limit}) reached for plan '{plan_str}'. "
        f"Wait for a running goal to complete before submitting another."
    )
    try:
        result = await redis.eval(_lua_script, 1, key, limit, 3600)
    except Exception as lua_exc:
        # No Lua (some Redis-compatible stores): same logic, non-atomic but
        # still enforced. Only if Redis itself is unreachable do we refuse —
        # this used to ``pass`` and allow the goal (fail-open).
        try:
            current = int(await redis.incr(key))
            await redis.expire(key, 3600)
            if current > limit:
                await redis.decr(key)
                raise over from None
            return
        except PlanLimitExceededError:
            raise
        except Exception as exc:
            raise ConcurrencyLimitUnavailableError() from (exc or lua_exc)
    if result == 0:
        raise over


async def decrement_concurrent_goals(tenant_id: str, redis: Any) -> None:
    """Decrement concurrent goal counter when a goal reaches terminal state."""
    key = f"concurrent_goals:{tenant_id}"
    try:
        val = int(await redis.get(key) or 0)
        if val > 0:
            await redis.decr(key)
    except Exception:
        pass
