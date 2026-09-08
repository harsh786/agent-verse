"""Tests for voice consent gating and audio/transcript retention (D-24).

Covers Coverage-Matrix row 21: the voice subsystem must not process audio without
recorded consent, and raw audio + transcripts must be governed by a retention policy
(drop raw audio after transcription, redact PII from transcripts by default).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from app.voice.consent import (
    ConsentRecord,
    VoiceConsentError,
    VoiceConsentPolicy,
)
from app.voice.retention import (
    RetentionResult,
    VoiceRetentionPolicy,
    apply_retention,
    purge_expired,
    redact_pii,
)
from app.voice.streaming import VoiceStreamingSession

TENANT = "00000000-0000-0000-0000-000000000002"
SPEAKER = "speaker-1"


# ── Fakes ──────────────────────────────────────────────────────────────────────


class _FakeWS:
    """Minimal WebSocket stand-in that records what the session sends."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_text(self, txt: str) -> None:
        self.sent.append(json.loads(txt))


def _make_session(*, consent_granted: bool) -> VoiceStreamingSession:
    ws = _FakeWS()
    sess = VoiceStreamingSession(
        ws,  # type: ignore[arg-type]
        tenant_id=TENANT,
        org_id="org-1",
        session_factory=None,
        consent_granted=consent_granted,
        speaker_id=SPEAKER,
    )
    # Populate the buffer with > 800 samples so the pipeline does not early-return.
    sess._buf.append(np.zeros(1600, dtype=np.float32))
    return sess


# ── Consent policy (unit) ───────────────────────────────────────────────────────


def test_consent_policy_fail_closed_by_default() -> None:
    policy = VoiceConsentPolicy()
    assert policy.has_consent(TENANT, SPEAKER) is False
    with pytest.raises(VoiceConsentError) as exc:
        policy.require(TENANT, SPEAKER)
    assert exc.value.code == "consent_required"


def test_consent_policy_records_and_allows() -> None:
    policy = VoiceConsentPolicy()
    rec = policy.record_consent(TENANT, SPEAKER)
    assert isinstance(rec, ConsentRecord)
    assert rec.granted is True
    assert policy.has_consent(TENANT, SPEAKER) is True
    # require() no longer raises once consent is recorded.
    policy.require(TENANT, SPEAKER)


def test_consent_policy_revoke() -> None:
    policy = VoiceConsentPolicy()
    policy.record_consent(TENANT, SPEAKER)
    policy.revoke(TENANT, SPEAKER)
    assert policy.has_consent(TENANT, SPEAKER) is False
    with pytest.raises(VoiceConsentError):
        policy.require(TENANT, SPEAKER)


def test_consent_policy_permissive_mode() -> None:
    policy = VoiceConsentPolicy(fail_closed=False)
    assert policy.has_consent(TENANT, SPEAKER) is True
    policy.require(TENANT, SPEAKER)  # does not raise


def test_consent_is_scoped_per_speaker() -> None:
    policy = VoiceConsentPolicy()
    policy.record_consent(TENANT, SPEAKER)
    assert policy.has_consent(TENANT, "other-speaker") is False


# ── Streaming pipeline consent gate ─────────────────────────────────────────────


async def test_pipeline_refused_without_consent() -> None:
    sess = _make_session(consent_granted=False)
    with patch("app.voice.streaming.transcribe", AsyncMock()) as mock_tx:
        await sess._run_pipeline()
    mock_tx.assert_not_awaited()
    codes = [m.get("code") for m in sess.ws.sent]  # type: ignore[attr-defined]
    assert "consent_required" in codes


async def test_pipeline_proceeds_with_consent() -> None:
    sess = _make_session(consent_granted=True)
    tx_result = {"transcript": "hello world", "language": "en", "confidence": 0.9}
    with (
        patch("app.voice.streaming.transcribe", AsyncMock(return_value=tx_result)) as mock_tx,
        patch(
            "app.voice.streaming.route_voice_command",
            AsyncMock(return_value="done"),
        ),
        patch("app.voice.streaming.synthesize_streaming") as mock_tts,
    ):
        mock_tts.return_value = _empty_async_gen()
        await sess._run_pipeline()
    mock_tx.assert_awaited_once()
    types = [m.get("type") for m in sess.ws.sent]  # type: ignore[attr-defined]
    assert "transcript" in types
    assert "agent_response" in types
    codes = [m.get("code") for m in sess.ws.sent]  # type: ignore[attr-defined]
    assert "consent_required" not in codes


