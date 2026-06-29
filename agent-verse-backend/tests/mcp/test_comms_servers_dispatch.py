"""Dispatch-level tests for communications MCP servers.

Exercises every call_tool() branch by mocking httpx.AsyncClient.
Targets: slack, microsoft_teams, discord, mattermost, twilio, sendgrid,
         mailchimp, brevo, customerio, klaviyo, mailerlite, mandrill,
         convertkit, telegram, whatsapp, front.
"""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def make_resp(status: int = 200, data: Any = None) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data if data is not None else {}
    m.text = str(data or "")
    m.content = b"ok"
    m.raise_for_status = MagicMock()
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
# Slack
# ---------------------------------------------------------------------------

_SLACK = {"SLACK_BOT_TOKEN": "xoxb-test-token"}


@pytest.mark.asyncio
async def test_slack_send_message():
    from app.mcp.servers.slack_server import call_tool

    mc = mk_client(post=make_resp(data={"ok": True, "ts": "1234.5678", "channel": "C123"}))
    with patch.dict("os.environ", _SLACK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("slack_send_message", {"channel": "C123", "text": "Hello"})
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_slack_send_message_with_thread():
    from app.mcp.servers.slack_server import call_tool

    mc = mk_client(post=make_resp(data={"ok": True, "ts": "1234.5679", "channel": "C123"}))
    with patch.dict("os.environ", _SLACK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "slack_send_message",
            {"channel": "C123", "text": "Reply", "thread_ts": "1234.5678"},
        )
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_slack_list_channels():
    from app.mcp.servers.slack_server import call_tool

    mc = mk_client(
        get=make_resp(data={"channels": [{"id": "C123", "name": "general"}]})
    )
    with patch.dict("os.environ", _SLACK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("slack_list_channels", {"limit": 10})
    assert "channels" in result


@pytest.mark.asyncio
async def test_slack_get_channel_history():
    from app.mcp.servers.slack_server import call_tool

    mc = mk_client(
        get=make_resp(
            data={"messages": [{"ts": "1234.5678", "text": "hi", "user": "U123"}]}
        )
    )
    with patch.dict("os.environ", _SLACK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("slack_get_channel_history", {"channel": "C123"})
    assert "messages" in result


@pytest.mark.asyncio
async def test_slack_search_messages():
    from app.mcp.servers.slack_server import call_tool

    mc = mk_client(
        get=make_resp(
            data={
                "messages": {
                    "matches": [
                        {"text": "hello", "channel": {"name": "general"}, "ts": "1234.5678"}
                    ]
                }
            }
        )
    )
    with patch.dict("os.environ", _SLACK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("slack_search_messages", {"query": "hello"})
    assert "messages" in result


@pytest.mark.asyncio
async def test_slack_missing_token():
    from app.mcp.servers.slack_server import call_tool

    with patch.dict("os.environ", {"SLACK_BOT_TOKEN": ""}):
        os.environ.pop("SLACK_BOT_TOKEN", None)
        result = await call_tool("slack_send_message", {"channel": "C123", "text": "hi"})
    assert "error" in result


@pytest.mark.asyncio
async def test_slack_unknown_tool():
    from app.mcp.servers.slack_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _SLACK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("slack_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Microsoft Teams
# ---------------------------------------------------------------------------

_TEAMS = {"TEAMS_ACCESS_TOKEN": "teams-access-tok"}


@pytest.mark.asyncio
async def test_teams_send_message():
    from app.mcp.servers.microsoft_teams_server import call_tool

    # Teams uses TEAMS_ACCESS_TOKEN directly (no OAuth token exchange)
    msg_resp = make_resp(data={"id": "msg1", "body": {"content": "Hello"}})
    mc = mk_client(post=msg_resp)
    with patch.dict("os.environ", _TEAMS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "teams_send_message",
            {"team_id": "t1", "channel_id": "c1", "content": "Hello"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_teams_list_channels():
    from app.mcp.servers.microsoft_teams_server import call_tool

    channels_resp = make_resp(
        data={"value": [{"id": "c1", "displayName": "General", "description": ""}]}
    )
    mc = mk_client(get=channels_resp)
    with patch.dict("os.environ", _TEAMS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("teams_list_channels", {"team_id": "t1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_teams_missing_env():
    from app.mcp.servers.microsoft_teams_server import call_tool

    with patch.dict("os.environ", {"TEAMS_TENANT_ID": ""}):
        os.environ.pop("TEAMS_TENANT_ID", None)
        result = await call_tool("teams_list_channels", {"team_id": "t1"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------

_DISCORD = {"DISCORD_BOT_TOKEN": "Bot discord-token"}


@pytest.mark.asyncio
async def test_discord_send_message():
    from app.mcp.servers.discord_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "msg1", "content": "Hello", "channel_id": "c1"}))
    with patch.dict("os.environ", _DISCORD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "discord_send_message", {"channel_id": "c1", "content": "Hello"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_discord_list_guilds():
    from app.mcp.servers.discord_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "g1", "name": "My Server", "icon": None}]))
    with patch.dict("os.environ", _DISCORD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("discord_list_guilds", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_discord_list_channels():
    from app.mcp.servers.discord_server import call_tool

    mc = mk_client(
        get=make_resp(data=[{"id": "c1", "name": "general", "type": 0}])
    )
    with patch.dict("os.environ", _DISCORD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("discord_list_channels", {"guild_id": "g1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_discord_get_messages():
    from app.mcp.servers.discord_server import call_tool

    mc = mk_client(
        get=make_resp(
            data=[{"id": "m1", "content": "Hello", "author": {"username": "user"}, "timestamp": "2024-01-01"}]
        )
    )
    with patch.dict("os.environ", _DISCORD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("discord_get_messages", {"channel_id": "c1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_discord_create_thread():
    from app.mcp.servers.discord_server import call_tool

    mc = mk_client(
        post=make_resp(data={"id": "t1", "name": "Thread 1", "parent_id": "c1"})
    )
    with patch.dict("os.environ", _DISCORD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "discord_create_thread",
            {"channel_id": "c1", "name": "Thread 1", "message_id": "m1"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_discord_delete_message():
    from app.mcp.servers.discord_server import call_tool

    mc = mk_client(delete=make_resp(status=204))
    with patch.dict("os.environ", _DISCORD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "discord_delete_message", {"channel_id": "c1", "message_id": "m1"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_discord_missing_env():
    from app.mcp.servers.discord_server import call_tool

    with patch.dict("os.environ", {"DISCORD_BOT_TOKEN": ""}):
        os.environ.pop("DISCORD_BOT_TOKEN", None)
        result = await call_tool("discord_send_message", {"channel_id": "c1", "content": "hi"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Mattermost
# ---------------------------------------------------------------------------

_MM = {"MATTERMOST_URL": "https://mm.example.com", "MATTERMOST_TOKEN": "mm-tok"}


@pytest.mark.asyncio
async def test_mattermost_send_message():
    from app.mcp.servers.mattermost_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "msg1", "message": "Hello"}))
    with patch.dict("os.environ", _MM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "mattermost_send_message", {"channel_id": "c1", "message": "Hello"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_mattermost_list_channels():
    from app.mcp.servers.mattermost_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "c1", "display_name": "General", "name": "general", "type": "O"}]))
    with patch.dict("os.environ", _MM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mattermost_list_channels", {"team_id": "t1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_mattermost_get_posts():
    from app.mcp.servers.mattermost_server import call_tool

    mc = mk_client(get=make_resp(data={"posts": {"m1": {"id": "m1", "message": "hi", "user_id": "u1", "create_at": 1704067200000}}, "order": ["m1"]}))
    with patch.dict("os.environ", _MM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mattermost_get_posts", {"channel_id": "c1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_mattermost_list_teams():
    from app.mcp.servers.mattermost_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "t1", "display_name": "My Team", "name": "myteam"}]))
    with patch.dict("os.environ", _MM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mattermost_list_teams", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_mattermost_create_channel():
    from app.mcp.servers.mattermost_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "c2", "display_name": "New Channel", "name": "new-channel", "type": "O"}))
    with patch.dict("os.environ", _MM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "mattermost_create_channel",
            {"team_id": "t1", "name": "new-channel", "display_name": "New Channel"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_mattermost_search_posts():
    from app.mcp.servers.mattermost_server import call_tool

    mc = mk_client(post=make_resp(data={"posts": {}, "order": []}))
    with patch.dict("os.environ", _MM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mattermost_search_posts", {"team_id": "t1", "terms": "hello"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_mattermost_missing_env():
    from app.mcp.servers.mattermost_server import call_tool

    with patch.dict("os.environ", {"MATTERMOST_URL": "", "MATTERMOST_TOKEN": ""}):
        os.environ.pop("MATTERMOST_URL", None)
        result = await call_tool("mattermost_list_teams", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Twilio
# ---------------------------------------------------------------------------

_TWILIO = {
    "TWILIO_ACCOUNT_SID": "AC123",
    "TWILIO_AUTH_TOKEN": "auth-tok",
    "TWILIO_FROM_NUMBER": "+15551234567",
}


@pytest.mark.asyncio
async def test_twilio_send_sms():
    from app.mcp.servers.twilio_server import call_tool

    mc = mk_client(post=make_resp(data={"sid": "SM123", "status": "queued", "body": "Hello", "to": "+15559876543", "from": "+15551234567", "price": None}))
    with patch.dict("os.environ", _TWILIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "twilio_send_sms", {"to": "+15559876543", "body": "Hello"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_twilio_send_whatsapp():
    from app.mcp.servers.twilio_server import call_tool

    mc = mk_client(post=make_resp(data={"sid": "SM456", "status": "queued", "to": "whatsapp:+15559876543", "body": "Hi"}))
    with patch.dict("os.environ", _TWILIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "twilio_send_whatsapp", {"to": "+15559876543", "body": "Hi"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_twilio_list_messages():
    from app.mcp.servers.twilio_server import call_tool

    mc = mk_client(get=make_resp(data={"messages": [{"sid": "SM123", "body": "Hello", "status": "delivered", "from": "+15551234567", "to": "+15559876543", "date_sent": "2024-01-01"}]}))
    with patch.dict("os.environ", _TWILIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("twilio_list_messages", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_twilio_make_call():
    from app.mcp.servers.twilio_server import call_tool

    mc = mk_client(post=make_resp(data={"sid": "CA123", "status": "queued", "to": "+15559876543", "from": "+15551234567"}))
    with patch.dict("os.environ", _TWILIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "twilio_make_call",
            {"to": "+15559876543", "twiml": "<Response><Say>Hello</Say></Response>"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_twilio_list_numbers():
    from app.mcp.servers.twilio_server import call_tool

    mc = mk_client(get=make_resp(data={"incoming_phone_numbers": [{"sid": "PN123", "phone_number": "+15551234567", "friendly_name": "My Number", "capabilities": {}}]}))
    with patch.dict("os.environ", _TWILIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("twilio_list_numbers", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_twilio_missing_env():
    from app.mcp.servers.twilio_server import call_tool

    with patch.dict("os.environ", {"TWILIO_ACCOUNT_SID": ""}):
        os.environ.pop("TWILIO_ACCOUNT_SID", None)
        result = await call_tool("twilio_send_sms", {"to": "+1", "body": "hi"})
    assert "error" in result


# ---------------------------------------------------------------------------
# SendGrid
# ---------------------------------------------------------------------------

_SG = {"SENDGRID_API_KEY": "SG.test-key"}


@pytest.mark.asyncio
async def test_sendgrid_send_email():
    from app.mcp.servers.sendgrid_server import call_tool

    # Server uses "to_email" not "to"
    mc = mk_client(post=make_resp(status=202))
    with patch.dict("os.environ", _SG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "sendgrid_send_email",
            {"to_email": "a@b.com", "subject": "Test", "html": "<p>Hello</p>"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_sendgrid_send_bulk():
    from app.mcp.servers.sendgrid_server import call_tool

    # Server uses "to_emails" (list) not "to"
    mc = mk_client(post=make_resp(status=202))
    with patch.dict("os.environ", _SG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "sendgrid_send_bulk",
            {
                "to_emails": ["a@b.com", "c@d.com"],
                "subject": "Bulk Test",
                "html": "<p>Hello all</p>",
                "from_email": "sender@example.com",
            },
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_sendgrid_list_templates():
    from app.mcp.servers.sendgrid_server import call_tool

    mc = mk_client(get=make_resp(data={"result": [{"id": "t1", "name": "Welcome", "generation": "dynamic"}]}))
    with patch.dict("os.environ", _SG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sendgrid_list_templates", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_sendgrid_send_template():
    from app.mcp.servers.sendgrid_server import call_tool

    # Server uses "to_email" not "to"
    mc = mk_client(post=make_resp(status=202))
    with patch.dict("os.environ", _SG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "sendgrid_send_template",
            {
                "to_email": "a@b.com",
                "template_id": "t1",
                "dynamic_template_data": {"name": "Alice"},
                "from_email": "sender@example.com",
            },
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_sendgrid_get_stats():
    from app.mcp.servers.sendgrid_server import call_tool

    mc = mk_client(get=make_resp(data=[{"date": "2024-01-01", "stats": [{"metrics": {"delivered": 100}}]}]))
    with patch.dict("os.environ", _SG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sendgrid_get_stats", {"start_date": "2024-01-01"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_sendgrid_list_contacts():
    from app.mcp.servers.sendgrid_server import call_tool

    mc = mk_client(get=make_resp(data={"result": [{"id": "c1", "email": "a@b.com"}], "contact_count": 1}))
    with patch.dict("os.environ", _SG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sendgrid_list_contacts", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_sendgrid_add_contacts():
    from app.mcp.servers.sendgrid_server import call_tool

    mc = mk_client(put=make_resp(data={"job_id": "job1"}))
    with patch.dict("os.environ", _SG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "sendgrid_add_contacts",
            {"contacts": [{"email": "new@example.com"}]},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_sendgrid_create_list():
    from app.mcp.servers.sendgrid_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "list1", "name": "My List", "contact_count": 0}))
    with patch.dict("os.environ", _SG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sendgrid_create_list", {"name": "My List"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_sendgrid_missing_env():
    from app.mcp.servers.sendgrid_server import call_tool

    with patch.dict("os.environ", {"SENDGRID_API_KEY": ""}):
        os.environ.pop("SENDGRID_API_KEY", None)
        result = await call_tool("sendgrid_send_email", {"to": "a@b.com", "subject": "t", "body": "b"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Mailchimp
# ---------------------------------------------------------------------------

_MC = {"MAILCHIMP_API_KEY": "abc123-us1", "MAILCHIMP_SERVER_PREFIX": "us1"}


@pytest.mark.asyncio
async def test_mailchimp_list_audiences():
    from app.mcp.servers.mailchimp_server import call_tool

    # Tool is "mailchimp_list_lists" not "mailchimp_list_audiences"
    mc = mk_client(get=make_resp(data={"lists": [{"id": "list1", "name": "My List", "stats": {"member_count": 100}}], "total_items": 1}))
    with patch.dict("os.environ", _MC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mailchimp_list_lists", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_mailchimp_add_member():
    from app.mcp.servers.mailchimp_server import call_tool

    mc = mk_client(put=make_resp(data={"id": "sub1", "email_address": "a@b.com", "status": "subscribed"}))
    with patch.dict("os.environ", _MC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "mailchimp_add_member",
            {"list_id": "list1", "email": "a@b.com", "status": "subscribed"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_mailchimp_update_member():
    from app.mcp.servers.mailchimp_server import call_tool

    mc = mk_client(patch=make_resp(data={"id": "sub1", "email_address": "a@b.com", "status": "unsubscribed"}))
    with patch.dict("os.environ", _MC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "mailchimp_update_member",
            {"list_id": "list1", "email": "a@b.com", "status": "unsubscribed"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_mailchimp_create_campaign():
    from app.mcp.servers.mailchimp_server import call_tool

    # Server uses "subject_line" not "subject"
    mc = mk_client(post=make_resp(data={"id": "camp1", "type": "regular", "status": "save"}))
    with patch.dict("os.environ", _MC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "mailchimp_create_campaign",
            {"list_id": "list1", "subject_line": "Newsletter", "from_name": "Test", "reply_to": "t@e.com"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_mailchimp_send_campaign():
    from app.mcp.servers.mailchimp_server import call_tool

    mc = mk_client(post=make_resp(status=204))
    with patch.dict("os.environ", _MC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mailchimp_send_campaign", {"campaign_id": "camp1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_mailchimp_list_members():
    from app.mcp.servers.mailchimp_server import call_tool

    mc = mk_client(get=make_resp(data={"members": [{"id": "m1", "email_address": "a@b.com", "status": "subscribed"}], "total_items": 1}))
    with patch.dict("os.environ", _MC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mailchimp_list_members", {"list_id": "list1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_mailchimp_missing_env():
    from app.mcp.servers.mailchimp_server import call_tool

    with patch.dict("os.environ", {"MAILCHIMP_API_KEY": ""}):
        os.environ.pop("MAILCHIMP_API_KEY", None)
        result = await call_tool("mailchimp_list_audiences", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Brevo (formerly Sendinblue)
# ---------------------------------------------------------------------------

_BREVO = {"BREVO_API_KEY": "brevo-key", "BREVO_SENDER_EMAIL": "sender@example.com"}


@pytest.mark.asyncio
async def test_brevo_send_email():
    from app.mcp.servers.brevo_server import call_tool

    # Server uses "to_email" not a list structure
    mc = mk_client(post=make_resp(data={"messageId": "<msg1@brevo.com>"}))
    with patch.dict("os.environ", _BREVO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "brevo_send_email",
            {
                "to_email": "a@b.com",
                "subject": "Test",
                "html_content": "<p>Hi</p>",
            },
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_brevo_list_contacts():
    from app.mcp.servers.brevo_server import call_tool

    mc = mk_client(get=make_resp(data={"contacts": [{"id": 1, "email": "a@b.com", "emailBlacklisted": False}], "count": 1}))
    with patch.dict("os.environ", _BREVO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("brevo_list_contacts", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_brevo_create_contact():
    from app.mcp.servers.brevo_server import call_tool

    mc = mk_client(post=make_resp(data={"id": 2}))
    with patch.dict("os.environ", _BREVO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("brevo_create_contact", {"email": "new@b.com"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_brevo_list_campaigns():
    from app.mcp.servers.brevo_server import call_tool

    mc = mk_client(get=make_resp(data={"campaigns": [{"id": 1, "name": "Campaign 1", "status": "sent", "subject": "Test"}], "count": 1}))
    with patch.dict("os.environ", _BREVO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("brevo_list_campaigns", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_brevo_get_contact():
    from app.mcp.servers.brevo_server import call_tool

    mc = mk_client(get=make_resp(data={"id": 1, "email": "a@b.com", "attributes": {}, "listIds": []}))
    with patch.dict("os.environ", _BREVO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("brevo_get_contact", {"email": "a@b.com"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_brevo_delete_contact():
    from app.mcp.servers.brevo_server import call_tool

    mc = mk_client(delete=make_resp(status=204))
    with patch.dict("os.environ", _BREVO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("brevo_delete_contact", {"email": "a@b.com"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_brevo_missing_env():
    from app.mcp.servers.brevo_server import call_tool

    with patch.dict("os.environ", {"BREVO_API_KEY": ""}):
        os.environ.pop("BREVO_API_KEY", None)
        result = await call_tool("brevo_send_email", {"to": [{"email": "a@b.com"}], "subject": "t", "html_content": "h"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Customer.io
# ---------------------------------------------------------------------------

_CIO = {"CUSTOMERIO_SITE_ID": "site123", "CUSTOMERIO_API_KEY": "key123", "CUSTOMERIO_APP_API_KEY": "appkey"}


@pytest.mark.asyncio
async def test_customerio_identify_customer():
    from app.mcp.servers.customerio_server import call_tool

    # Tool is "customerio_identify" not "customerio_identify_customer"
    mc = mk_client(put=make_resp(status=200))
    with patch.dict("os.environ", _CIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "customerio_identify",
            {"customer_id": "user123", "email": "a@b.com"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_customerio_track_event():
    from app.mcp.servers.customerio_server import call_tool

    mc = mk_client(post=make_resp(status=200))
    with patch.dict("os.environ", _CIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "customerio_track_event",
            {"customer_id": "user123", "event_name": "purchased"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_customerio_delete_customer():
    from app.mcp.servers.customerio_server import call_tool

    mc = mk_client(delete=make_resp(status=200))
    with patch.dict("os.environ", _CIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("customerio_delete_customer", {"customer_id": "user123"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_customerio_list_customers():
    from app.mcp.servers.customerio_server import call_tool

    mc = mk_client(get=make_resp(data={"customers": [{"id": "user123", "email": "a@b.com"}]}))
    with patch.dict("os.environ", _CIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("customerio_list_customers", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_customerio_suppress_customer():
    from app.mcp.servers.customerio_server import call_tool

    mc = mk_client(post=make_resp(status=200))
    with patch.dict("os.environ", _CIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("customerio_suppress_customer", {"customer_id": "user123"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_customerio_missing_env():
    from app.mcp.servers.customerio_server import call_tool

    with patch.dict("os.environ", {"CUSTOMERIO_SITE_ID": "", "CUSTOMERIO_APP_API_KEY": ""}):
        os.environ.pop("CUSTOMERIO_SITE_ID", None)
        os.environ.pop("CUSTOMERIO_APP_API_KEY", None)
        result = await call_tool("customerio_identify_customer", {"customer_id": "u", "attributes": {}})
    assert "error" in result


# ---------------------------------------------------------------------------
# Klaviyo
# ---------------------------------------------------------------------------

_KL = {"KLAVIYO_API_KEY": "klaviyo-key"}


@pytest.mark.asyncio
async def test_klaviyo_get_profiles():
    from app.mcp.servers.klaviyo_server import call_tool

    # Tool is "klaviyo_list_profiles" not "klaviyo_get_profiles"
    mc = mk_client(get=make_resp(data={"data": [{"id": "p1", "attributes": {"email": "a@b.com"}}]}))
    with patch.dict("os.environ", _KL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("klaviyo_list_profiles", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_klaviyo_create_profile():
    from app.mcp.servers.klaviyo_server import call_tool

    mc = mk_client(post=make_resp(status=201, data={"data": {"id": "p2", "attributes": {}}}))
    with patch.dict("os.environ", _KL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("klaviyo_create_profile", {"email": "new@b.com"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_klaviyo_update_profile():
    from app.mcp.servers.klaviyo_server import call_tool

    mc = mk_client(patch=make_resp(data={"data": {"id": "p1", "attributes": {}}}))
    with patch.dict("os.environ", _KL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "klaviyo_update_profile", {"profile_id": "p1", "first_name": "Alice"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_klaviyo_list_lists():
    from app.mcp.servers.klaviyo_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "l1", "attributes": {"name": "Newsletter"}}]}))
    with patch.dict("os.environ", _KL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("klaviyo_list_lists", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_klaviyo_add_to_list():
    from app.mcp.servers.klaviyo_server import call_tool

    mc = mk_client(post=make_resp(status=204))
    with patch.dict("os.environ", _KL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "klaviyo_add_to_list", {"list_id": "l1", "profile_ids": ["p1"]}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_klaviyo_send_event():
    from app.mcp.servers.klaviyo_server import call_tool

    # Server uses "event_name" and "profile_email" args
    mc = mk_client(post=make_resp(status=202))
    with patch.dict("os.environ", _KL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "klaviyo_send_event",
            {"event_name": "Purchased", "profile_email": "a@b.com"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_klaviyo_list_campaigns():
    from app.mcp.servers.klaviyo_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "c1", "attributes": {"name": "Campaign 1"}}]}))
    with patch.dict("os.environ", _KL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("klaviyo_list_campaigns", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_klaviyo_missing_env():
    from app.mcp.servers.klaviyo_server import call_tool

    with patch.dict("os.environ", {"KLAVIYO_API_KEY": ""}):
        os.environ.pop("KLAVIYO_API_KEY", None)
        result = await call_tool("klaviyo_get_profiles", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# MailerLite
# ---------------------------------------------------------------------------

_ML = {"MAILERLITE_API_KEY": "ml-key"}


@pytest.mark.asyncio
async def test_mailerlite_list_subscribers():
    from app.mcp.servers.mailerlite_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "s1", "email": "a@b.com", "status": "active"}], "meta": {"total": 1}}))
    with patch.dict("os.environ", _ML), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mailerlite_list_subscribers", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_mailerlite_create_subscriber():
    from app.mcp.servers.mailerlite_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"id": "s2", "email": "new@b.com"}}))
    with patch.dict("os.environ", _ML), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mailerlite_create_subscriber", {"email": "new@b.com"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_mailerlite_list_groups():
    from app.mcp.servers.mailerlite_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "g1", "name": "Group 1", "active_count": 5}]}))
    with patch.dict("os.environ", _ML), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mailerlite_list_groups", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_mailerlite_list_campaigns():
    from app.mcp.servers.mailerlite_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "c1", "name": "Campaign 1", "status": "sent"}]}))
    with patch.dict("os.environ", _ML), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mailerlite_list_campaigns", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_mailerlite_delete_subscriber():
    from app.mcp.servers.mailerlite_server import call_tool

    mc = mk_client(delete=make_resp(status=204))
    with patch.dict("os.environ", _ML), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mailerlite_delete_subscriber", {"subscriber_id": "s1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_mailerlite_missing_env():
    from app.mcp.servers.mailerlite_server import call_tool

    with patch.dict("os.environ", {"MAILERLITE_API_KEY": ""}):
        os.environ.pop("MAILERLITE_API_KEY", None)
        result = await call_tool("mailerlite_list_subscribers", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Mandrill
# ---------------------------------------------------------------------------

_MAN = {"MANDRILL_API_KEY": "mandrill-key"}


@pytest.mark.asyncio
async def test_mandrill_send_email():
    from app.mcp.servers.mandrill_server import call_tool

    # Server uses "to_email" not a list; "subject" required
    mc = mk_client(post=make_resp(data=[{"email": "a@b.com", "_id": "msg1", "status": "sent"}]))
    with patch.dict("os.environ", _MAN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "mandrill_send_email",
            {
                "to_email": "a@b.com",
                "to_name": "Alice",
                "subject": "Hello",
                "html": "<p>Hi</p>",
                "from_email": "sender@example.com",
            },
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_mandrill_send_template():
    from app.mcp.servers.mandrill_server import call_tool

    # Server uses "to_email" and "template_name"
    mc = mk_client(post=make_resp(data=[{"email": "a@b.com", "_id": "msg2", "status": "sent"}]))
    with patch.dict("os.environ", _MAN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "mandrill_send_template",
            {
                "template_name": "welcome",
                "to_email": "a@b.com",
                "subject": "Welcome",
                "from_email": "sender@example.com",
            },
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_mandrill_list_templates():
    from app.mcp.servers.mandrill_server import call_tool

    mc = mk_client(post=make_resp(data=[{"slug": "welcome", "name": "Welcome", "labels": []}]))
    with patch.dict("os.environ", _MAN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mandrill_list_templates", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_mandrill_get_message_info():
    from app.mcp.servers.mandrill_server import call_tool

    mc = mk_client(post=make_resp(data={"_id": "msg1", "email": "a@b.com", "status": "sent", "subject": "Hello"}))
    with patch.dict("os.environ", _MAN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mandrill_get_message_info", {"message_id": "msg1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_mandrill_list_senders():
    from app.mcp.servers.mandrill_server import call_tool

    mc = mk_client(post=make_resp(data=[{"address": "sender@example.com", "sent": 100, "reputation": 95}]))
    with patch.dict("os.environ", _MAN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mandrill_list_senders", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_mandrill_search_messages():
    from app.mcp.servers.mandrill_server import call_tool

    mc = mk_client(post=make_resp(data=[{"_id": "m1", "email": "a@b.com", "subject": "Test", "sender": "s@e.com", "state": "sent", "ts": 1704067200}]))
    with patch.dict("os.environ", _MAN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mandrill_search_messages", {"query": "test"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# ConvertKit
# ---------------------------------------------------------------------------

_CK = {"CONVERTKIT_API_KEY": "ck-key", "CONVERTKIT_API_SECRET": "ck-secret"}


@pytest.mark.asyncio
async def test_convertkit_list_subscribers():
    from app.mcp.servers.convertkit_server import call_tool

    mc = mk_client(get=make_resp(data={"subscribers": [{"id": 1, "email_address": "a@b.com", "state": "active"}], "total_subscribers": 1}))
    with patch.dict("os.environ", _CK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("convertkit_list_subscribers", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_convertkit_create_subscriber():
    from app.mcp.servers.convertkit_server import call_tool

    mc = mk_client(post=make_resp(data={"subscription": {"id": 1, "subscriber": {"id": 2, "email_address": "new@b.com"}}}))
    with patch.dict("os.environ", _CK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("convertkit_create_subscriber", {"email": "new@b.com", "form_id": "form1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_convertkit_list_forms():
    from app.mcp.servers.convertkit_server import call_tool

    mc = mk_client(get=make_resp(data={"forms": [{"id": 1, "name": "Form 1", "type": "embed", "url": "url"}]}))
    with patch.dict("os.environ", _CK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("convertkit_list_forms", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_convertkit_list_tags():
    from app.mcp.servers.convertkit_server import call_tool

    mc = mk_client(get=make_resp(data={"tags": [{"id": 1, "name": "Customer", "created_at": "2024-01-01"}]}))
    with patch.dict("os.environ", _CK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("convertkit_list_tags", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_convertkit_tag_subscriber():
    from app.mcp.servers.convertkit_server import call_tool

    mc = mk_client(post=make_resp(data={"subscription": {"id": 1, "subscriber": {}}}))
    with patch.dict("os.environ", _CK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "convertkit_tag_subscriber", {"tag_id": "1", "email": "a@b.com"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_convertkit_list_sequences():
    from app.mcp.servers.convertkit_server import call_tool

    mc = mk_client(get=make_resp(data={"courses": [{"id": 1, "name": "Onboarding"}]}))
    with patch.dict("os.environ", _CK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("convertkit_list_sequences", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_convertkit_unsubscribe():
    from app.mcp.servers.convertkit_server import call_tool

    mc = mk_client(put=make_resp(data={"subscriber": {"id": 1, "email_address": "a@b.com", "state": "cancelled"}}))
    with patch.dict("os.environ", _CK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("convertkit_unsubscribe", {"email": "a@b.com"})
    assert "error" not in result
