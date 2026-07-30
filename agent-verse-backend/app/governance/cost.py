"""Cost controls — per-goal and per-tenant daily budget enforcement.

Before every tool call the pipeline calls check_and_record():
  - If the estimated cost would exceed per_goal_usd, returns False (block).
  - If it would exceed per_tenant_daily_usd, returns False (block).
  - Otherwise adds the cost to running totals and returns True.

In production this is backed by Redis counters with daily TTL (midnight reset).
This in-memory implementation is used in tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger
from app.observability.metrics import record_cost_usd
from app.tenancy.context import TenantContext


@dataclass(frozen=True)
class BudgetConfig:
    per_goal_usd: float = 10.0
    per_tenant_daily_usd: float = 500.0


def _parse_float(val: Any) -> float:
    """Decode a Redis GET value (bytes, str, int, or None) to float."""
    if val is None:
        return 0.0
    if isinstance(val, (bytes, bytearray)):
        val = val.decode()
    return float(val) if val else 0.0


class CostController:
    """Enforces per-goal and per-tenant-daily cost budgets."""

    def __init__(
        self,
        config: BudgetConfig | None = None,
        *,
        per_goal_usd: float | None = None,
        per_tenant_daily_usd: float | None = None,
    ) -> None:
        if config is None:
            config = BudgetConfig(
                per_goal_usd=per_goal_usd if per_goal_usd is not None else 10.0,
                per_tenant_daily_usd=(
                    per_tenant_daily_usd if per_tenant_daily_usd is not None else 500.0
                ),
            )
        self._cfg = config
        # Key: (tenant_id, goal_id) → total USD spent
        self._goal_totals: dict[tuple[str, str], float] = defaultdict(float)
        # Key: tenant_id → total daily USD spent
        self._daily_totals: dict[str, float] = defaultdict(float)
        # Track when daily totals were last reset (per tenant: tenant_id → date string)
        self._last_reset_date: dict[str, str] = {}
        # Optional Redis client for cross-replica cost tracking (set by main.py)
        self._redis: Any = None
        # Per-goal+tenant locks to prevent TOCTOU races
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def _reset_if_new_day(self, tenant_id: str) -> None:
        """Reset daily totals if we've crossed midnight UTC (sync, no lock)."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        last = self._last_reset_date.get(tenant_id)
        if last != today:
            if last is not None:
                # It's a new day — reset daily total
                self._daily_totals[tenant_id] = 0.0
            self._last_reset_date[tenant_id] = today

    async def _reset_if_new_day_atomic(self, tenant_id: str) -> None:
        """Atomic daily reset using a per-tenant lock."""
        async with self._locks[f"reset:{tenant_id}"]:
            today = datetime.now(UTC).strftime("%Y-%m-%d")
            if self._last_reset_date.get(tenant_id) != today:
                if self._last_reset_date.get(tenant_id) is not None:
                    self._daily_totals[tenant_id] = 0.0
                self._last_reset_date[tenant_id] = today

    async def check_and_record(
        self,
        *,
        goal_id: str,
        cost_usd: float,
        tenant_ctx: TenantContext,
        tool_name: str = "",
        attempt_id: str = "",
    ) -> bool:
        """Atomically check budget and record cost. Returns True if within budget."""
        del attempt_id
        lock_key = f"{tenant_ctx.tenant_id}:{goal_id}"
        async with self._locks[lock_key]:
            await self._reset_if_new_day_atomic(tenant_ctx.tenant_id)

            goal_key = (tenant_ctx.tenant_id, goal_id)
            new_goal_total = self._goal_totals[goal_key] + cost_usd
            new_daily_total = self._daily_totals[tenant_ctx.tenant_id] + cost_usd

            if new_goal_total > self._cfg.per_goal_usd:
                return False
            if new_daily_total > self._cfg.per_tenant_daily_usd:
                return False

            self._goal_totals[goal_key] = new_goal_total
            self._daily_totals[tenant_ctx.tenant_id] = new_daily_total
            # 2.4: 80% budget alert
            if self._cfg.per_tenant_daily_usd > 0:
                _pct = new_daily_total / self._cfg.per_tenant_daily_usd
                if 0.79 < _pct <= 0.81:
                    get_logger(__name__).warning(
                        "budget_80pct_alert",
                        tenant_id=tenant_ctx.tenant_id,
                        daily_used=new_daily_total,
                        daily_limit=self._cfg.per_tenant_daily_usd,
                        pct=round(_pct * 100, 1),
                    )
            record_cost_usd(scope="tool", amount=cost_usd)
            return True

    def goal_total(self, goal_id: str, *, tenant_ctx: TenantContext) -> float:
        return self._goal_totals.get((tenant_ctx.tenant_id, goal_id), 0.0)

    def daily_total(self, *, tenant_ctx: TenantContext) -> float:
        self._reset_if_new_day(tenant_ctx.tenant_id)
        return self._daily_totals.get(tenant_ctx.tenant_id, 0.0)

    def get_tenant_cost_today(self, tenant_ctx: TenantContext) -> float:
        """Return the current-day spend for the tenant (resets at UTC midnight)."""
        self._reset_if_new_day(tenant_ctx.tenant_id)
        return self._daily_totals.get(tenant_ctx.tenant_id, 0.0)


