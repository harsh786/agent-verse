"""Tests for compliance data export with structured payload and service wiring."""
from __future__ import annotations

import pytest

from app.enterprise.compliance import ComplianceController
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="comp-t1", plan=PlanTier.ENTERPRISE, api_key_id="ck1")


@pytest.mark.asyncio
async def test_compliance_export_has_structured_payload():
    """Export payload contains structured data section with tenant_profile."""
    cc = ComplianceController()
    req = await cc.request_data_export(tenant_ctx=T)
    assert req.status == "ready"
    assert "data" in req.payload
    assert "tenant_profile" in req.payload["data"]
    assert req.payload["data"]["tenant_profile"]["tenant_id"] == T.tenant_id
    assert req.payload["data"]["tenant_profile"]["plan"] == T.plan.value


@pytest.mark.asyncio
async def test_compliance_export_payload_has_all_data_keys():
    """Export payload data section contains all expected collection keys."""
    cc = ComplianceController()
    req = await cc.request_data_export(tenant_ctx=T)
    data = req.payload["data"]
    for expected_key in ("goals", "audit_entries", "api_keys", "agents", "schedules"):
        assert expected_key in data, f"Missing key: {expected_key}"
        assert isinstance(data[expected_key], list)


@pytest.mark.asyncio
async def test_compliance_export_top_level_fields():
    """Export payload always includes tenant_id, plan, and timestamp at top level."""
    cc = ComplianceController()
    req = await cc.request_data_export(tenant_ctx=T)
    assert req.payload["tenant_id"] == T.tenant_id
    assert req.payload["plan"] == T.plan.value
    assert "export_timestamp" in req.payload
    assert "export_format_version" in req.payload


def test_compliance_configure_services_no_crash():
    """configure_services() accepts None for any argument without raising."""
    cc = ComplianceController()
    cc.configure_services(goal_service=None, audit_log=None)  # partial wiring
    cc.configure_services()  # all defaults


def test_compliance_configure_services_wires_references():
    """configure_services() stores references accessible as private attributes."""
    cc = ComplianceController()

    class FakeGoalSvc:
        pass

    class FakeAuditLog:
        pass

    svc = FakeGoalSvc()
    log = FakeAuditLog()
    cc.configure_services(goal_service=svc, audit_log=log)
    assert cc._goal_service is svc
    assert cc._audit_log is log
    assert cc._tenant_service is None  # not wired → stays None


def test_compliance_residency():
    """get_data_residency returns region fields; compliance is no longer hardcoded."""
    cc = ComplianceController()
    residency = cc.get_data_residency(tenant_ctx=T)
    # FIX: gdpr_compliant and soc2_type2 must NOT be hardcoded True.
    # They must be False (unclaimed) to avoid false regulatory assertions.
    assert residency["gdpr_compliant"] is False, "gdpr_compliant must not be hardcoded True"
    assert residency["soc2_type2"] is False, "soc2_type2 must not be hardcoded True"
    assert "primary_region" in residency
    assert residency["tenant_id"] == T.tenant_id
    assert "note" in residency  # Points to dynamic compliance endpoint


@pytest.mark.asyncio
async def test_compliance_export_download_url_format():
    """Download URL follows the /compliance/export/{id}/download pattern."""
    cc = ComplianceController()
    req = await cc.request_data_export(tenant_ctx=T)
    assert req.download_url.startswith("/compliance/export/")
    assert req.download_url.endswith("/download")
    assert req.request_id in req.download_url


@pytest.mark.asyncio
async def test_compliance_export_retrievable_after_creation():
    """Created export request is immediately retrievable by ID."""
    cc = ComplianceController()
    req = await cc.request_data_export(tenant_ctx=T)
    fetched = await cc.get_export_status(request_id=req.request_id, tenant_ctx=T)
    assert fetched is not None
    assert fetched.request_id == req.request_id
    assert fetched.tenant_id == T.tenant_id


@pytest.mark.asyncio
async def test_compliance_export_returns_real_goals_when_service_wired() -> None:
    """request_data_export populates goals from the injected GoalService."""
    from app.agent.state import GoalStatus
    from app.services.goal_service import GoalRecord, GoalService

    cc = ComplianceController()
    goal_svc = GoalService()

    # Inject a fake goal record directly into the in-memory store
    goal_svc._goals["g1"] = GoalRecord(
        goal_id="g1",
        goal_text="test goal",
        status=GoalStatus.COMPLETE,
        tenant_id=T.tenant_id,
        priority="normal",
        dry_run=False,
        created_at="2024-01-01T00:00:00Z",
    )

    cc.configure_services(goal_service=goal_svc)
    req = await cc.request_data_export(tenant_ctx=T)

    assert req.status == "ready"
    goals = req.payload["data"]["goals"]
    assert len(goals) == 1
    assert goals[0]["goal_id"] == "g1"
    assert goals[0]["goal_text"] == "test goal"
