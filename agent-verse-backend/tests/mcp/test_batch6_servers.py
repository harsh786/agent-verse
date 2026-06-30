"""Tests for Batch 6 MCP servers (files 1-20).

Covers: databox, geckoboard, elevenlabs, gemini, fireflies,
        phantombuster, google_contacts, google_chat, google_meet,
        google_my_business, google_photos, gotowebinar, livestorm,
        eventbrite, meetup, zenloop, delighted, freshchat, zoho_desk,
        help_scout
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
# 1. Databox
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_databox_push_data():
    from app.mcp.servers.databox_server import call_tool
    mc = mk_client(post=make_resp(200, {"status": "ok"}))
    with patch.dict("os.environ", {"DATABOX_API_KEY": "test_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("databox_push_data", {"push_token": "tok", "data": [{"key": "revenue", "value": 100}]})
    assert "error" not in r


@pytest.mark.asyncio
async def test_databox_list_databoards():
    from app.mcp.servers.databox_server import call_tool
    mc = mk_client(get=make_resp(200, {"databoards": []}))
    with patch.dict("os.environ", {"DATABOX_API_KEY": "test_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("databox_list_databoards", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_databox_no_key():
    from app.mcp.servers.databox_server import call_tool
    with patch.dict("os.environ", {}, clear=True):
        r = await call_tool("databox_push_data", {"push_token": "tok", "data": []})
    assert "error" in r


@pytest.mark.asyncio
async def test_databox_unknown_tool():
    from app.mcp.servers.databox_server import call_tool
    mc = mk_client()
    with patch.dict("os.environ", {"DATABOX_API_KEY": "test_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("unknown_tool", {})
    assert "error" in r


# ---------------------------------------------------------------------------
# 2. Geckoboard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_geckoboard_list_dashboards():
    from app.mcp.servers.geckoboard_server import call_tool
    mc = mk_client(get=make_resp(200, {"dashboards": []}))
    with patch.dict("os.environ", {"GECKOBOARD_API_KEY": "gk_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("geckoboard_list_dashboards", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_geckoboard_create_dataset():
    from app.mcp.servers.geckoboard_server import call_tool
    mc = mk_client(put=make_resp(201, {"id": "my_ds"}))
    with patch.dict("os.environ", {"GECKOBOARD_API_KEY": "gk_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("geckoboard_create_dataset", {"dataset_id": "my_ds", "fields": {"revenue": {"type": "number"}}})
    assert "error" not in r


@pytest.mark.asyncio
async def test_geckoboard_delete_dataset_item():
    from app.mcp.servers.geckoboard_server import call_tool
    mc = mk_client(delete=make_resp(204))
    mc.delete.return_value.raise_for_status = MagicMock()
    with patch.dict("os.environ", {"GECKOBOARD_API_KEY": "gk_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("geckoboard_delete_dataset_item", {"dataset_id": "my_ds"})
    assert r.get("deleted") is True


@pytest.mark.asyncio
async def test_geckoboard_no_key():
    from app.mcp.servers.geckoboard_server import call_tool
    with patch.dict("os.environ", {}, clear=True):
        r = await call_tool("geckoboard_list_dashboards", {})
    assert "error" in r


# ---------------------------------------------------------------------------
# 3. ElevenLabs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_elevenlabs_list_voices():
    from app.mcp.servers.elevenlabs_server import call_tool
    mc = mk_client(get=make_resp(200, {"voices": []}))
    with patch.dict("os.environ", {"ELEVENLABS_API_KEY": "el_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("elevenlabs_list_voices", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_elevenlabs_list_models():
    from app.mcp.servers.elevenlabs_server import call_tool
    mc = mk_client(get=make_resp(200, [{"model_id": "eleven_monolingual_v1"}]))
    with patch.dict("os.environ", {"ELEVENLABS_API_KEY": "el_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("elevenlabs_list_models", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_elevenlabs_get_usage_stats():
    from app.mcp.servers.elevenlabs_server import call_tool
    mc = mk_client(get=make_resp(200, {"character_count": 1000}))
    with patch.dict("os.environ", {"ELEVENLABS_API_KEY": "el_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("elevenlabs_get_usage_stats", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_elevenlabs_unknown_tool():
    from app.mcp.servers.elevenlabs_server import call_tool
    mc = mk_client()
    with patch.dict("os.environ", {"ELEVENLABS_API_KEY": "el_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("elevenlabs_xyz", {})
    assert "error" in r


# ---------------------------------------------------------------------------
# 4. Gemini
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gemini_generate_text():
    from app.mcp.servers.gemini_server import call_tool
    mc = mk_client(post=make_resp(200, {"candidates": []}))
    with patch.dict("os.environ", {"GEMINI_API_KEY": "g_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("gemini_generate_text", {"prompt": "Hello"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_gemini_list_models():
    from app.mcp.servers.gemini_server import call_tool
    mc = mk_client(get=make_resp(200, {"models": []}))
    with patch.dict("os.environ", {"GEMINI_API_KEY": "g_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("gemini_list_models", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_gemini_embed_text():
    from app.mcp.servers.gemini_server import call_tool
    mc = mk_client(post=make_resp(200, {"embedding": {"values": [0.1, 0.2]}}))
    with patch.dict("os.environ", {"GEMINI_API_KEY": "g_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("gemini_embed_text", {"text": "hello world"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_gemini_no_key():
    from app.mcp.servers.gemini_server import call_tool
    with patch.dict("os.environ", {}, clear=True):
        r = await call_tool("gemini_generate_text", {"prompt": "test"})
    assert "error" in r


# ---------------------------------------------------------------------------
# 5. Fireflies
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fireflies_list_transcripts():
    from app.mcp.servers.fireflies_server import call_tool
    mc = mk_client(post=make_resp(200, {"data": {"transcripts": []}}))
    with patch.dict("os.environ", {"FIREFLIES_API_KEY": "ff_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("fireflies_list_transcripts", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_fireflies_get_summary():
    from app.mcp.servers.fireflies_server import call_tool
    mc = mk_client(post=make_resp(200, {"data": {"transcript": {"id": "t1"}}}))
    with patch.dict("os.environ", {"FIREFLIES_API_KEY": "ff_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("fireflies_get_summary", {"transcript_id": "t1"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_fireflies_no_key():
    from app.mcp.servers.fireflies_server import call_tool
    with patch.dict("os.environ", {}, clear=True):
        r = await call_tool("fireflies_list_transcripts", {})
    assert "error" in r


# ---------------------------------------------------------------------------
# 6. PhantomBuster
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_phantombuster_list_agents():
    from app.mcp.servers.phantombuster_server import call_tool
    mc = mk_client(get=make_resp(200, {"agents": []}))
    with patch.dict("os.environ", {"PHANTOMBUSTER_API_KEY": "pb_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("phantombuster_list_agents", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_phantombuster_launch_agent():
    from app.mcp.servers.phantombuster_server import call_tool
    mc = mk_client(post=make_resp(200, {"status": "started"}))
    with patch.dict("os.environ", {"PHANTOMBUSTER_API_KEY": "pb_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("phantombuster_launch_agent", {"agent_id": "agent123"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_phantombuster_abort_agent():
    from app.mcp.servers.phantombuster_server import call_tool
    mc = mk_client(post=make_resp(200, {"status": "aborted"}))
    with patch.dict("os.environ", {"PHANTOMBUSTER_API_KEY": "pb_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("phantombuster_abort_agent", {"agent_id": "agent123"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 7. Google Contacts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_contacts_list():
    from app.mcp.servers.google_contacts_server import call_tool
    mc = mk_client(get=make_resp(200, {"connections": []}))
    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": "g_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("google_contacts_list_contacts", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_google_contacts_search():
    from app.mcp.servers.google_contacts_server import call_tool
    mc = mk_client(get=make_resp(200, {"results": []}))
    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": "g_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("google_contacts_search_contacts", {"query": "john"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_google_contacts_delete():
    from app.mcp.servers.google_contacts_server import call_tool
    mc = mk_client(delete=make_resp(200))
    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": "g_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("google_contacts_delete_contact", {"resource_name": "people/c123"})
    assert r.get("deleted") is True


# ---------------------------------------------------------------------------
# 8. Google Chat
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_chat_list_spaces():
    from app.mcp.servers.google_chat_server import call_tool
    mc = mk_client(get=make_resp(200, {"spaces": []}))
    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": "g_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("google_chat_list_spaces", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_google_chat_send_message():
    from app.mcp.servers.google_chat_server import call_tool
    mc = mk_client(post=make_resp(200, {"name": "spaces/abc/messages/123"}))
    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": "g_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("google_chat_send_message", {"space_name": "spaces/abc", "text": "Hello"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 9. Google Meet
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_meet_create_meeting():
    from app.mcp.servers.google_meet_server import call_tool
    mc = mk_client(post=make_resp(200, {"name": "spaces/xyz", "meetingUri": "https://meet.google.com/xyz"}))
    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": "g_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("google_meet_create_meeting", {"title": "Team Sync"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_google_meet_list_meetings():
    from app.mcp.servers.google_meet_server import call_tool
    mc = mk_client(get=make_resp(200, {"conferenceRecords": []}))
    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": "g_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("google_meet_list_meetings", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 10. Google My Business
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_mybusiness_list_locations():
    from app.mcp.servers.google_my_business_server import call_tool
    mc = mk_client(get=make_resp(200, {"locations": []}))
    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": "g_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("google_mybusiness_list_locations", {"account_id": "acct123"})
    assert "error" not in r


@pytest.mark.asyncio
async def test_google_mybusiness_list_reviews():
    from app.mcp.servers.google_my_business_server import call_tool
    mc = mk_client(get=make_resp(200, {"reviews": []}))
    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": "g_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("google_mybusiness_list_reviews", {"location_name": "locations/abc"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 11. Google Photos
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_google_photos_list_media():
    from app.mcp.servers.google_photos_server import call_tool
    mc = mk_client(get=make_resp(200, {"mediaItems": []}))
    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": "g_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("google_photos_list_media_items", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_google_photos_create_album():
    from app.mcp.servers.google_photos_server import call_tool
    mc = mk_client(post=make_resp(200, {"id": "album1", "title": "Vacation"}))
    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": "g_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("google_photos_create_album", {"title": "Vacation"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 12. GoToWebinar
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gotowebinar_list_webinars():
    from app.mcp.servers.gotowebinar_server import call_tool
    mc = mk_client(get=make_resp(200, {"_embedded": {"webinars": []}}))
    with patch.dict("os.environ", {"GOTOWEBINAR_ACCESS_TOKEN": "gw_tok", "GOTOWEBINAR_ORGANIZER_KEY": "org_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("gotowebinar_list_webinars", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_gotowebinar_cancel_webinar():
    from app.mcp.servers.gotowebinar_server import call_tool
    mc = mk_client(delete=make_resp(204))
    mc.delete.return_value.raise_for_status = MagicMock()
    with patch.dict("os.environ", {"GOTOWEBINAR_ACCESS_TOKEN": "gw_tok", "GOTOWEBINAR_ORGANIZER_KEY": "org_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("gotowebinar_cancel_webinar", {"webinar_key": "wk1"})
    assert r.get("cancelled") is True


# ---------------------------------------------------------------------------
# 13. Livestorm
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_livestorm_list_events():
    from app.mcp.servers.livestorm_server import call_tool
    mc = mk_client(get=make_resp(200, {"data": []}))
    with patch.dict("os.environ", {"LIVESTORM_API_KEY": "ls_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("livestorm_list_events", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_livestorm_list_registrants():
    from app.mcp.servers.livestorm_server import call_tool
    mc = mk_client(get=make_resp(200, {"data": []}))
    with patch.dict("os.environ", {"LIVESTORM_API_KEY": "ls_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("livestorm_list_registrants", {"event_id": "ev1"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 14. Eventbrite
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_eventbrite_list_events():
    from app.mcp.servers.eventbrite_server import call_tool
    mc = mk_client(get=make_resp(200, {"events": []}))
    with patch.dict("os.environ", {"EVENTBRITE_API_KEY": "eb_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("eventbrite_list_events", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_eventbrite_publish_event():
    from app.mcp.servers.eventbrite_server import call_tool
    mc = mk_client(post=make_resp(200, {"published": True}))
    with patch.dict("os.environ", {"EVENTBRITE_API_KEY": "eb_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("eventbrite_publish_event", {"event_id": "evt1"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 15. Meetup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_meetup_list_groups():
    from app.mcp.servers.meetup_server import call_tool
    mc = mk_client(get=make_resp(200, []))
    with patch.dict("os.environ", {"MEETUP_ACCESS_TOKEN": "mu_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("meetup_list_groups", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_meetup_list_events():
    from app.mcp.servers.meetup_server import call_tool
    mc = mk_client(get=make_resp(200, []))
    with patch.dict("os.environ", {"MEETUP_ACCESS_TOKEN": "mu_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("meetup_list_events", {"group_urlname": "mygroup"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 16. Zenloop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_zenloop_list_surveys():
    from app.mcp.servers.zenloop_server import call_tool
    mc = mk_client(get=make_resp(200, {"surveys": []}))
    with patch.dict("os.environ", {"ZENLOOP_API_TOKEN": "zl_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("zenloop_list_surveys", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_zenloop_get_nps_score():
    from app.mcp.servers.zenloop_server import call_tool
    mc = mk_client(get=make_resp(200, {"score": 42}))
    with patch.dict("os.environ", {"ZENLOOP_API_TOKEN": "zl_tok"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("zenloop_get_nps_score", {"survey_id": "s1"})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 17. Delighted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delighted_list_people():
    from app.mcp.servers.delighted_server import call_tool
    mc = mk_client(get=make_resp(200, []))
    with patch.dict("os.environ", {"DELIGHTED_API_KEY": "dl_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("delighted_list_people", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_delighted_get_metrics():
    from app.mcp.servers.delighted_server import call_tool
    mc = mk_client(get=make_resp(200, {"nps": 50}))
    with patch.dict("os.environ", {"DELIGHTED_API_KEY": "dl_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("delighted_get_metrics", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 18. Freshchat
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_freshchat_list_conversations():
    from app.mcp.servers.freshchat_server import call_tool
    mc = mk_client(get=make_resp(200, {"conversations": []}))
    with patch.dict("os.environ", {"FRESHCHAT_API_TOKEN": "fc_tok", "FRESHCHAT_DOMAIN": "myco"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("freshchat_list_conversations", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_freshchat_list_agents():
    from app.mcp.servers.freshchat_server import call_tool
    mc = mk_client(get=make_resp(200, {"agents": []}))
    with patch.dict("os.environ", {"FRESHCHAT_API_TOKEN": "fc_tok", "FRESHCHAT_DOMAIN": "myco"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("freshchat_list_agents", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 19. Zoho Desk
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_zoho_desk_list_tickets():
    from app.mcp.servers.zoho_desk_server import call_tool
    mc = mk_client(get=make_resp(200, {"data": []}))
    with patch.dict("os.environ", {"ZOHO_DESK_ACCESS_TOKEN": "zd_tok", "ZOHO_DESK_ORG_ID": "org1"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("zoho_desk_list_tickets", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_zoho_desk_list_departments():
    from app.mcp.servers.zoho_desk_server import call_tool
    mc = mk_client(get=make_resp(200, {"data": []}))
    with patch.dict("os.environ", {"ZOHO_DESK_ACCESS_TOKEN": "zd_tok", "ZOHO_DESK_ORG_ID": "org1"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("zoho_desk_list_departments", {})
    assert "error" not in r


# ---------------------------------------------------------------------------
# 20. Help Scout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_helpscout_list_conversations():
    from app.mcp.servers.help_scout_server import call_tool
    mc = mk_client(get=make_resp(200, {"_embedded": {"conversations": []}}))
    with patch.dict("os.environ", {"HELP_SCOUT_API_KEY": "hs_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("helpscout_list_conversations", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_helpscout_list_mailboxes():
    from app.mcp.servers.help_scout_server import call_tool
    mc = mk_client(get=make_resp(200, {"_embedded": {"mailboxes": []}}))
    with patch.dict("os.environ", {"HELP_SCOUT_API_KEY": "hs_key"}), \
         patch("httpx.AsyncClient", return_value=mc):
        r = await call_tool("helpscout_list_mailboxes", {})
    assert "error" not in r


@pytest.mark.asyncio
async def test_helpscout_no_key():
    from app.mcp.servers.help_scout_server import call_tool
    with patch.dict("os.environ", {}, clear=True):
        r = await call_tool("helpscout_list_conversations", {})
    assert "error" in r
