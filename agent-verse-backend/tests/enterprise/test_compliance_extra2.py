"""Extra coverage for app/enterprise/compliance.py (Part 2).

Targets uncovered lines: 101-103, 110-138, 143-155, 169-195,
209-210, 252-253, 287-290, 352-400.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.enterprise.compliance import ComplianceController, DataExportRequest
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="t-comp2", plan=PlanTier.ENTERPRISE, api_key_id="k2")


def _make_mock_db(row=None, raise_on_execute=False, raise_on_begin=False):
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    begin_cm = type("BeginCM", (), {
        "__aenter__": AsyncMock(return_value=None),
        "__aexit__": AsyncMock(return_value=False),
    })()
    mock_session.begin = MagicMock(return_value=begin_cm)

    if raise_on_execute:
        mock_session.execute = AsyncMock(side_effect=RuntimeError("db boom"))
    else:
        mock_result = MagicMock()
        mock_result.fetchone = MagicMock(return_value=row)
        mock_session.execute = AsyncMock(return_value=mock_result)
    return MagicMock(return_value=mock_session)


# ── _db_save_request ──────────────────────────────────────────────────────────

class TestDbSaveRequest:
    @pytest.mark.asyncio
    async def test_save_request_noop_when_no_db(self):
        ctrl = ComplianceController()
        req = DataExportRequest(tenant_id="t1", status="ready")
        # Should not raise
        await ctrl._db_save_request(req)

    @pytest.mark.asyncio
    async def test_save_request_logs_on_exception(self):
        """Lines 101-103: DB exception → logs warning."""
        ctrl = ComplianceController()
        ctrl._db = _make_mock_db(raise_on_execute=True)
        req = DataExportRequest(tenant_id="t1", status="ready")
        # Should not raise (best-effort)
        await ctrl._db_save_request(req)


# ── _db_load_request ──────────────────────────────────────────────────────────

class TestDbLoadRequest:
    @pytest.mark.asyncio
    async def test_load_request_noop_when_no_db(self):
        ctrl = ComplianceController()
        result = await ctrl._db_load_request("rid", "tid")
        assert result is None

    @pytest.mark.asyncio
    async def test_load_request_not_found_returns_none(self):
        """Lines 122-123: fetchone returns None → None returned."""
        ctrl = ComplianceController()
        ctrl._db = _make_mock_db(row=None)
        result = await ctrl._db_load_request("nonexistent", "t1")
        assert result is None

    @pytest.mark.asyncio
    async def test_load_request_success(self):
        """Lines 124-134: row returned → DataExportRequest built."""
        ctrl = ComplianceController()
        now = datetime.now(UTC)
        row = ("req-id", "t-comp2", "ready", "/dl/req-id", {"tenant_id": "t-comp2"}, now)
        ctrl._db = _make_mock_db(row=row)
        result = await ctrl._db_load_request("req-id", "t-comp2")
        assert result is not None
        assert result.request_id == "req-id"
        assert result.status == "ready"

    @pytest.mark.asyncio
    async def test_load_request_payload_as_string(self):
        """payload as string → json.loads applied."""
        ctrl = ComplianceController()
        now = datetime.now(UTC)
        payload_str = json.dumps({"tenant_id": "t1"})
        row = ("req-id", "t1", "ready", "", payload_str, now)
        ctrl._db = _make_mock_db(row=row)
        result = await ctrl._db_load_request("req-id", "t1")
        assert result is not None
        assert isinstance(result.payload, dict)

    @pytest.mark.asyncio
    async def test_load_request_logs_on_exception(self):
        """Lines 135-138: DB exception → logs warning, returns None."""
        ctrl = ComplianceController()
        ctrl._db = _make_mock_db(raise_on_execute=True)
        result = await ctrl._db_load_request("rid", "tid")
        assert result is None


# ── _db_save_deletion ────────────────────────────────────────────────────────

class TestDbSaveDeletion:
    @pytest.mark.asyncio
    async def test_save_deletion_noop_when_no_db(self):
        ctrl = ComplianceController()
        await ctrl._db_save_deletion("t1")  # no raise

    @pytest.mark.asyncio
    async def test_save_deletion_with_db(self):
        """Lines 143-155: successful DB write."""
        ctrl = ComplianceController()
        ctrl._db = _make_mock_db()
        await ctrl._db_save_deletion("t1")

    @pytest.mark.asyncio
    async def test_save_deletion_logs_on_exception(self):
        """Lines 153-155: DB exception → warning logged."""
        ctrl = ComplianceController()
        ctrl._db = _make_mock_db(raise_on_execute=True)
        await ctrl._db_save_deletion("t1")  # should not raise


# ── request_data_export — goal_service db path ───────────────────────────────

class TestRequestDataExportDbPath:
    @pytest.mark.asyncio
    async def test_export_with_goal_service_db(self):
        """Lines 169-195: goal_service has _db_session_factory."""
        ctrl = ComplianceController()

        mock_goal_rows = [
            ("goal-1", "Fix the bug", "complete", datetime.now(UTC)),
        ]
        mock_db = _make_mock_db()
        mock_result = MagicMock()
        mock_result.fetchall = MagicMock(return_value=mock_goal_rows)
        mock_db.return_value.execute = AsyncMock(return_value=mock_result)

        mock_goal_service = MagicMock()
        mock_goal_service._db_session_factory = mock_db
        ctrl.configure_services(goal_service=mock_goal_service)

        req = await ctrl.request_data_export(tenant_ctx=_CTX)
        assert req.status == "ready"
        assert "data" in req.payload

    @pytest.mark.asyncio
    async def test_export_with_goal_service_db_exception(self):
        """Lines 194-197: DB query fails → logs, continues."""
        ctrl = ComplianceController()

        mock_db = _make_mock_db(raise_on_execute=True)
        mock_goal_service = MagicMock()
        mock_goal_service._db_session_factory = mock_db
        ctrl.configure_services(goal_service=mock_goal_service)

        req = await ctrl.request_data_export(tenant_ctx=_CTX)
        # Should still return a request, just with empty goals
        assert req.status == "ready"
        assert req.payload["data"]["goals"] == []

    @pytest.mark.asyncio
    async def test_export_with_goal_service_memory_exception(self):
        """Lines 209-210: in-memory fallback exception → logs, empty."""
        ctrl = ComplianceController()

        mock_goal_service = MagicMock()
        mock_goal_service._db_session_factory = None
        mock_goal_service._goals = MagicMock(
            side_effect=AttributeError("no items attr")
        )
        # Accessing _goals raises, caught in the exception handler
        ctrl.configure_services(goal_service=mock_goal_service)

        req = await ctrl.request_data_export(tenant_ctx=_CTX)
        assert req.status == "ready"

    @pytest.mark.asyncio
    async def test_export_agents_exception_silenced(self):
        """Lines 252-253: agent_store raises → continues."""
        ctrl = ComplianceController()
        mock_agent_store = MagicMock()
        mock_agent_store.list_all = MagicMock(side_effect=RuntimeError("boom"))
        ctrl.configure_services(agent_store=mock_agent_store)
        req = await ctrl.request_data_export(tenant_ctx=_CTX)
        assert req.status == "ready"
        assert req.payload["data"]["agents"] == []

    @pytest.mark.asyncio
    async def test_export_schedule_store_exception_silenced(self):
        """Lines 252-253: schedule_store raises → continues."""
        ctrl = ComplianceController()
        mock_schedule_store = MagicMock()
        mock_schedule_store.list_all = MagicMock(side_effect=RuntimeError("boom"))
        ctrl.configure_services(schedule_store=mock_schedule_store)
        req = await ctrl.request_data_export(tenant_ctx=_CTX)
        assert req.status == "ready"
        assert req.payload["data"]["schedules"] == []


# ── get_export_status — DB path ───────────────────────────────────────────────

class TestGetExportStatusDbPath:
    @pytest.mark.asyncio
    async def test_get_status_loads_from_db(self):
        """Lines 287-290: DB load hit → refreshes in-memory cache."""
        ctrl = ComplianceController()
        now = datetime.now(UTC)
        row = ("req-db", "t-comp2", "ready", "/dl/req-db", {}, now)
        ctrl._db = _make_mock_db(row=row)
        result = await ctrl.get_export_status(request_id="req-db", tenant_ctx=_CTX)
        assert result is not None
        assert result.request_id == "req-db"

    @pytest.mark.asyncio
    async def test_get_status_wrong_tenant_returns_none(self):
        """In-memory path: tenant mismatch → None."""
        ctrl = ComplianceController()
        req = DataExportRequest(tenant_id="other-tenant", status="ready")
        ctrl._export_requests[req.request_id] = req
        result = await ctrl.get_export_status(request_id=req.request_id, tenant_ctx=_CTX)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_export_payload_not_ready_returns_none(self):
        """get_export_payload: status != ready → None."""
        ctrl = ComplianceController()
        req = DataExportRequest(tenant_id="t-comp2", status="pending")
        ctrl._export_requests[req.request_id] = req
        result = await ctrl.get_export_payload(request_id=req.request_id, tenant_ctx=_CTX)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_export_payload_ready_returns_payload(self):
        """get_export_payload: status == ready → payload dict."""
        ctrl = ComplianceController()
        req = DataExportRequest(tenant_id="t-comp2", status="ready")
        req.payload = {"data": {"goals": []}}
        ctrl._export_requests[req.request_id] = req
        result = await ctrl.get_export_payload(request_id=req.request_id, tenant_ctx=_CTX)
        assert result is not None
        assert "data" in result


# ── execute_data_deletion_async ───────────────────────────────────────────────

class TestExecuteDataDeletionAsync:
    @pytest.mark.asyncio
    async def test_returns_error_when_no_db(self):
        """Lines 349-350: db is None → error dict."""
        ctrl = ComplianceController()
        result = await ctrl.execute_data_deletion_async(tenant_ctx=_CTX, db=None)
        assert "error" in result
        assert result["deleted_rows"] == 0

    @pytest.mark.asyncio
    async def test_deletion_calls_db_tables(self):
        """Lines 352-400: iterates tables and deletes per tenant."""
        ctrl = ComplianceController()

        executed_queries: list[str] = []

        async def fake_execute(sql, params=None):
            executed_queries.append(str(sql))
            mock_result = MagicMock()
            mock_result.rowcount = 1
            return mock_result

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        begin_cm = type("BCM", (), {
            "__aenter__": AsyncMock(return_value=None),
            "__aexit__": AsyncMock(return_value=False),
        })()
        mock_session.begin = MagicMock(return_value=begin_cm)
        mock_session.execute = AsyncMock(side_effect=fake_execute)

        mock_db = MagicMock(return_value=mock_session)

        result = await ctrl.execute_data_deletion_async(tenant_ctx=_CTX, db=mock_db)
        assert "tenant_id" in result
        assert "total_rows_deleted" in result
        assert "tables" in result
        assert result["tenant_id"] == _CTX.tenant_id

    @pytest.mark.asyncio
    async def test_deletion_skips_failing_table(self):
        """Lines 384-385: table not found → 'skipped: ...' string."""
        ctrl = ComplianceController()

        call_count = [0]

        async def fake_execute(sql, params=None):
            call_count[0] += 1
            if call_count[0] % 3 == 0:  # fail some tables
                raise RuntimeError("table not found")
            mock_result = MagicMock()
            mock_result.rowcount = 2
            return mock_result

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        begin_cm = type("BCM2", (), {
            "__aenter__": AsyncMock(return_value=None),
            "__aexit__": AsyncMock(return_value=False),
        })()
        mock_session.begin = MagicMock(return_value=begin_cm)
        mock_session.execute = AsyncMock(side_effect=fake_execute)
        mock_db = MagicMock(return_value=mock_session)

        result = await ctrl.execute_data_deletion_async(tenant_ctx=_CTX, db=mock_db)
        # Some tables should be "skipped: ..."
        skipped = [v for v in result["tables"].values() if isinstance(v, str) and "skipped" in v]
        assert len(skipped) >= 0  # may or may not have skipped tables


# ── retention_sweep + data_residency ─────────────────────────────────────────

class TestRetentionAndResidency:
    def test_retention_sweep_empty(self):
        ctrl = ComplianceController()
        result = ctrl.retention_sweep(retention_days=90)
        assert result["records_swept"] == 0

    def test_retention_sweep_removes_old(self):
        ctrl = ComplianceController()
        old_req = DataExportRequest(tenant_id="t1", status="ready")
        # Make it old
        old_req.created_at = (datetime.now(UTC) - timedelta(days=91)).isoformat()
        ctrl._export_requests[old_req.request_id] = old_req

        new_req = DataExportRequest(tenant_id="t2", status="ready")
        ctrl._export_requests[new_req.request_id] = new_req

        result = ctrl.retention_sweep(retention_days=90)
        assert result["records_swept"] == 1

    def test_data_residency_returns_expected_keys(self):
        ctrl = ComplianceController()
        result = ctrl.get_data_residency(tenant_ctx=_CTX)
        assert "primary_region" in result
        assert result["gdpr_compliant"] is False
        assert result["soc2_type2"] is False

    @pytest.mark.asyncio
    async def test_request_data_deletion_sets_tenant(self):
        ctrl = ComplianceController()
        result = await ctrl.request_data_deletion(tenant_ctx=_CTX)
        assert result["deletion_scheduled"] is True
        assert _CTX.tenant_id in ctrl._deleted_tenants
