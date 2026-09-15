"""Tests for the telephony (voice/phone) channel adapter (Phase 8).

Covers inbound call-webhook normalization, TwiML-like response formatting, outbound
``place_call`` against an injected (mock) telephony client, and Twilio-style webhook
signature verification. No real telephony provider is contacted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any

import pytest

from app.gateway.channels.voice_phone import VoicePhoneChannelAdapter
from app.gateway.command import OrgResponse

TENANT = "00000000-0000-0000-0000-000000000002"
ORG = "org-1"
CALLER = "+15551234567"


# ── Inbound normalization ────────────────────────────────────────────────────────


async def test_normalize_speech_result() -> None:
    adapter = VoicePhoneChannelAdapter()
    payload = {
        "From": CALLER,
        "To": "+15559999999",
        "CallSid": "CA123",
        "SpeechResult": "launch a marketing mission",
        "Confidence": "0.92",
    }
    cmd = await adapter.normalize(payload, TENANT, ORG)
    assert cmd.text == "launch a marketing mission"
    assert cmd.actor_id == CALLER
    assert cmd.actor_channel == "voice_phone"
    # caller_id drives conversation continuity.
    assert cmd.conversation_id == CALLER
    assert cmd.tenant_id == TENANT
    assert cmd.raw_payload["_call_sid"] == "CA123"
    assert cmd.raw_payload["_confidence"] == 0.92


async def test_normalize_recording_only_has_empty_text_and_ref() -> None:
    adapter = VoicePhoneChannelAdapter()
    payload = {
        "From": CALLER,
        "CallSid": "CA9",
        "RecordingUrl": "https://api.example.com/rec/CA9.wav",
    }
    cmd = await adapter.normalize(payload, TENANT, ORG)
    assert cmd.text == ""
    assert cmd.raw_payload["_recording_url"] == "https://api.example.com/rec/CA9.wav"


# ── Response formatting ──────────────────────────────────────────────────────────


def test_format_reply_builds_gather_twiml() -> None:
    adapter = VoicePhoneChannelAdapter()
    out = adapter.format_reply("What environment should I target?")
    assert out["say"] == "What environment should I target?"
    assert out["gather"] is True
    assert out["twiml"].startswith('<?xml')
    assert "<Gather" in out["twiml"]
    assert "<Say>What environment should I target?</Say>" in out["twiml"]


def test_format_reply_no_gather() -> None:
    adapter = VoicePhoneChannelAdapter()
    out = adapter.format_reply("Goodbye.", gather=False)
    assert "<Gather" not in out["twiml"]
    assert "<Say>Goodbye.</Say>" in out["twiml"]


def test_format_reply_escapes_xml() -> None:
    adapter = VoicePhoneChannelAdapter()
    out = adapter.format_reply("Tom & Jerry <fast>")
    assert "&amp;" in out["twiml"]
    assert "&lt;fast&gt;" in out["twiml"]


def test_format_response_uses_voice_text() -> None:
    adapter = VoicePhoneChannelAdapter()
    resp = OrgResponse(command_id="c1", text="long text", voice_text="short spoken")
    out = adapter.format_response(resp)
    assert "short spoken" in out["twiml"]


# ── Outbound place_call ──────────────────────────────────────────────────────────


class _FakeCall:
    def __init__(self, sid: str, status: str = "queued") -> None:
        self.sid = sid
        self.status = status


class _FakeCalls:
    def __init__(self) -> None:
        self.create_kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> _FakeCall:
        self.create_kwargs = kwargs
        return _FakeCall("CA-outbound-1")


class _FakeTelephonyClient:
    def __init__(self) -> None:
        self.calls = _FakeCalls()


async def test_place_call_invokes_client_with_args() -> None:
    adapter = VoicePhoneChannelAdapter()
    client = _FakeTelephonyClient()
    result = await adapter.place_call(
        to=CALLER,
        from_="+15550000000",
        client=client,
        url="https://api.example.com/voice/twiml",
    )
    assert client.calls.create_kwargs == {
        "to": CALLER,
        "from_": "+15550000000",
        "url": "https://api.example.com/voice/twiml",
    }
    assert result is not None
    assert result["sid"] == "CA-outbound-1"
    assert result["to"] == CALLER
    assert result["from"] == "+15550000000"


async def test_place_call_uses_default_from() -> None:
    adapter = VoicePhoneChannelAdapter(default_from="+15551110000")
    client = _FakeTelephonyClient()
    await adapter.place_call(to=CALLER, client=client, twiml="<Response/>")
    assert client.calls.create_kwargs is not None
    assert client.calls.create_kwargs["from_"] == "+15551110000"
    assert client.calls.create_kwargs["twiml"] == "<Response/>"


async def test_place_call_requires_client() -> None:
    adapter = VoicePhoneChannelAdapter(default_from="+1")
    with pytest.raises(RuntimeError):
        await adapter.place_call(to=CALLER, client=None)


async def test_place_call_requires_from() -> None:
    adapter = VoicePhoneChannelAdapter()  # no default from
    client = _FakeTelephonyClient()
    with pytest.raises(ValueError):
        await adapter.place_call(to=CALLER, client=client)


# ── Auth verification ────────────────────────────────────────────────────────────


async def test_verify_auth_open_without_token() -> None:
    adapter = VoicePhoneChannelAdapter(auth_token="")
    assert await adapter.verify_auth({}, {"From": CALLER}) is True


async def test_verify_auth_validates_twilio_signature() -> None:
    token = "secrettoken"  # test fixture
    adapter = VoicePhoneChannelAdapter(auth_token=token)
    url = "https://api.example.com/voice/webhook"
    payload = {"From": CALLER, "CallSid": "CA1", "_request_url": url}
    params = {k: v for k, v in payload.items() if not k.startswith("_")}
    signed = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    expected = base64.b64encode(
        hmac.new(token.encode(), signed.encode(), hashlib.sha1).digest()
    ).decode()
    assert await adapter.verify_auth({"X-Twilio-Signature": expected}, payload) is True
    assert await adapter.verify_auth({"X-Twilio-Signature": "wrong"}, payload) is False
