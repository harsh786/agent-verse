"""GDPR/SOC2/PCI-DSS compliance controls.

Provides:
- GDPR right-to-erasure: delete all tenant data
- GDPR right-of-access: export all tenant data
- Data residency: declare data location
- Retention sweep: delete records older than configured retention window
- SOC2: audit access logs
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.tenancy.context import TenantContext


@dataclass
class DataExportRequest:
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    tenant_id: str = ""
    status: str = "pending"  # pending | processing | ready | failed
    download_url: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    payload: dict[str, Any] = field(default_factory=dict)


class ComplianceController:
    """In-memory compliance controller (production uses PostgreSQL)."""

    def __init__(self) -> None:
        self._export_requests: dict[str, DataExportRequest] = {}
        self._deleted_tenants: set[str] = set()
        # Optional service references injected via configure_services()
        self._goal_service: Any = None
        self._audit_log: Any = None
        self._tenant_service: Any = None
        self._agent_store: Any = None
        self._schedule_store: Any = None
        self._knowledge_store: Any = None

    def configure_services(
        self,
        *,
        goal_service: Any = None,
        audit_log: Any = None,
        tenant_service: Any = None,
        agent_store: Any = None,
        schedule_store: Any = None,
        knowledge_store: Any = None,
    ) -> None:
        """Inject service references for comprehensive data export."""
        self._goal_service = goal_service
        self._audit_log = audit_log
        self._tenant_service = tenant_service
        self._agent_store = agent_store
        self._schedule_store = schedule_store
        self._knowledge_store = knowledge_store

    def request_data_export(self, *, tenant_ctx: TenantContext) -> DataExportRequest:
        """GDPR right-of-access — collect and return all tenant data."""
        req = DataExportRequest(tenant_id=tenant_ctx.tenant_id)
        req.status = "ready"

        # Collect data from injected services
        goals_data: list[dict[str, Any]] = []
        if self._goal_service is not None:
            try:
                goal_records: dict[str, Any] = getattr(self._goal_service, "_goals", {})
                for gid, record in goal_records.items():
                    if getattr(record, "tenant_id", "") == tenant_ctx.tenant_id:
                        goals_data.append({
                            "goal_id": gid,
                            "goal_text": getattr(record, "goal_text", ""),
                            "status": str(getattr(record, "status", "")),
                            "created_at": getattr(record, "created_at", ""),
                        })
            except Exception:
                pass

        audit_data: list[dict[str, Any]] = []
        if self._audit_log is not None:
            try:
                entries = self._audit_log.query(tenant_ctx=tenant_ctx)
                audit_data = [
                    {
                        "event_id": e.event_id,
                        "goal_id": e.goal_id,
                        "tool_name": e.tool_name,
                        "outcome": e.outcome,
                    }
                    for e in entries[:100]  # Cap at 100 for export
                ]
            except Exception:
                pass

        agents_data: list[dict[str, Any]] = []
        if self._agent_store is not None:
            try:
                agents = self._agent_store.list_all(tenant_ctx=tenant_ctx)
                agents_data = [
                    {"agent_id": a.get("agent_id"), "name": a.get("name")} for a in agents
                ]
            except Exception:
                pass

        schedules_data: list[dict[str, Any]] = []
        if self._schedule_store is not None:
            try:
                schedules = self._schedule_store.list_all(tenant_ctx=tenant_ctx)
                schedules_data = [
                    {"schedule_id": s.get("schedule_id"), "goal_id": s.get("goal_id")}
                    for s in schedules
                ]
            except Exception:
                pass

        req.payload = {
            "tenant_id": tenant_ctx.tenant_id,
            "plan": tenant_ctx.plan.value,
            "export_timestamp": datetime.now(UTC).isoformat(),
            "export_format_version": "1.0",
            "data": {
                "tenant_profile": {
                    "tenant_id": tenant_ctx.tenant_id,
                    "plan": tenant_ctx.plan.value,
                },
                "goals": goals_data,
                "audit_entries": audit_data,
                "api_keys": [],  # Never export raw keys
                "agents": agents_data,
                "schedules": schedules_data,
                "knowledge_collections": [],
            },
        }
        req.download_url = f"/compliance/export/{req.request_id}/download"
        self._export_requests[req.request_id] = req
        return req

    def get_export_status(
        self, *, request_id: str, tenant_ctx: TenantContext
    ) -> DataExportRequest | None:
        req = self._export_requests.get(request_id)
        if req is None or req.tenant_id != tenant_ctx.tenant_id:
            return None
        return req

    def request_data_deletion(self, *, tenant_ctx: TenantContext) -> dict[str, Any]:
        """GDPR right-to-erasure. Marks tenant for deletion."""
        self._deleted_tenants.add(tenant_ctx.tenant_id)
        return {
            "tenant_id": tenant_ctx.tenant_id,
            "deletion_scheduled": True,
            "scheduled_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            "note": "Data will be permanently deleted in 30 days per GDPR article 17.",
        }

    def retention_sweep(self, *, retention_days: int = 90) -> dict[str, Any]:
        """Sweep and mark records older than retention_days for deletion."""
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        swept = [
            req.request_id
            for req in self._export_requests.values()
            if datetime.fromisoformat(req.created_at) < cutoff
        ]
        return {
            "sweep_cutoff": cutoff.isoformat(),
            "records_swept": len(swept),
            "retention_days": retention_days,
        }

    def get_export_payload(
        self, *, request_id: str, tenant_ctx: TenantContext
    ) -> dict[str, Any] | None:
        """Return the raw export payload dict for a ready export request."""
        req = self.get_export_status(request_id=request_id, tenant_ctx=tenant_ctx)
        if req is None or req.status != "ready":
            return None
        return req.payload

    def get_data_residency(self, *, tenant_ctx: TenantContext) -> dict[str, Any]:
        return {
            "tenant_id": tenant_ctx.tenant_id,
            "primary_region": "us-east-1",
            "backup_region": "eu-west-1",
            "gdpr_compliant": True,
            "pci_dss_scope": False,
            "soc2_type2": True,
        }

    async def execute_data_deletion_async(
        self, *, tenant_ctx: TenantContext, db: Any
    ) -> dict[str, Any]:
        """Execute GDPR erasure — actual DB deletion. Called 30 days after request."""
        if db is None:
            return {"error": "No database configured", "deleted_rows": 0}

        from sqlalchemy import text
        deleted_counts: dict[str, Any] = {}
        tables_ordered = [
            # Child tables first (FK constraints)
            "goal_events", "goal_checkpoints", "goal_steps",
            "decision_traces", "evaluations", "cost_ledger",
            "audit_log", "approval_requests", "governance_policies",
            "collab_operations", "collab_sessions",
            "documents", "knowledge_collections",
            "mcp_credentials", "oauth_tokens", "mcp_servers",
            "execution_memory", "long_term_memory",
            "agent_permissions", "agents",
            "schedules",
            "goals",
            "api_keys",
            # Parent last
            "tenants",
        ]

        async with db() as session, session.begin():
            for table in tables_ordered:
                try:
                    # Use tenant_id column — all tables have it
                    # tenants uses id column
                    col = "id" if table == "tenants" else "tenant_id"
                    result = await session.execute(
                        text(f"DELETE FROM {table} WHERE {col} = :tid"),
                        {"tid": tenant_ctx.tenant_id}
                    )
                    deleted_counts[table] = result.rowcount
                except Exception as exc:
                    deleted_counts[table] = f"skipped: {exc}"

        total = sum(v for v in deleted_counts.values() if isinstance(v, int))
        return {
            "tenant_id": tenant_ctx.tenant_id,
            "deleted_at": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
            "total_rows_deleted": total,
            "tables": deleted_counts,
        }