async def test_runtime_consent_message_grants_consent() -> None:
    """A `consent` control message grants consent for the session speaker."""
    sess = _make_session(consent_granted=False)
    assert sess.has_consent() is False
    sess._grant_consent(speaker_id=SPEAKER)
    assert sess.has_consent() is True


async def test_pipeline_redacts_pii_from_routed_transcript() -> None:
    """Consent granted, but PII in the transcript is redacted before routing."""
    sess = _make_session(consent_granted=True)
    tx_result = {
        "transcript": "my ssn is 123-45-6789 ok",
        "language": "en",
        "confidence": 0.9,
    }
    with (
        patch("app.voice.streaming.transcribe", AsyncMock(return_value=tx_result)),
        patch(
            "app.voice.streaming.route_voice_command",
            AsyncMock(return_value="done"),
        ) as mock_route,
        patch("app.voice.streaming.synthesize_streaming") as mock_tts,
    ):
        mock_tts.return_value = _empty_async_gen()
        await sess._run_pipeline()
    routed_text = mock_route.call_args.args[0]
    assert "123-45-6789" not in routed_text
    assert "[REDACTED]" in routed_text


# ── Retention policy (unit) ─────────────────────────────────────────────────────


def test_redact_pii_masks_common_identifiers() -> None:
    text = "ssn 123-45-6789 card 4111111111111111 mail a@b.com call 555-123-4567"
    red = redact_pii(text)
    assert "123-45-6789" not in red
    assert "4111111111111111" not in red
    assert "a@b.com" not in red
    assert "555-123-4567" not in red
    assert "[REDACTED]" in red


def test_apply_retention_drops_audio_and_redacts_by_default() -> None:
    policy = VoiceRetentionPolicy()  # defaults: drop audio, redact transcripts
    result = apply_retention(
        policy, audio=b"rawpcm", transcript="ssn 123-45-6789"
    )
    assert isinstance(result, RetentionResult)
    assert result.audio is None
    assert result.audio_dropped is True
    assert result.transcript is not None
    assert "123-45-6789" not in result.transcript
    assert result.transcript_redacted is True


def test_apply_retention_keeps_audio_when_enabled() -> None:
    policy = VoiceRetentionPolicy(retain_audio=True, redact_transcripts=False)
    result = apply_retention(policy, audio=b"rawpcm", transcript="ssn 123-45-6789")
    assert result.audio == b"rawpcm"
    assert result.audio_dropped is False
    assert result.transcript == "ssn 123-45-6789"
    assert result.transcript_redacted is False


def test_apply_retention_drops_transcript_when_not_retained() -> None:
    policy = VoiceRetentionPolicy(retain_transcripts=False)
    result = apply_retention(policy, audio=b"x", transcript="anything")
    assert result.transcript is None


def test_purge_expired_removes_old_records() -> None:
    import datetime as dt

    policy = VoiceRetentionPolicy(max_transcript_age=dt.timedelta(days=30))
    now = dt.datetime(2026, 9, 8, tzinfo=dt.UTC)
    records = [
        {"id": "fresh", "created_at": now - dt.timedelta(days=1)},
        {"id": "stale", "created_at": now - dt.timedelta(days=90)},
    ]
    kept = purge_expired(records, policy, now=now)
    kept_ids = {r["id"] for r in kept}
    assert "fresh" in kept_ids
    assert "stale" not in kept_ids


def test_purge_no_op_when_age_unset() -> None:
    import datetime as dt

    policy = VoiceRetentionPolicy(max_transcript_age=None)
    now = dt.datetime(2026, 9, 8, tzinfo=dt.UTC)
    records = [{"id": "old", "created_at": now - dt.timedelta(days=9999)}]
    assert purge_expired(records, policy, now=now) == records


# ── helpers ─────────────────────────────────────────────────────────────────────


async def _empty_async_gen():  # type: ignore[no-untyped-def]
    for _ in ():
        yield b""
