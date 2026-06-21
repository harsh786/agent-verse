"""In-memory schedule store — CRUD for trigger specs, per-tenant.

In production this is backed by PostgreSQL (schedules table).
"""

from __future__ import annotations

import uuid
from typing import Any

from app.tenancy.context import TenantContext
from app.triggers.models import TriggerSpec


class ScheduleStore:
    """Per-tenant schedule registry."""

    def __init__(self) -> None:
        # Key: (tenant_id, schedule_id) → schedule record
        self._data: dict[tuple[str, str], dict[str, Any]] = {}

    def create(
        self,
        *,
        goal_id: str,
        spec: TriggerSpec,
        tenant_ctx: TenantContext,
    ) -> str:
        sched_id = uuid.uuid4().hex
        self._data[(tenant_ctx.tenant_id, sched_id)] = {
            "schedule_id": sched_id,
            "goal_id": goal_id,
            "spec": spec,
            "paused": False,
        }
        return sched_id

    def get(self, schedule_id: str, *, tenant_ctx: TenantContext) -> dict[str, Any] | None:
        return self._data.get((tenant_ctx.tenant_id, schedule_id))

    def list_all(self, *, tenant_ctx: TenantContext) -> list[dict[str, Any]]:
        return [
            rec
            for (tid, _), rec in self._data.items()
            if tid == tenant_ctx.tenant_id
        ]

    def delete(self, schedule_id: str, *, tenant_ctx: TenantContext) -> bool:
        key = (tenant_ctx.tenant_id, schedule_id)
        if key not in self._data:
            return False
        del self._data[key]
        return True

    def pause(self, schedule_id: str, *, tenant_ctx: TenantContext) -> bool:
        rec = self.get(schedule_id, tenant_ctx=tenant_ctx)
        if rec is None:
            return False
        rec["paused"] = True
        return True

    def resume(self, schedule_id: str, *, tenant_ctx: TenantContext) -> bool:
        rec = self.get(schedule_id, tenant_ctx=tenant_ctx)
        if rec is None:
            return False
        rec["paused"] = False
        return True
