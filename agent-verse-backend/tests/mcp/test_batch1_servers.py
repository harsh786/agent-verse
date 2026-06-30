"""Unit tests for batch-1 MCP servers (20 new integrations).

Exercises every call_tool() branch by mocking httpx.AsyncClient.
Tests at least 2 tools per server plus missing-env guard for each.

Servers covered:
  activecampaign, aweber, campaign_monitor, constant_contact, drip,
  getresponse, mailgun, moosend, omnisend, loops,
  manychat, onesignal, pushover, pushbullet, postmark,
  ringcentral, plivo, vonage, twitch, gmail
"""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_resp(status: int = 200, data: Any = None) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data if data is not None else {}
    m.text = str(data or "")
    m.content = b"ok"
    m.raise_for_status = MagicMock()
    return m


def mk_client(**kwargs: MagicMock) -> AsyncMock:
    """Return a mock AsyncClient context manager with all HTTP methods set."""
    mc = AsyncMock()
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=False)
    _default = make_resp()
    for method in ("get", "post", "put", "patch", "delete"):
        setattr(mc, method, AsyncMock(return_value=kwargs.get(method, _default)))
    return mc


# ===========================================================================
# 1 — ActiveCampaign
# ===========================================================================

_AC = {
    "ACTIVECAMPAIGN_API_KEY": "ac-key",
    "ACTIVECAMPAIGN_BASE_URL": "https://myaccount.api-us1.com",
}


