"""Tests for the voice → ChatService bridge (Phase 8).

Verifies a transcribed call turn is routed through ChatService's unified channel
entry point with ``channel="voice_phone"`` and the caller id, that a reply string is
derived, that an injected TTS is invoked, and that consent/retention are honoured.
No real telephony / STT / TTS is used — everything is a fake.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.chat.intent import ClarifyRequest, ScheduleConfirmation
from app.voice.chat_bridge import (
    VOICE_CHANNEL,
    VoiceChatBridge,
    derive_reply_text,
    handle_voice_audio,
    handle_voice_turn,
)
from app.voice.consent import VoiceConsentError, VoiceConsentPolicy
from app.voice.retention import VoiceRetentionPolicy

TENANT = "00000000-0000-0000-0000-000000000002"
CALLER = "+15551234567"


class _FakeChatService:
    """Records the ahandle_channel_message call and returns canned dispatch data."""

    def __init__(self, dispatch: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._dispatch = dispatch or {
            "intent": "QA",
            "session_id": "sess-123",
            "clarify_request": None,
            "schedule_confirmation": None,
        }

    async def ahandle_channel_message(
        self, *, tenant_id: str, channel: str, channel_user_id: str, text: str
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "channel": channel,
                "channel_user_id": channel_user_id,
                "text": text,
            }
        )
        return {**self._dispatch, "channel": channel}


class _FakeTTS:
    def __init__(self) -> None:
        self.synth_calls: list[str] = []

    async def synthesize(self, text: str) -> bytes:
        self.synth_calls.append(text)
        return b"AUDIO:" + text.encode()


class _FakeSTT:
    def __init__(self, transcript: str) -> None:
        self._transcript = transcript
        self.calls: list[tuple[bytes, str]] = []

    async def transcribe(self, audio: bytes, content_type: str = "audio/wav") -> dict[str, Any]:
        self.calls.append((audio, content_type))
        return {"transcript": self._transcript, "language": "en", "confidence": 0.95}


# ── Routing ──────────────────────────────────────────────────────────────────────


async def test_turn_routes_transcript_with_voice_channel_and_caller() -> None:
    chat = _FakeChatService()
    result = await handle_voice_turn(
        chat_service=chat, tenant_id=TENANT, caller_id=CALLER, transcript="hello there"
    )
    assert len(chat.calls) == 1
    call = chat.calls[0]
    assert call["channel"] == VOICE_CHANNEL == "voice_phone"
    assert call["channel_user_id"] == CALLER
    assert call["text"] == "hello there"
    assert call["tenant_id"] == TENANT
    assert result["session_id"] == "sess-123"
    assert result["reply_text"]
    assert result["intent"] == "QA"
    assert "audio" not in result  # no tts injected


async def test_turn_invokes_tts_when_provided() -> None:
    chat = _FakeChatService()
    tts = _FakeTTS()
    result = await handle_voice_turn(
        chat_service=chat, tenant_id=TENANT, caller_id=CALLER, transcript="hi", tts=tts
    )
    assert tts.synth_calls == [result["reply_text"]]
    assert result["audio"] == b"AUDIO:" + result["reply_text"].encode()


async def test_audio_entry_transcribes_then_routes() -> None:
    chat = _FakeChatService()
    stt = _FakeSTT("deploy the app")
    result = await handle_voice_audio(
        chat_service=chat,
        tenant_id=TENANT,
        caller_id=CALLER,
        audio=b"\x00\x01",
        stt=stt,
    )
    assert stt.calls and stt.calls[0][0] == b"\x00\x01"
    assert result["transcript"] == "deploy the app"
    assert chat.calls[0]["text"] == "deploy the app"


# ── Reply derivation ─────────────────────────────────────────────────────────────


def test_derive_reply_speaks_clarify_question() -> None:
    dispatch = {
        "intent": "CLARIFY",
        "clarify_request": ClarifyRequest(question="Which environment?", options=["Dev", "Prod"]),
    }
    reply = derive_reply_text(dispatch)
    assert "Which environment?" in reply
    assert "Dev" in reply and "Prod" in reply


def test_derive_reply_speaks_schedule_confirmation() -> None:
    dispatch = {
        "intent": "SCHEDULE",
        "schedule_confirmation": ScheduleConfirmation(
            goal_text="report", cron_expression="0 9 * * *", human_schedule="every day at 09:00"
        ),
    }
    reply = derive_reply_text(dispatch)
    assert "every day at 09:00" in reply


def test_derive_reply_goal_ack() -> None:
    assert "follow up" in derive_reply_text({"intent": "GOAL"}).lower()


# ── Consent + retention ──────────────────────────────────────────────────────────


async def test_turn_refused_without_consent() -> None:
    chat = _FakeChatService()
    policy = VoiceConsentPolicy()  # fail-closed
    with pytest.raises(VoiceConsentError):
        await handle_voice_turn(
            chat_service=chat,
            tenant_id=TENANT,
            caller_id=CALLER,
            transcript="hi",
            consent_policy=policy,
        )
    assert chat.calls == []  # nothing routed


async def test_turn_proceeds_with_recorded_consent() -> None:
    chat = _FakeChatService()
    policy = VoiceConsentPolicy()
    policy.record_consent(TENANT, CALLER)
    await handle_voice_turn(
        chat_service=chat,
        tenant_id=TENANT,
        caller_id=CALLER,
        transcript="hi",
        consent_policy=policy,
    )
    assert len(chat.calls) == 1


async def test_turn_redacts_pii_before_routing() -> None:
    chat = _FakeChatService()
    await handle_voice_turn(
        chat_service=chat,
        tenant_id=TENANT,
        caller_id=CALLER,
        transcript="my ssn is 123-45-6789",
        retention_policy=VoiceRetentionPolicy(),  # redacts by default
    )
    routed = chat.calls[0]["text"]
    assert "123-45-6789" not in routed
    assert "[REDACTED]" in routed


async def test_bridge_class_reuses_injectables() -> None:
    chat = _FakeChatService()
    stt = _FakeSTT("call turn")
    tts = _FakeTTS()
    bridge = VoiceChatBridge(chat, stt=stt, tts=tts)
    result = await bridge.handle_audio(tenant_id=TENANT, caller_id=CALLER, audio=b"x")
    assert result["transcript"] == "call turn"
    assert chat.calls[0]["channel"] == "voice_phone"
    assert tts.synth_calls == [result["reply_text"]]