# Atomic Lua script: check BOTH goal AND daily limits, then increment both.
# Returns "<new_goal>:<new_daily>" on success, or raises a Redis error reply
# on budget violation — so no increment ever happens when a limit is exceeded.
_LUA_CHECK_AND_INCREMENT = """\
local goal_current  = tonumber(redis.call('GET', KEYS[1])) or 0
local daily_current = tonumber(redis.call('GET', KEYS[2])) or 0
local cost        = tonumber(ARGV[1])
local goal_limit  = tonumber(ARGV[2])
local daily_limit = tonumber(ARGV[3])
local goal_expiry  = tonumber(ARGV[4])
local daily_expiry = tonumber(ARGV[5])

if goal_limit > 0 and goal_current + cost > goal_limit then
    return redis.error_reply('GOAL_BUDGET_EXCEEDED')
end
if daily_limit > 0 and daily_current + cost > daily_limit then
    return redis.error_reply('DAILY_BUDGET_EXCEEDED')
end

local new_goal  = redis.call('INCRBYFLOAT', KEYS[1], cost)
redis.call('EXPIREAT', KEYS[1], goal_expiry)
local new_daily = redis.call('INCRBYFLOAT', KEYS[2], cost)
redis.call('EXPIREAT', KEYS[2], daily_expiry)
return tostring(new_goal) .. ':' .. tostring(new_daily)
"""