@pytest.mark.asyncio
async def test_activecampaign_list_contacts():
    from app.mcp.servers.activecampaign_server import call_tool

    mc = mk_client(get=make_resp(data={"contacts": [{"id": "1", "email": "a@b.com", "firstName": "Alice", "lastName": "Smith"}], "meta": {}}))
    with patch.dict("os.environ", _AC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("activecampaign_list_contacts", {"limit": 10})
    assert "contacts" in result
    assert result["contacts"][0]["email"] == "a@b.com"


@pytest.mark.asyncio
async def test_activecampaign_create_contact():
    from app.mcp.servers.activecampaign_server import call_tool

    mc = mk_client(post=make_resp(data={"contact": {"id": "2", "email": "new@b.com"}}))
    with patch.dict("os.environ", _AC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("activecampaign_create_contact", {"email": "new@b.com", "first_name": "Bob"})
    assert result["email"] == "new@b.com"


@pytest.mark.asyncio
async def test_activecampaign_update_contact():
    from app.mcp.servers.activecampaign_server import call_tool

    mc = mk_client(put=make_resp(data={"contact": {"id": "1", "email": "a@b.com"}}))
    with patch.dict("os.environ", _AC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("activecampaign_update_contact", {"contact_id": "1", "first_name": "Alice"})
    assert "id" in result


@pytest.mark.asyncio
async def test_activecampaign_add_to_list():
    from app.mcp.servers.activecampaign_server import call_tool

    mc = mk_client(post=make_resp(data={"contactList": {"id": "1"}}))
    with patch.dict("os.environ", _AC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("activecampaign_add_to_list", {"contact_id": "1", "list_id": "5"})
    assert "contactList" in result


@pytest.mark.asyncio
async def test_activecampaign_get_campaign_stats():
    from app.mcp.servers.activecampaign_server import call_tool

    mc = mk_client(get=make_resp(data={"opens": 100, "clicks": 50}))
    with patch.dict("os.environ", _AC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("activecampaign_get_campaign_stats", {"campaign_id": "99"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_activecampaign_unknown_tool():
    from app.mcp.servers.activecampaign_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _AC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("activecampaign_bogus", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_activecampaign_missing_api_key():
    from app.mcp.servers.activecampaign_server import call_tool

    env = {"ACTIVECAMPAIGN_BASE_URL": "https://x.api-us1.com", "ACTIVECAMPAIGN_API_KEY": ""}
    with patch.dict("os.environ", env):
        os.environ.pop("ACTIVECAMPAIGN_API_KEY", None)
        result = await call_tool("activecampaign_list_contacts", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_activecampaign_missing_base_url():
    from app.mcp.servers.activecampaign_server import call_tool

    env = {"ACTIVECAMPAIGN_API_KEY": "key", "ACTIVECAMPAIGN_BASE_URL": ""}
    with patch.dict("os.environ", env):
        os.environ.pop("ACTIVECAMPAIGN_BASE_URL", None)
        result = await call_tool("activecampaign_list_contacts", {})
    assert "error" in result


# ===========================================================================
# 2 — AWeber
# ===========================================================================

_AWB = {"AWEBER_ACCESS_TOKEN": "aweber-tok"}


@pytest.mark.asyncio
async def test_aweber_get_lists():
    from app.mcp.servers.aweber_server import call_tool

    mc = mk_client(get=make_resp(data={"entries": [{"id": "1", "name": "Subscribers", "total_subscribers": 500}], "total_size": 1}))
    with patch.dict("os.environ", _AWB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("aweber_get_lists", {"account_id": "acc1"})
    assert "lists" in result
    assert result["lists"][0]["name"] == "Subscribers"


@pytest.mark.asyncio
async def test_aweber_add_subscriber():
    from app.mcp.servers.aweber_server import call_tool

    mc = mk_client(post=make_resp(status=201))
    with patch.dict("os.environ", _AWB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("aweber_add_subscriber", {"account_id": "acc1", "list_id": "list1", "email": "x@y.com"})
    assert result.get("success") is True


@pytest.mark.asyncio
async def test_aweber_list_subscribers():
    from app.mcp.servers.aweber_server import call_tool

    mc = mk_client(get=make_resp(data={"entries": [{"id": "s1", "email": "a@b.com", "status": "subscribed"}], "total_size": 1}))
    with patch.dict("os.environ", _AWB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("aweber_list_subscribers", {"account_id": "acc1", "list_id": "list1"})
    assert "subscribers" in result


@pytest.mark.asyncio
async def test_aweber_get_account_stats():
    from app.mcp.servers.aweber_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "acc1", "email": "owner@example.com"}))
    with patch.dict("os.environ", _AWB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("aweber_get_account_stats", {"account_id": "acc1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_aweber_missing_env():
    from app.mcp.servers.aweber_server import call_tool

    with patch.dict("os.environ", {"AWEBER_ACCESS_TOKEN": ""}):
        os.environ.pop("AWEBER_ACCESS_TOKEN", None)
        result = await call_tool("aweber_get_lists", {"account_id": "acc1"})
    assert "error" in result


# ===========================================================================
# 3 — Campaign Monitor
# ===========================================================================

_CM = {"CAMPAIGN_MONITOR_API_KEY": "cm-key"}


@pytest.mark.asyncio
async def test_campaign_monitor_get_lists():
    from app.mcp.servers.campaign_monitor_server import call_tool

    mc = mk_client(get=make_resp(data=[{"ListID": "l1", "Name": "Newsletter"}]))
    with patch.dict("os.environ", _CM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("campaign_monitor_get_lists", {"client_id": "c1"})
    assert "lists" in result


@pytest.mark.asyncio
async def test_campaign_monitor_list_subscribers():
    from app.mcp.servers.campaign_monitor_server import call_tool

    mc = mk_client(get=make_resp(data={"Results": [{"EmailAddress": "a@b.com", "Name": "Alice", "Date": "2024-01-01"}], "TotalNumberOfRecords": 1}))
    with patch.dict("os.environ", _CM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("campaign_monitor_list_subscribers", {"list_id": "l1"})
    assert result["subscribers"][0]["email"] == "a@b.com"


@pytest.mark.asyncio
async def test_campaign_monitor_add_subscriber():
    from app.mcp.servers.campaign_monitor_server import call_tool

    mc = mk_client(post=make_resp(data="a@b.com"))
    with patch.dict("os.environ", _CM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("campaign_monitor_add_subscriber", {"list_id": "l1", "email": "a@b.com", "name": "Alice"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_campaign_monitor_get_campaign_summary():
    from app.mcp.servers.campaign_monitor_server import call_tool

    mc = mk_client(get=make_resp(data={"Recipients": 500, "Opened": {"UniqueOpened": 100}}))
    with patch.dict("os.environ", _CM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("campaign_monitor_get_campaign_summary", {"campaign_id": "camp1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_campaign_monitor_missing_env():
    from app.mcp.servers.campaign_monitor_server import call_tool

    with patch.dict("os.environ", {"CAMPAIGN_MONITOR_API_KEY": ""}):
        os.environ.pop("CAMPAIGN_MONITOR_API_KEY", None)
        result = await call_tool("campaign_monitor_get_lists", {"client_id": "c1"})
    assert "error" in result


# ===========================================================================
# 4 — Constant Contact
# ===========================================================================

_CC = {"CONSTANT_CONTACT_API_KEY": "cc-key"}


@pytest.mark.asyncio
async def test_constant_contact_list_contacts():
    from app.mcp.servers.constant_contact_server import call_tool

    mc = mk_client(get=make_resp(data={"contacts": [{"contact_id": "c1", "email_address": {"address": "a@b.com"}, "first_name": "Alice", "last_name": "Smith"}]}))
    with patch.dict("os.environ", _CC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("constant_contact_list_contacts", {"limit": 10})
    assert result["contacts"][0]["email"] == "a@b.com"


@pytest.mark.asyncio
async def test_constant_contact_create_contact():
    from app.mcp.servers.constant_contact_server import call_tool

    mc = mk_client(post=make_resp(data={"contact_id": "c2", "email_address": {"address": "new@b.com"}}))
    with patch.dict("os.environ", _CC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("constant_contact_create_contact", {"email": "new@b.com"})
    assert result["contact_id"] == "c2"


@pytest.mark.asyncio
async def test_constant_contact_list_contact_lists():
    from app.mcp.servers.constant_contact_server import call_tool

    mc = mk_client(get=make_resp(data={"lists": [{"list_id": "l1", "name": "Prospects", "membership_count": 200}]}))
    with patch.dict("os.environ", _CC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("constant_contact_list_contact_lists", {})
    assert result["lists"][0]["name"] == "Prospects"


@pytest.mark.asyncio
async def test_constant_contact_missing_env():
    from app.mcp.servers.constant_contact_server import call_tool

    with patch.dict("os.environ", {"CONSTANT_CONTACT_API_KEY": ""}):
        os.environ.pop("CONSTANT_CONTACT_API_KEY", None)
        result = await call_tool("constant_contact_list_contacts", {})
    assert "error" in result


# ===========================================================================
# 5 — Drip
# ===========================================================================

_DRIP = {"DRIP_API_TOKEN": "drip-tok"}


@pytest.mark.asyncio
async def test_drip_list_subscribers():
    from app.mcp.servers.drip_server import call_tool

    mc = mk_client(get=make_resp(data={"subscribers": [{"id": "s1", "email": "a@b.com", "status": "active"}], "meta": {"total_count": 1}}))
    with patch.dict("os.environ", _DRIP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("drip_list_subscribers", {"account_id": "acc1"})
    assert result["subscribers"][0]["email"] == "a@b.com"


@pytest.mark.asyncio
async def test_drip_create_subscriber():
    from app.mcp.servers.drip_server import call_tool

    mc = mk_client(post=make_resp(data={"subscribers": [{"id": "s2", "email": "new@b.com"}]}))
    with patch.dict("os.environ", _DRIP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("drip_create_subscriber", {"account_id": "acc1", "email": "new@b.com"})
    assert result.get("email") == "new@b.com"


@pytest.mark.asyncio
async def test_drip_tag_subscriber():
    from app.mcp.servers.drip_server import call_tool

    mc = mk_client(post=make_resp(status=204))
    with patch.dict("os.environ", _DRIP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("drip_tag_subscriber", {"account_id": "acc1", "email": "a@b.com", "tag": "vip"})
    assert result.get("success") is True


@pytest.mark.asyncio
async def test_drip_list_campaigns():
    from app.mcp.servers.drip_server import call_tool

    mc = mk_client(get=make_resp(data={"campaigns": [{"id": "cam1", "name": "Welcome", "status": "active"}]}))
    with patch.dict("os.environ", _DRIP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("drip_list_campaigns", {"account_id": "acc1"})
    assert result["campaigns"][0]["name"] == "Welcome"


@pytest.mark.asyncio
async def test_drip_missing_env():
    from app.mcp.servers.drip_server import call_tool

    with patch.dict("os.environ", {"DRIP_API_TOKEN": ""}):
        os.environ.pop("DRIP_API_TOKEN", None)
        result = await call_tool("drip_list_subscribers", {"account_id": "acc1"})
    assert "error" in result


# ===========================================================================
# 6 — GetResponse
# ===========================================================================

_GR = {"GETRESPONSE_API_KEY": "gr-key"}


@pytest.mark.asyncio
async def test_getresponse_get_lists():
    from app.mcp.servers.getresponse_server import call_tool

    mc = mk_client(get=make_resp(data=[{"campaignId": "c1", "name": "Newsletter"}]))
    with patch.dict("os.environ", _GR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("getresponse_get_lists", {})
    assert result["lists"][0]["campaignId"] == "c1"


@pytest.mark.asyncio
async def test_getresponse_list_contacts():
    from app.mcp.servers.getresponse_server import call_tool

    mc = mk_client(get=make_resp(data=[{"contactId": "ct1", "email": "a@b.com", "name": "Alice"}]))
    with patch.dict("os.environ", _GR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("getresponse_list_contacts", {})
    assert result["contacts"][0]["email"] == "a@b.com"


@pytest.mark.asyncio
async def test_getresponse_create_contact():
    from app.mcp.servers.getresponse_server import call_tool

    mc = mk_client(post=make_resp(status=202))
    with patch.dict("os.environ", _GR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("getresponse_create_contact", {"email": "new@b.com", "campaign_id": "c1"})
    assert result.get("success") is True


@pytest.mark.asyncio
async def test_getresponse_missing_env():
    from app.mcp.servers.getresponse_server import call_tool

    with patch.dict("os.environ", {"GETRESPONSE_API_KEY": ""}):
        os.environ.pop("GETRESPONSE_API_KEY", None)
        result = await call_tool("getresponse_get_lists", {})
    assert "error" in result


# ===========================================================================
# 7 — Mailgun
# ===========================================================================

_MG = {"MAILGUN_API_KEY": "key-mailgun", "MAILGUN_DOMAIN": "mg.example.com"}


@pytest.mark.asyncio
async def test_mailgun_send_email():
    from app.mcp.servers.mailgun_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "<msg1@mg.example.com>", "message": "Queued"}))
    with patch.dict("os.environ", _MG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mailgun_send_email", {"to": "a@b.com", "subject": "Test", "text": "Hello"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_mailgun_list_domains():
    from app.mcp.servers.mailgun_server import call_tool

    mc = mk_client(get=make_resp(data={"items": [{"name": "mg.example.com", "state": "active", "type": "sandbox"}], "total_count": 1}))
    with patch.dict("os.environ", _MG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mailgun_list_domains", {})
    assert result["domains"][0]["name"] == "mg.example.com"


@pytest.mark.asyncio
async def test_mailgun_list_events():
    from app.mcp.servers.mailgun_server import call_tool

    mc = mk_client(get=make_resp(data={"items": [{"event": "delivered", "recipient": "a@b.com"}], "paging": {}}))
    with patch.dict("os.environ", _MG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mailgun_list_events", {"domain": "mg.example.com"})
    assert "items" in result


@pytest.mark.asyncio
async def test_mailgun_add_list_member():
    from app.mcp.servers.mailgun_server import call_tool

    mc = mk_client(post=make_resp(data={"member": {"address": "m@b.com", "subscribed": True}}))
    with patch.dict("os.environ", _MG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mailgun_add_list_member", {"list_address": "team@mg.example.com", "email": "m@b.com"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_mailgun_missing_env():
    from app.mcp.servers.mailgun_server import call_tool

    with patch.dict("os.environ", {"MAILGUN_API_KEY": ""}):
        os.environ.pop("MAILGUN_API_KEY", None)
        result = await call_tool("mailgun_send_email", {"to": "a@b.com", "subject": "t"})
    assert "error" in result


# ===========================================================================
# 8 — Moosend
# ===========================================================================

_MS = {"MOOSEND_API_KEY": "moosend-key"}


@pytest.mark.asyncio
async def test_moosend_get_lists():
    from app.mcp.servers.moosend_server import call_tool

    mc = mk_client(get=make_resp(data={"Context": {"MailingLists": [{"ID": "l1", "Name": "Main", "ActiveMemberCount": 100}], "TotalPageCount": 1}}))
    with patch.dict("os.environ", _MS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("moosend_get_lists", {})
    assert result["lists"][0]["name"] == "Main"


@pytest.mark.asyncio
async def test_moosend_create_subscriber():
    from app.mcp.servers.moosend_server import call_tool

    mc = mk_client(post=make_resp(data={"Context": {"ID": "sub1"}}))
    with patch.dict("os.environ", _MS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("moosend_create_subscriber", {"list_id": "l1", "email": "a@b.com"})
    assert result["id"] == "sub1"


@pytest.mark.asyncio
async def test_moosend_get_campaigns():
    from app.mcp.servers.moosend_server import call_tool

    mc = mk_client(get=make_resp(data={"Context": {"Campaigns": [{"ID": "c1", "Name": "Weekly", "Status": "sent"}]}}))
    with patch.dict("os.environ", _MS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("moosend_get_campaigns", {})
    assert result["campaigns"][0]["name"] == "Weekly"


@pytest.mark.asyncio
async def test_moosend_missing_env():
    from app.mcp.servers.moosend_server import call_tool

    with patch.dict("os.environ", {"MOOSEND_API_KEY": ""}):
        os.environ.pop("MOOSEND_API_KEY", None)
        result = await call_tool("moosend_get_lists", {})
    assert "error" in result


# ===========================================================================
# 9 — Omnisend
# ===========================================================================

_OS = {"OMNISEND_API_KEY": "omnisend-key"}


@pytest.mark.asyncio
async def test_omnisend_list_contacts():
    from app.mcp.servers.omnisend_server import call_tool

    mc = mk_client(get=make_resp(data={"contacts": [{"contactID": "ct1", "email": "a@b.com", "status": "subscribed"}], "total": 1}))
    with patch.dict("os.environ", _OS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("omnisend_list_contacts", {})
    assert result["contacts"][0]["email"] == "a@b.com"


@pytest.mark.asyncio
async def test_omnisend_create_contact():
    from app.mcp.servers.omnisend_server import call_tool

    mc = mk_client(post=make_resp(data={"contactID": "ct2"}))
    with patch.dict("os.environ", _OS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("omnisend_create_contact", {"email": "new@b.com"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_omnisend_track_event():
    from app.mcp.servers.omnisend_server import call_tool

    mc = mk_client(post=make_resp(status=200))
    with patch.dict("os.environ", _OS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("omnisend_track_event", {"email": "a@b.com", "event_name": "purchase"})
    assert result.get("success") is True


@pytest.mark.asyncio
async def test_omnisend_list_campaigns():
    from app.mcp.servers.omnisend_server import call_tool

    mc = mk_client(get=make_resp(data={"campaigns": [{"campaignID": "c1", "name": "Promo", "status": "sent"}]}))
    with patch.dict("os.environ", _OS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("omnisend_list_campaigns", {})
    assert result["campaigns"][0]["campaignID"] == "c1"


@pytest.mark.asyncio
async def test_omnisend_missing_env():
    from app.mcp.servers.omnisend_server import call_tool

    with patch.dict("os.environ", {"OMNISEND_API_KEY": ""}):
        os.environ.pop("OMNISEND_API_KEY", None)
        result = await call_tool("omnisend_list_contacts", {})
    assert "error" in result


# ===========================================================================
# 10 — Loops
# ===========================================================================

_LOOPS = {"LOOPS_API_KEY": "loops-key"}


@pytest.mark.asyncio
async def test_loops_create_contact():
    from app.mcp.servers.loops_server import call_tool

    mc = mk_client(post=make_resp(data={"success": True, "id": "cid1"}))
    with patch.dict("os.environ", _LOOPS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("loops_create_contact", {"email": "a@b.com", "first_name": "Alice"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_loops_send_transactional_email():
    from app.mcp.servers.loops_server import call_tool

    mc = mk_client(post=make_resp(data={"success": True}))
    with patch.dict("os.environ", _LOOPS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("loops_send_transactional_email", {"transactional_id": "tmpl1", "email": "a@b.com", "data_variables": {"name": "Alice"}})
    assert "error" not in result


@pytest.mark.asyncio
async def test_loops_find_contact():
    from app.mcp.servers.loops_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "ct1", "email": "a@b.com"}]))
    with patch.dict("os.environ", _LOOPS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("loops_find_contact", {"email": "a@b.com"})
    assert "contacts" in result


@pytest.mark.asyncio
async def test_loops_list_events():
    from app.mcp.servers.loops_server import call_tool

    mc = mk_client(get=make_resp(data=["signup", "purchase"]))
    with patch.dict("os.environ", _LOOPS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("loops_list_events", {})
    assert "events" in result


@pytest.mark.asyncio
async def test_loops_missing_env():
    from app.mcp.servers.loops_server import call_tool

    with patch.dict("os.environ", {"LOOPS_API_KEY": ""}):
        os.environ.pop("LOOPS_API_KEY", None)
        result = await call_tool("loops_create_contact", {"email": "a@b.com"})
    assert "error" in result


# ===========================================================================
# 11 — ManyChat
# ===========================================================================

_MCH = {"MANYCHAT_API_KEY": "mc-key"}


@pytest.mark.asyncio
async def test_manychat_get_subscriber_info():
    from app.mcp.servers.manychat_server import call_tool

    mc = mk_client(get=make_resp(data={"status": "success", "data": {"id": "sub1", "first_name": "Alice"}}))
    with patch.dict("os.environ", _MCH), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("manychat_get_subscriber_info", {"subscriber_id": "sub1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_manychat_add_tag():
    from app.mcp.servers.manychat_server import call_tool

    mc = mk_client(post=make_resp(data={"status": "success"}))
    with patch.dict("os.environ", _MCH), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("manychat_add_tag", {"subscriber_id": "sub1", "tag_id": 42})
    assert "error" not in result


@pytest.mark.asyncio
async def test_manychat_remove_tag():
    from app.mcp.servers.manychat_server import call_tool

    mc = mk_client(post=make_resp(data={"status": "success"}))
    with patch.dict("os.environ", _MCH), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("manychat_remove_tag", {"subscriber_id": "sub1", "tag_id": 42})
    assert "error" not in result


@pytest.mark.asyncio
async def test_manychat_missing_env():
    from app.mcp.servers.manychat_server import call_tool

    with patch.dict("os.environ", {"MANYCHAT_API_KEY": ""}):
        os.environ.pop("MANYCHAT_API_KEY", None)
        result = await call_tool("manychat_get_subscriber_info", {"subscriber_id": "s1"})
    assert "error" in result


# ===========================================================================
# 12 — OneSignal
# ===========================================================================

_ONE = {"ONESIGNAL_API_KEY": "one-key", "ONESIGNAL_APP_ID": "app-uuid"}


@pytest.mark.asyncio
async def test_onesignal_create_notification():
    from app.mcp.servers.onesignal_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "notif1", "recipients": 100}))
    with patch.dict("os.environ", _ONE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("onesignal_create_notification", {"contents": {"en": "Hello!"}, "included_segments": ["All"]})
    assert result.get("id") == "notif1"


@pytest.mark.asyncio
async def test_onesignal_list_devices():
    from app.mcp.servers.onesignal_server import call_tool

    mc = mk_client(get=make_resp(data={"players": [{"id": "d1", "device_type": 0, "last_active": 1704067200}], "total_count": 1}))
    with patch.dict("os.environ", _ONE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("onesignal_list_devices", {})
    assert result["players"][0]["id"] == "d1"


@pytest.mark.asyncio
async def test_onesignal_cancel_notification():
    from app.mcp.servers.onesignal_server import call_tool

    mc = mk_client(delete=make_resp(data={"success": "true"}))
    with patch.dict("os.environ", _ONE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("onesignal_cancel_notification", {"notification_id": "notif1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_onesignal_missing_env():
    from app.mcp.servers.onesignal_server import call_tool

    with patch.dict("os.environ", {"ONESIGNAL_API_KEY": ""}):
        os.environ.pop("ONESIGNAL_API_KEY", None)
        result = await call_tool("onesignal_create_notification", {"contents": {"en": "Hi"}})
    assert "error" in result


# ===========================================================================
# 13 — Pushover
# ===========================================================================

_PO = {"PUSHOVER_TOKEN": "po-token", "PUSHOVER_USER": "po-user"}


@pytest.mark.asyncio
async def test_pushover_send_notification():
    from app.mcp.servers.pushover_server import call_tool

    mc = mk_client(post=make_resp(data={"status": 1, "request": "req123"}))
    with patch.dict("os.environ", _PO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("pushover_send_notification", {"message": "Hello!"})
    assert result.get("status") == 1


@pytest.mark.asyncio
async def test_pushover_get_sounds():
    from app.mcp.servers.pushover_server import call_tool

    mc = mk_client(get=make_resp(data={"status": 1, "sounds": {"pushover": "Pushover (default)", "bike": "Bike"}}))
    with patch.dict("os.environ", _PO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("pushover_get_sounds", {})
    assert "sounds" in result


@pytest.mark.asyncio
async def test_pushover_validate_user():
    from app.mcp.servers.pushover_server import call_tool

    mc = mk_client(post=make_resp(data={"status": 1, "request": "req456"}))
    with patch.dict("os.environ", _PO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("pushover_validate_user", {"user": "po-user"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_pushover_missing_token():
    from app.mcp.servers.pushover_server import call_tool

    with patch.dict("os.environ", {"PUSHOVER_TOKEN": "", "PUSHOVER_USER": "u"}):
        os.environ.pop("PUSHOVER_TOKEN", None)
        result = await call_tool("pushover_send_notification", {"message": "Hi"})
    assert "error" in result


# ===========================================================================
# 14 — Pushbullet
# ===========================================================================

_PB = {"PUSHBULLET_API_KEY": "pb-key"}


@pytest.mark.asyncio
async def test_pushbullet_push_note():
    from app.mcp.servers.pushbullet_server import call_tool

    mc = mk_client(post=make_resp(data={"iden": "push1", "type": "note", "title": "Hello", "body": "World"}))
    with patch.dict("os.environ", _PB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("pushbullet_push_note", {"title": "Hello", "body": "World"})
    assert result.get("type") == "note"


@pytest.mark.asyncio
async def test_pushbullet_list_devices():
    from app.mcp.servers.pushbullet_server import call_tool

    mc = mk_client(get=make_resp(data={"devices": [{"iden": "dev1", "nickname": "My Phone", "type": "android", "active": True}]}))
    with patch.dict("os.environ", _PB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("pushbullet_list_devices", {})
    assert result["devices"][0]["iden"] == "dev1"


@pytest.mark.asyncio
async def test_pushbullet_push_link():
    from app.mcp.servers.pushbullet_server import call_tool

    mc = mk_client(post=make_resp(data={"iden": "push2", "type": "link", "title": "Check this", "url": "https://example.com"}))
    with patch.dict("os.environ", _PB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("pushbullet_push_link", {"title": "Check this", "url": "https://example.com"})
    assert result.get("type") == "link"


@pytest.mark.asyncio
async def test_pushbullet_get_user_info():
    from app.mcp.servers.pushbullet_server import call_tool

    mc = mk_client(get=make_resp(data={"iden": "u1", "email": "me@example.com", "name": "Me"}))
    with patch.dict("os.environ", _PB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("pushbullet_get_user_info", {})
    assert result.get("email") == "me@example.com"


@pytest.mark.asyncio
async def test_pushbullet_missing_env():
    from app.mcp.servers.pushbullet_server import call_tool

    with patch.dict("os.environ", {"PUSHBULLET_API_KEY": ""}):
        os.environ.pop("PUSHBULLET_API_KEY", None)
        result = await call_tool("pushbullet_push_note", {"title": "t", "body": "b"})
    assert "error" in result


# ===========================================================================
# 15 — Postmark
# ===========================================================================

_PMK = {"POSTMARK_SERVER_TOKEN": "pmk-token"}


@pytest.mark.asyncio
async def test_postmark_send_email():
    from app.mcp.servers.postmark_server import call_tool

    mc = mk_client(post=make_resp(data={"To": "a@b.com", "MessageID": "msg1", "ErrorCode": 0, "Message": "OK"}))
    with patch.dict("os.environ", _PMK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("postmark_send_email", {"from_email": "s@example.com", "to": "a@b.com", "subject": "Test", "text_body": "Hello"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_postmark_list_message_streams():
    from app.mcp.servers.postmark_server import call_tool

    mc = mk_client(get=make_resp(data={"MessageStreams": [{"ID": "outbound", "Name": "Default Transactional Stream", "MessageStreamType": "Transactional"}]}))
    with patch.dict("os.environ", _PMK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("postmark_list_message_streams", {})
    assert result["streams"][0]["ID"] == "outbound"


@pytest.mark.asyncio
async def test_postmark_list_bounces():
    from app.mcp.servers.postmark_server import call_tool

    mc = mk_client(get=make_resp(data={"Bounces": [{"ID": 1, "Email": "bad@x.com", "Type": "HardBounce"}], "TotalCount": 1}))
    with patch.dict("os.environ", _PMK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("postmark_list_bounces", {})
    assert result["bounces"][0]["Email"] == "bad@x.com"


@pytest.mark.asyncio
async def test_postmark_get_delivery_stats():
    from app.mcp.servers.postmark_server import call_tool

    mc = mk_client(get=make_resp(data={"InactiveMails": 5, "Bounces": []}))
    with patch.dict("os.environ", _PMK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("postmark_get_delivery_stats", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_postmark_missing_env():
    from app.mcp.servers.postmark_server import call_tool

    with patch.dict("os.environ", {"POSTMARK_SERVER_TOKEN": ""}):
        os.environ.pop("POSTMARK_SERVER_TOKEN", None)
        result = await call_tool("postmark_send_email", {"from_email": "s@e.com", "to": "a@b.com", "subject": "t"})
    assert "error" in result


# ===========================================================================
# 16 — RingCentral
# ===========================================================================

_RC = {"RINGCENTRAL_ACCESS_TOKEN": "rc-token"}


@pytest.mark.asyncio
async def test_ringcentral_send_sms():
    from app.mcp.servers.ringcentral_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "msg1", "type": "SMS", "status": "Queued"}))
    with patch.dict("os.environ", _RC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("ringcentral_send_sms", {"to": "+15559876543", "from_number": "+15551234567", "text": "Hi"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_ringcentral_get_account_info():
    from app.mcp.servers.ringcentral_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "acc1", "status": "Confirmed", "mainNumber": "+15551234567"}))
    with patch.dict("os.environ", _RC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("ringcentral_get_account_info", {})
    assert result.get("id") == "acc1"


@pytest.mark.asyncio
async def test_ringcentral_list_extensions():
    from app.mcp.servers.ringcentral_server import call_tool

    mc = mk_client(get=make_resp(data={"records": [{"id": "e1", "name": "Alice", "type": "User", "extensionNumber": "101"}], "paging": {}}))
    with patch.dict("os.environ", _RC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("ringcentral_list_extensions", {})
    assert result["extensions"][0]["name"] == "Alice"


@pytest.mark.asyncio
async def test_ringcentral_list_calls():
    from app.mcp.servers.ringcentral_server import call_tool

    mc = mk_client(get=make_resp(data={"records": [{"id": "c1", "direction": "Outbound", "duration": 60, "startTime": "2024-01-01T10:00:00Z"}]}))
    with patch.dict("os.environ", _RC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("ringcentral_list_calls", {})
    assert result["records"][0]["id"] == "c1"


@pytest.mark.asyncio
async def test_ringcentral_missing_env():
    from app.mcp.servers.ringcentral_server import call_tool

    with patch.dict("os.environ", {"RINGCENTRAL_ACCESS_TOKEN": ""}):
        os.environ.pop("RINGCENTRAL_ACCESS_TOKEN", None)
        result = await call_tool("ringcentral_send_sms", {"to": "+1", "from_number": "+1", "text": "hi"})
    assert "error" in result


# ===========================================================================
# 17 — Plivo
# ===========================================================================

_PLV = {"PLIVO_AUTH_ID": "plivo-id", "PLIVO_AUTH_TOKEN": "plivo-tok"}


@pytest.mark.asyncio
async def test_plivo_send_sms():
    from app.mcp.servers.plivo_server import call_tool

    mc = mk_client(post=make_resp(data={"message": "request to send", "message_uuid": ["uuid1"], "api_id": "api1"}))
    with patch.dict("os.environ", _PLV), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("plivo_send_sms", {"src": "+15551234567", "dst": "+15559876543", "text": "Hi"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_plivo_get_account():
    from app.mcp.servers.plivo_server import call_tool

    mc = mk_client(get=make_resp(data={"account_type": "standard", "cash_credits": "10.00", "auth_id": "plivo-id"}))
    with patch.dict("os.environ", _PLV), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("plivo_get_account", {})
    assert result.get("auth_id") == "plivo-id"


@pytest.mark.asyncio
async def test_plivo_list_messages():
    from app.mcp.servers.plivo_server import call_tool

    mc = mk_client(get=make_resp(data={"objects": [{"message_uuid": "uuid1", "from_number": "+1", "to_number": "+2", "message_state": "delivered"}], "meta": {}}))
    with patch.dict("os.environ", _PLV), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("plivo_list_messages", {})
    assert result["messages"][0]["message_uuid"] == "uuid1"


@pytest.mark.asyncio
async def test_plivo_list_phone_numbers():
    from app.mcp.servers.plivo_server import call_tool

    mc = mk_client(get=make_resp(data={"objects": [{"number": "+15551234567", "number_type": "local", "country": "US"}], "meta": {}}))
    with patch.dict("os.environ", _PLV), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("plivo_list_phone_numbers", {})
    assert result["numbers"][0]["number"] == "+15551234567"


@pytest.mark.asyncio
async def test_plivo_missing_env():
    from app.mcp.servers.plivo_server import call_tool

    with patch.dict("os.environ", {"PLIVO_AUTH_ID": ""}):
        os.environ.pop("PLIVO_AUTH_ID", None)
        result = await call_tool("plivo_send_sms", {"src": "+1", "dst": "+2", "text": "hi"})
    assert "error" in result


# ===========================================================================
# 18 — Vonage
# ===========================================================================

_VNG = {"VONAGE_API_KEY": "vonage-key", "VONAGE_API_SECRET": "vonage-secret"}


@pytest.mark.asyncio
async def test_vonage_send_sms():
    from app.mcp.servers.vonage_server import call_tool

    mc = mk_client(post=make_resp(data={"message-count": "1", "messages": [{"message-id": "mid1", "status": "0", "to": "+15559876543"}]}))
    with patch.dict("os.environ", _VNG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("vonage_send_sms", {"from_number": "+15551234567", "to": "+15559876543", "text": "Hello"})
    assert result["messages"][0]["message-id"] == "mid1"


@pytest.mark.asyncio
async def test_vonage_get_account_balance():
    from app.mcp.servers.vonage_server import call_tool

    mc = mk_client(get=make_resp(data={"value": 10.50, "autoReload": False}))
    with patch.dict("os.environ", _VNG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("vonage_get_account_balance", {})
    assert result.get("value") == 10.50


@pytest.mark.asyncio
async def test_vonage_list_numbers():
    from app.mcp.servers.vonage_server import call_tool

    mc = mk_client(get=make_resp(data={"count": 1, "numbers": [{"msisdn": "+15551234567", "country": "US", "type": "mobile-lvn"}]}))
    with patch.dict("os.environ", _VNG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("vonage_list_numbers", {})
    assert result["numbers"][0]["msisdn"] == "+15551234567"


@pytest.mark.asyncio
async def test_vonage_list_calls():
    from app.mcp.servers.vonage_server import call_tool

    mc = mk_client(get=make_resp(data={"count": 1, "_embedded": {"calls": [{"uuid": "call1", "status": "completed", "direction": "outbound", "duration": "60"}]}}))
    with patch.dict("os.environ", _VNG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("vonage_list_calls", {})
    assert result["calls"][0]["uuid"] == "call1"


@pytest.mark.asyncio
async def test_vonage_missing_env():
    from app.mcp.servers.vonage_server import call_tool

    with patch.dict("os.environ", {"VONAGE_API_KEY": ""}):
        os.environ.pop("VONAGE_API_KEY", None)
        result = await call_tool("vonage_send_sms", {"from_number": "+1", "to": "+2", "text": "hi"})
    assert "error" in result


# ===========================================================================
# 19 — Twitch
# ===========================================================================

_TWC = {"TWITCH_CLIENT_ID": "twitch-cid", "TWITCH_ACCESS_TOKEN": "twitch-tok"}


@pytest.mark.asyncio
async def test_twitch_get_streams():
    from app.mcp.servers.twitch_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "s1", "user_name": "streamer1", "game_name": "Chess", "title": "Playing chess", "viewer_count": 500}], "pagination": {}}))
    with patch.dict("os.environ", _TWC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("twitch_get_streams", {"first": 5})
    assert result["streams"][0]["user_name"] == "streamer1"


@pytest.mark.asyncio
async def test_twitch_get_user():
    from app.mcp.servers.twitch_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "u1", "login": "alice", "display_name": "Alice"}]}))
    with patch.dict("os.environ", _TWC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("twitch_get_user", {"login": "alice"})
    assert result["user"]["login"] == "alice"


@pytest.mark.asyncio
async def test_twitch_search_channels():
    from app.mcp.servers.twitch_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "c1", "display_name": "GameChan", "game_name": "Chess", "is_live": True}]}))
    with patch.dict("os.environ", _TWC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("twitch_search_channels", {"query": "chess"})
    assert result["channels"][0]["is_live"] is True


@pytest.mark.asyncio
async def test_twitch_get_channel_info():
    from app.mcp.servers.twitch_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"broadcaster_id": "u1", "broadcaster_name": "Alice", "game_name": "Chess", "title": "Live Chess"}]}))
    with patch.dict("os.environ", _TWC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("twitch_get_channel_info", {"broadcaster_id": "u1"})
    assert result["channel"]["broadcaster_name"] == "Alice"


@pytest.mark.asyncio
async def test_twitch_missing_env():
    from app.mcp.servers.twitch_server import call_tool

    with patch.dict("os.environ", {"TWITCH_CLIENT_ID": ""}):
        os.environ.pop("TWITCH_CLIENT_ID", None)
        result = await call_tool("twitch_get_streams", {})
    assert "error" in result


# ===========================================================================
# 20 — Gmail
# ===========================================================================

_GMAIL = {"GMAIL_ACCESS_TOKEN": "gmail-tok"}


@pytest.mark.asyncio
async def test_gmail_list_messages():
    from app.mcp.servers.gmail_server import call_tool

    mc = mk_client(get=make_resp(data={"messages": [{"id": "msg1", "threadId": "thr1"}], "resultSizeEstimate": 1}))
    with patch.dict("os.environ", _GMAIL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gmail_list_messages", {})
    assert result["messages"][0]["id"] == "msg1"


@pytest.mark.asyncio
async def test_gmail_send_message():
    from app.mcp.servers.gmail_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "sent1", "threadId": "thr2", "labelIds": ["SENT"]}))
    with patch.dict("os.environ", _GMAIL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gmail_send_message", {"to": "a@b.com", "subject": "Test", "body": "Hello!"})
    assert result.get("id") == "sent1"


@pytest.mark.asyncio
async def test_gmail_list_labels():
    from app.mcp.servers.gmail_server import call_tool

    mc = mk_client(get=make_resp(data={"labels": [{"id": "INBOX", "name": "INBOX", "type": "system"}, {"id": "SENT", "name": "SENT", "type": "system"}]}))
    with patch.dict("os.environ", _GMAIL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gmail_list_labels", {})
    assert any(lbl["id"] == "INBOX" for lbl in result["labels"])


@pytest.mark.asyncio
async def test_gmail_create_draft():
    from app.mcp.servers.gmail_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "draft1", "message": {"id": "msg2"}}))
    with patch.dict("os.environ", _GMAIL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gmail_create_draft", {"to": "a@b.com", "subject": "Draft", "body": "Draft body"})
    assert result.get("id") == "draft1"


@pytest.mark.asyncio
async def test_gmail_search_messages():
    from app.mcp.servers.gmail_server import call_tool

    mc = mk_client(get=make_resp(data={"messages": [{"id": "msg3", "threadId": "thr3"}], "resultSizeEstimate": 1}))
    with patch.dict("os.environ", _GMAIL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gmail_search_messages", {"query": "from:alice@example.com"})
    assert result["resultSizeEstimate"] == 1


@pytest.mark.asyncio
async def test_gmail_get_message():
    from app.mcp.servers.gmail_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "msg1", "payload": {"headers": [{"name": "Subject", "value": "Hello"}]}}))
    with patch.dict("os.environ", _GMAIL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gmail_get_message", {"message_id": "msg1"})
    assert result.get("id") == "msg1"


@pytest.mark.asyncio
async def test_gmail_missing_env():
    from app.mcp.servers.gmail_server import call_tool

    with patch.dict("os.environ", {"GMAIL_ACCESS_TOKEN": ""}):
        os.environ.pop("GMAIL_ACCESS_TOKEN", None)
        result = await call_tool("gmail_list_messages", {})
    assert "error" in result
