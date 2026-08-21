from __future__ import annotations

import enum

from app.tenancy.context import PlanTier, TenantContext


class QueuePriority(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


_PLAN_PRIORITY: dict[PlanTier, QueuePriority] = {
    PlanTier.ENTERPRISE: QueuePriority.HIGH,
    PlanTier.PROFESSIONAL: QueuePriority.MEDIUM,
    PlanTier.STARTER: QueuePriority.MEDIUM,
    PlanTier.FREE: QueuePriority.LOW,
}


class PriorityPolicy:
    def compute(
        self,
        *,
        tenant_ctx: TenantContext,
        risk_level: str = "low",
        retry_count: int = 0,
    ) -> QueuePriority:
        base = _PLAN_PRIORITY.get(tenant_ctx.plan, QueuePriority.LOW)
        if risk_level in ("high", "critical"):
            bump: dict[QueuePriority, QueuePriority] = {
                QueuePriority.LOW: QueuePriority.MEDIUM,
                QueuePriority.MEDIUM: QueuePriority.HIGH,
                QueuePriority.HIGH: QueuePriority.CRITICAL,
                QueuePriority.CRITICAL: QueuePriority.CRITICAL,
            }
            return bump[base]
        return base
