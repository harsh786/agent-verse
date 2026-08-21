"""QueuePolicy — per-tenant queue routing rules."""

from __future__ import annotations

from dataclasses import dataclass

from app.tenancy.context import PlanTier, TenantContext


@dataclass
class QueueRoutingConfig:
    queue_name: str
    max_concurrent: int
    priority_boost: int = 0


_QUEUE_CONFIG: dict[PlanTier, QueueRoutingConfig] = {
    PlanTier.ENTERPRISE: QueueRoutingConfig(
        "goals.enterprise", max_concurrent=50, priority_boost=2
    ),
    PlanTier.PROFESSIONAL: QueueRoutingConfig(
        "goals.professional", max_concurrent=20, priority_boost=1
    ),
    PlanTier.STARTER: QueueRoutingConfig("goals.starter", max_concurrent=5),
    PlanTier.FREE: QueueRoutingConfig("goals.free", max_concurrent=1),
}


class QueuePolicy:
    def get_routing(self, tenant_ctx: TenantContext) -> QueueRoutingConfig:
        return _QUEUE_CONFIG.get(tenant_ctx.plan, _QUEUE_CONFIG[PlanTier.FREE])
