"""Voice → ChatService bridge (Phase 8, voice/phone channel).

A phone call is just another *channel* into the unified chat pipeline. Rather than
routing spoken commands to a bespoke voice handler (``app/voice/intent_router.py``
dispatches to ``OrgService``), Phase 8 routes a transcribed call turn through
:meth:`ChatService.ahandle_channel_message` with ``channel="voice_phone"`` — so a
call shares the SAME durable, identity-aware pipeline (session continuity, intent
classification, persistence, cross-channel handoff) as web/WhatsApp/Telegram.

The bridge is deliberately thin:

    audio ──(stt)──▶ transcript ──▶ ChatService.ahandle_channel_message ──▶
        dispatch metadata ──▶ a short reply string ──(tts)──▶ audio

It does NOT synthesise the full spoken answer. ``ChatService`` classifies intent
and persists the turn but streams the actual QA answer over SSE
(:meth:`ChatService.run_qa`, which needs a wired answer generator). For a spoken
reply the bridge derives a concise acknowledgement / clarify / schedule-confirmation
string from the dispatch result. Producing the full narrated answer is a follow-up —
see :data:`_QA_TODO`. Everything (``chat_service``, ``stt``, ``tts``, consent and
retention policies) is injectable so tests pass fakes and no real telephony / STT /
TTS is required.
"""

from __future__ import annotations

import inspect
from typing import Any

from app.voice.consent import VoiceConsentPolicy
from app.voice.retention import VoiceRetentionPolicy, apply_retention

# The voice channel name understood by ChatService's unified entry point.
VOICE_CHANNEL = "voice_phone"

# TODO(Phase 8): the spoken reply for a QA/GOAL turn is currently a short
# acknowledgement. Streaming the real answer means consuming ChatService.run_qa
# (SSE token stream, requires a wired answer generator) and piping tokens into
# streaming TTS. Intentionally not reimplemented here — see module docstring.
_QA_TODO = "full spoken QA answer streams via ChatService.run_qa (not reimplemented)"

_DEFAULT_GOAL_REPLY = "Got it. I'm working on that now and will follow up here."
_DEFAULT_ACK_REPLY = "Okay, I've noted that."


def derive_reply_text(dispatch: dict[str, Any]) -> str:
    """Turn ChatService dispatch metadata into a short string to speak back.

    The dispatch result carries ``intent`` plus an optional ``clarify_request``
    (a :class:`~app.chat.intent.ClarifyRequest`) or ``schedule_confirmation``
    (a :class:`~app.chat.intent.ScheduleConfirmation`). We speak the clarifying
    question or the schedule confirmation verbatim when present; otherwise a
    concise acknowledgement (the real QA/GOAL answer is delivered out-of-band).
    """
    clarify = dispatch.get("clarify_request")
    if clarify is not None:
        question = _attr_or_key(clarify, "question") or "Could you clarify that?"
        options = _attr_or_key(clarify, "options") or []
        if options:
            return f"{question} For example: {', '.join(str(o) for o in options)}."
        return str(question)

    schedule = dispatch.get("schedule_confirmation")
    if schedule is not None:
        human = _attr_or_key(schedule, "human_schedule") or "on the requested schedule"
        return f"I'll take care of that {human}. Shall I set it up?"

    intent = str(dispatch.get("intent") or "").upper()
    if intent == "GOAL":
        return _DEFAULT_GOAL_REPLY
    return _DEFAULT_ACK_REPLY


def _attr_or_key(obj: Any, name: str) -> Any:
    """Read ``name`` from a dataclass attribute or a dict key (fakes use dicts)."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` when it is awaitable, else return it as-is.

    Lets the bridge accept both async providers (real ``stt_engine`` /
    ``tts_engine`` module functions) and simple sync fakes.
    """
    if inspect.isawaitable(value):
        return await value
    return value


async def transcribe_audio(stt: Any, audio: bytes, content_type: str = "audio/wav") -> str:
    """Transcribe ``audio`` bytes to a transcript string via an injected STT.

    Accepts the real :func:`app.voice.stt_engine.transcribe` (returns a dict with a
    ``transcript`` key) or any object/callable exposing ``transcribe`` that returns a
    string or a mapping. Normalises the result to a plain transcript string.
    """
    fn = getattr(stt, "transcribe", stt)
    result = await _maybe_await(fn(audio, content_type))
    if isinstance(result, dict):
        return str(result.get("transcript", "") or "")
    return str(result or "")


async def synthesize_reply(tts: Any, text: str) -> bytes:
    """Synthesise ``text`` to audio bytes via an injected TTS (mockable)."""
    fn = getattr(tts, "synthesize", tts)
    return bytes(await _maybe_await(fn(text)))


