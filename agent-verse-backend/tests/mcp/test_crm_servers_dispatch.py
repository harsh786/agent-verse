"""Dispatch-level tests for CRM MCP servers.

Exercises every call_tool() branch by mocking httpx.AsyncClient.
Targets: salesforce, hubspot, pipedrive, zoho_crm, close_crm, copper,
         attio, affinity, apollo, gong, planhat, linkedin, linkedin_ads.
"""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def make_resp(status: int = 200, data: Any = None) -> MagicMock:
    """Return a mock httpx.Response with the given status and JSON payload."""
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data if data is not None else {}
    m.text = str(data or "")
    m.content = b"ok"
    m.raise_for_status = MagicMock()  # no-op – does not raise
    return m


def mk_client(**kwargs: MagicMock) -> AsyncMock:
    """Return a mock AsyncClient context manager.
    
    All HTTP method mocks are explicitly set to AsyncMock so that
    awaiting them works correctly regardless of Python version.
    """
    mc = AsyncMock()
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=False)
    _default = make_resp()
    for method in ("get", "post", "put", "patch", "delete"):
        setattr(mc, method, AsyncMock(return_value=kwargs.get(method, _default)))
    return mc


# ---------------------------------------------------------------------------
# Salesforce
# ---------------------------------------------------------------------------

_SF = {
    "SALESFORCE_INSTANCE_URL": "https://sf.example.com",
    "SALESFORCE_ACCESS_TOKEN": "tok123",
}


