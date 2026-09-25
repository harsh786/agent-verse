"""Phase 8 e2e — inbound voice call webhook drives ChatService and speaks TwiML.

Builds a minimal app mounting the gateway router with an in-memory ChatService,
a voice registry, and the telephony adapter — then posts a Twilio-style call
webhook and asserts: the tenant is resolved from the called number, the caller's
speech runs through the unified ChatService pipeline (durable session + persisted
turn), and a TwiML reply is spoken back.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.chat.service import ChatService
from app.gateway.channels.voice_phone import VoicePhoneChannelAdapter
from app.gateway.router import router as gateway_router
from app.gateway.voice_registry import VoicePhoneRegistry
from app.identity import IdentityService

TENANT = "tenant-voice"
LINE = "+15550001111"   # the provisioned number (To)
CALLER = "+15559998888"  # the caller (From)


def _app() -> tuple[FastAPI, ChatService]:
    app = FastAPI()
    app.include_router(gateway_router)
    chat = ChatService()
    chat.attach_engine(identity_service=IdentityService())
    reg = VoicePhoneRegistry()
    reg.register(LINE, TENANT)
    app.state.chat_service = chat
    app.state.voice_phone_registry = reg
    app.state.voice_phone_adapter = VoicePhoneChannelAdapter()
    return app, chat


def _post(client: TestClient, **form: str):
    return client.post("/v1/gateway/voice/incoming", data=form)


def test_inbound_call_routes_through_chatservice_and_speaks_twiml() -> None:
    app, chat = _app()
    client = TestClient(app)
    r = _post(client, **{
        "From": CALLER, "To": LINE, "CallSid": "CA1",
        "SpeechResult": "remind me to call the dentist tomorrow",
    })
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    body = r.text
    assert "<Response>" in body and "<Say>" in body and "<Gather" in body

    # The caller's speech was persisted through the unified pipeline as a durable
    # voice_phone conversation keyed on the caller number.
    session = chat.get_or_create_channel_session(
        tenant_id=TENANT, channel="voice_phone", channel_user_id=CALLER
    )
    history = chat.list_messages(session.id, TENANT)
    assert any("dentist" in m.content for m in history)


def test_call_to_unknown_number_does_no_tenant_work() -> None:
    app, chat = _app()
    client = TestClient(app)
    r = _post(client, **{
        "From": CALLER, "To": "+19999999999", "CallSid": "CA2",
        "SpeechResult": "hello",
    })
    assert r.status_code == 200
    assert "isn't set up" in r.text
    # No session created for the caller.
    assert chat.get_session("nope", TENANT) is None


def test_call_with_no_speech_greets_and_gathers() -> None:
    app, _ = _app()
    client = TestClient(app)
    r = _post(client, **{"From": CALLER, "To": LINE, "CallSid": "CA3"})
    assert r.status_code == 200
    assert "How can I help" in r.text
    assert "<Gather" in r.text


def test_repeat_calls_continue_same_session() -> None:
    app, chat = _app()
    client = TestClient(app)
    _post(client, **{"From": CALLER, "To": LINE, "CallSid": "CA4", "SpeechResult": "first turn"})
    _post(client, **{"From": CALLER, "To": LINE, "CallSid": "CA4", "SpeechResult": "second turn"})
    session = chat.get_or_create_channel_session(
        tenant_id=TENANT, channel="voice_phone", channel_user_id=CALLER
    )
    contents = [m.content for m in chat.list_messages(session.id, TENANT)]
    assert any("first turn" in c for c in contents)
    assert any("second turn" in c for c in contents)


def test_bad_signature_is_rejected_when_auth_token_configured() -> None:
    app, _ = _app()
    # Adapter with an auth token now enforces the Twilio signature.
    app.state.voice_phone_adapter = VoicePhoneChannelAdapter(auth_token="secret-token")
    client = TestClient(app)
    r = _post(client, **{
        "From": CALLER, "To": LINE, "CallSid": "CA5", "SpeechResult": "hi",
    })
    assert r.status_code == 403


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+1 (555) 000-1111", "+15550001111"),
        ("+15550001111", "+15550001111"),
    ],
)
def test_registry_normalizes_numbers(raw: str, expected: str) -> None:
    reg = VoicePhoneRegistry()
    reg.register(raw, "t1")
    assert reg.resolve(expected) is not None
    assert reg.resolve("+15550009999") is None


def test_registry_from_env_seeds_bindings() -> None:
    reg = VoicePhoneRegistry.from_env("+15550001111:tenant_a,+15550002222:tenant_b:org_x")
    a = reg.resolve("+15550001111")
    b = reg.resolve("+15550002222")
    assert a is not None and a.tenant_id == "tenant_a"
    assert b is not None and b.tenant_id == "tenant_b" and b.org_id == "org_x"


def test_inbound_call_redacts_pii_from_the_persisted_transcript() -> None:
    """A caller speaking an SSN/card number must not have it persisted verbatim.

    Regression: ``/voice/incoming`` called ``handle_voice_turn`` without a
    ``retention_policy``, so ``apply_retention`` never ran on the live PSTN path
    and the raw transcript was persisted straight into the durable chat session.

    The WebSocket voice path (``app/voice/streaming.py``) defaults the policy on
    (``self._retention = retention_policy or VoiceRetentionPolicy()``), and
    ``app/voice/retention.py`` documents redaction as the default ("transcripts
    are PII-redacted by default before they are kept or forwarded") — the phone
    path was the one entry point that skipped it.
    """
    app, chat = _app()
    client = TestClient(app)
    r = _post(client, **{
        "From": CALLER, "To": LINE, "CallSid": "CA-PII",
        "SpeechResult": "my ssn is 123-45-6789 and my card is 4111111111111111",
    })
    assert r.status_code == 200

    session = chat.get_or_create_channel_session(
        tenant_id=TENANT, channel="voice_phone", channel_user_id=CALLER
    )
    persisted = " ".join(m.content for m in chat.list_messages(session.id, TENANT))
    assert "123-45-6789" not in persisted, f"SSN persisted verbatim: {persisted!r}"
    assert "4111111111111111" not in persisted, f"card persisted verbatim: {persisted!r}"
    assert "[REDACTED]" in persisted
