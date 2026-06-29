"""Comprehensive tests for app/enterprise/compliance.py."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.enterprise.compliance import ComplianceController, DataExportRequest
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="comp-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")
T2 = TenantContext(tenant_id="comp-t2", plan=PlanTier.STARTER, api_key_id="k2")


# ── DataExportRequest dataclass ───────────────────────────────────────────────


def test_data_export_request_defaults() -> None:
    req = DataExportRequest()
    assert req.request_id  # auto-generated
    assert req.status == "pending"
    assert req.download_url == ""
    assert req.payload == {}
    assert req.tenant_id == ""


def test_data_export_request_request_id_is_unique() -> None:
    r1 = DataExportRequest()
    r2 = DataExportRequest()
    assert r1.request_id != r2.request_id


# ── ComplianceController — basic export ──────────────────────────────────────


async def test_request_data_export_status_ready() -> None:
    cc = ComplianceController()
    req = await cc.request_data_export(tenant_ctx=T)
    assert req.status == "ready"
    assert req.tenant_id == T.tenant_id


async def test_request_data_export_download_url_format() -> None:
    cc = ComplianceController()
    req = await cc.request_data_export(tenant_ctx=T)
    assert req.download_url.startswith("/compliance/export/")
    assert req.request_id in req.download_url


async def test_request_data_export_payload_structure() -> None:
    cc = ComplianceController()
    req = await cc.request_data_export(tenant_ctx=T)
    payload = req.payload
    assert "tenant_id" in payload
    assert "plan" in payload
    assert "export_timestamp" in payload
    assert "export_format_version" in payload
    assert "data" in payload

    data = payload["data"]
    assert "goals" in data
    assert "audit_entries" in data
    assert "api_keys" in data
    assert data["api_keys"] == []  # Never export raw keys


async def test_request_data_export_tenant_id_in_payload() -> None:
    cc = ComplianceController()
    req = await cc.request_data_export(tenant_ctx=T)
    assert req.payload["tenant_id"] == T.tenant_id


async def test_request_data_export_stored_in_memory() -> None:
    cc = ComplianceController()
    req = await cc.request_data_export(tenant_ctx=T)
    assert req.request_id in cc._export_requests


# ── ComplianceController.configure_services ───────────────────────────────────


def test_configure_services_sets_references() -> None:
    cc = ComplianceController()
    mock_goal_svc = MagicMock()
    mock_audit = MagicMock()
    mock_db = MagicMock()

    cc.configure_services(
        goal_service=mock_goal_svc,
        audit_log=mock_audit,
        db=mock_db,
    )

    assert cc._goal_service is mock_goal_svc
    assert cc._audit_log is mock_audit
    assert cc._db is mock_db


async def test_request_data_export_with_audit_log() -> None:
    cc = ComplianceController()

    mock_audit = MagicMock()
    mock_entry = MagicMock(
        event_id="e1", goal_id="g1", tool_name="tool", outcome="success"
    )
    mock_audit.query = MagicMock(return_value=[mock_entry])
    cc.configure_services(audit_log=mock_audit)

    req = await cc.request_data_export(tenant_ctx=T)
    audit_data = req.payload["data"]["audit_entries"]
    assert len(audit_data) == 1
    assert audit_data[0]["event_id"] == "e1"


async def test_request_data_export_with_agent_store() -> None:
    cc = ComplianceController()
    mock_agent_store = MagicMock()
    mock_agent_store.list_all = MagicMock(
        return_value=[{"agent_id": "a1", "name": "Agent One"}]
    )
    cc.configure_services(agent_store=mock_agent_store)

    req = await cc.request_data_export(tenant_ctx=T)
    agents_data = req.payload["data"]["agents"]
    assert len(agents_data) == 1
    assert agents_data[0]["agent_id"] == "a1"


async def test_request_data_export_with_schedule_store() -> None:
    cc = ComplianceController()
    mock_schedule_store = MagicMock()
    mock_schedule_store.list_all = MagicMock(
        return_value=[{"schedule_id": "s1", "goal_id": "g1"}]
    )
    cc.configure_services(schedule_store=mock_schedule_store)

    req = await cc.request_data_export(tenant_ctx=T)
    schedules = req.payload["data"]["schedules"]
    assert len(schedules) == 1
    assert schedules[0]["schedule_id"] == "s1"


async def test_request_data_export_with_goal_service_in_memory() -> None:
    """Goal service with in-memory _goals dict (no DB session factory)."""
    cc = ComplianceController()

    mock_goal = MagicMock()
    mock_goal.tenant_id = T.tenant_id
    mock_goal.goal_text = "Fix the bug"
    mock_goal.status = "completed"
    mock_goal.created_at = "2024-01-01T00:00:00Z"

    mock_goal_svc = MagicMock()
    mock_goal_svc._db_session_factory = None
    mock_goal_svc._goals = {"g1": mock_goal}
    cc.configure_services(goal_service=mock_goal_svc)

    req = await cc.request_data_export(tenant_ctx=T)
    goals = req.payload["data"]["goals"]
    assert len(goals) == 1
    assert goals[0]["goal_text"] == "Fix the bug"


async def test_request_data_export_with_goal_service_wrong_tenant_excluded() -> None:
    """Goals belonging to another tenant must not appear in the export."""
    cc = ComplianceController()

    mock_goal = MagicMock()
    mock_goal.tenant_id = "other-tenant"  # Different tenant
    mock_goal.goal_text = "Secret goal"

    mock_goal_svc = MagicMock()
    mock_goal_svc._db_session_factory = None
    mock_goal_svc._goals = {"g1": mock_goal}
    cc.configure_services(goal_service=mock_goal_svc)

    req = await cc.request_data_export(tenant_ctx=T)
    goals = req.payload["data"]["goals"]
    assert len(goals) == 0


async def test_request_data_export_audit_exception_continues() -> None:
    """If audit_log.query raises, export should still succeed."""
    cc = ComplianceController()
    mock_audit = MagicMock()
    mock_audit.query = MagicMock(side_effect=RuntimeError("audit store down"))
    cc.configure_services(audit_log=mock_audit)

    req = await cc.request_data_export(tenant_ctx=T)
    # Export still completes
    assert req.status == "ready"


# ── ComplianceController.get_export_status ────────────────────────────────────


async def test_get_export_status_found() -> None:
    cc = ComplianceController()
    req = await cc.request_data_export(tenant_ctx=T)
    fetched = await cc.get_export_status(request_id=req.request_id, tenant_ctx=T)
    assert fetched is not None
    assert fetched.request_id == req.request_id


async def test_get_export_status_wrong_tenant_returns_none() -> None:
    cc = ComplianceController()
    req = await cc.request_data_export(tenant_ctx=T)
    result = await cc.get_export_status(request_id=req.request_id, tenant_ctx=T2)
    assert result is None


async def test_get_export_status_missing_returns_none() -> None:
    cc = ComplianceController()
    result = await cc.get_export_status(request_id="nonexistent-id", tenant_ctx=T)
    assert result is None


# ── ComplianceController.get_export_payload ───────────────────────────────────


async def test_get_export_payload_returns_dict() -> None:
    cc = ComplianceController()
    req = await cc.request_data_export(tenant_ctx=T)
    payload = await cc.get_export_payload(request_id=req.request_id, tenant_ctx=T)
    assert isinstance(payload, dict)
    assert "tenant_id" in payload


async def test_get_export_payload_nonexistent_returns_none() -> None:
    cc = ComplianceController()
    result = await cc.get_export_payload(request_id="ghost-id", tenant_ctx=T)
    assert result is None


# ── ComplianceController.request_data_deletion ────────────────────────────────


async def test_request_data_deletion_scheduled() -> None:
    cc = ComplianceController()
    result = await cc.request_data_deletion(tenant_ctx=T)
    assert result["deletion_scheduled"] is True
    assert result["tenant_id"] == T.tenant_id
    assert "scheduled_at" in result
    assert "30 days" in result["note"]


async def test_request_data_deletion_records_tenant() -> None:
    cc = ComplianceController()
    await cc.request_data_deletion(tenant_ctx=T)
    assert T.tenant_id in cc._deleted_tenants


async def test_request_data_deletion_scheduled_at_30_days_future() -> None:
    cc = ComplianceController()
    result = await cc.request_data_deletion(tenant_ctx=T)
    scheduled = datetime.fromisoformat(result["scheduled_at"])
    expected = datetime.now(UTC) + timedelta(days=29)  # Should be ~30 days out
    assert scheduled > expected


# ── ComplianceController.retention_sweep ─────────────────────────────────────


async def test_retention_sweep_no_requests() -> None:
    cc = ComplianceController()
    result = cc.retention_sweep(retention_days=90)
    assert result["records_swept"] == 0
    assert result["retention_days"] == 90


async def test_retention_sweep_old_requests_swept() -> None:
    cc = ComplianceController()
    # Create a request that's 100 days old
    old_req = DataExportRequest(tenant_id="t1")
    old_req.created_at = (datetime.now(UTC) - timedelta(days=100)).isoformat()
    cc._export_requests[old_req.request_id] = old_req

    result = cc.retention_sweep(retention_days=90)
    assert result["records_swept"] == 1


async def test_retention_sweep_new_requests_not_swept() -> None:
    cc = ComplianceController()
    new_req = DataExportRequest(tenant_id="t1")
    # created_at defaults to now
    cc._export_requests[new_req.request_id] = new_req

    result = cc.retention_sweep(retention_days=90)
    assert result["records_swept"] == 0


async def test_retention_sweep_custom_retention_days() -> None:
    cc = ComplianceController()
    old_req = DataExportRequest(tenant_id="t1")
    old_req.created_at = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    cc._export_requests[old_req.request_id] = old_req

    result = cc.retention_sweep(retention_days=5)  # 5-day retention
    assert result["records_swept"] == 1
    assert result["retention_days"] == 5


# ── ComplianceController.get_data_residency ───────────────────────────────────


def test_get_data_residency_returns_regions() -> None:
    cc = ComplianceController()
    res = cc.get_data_residency(tenant_ctx=T)
    assert res["tenant_id"] == T.tenant_id
    assert "primary_region" in res
    assert "backup_region" in res


def test_get_data_residency_gdpr_and_soc2_not_hardcoded_true() -> None:
    """Compliance assertions must not be hardcoded True (regression guard)."""
    cc = ComplianceController()
    res = cc.get_data_residency(tenant_ctx=T)
    assert res["gdpr_compliant"] is False
    assert res["soc2_type2"] is False


def test_get_data_residency_has_note() -> None:
    cc = ComplianceController()
    res = cc.get_data_residency(tenant_ctx=T)
    assert "note" in res
    assert "compliance" in res["note"].lower()


# ── ComplianceController.execute_data_deletion_async ─────────────────────────


async def test_execute_data_deletion_no_db_returns_error() -> None:
    cc = ComplianceController()
    result = await cc.execute_data_deletion_async(tenant_ctx=T, db=None)
    assert "error" in result
    assert result["deleted_rows"] == 0


# ── DB-layer no-ops when factory is None ──────────────────────────────────────


async def test_db_save_request_no_factory_noop() -> None:
    cc = ComplianceController()
    req = DataExportRequest(tenant_id="t1")
    # Should not raise
    await cc._db_save_request(req)


async def test_db_load_request_no_factory_returns_none() -> None:
    cc = ComplianceController()
    result = await cc._db_load_request("req-id", "t1")
    assert result is None


async def test_db_save_deletion_no_factory_noop() -> None:
    cc = ComplianceController()
    # Should not raise
    await cc._db_save_deletion("t1")
