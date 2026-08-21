from __future__ import annotations

from app.lifecycle.retention_policy import DataCategory


class ExportPolicy:
    def can_export(
        self,
        tenant_id: str,
        requestor_role: str,
        data_category: DataCategory,
        requesting_tenant_id: str | None = None,
    ) -> bool:
        if requesting_tenant_id and requesting_tenant_id != tenant_id:
            return False
        if data_category == DataCategory.AUDIT_LOG and requestor_role not in (
            "admin",
            "super_admin",
        ):
            return False
        return True