async def handle_voice_turn(
    *,
    chat_service: Any,
    tenant_id: str,
    caller_id: str,
    transcript: str,
    tts: Any | None = None,
    consent_policy: VoiceConsentPolicy | None = None,
    retention_policy: VoiceRetentionPolicy | None = None,
) -> dict[str, Any]:
    """Route one transcribed call turn through ChatService and return a spoken reply.

    Core flow: ``transcript`` → :meth:`ChatService.ahandle_channel_message`
    (``channel="voice_phone"``, ``channel_user_id=caller_id``) → derive a reply
    string → optionally ``tts.synthesize(reply)``.

    ``caller_id`` (the calling phone number) is the channel user id, so ChatService
    resolves/creates ONE durable conversation per caller: repeat calls continue the
    same session, and — once identities are linked — the same thread the caller uses
    on web or WhatsApp.

    Returns ``{session_id, reply_text, intent}`` plus ``audio`` when ``tts`` is given.
    Raises :class:`~app.voice.consent.VoiceConsentError` when a fail-closed consent
    policy has no recorded consent for ``(tenant_id, caller_id)``.
    """
    # Consent gate — refuse to process a caller's speech without recorded consent.
    if consent_policy is not None:
        consent_policy.require(tenant_id, caller_id)

    # Retention: PII-redact the transcript before it is persisted / routed.
    # The policy's verdict is authoritative — never fall back to the raw
    # transcript. `retain_transcripts=False` is exactly the case that yields
    # None ("drop it"), so the old `if outcome.transcript is not None` guard
    # made the *strictest* policy the leakiest one: it routed the original,
    # unredacted text. app/voice/streaming.py honours the same drop by
    # stopping the turn on an empty result; do the same here.
    routed_transcript = transcript
    if retention_policy is not None:
        outcome = apply_retention(retention_policy, audio=None, transcript=transcript)
        routed_transcript = (outcome.transcript or "").strip()
        if not routed_transcript:
            dropped: dict[str, Any] = {
                "session_id": None,
                "reply_text": _DEFAULT_ACK_REPLY,
                "intent": None,
                "transcript_dropped": True,
            }
            if tts is not None:
                dropped["audio"] = await synthesize_reply(tts, _DEFAULT_ACK_REPLY)
            return dropped

    dispatch = await chat_service.ahandle_channel_message(
        tenant_id=tenant_id,
        channel=VOICE_CHANNEL,
        channel_user_id=caller_id,
        text=routed_transcript,
    )

    reply_text = derive_reply_text(dispatch)
    result: dict[str, Any] = {
        "session_id": dispatch.get("session_id"),
        "reply_text": reply_text,
        "intent": dispatch.get("intent"),
    }
    if tts is not None:
        result["audio"] = await synthesize_reply(tts, reply_text)
    return result


async def handle_voice_audio(
    *,
    chat_service: Any,
    tenant_id: str,
    caller_id: str,
    audio: bytes,
    stt: Any,
    content_type: str = "audio/wav",
    tts: Any | None = None,
    consent_policy: VoiceConsentPolicy | None = None,
    retention_policy: VoiceRetentionPolicy | None = None,
) -> dict[str, Any]:
    """Audio-bytes entry point: ``stt.transcribe(audio)`` → :func:`handle_voice_turn`.

    Consent is checked BEFORE transcription (we must not transcribe a caller's audio
    without consent). The resulting ``transcript`` is added to the returned dict.
    """
    if consent_policy is not None:
        consent_policy.require(tenant_id, caller_id)
    transcript = await transcribe_audio(stt, audio, content_type)
    result = await handle_voice_turn(
        chat_service=chat_service,
        tenant_id=tenant_id,
        caller_id=caller_id,
        transcript=transcript,
        tts=tts,
        consent_policy=None,  # already checked above
        retention_policy=retention_policy,
    )
    result["transcript"] = transcript
    return result


class VoiceChatBridge:
    """Stateful wrapper binding injectables so a call session can reuse them.

    A telephony webhook constructs one bridge (with the shared ``chat_service`` and
    optional ``stt`` / ``tts`` / consent + retention policies) and calls
    :meth:`handle_turn` / :meth:`handle_audio` per call turn. Each caller maps to one
    chat conversation via ``caller_id`` (see :func:`handle_voice_turn`).
    """

    def __init__(
        self,
        chat_service: Any,
        *,
        stt: Any | None = None,
        tts: Any | None = None,
        consent_policy: VoiceConsentPolicy | None = None,
        retention_policy: VoiceRetentionPolicy | None = None,
    ) -> None:
        self._chat = chat_service
        self._stt = stt
        self._tts = tts
        self._consent = consent_policy
        self._retention = retention_policy

    async def handle_turn(
        self, *, tenant_id: str, caller_id: str, transcript: str
    ) -> dict[str, Any]:
        return await handle_voice_turn(
            chat_service=self._chat,
            tenant_id=tenant_id,
            caller_id=caller_id,
            transcript=transcript,
            tts=self._tts,
            consent_policy=self._consent,
            retention_policy=self._retention,
        )

    async def handle_audio(
        self,
        *,
        tenant_id: str,
        caller_id: str,
        audio: bytes,
        content_type: str = "audio/wav",
    ) -> dict[str, Any]:
        if self._stt is None:
            raise RuntimeError("VoiceChatBridge.handle_audio requires an stt engine")
        return await handle_voice_audio(
            chat_service=self._chat,
            tenant_id=tenant_id,
            caller_id=caller_id,
            audio=audio,
            stt=self._stt,
            content_type=content_type,
            tts=self._tts,
            consent_policy=self._consent,
            retention_policy=self._retention,
        )
