"""Tests for Batch 7 MCP servers (files 21-45).

Covers: epic_fhir, athenahealth, drchrono, clio, harvey, plaid, alpaca,
        brex, ramp, zillow, buildium, canvas_lms, moodle, home_assistant,
        aws_iot, steam, amadeus, doordash, alchemy, sportradar, flexport,
        sap, moralis, toast_pos, ap_news
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
# 21. Epic FHIR
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_epic_get_patient():
    from app.mcp.servers.epic_fhir_server import call_tool
    mc = mk_client(get=make_resp(200, {"resourceType": "Patient", "id": "pt1"}))
    with patch.dict("os.environ", {"EPIC_ACCESS_TOKEN": "epic_tok", "EPIC_BASE_URL": "https://fhir.epic.com"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("epic_get_patient", {"patient_id": "pt1"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_epic_list_conditions():
    from app.mcp.servers.epic_fhir_server import call_tool
    mc = mk_client(get=make_resp(200, {"entry": []}))
    with patch.dict("os.environ", {"EPIC_ACCESS_TOKEN": "epic_tok", "EPIC_BASE_URL": "https://fhir.epic.com"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("epic_list_conditions", {"patient_id": "pt1"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_epic_no_token():
    from app.mcp.servers.epic_fhir_server import call_tool
    with patch.dict("os.environ", {}, clear=True):
        r = await call_tool("epic_get_patient", {"patient_id": "pt1"})
    assert "error" in r


# ---------------------------------------------------------------------------
# 22. Athenahealth
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_athena_list_patients():
    from app.mcp.servers.athenahealth_server import call_tool
    mc = mk_client(get=make_resp(200, {"patients": []}))
    with patch.dict("os.environ", {"ATHENA_ACCESS_TOKEN": "at_tok", "ATHENA_PRACTICE_ID": "p1"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("athena_list_patients", {"lastname": "Smith"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_athena_list_providers():
    from app.mcp.servers.athenahealth_server import call_tool
    mc = mk_client(get=make_resp(200, {"providers": []}))
    with patch.dict("os.environ", {"ATHENA_ACCESS_TOKEN": "at_tok", "ATHENA_PRACTICE_ID": "p1"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("athena_list_providers", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 23. DrChrono
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_drchrono_list_patients():
    from app.mcp.servers.drchrono_server import call_tool
    mc = mk_client(get=make_resp(200, {"results": []}))
    with patch.dict("os.environ", {"DRCHRONO_ACCESS_TOKEN": "dc_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("drchrono_list_patients", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_drchrono_list_appointments():
    from app.mcp.servers.drchrono_server import call_tool
    mc = mk_client(get=make_resp(200, {"results": []}))
    with patch.dict("os.environ", {"DRCHRONO_ACCESS_TOKEN": "dc_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("drchrono_list_appointments", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 24. Clio
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clio_list_matters():
    from app.mcp.servers.clio_server import call_tool
    mc = mk_client(get=make_resp(200, {"data": []}))
    with patch.dict("os.environ", {"CLIO_ACCESS_TOKEN": "clio_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("clio_list_matters", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_clio_list_contacts():
    from app.mcp.servers.clio_server import call_tool
    mc = mk_client(get=make_resp(200, {"data": []}))
    with patch.dict("os.environ", {"CLIO_ACCESS_TOKEN": "clio_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("clio_list_contacts", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 25. Harvey AI
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_harvey_legal_research():
    from app.mcp.servers.harvey_server import call_tool
    mc = mk_client(post=make_resp(200, {"result": "Legal analysis"}))
    with patch.dict("os.environ", {"HARVEY_API_KEY": "hv_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("harvey_legal_research", {"query": "contract law"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_harvey_check_compliance():
    from app.mcp.servers.harvey_server import call_tool
    mc = mk_client(post=make_resp(200, {"compliant": True}))
    with patch.dict("os.environ", {"HARVEY_API_KEY": "hv_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("harvey_check_compliance", {"document_text": "terms...", "regulations": ["GDPR"]})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 26. Plaid
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plaid_get_accounts():
    from app.mcp.servers.plaid_server import call_tool
    mc = mk_client(post=make_resp(200, {"accounts": []}))
    with patch.dict("os.environ", {"PLAID_CLIENT_ID": "plaid_id", "PLAID_SECRET": "plaid_sec", "PLAID_ACCESS_TOKEN": "plaid_at"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("plaid_get_accounts", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_plaid_create_link_token():
    from app.mcp.servers.plaid_server import call_tool
    mc = mk_client(post=make_resp(200, {"link_token": "link-tok"}))
    with patch.dict("os.environ", {"PLAID_CLIENT_ID": "plaid_id", "PLAID_SECRET": "plaid_sec", "PLAID_ACCESS_TOKEN": "plaid_at"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("plaid_create_link_token", {"user_id": "user1", "client_name": "MyApp"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 27. Alpaca
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alpaca_get_account():
    from app.mcp.servers.alpaca_server import call_tool
    mc = mk_client(get=make_resp(200, {"id": "acct1"}))
    with patch.dict("os.environ", {"ALPACA_API_KEY": "alp_key", "ALPACA_SECRET_KEY": "alp_sec"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("alpaca_get_account", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_alpaca_list_assets():
    from app.mcp.servers.alpaca_server import call_tool
    mc = mk_client(get=make_resp(200, []))
    with patch.dict("os.environ", {"ALPACA_API_KEY": "alp_key", "ALPACA_SECRET_KEY": "alp_sec"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("alpaca_list_assets", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 28. Brex
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_brex_list_accounts():
    from app.mcp.servers.brex_server import call_tool
    mc = mk_client(get=make_resp(200, {"items": []}))
    with patch.dict("os.environ", {"BREX_TOKEN": "brex_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("brex_list_accounts", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_brex_list_cards():
    from app.mcp.servers.brex_server import call_tool
    mc = mk_client(get=make_resp(200, {"items": []}))
    with patch.dict("os.environ", {"BREX_TOKEN": "brex_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("brex_list_cards", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 29. Ramp
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ramp_list_transactions():
    from app.mcp.servers.ramp_server import call_tool
    mc = mk_client(get=make_resp(200, {"data": []}))
    with patch.dict("os.environ", {"RAMP_ACCESS_TOKEN": "ramp_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("ramp_list_transactions", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_ramp_list_departments():
    from app.mcp.servers.ramp_server import call_tool
    mc = mk_client(get=make_resp(200, {"data": []}))
    with patch.dict("os.environ", {"RAMP_ACCESS_TOKEN": "ramp_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("ramp_list_departments", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 30. Zillow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_zillow_search_properties():
    from app.mcp.servers.zillow_server import call_tool
    mc = mk_client(get=make_resp(200, {"bundle": []}))
    with patch.dict("os.environ", {"ZILLOW_API_KEY": "zil_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("zillow_search_properties", {"city": "Seattle", "state": "WA"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_zillow_get_rent_estimates():
    from app.mcp.servers.zillow_server import call_tool
    mc = mk_client(get=make_resp(200, {"bundle": []}))
    with patch.dict("os.environ", {"ZILLOW_API_KEY": "zil_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("zillow_get_rent_estimates", {"zipcode": "98101"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 31. Buildium
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_buildium_list_properties():
    from app.mcp.servers.buildium_server import call_tool
    mc = mk_client(get=make_resp(200, []))
    with patch.dict("os.environ", {"BUILDIUM_CLIENT_ID": "bd_id", "BUILDIUM_CLIENT_SECRET": "bd_sec"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("buildium_list_properties", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_buildium_list_leases():
    from app.mcp.servers.buildium_server import call_tool
    mc = mk_client(get=make_resp(200, []))
    with patch.dict("os.environ", {"BUILDIUM_CLIENT_ID": "bd_id", "BUILDIUM_CLIENT_SECRET": "bd_sec"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("buildium_list_leases", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 32. Canvas LMS
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_canvas_list_courses():
    from app.mcp.servers.canvas_lms_server import call_tool
    mc = mk_client(get=make_resp(200, []))
    with patch.dict("os.environ", {"CANVAS_ACCESS_TOKEN": "cv_tok", "CANVAS_DOMAIN": "myschool.instructure.com"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("canvas_list_courses", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_canvas_list_students():
    from app.mcp.servers.canvas_lms_server import call_tool
    mc = mk_client(get=make_resp(200, []))
    with patch.dict("os.environ", {"CANVAS_ACCESS_TOKEN": "cv_tok", "CANVAS_DOMAIN": "myschool.instructure.com"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("canvas_list_students", {"course_id": "c1"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 33. Moodle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_moodle_list_courses():
    from app.mcp.servers.moodle_server import call_tool
    mc = mk_client(get=make_resp(200, []))
    with patch.dict("os.environ", {"MOODLE_TOKEN": "md_tok", "MOODLE_URL": "https://moodle.example.com"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("moodle_list_courses", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_moodle_list_users():
    from app.mcp.servers.moodle_server import call_tool
    mc = mk_client(get=make_resp(200, {"users": []}))
    with patch.dict("os.environ", {"MOODLE_TOKEN": "md_tok", "MOODLE_URL": "https://moodle.example.com"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("moodle_list_users", {"field": "email", "value": "test@test.com"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 34. Home Assistant
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_homeassistant_list_entities():
    from app.mcp.servers.home_assistant_server import call_tool
    mc = mk_client(get=make_resp(200, [{"entity_id": "light.living_room", "state": "on"}]))
    with patch.dict("os.environ", {"HOME_ASSISTANT_TOKEN": "ha_tok", "HOME_ASSISTANT_URL": "http://homeassistant.local:8123"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("homeassistant_list_entities", {})
    assert isinstance(r, list)


@pytest.mark.asyncio
async def test_homeassistant_call_service():
    from app.mcp.servers.home_assistant_server import call_tool
    mc = mk_client(post=make_resp(200, []))
    with patch.dict("os.environ", {"HOME_ASSISTANT_TOKEN": "ha_tok", "HOME_ASSISTANT_URL": "http://homeassistant.local:8123"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("homeassistant_call_service", {"domain": "light", "service": "turn_on", "entity_id": "light.living_room"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 35. AWS IoT
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aws_iot_list_things():
    from app.mcp.servers.aws_iot_server import call_tool
    mock_iot = MagicMock()
    mock_iot.list_things.return_value = {"things": []}
    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_iot
    with patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": "ak", "AWS_SECRET_ACCESS_KEY": "sk", "AWS_REGION": "us-east-1"}), \
         patch.dict("sys.modules", {"boto3": mock_boto3}):
        r = await call_tool("aws_iot_list_things", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_aws_iot_no_keys():
    from app.mcp.servers.aws_iot_server import call_tool
    with patch.dict("os.environ", {}, clear=True):
        r = await call_tool("aws_iot_list_things", {})
    assert "error" in r


# ---------------------------------------------------------------------------
# 36. Steam
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_steam_get_player_summary():
    from app.mcp.servers.steam_server import call_tool
    mc = mk_client(get=make_resp(200, {"response": {"players": []}}))
    with patch.dict("os.environ", {"STEAM_API_KEY": "steam_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("steam_get_player_summary", {"steam_ids": ["76561198000000000"]})
    assert "error" not in r


@pytest.mark.asyncio
async def test_steam_get_game_news():
    from app.mcp.servers.steam_server import call_tool
    mc = mk_client(get=make_resp(200, {"appnews": {"newsitems": []}}))
    with patch.dict("os.environ", {"STEAM_API_KEY": "steam_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("steam_get_game_news", {"app_id": 570})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 37. Amadeus
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_amadeus_search_flights():
    from app.mcp.servers.amadeus_server import call_tool
    auth_resp = make_resp(200, {"access_token": "ama_tok"})
    search_resp = make_resp(200, {"data": []})
    mc = mk_client(post=auth_resp, get=search_resp)
    mc.post.return_value = auth_resp
    mc.get.return_value = search_resp
    with patch.dict("os.environ", {"AMADEUS_CLIENT_ID": "ama_id", "AMADEUS_CLIENT_SECRET": "ama_sec"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("amadeus_search_flights", {
            "origin_location_code": "JFK",
            "destination_location_code": "LAX",
            "departure_date": "2025-06-01"
        })
    assert "error" not in r


# ---------------------------------------------------------------------------
# 38. DoorDash
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_doordash_get_delivery():
    from app.mcp.servers.doordash_server import call_tool
    mc = mk_client(get=make_resp(200, {"external_delivery_id": "del1", "status": "picked_up"}))
    with patch.dict("os.environ", {"DOORDASH_DEVELOPER_ID": "dev1", "DOORDASH_KEY_ID": "key1", "DOORDASH_SIGNING_SECRET": "sec1"}), \
         patch("httpx.AsyncClient", return_value=mc):
        with patch("app.mcp.servers.doordash_server._make_jwt", return_value="test_jwt"):
            r = await call_tool("doordash_get_delivery", {"external_delivery_id": "del1"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_doordash_no_credentials():
    from app.mcp.servers.doordash_server import call_tool
    with patch.dict("os.environ", {}, clear=True):
        r = await call_tool("doordash_create_delivery", {"external_delivery_id": "del1", "pickup_address": "A", "dropoff_address": "B"})
    assert "error" in r


# ---------------------------------------------------------------------------
# 39. Alchemy
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alchemy_get_balance():
    from app.mcp.servers.alchemy_server import call_tool
    mc = mk_client(post=make_resp(200, {"result": "0x29a2241af62c0000"}))
    with patch.dict("os.environ", {"ALCHEMY_API_KEY": "alc_key", "ALCHEMY_NETWORK": "eth-mainnet"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("alchemy_get_balance", {"address": "0x123"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_alchemy_get_gas_price():
    from app.mcp.servers.alchemy_server import call_tool
    mc = mk_client(post=make_resp(200, {"result": "0x3B9ACA00"}))
    with patch.dict("os.environ", {"ALCHEMY_API_KEY": "alc_key", "ALCHEMY_NETWORK": "eth-mainnet"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("alchemy_get_gas_price", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 40. Sportradar
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sportradar_get_standings():
    from app.mcp.servers.sportradar_server import call_tool
    mc = mk_client(get=make_resp(200, {"standing": []}))
    with patch.dict("os.environ", {"SPORTRADAR_API_KEY": "sr_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("sportradar_get_standings", {"sport": "nfl", "season_year": 2024})
    assert "error" not in r


@pytest.mark.asyncio
async def test_sportradar_search_teams():
    from app.mcp.servers.sportradar_server import call_tool
    mc = mk_client(get=make_resp(200, {"teams": []}))
    with patch.dict("os.environ", {"SPORTRADAR_API_KEY": "sr_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("sportradar_search_teams", {"name": "Patriots"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 41. Flexport
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_flexport_list_shipments():
    from app.mcp.servers.flexport_server import call_tool
    mc = mk_client(get=make_resp(200, {"data": []}))
    with patch.dict("os.environ", {"FLEXPORT_API_KEY": "fp_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("flexport_list_shipments", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_flexport_track_shipment():
    from app.mcp.servers.flexport_server import call_tool
    mc = mk_client(get=make_resp(200, {"data": []}))
    with patch.dict("os.environ", {"FLEXPORT_API_KEY": "fp_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("flexport_track_shipment", {"shipment_id": 123})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 42. SAP
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sap_list_purchase_orders():
    from app.mcp.servers.sap_server import call_tool
    auth_resp = make_resp(200, {"access_token": "sap_tok"})
    data_resp = make_resp(200, {"d": {"results": []}})
    mc = mk_client(post=auth_resp, get=data_resp)
    mc.post.return_value = auth_resp
    mc.get.return_value = data_resp
    with patch.dict("os.environ", {"SAP_CLIENT_ID": "sap_id", "SAP_CLIENT_SECRET": "sap_sec", "SAP_BASE_URL": "https://sap.example.com"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("sap_list_purchase_orders", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 43. Moralis
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_moralis_get_native_balance():
    from app.mcp.servers.moralis_server import call_tool
    mc = mk_client(get=make_resp(200, {"balance": "1000000000000000000"}))
    with patch.dict("os.environ", {"MORALIS_API_KEY": "mor_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("moralis_get_native_balance", {"address": "0x123"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_moralis_search_nfts():
    from app.mcp.servers.moralis_server import call_tool
    mc = mk_client(get=make_resp(200, {"result": []}))
    with patch.dict("os.environ", {"MORALIS_API_KEY": "mor_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("moralis_search_nfts", {"query": "Bored Ape"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 44. Toast POS
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_toast_list_orders():
    from app.mcp.servers.toast_pos_server import call_tool
    auth_resp = make_resp(200, {"token": {"accessToken": "toast_tok"}})
    orders_resp = make_resp(200, [])
    mc = mk_client(post=auth_resp, get=orders_resp)
    mc.post.return_value = auth_resp
    mc.get.return_value = orders_resp
    with patch.dict("os.environ", {"TOAST_CLIENT_ID": "toast_id", "TOAST_CLIENT_SECRET": "toast_sec"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("toast_list_orders", {"restaurant_guid": "rest1"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 45. AP News
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ap_news_search_articles():
    from app.mcp.servers.ap_news_server import call_tool
    mc = mk_client(get=make_resp(200, {"items": []}))
    with patch.dict("os.environ", {"AP_NEWS_API_KEY": "ap_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("ap_news_search_articles", {"query": "technology"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_ap_news_get_breaking():
    from app.mcp.servers.ap_news_server import call_tool
    mc = mk_client(get=make_resp(200, {"items": []}))
    with patch.dict("os.environ", {"AP_NEWS_API_KEY": "ap_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("ap_news_get_breaking_news", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_ap_news_no_key():
    from app.mcp.servers.ap_news_server import call_tool
    with patch.dict("os.environ", {}, clear=True):
        r = await call_tool("ap_news_search_articles", {"query": "news"})
    assert "error" in r