class RedisCostController:
    """Production CostController backed by Redis for cross-replica accuracy.

    Uses an atomic Lua script (check-then-increment) so a denied request never
    permanently charges the tenant.  EXPIREAT resets counters at next UTC midnight
    for daily counters and after 24 h for per-goal counters.

    All replicas share the same counters, preventing per-replica bypass.
    """

    def __init__(
        self, redis: Any, per_tenant_config: dict[str, BudgetConfig] | None = None
    ) -> None:
        self._redis = redis
        self._tenant_configs: dict[str, BudgetConfig] = per_tenant_config or {}

    def _daily_key(self, tenant_id: str) -> str:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        return f"cost:daily:{tenant_id}:{today}"

    def _goal_key(self, goal_id: str, tenant_id: str = "") -> str:
        return f"cost:goal:{tenant_id}:{goal_id}"

    async def _get_ttl_to_midnight(self) -> int:
        """Seconds until next UTC midnight."""
        now = datetime.now(UTC)
        from datetime import timedelta

        midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return max(1, int((midnight - now).total_seconds()))

    async def check_and_record_async(
        self,
        *,
        goal_id: str,
        cost_usd: float,
        tenant_ctx: Any,
        attempt_id: str = "",
    ) -> bool:
        """Check budget atomically (check-then-increment). Returns True if within budget.

        Uses a Lua script so the counters are only incremented when both the
        per-goal and per-tenant-daily limits are satisfied.  On Redis clients
        that don't support Lua (test doubles), falls back to a non-atomic
        GET-check-INCRBYFLOAT sequence.

        ``attempt_id`` enables idempotency: the same (goal_id, attempt_id) pair
        is only charged once, making it safe to retry on Celery redeliveries.
        """
        cfg = self._tenant_configs.get(tenant_ctx.tenant_id, BudgetConfig())

        # Idempotency guard — skip re-charging the same attempt
        idem_key = (
            f"cost_idem:{tenant_ctx.tenant_id}:{goal_id}:{attempt_id}"
            if attempt_id
            else ""
        )
        if idem_key:
            try:
                if await self._redis.exists(idem_key):
                    return True  # already charged this attempt
            except Exception:
                pass

        try:
            goal_key = self._goal_key(goal_id, tenant_ctx.tenant_id)
            daily_key = self._daily_key(tenant_ctx.tenant_id)
            goal_limit = cfg.per_goal_usd if cfg.per_goal_usd > 0 else 0.0
            daily_limit = cfg.per_tenant_daily_usd if cfg.per_tenant_daily_usd > 0 else 0.0
            now_ts = int(time.time())
            ttl = await self._get_ttl_to_midnight()
            goal_expiry = now_ts + 86400
            daily_expiry = now_ts + ttl

            if getattr(self._redis, "register_script", None) is not None:
                # Atomic Lua path — check THEN increment (both counters in one script)
                script = self._redis.register_script(_LUA_CHECK_AND_INCREMENT)
                await script(
                    keys=[goal_key, daily_key],
                    args=[
                        str(cost_usd),
                        str(goal_limit),
                        str(daily_limit),
                        str(goal_expiry),
                        str(daily_expiry),
                    ],
                )
            else:
                # Fallback for test doubles without Lua support.
                # Non-atomic GET-check-INCRBYFLOAT — safe for single-replica tests.
                goal_current = _parse_float(await self._redis.get(goal_key))
                daily_current = _parse_float(await self._redis.get(daily_key))
                if goal_limit > 0 and goal_current + cost_usd > goal_limit:
                    return False
                if daily_limit > 0 and daily_current + cost_usd > daily_limit:
                    return False
                await self._redis.incrbyfloat(goal_key, cost_usd)
                await self._redis.expire(goal_key, 86400)
                _new_daily = _parse_float(await self._redis.incrbyfloat(daily_key, cost_usd))
                await self._redis.expireat(daily_key, daily_expiry)
                # 2.4: 80% budget alert
                if cfg.per_tenant_daily_usd > 0:
                    _pct = _new_daily / cfg.per_tenant_daily_usd
                    if 0.79 < _pct <= 0.81:
                        get_logger(__name__).warning(
                            "budget_80pct_alert",
                            tenant_id=tenant_ctx.tenant_id,
                            daily_used=_new_daily,
                            daily_limit=cfg.per_tenant_daily_usd,
                            pct=round(_pct * 100, 1),
                        )

            # Stamp idempotency key so retries are skipped
            if idem_key:
                with contextlib.suppress(Exception):
                    await self._redis.set(idem_key, "1", ex=86400)

            record_cost_usd(scope="tool", amount=cost_usd)
            return True

        except Exception as exc:
            err_str = str(exc)
            if "GOAL_BUDGET_EXCEEDED" in err_str or "DAILY_BUDGET_EXCEEDED" in err_str:
                return False
            get_logger(__name__).warning("cost_check_error", error=err_str[:100])
            return os.getenv("ENVIRONMENT", "development") != "production"

    async def refund_async(
        self,
        *,
        goal_id: str,
        cost_usd: float,
        tenant_ctx: Any,
        reason: str = "",
    ) -> None:
        """Refund a previously charged cost when the downstream operation failed.

        Uses INCRBYFLOAT with a negative amount — this is the standard Redis
        compensating pattern and is always safe (no underflow guard; a counter
        going slightly negative is acceptable and self-corrects on the next charge).
        """
        try:
            goal_key = self._goal_key(goal_id, tenant_ctx.tenant_id)
            daily_key = self._daily_key(tenant_ctx.tenant_id)
            await self._redis.incrbyfloat(goal_key, -cost_usd)
            await self._redis.incrbyfloat(daily_key, -cost_usd)
            get_logger(__name__).info(
                "cost_refunded", goal_id=goal_id, amount=cost_usd, reason=reason
            )
        except Exception as exc:
            get_logger(__name__).warning("cost_refund_failed", error=str(exc)[:80])

    async def get_tenant_cost_today(self, tenant_ctx: Any) -> float:
        """Get today's accumulated cost for this tenant from Redis."""
        try:
            daily_key = self._daily_key(tenant_ctx.tenant_id)
            val = await self._redis.get(daily_key)
            return float(val) if val else 0.0
        except Exception:
            return 0.0

    async def get_budget_status(
        self,
        tenant_id: str | None = None,
        goal_id: str | None = None,
        *,
        tenant_ctx: Any = None,
    ) -> dict[str, Any]:
        """Pure READ operation — never modifies Redis counters (Amendment 6.4).

        Accepts either ``tenant_id`` (positional/keyword) or ``tenant_ctx``
        (keyword-only) to resolve the tenant.  Safe to call from prediction
        endpoints and dashboards.  Uses GET (not INCRBYFLOAT) so it cannot
        corrupt TTLs or counter values.
        """
        resolved_id: str
        if tenant_id is not None:
            resolved_id = tenant_id
        elif tenant_ctx is not None:
            resolved_id = tenant_ctx.tenant_id
        else:
            msg = "Either tenant_id or tenant_ctx must be provided"
            raise ValueError(msg)

        daily_key = self._daily_key(resolved_id)
        goal_key = f"cost:goal:{resolved_id}:{goal_id}" if goal_id else None

        try:
            daily_spent = _parse_float(await self._redis.get(daily_key))
            goal_spent = (
                _parse_float(await self._redis.get(goal_key)) if goal_key else 0.0
            )
        except Exception:
            daily_spent = 0.0
            goal_spent = 0.0

        cfg = self._tenant_configs.get(resolved_id, BudgetConfig())
        remaining = max(0.0, cfg.per_tenant_daily_usd - daily_spent)

        return {
            "daily_spent": daily_spent,
            "daily_limit": cfg.per_tenant_daily_usd,
            "daily_remaining": remaining,
            "budget_pct_remaining": remaining / max(cfg.per_tenant_daily_usd, 0.01),
            "goal_spent": goal_spent,
        }

    async def get_cost_tier(
        self, *, goal_id: str, tenant_ctx: Any
    ) -> str:
        """
        Return cost tier based on budget consumption.
        Used by ModelRouter to auto-downgrade models when budget is tight.

        Returns:
            'premium'  — < 60% budget used → full model tier
            'standard' — 60-85% used      → execution model for planning
            'economy'  — > 85% used        → verification model for all
        """
        try:
            status = await self.get_budget_status(
                tenant_ctx.tenant_id, goal_id=goal_id
            )
            pct_used = 1.0 - status.get("budget_pct_remaining", 1.0)
            if pct_used >= 0.85:
                return "economy"
            if pct_used >= 0.60:
                return "standard"
        except Exception:
            pass
        return "premium"

    async def check_and_record(
        self,
        *,
        tenant_ctx: Any,
        goal_id: str,
        cost_usd: float,
        tool_name: str = "",
        attempt_id: str = "",
    ) -> bool:
        """Drop-in alias matching CostController.check_and_record signature."""
        return await self.check_and_record_async(
            tenant_ctx=tenant_ctx,
            goal_id=goal_id,
            cost_usd=cost_usd,
            attempt_id=attempt_id,
        )

    def configure_tenant_budget(self, tenant_id: str, budget: BudgetConfig) -> None:
        self._tenant_configs[tenant_id] = budget

    async def try_record_and_check(
        self,
        tenant_id: str,
        goal_id: str,
        cost_usd: float,
        tenant_budget: float,
    ) -> bool:
        """Atomically check budget and record cost using a Lua script.

        Returns True if within budget and cost recorded, False if budget exceeded.
        Fails open (returns True) on Redis errors other than BUDGET_EXCEEDED so
        a Redis outage never blocks all tool calls.
        """
        import datetime as _dt
        import logging as _logging

        if self._redis is None:
            return True

        daily_key = self._daily_key(tenant_id)

        # Expiry = next midnight UTC (epoch seconds)
        now = _dt.datetime.now(_dt.UTC)
        midnight = (now + _dt.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        expiry_ts = int(midnight.timestamp())

        _atomic_script = """
local current = tonumber(redis.call('GET', KEYS[1])) or 0
local increment = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
if current + increment > limit then
    return redis.error_reply('BUDGET_EXCEEDED')
end
local new_val = redis.call('INCRBYFLOAT', KEYS[1], increment)
redis.call('EXPIREAT', KEYS[1], tonumber(ARGV[3]))
return tostring(new_val)
"""
        try:
            if hasattr(self._redis, "register_script"):
                script = self._redis.register_script(_atomic_script)
                await script(
                    keys=[daily_key],
                    args=[str(cost_usd), str(tenant_budget), str(expiry_ts)],
                )
            else:
                # Fallback for Redis clients without Lua support
                current = float(await self._redis.get(daily_key) or 0)
                if current + cost_usd > tenant_budget:
                    return False
                await self._redis.incrbyfloat(daily_key, cost_usd)
                await self._redis.expireat(daily_key, expiry_ts)
            return True
        except Exception as exc:
            if "BUDGET_EXCEEDED" in str(exc):
                return False
            _logging.getLogger(__name__).warning(
                "cost_controller_atomic_redis_error error=%s fail_open=True", str(exc)
            )
            return True  # Fail open on non-budget errors
