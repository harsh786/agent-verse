"""Phase 8 e2e — inbound voice call webhook drives ChatService and speaks TwiML.

Builds a minimal app mounting the gateway router with an in-memory ChatService,
a voice registry, and the telephony adapter — then posts a Twilio-style call
webhook and asserts: the tenant is resolved from the called number, the caller's
speech runs through the unified ChatService pipeline (durable session + persisted
turn), and a TwiML reply is spoken back.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.chat.service import ChatService
from app.gateway.channels.voice_phone import VoicePhoneChannelAdapter
from app.gateway.router import router as gateway_router
from app.gateway.voice_registry import VoicePhoneRegistry
from app.identity import IdentityService
from app.voice.consent import VoiceConsentPolicy

TENANT = "tenant-voice"
LINE = "+15550001111"   # the provisioned number (To)
AUTH_TOKEN = "voice-e2e-token"  # test fixture; the webhook fails closed without one
CALLER = "+15559998888"  # the caller (From)


def _app(*, consented: bool = True) -> tuple[FastAPI, ChatService]:
    app = FastAPI()
    app.include_router(gateway_router)
    chat = ChatService()
    chat.attach_engine(identity_service=IdentityService())
    reg = VoicePhoneRegistry()
    reg.register(LINE, TENANT)
    app.state.chat_service = chat
    app.state.voice_phone_registry = reg
    app.state.voice_phone_adapter = VoicePhoneChannelAdapter(auth_token=AUTH_TOKEN)
    if consented:
        policy = VoiceConsentPolicy()
        policy.record_consent(TENANT, CALLER, purpose="voice_phone")
        app.state.voice_consent_policy = policy
    return app, chat


def _twilio_sig(url: str, form: dict[str, str]) -> str:
    payload = url + "".join(f"{k}{form[k]}" for k in sorted(form))
    return base64.b64encode(
        hmac.new(AUTH_TOKEN.encode(), payload.encode(), hashlib.sha1).digest()
    ).decode()


def _post(client: TestClient, **form: str):
    url = "http://testserver/v1/gateway/voice/incoming"
    return client.post(
        "/v1/gateway/voice/incoming",
        data=form,
        headers={"X-Twilio-Signature": _twilio_sig(url, form)},
    )


def _channel_session(chat, channel, channel_user_id):
    """The session the inbound path mapped this channel user to (read-only).

    The sync ``get_or_create_channel_session`` was removed (CHAT-CHANNEL-DEAD);
    a KeyError here means the webhook never resolved a session for the user.
    """
    session_id = chat._channel_sessions[(TENANT, channel, channel_user_id)]
    session = chat.get_session(session_id, TENANT)
    assert session is not None
    return session


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
    session = _channel_session(chat, "voice_phone", CALLER)
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
    session = _channel_session(chat, "voice_phone", CALLER)
    contents = [m.content for m in chat.list_messages(session.id, TENANT)]
    assert any("first turn" in c for c in contents)
    assert any("second turn" in c for c in contents)


def test_bad_signature_is_rejected_when_auth_token_configured() -> None:
    app, _ = _app()
    # Adapter with an auth token now enforces the Twilio signature.
    app.state.voice_phone_adapter = VoicePhoneChannelAdapter(auth_token="secret-token")
    client = TestClient(app)
    r = _post(client, **{  # signed with AUTH_TOKEN, not "secret-token" → invalid
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

    session = _channel_session(chat, "voice_phone", CALLER)
    persisted = " ".join(m.content for m in chat.list_messages(session.id, TENANT))
    assert "123-45-6789" not in persisted, f"SSN persisted verbatim: {persisted!r}"
    assert "4111111111111111" not in persisted, f"card persisted verbatim: {persisted!r}"
    assert "[REDACTED]" in persisted


# ── Consent gate (regression: voice_consent_policy was never set → no gate) ──


def _history(chat: ChatService) -> list[str]:
    """Everything persisted for the caller; nothing when no session was ever made."""
    if (TENANT, "voice_phone", CALLER) not in chat._channel_sessions:
        return []
    session = _channel_session(chat, "voice_phone", CALLER)
    return [m.content for m in chat.list_messages(session.id, TENANT)]


def test_speech_without_consent_is_not_processed_or_persisted() -> None:
    app, chat = _app(consented=False)
    client = TestClient(app)
    r = _post(client, **{
        "From": CALLER, "To": LINE, "CallSid": "CC1",
        "SpeechResult": "my account number is 998877",
    })
    assert r.status_code == 200
    assert "Say yes to continue" in r.text
    assert not any("998877" in c for c in _history(chat))


def test_greeting_without_consent_speaks_the_notice() -> None:
    app, _ = _app(consented=False)
    r = _post(TestClient(app), **{"From": CALLER, "To": LINE, "CallSid": "CC2"})
    assert "Say yes to continue" in r.text and "<Gather" in r.text


def test_saying_yes_records_consent_then_turns_are_processed() -> None:
    app, chat = _app(consented=False)
    client = TestClient(app)
    yes = _post(client, **{"From": CALLER, "To": LINE, "CallSid": "CC3", "SpeechResult": "Yes."})
    assert "How can I help" in yes.text
    assert app.state.voice_consent_policy.has_consent(TENANT, CALLER)
    _post(client, **{"From": CALLER, "To": LINE, "CallSid": "CC3",
                     "SpeechResult": "book the dentist"})
    assert any("dentist" in c for c in _history(chat))


def test_stop_revokes_consent_and_ends_the_call() -> None:
    app, chat = _app()
    client = TestClient(app)
    r = _post(client, **{"From": CALLER, "To": LINE, "CallSid": "CC4", "SpeechResult": "stop"})
    assert "Goodbye" in r.text and "<Gather" not in r.text
    assert not app.state.voice_consent_policy.has_consent(TENANT, CALLER)
    _post(client, **{"From": CALLER, "To": LINE, "CallSid": "CC4", "SpeechResult": "secret 42"})
    assert not any("secret 42" in c for c in _history(chat))
