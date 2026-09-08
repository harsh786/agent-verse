"""Retention governance for voice audio + transcripts (D-24, Coverage-Matrix row 21).

Two personal-data artefacts flow through a voice session: the **raw audio** and the
**transcript**. This module governs both:

* raw audio is **dropped** after transcription unless retention is explicitly enabled;
* transcripts are **PII-redacted** by default before they are kept or forwarded;
* :func:`purge_expired` drops transcript records older than the configured max age.

The redaction here is deliberately self-contained (SSN / credit-card / email / phone)
and mirrors the leakage patterns in ``app/intelligence/guardrails.py`` so the voice
package carries no import-time dependency on the guardrail engine.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from typing import Any, TypeVar

_REDACTION = "[REDACTED]"

# PII patterns (mirrors app/intelligence/guardrails._PII_PATTERNS, plus email/phone).
_PII_PATTERNS: list[re.Pattern[str]] = [
    # SSN: 123-45-6789
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    # Credit card: major-issuer 13-16 digit runs
    re.compile(
        r"\b(?:4[0-9]{12}(?:[0-9]{3})?|[25][1-7][0-9]{14}|6(?:011|5[0-9][0-9])[0-9]{12}"
        r"|3[47][0-9]{13}|3(?:0[0-5]|[68][0-9])[0-9]{11}|(?:2131|1800|35\d{3})\d{11})\b"
    ),
    # Generic 16-digit card (grouped or not)
    re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"),
    # Email
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    # North-American phone: 555-123-4567 / 555.123.4567 / (555) 123 4567
    re.compile(r"\b(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"),
]


def redact_pii(text: str) -> str:
    """Return ``text`` with recognised PII replaced by ``[REDACTED]``."""
    redacted = text
    for pattern in _PII_PATTERNS:
        redacted = pattern.sub(_REDACTION, redacted)
    return redacted


@dataclass
class VoiceRetentionPolicy:
    """How long / whether voice artefacts are kept.

    Attributes:
        retain_audio: keep raw audio after transcription (default: drop it).
        retain_transcripts: keep the transcript at all (default: yes).
        redact_transcripts: PII-redact the transcript before keeping/forwarding.
        max_transcript_age: transcripts older than this are purged; ``None`` = no cap.
    """

    retain_audio: bool = False
    retain_transcripts: bool = True
    redact_transcripts: bool = True
    max_transcript_age: _dt.timedelta | None = None


@dataclass
class RetentionResult:
    """Outcome of applying a :class:`VoiceRetentionPolicy` to one utterance."""

    audio: bytes | None
    transcript: str | None
    audio_dropped: bool
    transcript_redacted: bool


def apply_retention(
    policy: VoiceRetentionPolicy,
    *,
    audio: bytes | None,
    transcript: str | None,
) -> RetentionResult:
    """Apply ``policy`` to one utterance's raw audio and transcript.

    Returns a :class:`RetentionResult` where ``audio``/``transcript`` are ``None`` when
    the policy drops them, and ``transcript`` is PII-redacted when configured.
    """
    kept_audio = audio if policy.retain_audio else None
    audio_dropped = audio is not None and kept_audio is None

    kept_transcript: str | None = transcript
    transcript_redacted = False
    if not policy.retain_transcripts:
        kept_transcript = None
    elif transcript is not None and policy.redact_transcripts:
        redacted = redact_pii(transcript)
        transcript_redacted = redacted != transcript
        kept_transcript = redacted

    return RetentionResult(
        audio=kept_audio,
        transcript=kept_transcript,
        audio_dropped=audio_dropped,
        transcript_redacted=transcript_redacted,
    )


_Record = TypeVar("_Record", bound=dict[str, Any])


def purge_expired(
    records: list[_Record],
    policy: VoiceRetentionPolicy,
    *,
    now: _dt.datetime | None = None,
    timestamp_key: str = "created_at",
) -> list[_Record]:
    """Return the subset of ``records`` still within ``policy.max_transcript_age``.

    Each record is a mapping carrying a timezone-aware ``created_at`` (or
    ``timestamp_key``) datetime. When ``max_transcript_age`` is ``None`` the input is
    returned unchanged (no retention cap configured).
    """
    if policy.max_transcript_age is None:
        return records
    cutoff = (now or _dt.datetime.now(_dt.UTC)) - policy.max_transcript_age
    return [r for r in records if r[timestamp_key] >= cutoff]
