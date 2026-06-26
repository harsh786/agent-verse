"""Cost controls — per-goal and per-tenant daily budget enforcement.

Before every tool call the pipeline calls check_and_record():
  - If the estimated cost would exceed per_goal_usd, returns False (block).
  - If it would exceed per_tenant_daily_usd, returns False (block).
  - Otherwise adds the cost to running totals and returns True.

In production this is backed by Redis counters with daily TTL (midnight reset).
This in-memory implementation is used in tests.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from app.observability.metrics import record_cost_usd
from app.tenancy.context import TenantContext


@dataclass(frozen=True)
class BudgetConfig:
    per_goal_usd: float = 10.0
    per_tenant_daily_usd: float = 500.0


class CostController:
    """Enforces per-goal and per-tenant-daily cost budgets."""

    def __init__(self, config: BudgetConfig | None = None) -> None:
        self._cfg = config or BudgetConfig()
        # Key: (tenant_id, goal_id) → total USD spent
        self._goal_totals: dict[tuple[str, str], float] = defaultdict(float)
        # Key: tenant_id → total daily USD spent
        self._daily_totals: dict[str, float] = defaultdict(float)
        # Track when daily totals were last reset (per tenant: tenant_id → date string)
        self._last_reset_date: dict[str, str] = {}

    def _reset_if_new_day(self, tenant_id: str) -> None:
        """Reset daily totals if we've crossed midnight UTC."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        last = self._last_reset_date.get(tenant_id)
        if last != today:
            if last is not None:
                # It's a new day — reset daily total
                self._daily_totals[tenant_id] = 0.0
            self._last_reset_date[tenant_id] = today

    def check_and_record(
        self,
        *,
        goal_id: str,
        cost_usd: float,
        tenant_ctx: TenantContext,
    ) -> bool:
        self._reset_if_new_day(tenant_ctx.tenant_id)

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
