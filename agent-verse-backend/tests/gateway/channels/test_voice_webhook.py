"""Tests for app/gateway/channels/voice_webhook.py — VoiceWebhookAdapter.

Covers:
  - verify_auth (dev mode / HMAC verification)
  - normalize (transcript source keys, low-confidence flag, defaults)
  - format_response (voice_text override, TTS trimming)
  - _trim_for_tts edge cases
"""
from __future__ import annotations

import hashlib
import hmac

import pytest

from app.gateway.channels.voice_webhook import VoiceWebhookAdapter
from app.gateway.command import OrgResponse


# ── verify_auth ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_auth_no_secret_dev_mode_allows():
    adapter = VoiceWebhookAdapter(webhook_secret="")
    ok = await adapter.verify_auth({}, {})
    assert ok is True


@pytest.mark.asyncio
async def test_verify_auth_valid_signature():
    secret = "shh"
    adapter = VoiceWebhookAdapter(webhook_secret=secret)
    body = '{"transcript": "hi"}'
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    ok = await adapter.verify_auth(
        {"X-Voice-Signature": f"sha256={expected}"},
        {"_raw_body": body},
    )
    assert ok is True


@pytest.mark.asyncio
async def test_verify_auth_invalid_signature_rejects():
    adapter = VoiceWebhookAdapter(webhook_secret="shh")
    ok = await adapter.verify_auth(
        {"X-Voice-Signature": "sha256=deadbeef"},
        {"_raw_body": "some body"},
    )
    assert ok is False


@pytest.mark.asyncio
async def test_verify_auth_missing_raw_body_computes_against_empty_string():
    secret = "shh"
    adapter = VoiceWebhookAdapter(webhook_secret=secret)
    expected = hmac.new(secret.encode(), b"", hashlib.sha256).hexdigest()
    ok = await adapter.verify_auth({"X-Voice-Signature": f"sha256={expected}"}, {})
    assert ok is True


# ── normalize ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_normalize_transcript_field():
    adapter = VoiceWebhookAdapter()
    cmd = await adapter.normalize(
        {"transcript": "  turn on the lights  "}, tenant_id="t1", org_id="o1"
    )
    assert cmd.text == "turn on the lights"
    assert cmd.actor_id == "voice_user"
    assert cmd.actor_channel == "voice"
    assert cmd.urgency == "normal"


@pytest.mark.asyncio
async def test_normalize_falls_back_to_text_field():
    adapter = VoiceWebhookAdapter()
    cmd = await adapter.normalize({"text": "hello world"}, tenant_id="t1", org_id="o1")
    assert cmd.text == "hello world"


@pytest.mark.asyncio
async def test_normalize_falls_back_to_transcription_field():
    adapter = VoiceWebhookAdapter()
    cmd = await adapter.normalize({"transcription": "hey there"}, tenant_id="t1", org_id="o1")
    assert cmd.text == "hey there"


@pytest.mark.asyncio
async def test_normalize_prefers_transcript_over_others():
    adapter = VoiceWebhookAdapter()
    cmd = await adapter.normalize(
        {"transcript": "A", "text": "B", "transcription": "C"}, tenant_id="t1", org_id="o1"
    )
    assert cmd.text == "A"


@pytest.mark.asyncio
async def test_normalize_low_confidence_adds_prefix():
    adapter = VoiceWebhookAdapter()
    cmd = await adapter.normalize(
        {"transcript": "maybe this", "confidence": 0.4}, tenant_id="t1", org_id="o1"
    )
    assert cmd.text.startswith("[Low-confidence transcript, please confirm]")
    assert "maybe this" in cmd.text


@pytest.mark.asyncio
async def test_normalize_high_confidence_no_prefix():
    adapter = VoiceWebhookAdapter()
    cmd = await adapter.normalize(
        {"transcript": "clear text", "confidence": 0.95}, tenant_id="t1", org_id="o1"
    )
    assert cmd.text == "clear text"


@pytest.mark.asyncio
async def test_normalize_custom_actor_and_origin_channel():
    adapter = VoiceWebhookAdapter()
    cmd = await adapter.normalize(
        {
            "transcript": "hi",
            "user_id": "u1",
            "user_name": "Alice",
            "origin_channel": "telegram_voice",
            "urgency": "urgent",
        },
        tenant_id="t1",
        org_id="o1",
    )
    assert cmd.actor_id == "u1"
    assert cmd.actor_name == "Alice"
    assert cmd.actor_channel == "telegram_voice"
    assert cmd.urgency == "urgent"


# ── format_response ───────────────────────────────────────────────────────────

def test_format_response_uses_voice_text_when_present():
    adapter = VoiceWebhookAdapter()
    resp = OrgResponse(command_id="c1", text="long text version", voice_text="short version")
    out = adapter.format_response(resp)
    assert out["text"] == "short version"
    assert out["ssml"] == "<speak>short version</speak>"
    assert out["tts_engine"] == "auto"


def test_format_response_falls_back_to_trimmed_text():
    adapter = VoiceWebhookAdapter()
    resp = OrgResponse(command_id="c1", text="short")
    out = adapter.format_response(resp)
    assert out["text"] == "short"


# ── _trim_for_tts ─────────────────────────────────────────────────────────────

def test_trim_for_tts_short_text_unchanged():
    assert VoiceWebhookAdapter._trim_for_tts("hello") == "hello"


def test_trim_for_tts_trims_at_sentence_boundary():
    text = "First sentence. Second sentence. " + "x" * 500
    trimmed = VoiceWebhookAdapter._trim_for_tts(text, max_chars=40)
    assert trimmed.endswith(".")
    assert len(trimmed) <= 40


def test_trim_for_tts_no_sentence_boundary_hard_truncates():
    text = "x" * 100
    trimmed = VoiceWebhookAdapter._trim_for_tts(text, max_chars=20)
    assert trimmed == "x" * 20


def test_channel_name_is_voice_webhook():
    assert VoiceWebhookAdapter.channel_name == "voice_webhook"
