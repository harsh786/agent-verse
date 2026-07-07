from __future__ import annotations
from dataclasses import dataclass
from app.lifecycle.retention_policy import DataCategory


@dataclass
class DeletionSchedule:
    tenant_id: str
    data_category: DataCategory
    record_ids: list[str]
    scheduled_count: int


class DeletionOrchestrator:
    def schedule_deletion(
        self,
        tenant_id: str,
        data_category: DataCategory,
        record_ids: list[str],
    ) -> DeletionSchedule:
        return DeletionSchedule(
            tenant_id=tenant_id,
            data_category=data_category,
            record_ids=record_ids,
            scheduled_count=len(record_ids),
        )
