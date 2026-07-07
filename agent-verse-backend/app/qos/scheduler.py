from __future__ import annotations
from dataclasses import dataclass
from app.qos.priority_policy import QueuePriority
from app.tenancy.context import TenantContext, PlanTier

_QUEUE_MAP: dict[PlanTier, str] = {
    PlanTier.ENTERPRISE: "goals.enterprise",
    PlanTier.PROFESSIONAL: "goals.professional",
    PlanTier.STARTER: "goals.starter",
    PlanTier.FREE: "goals.free",
}


@dataclass
class ScheduledGoal:
    goal_id: str
    tenant_id: str
    queue_name: str
    priority: QueuePriority


class QoSScheduler:
    def __init__(self, max_concurrent: int = 10) -> None:
        self._max = max_concurrent

    def schedule(
        self,
        goal_id: str,
        *,
        tenant_ctx: TenantContext,
        priority: QueuePriority = QueuePriority.MEDIUM,
    ) -> ScheduledGoal:
        queue = _QUEUE_MAP.get(tenant_ctx.plan, "goals.free")
        return ScheduledGoal(
            goal_id=goal_id,
            tenant_id=tenant_ctx.tenant_id,
            queue_name=queue,
            priority=priority,
        )
