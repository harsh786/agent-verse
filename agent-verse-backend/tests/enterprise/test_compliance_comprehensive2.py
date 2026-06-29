"""Comprehensive tests for app/enterprise/compliance.py — covering the 38% gap.

Covers lines not yet exercised:
  - ComplianceController.configure_services() + data_export with services
  - request_data_export with goal_service in memory mode (goals_data)
  - request_data_export with audit_log service
  - request_data_export with agent_store and schedule_store
  - request_data_deletion: scheduled_at is 30 days from now
  - retention_sweep sweeps old AND keeps new records
  - get_export_payload returns payload for ready request; None for bad id
  - get_export_status in-memory fallback
  - execute_data_deletion_async with db=None returns error
  - get_data_residency fields and no hardcoded gdpr_compliant=True
  - DataExportRequest default fields
  - _db_save_request / _db_load_request no-op when db is None
  - _db_save_deletion no-op when db is None
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.enterprise.compliance import ComplianceController, DataExportRequest
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="compliance-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")
T2 = TenantContext(tenant_id="compliance-t2", plan=PlanTier.STARTER, api_key_id="k2")


# ---------------------------------------------------------------------------
# DataExportRequest — defaults
# ---------------------------------------------------------------------------


def test_data_export_request_defaults() -> None:
    req = DataExportRequest(tenant_id="t1")
    assert req.tenant_id == "t1"
    assert req.status == "pending"
    assert req.download_url == ""
    assert isinstance(req.request_id, str) and len(req.request_id) > 0
    assert isinstance(req.created_at, str)


# ---------------------------------------------------------------------------
# ComplianceController — basic instantiation
# ---------------------------------------------------------------------------


def test_controller_instantiates_with_no_args() -> None:
    ctrl = ComplianceController()
    assert ctrl._db is None
    assert len(ctrl._export_requests) == 0
    assert len(ctrl._deleted_tenants) == 0


def test_configure_services_wires_all() -> None:
    ctrl = ComplianceController()
    mock_goal_service = MagicMock()
    mock_audit_log = MagicMock()
    mock_tenant_service = MagicMock()
    mock_agent_store = MagicMock()
    mock_schedule_store = MagicMock()
    mock_knowledge_store = MagicMock()
    mock_db = MagicMock()

    ctrl.configure_services(
        goal_service=mock_goal_service,
        audit_log=mock_audit_log,
        tenant_service=mock_tenant_service,
        agent_store=mock_agent_store,
        schedule_store=mock_schedule_store,
        knowledge_store=mock_knowledge_store,
        db=mock_db,
    )

    assert ctrl._goal_service is mock_goal_service
    assert ctrl._audit_log is mock_audit_log
    assert ctrl._agent_store is mock_agent_store
    assert ctrl._db is mock_db


# ---------------------------------------------------------------------------
# request_data_export — in-memory (no DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_data_export_basic_structure() -> None:
    ctrl = ComplianceController()
    req = await ctrl.request_data_export(tenant_ctx=T)
    assert req.status == "ready"
    assert req.tenant_id == T.tenant_id
    assert req.download_url.startswith("/compliance/export/")
    assert isinstance(req.payload, dict)
    assert "data" in req.payload


@pytest.mark.asyncio
async def test_request_data_export_payload_has_required_keys() -> None:
    ctrl = ComplianceController()
    req = await ctrl.request_data_export(tenant_ctx=T)
    data = req.payload["data"]
    assert "goals" in data
    assert "audit_entries" in data
    assert "agents" in data
    assert "schedules" in data
    assert "api_keys" in data
    # Never export raw keys
    assert data["api_keys"] == []


@pytest.mark.asyncio
async def test_request_data_export_stored_in_memory() -> None:
    ctrl = ComplianceController()
    req = await ctrl.request_data_export(tenant_ctx=T)
    assert req.request_id in ctrl._export_requests


@pytest.mark.asyncio
async def test_request_data_export_with_goal_service_memory_mode() -> None:
    """With a _goals dict on goal_service (no DB), goals are extracted."""
    ctrl = ComplianceController()

    mock_goal = MagicMock()
    mock_goal.tenant_id = T.tenant_id
    mock_goal.goal_text = "Do something"
    mock_goal.status = "completed"
    mock_goal.created_at = "2024-01-01T00:00:00"

    mock_goal_service = MagicMock()
    mock_goal_service._db_session_factory = None  # force memory path
    mock_goal_service._goals = {
        "goal-1": mock_goal,
    }
    ctrl.configure_services(goal_service=mock_goal_service)

    req = await ctrl.request_data_export(tenant_ctx=T)
    assert req.status == "ready"
    assert len(req.payload["data"]["goals"]) == 1
    assert req.payload["data"]["goals"][0]["goal_id"] == "goal-1"


@pytest.mark.asyncio
async def test_request_data_export_with_audit_log() -> None:
    ctrl = ComplianceController()

    audit_entry = MagicMock()
    audit_entry.event_id = "evt-1"
    audit_entry.goal_id = "goal-1"
    audit_entry.tool_name = "github"
    audit_entry.outcome = "success"

    mock_audit = MagicMock()
    mock_audit.query = MagicMock(return_value=[audit_entry])
    ctrl.configure_services(audit_log=mock_audit)

    req = await ctrl.request_data_export(tenant_ctx=T)
    assert len(req.payload["data"]["audit_entries"]) == 1
    assert req.payload["data"]["audit_entries"][0]["event_id"] == "evt-1"


@pytest.mark.asyncio
async def test_request_data_export_with_agent_store() -> None:
    ctrl = ComplianceController()
    mock_agent_store = MagicMock()
    mock_agent_store.list_all = MagicMock(return_value=[
        {"agent_id": "agent-1", "name": "Bug Fixer"},
    ])
    ctrl.configure_services(agent_store=mock_agent_store)

    req = await ctrl.request_data_export(tenant_ctx=T)
    assert len(req.payload["data"]["agents"]) == 1
    assert req.payload["data"]["agents"][0]["agent_id"] == "agent-1"


@pytest.mark.asyncio
async def test_request_data_export_with_schedule_store() -> None:
    ctrl = ComplianceController()
    mock_sched_store = MagicMock()
    mock_sched_store.list_all = MagicMock(return_value=[
        {"schedule_id": "sched-1", "goal_id": "goal-1"},
    ])
    ctrl.configure_services(schedule_store=mock_sched_store)

    req = await ctrl.request_data_export(tenant_ctx=T)
    assert len(req.payload["data"]["schedules"]) == 1


@pytest.mark.asyncio
async def test_request_data_export_service_failure_does_not_raise() -> None:
    """Failing services must not surface exceptions."""
    ctrl = ComplianceController()
    bad_agent_store = MagicMock()
    bad_agent_store.list_all = MagicMock(side_effect=RuntimeError("DB down"))
    ctrl.configure_services(agent_store=bad_agent_store)

    # Must not raise
    req = await ctrl.request_data_export(tenant_ctx=T)
    assert req.status == "ready"


# ---------------------------------------------------------------------------
# get_export_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_export_status_returns_request() -> None:
    ctrl = ComplianceController()
    req = await ctrl.request_data_export(tenant_ctx=T)
    found = await ctrl.get_export_status(request_id=req.request_id, tenant_ctx=T)
    assert found is not None
    assert found.request_id == req.request_id


@pytest.mark.asyncio
async def test_get_export_status_wrong_tenant_returns_none() -> None:
    ctrl = ComplianceController()
    req = await ctrl.request_data_export(tenant_ctx=T)
    found = await ctrl.get_export_status(request_id=req.request_id, tenant_ctx=T2)
    assert found is None


@pytest.mark.asyncio
async def test_get_export_status_nonexistent_returns_none() -> None:
    ctrl = ComplianceController()
    found = await ctrl.get_export_status(request_id="ghost-id", tenant_ctx=T)
    assert found is None


# ---------------------------------------------------------------------------
# get_export_payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_export_payload_returns_payload_for_ready() -> None:
    ctrl = ComplianceController()
    req = await ctrl.request_data_export(tenant_ctx=T)
    payload = await ctrl.get_export_payload(request_id=req.request_id, tenant_ctx=T)
    assert payload is not None
    assert "data" in payload


@pytest.mark.asyncio
async def test_get_export_payload_returns_none_for_nonexistent() -> None:
    ctrl = ComplianceController()
    payload = await ctrl.get_export_payload(request_id="ghost-id", tenant_ctx=T)
    assert payload is None


@pytest.mark.asyncio
async def test_get_export_payload_returns_none_for_wrong_tenant() -> None:
    ctrl = ComplianceController()
    req = await ctrl.request_data_export(tenant_ctx=T)
    payload = await ctrl.get_export_payload(request_id=req.request_id, tenant_ctx=T2)
    assert payload is None


# ---------------------------------------------------------------------------
# request_data_deletion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_data_deletion_marks_tenant() -> None:
    ctrl = ComplianceController()
    result = await ctrl.request_data_deletion(tenant_ctx=T)
    assert result["deletion_scheduled"] is True
    assert T.tenant_id in ctrl._deleted_tenants


@pytest.mark.asyncio
async def test_request_data_deletion_scheduled_30_days_out() -> None:
    ctrl = ComplianceController()
    before = datetime.now(UTC)
    result = await ctrl.request_data_deletion(tenant_ctx=T)
    scheduled = datetime.fromisoformat(result["scheduled_at"])
    # Should be approximately 30 days from now
    delta = scheduled - before
    assert 29 <= delta.days <= 31


@pytest.mark.asyncio
async def test_request_data_deletion_note_mentions_gdpr() -> None:
    ctrl = ComplianceController()
    result = await ctrl.request_data_deletion(tenant_ctx=T)
    assert "GDPR" in result["note"] or "gdpr" in result["note"].lower()


# ---------------------------------------------------------------------------
# retention_sweep
# ---------------------------------------------------------------------------


def test_retention_sweep_sweeps_old_records() -> None:
    ctrl = ComplianceController()
    # Manually inject an old request
    old_req = DataExportRequest(tenant_id="t1")
    old_req.created_at = (datetime.now(UTC) - timedelta(days=100)).isoformat()
    ctrl._export_requests["old-req"] = old_req

    result = ctrl.retention_sweep(retention_days=90)
    assert result["records_swept"] == 1
    assert result["retention_days"] == 90


def test_retention_sweep_keeps_recent_records() -> None:
    ctrl = ComplianceController()
    recent_req = DataExportRequest(tenant_id="t1")
    recent_req.created_at = datetime.now(UTC).isoformat()
    ctrl._export_requests["recent-req"] = recent_req

    result = ctrl.retention_sweep(retention_days=90)
    assert result["records_swept"] == 0


def test_retention_sweep_custom_retention_days() -> None:
    ctrl = ComplianceController()
    old_req = DataExportRequest(tenant_id="t1")
    old_req.created_at = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    ctrl._export_requests["slightly-old"] = old_req

    result = ctrl.retention_sweep(retention_days=7)
    assert result["records_swept"] == 1
    assert result["retention_days"] == 7


def test_retention_sweep_empty_store_returns_zero() -> None:
    ctrl = ComplianceController()
    result = ctrl.retention_sweep(retention_days=30)
    assert result["records_swept"] == 0


# ---------------------------------------------------------------------------
# get_data_residency
# ---------------------------------------------------------------------------


def test_get_data_residency_returns_expected_fields() -> None:
    ctrl = ComplianceController()
    result = ctrl.get_data_residency(tenant_ctx=T)
    assert result["tenant_id"] == T.tenant_id
    assert "primary_region" in result
    assert "backup_region" in result
    assert "gdpr_compliant" in result
    assert "pci_dss_scope" in result
    assert "soc2_type2" in result


def test_get_data_residency_gdpr_not_hardcoded_true() -> None:
    """FIX: gdpr_compliant must NOT be hardcoded True."""
    ctrl = ComplianceController()
    result = ctrl.get_data_residency(tenant_ctx=T)
    # Per the fix in the code, these should NOT be hardcoded True
    assert result["gdpr_compliant"] is False
    assert result["soc2_type2"] is False


def test_get_data_residency_note_references_api() -> None:
    ctrl = ComplianceController()
    result = ctrl.get_data_residency(tenant_ctx=T)
    assert "note" in result
    assert "/enterprise/compliance" in result["note"] or "compliance" in result["note"].lower()


# ---------------------------------------------------------------------------
# execute_data_deletion_async — no-DB guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_data_deletion_async_no_db_returns_error() -> None:
    ctrl = ComplianceController()
    result = await ctrl.execute_data_deletion_async(tenant_ctx=T, db=None)
    assert "error" in result
    assert result.get("deleted_rows", 0) == 0


# ---------------------------------------------------------------------------
# _db_save_request / _db_load_request / _db_save_deletion — no-op without db
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_save_request_noop_without_db() -> None:
    ctrl = ComplianceController()
    req = DataExportRequest(tenant_id="t1")
    # Should complete without raising
    await ctrl._db_save_request(req)


@pytest.mark.asyncio
async def test_db_load_request_returns_none_without_db() -> None:
    ctrl = ComplianceController()
    result = await ctrl._db_load_request("req-id", "t1")
    assert result is None


@pytest.mark.asyncio
async def test_db_save_deletion_noop_without_db() -> None:
    ctrl = ComplianceController()
    # Should complete without raising
    await ctrl._db_save_deletion("tenant-1")


# ---------------------------------------------------------------------------
# export_format_version in payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_payload_has_version() -> None:
    ctrl = ComplianceController()
    req = await ctrl.request_data_export(tenant_ctx=T)
    assert req.payload.get("export_format_version") == "1.0"
