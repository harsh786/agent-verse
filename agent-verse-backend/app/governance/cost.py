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
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.observability.metrics import record_cost_usd
from app.tenancy.context import TenantContext


@dataclass(frozen=True)
class BudgetConfig:
    per_goal_usd: float = 10.0
    per_tenant_daily_usd: float = 500.0


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
            from datetime import date  # noqa: PLC0415

            today = date.today().isoformat()
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
    ) -> bool:
        """Atomically check budget and record cost. Returns True if within budget."""
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


class RedisCostController:
    """Production CostController backed by Redis for cross-replica accuracy.

    Uses INCRBYFLOAT with EXPIREAT at next midnight UTC for daily counters.
    All replicas share the same counters, preventing per-replica bypass.
    """

    def __init__(self, redis: Any, per_tenant_config: dict | None = None) -> None:
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
        self, *, goal_id: str, cost_usd: float, tenant_ctx: Any
    ) -> bool:
        """Check budget and record cost atomically. Returns True if within budget."""
        cfg = self._tenant_configs.get(tenant_ctx.tenant_id, BudgetConfig())

        try:
            # Record goal-level cost (namespaced by tenant to prevent cross-tenant leakage)
            goal_key = self._goal_key(goal_id, tenant_ctx.tenant_id)
            goal_total = await self._redis.incrbyfloat(goal_key, cost_usd)
            await self._redis.expire(goal_key, 86400)  # 24h TTL

            if cfg.per_goal_usd > 0 and goal_total > cfg.per_goal_usd:
                return False  # Over per-goal budget

            # Record daily tenant cost
            daily_key = self._daily_key(tenant_ctx.tenant_id)
            daily_total = await self._redis.incrbyfloat(daily_key, cost_usd)
            ttl = await self._get_ttl_to_midnight()
            await self._redis.expire(daily_key, ttl + 3600)  # Extra hour buffer

            if cfg.per_tenant_daily_usd > 0 and daily_total > cfg.per_tenant_daily_usd:
                return False  # Over daily tenant budget

            return True
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("redis_cost_check_failed", error=str(exc))
            import os

            if os.getenv("ENVIRONMENT", "development") == "production":
                return False  # fail-closed: deny when cost tracking unavailable
            return True  # fail-open in development

    async def get_tenant_cost_today(self, tenant_ctx: Any) -> float:
        """Get today's accumulated cost for this tenant from Redis."""
        try:
            daily_key = self._daily_key(tenant_ctx.tenant_id)
            val = await self._redis.get(daily_key)
            return float(val) if val else 0.0
        except Exception:
            return 0.0

    async def check_and_record(
        self,
        *,
        tenant_ctx: Any,
        goal_id: str,
        cost_usd: float,
        tool_name: str = "",
    ) -> bool:
        """Drop-in alias matching CostController.check_and_record signature."""
        return await self.check_and_record_async(
            tenant_ctx=tenant_ctx, goal_id=goal_id, cost_usd=cost_usd
        )

    def configure_tenant_budget(self, tenant_id: str, budget: BudgetConfig) -> None:
        self._tenant_configs[tenant_id] = budget
