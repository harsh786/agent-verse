"""Tests for app/gateway/channels/slack.py — SlackChannelAdapter.

Covers:
  - normalize (slash command, app_mention, message event, unknown/default)
  - format_response (blocks, action button styling, truncation)
  - post_message (no token, success, exception path)
  - verify_auth (no secret, valid signature, invalid signature)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway.channels.slack import SlackChannelAdapter
from app.gateway.command import OrgResponse, ResponseAction


# ── normalize ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_normalize_slash_command():
    adapter = SlackChannelAdapter()
    payload = {
        "command": "/org",
        "text": "status",
        "user_id": "U1",
        "user_name": "alice",
        "channel_id": "C1",
    }
    cmd = await adapter.normalize(payload, tenant_id="t1", org_id="o1")
    assert cmd.text == "/org status"
    assert cmd.actor_id == "U1"
    assert cmd.actor_name == "alice"
    assert cmd.conversation_id == "C1"
    assert cmd.actor_channel == "slack"


@pytest.mark.asyncio
async def test_normalize_app_mention_strips_bot_prefix():
    adapter = SlackChannelAdapter()
    payload = {
        "type": "event_callback",
        "event": {
            "type": "app_mention",
            "text": "<@U0BOT> what's the status?",
            "user": "U2",
            "channel": "C2",
        },
    }
    cmd = await adapter.normalize(payload, tenant_id="t1", org_id="o1")
    assert cmd.text == "what's the status?"
    assert cmd.actor_id == "U2"
    assert cmd.conversation_id == "C2"


@pytest.mark.asyncio
async def test_normalize_message_event_no_bot_prefix():
    adapter = SlackChannelAdapter()
    payload = {
        "type": "event_callback",
        "event": {"type": "message", "text": "hello", "user": "U3", "channel": "C3"},
    }
    cmd = await adapter.normalize(payload, tenant_id="t1", org_id="o1")
    assert cmd.text == "hello"


@pytest.mark.asyncio
async def test_normalize_event_callback_unrecognized_event_type_returns_default():
    adapter = SlackChannelAdapter()
    payload = {
        "type": "event_callback",
        "event": {"type": "reaction_added", "text": "n/a"},
    }
    cmd = await adapter.normalize(payload, tenant_id="t1", org_id="o1")
    assert cmd.text == ""
    assert cmd.actor_channel == "slack"


@pytest.mark.asyncio
async def test_normalize_unknown_payload_returns_default():
    adapter = SlackChannelAdapter()
    cmd = await adapter.normalize({}, tenant_id="t1", org_id="o1")
    assert cmd.text == ""
    assert cmd.actor_channel == "slack"


# ── format_response ───────────────────────────────────────────────────────────

def test_format_response_basic_block():
    adapter = SlackChannelAdapter()
    resp = OrgResponse(command_id="c1", text="hello world")
    out = adapter.format_response(resp)
    assert out["blocks"][0]["type"] == "section"
    assert out["blocks"][0]["text"]["text"] == "hello world"
    assert out["text"] == "hello world"


def test_format_response_truncates_section_and_fallback_text():
    adapter = SlackChannelAdapter()
    resp = OrgResponse(command_id="c1", text="x" * 5000)
    out = adapter.format_response(resp)
    assert len(out["blocks"][0]["text"]["text"]) == 3000
    assert len(out["text"]) == 150


def test_format_response_actions_style_primary_for_approve():
    adapter = SlackChannelAdapter()
    resp = OrgResponse(
        command_id="c1",
        text="approve?",
        actions=[
            ResponseAction(action_id="a1", label="Approve", action_type="approve"),
            ResponseAction(action_id="a2", label="View", action_type="view"),
        ],
    )
    out = adapter.format_response(resp)
    action_block = out["blocks"][1]
    assert action_block["type"] == "actions"
    styles = {el["action_id"]: el["style"] for el in action_block["elements"]}
    assert styles["a1"] == "primary"
    assert styles["a2"] == "default"


def test_format_response_caps_actions_at_five():
    adapter = SlackChannelAdapter()
    actions = [
        ResponseAction(action_id=f"a{i}", label=f"L{i}", action_type="view") for i in range(8)
    ]
    resp = OrgResponse(command_id="c1", text="many", actions=actions)
    out = adapter.format_response(resp)
    assert len(out["blocks"][1]["elements"]) == 5


# ── post_message ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_message_no_token_returns_none():
    adapter = SlackChannelAdapter(bot_token="")
    resp = OrgResponse(command_id="c1", text="hi")
    result = await adapter.post_message("C1", resp)
    assert result is None


@pytest.mark.asyncio
async def test_post_message_success_returns_json():
    adapter = SlackChannelAdapter(bot_token="xoxb-token")
    resp = OrgResponse(command_id="c1", text="hi")

    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": True}

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.gateway.channels.slack.httpx.AsyncClient", return_value=mock_client):
        result = await adapter.post_message("C1", resp)

    assert result == {"ok": True}
    mock_client.post.assert_awaited_once()
    call_kwargs = mock_client.post.call_args.kwargs
    assert call_kwargs["json"]["channel"] == "C1"
    assert call_kwargs["headers"]["Authorization"] == "Bearer xoxb-token"


@pytest.mark.asyncio
async def test_post_message_exception_returns_none():
    adapter = SlackChannelAdapter(bot_token="xoxb-token")
    resp = OrgResponse(command_id="c1", text="hi")

    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=RuntimeError("boom"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.gateway.channels.slack.httpx.AsyncClient", return_value=mock_client):
        result = await adapter.post_message("C1", resp)

    assert result is None


# ── verify_auth ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_auth_no_signing_secret_allows():
    adapter = SlackChannelAdapter(signing_secret="")
    ok = await adapter.verify_auth({}, {})
    assert ok is True


@pytest.mark.asyncio
async def test_verify_auth_valid_signature():
    """A signature computed the way Slack actually computes it — HMAC over the
    exact raw request bytes — must validate when those same raw bytes are
    passed through."""
    secret = "s3cr3t"
    adapter = SlackChannelAdapter(signing_secret=secret)
    payload = {"a": 1}
    raw_body = json.dumps(payload).encode()
    timestamp = str(int(time.time()))
    sig_base = b"v0:" + timestamp.encode() + b":" + raw_body
    expected = "v0=" + hmac.new(secret.encode(), sig_base, hashlib.sha256).hexdigest()
    ok = await adapter.verify_auth(
        {"x-slack-request-timestamp": timestamp, "x-slack-signature": expected},
        payload,
        raw_body=raw_body,
    )
    assert ok is True


@pytest.mark.asyncio
async def test_verify_auth_invalid_signature_rejects():
    adapter = SlackChannelAdapter(signing_secret="s3cr3t")
    timestamp = str(int(time.time()))
    ok = await adapter.verify_auth(
        {"x-slack-request-timestamp": timestamp, "x-slack-signature": "v0=bad"},
        {},
        raw_body=b"{}",
    )
    assert ok is False


@pytest.mark.asyncio
async def test_verify_auth_regression_real_slack_payload_with_special_chars():
    """Regression test for the raw-body-reconstruction bug.

    The old implementation computed the signature base from ``str(raw_payload)``
    (Python dict repr: single quotes, ``True``/``None``, etc.) instead of the
    actual raw bytes Slack signed. That made genuine Slack requests — whose
    JSON contains booleans, nested dicts, or apostrophes — permanently fail
    verification. This constructs a signature exactly the way Slack does (over
    the real raw JSON bytes) and asserts it is accepted.
    """
    secret = "s3cr3t"
    adapter = SlackChannelAdapter(signing_secret=secret)
    # A realistic event_callback payload: booleans + an apostrophe in the text,
    # both of which render completely differently under str(dict) than in JSON.
    payload = {
        "type": "event_callback",
        "event": {"type": "app_mention", "text": "what's the status?", "user": "U2"},
        "authed": True,
        "extra": None,
    }
    raw_body = json.dumps(payload).encode()
    timestamp = str(int(time.time()))
    sig_base = b"v0:" + timestamp.encode() + b":" + raw_body
    expected = "v0=" + hmac.new(secret.encode(), sig_base, hashlib.sha256).hexdigest()

    ok = await adapter.verify_auth(
        {"x-slack-request-timestamp": timestamp, "x-slack-signature": expected},
        payload,
        raw_body=raw_body,
    )
    assert ok is True


@pytest.mark.asyncio
async def test_verify_auth_missing_raw_body_fails_closed():
    """When a secret is configured but the caller didn't pass raw_body, we must
    fail closed (reject) rather than compare against a reconstruction that can
    never match a real Slack signature."""
    adapter = SlackChannelAdapter(signing_secret="s3cr3t")
    ok = await adapter.verify_auth(
        {"x-slack-request-timestamp": str(int(time.time())), "x-slack-signature": "v0=whatever"},
        {"a": 1},
    )
    assert ok is False


@pytest.mark.asyncio
async def test_verify_auth_stale_timestamp_rejects():
    adapter = SlackChannelAdapter(signing_secret="s3cr3t")
    raw_body = b'{"a":1}'
    stale_timestamp = str(int(time.time()) - 600)  # 10 minutes old
    sig_base = b"v0:" + stale_timestamp.encode() + b":" + raw_body
    computed = "v0=" + hmac.new(b"s3cr3t", sig_base, hashlib.sha256).hexdigest()
    ok = await adapter.verify_auth(
        {"x-slack-request-timestamp": stale_timestamp, "x-slack-signature": computed},
        {"a": 1},
        raw_body=raw_body,
    )
    assert ok is False


def test_channel_name_is_slack():
    assert SlackChannelAdapter.channel_name == "slack"
