"""Unit tests for batch-3 MCP servers (servers 1-25 of this batch).

Uses the same mock pattern as existing test files:
  - patch httpx.AsyncClient with a mock that returns pre-configured responses
  - assert no 'error' key in successful calls
  - assert 'error' key returned when env vars missing
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def make_resp(status: int = 200, data: Any = None) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data if data is not None else {}
    m.text = str(data or "")
    m.content = b"ok"
    m.raise_for_status = MagicMock()
    m.headers = MagicMock()
    m.headers.get = MagicMock(return_value="application/json")
    return m


def mk_client(**kwargs: MagicMock) -> AsyncMock:
    mc = AsyncMock()
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=False)
    _default = make_resp()
    for method in ("get", "post", "put", "patch", "delete"):
        setattr(mc, method, AsyncMock(return_value=kwargs.get(method, _default)))
    return mc


# ===========================================================================
# 1. Clockify
# ===========================================================================

_CK = {"CLOCKIFY_API_KEY": "ck-key"}


@pytest.mark.asyncio
async def test_clockify_list_workspaces():
    from app.mcp.servers.clockify_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "ws1", "name": "My Workspace"}]))
    with patch.dict("os.environ", _CK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("clockify_list_workspaces", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_clockify_list_time_entries():
    from app.mcp.servers.clockify_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "te1", "description": "Coding", "timeInterval": {"start": "2024-01-15T09:00:00Z"}}]))
    with patch.dict("os.environ", _CK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("clockify_list_time_entries", {"workspace_id": "ws1", "user_id": "u1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_clockify_create_time_entry():
    from app.mcp.servers.clockify_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "te2", "description": "Design", "billable": True}))
    with patch.dict("os.environ", _CK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("clockify_create_time_entry", {
            "workspace_id": "ws1",
            "start": "2024-01-15T09:00:00Z",
            "end": "2024-01-15T10:00:00Z",
            "project_id": "p1",
            "description": "Design",
            "billable": True,
        })
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_clockify_list_projects():
    from app.mcp.servers.clockify_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "p1", "name": "Project Alpha", "archived": False}]))
    with patch.dict("os.environ", _CK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("clockify_list_projects", {"workspace_id": "ws1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_clockify_list_users():
    from app.mcp.servers.clockify_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "u1", "name": "Alice", "email": "a@b.com", "status": "ACTIVE"}]))
    with patch.dict("os.environ", _CK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("clockify_list_users", {"workspace_id": "ws1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_clockify_get_summary_report():
    from app.mcp.servers.clockify_server import call_tool

    mc = mk_client(post=make_resp(data={"totals": [{"totalTime": 3600}], "groupOne": []}))
    with patch.dict("os.environ", _CK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("clockify_get_summary_report", {
            "workspace_id": "ws1",
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-01-31T23:59:59Z",
        })
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_clockify_missing_key():
    from app.mcp.servers.clockify_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("clockify_list_workspaces", {})
    assert "error" in result


# ===========================================================================
# 2. Toggl
# ===========================================================================

_TG = {"TOGGL_API_TOKEN": "toggl-token"}


@pytest.mark.asyncio
async def test_toggl_list_time_entries():
    from app.mcp.servers.toggl_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 1, "description": "Meeting", "duration": 3600}]))
    with patch.dict("os.environ", _TG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("toggl_list_time_entries", {"start_date": "2024-01-01", "end_date": "2024-01-31"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_toggl_create_time_entry():
    from app.mcp.servers.toggl_server import call_tool

    mc = mk_client(post=make_resp(data={"id": 2, "description": "Coding", "duration": 1800}))
    with patch.dict("os.environ", _TG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("toggl_create_time_entry", {
            "workspace_id": 12345,
            "start": "2024-01-15T09:00:00+00:00",
            "duration": 1800,
            "description": "Coding",
        })
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_toggl_stop_timer():
    from app.mcp.servers.toggl_server import call_tool

    mc = mk_client(patch=make_resp(data={"id": 1, "duration": 3600, "stop": "2024-01-15T10:00:00Z"}))
    with patch.dict("os.environ", _TG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("toggl_stop_timer", {"workspace_id": 12345, "time_entry_id": 1})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_toggl_list_projects():
    from app.mcp.servers.toggl_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 100, "name": "Project X", "active": True}]))
    with patch.dict("os.environ", _TG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("toggl_list_projects", {"workspace_id": 12345})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_toggl_list_clients():
    from app.mcp.servers.toggl_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 200, "name": "Acme Corp", "wid": 12345}]))
    with patch.dict("os.environ", _TG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("toggl_list_clients", {"workspace_id": 12345})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_toggl_get_detailed_report():
    from app.mcp.servers.toggl_server import call_tool

    mc = mk_client(post=make_resp(data=[{"id": 1, "description": "Coding", "duration": 1800, "project_id": 100}]))
    with patch.dict("os.environ", _TG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("toggl_get_detailed_report", {
            "workspace_id": 12345,
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
        })
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_toggl_missing_key():
    from app.mcp.servers.toggl_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("toggl_list_time_entries", {})
    assert "error" in result


# ===========================================================================
# 3. Harvest
# ===========================================================================

_HV = {"HARVEST_ACCESS_TOKEN": "hv-tok", "HARVEST_ACCOUNT_ID": "12345"}


@pytest.mark.asyncio
async def test_harvest_list_time_entries():
    from app.mcp.servers.harvest_server import call_tool

    mc = mk_client(get=make_resp(data={"time_entries": [{"id": 1, "hours": 8.0, "notes": "Dev work"}], "total_pages": 1}))
    with patch.dict("os.environ", _HV), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("harvest_list_time_entries", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_harvest_create_time_entry():
    from app.mcp.servers.harvest_server import call_tool

    mc = mk_client(post=make_resp(data={"id": 2, "hours": 4.0, "spent_date": "2024-01-15"}))
    with patch.dict("os.environ", _HV), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("harvest_create_time_entry", {
            "project_id": 10,
            "task_id": 20,
            "spent_date": "2024-01-15",
            "hours": 4.0,
        })
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_harvest_list_projects():
    from app.mcp.servers.harvest_server import call_tool

    mc = mk_client(get=make_resp(data={"projects": [{"id": 10, "name": "Website Redesign", "is_active": True}], "total_pages": 1}))
    with patch.dict("os.environ", _HV), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("harvest_list_projects", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_harvest_list_clients():
    from app.mcp.servers.harvest_server import call_tool

    mc = mk_client(get=make_resp(data={"clients": [{"id": 1, "name": "Acme Corp", "is_active": True}], "total_pages": 1}))
    with patch.dict("os.environ", _HV), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("harvest_list_clients", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_harvest_list_invoices():
    from app.mcp.servers.harvest_server import call_tool

    mc = mk_client(get=make_resp(data={"invoices": [{"id": 100, "number": "INV-001", "state": "open", "amount": 5000}], "total_pages": 1}))
    with patch.dict("os.environ", _HV), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("harvest_list_invoices", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_harvest_get_project_budget():
    from app.mcp.servers.harvest_server import call_tool

    mc = mk_client(get=make_resp(data={"id": 10, "name": "Website", "budget": 100.0, "budget_by": "hours", "cost_budget": 5000.0}))
    with patch.dict("os.environ", _HV), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("harvest_get_project_budget", {"project_id": 10})
    assert "error" not in str(result)


# ===========================================================================
# 4. Greenhouse
# ===========================================================================

_GH = {"GREENHOUSE_API_KEY": "gh-key"}


@pytest.mark.asyncio
async def test_greenhouse_list_jobs():
    from app.mcp.servers.greenhouse_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 1, "title": "Software Engineer", "status": "open"}]))
    with patch.dict("os.environ", _GH), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("greenhouse_list_jobs", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_greenhouse_list_candidates():
    from app.mcp.servers.greenhouse_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 1, "first_name": "Alice", "last_name": "Smith"}]))
    with patch.dict("os.environ", _GH), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("greenhouse_list_candidates", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_greenhouse_create_candidate():
    from app.mcp.servers.greenhouse_server import call_tool

    mc = mk_client(post=make_resp(data={"id": 2, "first_name": "Bob", "last_name": "Jones"}))
    with patch.dict("os.environ", _GH), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("greenhouse_create_candidate", {"first_name": "Bob", "last_name": "Jones"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_greenhouse_get_candidate():
    from app.mcp.servers.greenhouse_server import call_tool

    mc = mk_client(get=make_resp(data={"id": 1, "first_name": "Alice", "applications": []}))
    with patch.dict("os.environ", _GH), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("greenhouse_get_candidate", {"candidate_id": 1})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_greenhouse_list_applications():
    from app.mcp.servers.greenhouse_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 10, "status": "active", "candidate_id": 1, "job_id": 1}]))
    with patch.dict("os.environ", _GH), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("greenhouse_list_applications", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_greenhouse_advance_application():
    from app.mcp.servers.greenhouse_server import call_tool

    mc = mk_client(post=make_resp(data={"id": 10, "current_stage": {"id": 2, "name": "Phone Screen"}}))
    with patch.dict("os.environ", _GH), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("greenhouse_advance_application", {"application_id": 10, "from_stage_id": 1})
    assert "error" not in str(result)


# ===========================================================================
# 5. Recruitee
# ===========================================================================

_RC = {"RECRUITEE_API_TOKEN": "rc-tok", "RECRUITEE_COMPANY_ID": "myco"}


@pytest.mark.asyncio
async def test_recruitee_list_offers():
    from app.mcp.servers.recruitee_server import call_tool

    mc = mk_client(get=make_resp(data={"offers": [{"id": 1, "title": "Backend Developer", "status": "published"}]}))
    with patch.dict("os.environ", _RC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("recruitee_list_offers", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_recruitee_list_candidates():
    from app.mcp.servers.recruitee_server import call_tool

    mc = mk_client(get=make_resp(data={"candidates": [{"id": 1, "name": "Alice Smith"}]}))
    with patch.dict("os.environ", _RC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("recruitee_list_candidates", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_recruitee_create_candidate():
    from app.mcp.servers.recruitee_server import call_tool

    mc = mk_client(post=make_resp(data={"candidate": {"id": 2, "name": "Bob Jones"}}))
    with patch.dict("os.environ", _RC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("recruitee_create_candidate", {"name": "Bob Jones", "emails": ["bob@example.com"]})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_recruitee_update_candidate_stage():
    from app.mcp.servers.recruitee_server import call_tool

    mc = mk_client(patch=make_resp(data={"candidate": {"id": 1, "stage_id": 2}}))
    with patch.dict("os.environ", _RC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("recruitee_update_candidate_stage", {"candidate_id": 1, "stage_id": 2})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_recruitee_list_stages():
    from app.mcp.servers.recruitee_server import call_tool

    mc = mk_client(get=make_resp(data={"stages": [{"id": 1, "name": "Applied"}, {"id": 2, "name": "Phone Screen"}]}))
    with patch.dict("os.environ", _RC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("recruitee_list_stages", {"offer_id": 1})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_recruitee_get_statistics():
    from app.mcp.servers.recruitee_server import call_tool

    mc = mk_client(get=make_resp(data={"stats": {"total_candidates": 50, "hired": 5, "rejected": 20}}))
    with patch.dict("os.environ", _RC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("recruitee_get_statistics", {})
    assert "error" not in str(result)


# ===========================================================================
# 6. Gusto
# ===========================================================================

_GS = {"GUSTO_ACCESS_TOKEN": "gusto-tok"}


@pytest.mark.asyncio
async def test_gusto_list_employees():
    from app.mcp.servers.gusto_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "emp1", "first_name": "Alice", "last_name": "Smith"}]))
    with patch.dict("os.environ", _GS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gusto_list_employees", {"company_id": "co1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_gusto_get_employee():
    from app.mcp.servers.gusto_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "emp1", "first_name": "Alice", "department": "Engineering"}))
    with patch.dict("os.environ", _GS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gusto_get_employee", {"employee_id": "emp1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_gusto_list_pay_periods():
    from app.mcp.servers.gusto_server import call_tool

    mc = mk_client(get=make_resp(data=[{"start_date": "2024-01-01", "end_date": "2024-01-15", "pay_schedule_uuid": "ps1"}]))
    with patch.dict("os.environ", _GS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gusto_list_pay_periods", {"company_id": "co1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_gusto_get_payroll():
    from app.mcp.servers.gusto_server import call_tool

    mc = mk_client(get=make_resp(data={"payroll_uuid": "pay1", "processed": True, "totals": {"company_debit": "10000.00"}}))
    with patch.dict("os.environ", _GS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gusto_get_payroll", {"company_id": "co1", "payroll_id": "pay1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_gusto_list_benefits():
    from app.mcp.servers.gusto_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "ben1", "description": "Medical", "active": True}]))
    with patch.dict("os.environ", _GS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gusto_list_benefits", {"company_id": "co1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_gusto_get_company():
    from app.mcp.servers.gusto_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "co1", "name": "Acme Corp", "ein": "12-3456789"}))
    with patch.dict("os.environ", _GS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gusto_get_company", {"company_id": "co1"})
    assert "error" not in str(result)


# ===========================================================================
# 7. FreshBooks
# ===========================================================================

_FB = {"FRESHBOOKS_ACCESS_TOKEN": "fb-tok"}


@pytest.mark.asyncio
async def test_freshbooks_list_invoices():
    from app.mcp.servers.freshbooks_server import call_tool

    mc = mk_client(get=make_resp(data={"response": {"result": {"invoices": [{"id": 1, "invoice_number": "INV-001", "status": 2}]}}}))
    with patch.dict("os.environ", _FB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("freshbooks_list_invoices", {"account_id": "acct1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_freshbooks_create_invoice():
    from app.mcp.servers.freshbooks_server import call_tool

    mc = mk_client(post=make_resp(data={"response": {"result": {"invoice": {"id": 2, "invoice_number": "INV-002"}}}}))
    with patch.dict("os.environ", _FB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("freshbooks_create_invoice", {"account_id": "acct1", "customerid": 10})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_freshbooks_list_clients():
    from app.mcp.servers.freshbooks_server import call_tool

    mc = mk_client(get=make_resp(data={"response": {"result": {"clients": [{"id": 10, "email": "a@b.com", "fname": "Alice"}]}}}))
    with patch.dict("os.environ", _FB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("freshbooks_list_clients", {"account_id": "acct1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_freshbooks_create_client():
    from app.mcp.servers.freshbooks_server import call_tool

    mc = mk_client(post=make_resp(data={"response": {"result": {"client": {"id": 11, "email": "b@c.com"}}}}))
    with patch.dict("os.environ", _FB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("freshbooks_create_client", {"account_id": "acct1", "email": "b@c.com"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_freshbooks_list_expenses():
    from app.mcp.servers.freshbooks_server import call_tool

    mc = mk_client(get=make_resp(data={"response": {"result": {"expenses": [{"id": 1, "amount": {"amount": "50.00"}}]}}}))
    with patch.dict("os.environ", _FB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("freshbooks_list_expenses", {"account_id": "acct1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_freshbooks_create_expense():
    from app.mcp.servers.freshbooks_server import call_tool

    mc = mk_client(post=make_resp(data={"response": {"result": {"expense": {"id": 2, "amount": {"amount": "75.00"}}}}}))
    with patch.dict("os.environ", _FB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("freshbooks_create_expense", {
            "account_id": "acct1",
            "amount": {"amount": "75.00", "code": "USD"},
            "categoryid": 5,
            "date": "2024-01-15",
        })
    assert "error" not in str(result)


# ===========================================================================
# 8. Wave (GraphQL)
# ===========================================================================

_WV = {"WAVE_ACCESS_TOKEN": "wave-tok"}


@pytest.mark.asyncio
async def test_wave_list_businesses():
    from app.mcp.servers.wave_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"businesses": {"edges": [{"node": {"id": "biz1", "name": "My Business"}}]}}}))
    with patch.dict("os.environ", _WV), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("wave_list_businesses", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_wave_list_customers():
    from app.mcp.servers.wave_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"business": {"customers": {"edges": [{"node": {"id": "c1", "name": "Alice"}}]}}}}))
    with patch.dict("os.environ", _WV), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("wave_list_customers", {"business_id": "biz1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_wave_list_invoices():
    from app.mcp.servers.wave_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"business": {"invoices": {"edges": [{"node": {"id": "inv1", "invoiceNumber": "0001", "status": "DRAFT"}}]}}}}))
    with patch.dict("os.environ", _WV), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("wave_list_invoices", {"business_id": "biz1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_wave_create_invoice():
    from app.mcp.servers.wave_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"invoiceCreate": {"didSucceed": True, "invoice": {"id": "inv2", "invoiceNumber": "0002"}}}}))
    with patch.dict("os.environ", _WV), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("wave_create_invoice", {"business_id": "biz1", "customer_id": "c1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_wave_list_transactions():
    from app.mcp.servers.wave_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"business": {"transactions": {"edges": [{"node": {"id": "t1", "description": "Payment", "amount": {"raw": 500}}}]}}}}))
    with patch.dict("os.environ", _WV), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("wave_list_transactions", {"business_id": "biz1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_wave_get_account_balances():
    from app.mcp.servers.wave_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"business": {"accounts": {"edges": [{"node": {"id": "acc1", "name": "Cash", "balance": {"raw": 10000}}}]}}}}))
    with patch.dict("os.environ", _WV), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("wave_get_account_balances", {"business_id": "biz1"})
    assert "error" not in str(result)


# ===========================================================================
# 9. Invoice Ninja
# ===========================================================================

_IN = {"INVOICE_NINJA_TOKEN": "in-tok"}


@pytest.mark.asyncio
async def test_invoice_ninja_list_invoices():
    from app.mcp.servers.invoice_ninja_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "inv1", "number": "0001", "status_id": "2"}]}))
    with patch.dict("os.environ", _IN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("invoice_ninja_list_invoices", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_invoice_ninja_create_invoice():
    from app.mcp.servers.invoice_ninja_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"id": "inv2", "number": "0002", "client_id": "c1"}}))
    with patch.dict("os.environ", _IN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("invoice_ninja_create_invoice", {"client_id": "c1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_invoice_ninja_list_clients():
    from app.mcp.servers.invoice_ninja_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "c1", "name": "Acme Corp"}]}))
    with patch.dict("os.environ", _IN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("invoice_ninja_list_clients", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_invoice_ninja_create_client():
    from app.mcp.servers.invoice_ninja_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"id": "c2", "name": "Beta LLC"}}))
    with patch.dict("os.environ", _IN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("invoice_ninja_create_client", {"name": "Beta LLC"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_invoice_ninja_list_payments():
    from app.mcp.servers.invoice_ninja_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "pay1", "amount": 500.0, "date": "2024-01-15"}]}))
    with patch.dict("os.environ", _IN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("invoice_ninja_list_payments", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_invoice_ninja_send_invoice():
    from app.mcp.servers.invoice_ninja_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"id": "email1", "sent_at": "2024-01-15T10:00:00Z"}}))
    with patch.dict("os.environ", _IN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("invoice_ninja_send_invoice", {"invoice_id": "inv1"})
    assert "error" not in str(result)


# ===========================================================================
# 10. NetSuite
# ===========================================================================

_NS = {"NETSUITE_ACCOUNT_ID": "TSTDRV12345", "NETSUITE_CONSUMER_KEY": "ck1", "NETSUITE_TOKEN_KEY": "tk1"}


@pytest.mark.asyncio
async def test_netsuite_list_records():
    from app.mcp.servers.netsuite_server import call_tool

    mc = mk_client(get=make_resp(data={"items": [{"id": "1", "companyName": "Acme"}], "totalResults": 1}))
    with patch.dict("os.environ", _NS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("netsuite_list_records", {"record_type": "customer"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_netsuite_get_record():
    from app.mcp.servers.netsuite_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "1", "companyName": "Acme", "email": "a@b.com"}))
    with patch.dict("os.environ", _NS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("netsuite_get_record", {"record_type": "customer", "record_id": "1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_netsuite_create_record():
    from app.mcp.servers.netsuite_server import call_tool

    mc = mk_client(post=make_resp(status=204))
    mc.post.return_value.content = b""
    mc.post.return_value.headers = MagicMock()
    mc.post.return_value.headers.get = MagicMock(return_value="/services/rest/record/v1/customer/2")
    with patch.dict("os.environ", _NS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("netsuite_create_record", {"record_type": "customer", "fields": {"companyName": "New Corp"}})
    assert result is not None


@pytest.mark.asyncio
async def test_netsuite_update_record():
    from app.mcp.servers.netsuite_server import call_tool

    mc = mk_client(patch=make_resp(status=204))
    mc.patch.return_value.content = b""
    with patch.dict("os.environ", _NS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("netsuite_update_record", {"record_type": "customer", "record_id": "1", "fields": {"email": "new@corp.com"}})
    assert result is not None


@pytest.mark.asyncio
async def test_netsuite_search_records():
    from app.mcp.servers.netsuite_server import call_tool

    mc = mk_client(get=make_resp(data={"items": [{"id": "1"}], "totalResults": 1}))
    with patch.dict("os.environ", _NS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("netsuite_search_records", {"record_type": "customer", "query": "Acme"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_netsuite_run_saved_search():
    from app.mcp.servers.netsuite_server import call_tool

    mc = mk_client(get=make_resp(data={"items": [], "totalResults": 0}))
    with patch.dict("os.environ", _NS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("netsuite_run_saved_search", {"saved_search_id": "customsearch1", "record_type": "customer"})
    assert "error" not in str(result)


# ===========================================================================
# 11. Braintree
# ===========================================================================

_BT = {"BRAINTREE_MERCHANT_ID": "m1", "BRAINTREE_PUBLIC_KEY": "pub1", "BRAINTREE_PRIVATE_KEY": "priv1"}


@pytest.mark.asyncio
async def test_braintree_create_transaction():
    from app.mcp.servers.braintree_server import call_tool

    mc = mk_client(post=make_resp(data={"transaction": {"id": "txn1", "amount": "10.00", "status": "submitted_for_settlement"}}))
    with patch.dict("os.environ", _BT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("braintree_create_transaction", {"amount": "10.00", "payment_method_nonce": "fake-valid-nonce"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_braintree_find_transaction():
    from app.mcp.servers.braintree_server import call_tool

    mc = mk_client(get=make_resp(data={"transaction": {"id": "txn1", "amount": "10.00", "status": "settled"}}))
    with patch.dict("os.environ", _BT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("braintree_find_transaction", {"transaction_id": "txn1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_braintree_list_transactions():
    from app.mcp.servers.braintree_server import call_tool

    mc = mk_client(post=make_resp(data={"searchResults": {"pageSize": 50, "totalItems": 1}, "ids": ["txn1"]}))
    with patch.dict("os.environ", _BT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("braintree_list_transactions", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_braintree_create_customer():
    from app.mcp.servers.braintree_server import call_tool

    mc = mk_client(post=make_resp(data={"customer": {"id": "cust1", "email": "a@b.com"}}))
    with patch.dict("os.environ", _BT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("braintree_create_customer", {"email": "a@b.com", "first_name": "Alice"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_braintree_find_customer():
    from app.mcp.servers.braintree_server import call_tool

    mc = mk_client(get=make_resp(data={"customer": {"id": "cust1", "email": "a@b.com", "paymentMethods": []}}))
    with patch.dict("os.environ", _BT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("braintree_find_customer", {"customer_id": "cust1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_braintree_create_subscription():
    from app.mcp.servers.braintree_server import call_tool

    mc = mk_client(post=make_resp(data={"subscription": {"id": "sub1", "planId": "monthly", "status": "Active"}}))
    with patch.dict("os.environ", _BT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("braintree_create_subscription", {"payment_method_token": "token1", "plan_id": "monthly"})
    assert "error" not in str(result)


# ===========================================================================
# 12. SamCart
# ===========================================================================

_SC = {"SAMCART_API_KEY": "sc-key"}


@pytest.mark.asyncio
async def test_samcart_list_products():
    from app.mcp.servers.samcart_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": 1, "name": "Course Bundle", "price": 497}]}))
    with patch.dict("os.environ", _SC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("samcart_list_products", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_samcart_get_product():
    from app.mcp.servers.samcart_server import call_tool

    mc = mk_client(get=make_resp(data={"id": 1, "name": "Course Bundle", "price": 497, "active": True}))
    with patch.dict("os.environ", _SC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("samcart_get_product", {"product_id": 1})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_samcart_list_orders():
    from app.mcp.servers.samcart_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": 100, "product_id": 1, "total": 497}]}))
    with patch.dict("os.environ", _SC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("samcart_list_orders", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_samcart_get_order():
    from app.mcp.servers.samcart_server import call_tool

    mc = mk_client(get=make_resp(data={"id": 100, "product_id": 1, "status": "complete", "total": 497}))
    with patch.dict("os.environ", _SC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("samcart_get_order", {"order_id": 100})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_samcart_list_customers():
    from app.mcp.servers.samcart_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": 1, "email": "a@b.com", "first_name": "Alice"}]}))
    with patch.dict("os.environ", _SC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("samcart_list_customers", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_samcart_get_customer_subscriptions():
    from app.mcp.servers.samcart_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "sub1", "product_id": 1, "status": "active"}]}))
    with patch.dict("os.environ", _SC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("samcart_get_customer_subscriptions", {"customer_id": 1})
    assert "error" not in str(result)


# ===========================================================================
# 13. ProfitWell
# ===========================================================================

_PW = {"PROFITWELL_API_KEY": "pw-key"}


@pytest.mark.asyncio
async def test_profitwell_get_metrics():
    from app.mcp.servers.profitwell_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"date": 20240101, "mrr": 100000, "active_customers": 50}]}))
    with patch.dict("os.environ", _PW), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("profitwell_get_metrics", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_profitwell_get_mrr():
    from app.mcp.servers.profitwell_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"date": 20240101, "mrr": 100000, "new_mrr": 5000}]}))
    with patch.dict("os.environ", _PW), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("profitwell_get_mrr", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_profitwell_get_churn():
    from app.mcp.servers.profitwell_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"date": 20240101, "customer_churn_rate": 0.02, "revenue_churn_rate": 0.015}]}))
    with patch.dict("os.environ", _PW), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("profitwell_get_churn", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_profitwell_list_customers():
    from app.mcp.servers.profitwell_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "c1", "email": "a@b.com", "status": "active", "mrr": 99}]}))
    with patch.dict("os.environ", _PW), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("profitwell_list_customers", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_profitwell_get_customer():
    from app.mcp.servers.profitwell_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "c1", "email": "a@b.com", "plan_id": "pro", "mrr": 99}]}))
    with patch.dict("os.environ", _PW), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("profitwell_get_customer", {"user_alias": "a@b.com"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_profitwell_get_plan_metrics():
    from app.mcp.servers.profitwell_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"plan_id": "pro", "active_customers": 20, "mrr": 1980}]}))
    with patch.dict("os.environ", _PW), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("profitwell_get_plan_metrics", {"plan_id": "pro"})
    assert "error" not in str(result)


# ===========================================================================
# 14. Zuora
# ===========================================================================

_ZU = {"ZUORA_CLIENT_ID": "zc1", "ZUORA_CLIENT_SECRET": "zs1"}


@pytest.mark.asyncio
async def test_zuora_list_subscriptions():
    from app.mcp.servers.zuora_server import call_tool

    token_resp = make_resp(data={"access_token": "zu-tok", "expires_in": 3600})
    subs_resp = make_resp(data={"subscriptions": [{"id": "sub1", "subscriptionNumber": "A-S00001", "status": "Active"}]})
    mc = mk_client()
    mc.post = AsyncMock(return_value=token_resp)
    mc.get = AsyncMock(return_value=subs_resp)
    with patch.dict("os.environ", _ZU), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zuora_list_subscriptions", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_zuora_get_subscription():
    from app.mcp.servers.zuora_server import call_tool

    token_resp = make_resp(data={"access_token": "zu-tok", "expires_in": 3600})
    sub_resp = make_resp(data={"id": "sub1", "subscriptionNumber": "A-S00001", "status": "Active", "termType": "EVERGREEN"})
    mc = mk_client()
    mc.post = AsyncMock(return_value=token_resp)
    mc.get = AsyncMock(return_value=sub_resp)
    with patch.dict("os.environ", _ZU), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zuora_get_subscription", {"subscription_key": "A-S00001"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_zuora_get_account():
    from app.mcp.servers.zuora_server import call_tool

    token_resp = make_resp(data={"access_token": "zu-tok", "expires_in": 3600})
    acct_resp = make_resp(data={"id": "acct1", "accountNumber": "A-00001", "status": "Active"})
    mc = mk_client()
    mc.post = AsyncMock(return_value=token_resp)
    mc.get = AsyncMock(return_value=acct_resp)
    with patch.dict("os.environ", _ZU), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zuora_get_account", {"account_key": "A-00001"})
    assert "error" not in str(result)


# ===========================================================================
# 15. Zoho Books
# ===========================================================================

_ZB = {"ZOHO_ACCESS_TOKEN": "zb-tok", "ZOHO_ORGANIZATION_ID": "org1"}


@pytest.mark.asyncio
async def test_zoho_books_list_invoices():
    from app.mcp.servers.zoho_books_server import call_tool

    mc = mk_client(get=make_resp(data={"invoices": [{"invoice_id": "inv1", "invoice_number": "INV-001", "status": "draft"}]}))
    with patch.dict("os.environ", _ZB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zoho_books_list_invoices", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_zoho_books_create_invoice():
    from app.mcp.servers.zoho_books_server import call_tool

    mc = mk_client(post=make_resp(data={"invoice": {"invoice_id": "inv2", "status": "draft"}}))
    with patch.dict("os.environ", _ZB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zoho_books_create_invoice", {
            "customer_id": "c1",
            "line_items": [{"name": "Service", "quantity": 1, "rate": 100}],
        })
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_zoho_books_list_contacts():
    from app.mcp.servers.zoho_books_server import call_tool

    mc = mk_client(get=make_resp(data={"contacts": [{"contact_id": "c1", "contact_name": "Acme Corp", "contact_type": "customer"}]}))
    with patch.dict("os.environ", _ZB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zoho_books_list_contacts", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_zoho_books_create_contact():
    from app.mcp.servers.zoho_books_server import call_tool

    mc = mk_client(post=make_resp(data={"contact": {"contact_id": "c2", "contact_name": "Beta LLC"}}))
    with patch.dict("os.environ", _ZB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zoho_books_create_contact", {"contact_name": "Beta LLC"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_zoho_books_list_expenses():
    from app.mcp.servers.zoho_books_server import call_tool

    mc = mk_client(get=make_resp(data={"expenses": [{"expense_id": "exp1", "total": 50.0, "status": "unbilled"}]}))
    with patch.dict("os.environ", _ZB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zoho_books_list_expenses", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_zoho_books_get_dashboard():
    from app.mcp.servers.zoho_books_server import call_tool

    mc = mk_client(get=make_resp(data={"dashboard": {"total_receivable": 5000, "total_payable": 2000}}))
    with patch.dict("os.environ", _ZB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zoho_books_get_dashboard", {})
    assert "error" not in str(result)


# ===========================================================================
# 16. Zoho Invoice
# ===========================================================================

_ZI = {"ZOHO_ACCESS_TOKEN": "zi-tok", "ZOHO_ORGANIZATION_ID": "org2"}


@pytest.mark.asyncio
async def test_zoho_invoice_list_invoices():
    from app.mcp.servers.zoho_invoice_server import call_tool

    mc = mk_client(get=make_resp(data={"invoices": [{"invoice_id": "inv1", "status": "sent"}]}))
    with patch.dict("os.environ", _ZI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zoho_invoice_list_invoices", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_zoho_invoice_create_invoice():
    from app.mcp.servers.zoho_invoice_server import call_tool

    mc = mk_client(post=make_resp(data={"invoice": {"invoice_id": "inv2", "status": "draft"}}))
    with patch.dict("os.environ", _ZI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zoho_invoice_create_invoice", {
            "customer_id": "c1",
            "line_items": [{"name": "Consulting", "quantity": 1, "rate": 500}],
        })
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_zoho_invoice_send_invoice():
    from app.mcp.servers.zoho_invoice_server import call_tool

    mc = mk_client(post=make_resp(data={"code": 0, "message": "Your invoice has been sent."}))
    with patch.dict("os.environ", _ZI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zoho_invoice_send_invoice", {"invoice_id": "inv1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_zoho_invoice_list_customers():
    from app.mcp.servers.zoho_invoice_server import call_tool

    mc = mk_client(get=make_resp(data={"customers": [{"customer_id": "c1", "display_name": "Acme Corp", "email": "a@b.com"}]}))
    with patch.dict("os.environ", _ZI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zoho_invoice_list_customers", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_zoho_invoice_create_customer():
    from app.mcp.servers.zoho_invoice_server import call_tool

    mc = mk_client(post=make_resp(data={"customer": {"customer_id": "c2", "display_name": "Beta LLC"}}))
    with patch.dict("os.environ", _ZI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zoho_invoice_create_customer", {"display_name": "Beta LLC"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_zoho_invoice_get_invoice_status():
    from app.mcp.servers.zoho_invoice_server import call_tool

    mc = mk_client(get=make_resp(data={"invoice": {"invoice_id": "inv1", "status": "paid", "total": 500.0}}))
    with patch.dict("os.environ", _ZI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zoho_invoice_get_invoice_status", {"invoice_id": "inv1"})
    assert "error" not in str(result)


# ===========================================================================
# 17. Teamwork
# ===========================================================================

_TW = {"TEAMWORK_API_KEY": "tw-key", "TEAMWORK_SITE": "myco"}


@pytest.mark.asyncio
async def test_teamwork_list_projects():
    from app.mcp.servers.teamwork_server import call_tool

    mc = mk_client(get=make_resp(data={"projects": [{"id": 1, "name": "Website", "status": "active"}]}))
    with patch.dict("os.environ", _TW), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("teamwork_list_projects", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_teamwork_create_project():
    from app.mcp.servers.teamwork_server import call_tool

    mc = mk_client(post=make_resp(data={"project": {"id": 2, "name": "Mobile App"}}))
    with patch.dict("os.environ", _TW), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("teamwork_create_project", {"name": "Mobile App"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_teamwork_list_tasks():
    from app.mcp.servers.teamwork_server import call_tool

    mc = mk_client(get=make_resp(data={"todo-items": [{"id": 100, "content": "Fix bug", "status": "active"}]}))
    with patch.dict("os.environ", _TW), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("teamwork_list_tasks", {"project_id": 1})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_teamwork_create_task():
    from app.mcp.servers.teamwork_server import call_tool

    mc = mk_client(post=make_resp(data={"todo-item": {"id": 101, "content": "New task"}}))
    with patch.dict("os.environ", _TW), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("teamwork_create_task", {"project_id": 1, "name": "New task"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_teamwork_complete_task():
    from app.mcp.servers.teamwork_server import call_tool

    mc = mk_client(put=make_resp(status=200, data={"STATUS": "OK"}))
    with patch.dict("os.environ", _TW), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("teamwork_complete_task", {"task_id": 100})
    assert result is not None


@pytest.mark.asyncio
async def test_teamwork_list_milestones():
    from app.mcp.servers.teamwork_server import call_tool

    mc = mk_client(get=make_resp(data={"milestones": [{"id": 50, "title": "Launch", "status": "incomplete"}]}))
    with patch.dict("os.environ", _TW), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("teamwork_list_milestones", {"project_id": 1})
    assert "error" not in str(result)


# ===========================================================================
# 18. Hive
# ===========================================================================

_HI = {"HIVE_API_KEY": "hi-key", "HIVE_USER_ID": "u1"}


@pytest.mark.asyncio
async def test_hive_list_actions():
    from app.mcp.servers.hive_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "a1", "title": "Write tests", "status": "todo"}]))
    with patch.dict("os.environ", _HI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("hive_list_actions", {"project_id": "p1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_hive_create_action():
    from app.mcp.servers.hive_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "a2", "title": "Deploy app", "status": "todo"}))
    with patch.dict("os.environ", _HI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("hive_create_action", {"project_id": "p1", "title": "Deploy app"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_hive_list_projects():
    from app.mcp.servers.hive_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "p1", "name": "Q1 Goals", "workspace_id": "ws1"}]))
    with patch.dict("os.environ", _HI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("hive_list_projects", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_hive_update_action_status():
    from app.mcp.servers.hive_server import call_tool

    mc = mk_client(put=make_resp(data={"id": "a1", "status": "inProgress"}))
    with patch.dict("os.environ", _HI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("hive_update_action_status", {"action_id": "a1", "status": "inProgress"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_hive_list_workspaces():
    from app.mcp.servers.hive_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "ws1", "name": "My Workspace"}]))
    with patch.dict("os.environ", _HI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("hive_list_workspaces", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_hive_add_comment():
    from app.mcp.servers.hive_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "cmt1", "message": "Great work!", "user_id": "u1"}))
    with patch.dict("os.environ", _HI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("hive_add_comment", {"action_id": "a1", "message": "Great work!"})
    assert "error" not in str(result)


# ===========================================================================
# 19. Pivotal Tracker
# ===========================================================================

_PT = {"PIVOTAL_TRACKER_TOKEN": "pt-tok"}


@pytest.mark.asyncio
async def test_pivotal_tracker_list_stories():
    from app.mcp.servers.pivotal_tracker_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 1, "name": "User login", "story_type": "feature", "current_state": "started"}]))
    with patch.dict("os.environ", _PT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("pivotal_tracker_list_stories", {"project_id": 100})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_pivotal_tracker_create_story():
    from app.mcp.servers.pivotal_tracker_server import call_tool

    mc = mk_client(post=make_resp(data={"id": 2, "name": "New feature", "story_type": "feature", "current_state": "unstarted"}))
    with patch.dict("os.environ", _PT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("pivotal_tracker_create_story", {"project_id": 100, "name": "New feature"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_pivotal_tracker_update_story():
    from app.mcp.servers.pivotal_tracker_server import call_tool

    mc = mk_client(put=make_resp(data={"id": 1, "current_state": "accepted", "accepted_at": "2024-01-15T00:00:00Z"}))
    with patch.dict("os.environ", _PT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("pivotal_tracker_update_story", {"project_id": 100, "story_id": 1, "current_state": "accepted"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_pivotal_tracker_list_projects():
    from app.mcp.servers.pivotal_tracker_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 100, "name": "My Project", "current_iteration_number": 5}]))
    with patch.dict("os.environ", _PT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("pivotal_tracker_list_projects", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_pivotal_tracker_list_iterations():
    from app.mcp.servers.pivotal_tracker_server import call_tool

    mc = mk_client(get=make_resp(data=[{"number": 5, "stories": [], "start": "2024-01-15", "finish": "2024-01-29"}]))
    with patch.dict("os.environ", _PT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("pivotal_tracker_list_iterations", {"project_id": 100})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_pivotal_tracker_get_project_stats():
    from app.mcp.servers.pivotal_tracker_server import call_tool

    mc = mk_client(get=make_resp(data={"id": 100, "name": "My Project", "velocity_averaged_over": 3, "current_iteration_number": 5, "initial_velocity": 8}))
    with patch.dict("os.environ", _PT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("pivotal_tracker_get_project_stats", {"project_id": 100})
    assert "error" not in str(result)


# ===========================================================================
# 20. Redmine
# ===========================================================================

_RM = {"REDMINE_API_KEY": "rm-key", "REDMINE_URL": "https://redmine.example.com"}


@pytest.mark.asyncio
async def test_redmine_list_issues():
    from app.mcp.servers.redmine_server import call_tool

    mc = mk_client(get=make_resp(data={"issues": [{"id": 1, "subject": "Fix login bug", "status": {"name": "New"}}], "total_count": 1}))
    with patch.dict("os.environ", _RM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("redmine_list_issues", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_redmine_create_issue():
    from app.mcp.servers.redmine_server import call_tool

    mc = mk_client(post=make_resp(data={"issue": {"id": 2, "subject": "New bug", "project": {"id": 1}}}))
    with patch.dict("os.environ", _RM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("redmine_create_issue", {"project_id": "myproject", "subject": "New bug"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_redmine_update_issue():
    from app.mcp.servers.redmine_server import call_tool

    mc = mk_client(put=make_resp(status=204))
    mc.put.return_value.content = b""
    with patch.dict("os.environ", _RM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("redmine_update_issue", {"issue_id": 1, "status_id": 3})
    assert result is not None


@pytest.mark.asyncio
async def test_redmine_list_projects():
    from app.mcp.servers.redmine_server import call_tool

    mc = mk_client(get=make_resp(data={"projects": [{"id": 1, "identifier": "myproject", "name": "My Project"}], "total_count": 1}))
    with patch.dict("os.environ", _RM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("redmine_list_projects", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_redmine_list_users():
    from app.mcp.servers.redmine_server import call_tool

    mc = mk_client(get=make_resp(data={"users": [{"id": 1, "login": "alice", "firstname": "Alice"}], "total_count": 1}))
    with patch.dict("os.environ", _RM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("redmine_list_users", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_redmine_get_time_entries():
    from app.mcp.servers.redmine_server import call_tool

    mc = mk_client(get=make_resp(data={"time_entries": [{"id": 1, "hours": 4.0, "activity": {"name": "Development"}}], "total_count": 1}))
    with patch.dict("os.environ", _RM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("redmine_get_time_entries", {})
    assert "error" not in str(result)


# ===========================================================================
# 21. Procore
# ===========================================================================

_PC = {"PROCORE_ACCESS_TOKEN": "pc-tok"}


@pytest.mark.asyncio
async def test_procore_list_projects():
    from app.mcp.servers.procore_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 1, "name": "Downtown Office", "active": True}]))
    with patch.dict("os.environ", _PC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("procore_list_projects", {"company_id": 100})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_procore_list_rfi():
    from app.mcp.servers.procore_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 1, "subject": "Clarify specs", "status": "open"}]))
    with patch.dict("os.environ", _PC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("procore_list_rfi", {"project_id": 1})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_procore_create_rfi():
    from app.mcp.servers.procore_server import call_tool

    mc = mk_client(post=make_resp(data={"id": 2, "subject": "New RFI", "status": "draft"}))
    with patch.dict("os.environ", _PC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("procore_create_rfi", {"project_id": 1, "subject": "New RFI"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_procore_list_submittals():
    from app.mcp.servers.procore_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 1, "title": "Steel specs", "status": "open"}]))
    with patch.dict("os.environ", _PC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("procore_list_submittals", {"project_id": 1})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_procore_list_meetings():
    from app.mcp.servers.procore_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 1, "title": "Kick-off", "date": "2024-01-10"}]))
    with patch.dict("os.environ", _PC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("procore_list_meetings", {"project_id": 1})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_procore_get_project_budget():
    from app.mcp.servers.procore_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 1, "description": "Foundation", "original_budget_amount": "100000.00"}]))
    with patch.dict("os.environ", _PC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("procore_get_project_budget", {"project_id": 1})
    assert "error" not in str(result)


# ===========================================================================
# 22. Ninox
# ===========================================================================

_NX = {"NINOX_API_KEY": "nx-key"}


@pytest.mark.asyncio
async def test_ninox_list_databases():
    from app.mcp.servers.ninox_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "team1", "name": "My Team"}]))
    with patch.dict("os.environ", _NX), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("ninox_list_databases", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_ninox_list_tables():
    from app.mcp.servers.ninox_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "tbl1", "name": "Customers"}]))
    with patch.dict("os.environ", _NX), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("ninox_list_tables", {"team_id": "team1", "database_id": "db1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_ninox_query_records():
    from app.mcp.servers.ninox_server import call_tool

    mc = mk_client(get=make_resp(data=[{"_id": "rec1", "fields": {"Name": "Alice"}}]))
    with patch.dict("os.environ", _NX), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("ninox_query_records", {"team_id": "team1", "database_id": "db1", "table_id": "tbl1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_ninox_create_record():
    from app.mcp.servers.ninox_server import call_tool

    mc = mk_client(post=make_resp(data=[{"_id": "rec2", "fields": {"Name": "Bob"}}]))
    with patch.dict("os.environ", _NX), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("ninox_create_record", {"team_id": "team1", "database_id": "db1", "table_id": "tbl1", "fields": {"Name": "Bob"}})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_ninox_update_record():
    from app.mcp.servers.ninox_server import call_tool

    mc = mk_client(put=make_resp(data={"_id": "rec1", "fields": {"Name": "Alice Updated"}}))
    with patch.dict("os.environ", _NX), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("ninox_update_record", {
            "team_id": "team1", "database_id": "db1", "table_id": "tbl1",
            "record_id": "rec1", "fields": {"Name": "Alice Updated"},
        })
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_ninox_delete_record():
    from app.mcp.servers.ninox_server import call_tool

    mc = mk_client(delete=make_resp(status=200, data={"status": "deleted"}))
    with patch.dict("os.environ", _NX), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("ninox_delete_record", {
            "team_id": "team1", "database_id": "db1", "table_id": "tbl1", "record_id": "rec1",
        })
    assert result is not None


# ===========================================================================
# 23. Knack
# ===========================================================================

_KN = {"KNACK_APP_ID": "kn-app", "KNACK_API_KEY": "kn-key"}


@pytest.mark.asyncio
async def test_knack_get_records():
    from app.mcp.servers.knack_server import call_tool

    mc = mk_client(get=make_resp(data={"records": [{"id": "rec1", "field_1": "Alice"}], "total_records": 1}))
    with patch.dict("os.environ", _KN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("knack_get_records", {"object_key": "object_1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_knack_create_record():
    from app.mcp.servers.knack_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "rec2", "field_1": "Bob"}))
    with patch.dict("os.environ", _KN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("knack_create_record", {"object_key": "object_1", "fields": {"field_1": "Bob"}})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_knack_update_record():
    from app.mcp.servers.knack_server import call_tool

    mc = mk_client(put=make_resp(data={"id": "rec1", "field_1": "Alice Updated"}))
    with patch.dict("os.environ", _KN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("knack_update_record", {"object_key": "object_1", "record_id": "rec1", "fields": {"field_1": "Alice Updated"}})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_knack_delete_record():
    from app.mcp.servers.knack_server import call_tool

    mc = mk_client(delete=make_resp(status=200, data={"delete": True}))
    with patch.dict("os.environ", _KN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("knack_delete_record", {"object_key": "object_1", "record_id": "rec1"})
    assert result is not None


@pytest.mark.asyncio
async def test_knack_list_objects():
    from app.mcp.servers.knack_server import call_tool

    mc = mk_client(get=make_resp(data={"objects": [{"key": "object_1", "name": "Contacts", "fields": []}]}))
    with patch.dict("os.environ", _KN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("knack_list_objects", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_knack_run_view():
    from app.mcp.servers.knack_server import call_tool

    mc = mk_client(get=make_resp(data={"records": [{"id": "rec1", "field_1": "Alice"}], "total_records": 1}))
    with patch.dict("os.environ", _KN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("knack_run_view", {"scene_key": "scene_1", "view_key": "view_1"})
    assert "error" not in str(result)


# ===========================================================================
# 24. Smartsheet
# ===========================================================================

_SS = {"SMARTSHEET_ACCESS_TOKEN": "ss-tok"}


@pytest.mark.asyncio
async def test_smartsheet_list_sheets():
    from app.mcp.servers.smartsheets_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": 1, "name": "Project Plan", "accessLevel": "OWNER"}], "pageNumber": 1}))
    with patch.dict("os.environ", _SS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("smartsheet_list_sheets", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_smartsheet_get_sheet():
    from app.mcp.servers.smartsheets_server import call_tool

    mc = mk_client(get=make_resp(data={"id": 1, "name": "Project Plan", "columns": [], "rows": []}))
    with patch.dict("os.environ", _SS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("smartsheet_get_sheet", {"sheet_id": 1})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_smartsheet_create_row():
    from app.mcp.servers.smartsheets_server import call_tool

    mc = mk_client(post=make_resp(data={"result": [{"id": 100, "rowNumber": 1, "cells": []}], "resultCode": 0}))
    with patch.dict("os.environ", _SS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("smartsheet_create_row", {
            "sheet_id": 1,
            "cells": [{"column_id": 10, "value": "Task 1"}],
        })
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_smartsheet_update_row():
    from app.mcp.servers.smartsheets_server import call_tool

    mc = mk_client(put=make_resp(data={"result": [{"id": 100, "cells": []}], "resultCode": 0}))
    with patch.dict("os.environ", _SS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("smartsheet_update_row", {
            "sheet_id": 1,
            "row_id": 100,
            "cells": [{"column_id": 10, "value": "Updated Task"}],
        })
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_smartsheet_list_reports():
    from app.mcp.servers.smartsheets_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": 200, "name": "Status Report"}], "pageNumber": 1}))
    with patch.dict("os.environ", _SS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("smartsheet_list_reports", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_smartsheet_share_sheet():
    from app.mcp.servers.smartsheets_server import call_tool

    mc = mk_client(post=make_resp(data={"result": [{"id": "share1", "email": "b@c.com", "accessLevel": "VIEWER"}], "resultCode": 0}))
    with patch.dict("os.environ", _SS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("smartsheet_share_sheet", {"sheet_id": 1, "email": "b@c.com"})
    assert "error" not in str(result)


# ===========================================================================
# 25. Miro
# ===========================================================================

_MR = {"MIRO_ACCESS_TOKEN": "miro-tok"}


@pytest.mark.asyncio
async def test_miro_list_boards():
    from app.mcp.servers.miro_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "board1", "name": "Sprint Planning", "type": "board"}], "size": 1}))
    with patch.dict("os.environ", _MR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("miro_list_boards", {})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_miro_create_board():
    from app.mcp.servers.miro_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "board2", "name": "Roadmap 2024", "type": "board", "viewLink": "https://miro.com/board/board2/"}))
    with patch.dict("os.environ", _MR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("miro_create_board", {"name": "Roadmap 2024"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_miro_get_board():
    from app.mcp.servers.miro_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "board1", "name": "Sprint Planning", "type": "board"}))
    with patch.dict("os.environ", _MR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("miro_get_board", {"board_id": "board1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_miro_create_sticky_note():
    from app.mcp.servers.miro_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "item1", "type": "sticky_note", "data": {"content": "Hello world"}}))
    with patch.dict("os.environ", _MR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("miro_create_sticky_note", {"board_id": "board1", "content": "Hello world"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_miro_list_items():
    from app.mcp.servers.miro_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "item1", "type": "sticky_note"}, {"id": "item2", "type": "frame"}], "size": 2}))
    with patch.dict("os.environ", _MR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("miro_list_items", {"board_id": "board1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_miro_create_frame():
    from app.mcp.servers.miro_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "frame1", "type": "frame", "data": {"title": "Sprint 1", "format": "custom"}}))
    with patch.dict("os.environ", _MR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("miro_create_frame", {"board_id": "board1", "title": "Sprint 1"})
    assert "error" not in str(result)


@pytest.mark.asyncio
async def test_miro_missing_key():
    from app.mcp.servers.miro_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("miro_list_boards", {})
    assert "error" in result


# ===========================================================================
# Unknown tool fallthrough tests for a selection of servers
# ===========================================================================

@pytest.mark.asyncio
async def test_clockify_unknown_tool():
    from app.mcp.servers.clockify_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _CK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("clockify_nonexistent", {})
    assert result.get("error", "").startswith("Unknown tool")


@pytest.mark.asyncio
async def test_miro_unknown_tool():
    from app.mcp.servers.miro_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _MR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("miro_nonexistent", {})
    assert result.get("error", "").startswith("Unknown tool")


@pytest.mark.asyncio
async def test_smartsheet_unknown_tool():
    from app.mcp.servers.smartsheets_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _SS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("smartsheet_nonexistent", {})
    assert result.get("error", "").startswith("Unknown tool")
