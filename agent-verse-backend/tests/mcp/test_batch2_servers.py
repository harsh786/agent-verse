"""Batch 2 MCP server unit tests — all 25 new servers mocked via httpx.

Tests use @pytest.mark.asyncio and mock httpx.AsyncClient so no real network calls
are made.  At least 2 tests per server (happy-path + missing auth key).
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def make_resp(status: int = 200, data: Any = None) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data if data is not None else {}
    m.text = str(data or "")
    m.headers = {"Content-Type": "application/json"}
    m.raise_for_status = MagicMock()
    return m


def mk_client(**overrides: MagicMock) -> AsyncMock:
    mc = AsyncMock()
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=False)
    default = make_resp()
    for method in ("get", "post", "put", "patch", "delete"):
        setattr(mc, method, AsyncMock(return_value=overrides.get(method, default)))
    return mc


# ===========================================================================
# 1. Airtable
# ===========================================================================

_AT = {"AIRTABLE_API_KEY": "key-test", "AIRTABLE_BASE_ID": "appXXXXXX"}


@pytest.mark.asyncio
async def test_airtable_list_records_happy():
    from app.mcp.servers.airtable_server import call_tool

    mc = mk_client(get=make_resp(data={"records": [{"id": "rec1", "fields": {"Name": "Alice"}}]}))
    with patch.dict("os.environ", _AT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("airtable_list_records", {"table_name": "Contacts"})
    assert "records" in result


@pytest.mark.asyncio
async def test_airtable_missing_api_key():
    from app.mcp.servers.airtable_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("airtable_list_records", {"table_name": "Contacts"})
    assert "error" in result
    assert "AIRTABLE_API_KEY" in result["error"]


@pytest.mark.asyncio
async def test_airtable_create_record():
    from app.mcp.servers.airtable_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "recNEW", "fields": {"Name": "Bob"}}))
    with patch.dict("os.environ", _AT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("airtable_create_record", {
            "table_name": "Contacts", "fields": {"Name": "Bob"},
        })
    assert "id" in result


@pytest.mark.asyncio
async def test_airtable_unknown_tool():
    from app.mcp.servers.airtable_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _AT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("airtable_nonexistent", {})
    assert "error" in result


# ===========================================================================
# 2. Capsule CRM
# ===========================================================================

_CAPS = {"CAPSULE_API_TOKEN": "capsule-token-123"}


@pytest.mark.asyncio
async def test_capsule_list_contacts_happy():
    from app.mcp.servers.capsule_crm_server import call_tool

    mc = mk_client(get=make_resp(data={"parties": [{"id": 1, "firstName": "Jane"}]}))
    with patch.dict("os.environ", _CAPS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("capsule_list_contacts", {"page": 1})
    assert "parties" in result


@pytest.mark.asyncio
async def test_capsule_missing_token():
    from app.mcp.servers.capsule_crm_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("capsule_list_contacts", {})
    assert "error" in result
    assert "CAPSULE_API_TOKEN" in result["error"]


@pytest.mark.asyncio
async def test_capsule_create_opportunity():
    from app.mcp.servers.capsule_crm_server import call_tool

    mc = mk_client(post=make_resp(data={"opportunity": {"id": 99, "name": "Big Deal"}}))
    with patch.dict("os.environ", _CAPS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("capsule_create_opportunity", {"name": "Big Deal", "value": 50000})
    assert result is not None


# ===========================================================================
# 3. Clearbit
# ===========================================================================

_CB = {"CLEARBIT_API_KEY": "sk_clearbit_test"}


@pytest.mark.asyncio
async def test_clearbit_enrich_person_happy():
    from app.mcp.servers.clearbit_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "p1", "name": {"fullName": "Jane Doe"}}))
    with patch.dict("os.environ", _CB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("clearbit_enrich_person", {"email": "jane@example.com"})
    assert "id" in result


@pytest.mark.asyncio
async def test_clearbit_missing_api_key():
    from app.mcp.servers.clearbit_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("clearbit_enrich_person", {"email": "x@y.com"})
    assert "CLEARBIT_API_KEY" in result["error"]


@pytest.mark.asyncio
async def test_clearbit_reveal_company_from_ip():
    from app.mcp.servers.clearbit_server import call_tool

    mc = mk_client(get=make_resp(data={"company": {"name": "Google"}}))
    with patch.dict("os.environ", _CB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("clearbit_reveal_company_from_ip", {"ip": "8.8.8.8"})
    assert result is not None


# ===========================================================================
# 4. Dynamics 365
# ===========================================================================

_D365 = {
    "DYNAMICS365_ACCESS_TOKEN": "d365-bearer-token",
    "DYNAMICS365_ORG_URL": "https://myorg.crm.dynamics.com",
}


@pytest.mark.asyncio
async def test_dynamics365_list_accounts_happy():
    from app.mcp.servers.dynamics365_server import call_tool

    mc = mk_client(get=make_resp(data={"value": [{"accountid": "a1", "name": "Contoso"}]}))
    with patch.dict("os.environ", _D365), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("dynamics365_list_accounts", {"top": 10})
    assert "value" in result


@pytest.mark.asyncio
async def test_dynamics365_missing_token():
    from app.mcp.servers.dynamics365_server import call_tool

    with patch.dict("os.environ", {"DYNAMICS365_ORG_URL": "https://x.crm.dynamics.com"}, clear=True):
        result = await call_tool("dynamics365_list_accounts", {})
    assert "DYNAMICS365_ACCESS_TOKEN" in result["error"]


@pytest.mark.asyncio
async def test_dynamics365_missing_org_url():
    from app.mcp.servers.dynamics365_server import call_tool

    with patch.dict("os.environ", {"DYNAMICS365_ACCESS_TOKEN": "tok"}, clear=True):
        result = await call_tool("dynamics365_list_accounts", {})
    assert "DYNAMICS365_ORG_URL" in result["error"]


@pytest.mark.asyncio
async def test_dynamics365_create_lead():
    from app.mcp.servers.dynamics365_server import call_tool

    resp = make_resp(status=204)
    resp.headers = {"OData-EntityId": "https://myorg.crm.dynamics.com/api/data/v9.2/leads(abc123)"}
    mc = mk_client(post=resp)
    with patch.dict("os.environ", _D365), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("dynamics365_create_lead", {"lastname": "Smith", "subject": "Test Lead"})
    assert result.get("created") is True


# ===========================================================================
# 5. Encharge
# ===========================================================================

_ENC = {"ENCHARGE_API_KEY": "enc-key-abc"}


@pytest.mark.asyncio
async def test_encharge_create_user_happy():
    from app.mcp.servers.encharge_server import call_tool

    mc = mk_client(post=make_resp(data={"person": {"id": "u1", "email": "bob@acme.com"}}))
    with patch.dict("os.environ", _ENC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("encharge_create_user", {
            "email": "bob@acme.com", "firstName": "Bob",
        })
    assert "person" in result


@pytest.mark.asyncio
async def test_encharge_missing_api_key():
    from app.mcp.servers.encharge_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("encharge_create_user", {"email": "x@y.com"})
    assert "ENCHARGE_API_KEY" in result["error"]


@pytest.mark.asyncio
async def test_encharge_track_event():
    from app.mcp.servers.encharge_server import call_tool

    mc = mk_client(post=make_resp(data={"status": "ok"}))
    with patch.dict("os.environ", _ENC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("encharge_track_event", {
            "email": "bob@acme.com",
            "name": "Signed Up",
            "properties": {"plan": "pro"},
        })
    assert result is not None


# ===========================================================================
# 6. Freshsales
# ===========================================================================

_FS = {"FRESHSALES_API_KEY": "fs-key", "FRESHSALES_DOMAIN": "testcompany"}


@pytest.mark.asyncio
async def test_freshsales_list_contacts_happy():
    from app.mcp.servers.freshsales_server import call_tool

    mc = mk_client(get=make_resp(data={"contacts": [{"id": 1, "email": "a@b.com"}]}))
    with patch.dict("os.environ", _FS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("freshsales_list_contacts", {"page": 1})
    assert "contacts" in result


@pytest.mark.asyncio
async def test_freshsales_missing_domain():
    from app.mcp.servers.freshsales_server import call_tool

    with patch.dict("os.environ", {"FRESHSALES_API_KEY": "key"}, clear=True):
        result = await call_tool("freshsales_list_contacts", {})
    assert "FRESHSALES_DOMAIN" in result["error"]


@pytest.mark.asyncio
async def test_freshsales_create_deal():
    from app.mcp.servers.freshsales_server import call_tool

    mc = mk_client(post=make_resp(data={"deal": {"id": 42, "name": "Mega Deal"}}))
    with patch.dict("os.environ", _FS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("freshsales_create_deal", {"name": "Mega Deal", "amount": 100000})
    assert "deal" in result


# ===========================================================================
# 7. FullContact
# ===========================================================================

_FC = {"FULLCONTACT_API_KEY": "fc-apikey-xyz"}


@pytest.mark.asyncio
async def test_fullcontact_enrich_person_happy():
    from app.mcp.servers.fullcontact_server import call_tool

    mc = mk_client(post=make_resp(data={"fullName": "Jane Doe", "ageRange": "25-34"}))
    with patch.dict("os.environ", _FC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("fullcontact_enrich_person", {"email": "jane@fullcontact.com"})
    assert "fullName" in result


@pytest.mark.asyncio
async def test_fullcontact_missing_api_key():
    from app.mcp.servers.fullcontact_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("fullcontact_enrich_person", {"email": "x@y.com"})
    assert "FULLCONTACT_API_KEY" in result["error"]


@pytest.mark.asyncio
async def test_fullcontact_enrich_company():
    from app.mcp.servers.fullcontact_server import call_tool

    mc = mk_client(post=make_resp(data={"name": "FullContact", "employees": 100}))
    with patch.dict("os.environ", _FC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("fullcontact_enrich_company", {"domain": "fullcontact.com"})
    assert result is not None


# ===========================================================================
# 8. Gainsight
# ===========================================================================

_GS = {"GAINSIGHT_ACCESS_KEY": "gs-access-key"}


@pytest.mark.asyncio
async def test_gainsight_list_accounts_happy():
    from app.mcp.servers.gainsight_server import call_tool

    mc = mk_client(get=make_resp(data={"accounts": [{"id": "gs1", "name": "BigCorp"}]}))
    with patch.dict("os.environ", _GS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gainsight_list_accounts", {"pageSize": 10})
    assert "accounts" in result


@pytest.mark.asyncio
async def test_gainsight_missing_key():
    from app.mcp.servers.gainsight_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("gainsight_list_accounts", {})
    assert "GAINSIGHT_ACCESS_KEY" in result["error"]


@pytest.mark.asyncio
async def test_gainsight_create_cta():
    from app.mcp.servers.gainsight_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "cta1", "name": "At Risk"}))
    with patch.dict("os.environ", _GS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gainsight_create_call_to_action", {
            "account_id": "acct1", "name": "At Risk", "reason": "No login in 30 days",
        })
    assert result is not None


# ===========================================================================
# 9. GoHighLevel
# ===========================================================================

_HL = {"HIGHLEVEL_API_KEY": "hl-api-key-123"}


@pytest.mark.asyncio
async def test_highlevel_list_contacts_happy():
    from app.mcp.servers.highlevel_server import call_tool

    mc = mk_client(get=make_resp(data={"contacts": [{"id": "c1", "email": "jane@test.com"}]}))
    with patch.dict("os.environ", _HL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("highlevel_list_contacts", {"limit": 10})
    assert "contacts" in result


@pytest.mark.asyncio
async def test_highlevel_missing_api_key():
    from app.mcp.servers.highlevel_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("highlevel_list_contacts", {})
    assert "HIGHLEVEL_API_KEY" in result["error"]


@pytest.mark.asyncio
async def test_highlevel_send_sms():
    from app.mcp.servers.highlevel_server import call_tool

    mc = mk_client(post=make_resp(data={"messageId": "msg1", "status": "sent"}))
    with patch.dict("os.environ", _HL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("highlevel_send_sms", {
            "contactId": "c1", "message": "Hello from AgentVerse!",
        })
    assert result is not None


# ===========================================================================
# 10. HubSpot Marketing
# ===========================================================================

_HSMA = {"HUBSPOT_API_KEY": "hub-priv-app-key"}


@pytest.mark.asyncio
async def test_hubspot_marketing_list_forms_happy():
    from app.mcp.servers.hubspot_marketing_server import call_tool

    mc = mk_client(get=make_resp(data=[{"guid": "f1", "name": "Contact Form"}]))
    with patch.dict("os.environ", _HSMA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("hubspot_marketing_list_forms", {"limit": 10})
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_hubspot_marketing_missing_key():
    from app.mcp.servers.hubspot_marketing_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("hubspot_marketing_list_forms", {})
    assert "HUBSPOT_API_KEY" in result["error"]


@pytest.mark.asyncio
async def test_hubspot_marketing_create_list():
    from app.mcp.servers.hubspot_marketing_server import call_tool

    mc = mk_client(post=make_resp(data={"listId": 42, "name": "VIP Leads"}))
    with patch.dict("os.environ", _HSMA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("hubspot_marketing_create_list", {
            "name": "VIP Leads", "dynamic": False,
        })
    assert result is not None


# ===========================================================================
# 11. Insightly
# ===========================================================================

_INS = {"INSIGHTLY_API_KEY": "ins-api-key"}


@pytest.mark.asyncio
async def test_insightly_list_contacts_happy():
    from app.mcp.servers.insightly_server import call_tool

    mc = mk_client(get=make_resp(data=[{"CONTACT_ID": 1, "FIRST_NAME": "Alice"}]))
    with patch.dict("os.environ", _INS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("insightly_list_contacts", {"top": 10})
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_insightly_missing_key():
    from app.mcp.servers.insightly_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("insightly_list_contacts", {})
    assert "INSIGHTLY_API_KEY" in result["error"]


@pytest.mark.asyncio
async def test_insightly_create_task():
    from app.mcp.servers.insightly_server import call_tool

    mc = mk_client(post=make_resp(data={"TASK_ID": 99, "TITLE": "Follow up"}))
    with patch.dict("os.environ", _INS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("insightly_create_task", {
            "TITLE": "Follow up", "STATUS": "NOT STARTED",
        })
    assert "TASK_ID" in result


# ===========================================================================
# 12. Klenty
# ===========================================================================

_KL = {"KLENTY_API_KEY": "klenty-key-xyz"}


@pytest.mark.asyncio
async def test_klenty_list_prospects_happy():
    from app.mcp.servers.klenty_server import call_tool

    mc = mk_client(get=make_resp(data={"prospects": [{"Email": "a@b.com", "FirstName": "Alice"}]}))
    with patch.dict("os.environ", _KL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("klenty_list_prospects", {"page": 1})
    assert "prospects" in result


@pytest.mark.asyncio
async def test_klenty_missing_api_key():
    from app.mcp.servers.klenty_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("klenty_list_prospects", {})
    assert "KLENTY_API_KEY" in result["error"]


@pytest.mark.asyncio
async def test_klenty_start_cadence():
    from app.mcp.servers.klenty_server import call_tool

    mc = mk_client(post=make_resp(data={"status": "started", "email": "a@b.com"}))
    with patch.dict("os.environ", _KL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("klenty_start_cadence", {
            "Email": "a@b.com", "CadenceName": "Welcome Sequence",
        })
    assert result is not None


# ===========================================================================
# 13. Konnektive
# ===========================================================================

_KON = {"KONNEKTIVE_LOGIN_ID": "kon-login", "KONNEKTIVE_PASSWORD": "kon-pass"}


@pytest.mark.asyncio
async def test_konnektive_list_orders_happy():
    from app.mcp.servers.konnektive_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"orderId": "101", "orderStatus": "COMPLETE"}]}))
    with patch.dict("os.environ", _KON), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("konnektive_list_orders", {"page": 1})
    assert "data" in result


@pytest.mark.asyncio
async def test_konnektive_missing_login():
    from app.mcp.servers.konnektive_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("konnektive_list_orders", {})
    assert "KONNEKTIVE_LOGIN_ID" in result["error"]


@pytest.mark.asyncio
async def test_konnektive_get_customer():
    from app.mcp.servers.konnektive_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"customerId": "c1", "firstName": "John"}]}))
    with patch.dict("os.environ", _KON), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("konnektive_get_customer", {"customerId": "c1"})
    assert "data" in result


# ===========================================================================
# 14. Leadpages
# ===========================================================================

_LP = {"LEADPAGES_API_KEY": "lp-api-key-123"}


@pytest.mark.asyncio
async def test_leadpages_list_pages_happy():
    from app.mcp.servers.leadpages_server import call_tool

    mc = mk_client(get=make_resp(data={"pages": [{"id": "pg1", "name": "Landing Page"}]}))
    with patch.dict("os.environ", _LP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("leadpages_list_pages", {"limit": 10})
    assert "pages" in result


@pytest.mark.asyncio
async def test_leadpages_missing_api_key():
    from app.mcp.servers.leadpages_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("leadpages_list_pages", {})
    assert "LEADPAGES_API_KEY" in result["error"]


@pytest.mark.asyncio
async def test_leadpages_get_page_stats():
    from app.mcp.servers.leadpages_server import call_tool

    mc = mk_client(get=make_resp(data={"views": 1500, "conversions": 150, "conversion_rate": 10.0}))
    with patch.dict("os.environ", _LP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("leadpages_get_page_stats", {"page_id": "pg1"})
    assert "views" in result


# ===========================================================================
# 15. Lemlist
# ===========================================================================

_LL = {"LEMLIST_API_KEY": "ll-api-key"}


@pytest.mark.asyncio
async def test_lemlist_list_campaigns_happy():
    from app.mcp.servers.lemlist_server import call_tool

    mc = mk_client(get=make_resp(data=[{"_id": "camp1", "name": "Q4 Outreach"}]))
    with patch.dict("os.environ", _LL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("lemlist_list_campaigns", {"limit": 10})
    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_lemlist_missing_api_key():
    from app.mcp.servers.lemlist_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("lemlist_list_campaigns", {})
    assert "LEMLIST_API_KEY" in result["error"]


@pytest.mark.asyncio
async def test_lemlist_add_lead():
    from app.mcp.servers.lemlist_server import call_tool

    mc = mk_client(post=make_resp(data={"_id": "lead1", "email": "j@test.com"}))
    with patch.dict("os.environ", _LL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("lemlist_add_lead", {
            "campaign_id": "camp1", "email": "j@test.com", "firstName": "John",
        })
    assert result is not None


# ===========================================================================
# 16. Outreach
# ===========================================================================

_OUT = {"OUTREACH_ACCESS_TOKEN": "out-access-token"}


@pytest.mark.asyncio
async def test_outreach_list_prospects_happy():
    from app.mcp.servers.outreach_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": 1, "attributes": {"emails": ["a@b.com"]}}]}))
    with patch.dict("os.environ", _OUT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("outreach_list_prospects", {"page_size": 10})
    assert "data" in result


@pytest.mark.asyncio
async def test_outreach_missing_token():
    from app.mcp.servers.outreach_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("outreach_list_prospects", {})
    assert "OUTREACH_ACCESS_TOKEN" in result["error"]


@pytest.mark.asyncio
async def test_outreach_create_prospect():
    from app.mcp.servers.outreach_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"id": 42, "type": "prospect"}}))
    with patch.dict("os.environ", _OUT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("outreach_create_prospect", {
            "emails": ["sales@bigco.com"], "firstName": "Sales", "lastName": "Rep",
        })
    assert "data" in result


# ===========================================================================
# 17. Overloop
# ===========================================================================

_OVR = {"OVERLOOP_API_KEY": "overloop-key-123"}


@pytest.mark.asyncio
async def test_overloop_list_prospects_happy():
    from app.mcp.servers.overloop_server import call_tool

    mc = mk_client(get=make_resp(data={"prospects": [{"id": 1, "email": "a@b.com"}]}))
    with patch.dict("os.environ", _OVR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("overloop_list_prospects", {"page": 1})
    assert "prospects" in result


@pytest.mark.asyncio
async def test_overloop_missing_key():
    from app.mcp.servers.overloop_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("overloop_list_prospects", {})
    assert "OVERLOOP_API_KEY" in result["error"]


@pytest.mark.asyncio
async def test_overloop_enroll_prospect():
    from app.mcp.servers.overloop_server import call_tool

    mc = mk_client(post=make_resp(data={"id": 99, "status": "enrolled"}))
    with patch.dict("os.environ", _OVR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("overloop_enroll_prospect", {
            "prospect_id": 1, "campaign_id": 5,
        })
    assert result is not None


# ===========================================================================
# 18. Reply.io
# ===========================================================================

_RIO = {"REPLYIO_API_KEY": "replyio-key-abc"}


@pytest.mark.asyncio
async def test_replyio_list_people_happy():
    from app.mcp.servers.reply_io_server import call_tool

    mc = mk_client(get=make_resp(data={"people": [{"id": 1, "email": "a@b.com"}]}))
    with patch.dict("os.environ", _RIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("replyio_list_people", {"page": 1})
    assert "people" in result


@pytest.mark.asyncio
async def test_replyio_missing_key():
    from app.mcp.servers.reply_io_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("replyio_list_people", {})
    assert "REPLYIO_API_KEY" in result["error"]


@pytest.mark.asyncio
async def test_replyio_push_to_sequence():
    from app.mcp.servers.reply_io_server import call_tool

    mc = mk_client(post=make_resp(data={"ok": True}))
    with patch.dict("os.environ", _RIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("replyio_push_to_sequence", {
            "email": "a@b.com", "sequenceId": 7,
        })
    assert result is not None


# ===========================================================================
# 19. Salesloft
# ===========================================================================

_SL = {"SALESLOFT_API_KEY": "sl-key-xyz"}


@pytest.mark.asyncio
async def test_salesloft_list_people_happy():
    from app.mcp.servers.salesloft_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": 1, "email_address": "a@b.com"}]}))
    with patch.dict("os.environ", _SL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("salesloft_list_people", {"per_page": 10})
    assert "data" in result


@pytest.mark.asyncio
async def test_salesloft_missing_key():
    from app.mcp.servers.salesloft_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("salesloft_list_people", {})
    assert "SALESLOFT_API_KEY" in result["error"]


@pytest.mark.asyncio
async def test_salesloft_create_person():
    from app.mcp.servers.salesloft_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"id": 42, "email_address": "b@c.com"}}))
    with patch.dict("os.environ", _SL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("salesloft_create_person", {
            "email_address": "b@c.com", "first_name": "Bob",
        })
    assert "data" in result


# ===========================================================================
# 20. Segment
# ===========================================================================

_SEG = {"SEGMENT_WRITE_KEY": "seg-write-key-abc"}


@pytest.mark.asyncio
async def test_segment_track_event_happy():
    from app.mcp.servers.segment_server import call_tool

    mc = mk_client(post=make_resp(data={"success": True}))
    with patch.dict("os.environ", _SEG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("segment_track_event", {
            "userId": "user_123",
            "event": "Order Completed",
            "properties": {"revenue": 99.9, "currency": "USD"},
        })
    assert "success" in result


@pytest.mark.asyncio
async def test_segment_missing_write_key():
    from app.mcp.servers.segment_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("segment_track_event", {"event": "Test"})
    assert "SEGMENT_WRITE_KEY" in result["error"]


@pytest.mark.asyncio
async def test_segment_identify_user():
    from app.mcp.servers.segment_server import call_tool

    mc = mk_client(post=make_resp(data={"success": True}))
    with patch.dict("os.environ", _SEG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("segment_identify_user", {
            "userId": "user_123",
            "traits": {"name": "Alice", "email": "alice@example.com", "plan": "enterprise"},
        })
    assert result is not None


# ===========================================================================
# 21. Snov.io
# ===========================================================================

_SNO = {"SNOVIO_CLIENT_ID": "snov-cid", "SNOVIO_CLIENT_SECRET": "snov-csec"}


@pytest.mark.asyncio
async def test_snovio_verify_email_happy():
    from app.mcp.servers.snovio_server import call_tool

    with patch("app.mcp.servers.snovio_server._get_access_token", new=AsyncMock(return_value="tok")):
        mc = mk_client(post=make_resp(data={"emails": [{"email": "a@b.com", "status": "valid"}]}))
        with patch.dict("os.environ", _SNO), patch("httpx.AsyncClient") as Cls:
            Cls.return_value = mc
            result = await call_tool("snovio_verify_email", {"email": "a@b.com"})
    assert result is not None


@pytest.mark.asyncio
async def test_snovio_missing_client_id():
    from app.mcp.servers.snovio_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("snovio_verify_email", {"email": "a@b.com"})
    assert "SNOVIO_CLIENT_ID" in result["error"]


@pytest.mark.asyncio
async def test_snovio_missing_client_secret():
    from app.mcp.servers.snovio_server import call_tool

    with patch.dict("os.environ", {"SNOVIO_CLIENT_ID": "cid"}, clear=True):
        result = await call_tool("snovio_verify_email", {"email": "a@b.com"})
    assert "SNOVIO_CLIENT_SECRET" in result["error"]


# ===========================================================================
# 22. SugarCRM
# ===========================================================================

_SUGAR = {
    "SUGARCRM_ACCESS_TOKEN": "sugar-token",
    "SUGARCRM_INSTANCE_URL": "https://mysugar.sugarcrm.com",
}


@pytest.mark.asyncio
async def test_sugarcrm_list_accounts_happy():
    from app.mcp.servers.sugarcrm_server import call_tool

    mc = mk_client(get=make_resp(data={"records": [{"id": "a1", "name": "BigCorp"}]}))
    with patch.dict("os.environ", _SUGAR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sugarcrm_list_accounts", {"limit": 10})
    assert "records" in result


@pytest.mark.asyncio
async def test_sugarcrm_missing_token():
    from app.mcp.servers.sugarcrm_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("sugarcrm_list_accounts", {})
    assert "SUGARCRM_ACCESS_TOKEN" in result["error"]


@pytest.mark.asyncio
async def test_sugarcrm_missing_instance_url():
    from app.mcp.servers.sugarcrm_server import call_tool

    with patch.dict("os.environ", {"SUGARCRM_ACCESS_TOKEN": "tok"}, clear=True):
        result = await call_tool("sugarcrm_list_accounts", {})
    assert "SUGARCRM_INSTANCE_URL" in result["error"]


@pytest.mark.asyncio
async def test_sugarcrm_create_lead():
    from app.mcp.servers.sugarcrm_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "lead1", "last_name": "Johnson"}))
    with patch.dict("os.environ", _SUGAR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sugarcrm_create_lead", {
            "last_name": "Johnson", "first_name": "Mark",
        })
    assert "id" in result


# ===========================================================================
# 23. Vero
# ===========================================================================

_VERO = {"VERO_AUTH_TOKEN": "vero-token-xyz"}


@pytest.mark.asyncio
async def test_vero_track_event_happy():
    from app.mcp.servers.vero_server import call_tool

    mc = mk_client(post=make_resp(data={"ok": True}))
    with patch.dict("os.environ", _VERO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("vero_track_event", {
            "identity": {"id": "u1", "email": "a@b.com"},
            "event_name": "Purchased",
            "data": {"product": "Pro Plan"},
        })
    assert result is not None


@pytest.mark.asyncio
async def test_vero_missing_token():
    from app.mcp.servers.vero_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("vero_track_event", {
            "identity": {"id": "u1"},
            "event_name": "Test",
        })
    assert "VERO_AUTH_TOKEN" in result["error"]


@pytest.mark.asyncio
async def test_vero_identify_user():
    from app.mcp.servers.vero_server import call_tool

    mc = mk_client(post=make_resp(data={"ok": True}))
    with patch.dict("os.environ", _VERO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("vero_identify_user", {
            "id": "u1", "email": "a@b.com", "first_name": "Alice",
        })
    assert result is not None


# ===========================================================================
# 24. Podio
# ===========================================================================

_PODIO = {"PODIO_ACCESS_TOKEN": "podio-oauth-token"}


@pytest.mark.asyncio
async def test_podio_list_items_happy():
    from app.mcp.servers.podio_server import call_tool

    mc = mk_client(post=make_resp(data={"items": [{"item_id": 1, "title": "Task 1"}], "total": 1}))
    with patch.dict("os.environ", _PODIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("podio_list_items", {"app_id": 12345, "limit": 10})
    assert "items" in result


@pytest.mark.asyncio
async def test_podio_missing_token():
    from app.mcp.servers.podio_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("podio_list_items", {"app_id": 1})
    assert "PODIO_ACCESS_TOKEN" in result["error"]


@pytest.mark.asyncio
async def test_podio_create_item():
    from app.mcp.servers.podio_server import call_tool

    mc = mk_client(post=make_resp(data={"item_id": 99, "title": "New Item"}))
    with patch.dict("os.environ", _PODIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("podio_create_item", {
            "app_id": 12345,
            "fields": {"title": "New Item", "status": "open"},
        })
    assert "item_id" in result


@pytest.mark.asyncio
async def test_podio_create_task():
    from app.mcp.servers.podio_server import call_tool

    mc = mk_client(post=make_resp(data={"task_id": 55, "text": "Review PR"}))
    with patch.dict("os.environ", _PODIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("podio_create_task", {
            "text": "Review PR",
            "due_date": "2026-07-15",
            "ref_type": "item",
            "ref_id": 99,
        })
    assert "task_id" in result


# ===========================================================================
# 25. Orbit
# ===========================================================================

_ORBIT = {"ORBIT_API_KEY": "orbit-key-abc", "ORBIT_WORKSPACE": "agentverse"}


@pytest.mark.asyncio
async def test_orbit_list_members_happy():
    from app.mcp.servers.orbit_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "m1", "attributes": {"name": "Alice"}}]}))
    with patch.dict("os.environ", _ORBIT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("orbit_list_members", {"page": 1, "items": 10})
    assert "data" in result


@pytest.mark.asyncio
async def test_orbit_missing_api_key():
    from app.mcp.servers.orbit_server import call_tool

    with patch.dict("os.environ", {}, clear=True):
        result = await call_tool("orbit_list_members", {})
    assert "ORBIT_API_KEY" in result["error"]


@pytest.mark.asyncio
async def test_orbit_missing_workspace():
    from app.mcp.servers.orbit_server import call_tool

    with patch.dict("os.environ", {"ORBIT_API_KEY": "key"}, clear=True):
        result = await call_tool("orbit_list_members", {})
    assert "ORBIT_WORKSPACE" in result["error"]


@pytest.mark.asyncio
async def test_orbit_track_activity():
    from app.mcp.servers.orbit_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"id": "act1", "type": "activity"}}))
    with patch.dict("os.environ", _ORBIT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("orbit_track_activity", {
            "member_email": "alice@community.dev",
            "activity_type": "product:review",
            "title": "Left a 5-star review",
        })
    assert "data" in result


@pytest.mark.asyncio
async def test_orbit_get_workspace_stats():
    from app.mcp.servers.orbit_server import call_tool

    mc = mk_client(get=make_resp(data={"members": 1200, "activities": 5600}))
    with patch.dict("os.environ", _ORBIT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("orbit_get_workspace_stats", {
            "start_date": "2026-01-01", "end_date": "2026-06-30",
        })
    assert "members" in result
