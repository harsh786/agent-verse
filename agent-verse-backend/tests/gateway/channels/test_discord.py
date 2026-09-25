"""Tests for app/gateway/channels/discord.py — DiscordChannelAdapter.

Covers:
  - verify_auth (public key presence, signature/timestamp/body checks)
  - normalize for interaction types: ping, slash command, button, unknown
  - format_response (buttons, truncation)
"""
from __future__ import annotations

from typing import Any

import pytest

from app.gateway.channels.discord import DiscordChannelAdapter
from app.gateway.command import OrgResponse, ResponseAction


# ── verify_auth ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_auth_no_public_key_rejects():
    adapter = DiscordChannelAdapter(bot_token="tok", public_key="")
    ok = await adapter.verify_auth({}, {})
    assert ok is False


@pytest.mark.asyncio
async def test_verify_auth_with_all_fields_present_but_unsigned_rejects():
    """Presence of the header triple is NOT authentication.

    This test previously asserted ``ok is True`` for a literal ``"sig"`` against
    a literal ``"pubkey"`` — it was encoding the bypass bug rather than testing
    the documented contract ("Verify Discord Ed25519 signature"). Corrected to
    assert the signature is actually verified; see the real-keypair tests below.
    """
    adapter = DiscordChannelAdapter(bot_token="tok", public_key="pubkey")
    ok = await adapter.verify_auth(
        {"X-Signature-Ed25519": "sig", "X-Signature-Timestamp": "123"},
        {"_raw_body": "some-body"},
    )
    assert ok is False


@pytest.mark.asyncio
async def test_verify_auth_missing_signature_rejects():
    adapter = DiscordChannelAdapter(bot_token="tok", public_key="pubkey")
    ok = await adapter.verify_auth(
        {"X-Signature-Timestamp": "123"},
        {"_raw_body": "some-body"},
    )
    assert ok is False


@pytest.mark.asyncio
async def test_verify_auth_missing_raw_body_rejects():
    adapter = DiscordChannelAdapter(bot_token="tok", public_key="pubkey")
    ok = await adapter.verify_auth(
        {"X-Signature-Ed25519": "sig", "X-Signature-Timestamp": "123"},
        {},
    )
    assert ok is False


# ── normalize: ping ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_normalize_ping_interaction():
    adapter = DiscordChannelAdapter()
    cmd = await adapter.normalize({"type": 1}, tenant_id="t1", org_id="o1")
    assert cmd.text == "_ping"
    assert cmd.actor_id == "discord_ping"
    assert cmd.actor_channel == "discord"


# ── normalize: slash commands ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_normalize_slash_command_org_status():
    adapter = DiscordChannelAdapter()
    payload = {
        "type": 2,
        "member": {"user": {"id": "u1", "username": "alice"}},
        "data": {"name": "org-status", "options": []},
        "channel_id": "chan1",
    }
    cmd = await adapter.normalize(payload, tenant_id="t1", org_id="o1")
    assert cmd.text == "What is the org status?"
    assert cmd.actor_id == "u1"
    assert cmd.actor_name == "alice"
    assert cmd.conversation_id == "chan1"


@pytest.mark.asyncio
async def test_normalize_slash_command_org_ask_uses_text_option():
    adapter = DiscordChannelAdapter()
    payload = {
        "type": 2,
        "user": {"id": "u2", "username": "bob"},
        "data": {"name": "org-ask", "options": [{"name": "text", "value": "What's up?"}]},
    }
    cmd = await adapter.normalize(payload, tenant_id="t1", org_id="o1")
    assert cmd.text == "What's up?"
    assert cmd.actor_id == "u2"


@pytest.mark.asyncio
async def test_normalize_slash_command_org_mission_builds_prefix():
    adapter = DiscordChannelAdapter()
    payload = {
        "type": 2,
        "member": {"user": {"id": "u3", "username": "carol"}},
        "data": {"name": "org-mission", "options": [{"name": "query", "value": "Launch campaign"}]},
    }
    cmd = await adapter.normalize(payload, tenant_id="t1", org_id="o1")
    assert cmd.text == "Create a mission: Launch campaign"


@pytest.mark.asyncio
async def test_normalize_slash_command_org_approve():
    adapter = DiscordChannelAdapter()
    payload = {"type": 2, "member": {"user": {"id": "u4"}}, "data": {"name": "org-approve"}}
    cmd = await adapter.normalize(payload, tenant_id="t1", org_id="o1")
    assert cmd.text == "List pending approvals"


@pytest.mark.asyncio
async def test_normalize_slash_command_org_brief():
    adapter = DiscordChannelAdapter()
    payload = {"type": 2, "member": {"user": {"id": "u5"}}, "data": {"name": "org-brief"}}
    cmd = await adapter.normalize(payload, tenant_id="t1", org_id="o1")
    assert cmd.text == "Morning brief"


@pytest.mark.asyncio
async def test_normalize_slash_command_unknown_falls_back_to_text_or_name():
    adapter = DiscordChannelAdapter()
    payload = {"type": 2, "member": {"user": {"id": "u6"}}, "data": {"name": "org-unknown"}}
    cmd = await adapter.normalize(payload, tenant_id="t1", org_id="o1")
    assert cmd.text == "org-unknown"


