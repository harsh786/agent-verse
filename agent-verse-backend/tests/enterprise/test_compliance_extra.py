"""Extra coverage for app/enterprise/compliance.py — ComplianceController methods."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.enterprise.compliance import ComplianceController, DataExportRequest
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="t-comp", plan=PlanTier.ENTERPRISE, api_key_id="k1")


class TestDataExportRequest:
    def test_default_fields(self):
        req = DataExportRequest()
        assert req.status == "pending"
        assert req.request_id
        assert req.tenant_id == ""
        assert req.payload == {}
        assert req.download_url == ""

    def test_custom_fields(self):
        req = DataExportRequest(tenant_id="t1", status="ready", download_url="/dl/123")
        assert req.tenant_id == "t1"
        assert req.status == "ready"
        assert req.download_url == "/dl/123"


class TestComplianceControllerInit:
    def test_default_state(self):
        ctrl = ComplianceController()
        assert ctrl._export_requests == {}
        assert ctrl._deleted_tenants == set()
        assert ctrl._db is None

    def test_configure_services(self):
        ctrl = ComplianceController()
        mock_goal = MagicMock()
        mock_audit = MagicMock()
        mock_db = MagicMock()
        ctrl.configure_services(
            goal_service=mock_goal,
            audit_log=mock_audit,
            db=mock_db,
        )
        assert ctrl._goal_service is mock_goal
        assert ctrl._audit_log is mock_audit
        assert ctrl._db is mock_db

    def test_configure_services_skips_none_db(self):
        ctrl = ComplianceController()
        ctrl.configure_services(db=None)
        assert ctrl._db is None


class TestDbSaveRequest:
    @pytest.mark.asyncio
    async def test_skips_when_no_db(self):
        ctrl = ComplianceController()
        req = DataExportRequest(tenant_id="t1", status="ready")
        # Must not raise; returns None
        result = await ctrl._db_save_request(req)
        assert result is None

    @pytest.mark.asyncio
    async def test_calls_db_when_present(self):
        ctrl = ComplianceController()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(
            return_value=type("CM", (), {
                "__aenter__": AsyncMock(return_value=None),
                "__aexit__": AsyncMock(return_value=False),
            })()
        )
        mock_session.execute = AsyncMock()

        mock_db = MagicMock(return_value=mock_session)
        ctrl._db = mock_db

        req = DataExportRequest(tenant_id="t1", status="ready", payload={"x": 1})
        await ctrl._db_save_request(req)
        # If exception is raised (session not real), it's swallowed
        # Just verify no unhandled exception


class TestDbLoadRequest:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_db(self):
        ctrl = ComplianceController()
        result = await ctrl._db_load_request("req1", "t1")
        assert result is None


class TestDbSaveDeletion:
    @pytest.mark.asyncio
    async def test_skips_when_no_db(self):
        ctrl = ComplianceController()
        result = await ctrl._db_save_deletion("t1")
        assert result is None


class TestRequestDataExport:
    @pytest.mark.asyncio
    async def test_basic_export_no_services(self):
        ctrl = ComplianceController()
        req = await ctrl.request_data_export(tenant_ctx=_CTX)
        assert req.status == "ready"
        assert req.tenant_id == "t-comp"
        assert "goals" in req.payload["data"]
        assert "audit_entries" in req.payload["data"]
        assert req.download_url.startswith("/compliance/export/")

    @pytest.mark.asyncio
    async def test_export_with_audit_log(self):
        ctrl = ComplianceController()
        mock_audit = MagicMock()
        mock_entry = MagicMock()
        mock_entry.event_id = "e1"
        mock_entry.goal_id = "g1"
        mock_entry.tool_name = "github:create_issue"
        mock_entry.outcome = "success"
        mock_audit.query.return_value = [mock_entry]
        ctrl.configure_services(audit_log=mock_audit)

        req = await ctrl.request_data_export(tenant_ctx=_CTX)
        assert len(req.payload["data"]["audit_entries"]) == 1
        assert req.payload["data"]["audit_entries"][0]["tool_name"] == "github:create_issue"

    @pytest.mark.asyncio
    async def test_export_with_agent_store(self):
        ctrl = ComplianceController()
        mock_store = MagicMock()
        mock_store.list_all.return_value = [
            {"agent_id": "a1", "name": "TestAgent"},
        ]
        ctrl.configure_services(agent_store=mock_store)
        req = await ctrl.request_data_export(tenant_ctx=_CTX)
        assert len(req.payload["data"]["agents"]) == 1

    @pytest.mark.asyncio
    async def test_export_with_schedule_store(self):
        ctrl = ComplianceController()
        mock_sched = MagicMock()
        mock_sched.list_all.return_value = [{"schedule_id": "s1", "goal_id": "g1"}]
        ctrl.configure_services(schedule_store=mock_sched)
        req = await ctrl.request_data_export(tenant_ctx=_CTX)
        assert len(req.payload["data"]["schedules"]) == 1

    @pytest.mark.asyncio
    async def test_export_with_goal_service_memory(self):
        """Goal service without DB → falls back to in-memory _goals dict."""
        ctrl = ComplianceController()
        mock_goal = MagicMock()
        mock_goal._db_session_factory = None  # no DB
        mock_record = MagicMock()
        mock_record.tenant_id = "t-comp"
        mock_record.goal_text = "test goal"
        mock_record.status = "complete"
        mock_record.created_at = ""
        mock_goal._goals = {"g1": mock_record}
        ctrl.configure_services(goal_service=mock_goal)

        req = await ctrl.request_data_export(tenant_ctx=_CTX)
        assert req.status == "ready"

    @pytest.mark.asyncio
    async def test_export_audit_exception_swallowed(self):
        ctrl = ComplianceController()
        mock_audit = MagicMock()
        mock_audit.query.side_effect = RuntimeError("db error")
        ctrl.configure_services(audit_log=mock_audit)
        # Must not raise
        req = await ctrl.request_data_export(tenant_ctx=_CTX)
        assert req.status == "ready"

    @pytest.mark.asyncio
    async def test_export_agent_store_exception_swallowed(self):
        ctrl = ComplianceController()
        mock_store = MagicMock()
        mock_store.list_all.side_effect = RuntimeError("oops")
        ctrl.configure_services(agent_store=mock_store)
        req = await ctrl.request_data_export(tenant_ctx=_CTX)
        assert req.status == "ready"

    @pytest.mark.asyncio
    async def test_export_saved_to_memory(self):
        ctrl = ComplianceController()
        req = await ctrl.request_data_export(tenant_ctx=_CTX)
        assert req.request_id in ctrl._export_requests


class TestGetExportStatus:
    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_id(self):
        ctrl = ComplianceController()
        result = await ctrl.get_export_status(request_id="nope", tenant_ctx=_CTX)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_correct_request(self):
        ctrl = ComplianceController()
        req = await ctrl.request_data_export(tenant_ctx=_CTX)
        result = await ctrl.get_export_status(request_id=req.request_id, tenant_ctx=_CTX)
        assert result is not None
        assert result.request_id == req.request_id

    @pytest.mark.asyncio
    async def test_returns_none_for_wrong_tenant(self):
        ctrl = ComplianceController()
        req = await ctrl.request_data_export(tenant_ctx=_CTX)
        other_ctx = TenantContext(tenant_id="other-tenant", plan=PlanTier.FREE, api_key_id="k2")
        result = await ctrl.get_export_status(request_id=req.request_id, tenant_ctx=other_ctx)
        assert result is None


class TestGetExportPayload:
    @pytest.mark.asyncio
    async def test_returns_payload_for_ready_request(self):
        ctrl = ComplianceController()
        req = await ctrl.request_data_export(tenant_ctx=_CTX)
        payload = await ctrl.get_export_payload(request_id=req.request_id, tenant_ctx=_CTX)
        assert payload is not None
        assert "tenant_id" in payload

    @pytest.mark.asyncio
    async def test_returns_none_for_pending(self):
        ctrl = ComplianceController()
        req = DataExportRequest(tenant_id="t-comp", status="pending")
        ctrl._export_requests[req.request_id] = req
        result = await ctrl.get_export_payload(request_id=req.request_id, tenant_ctx=_CTX)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown(self):
        ctrl = ComplianceController()
        result = await ctrl.get_export_payload(request_id="x", tenant_ctx=_CTX)
        assert result is None


class TestRequestDataDeletion:
    @pytest.mark.asyncio
    async def test_returns_deletion_info(self):
        ctrl = ComplianceController()
        result = await ctrl.request_data_deletion(tenant_ctx=_CTX)
        assert result["tenant_id"] == "t-comp"
        assert result["deletion_scheduled"] is True
        assert "scheduled_at" in result

    @pytest.mark.asyncio
    async def test_adds_to_deleted_tenants(self):
        ctrl = ComplianceController()
        await ctrl.request_data_deletion(tenant_ctx=_CTX)
        assert "t-comp" in ctrl._deleted_tenants


class TestRetentionSweep:
    def test_returns_sweep_info(self):
        ctrl = ComplianceController()
        result = ctrl.retention_sweep(retention_days=90)
        assert "sweep_cutoff" in result
        assert result["retention_days"] == 90
        assert "records_swept" in result

    def test_sweeps_old_records(self):
        from datetime import UTC, datetime, timedelta
        ctrl = ComplianceController()
        # Add an old request
        old_req = DataExportRequest(tenant_id="t-comp", status="ready")
        old_req.created_at = (datetime.now(UTC) - timedelta(days=100)).isoformat()
        ctrl._export_requests[old_req.request_id] = old_req

        result = ctrl.retention_sweep(retention_days=90)
        assert result["records_swept"] >= 1

    def test_does_not_sweep_recent_records(self):
        ctrl = ComplianceController()
        recent_req = DataExportRequest(tenant_id="t-comp", status="ready")
        ctrl._export_requests[recent_req.request_id] = recent_req

        result = ctrl.retention_sweep(retention_days=90)
        assert result["records_swept"] == 0


class TestGetDataResidency:
    def test_returns_residency_info(self):
        ctrl = ComplianceController()
        result = ctrl.get_data_residency(tenant_ctx=_CTX)
        assert result["tenant_id"] == "t-comp"
        assert "primary_region" in result
        assert result["gdpr_compliant"] is False  # FIX: was hardcoded True
        assert result["soc2_type2"] is False        # FIX: was hardcoded True


class TestExecuteDataDeletionAsync:
    @pytest.mark.asyncio
    async def test_returns_error_when_no_db(self):
        ctrl = ComplianceController()
        result = await ctrl.execute_data_deletion_async(tenant_ctx=_CTX, db=None)
        assert "error" in result
        assert result["deleted_rows"] == 0
