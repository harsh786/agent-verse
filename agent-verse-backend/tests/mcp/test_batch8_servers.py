"""Tests for Batch 8 MCP servers (files 46-75).

Covers: autopilot, acoustic, appsheet, anvil, beds24, channable,
        chatfuel, clickfunnels, digistore24, egoi, easywebinar, elavon,
        emarsys, emma, esputnik, feedly, fitbit, gleam, gravity_forms,
        gust, koala, logmein, maropost, mendeley, upkeep, yandex,
        wufoo, thrivecart, unbounce, upwork
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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
    for method in ("get", "post", "put", "patch", "delete", "request"):
        setattr(mc, method, AsyncMock(return_value=kwargs.get(method, _default)))
    return mc


# ---------------------------------------------------------------------------
# 46. Autopilot
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_autopilot_add_contact():
    from app.mcp.servers.autopilot_server import call_tool
    mc = mk_client(post=make_resp(200, {"contact_id": "ct1"}))
    with patch.dict("os.environ", {"AUTOPILOT_API_KEY": "ap_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("autopilot_add_contact", {"email": "test@example.com"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_autopilot_add_to_list():
    from app.mcp.servers.autopilot_server import call_tool
    mc = mk_client(post=make_resp(200))
    with patch.dict("os.environ", {"AUTOPILOT_API_KEY": "ap_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("autopilot_add_to_list", {"list_id": "list1", "contact_id": "ct1"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_autopilot_no_key():
    from app.mcp.servers.autopilot_server import call_tool
    with patch.dict("os.environ", {}, clear=True):
        r = await call_tool("autopilot_add_contact", {"email": "test@example.com"})
    assert "error" in r


# ---------------------------------------------------------------------------
# 47. Acoustic
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_acoustic_list_campaigns():
    from app.mcp.servers.acoustic_server import call_tool
    auth_resp = make_resp(200, {"access_token": "aco_tok"})
    xml_resp = make_resp(200)
    xml_resp.text = "<Envelope><Body><RESULT><SUCCESS>TRUE</SUCCESS></RESULT></Body></Envelope>"
    mc = mk_client(post=auth_resp)
    mc.post.side_effect = [auth_resp, xml_resp]
    with patch.dict("os.environ", {
        "ACOUSTIC_CLIENT_ID": "aco_id", "ACOUSTIC_CLIENT_SECRET": "aco_sec",
        "ACOUSTIC_REFRESH_TOKEN": "aco_rt"
    }), patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("acoustic_list_campaigns", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_acoustic_no_credentials():
    from app.mcp.servers.acoustic_server import call_tool
    with patch.dict("os.environ", {}, clear=True):
        r = await call_tool("acoustic_list_campaigns", {})
    assert "error" in r


# ---------------------------------------------------------------------------
# 48. AppSheet
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_appsheet_get_app_data():
    from app.mcp.servers.appsheet_server import call_tool
    mc = mk_client(post=make_resp(200, {"Rows": []}))
    with patch.dict("os.environ", {"APPSHEET_APP_ID": "app1", "APPSHEET_ACCESS_KEY": "ask1"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("appsheet_get_app_data", {"table_name": "Customers"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_appsheet_add_record():
    from app.mcp.servers.appsheet_server import call_tool
    mc = mk_client(post=make_resp(200, {"Rows": [{"_RowNumber": 1}]}))
    with patch.dict("os.environ", {"APPSHEET_APP_ID": "app1", "APPSHEET_ACCESS_KEY": "ask1"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("appsheet_add_record", {"table_name": "Tasks", "rows": [{"Name": "Task 1"}]})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 49. Anvil
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_anvil_list_webhooks():
    from app.mcp.servers.anvil_server import call_tool
    mc = mk_client(post=make_resp(200, {"data": {"webhooks": []}}))
    with patch.dict("os.environ", {"ANVIL_API_KEY": "anv_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("anvil_list_webhooks", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_anvil_no_key():
    from app.mcp.servers.anvil_server import call_tool
    with patch.dict("os.environ", {}, clear=True):
        r = await call_tool("anvil_list_webhooks", {})
    assert "error" in r


# ---------------------------------------------------------------------------
# 50. Beds24
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_beds24_list_properties():
    from app.mcp.servers.beds24_server import call_tool
    mc = mk_client(get=make_resp(200, []))
    with patch.dict("os.environ", {"BEDS24_API_KEY": "b24_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("beds24_list_properties", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_beds24_get_booking():
    from app.mcp.servers.beds24_server import call_tool
    mc = mk_client(get=make_resp(200, {"id": 1001}))
    with patch.dict("os.environ", {"BEDS24_API_KEY": "b24_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("beds24_get_booking", {"booking_id": 1001})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 51. Channable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_channable_list_projects():
    from app.mcp.servers.channable_server import call_tool
    mc = mk_client(get=make_resp(200, {"projects": []}))
    with patch.dict("os.environ", {"CHANNABLE_API_KEY": "ch_key", "CHANNABLE_COMPANY_ID": "co1"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("channable_list_projects", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_channable_list_feeds():
    from app.mcp.servers.channable_server import call_tool
    mc = mk_client(get=make_resp(200, {"feeds": []}))
    with patch.dict("os.environ", {"CHANNABLE_API_KEY": "ch_key", "CHANNABLE_COMPANY_ID": "co1"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("channable_list_feeds", {"project_id": 1})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 52. Chatfuel
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chatfuel_list_users():
    from app.mcp.servers.chatfuel_server import call_tool
    mc = mk_client(get=make_resp(200, {"users": []}))
    with patch.dict("os.environ", {"CHATFUEL_TOKEN": "cf_tok", "CHATFUEL_BOT_ID": "bot1"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("chatfuel_list_users", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_chatfuel_send_message():
    from app.mcp.servers.chatfuel_server import call_tool
    mc = mk_client(post=make_resp(200, {"success": True}))
    with patch.dict("os.environ", {"CHATFUEL_TOKEN": "cf_tok", "CHATFUEL_BOT_ID": "bot1"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("chatfuel_send_message", {"user_id": "u1", "messages": [{"text": "hi"}]})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 53. ClickFunnels
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clickfunnels_list_funnels():
    from app.mcp.servers.clickfunnels_server import call_tool
    mc = mk_client(get=make_resp(200, {"funnels": []}))
    with patch.dict("os.environ", {"CLICKFUNNELS_API_KEY": "cf_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("clickfunnels_list_funnels", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_clickfunnels_list_orders():
    from app.mcp.servers.clickfunnels_server import call_tool
    mc = mk_client(get=make_resp(200, {"orders": []}))
    with patch.dict("os.environ", {"CLICKFUNNELS_API_KEY": "cf_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("clickfunnels_list_orders", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 54. Digistore24
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_digistore24_list_orders():
    from app.mcp.servers.digistore24_server import call_tool
    mc = mk_client(get=make_resp(200, {"orders": []}))
    with patch.dict("os.environ", {"DIGISTORE24_API_KEY": "ds24_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("digistore24_list_orders", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_digistore24_list_products():
    from app.mcp.servers.digistore24_server import call_tool
    mc = mk_client(get=make_resp(200, {"products": []}))
    with patch.dict("os.environ", {"DIGISTORE24_API_KEY": "ds24_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("digistore24_list_products", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 55. E-goi
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_egoi_list_lists():
    from app.mcp.servers.egoi_server import call_tool
    mc = mk_client(get=make_resp(200, {"items": []}))
    with patch.dict("os.environ", {"EGOI_API_KEY": "egoi_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("egoi_list_lists", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_egoi_list_contacts():
    from app.mcp.servers.egoi_server import call_tool
    mc = mk_client(get=make_resp(200, {"items": []}))
    with patch.dict("os.environ", {"EGOI_API_KEY": "egoi_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("egoi_list_contacts", {"list_id": 1})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 56. EasyWebinar
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_easywebinar_list_webinars():
    from app.mcp.servers.easywebinar_server import call_tool
    mc = mk_client(get=make_resp(200, {"webinars": []}))
    with patch.dict("os.environ", {"EASYWEBINAR_API_KEY": "ew_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("easywebinar_list_webinars", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_easywebinar_get_stats():
    from app.mcp.servers.easywebinar_server import call_tool
    mc = mk_client(get=make_resp(200, {"attendees": 100}))
    with patch.dict("os.environ", {"EASYWEBINAR_API_KEY": "ew_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("easywebinar_get_stats", {"webinar_id": "w1"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 57. Elavon
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_elavon_process_payment():
    from app.mcp.servers.elavon_server import call_tool
    mc = mk_client(post=make_resp(200))
    mc.post.return_value.text = "ssl_result=0&ssl_result_message=APPROVAL"
    with patch.dict("os.environ", {"ELAVON_MERCHANT_ID": "merch1", "ELAVON_USER_ID": "user1", "ELAVON_PIN": "pin1"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("elavon_process_payment", {"amount": "10.00", "card_number": "4111111111111111", "exp_date": "1225"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_elavon_batch_close():
    from app.mcp.servers.elavon_server import call_tool
    mc = mk_client(post=make_resp(200))
    mc.post.return_value.text = "ssl_result=0"
    with patch.dict("os.environ", {"ELAVON_MERCHANT_ID": "merch1", "ELAVON_USER_ID": "user1", "ELAVON_PIN": "pin1"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("elavon_batch_close", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 58. Emarsys
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_emarsys_list_campaigns():
    from app.mcp.servers.emarsys_server import call_tool
    mc = mk_client(get=make_resp(200, {"data": []}))
    with patch.dict("os.environ", {"EMARSYS_USERNAME": "ems_user", "EMARSYS_SECRET": "ems_sec"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("emarsys_list_campaigns", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_emarsys_create_contact():
    from app.mcp.servers.emarsys_server import call_tool
    mc = mk_client(post=make_resp(200, {"data": {"id": "123"}}))
    with patch.dict("os.environ", {"EMARSYS_USERNAME": "ems_user", "EMARSYS_SECRET": "ems_sec"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("emarsys_create_contact", {"email": "test@test.com"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 59. Emma
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_emma_list_members():
    from app.mcp.servers.emma_server import call_tool
    mc = mk_client(get=make_resp(200, []))
    with patch.dict("os.environ", {"EMMA_ACCOUNT_ID": "em_acct", "EMMA_PUBLIC_KEY": "em_pub", "EMMA_PRIVATE_KEY": "em_priv"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("emma_list_members", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_emma_list_groups():
    from app.mcp.servers.emma_server import call_tool
    mc = mk_client(get=make_resp(200, []))
    with patch.dict("os.environ", {"EMMA_ACCOUNT_ID": "em_acct", "EMMA_PUBLIC_KEY": "em_pub", "EMMA_PRIVATE_KEY": "em_priv"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("emma_list_groups", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 60. eSputnik
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_esputnik_get_contact():
    from app.mcp.servers.esputnik_server import call_tool
    mc = mk_client(get=make_resp(200, {"id": "ct1"}))
    with patch.dict("os.environ", {"ESPUTNIK_LOGIN": "esp_login", "ESPUTNIK_PASSWORD": "esp_pass"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("esputnik_get_contact", {"email": "test@test.com"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_esputnik_list_segments():
    from app.mcp.servers.esputnik_server import call_tool
    mc = mk_client(get=make_resp(200, {"segments": []}))
    with patch.dict("os.environ", {"ESPUTNIK_LOGIN": "esp_login", "ESPUTNIK_PASSWORD": "esp_pass"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("esputnik_list_segments", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 61. Feedly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_feedly_list_streams():
    from app.mcp.servers.feedly_server import call_tool
    mc = mk_client(get=make_resp(200, []))
    with patch.dict("os.environ", {"FEEDLY_ACCESS_TOKEN": "fl_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("feedly_list_streams", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_feedly_search_feeds():
    from app.mcp.servers.feedly_server import call_tool
    mc = mk_client(get=make_resp(200, {"results": []}))
    with patch.dict("os.environ", {"FEEDLY_ACCESS_TOKEN": "fl_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("feedly_search_feeds", {"query": "python news"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 62. Fitbit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fitbit_get_activity_summary():
    from app.mcp.servers.fitbit_server import call_tool
    mc = mk_client(get=make_resp(200, {"summary": {"steps": 10000}}))
    with patch.dict("os.environ", {"FITBIT_ACCESS_TOKEN": "fb_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("fitbit_get_activity_summary", {"date": "today"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_fitbit_list_devices():
    from app.mcp.servers.fitbit_server import call_tool
    mc = mk_client(get=make_resp(200, []))
    with patch.dict("os.environ", {"FITBIT_ACCESS_TOKEN": "fb_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("fitbit_list_devices", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 63. Gleam
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gleam_list_campaigns():
    from app.mcp.servers.gleam_server import call_tool
    mc = mk_client(get=make_resp(200, {"campaigns": []}))
    with patch.dict("os.environ", {"GLEAM_API_KEY": "gl_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("gleam_list_campaigns", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_gleam_get_stats():
    from app.mcp.servers.gleam_server import call_tool
    mc = mk_client(get=make_resp(200, {"entries": 100}))
    with patch.dict("os.environ", {"GLEAM_API_KEY": "gl_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("gleam_get_stats", {"campaign_id": "camp1"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 64. Gravity Forms
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gravity_forms_list_forms():
    from app.mcp.servers.gravity_forms_server import call_tool
    mc = mk_client(get=make_resp(200, []))
    with patch.dict("os.environ", {
        "GRAVITY_FORMS_CONSUMER_KEY": "gf_ckey",
        "GRAVITY_FORMS_CONSUMER_SECRET": "gf_csec",
        "GRAVITY_FORMS_SITE_URL": "https://mysite.com"
    }), patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("gravity_forms_list_forms", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_gravity_forms_get_entries():
    from app.mcp.servers.gravity_forms_server import call_tool
    mc = mk_client(get=make_resp(200, {"entries": []}))
    with patch.dict("os.environ", {
        "GRAVITY_FORMS_CONSUMER_KEY": "gf_ckey",
        "GRAVITY_FORMS_CONSUMER_SECRET": "gf_csec",
        "GRAVITY_FORMS_SITE_URL": "https://mysite.com"
    }), patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("gravity_forms_get_entries", {"form_id": 1})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 65. Gust
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gust_list_startups():
    from app.mcp.servers.gust_server import call_tool
    mc = mk_client(get=make_resp(200, {"companies": []}))
    with patch.dict("os.environ", {"GUST_ACCESS_TOKEN": "gust_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("gust_list_startups", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_gust_get_profile():
    from app.mcp.servers.gust_server import call_tool
    mc = mk_client(get=make_resp(200, {"profile": {}}))
    with patch.dict("os.environ", {"GUST_ACCESS_TOKEN": "gust_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("gust_get_profile", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 66. Koala
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_koala_list_visitors():
    from app.mcp.servers.koala_server import call_tool
    mc = mk_client(get=make_resp(200, {"visitors": []}))
    with patch.dict("os.environ", {"KOALA_API_KEY": "koala_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("koala_list_visitors", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_koala_get_firmographics():
    from app.mcp.servers.koala_server import call_tool
    mc = mk_client(get=make_resp(200, {"industry": "SaaS"}))
    with patch.dict("os.environ", {"KOALA_API_KEY": "koala_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("koala_get_firmographics", {"domain": "example.com"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 67. LogMeIn
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_logmein_list_sessions():
    from app.mcp.servers.logmein_server import call_tool
    mc = mk_client(get=make_resp(200, {"sessions": []}))
    with patch.dict("os.environ", {"LOGMEIN_API_KEY": "lmi_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("logmein_list_sessions", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_logmein_list_computers():
    from app.mcp.servers.logmein_server import call_tool
    mc = mk_client(get=make_resp(200, {"computers": []}))
    with patch.dict("os.environ", {"LOGMEIN_API_KEY": "lmi_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("logmein_list_computers", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 68. Maropost
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_maropost_list_contacts():
    from app.mcp.servers.maropost_server import call_tool
    mc = mk_client(get=make_resp(200, []))
    with patch.dict("os.environ", {"MAROPOST_ACCOUNT_ID": "maro_acct", "MAROPOST_API_KEY": "maro_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("maropost_list_contacts", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_maropost_list_lists():
    from app.mcp.servers.maropost_server import call_tool
    mc = mk_client(get=make_resp(200, []))
    with patch.dict("os.environ", {"MAROPOST_ACCOUNT_ID": "maro_acct", "MAROPOST_API_KEY": "maro_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("maropost_list_lists", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 69. Mendeley
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mendeley_list_documents():
    from app.mcp.servers.mendeley_server import call_tool
    mc = mk_client(get=make_resp(200, []))
    with patch.dict("os.environ", {"MENDELEY_ACCESS_TOKEN": "men_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("mendeley_list_documents", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_mendeley_search_catalog():
    from app.mcp.servers.mendeley_server import call_tool
    mc = mk_client(get=make_resp(200, {"results": []}))
    with patch.dict("os.environ", {"MENDELEY_ACCESS_TOKEN": "men_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("mendeley_search_catalog", {"query": "machine learning"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 70. UpKeep
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upkeep_list_work_orders():
    from app.mcp.servers.upkeep_server import call_tool
    mc = mk_client(get=make_resp(200, []))
    with patch.dict("os.environ", {"UPKEEP_API_KEY": "uk_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("upkeep_list_work_orders", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_upkeep_list_assets():
    from app.mcp.servers.upkeep_server import call_tool
    mc = mk_client(get=make_resp(200, []))
    with patch.dict("os.environ", {"UPKEEP_API_KEY": "uk_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("upkeep_list_assets", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 71. Yandex
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_yandex_translate():
    from app.mcp.servers.yandex_server import call_tool
    mc = mk_client(post=make_resp(200, {"translations": [{"text": "Hello"}]}))
    with patch.dict("os.environ", {"YANDEX_API_KEY": "ya_key", "YANDEX_OAUTH_TOKEN": "ya_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("yandex_translate", {"texts": ["Привет"], "target_language_code": "en"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_yandex_geocode():
    from app.mcp.servers.yandex_server import call_tool
    mc = mk_client(get=make_resp(200, {"response": {"GeoObjectCollection": {}}}))
    with patch.dict("os.environ", {"YANDEX_API_KEY": "ya_key", "YANDEX_OAUTH_TOKEN": "ya_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("yandex_geocode", {"geocode": "Moscow, Russia"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 72. Wufoo
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wufoo_list_forms():
    from app.mcp.servers.wufoo_server import call_tool
    mc = mk_client(get=make_resp(200, {"Forms": []}))
    with patch.dict("os.environ", {"WUFOO_API_KEY": "wf_key", "WUFOO_SUBDOMAIN": "myco"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("wufoo_list_forms", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_wufoo_list_reports():
    from app.mcp.servers.wufoo_server import call_tool
    mc = mk_client(get=make_resp(200, {"Reports": []}))
    with patch.dict("os.environ", {"WUFOO_API_KEY": "wf_key", "WUFOO_SUBDOMAIN": "myco"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("wufoo_list_reports", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 73. ThriveCart
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_thrivecart_list_products():
    from app.mcp.servers.thrivecart_server import call_tool
    mc = mk_client(get=make_resp(200, {"products": []}))
    with patch.dict("os.environ", {"THRIVECART_API_KEY": "tc_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("thrivecart_list_products", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_thrivecart_get_revenue_stats():
    from app.mcp.servers.thrivecart_server import call_tool
    mc = mk_client(get=make_resp(200, {"revenue": 5000}))
    with patch.dict("os.environ", {"THRIVECART_API_KEY": "tc_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("thrivecart_get_revenue_stats", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 74. Unbounce
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unbounce_list_pages():
    from app.mcp.servers.unbounce_server import call_tool
    mc = mk_client(get=make_resp(200, {"pages": []}))
    with patch.dict("os.environ", {"UNBOUNCE_API_KEY": "ub_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("unbounce_list_pages", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_unbounce_list_leads():
    from app.mcp.servers.unbounce_server import call_tool
    mc = mk_client(get=make_resp(200, {"leads": []}))
    with patch.dict("os.environ", {"UNBOUNCE_API_KEY": "ub_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("unbounce_list_leads", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 75. Upwork
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upwork_search_jobs():
    from app.mcp.servers.upwork_server import call_tool
    mc = mk_client(get=make_resp(200, {"jobs": {"job": []}}))
    with patch.dict("os.environ", {"UPWORK_ACCESS_TOKEN": "uw_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("upwork_search_jobs", {"q": "python developer"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_upwork_get_profile():
    from app.mcp.servers.upwork_server import call_tool
    mc = mk_client(get=make_resp(200, {"profile": {}}))
    with patch.dict("os.environ", {"UPWORK_ACCESS_TOKEN": "uw_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("upwork_get_profile", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_upwork_no_token():
    from app.mcp.servers.upwork_server import call_tool
    with patch.dict("os.environ", {}, clear=True):
        r = await call_tool("upwork_search_jobs", {"q": "python"})
    assert "error" in r