@pytest.mark.asyncio
async def test_normalize_slash_command_no_member_uses_top_level_user():
    adapter = DiscordChannelAdapter()
    payload = {
        "type": 2,
        "user": {"id": "u7", "username": "dave"},
        "data": {"name": "org-status"},
    }
    cmd = await adapter.normalize(payload, tenant_id="t1", org_id="o1")
    assert cmd.actor_id == "u7"
    assert cmd.actor_name == "dave"


# ── normalize: button press ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_normalize_button_press():
    adapter = DiscordChannelAdapter()
    payload = {
        "type": 3,
        "member": {"user": {"id": "u8"}},
        "data": {"custom_id": "approve:123"},
    }
    cmd = await adapter.normalize(payload, tenant_id="t1", org_id="o1")
    assert cmd.text == "action:approve:123"
    assert cmd.actor_id == "u8"


# ── normalize: unknown type ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_normalize_unknown_interaction_type():
    adapter = DiscordChannelAdapter()
    cmd = await adapter.normalize({"type": 99}, tenant_id="t1", org_id="o1")
    assert cmd.text == ""
    assert cmd.actor_id == "unknown"


# ── format_response ───────────────────────────────────────────────────────────

def test_format_response_basic_no_actions():
    adapter = DiscordChannelAdapter()
    resp = OrgResponse(command_id="c1", text="Hello there")
    out = adapter.format_response(resp)
    assert out["type"] == 4
    assert out["data"]["content"] == "Hello there"
    assert out["data"]["components"] == []


def test_format_response_with_actions_builds_buttons():
    adapter = DiscordChannelAdapter()
    resp = OrgResponse(
        command_id="c1",
        text="Choose",
        actions=[
            ResponseAction(action_id="a1", label="Approve", action_type="approve"),
            ResponseAction(action_id="a2", label="Reject", action_type="reject"),
        ],
    )
    out = adapter.format_response(resp)
    row = out["data"]["components"][0]
    assert row["type"] == 1
    assert len(row["components"]) == 2
    assert row["components"][0]["label"] == "Approve"
    assert row["components"][0]["custom_id"] == "a1"


def test_format_response_caps_at_five_buttons():
    adapter = DiscordChannelAdapter()
    actions = [
        ResponseAction(action_id=f"a{i}", label=f"L{i}", action_type="view") for i in range(8)
    ]
    resp = OrgResponse(command_id="c1", text="many", actions=actions)
    out = adapter.format_response(resp)
    assert len(out["data"]["components"][0]["components"]) == 5


def test_format_response_truncates_content_to_1990_chars():
    adapter = DiscordChannelAdapter()
    resp = OrgResponse(command_id="c1", text="x" * 3000)
    out = adapter.format_response(resp)
    assert len(out["data"]["content"]) == 1990


def test_channel_name_is_discord():
    assert DiscordChannelAdapter.channel_name == "discord"


# ── verify_auth: real Ed25519 (regression) ───────────────────────────────────


def _discord_keypair() -> tuple[str, Any]:
    """Return (hex public key, private key) — mirrors a Discord application key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    public_hex = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    ).hex()
    return public_hex, private


@pytest.mark.asyncio
async def test_verify_auth_rejects_a_forged_signature() -> None:
    """A syntactically-plausible but unsigned request must be rejected.

    Regression: verify_auth carried a `TODO: implement full Ed25519 verify` and
    returned `bool(signature and timestamp and body)` — so ANY non-empty header
    triple authenticated. Identical in shape to the Microsoft Teams auth bypass
    (`Bearer ` + 20 chars) fixed earlier; anyone who knew an org_id could forge
    a Discord interaction the moment this adapter was routed.
    """
    public_hex, _ = _discord_keypair()
    adapter = DiscordChannelAdapter(bot_token="tok", public_key=public_hex)
    ok = await adapter.verify_auth(
        {"X-Signature-Ed25519": "ab" * 64, "X-Signature-Timestamp": "1700000000"},
        {},
        raw_body=b'{"type":1}',
    )
    assert ok is False


@pytest.mark.asyncio
async def test_verify_auth_accepts_a_genuine_signature() -> None:
    public_hex, private = _discord_keypair()
    adapter = DiscordChannelAdapter(bot_token="tok", public_key=public_hex)
    timestamp, body = "1700000000", b'{"type":1}'
    signature = private.sign(timestamp.encode() + body).hex()
    ok = await adapter.verify_auth(
        {"X-Signature-Ed25519": signature, "X-Signature-Timestamp": timestamp},
        {},
        raw_body=body,
    )
    assert ok is True


@pytest.mark.asyncio
async def test_verify_auth_rejects_a_tampered_body() -> None:
    """A signature genuinely issued for one body must not authenticate another."""
    public_hex, private = _discord_keypair()
    adapter = DiscordChannelAdapter(bot_token="tok", public_key=public_hex)
    timestamp = "1700000000"
    signature = private.sign(timestamp.encode() + b'{"type":1}').hex()
    ok = await adapter.verify_auth(
        {"X-Signature-Ed25519": signature, "X-Signature-Timestamp": timestamp},
        {},
        raw_body=b'{"type":2,"data":{"name":"org-approve"}}',
    )
    assert ok is False