@pytest.mark.asyncio
async def test_salesforce_query():
    from app.mcp.servers.salesforce_server import call_tool

    resp_data = {"totalSize": 1, "done": True, "records": [{"Id": "001", "Name": "Acme"}]}
    mc = mk_client(get=make_resp(data=resp_data))
    with patch.dict("os.environ", _SF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("salesforce_query", {"soql": "SELECT Id FROM Account"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_salesforce_create_record():
    from app.mcp.servers.salesforce_server import call_tool

    resp_data = {"id": "001xxx", "success": True, "errors": []}
    mc = mk_client(post=make_resp(data=resp_data))
    with patch.dict("os.environ", _SF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "salesforce_create_record",
            {"object_type": "Account", "fields": {"Name": "Acme"}},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_salesforce_update_record():
    from app.mcp.servers.salesforce_server import call_tool

    mc = mk_client(patch=make_resp(status=204))
    with patch.dict("os.environ", _SF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "salesforce_update_record",
            {"object_type": "Account", "record_id": "001", "fields": {"Name": "ACME"}},
        )
    assert result["success"] is True


@pytest.mark.asyncio
async def test_salesforce_delete_record():
    from app.mcp.servers.salesforce_server import call_tool

    mc = mk_client(delete=make_resp(status=204))
    with patch.dict("os.environ", _SF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "salesforce_delete_record",
            {"object_type": "Account", "record_id": "001"},
        )
    assert result["success"] is True


@pytest.mark.asyncio
async def test_salesforce_describe_object():
    from app.mcp.servers.salesforce_server import call_tool

    resp_data = {
        "name": "Account",
        "label": "Account",
        "fields": [{"name": "Id", "type": "id", "label": "Account ID"}],
    }
    mc = mk_client(get=make_resp(data=resp_data))
    with patch.dict("os.environ", _SF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("salesforce_describe_object", {"object_type": "Account"})
    assert result["name"] == "Account"


@pytest.mark.asyncio
async def test_salesforce_search():
    from app.mcp.servers.salesforce_server import call_tool

    resp_data = {"searchRecords": [{"Id": "001", "Name": "Acme"}]}
    mc = mk_client(get=make_resp(data=resp_data))
    with patch.dict("os.environ", _SF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("salesforce_search", {"sosl": "FIND {Acme}"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_salesforce_get_record():
    from app.mcp.servers.salesforce_server import call_tool

    resp_data = {"Id": "001", "Name": "Acme Corp"}
    mc = mk_client(get=make_resp(data=resp_data))
    with patch.dict("os.environ", _SF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "salesforce_get_record",
            {"object_type": "Account", "record_id": "001"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_salesforce_missing_env():
    from app.mcp.servers.salesforce_server import call_tool

    env = {k: "" for k in ("SALESFORCE_INSTANCE_URL", "SALESFORCE_ACCESS_TOKEN")}
    with patch.dict("os.environ", env, clear=False):
        os.environ.pop("SALESFORCE_INSTANCE_URL", None)
        result = await call_tool("salesforce_query", {"soql": "SELECT Id FROM Account"})
    assert "error" in result


@pytest.mark.asyncio
async def test_salesforce_unknown_tool():
    from app.mcp.servers.salesforce_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _SF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("salesforce_nonexistent_tool", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# HubSpot
# ---------------------------------------------------------------------------

_HS = {"HUBSPOT_API_KEY": "test-hubspot-key"}
_HS_CONTACT = {"id": "1", "properties": {"email": "a@b.com", "firstname": "John"}}
_HS_LIST = {"results": [{"id": "1", "properties": {}}], "paging": {}}


@pytest.mark.asyncio
async def test_hubspot_list_contacts():
    from app.mcp.servers.hubspot_server import call_tool

    mc = mk_client(get=make_resp(data=_HS_LIST))
    with patch.dict("os.environ", _HS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("hubspot_list_contacts", {"limit": 5})
    assert "error" not in result


@pytest.mark.asyncio
async def test_hubspot_get_contact():
    from app.mcp.servers.hubspot_server import call_tool

    mc = mk_client(get=make_resp(data=_HS_CONTACT))
    with patch.dict("os.environ", _HS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("hubspot_get_contact", {"contact_id": "1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_hubspot_create_contact():
    from app.mcp.servers.hubspot_server import call_tool

    mc = mk_client(post=make_resp(data=_HS_CONTACT))
    with patch.dict("os.environ", _HS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "hubspot_create_contact", {"properties": {"email": "a@b.com"}}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_hubspot_update_contact():
    from app.mcp.servers.hubspot_server import call_tool

    mc = mk_client(patch=make_resp(data=_HS_CONTACT))
    with patch.dict("os.environ", _HS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "hubspot_update_contact", {"contact_id": "1", "properties": {"phone": "123"}}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_hubspot_list_companies():
    from app.mcp.servers.hubspot_server import call_tool

    mc = mk_client(get=make_resp(data=_HS_LIST))
    with patch.dict("os.environ", _HS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("hubspot_list_companies", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_hubspot_create_company():
    from app.mcp.servers.hubspot_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "2", "properties": {"name": "ACME"}}))
    with patch.dict("os.environ", _HS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "hubspot_create_company", {"properties": {"name": "ACME", "domain": "acme.com"}}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_hubspot_list_deals():
    from app.mcp.servers.hubspot_server import call_tool

    mc = mk_client(get=make_resp(data=_HS_LIST))
    with patch.dict("os.environ", _HS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("hubspot_list_deals", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_hubspot_create_deal():
    from app.mcp.servers.hubspot_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "3", "properties": {"dealname": "Big Deal"}}))
    with patch.dict("os.environ", _HS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "hubspot_create_deal",
            {"properties": {"dealname": "Big Deal", "amount": "50000"}},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_hubspot_update_deal():
    from app.mcp.servers.hubspot_server import call_tool

    mc = mk_client(patch=make_resp(data={"id": "3", "properties": {}}))
    with patch.dict("os.environ", _HS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "hubspot_update_deal",
            {"deal_id": "3", "properties": {"dealstage": "closedwon"}},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_hubspot_create_note():
    from app.mcp.servers.hubspot_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "note1", "properties": {}}))
    with patch.dict("os.environ", _HS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "hubspot_create_note",
            {"body": "Call went well", "contact_id": "1"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_hubspot_search_crm():
    from app.mcp.servers.hubspot_server import call_tool

    mc = mk_client(post=make_resp(data={"results": [], "total": 0}))
    with patch.dict("os.environ", _HS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "hubspot_search_crm",
            {"object_type": "contacts", "query": "Acme"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_hubspot_missing_env():
    from app.mcp.servers.hubspot_server import call_tool

    with patch.dict("os.environ", {"HUBSPOT_API_KEY": ""}):
        os.environ.pop("HUBSPOT_API_KEY", None)
        result = await call_tool("hubspot_list_contacts", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_hubspot_unknown_tool():
    from app.mcp.servers.hubspot_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _HS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("hubspot_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Pipedrive
# ---------------------------------------------------------------------------

_PD = {"PIPEDRIVE_API_TOKEN": "pd-token", "PIPEDRIVE_COMPANY_DOMAIN": "myco"}
_PD_DEAL = {"success": True, "data": {"id": 1, "title": "Deal", "stage_id": 1, "status": "open"}}


@pytest.mark.asyncio
async def test_pipedrive_list_deals():
    from app.mcp.servers.pipedrive_server import call_tool

    mc = mk_client(get=make_resp(data={"success": True, "data": [_PD_DEAL["data"]]}))
    with patch.dict("os.environ", _PD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("pipedrive_list_deals", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_pipedrive_create_deal():
    from app.mcp.servers.pipedrive_server import call_tool

    mc = mk_client(post=make_resp(data=_PD_DEAL))
    with patch.dict("os.environ", _PD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("pipedrive_create_deal", {"title": "New Deal"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_pipedrive_update_deal():
    from app.mcp.servers.pipedrive_server import call_tool

    mc = mk_client(put=make_resp(data=_PD_DEAL))
    with patch.dict("os.environ", _PD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "pipedrive_update_deal", {"deal_id": 1, "status": "won"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_pipedrive_list_persons():
    from app.mcp.servers.pipedrive_server import call_tool

    mc = mk_client(
        get=make_resp(data={"success": True, "data": [{"id": 1, "name": "John"}]})
    )
    with patch.dict("os.environ", _PD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("pipedrive_list_persons", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_pipedrive_create_person():
    from app.mcp.servers.pipedrive_server import call_tool

    mc = mk_client(post=make_resp(data={"success": True, "data": {"id": 2, "name": "Jane"}}))
    with patch.dict("os.environ", _PD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("pipedrive_create_person", {"name": "Jane"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_pipedrive_list_organizations():
    from app.mcp.servers.pipedrive_server import call_tool

    mc = mk_client(get=make_resp(data={"success": True, "data": [{"id": 1, "name": "Acme"}]}))
    with patch.dict("os.environ", _PD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("pipedrive_list_organizations", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_pipedrive_create_activity():
    from app.mcp.servers.pipedrive_server import call_tool

    mc = mk_client(
        post=make_resp(data={"success": True, "data": {"id": 3, "subject": "Call"}})
    )
    with patch.dict("os.environ", _PD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "pipedrive_create_activity", {"subject": "Call", "type": "call"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_pipedrive_get_pipeline_stages():
    from app.mcp.servers.pipedrive_server import call_tool

    mc = mk_client(
        get=make_resp(data={"success": True, "data": [{"id": 1, "name": "Stage 1"}]})
    )
    with patch.dict("os.environ", _PD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("pipedrive_get_pipeline_stages", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_pipedrive_missing_env():
    from app.mcp.servers.pipedrive_server import call_tool

    with patch.dict("os.environ", {"PIPEDRIVE_API_TOKEN": "", "PIPEDRIVE_COMPANY_DOMAIN": ""}):
        os.environ.pop("PIPEDRIVE_API_TOKEN", None)
        result = await call_tool("pipedrive_list_deals", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Zoho CRM
# ---------------------------------------------------------------------------

_ZOHO = {"ZOHO_ACCESS_TOKEN": "zoho-tok"}


@pytest.mark.asyncio
async def test_zoho_list_records():
    from app.mcp.servers.zoho_crm_server import call_tool

    mc = mk_client(
        get=make_resp(data={"data": [{"id": "1", "Account_Name": "Acme"}], "info": {}})
    )
    with patch.dict("os.environ", _ZOHO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zoho_list_records", {"module": "Accounts"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_zoho_create_record():
    from app.mcp.servers.zoho_crm_server import call_tool

    mc = mk_client(post=make_resp(data={"data": [{"code": "SUCCESS", "details": {"id": "1"}}]}))
    with patch.dict("os.environ", _ZOHO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "zoho_create_record",
            # argument key is "data", not "fields"
            {"module": "Contacts", "data": {"Last_Name": "Smith"}},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_zoho_update_record():
    from app.mcp.servers.zoho_crm_server import call_tool

    mc = mk_client(put=make_resp(data={"data": [{"code": "SUCCESS", "details": {}}]}))
    with patch.dict("os.environ", _ZOHO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "zoho_update_record",
            # argument key is "data", not "fields"
            {"module": "Contacts", "record_id": "1", "data": {"Phone": "123"}},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_zoho_delete_record():
    from app.mcp.servers.zoho_crm_server import call_tool

    mc = mk_client(delete=make_resp(data={"data": [{"code": "SUCCESS"}]}))
    with patch.dict("os.environ", _ZOHO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "zoho_delete_record", {"module": "Contacts", "record_id": "1"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_zoho_search_records():
    from app.mcp.servers.zoho_crm_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [], "info": {}}))
    with patch.dict("os.environ", _ZOHO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "zoho_search_records", {"module": "Contacts", "criteria": "Phone:equals:123"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_zoho_missing_env():
    from app.mcp.servers.zoho_crm_server import call_tool

    with patch.dict("os.environ", {"ZOHO_ACCESS_TOKEN": ""}):
        os.environ.pop("ZOHO_ACCESS_TOKEN", None)
        result = await call_tool("zoho_list_records", {"module": "Accounts"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Close CRM
# ---------------------------------------------------------------------------

_CLOSE = {"CLOSE_API_KEY": "api_123"}


@pytest.mark.asyncio
async def test_close_list_leads():
    from app.mcp.servers.close_crm_server import call_tool

    mc = mk_client(
        get=make_resp(data={"data": [{"id": "lead_1", "display_name": "Acme"}], "has_more": False})
    )
    with patch.dict("os.environ", _CLOSE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("close_list_leads", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_close_create_lead():
    from app.mcp.servers.close_crm_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "lead_2", "display_name": "New Lead"}))
    with patch.dict("os.environ", _CLOSE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("close_create_lead", {"name": "New Lead"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_close_update_lead():
    from app.mcp.servers.close_crm_server import call_tool

    mc = mk_client(put=make_resp(data={"id": "lead_2", "display_name": "Updated"}))
    with patch.dict("os.environ", _CLOSE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "close_update_lead", {"lead_id": "lead_2", "name": "Updated"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_close_list_contacts():
    from app.mcp.servers.close_crm_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [], "has_more": False}))
    with patch.dict("os.environ", _CLOSE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("close_list_contacts", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_close_create_contact():
    from app.mcp.servers.close_crm_server import call_tool

    mc = mk_client(
        post=make_resp(data={"id": "cont_1", "name": "Jane", "lead_id": "lead_1"})
    )
    with patch.dict("os.environ", _CLOSE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "close_create_contact", {"name": "Jane", "lead_id": "lead_1"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_close_create_activity():
    from app.mcp.servers.close_crm_server import call_tool

    mc = mk_client(
        post=make_resp(data={"id": "acti_1", "_type": "Note", "note": "Spoke to client"})
    )
    with patch.dict("os.environ", _CLOSE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "close_create_activity",
            {"activity_type": "note", "lead_id": "lead_1", "note": "Spoke to client"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_close_missing_env():
    from app.mcp.servers.close_crm_server import call_tool

    with patch.dict("os.environ", {"CLOSE_API_KEY": ""}):
        os.environ.pop("CLOSE_API_KEY", None)
        result = await call_tool("close_list_leads", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Copper CRM
# ---------------------------------------------------------------------------

_COPPER = {"COPPER_API_KEY": "cp-key", "COPPER_USER_EMAIL": "user@example.com"}


@pytest.mark.asyncio
async def test_copper_list_people():
    from app.mcp.servers.copper_server import call_tool

    mc = mk_client(post=make_resp(data=[{"id": 1, "name": "John"}]))
    with patch.dict("os.environ", _COPPER), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("copper_list_people", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_copper_create_person():
    from app.mcp.servers.copper_server import call_tool

    mc = mk_client(post=make_resp(data={"id": 2, "name": "Jane"}))
    with patch.dict("os.environ", _COPPER), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("copper_create_person", {"name": "Jane"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_copper_list_companies():
    from app.mcp.servers.copper_server import call_tool

    mc = mk_client(post=make_resp(data=[{"id": 1, "name": "Acme"}]))
    with patch.dict("os.environ", _COPPER), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("copper_list_companies", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_copper_list_opportunities():
    from app.mcp.servers.copper_server import call_tool

    mc = mk_client(post=make_resp(data=[{"id": 1, "name": "Opp 1"}]))
    with patch.dict("os.environ", _COPPER), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("copper_list_opportunities", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_copper_create_opportunity():
    from app.mcp.servers.copper_server import call_tool

    mc = mk_client(post=make_resp(data={"id": 2, "name": "New Deal"}))
    with patch.dict("os.environ", _COPPER), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("copper_create_opportunity", {"name": "New Deal"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_copper_missing_env():
    from app.mcp.servers.copper_server import call_tool

    with patch.dict("os.environ", {"COPPER_API_KEY": "", "COPPER_USER_EMAIL": ""}):
        os.environ.pop("COPPER_API_KEY", None)
        result = await call_tool("copper_list_people", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Attio CRM
# ---------------------------------------------------------------------------

_ATTIO = {"ATTIO_API_KEY": "attio-key"}


@pytest.mark.asyncio
async def test_attio_list_records():
    from app.mcp.servers.attio_server import call_tool

    mc = mk_client(post=make_resp(data={"data": [{"id": {"record_id": "1"}}]}))
    with patch.dict("os.environ", _ATTIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("attio_list_records", {"object_slug": "people"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_attio_create_record():
    from app.mcp.servers.attio_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"id": {"record_id": "2"}}}))
    with patch.dict("os.environ", _ATTIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "attio_create_record",
            {"object_slug": "people", "attributes": {"name": [{"full_name": "Alice"}]}},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_attio_update_record():
    from app.mcp.servers.attio_server import call_tool

    mc = mk_client(patch=make_resp(data={"data": {"id": {"record_id": "2"}}}))
    with patch.dict("os.environ", _ATTIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "attio_update_record",
            {"object_slug": "people", "record_id": "2", "attributes": {"phone": [{"number": "123"}]}},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_attio_list_notes():
    from app.mcp.servers.attio_server import call_tool

    mc = mk_client(get=make_resp(data={"data": []}))
    with patch.dict("os.environ", _ATTIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "attio_list_notes", {"object_slug": "people", "record_id": "2"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_attio_create_note():
    from app.mcp.servers.attio_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"id": "note1"}}))
    with patch.dict("os.environ", _ATTIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "attio_create_note",
            {
                "object_slug": "people",
                "record_id": "2",
                "title": "Call Notes",
                "content_plaintext": "Great call!",
            },
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_attio_missing_env():
    from app.mcp.servers.attio_server import call_tool

    with patch.dict("os.environ", {"ATTIO_API_KEY": ""}):
        os.environ.pop("ATTIO_API_KEY", None)
        result = await call_tool("attio_list_records", {"object_slug": "people"})
    assert "error" in result


@pytest.mark.asyncio
async def test_attio_unknown_tool():
    from app.mcp.servers.attio_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _ATTIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("attio_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Affinity
# ---------------------------------------------------------------------------

_AFF = {"AFFINITY_API_KEY": "aff-key"}


@pytest.mark.asyncio
async def test_affinity_list_lists():
    from app.mcp.servers.affinity_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 1, "name": "My List", "type": 0}]))
    with patch.dict("os.environ", _AFF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("affinity_list_lists", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_affinity_list_list_entries():
    from app.mcp.servers.affinity_server import call_tool

    mc = mk_client(get=make_resp(data={"list_entries": [], "next_page_token": None}))
    with patch.dict("os.environ", _AFF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("affinity_list_list_entries", {"list_id": 1})
    assert "error" not in result


@pytest.mark.asyncio
async def test_affinity_create_list_entry():
    from app.mcp.servers.affinity_server import call_tool

    mc = mk_client(post=make_resp(data={"id": 1, "list_id": 1}))
    with patch.dict("os.environ", _AFF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "affinity_create_list_entry", {"list_id": 1, "entity_id": 100, "entity_type": 1}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_affinity_list_persons():
    from app.mcp.servers.affinity_server import call_tool

    mc = mk_client(
        get=make_resp(data={"persons": [{"id": 1, "first_name": "Alice"}], "next_page_token": None})
    )
    with patch.dict("os.environ", _AFF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("affinity_list_persons", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_affinity_list_organizations():
    from app.mcp.servers.affinity_server import call_tool

    mc = mk_client(
        get=make_resp(
            data={"organizations": [{"id": 1, "name": "Acme"}], "next_page_token": None}
        )
    )
    with patch.dict("os.environ", _AFF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("affinity_list_organizations", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_affinity_missing_env():
    from app.mcp.servers.affinity_server import call_tool

    with patch.dict("os.environ", {"AFFINITY_API_KEY": ""}):
        os.environ.pop("AFFINITY_API_KEY", None)
        result = await call_tool("affinity_list_lists", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Apollo
# ---------------------------------------------------------------------------

_APOLLO = {"APOLLO_API_KEY": "apollo-key"}


@pytest.mark.asyncio
async def test_apollo_search_people():
    from app.mcp.servers.apollo_server import call_tool

    mc = mk_client(
        post=make_resp(
            data={
                "people": [{"id": "p1", "name": "Alice", "title": "CEO", "organization": {}}],
                "pagination": {},
            }
        )
    )
    with patch.dict("os.environ", _APOLLO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("apollo_search_people", {"query": "CEO"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_apollo_enrich_person():
    from app.mcp.servers.apollo_server import call_tool

    mc = mk_client(
        post=make_resp(data={"person": {"id": "p1", "name": "Alice", "email": "a@b.com"}})
    )
    with patch.dict("os.environ", _APOLLO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "apollo_enrich_person", {"email": "a@b.com"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_apollo_search_companies():
    from app.mcp.servers.apollo_server import call_tool

    mc = mk_client(
        post=make_resp(data={"organizations": [{"id": "o1", "name": "Acme"}], "pagination": {}})
    )
    with patch.dict("os.environ", _APOLLO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("apollo_search_companies", {"query": "SaaS"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_apollo_enrich_company():
    from app.mcp.servers.apollo_server import call_tool

    mc = mk_client(
        post=make_resp(data={"organization": {"id": "o1", "name": "Acme", "domain": "acme.com"}})
    )
    with patch.dict("os.environ", _APOLLO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("apollo_enrich_company", {"domain": "acme.com"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_apollo_get_email():
    from app.mcp.servers.apollo_server import call_tool

    mc = mk_client(
        post=make_resp(data={"contacts": [{"id": "c1", "email": "a@acme.com", "email_status": "verified"}]})
    )
    with patch.dict("os.environ", _APOLLO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "apollo_get_email", {"first_name": "Alice", "last_name": "Smith", "domain": "acme.com"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_apollo_missing_env():
    from app.mcp.servers.apollo_server import call_tool

    with patch.dict("os.environ", {"APOLLO_API_KEY": ""}):
        os.environ.pop("APOLLO_API_KEY", None)
        result = await call_tool("apollo_search_people", {"query": "test"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Gong
# ---------------------------------------------------------------------------

_GONG = {"GONG_ACCESS_KEY": "gk", "GONG_ACCESS_KEY_SECRET": "gs"}


@pytest.mark.asyncio
async def test_gong_list_calls():
    from app.mcp.servers.gong_server import call_tool

    mc = mk_client(
        get=make_resp(
            data={"calls": [{"metaData": {"id": "c1", "title": "Demo"}}], "records": {}}
        )
    )
    with patch.dict("os.environ", _GONG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gong_list_calls", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_gong_get_call_transcript():
    from app.mcp.servers.gong_server import call_tool

    mc = mk_client(
        post=make_resp(
            data={"callTranscripts": [{"callId": "c1", "transcript": []}]}
        )
    )
    with patch.dict("os.environ", _GONG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gong_get_call_transcript", {"call_id": "c1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_gong_list_users():
    from app.mcp.servers.gong_server import call_tool

    mc = mk_client(
        get=make_resp(data={"users": [{"id": "u1", "firstName": "Alice"}], "records": {}})
    )
    with patch.dict("os.environ", _GONG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gong_list_users", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_gong_get_call_stats():
    from app.mcp.servers.gong_server import call_tool

    # gong_get_call_stats uses GET with optional date params, not POST
    mc = mk_client(
        get=make_resp(
            data={"callsStats": {"averageDuration": 1800, "totalCalls": 10}}
        )
    )
    with patch.dict("os.environ", _GONG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "gong_get_call_stats",
            {"from_date_time": "2024-01-01T00:00:00Z", "to_date_time": "2024-01-31T23:59:59Z"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_gong_missing_env():
    from app.mcp.servers.gong_server import call_tool

    with patch.dict("os.environ", {"GONG_ACCESS_KEY": "", "GONG_ACCESS_KEY_SECRET": ""}):
        os.environ.pop("GONG_ACCESS_KEY", None)
        result = await call_tool("gong_list_calls", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Planhat
# ---------------------------------------------------------------------------

_PLANHAT = {"PLANHAT_API_KEY": "ph-key"}


@pytest.mark.asyncio
async def test_planhat_list_companies():
    from app.mcp.servers.planhat_server import call_tool

    mc = mk_client(get=make_resp(data=[{"_id": "1", "name": "Acme", "mrr": 1000}]))
    with patch.dict("os.environ", _PLANHAT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("planhat_list_companies", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_planhat_create_company():
    from app.mcp.servers.planhat_server import call_tool

    mc = mk_client(post=make_resp(data={"_id": "2", "name": "Beta Inc"}))
    with patch.dict("os.environ", _PLANHAT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("planhat_create_company", {"name": "Beta Inc"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_planhat_list_endusers():
    from app.mcp.servers.planhat_server import call_tool

    mc = mk_client(get=make_resp(data=[{"_id": "u1", "name": "User 1"}]))
    with patch.dict("os.environ", _PLANHAT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("planhat_list_endusers", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_planhat_create_enduser():
    from app.mcp.servers.planhat_server import call_tool

    mc = mk_client(post=make_resp(data={"_id": "u2", "name": "New User"}))
    with patch.dict("os.environ", _PLANHAT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "planhat_create_enduser",
            {"name": "New User", "email": "new@acme.com", "company_id": "1"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_planhat_log_activity():
    from app.mcp.servers.planhat_server import call_tool

    mc = mk_client(post=make_resp(data={"_id": "a1"}))
    with patch.dict("os.environ", _PLANHAT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "planhat_log_activity",
            {"action": "login", "company_id": "1"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_planhat_missing_env():
    from app.mcp.servers.planhat_server import call_tool

    with patch.dict("os.environ", {"PLANHAT_API_KEY": ""}):
        os.environ.pop("PLANHAT_API_KEY", None)
        result = await call_tool("planhat_list_companies", {})
    assert "error" in result
