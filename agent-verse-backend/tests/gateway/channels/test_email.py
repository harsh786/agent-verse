"""Tests for app/gateway/channels/email.py — EmailChannelAdapter.

Covers:
  - verify_auth (whitelist / open)
  - normalize (subject/body combos, reply threading, sender parsing)
  - format_response (html body, actions)
  - helper regex parsers
"""
from __future__ import annotations

import pytest

from app.gateway.channels.email import EmailChannelAdapter
from app.gateway.command import OrgResponse, ResponseAction


# ── verify_auth ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_auth_no_whitelist_allows_any_sender():
    adapter = EmailChannelAdapter()
    ok = await adapter.verify_auth({}, {"from": "Someone <someone@example.com>"})
    assert ok is True


@pytest.mark.asyncio
async def test_verify_auth_whitelist_allows_matching_sender_case_insensitive():
    adapter = EmailChannelAdapter(allowed_senders=["Boss@Example.com"])
    ok = await adapter.verify_auth({}, {"from": "Boss <boss@example.com>"})
    assert ok is True


@pytest.mark.asyncio
async def test_verify_auth_whitelist_rejects_unknown_sender():
    adapter = EmailChannelAdapter(allowed_senders=["boss@example.com"])
    ok = await adapter.verify_auth({}, {"from": "Stranger <stranger@example.com>"})
    assert ok is False


@pytest.mark.asyncio
async def test_verify_auth_whitelist_with_plain_email_no_angle_brackets():
    adapter = EmailChannelAdapter(allowed_senders=["boss@example.com"])
    ok = await adapter.verify_auth({}, {"from": "boss@example.com"})
    assert ok is True


# ── normalize ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_normalize_subject_only():
    adapter = EmailChannelAdapter()
    cmd = await adapter.normalize(
        {"from": "Alice <alice@example.com>", "subject": "Do the thing"},
        tenant_id="t1",
        org_id="o1",
    )
    assert cmd.text == "Do the thing"
    assert cmd.actor_id == "alice@example.com"
    assert cmd.actor_name == "Alice"
    assert cmd.actor_channel == "email"
    assert cmd.tenant_id == "t1"
    assert cmd.org_id == "o1"
    assert cmd.conversation_id is None


@pytest.mark.asyncio
async def test_normalize_body_only_no_subject_truncated_to_500():
    adapter = EmailChannelAdapter()
    long_body = "x" * 600
    cmd = await adapter.normalize(
        {"from": "alice@example.com", "subject": "", "body": long_body},
        tenant_id="t1",
        org_id="o1",
    )
    assert cmd.text == long_body[:500]


@pytest.mark.asyncio
async def test_normalize_subject_and_body_combined():
    adapter = EmailChannelAdapter()
    cmd = await adapter.normalize(
        {
            "from": "alice@example.com",
            "subject": "Subject line",
            "text": "Body content here",
        },
        tenant_id="t1",
        org_id="o1",
    )
    assert cmd.text == "Subject line\n\nBody content here"


@pytest.mark.asyncio
async def test_normalize_uses_text_field_over_body_field():
    adapter = EmailChannelAdapter()
    cmd = await adapter.normalize(
        {
            "from": "alice@example.com",
            "subject": "S",
            "text": "from-text",
            "body": "from-body",
        },
        tenant_id="t1",
        org_id="o1",
    )
    assert "from-text" in cmd.text
    assert "from-body" not in cmd.text


@pytest.mark.asyncio
async def test_normalize_with_reply_sets_conversation_id():
    adapter = EmailChannelAdapter()
    cmd = await adapter.normalize(
        {
            "from": "alice@example.com",
            "subject": "Re: hi",
            "in_reply_to": "<msg-123@mail.example.com>",
        },
        tenant_id="t1",
        org_id="o1",
    )
    assert cmd.conversation_id == "msg-123@mail.example.com"


@pytest.mark.asyncio
async def test_normalize_raw_payload_preserved():
    adapter = EmailChannelAdapter()
    payload = {"from": "alice@example.com", "subject": "hi"}
    cmd = await adapter.normalize(payload, tenant_id="t1", org_id="o1")
    assert cmd.raw_payload is payload


@pytest.mark.asyncio
async def test_normalize_sender_without_name_has_none_actor_name():
    adapter = EmailChannelAdapter()
    cmd = await adapter.normalize(
        {"from": "alice@example.com", "subject": "hi"},
        tenant_id="t1",
        org_id="o1",
    )
    assert cmd.actor_name is None


# ── format_response ───────────────────────────────────────────────────────────

def test_format_response_basic():
    adapter = EmailChannelAdapter()
    resp = OrgResponse(command_id="c1", text="Hello\n\nWorld")
    formatted = adapter.format_response(resp)
    assert formatted["text_body"] == "Hello\n\nWorld"
    assert formatted["html_body"] == "<p>Hello</p><p>World</p>"
    assert formatted["references"] == "c1"
    assert formatted["subject"].startswith("Re: AgentVerse —")


def test_format_response_with_actions_appends_list():
    adapter = EmailChannelAdapter()
    resp = OrgResponse(
        command_id="c1",
        text="Please review",
        actions=[
            ResponseAction(action_id="a1", label="Approve <now>", action_type="approve"),
            ResponseAction(action_id="a2", label="Reject", action_type="reject"),
        ],
    )
    formatted = adapter.format_response(resp)
    assert "<strong>Actions:</strong>" in formatted["html_body"]
    assert "Approve &lt;now&gt;" in formatted["html_body"]
    assert "Reject" in formatted["html_body"]


def test_format_response_escapes_html_in_text():
    adapter = EmailChannelAdapter()
    resp = OrgResponse(command_id="c1", text="<script>alert(1)</script>")
    formatted = adapter.format_response(resp)
    assert "<script>" not in formatted["html_body"]
    assert "&lt;script&gt;" in formatted["html_body"]


def test_format_response_subject_truncates_long_text():
    adapter = EmailChannelAdapter()
    resp = OrgResponse(command_id="c1", text="x" * 100)
    formatted = adapter.format_response(resp)
    assert formatted["subject"] == f"Re: AgentVerse — {'x' * 60}…"


# ── helper parsers ────────────────────────────────────────────────────────────

def test_extract_email_with_angle_brackets():
    assert EmailChannelAdapter._extract_email("Alice <alice@example.com>") == "alice@example.com"


def test_extract_email_plain():
    assert EmailChannelAdapter._extract_email("  alice@example.com  ") == "alice@example.com"


def test_extract_name_present():
    assert EmailChannelAdapter._extract_name("Alice <alice@example.com>") == "Alice"


def test_extract_name_absent():
    assert EmailChannelAdapter._extract_name("alice@example.com") is None


def test_extract_message_id_present():
    assert EmailChannelAdapter._extract_message_id("<abc123@mail.com>") == "abc123@mail.com"


def test_extract_message_id_absent():
    assert EmailChannelAdapter._extract_message_id("no-brackets-here") is None


def test_text_to_html_single_newline_becomes_br():
    html_out = EmailChannelAdapter._text_to_html("line1\nline2")
    assert html_out == "<p>line1<br>line2</p>"


def test_channel_name_is_email():
    assert EmailChannelAdapter.channel_name == "email"
