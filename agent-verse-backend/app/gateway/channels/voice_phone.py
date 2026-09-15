"""Telephony (voice/phone) channel adapter — Phase 8.

Handles a real phone *call* as a channel: an inbound Twilio/Vonage-style webhook
(form payload: ``From`` / ``To`` / ``CallSid`` and either ``SpeechResult`` for live
speech recognition or ``RecordingUrl`` for a recording) is normalised into an
:class:`OrgCommand`, and a reply is formatted as TwiML-like TTS instructions the
provider speaks back on the call. An agent can also *place* an outbound call via
:meth:`place_call` with an injectable telephony client (the agent phoning someone
to accomplish a task).

A call session maps to a chat conversation via ``caller_id`` (the ``From`` number):
that number is used as the ``conversation_id`` here and as ChatService's
``channel_user_id`` in :func:`app.voice.chat_bridge.handle_voice_turn`, so every
call from a number continues the same durable conversation.

Setup: ``VOICE_PHONE_AUTH_TOKEN`` (provider auth token) for webhook HMAC
verification; ``VOICE_PHONE_FROM`` as the default caller id for outbound calls.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import uuid
from typing import Any
from xml.sax.saxutils import escape

import structlog
from opentelemetry import trace

from app.gateway.channels.base import ChannelAdapter
from app.gateway.command import OrgCommand, OrgResponse

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)

_MAX_TTS_CHARS = 1000


class VoicePhoneChannelAdapter(ChannelAdapter):
    """Twilio/Vonage-style telephony adapter for live phone calls."""

    channel_name = "voice_phone"

    def __init__(self, auth_token: str | None = None, default_from: str | None = None) -> None:
        self._auth_token = auth_token or os.getenv("VOICE_PHONE_AUTH_TOKEN", "")
        self._default_from = default_from or os.getenv("VOICE_PHONE_FROM", "")

    async def verify_auth(
        self, request_headers: dict[str, str], raw_payload: dict[str, Any]
    ) -> bool:
        """Verify the provider's request signature (Twilio ``X-Twilio-Signature``).

        Twilio signs ``base64(HMAC-SHA1(auth_token, url + concat(sorted k+v)))`` over
        the request URL and POSTed params. When no auth token is configured the gate
        is open (dev mode), matching the other channel adapters.
        """
        if not self._auth_token:
            return True
        signature = request_headers.get("X-Twilio-Signature") or request_headers.get(
            "x-twilio-signature", ""
        )
        url = str(raw_payload.get("_request_url", ""))
        params = {k: v for k, v in raw_payload.items() if not k.startswith("_")}
        expected = self._twilio_signature(url, params)
        return hmac.compare_digest(signature, expected)

    def _twilio_signature(self, url: str, params: dict[str, Any]) -> str:
        payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
        digest = hmac.new(
            self._auth_token.encode(), payload.encode("utf-8"), hashlib.sha1
        ).digest()
        return base64.b64encode(digest).decode("ascii")

    async def normalize(
        self, raw_payload: dict[str, Any], tenant_id: str, org_id: str
    ) -> OrgCommand:
        """Normalise an inbound call webhook into an :class:`OrgCommand`.

        Prefers ``SpeechResult`` (provider speech-to-text). A ``RecordingUrl`` with
        no transcript is surfaced as an empty-text command carrying the recording ref
        in ``raw_payload`` so the caller can fetch + transcribe it via the STT engine.
        """
        with _tracer.start_as_current_span("voice_phone.normalize") as span:
            command_id = str(uuid.uuid4())
            from_number = str(raw_payload.get("From", "") or "")
            to_number = str(raw_payload.get("To", "") or "")
            call_sid = str(raw_payload.get("CallSid", "") or "")
            transcript = str(
                raw_payload.get("SpeechResult")
                or raw_payload.get("transcript")
                or raw_payload.get("text")
                or ""
            ).strip()
            confidence = _as_float(raw_payload.get("Confidence"), default=1.0)

            span.set_attribute("voice_phone.call_sid", call_sid)
            span.set_attribute("voice_phone.has_speech", bool(transcript))
            span.set_attribute("voice_phone.confidence", confidence)

            return OrgCommand(
                command_id=command_id,
                tenant_id=tenant_id,
                org_id=org_id,
                text=transcript,
                actor_id=from_number,
                actor_channel=self.channel_name,
                # caller_id → conversation continuity (see module docstring).
                conversation_id=from_number,
                raw_payload={
                    **raw_payload,
                    "_call_sid": call_sid,
                    "_to": to_number,
                    "_recording_url": raw_payload.get("RecordingUrl"),
                    "_confidence": confidence,
                },
            )

    def format_response(self, response: OrgResponse) -> dict[str, Any]:
        """Format an :class:`OrgResponse` as TwiML-like TTS instructions (ABC entry).

        Uses ``voice_text`` when present (a TTS-optimised variant), else ``text``.
        """
        return self.format_reply(response.voice_text or response.text)

    def format_reply(self, reply_text: str, *, gather: bool = True) -> dict[str, Any]:
        """Build TwiML-like TTS instructions for a plain reply string.

        Returns both a structured dict (``say`` / ``gather``) and a ready-to-serve
        ``twiml`` XML string. When ``gather`` is true the ``<Gather>`` collects the
        caller's next spoken turn and posts it back to this webhook, keeping the call
        a continuous conversation mapped to the same chat session.
        """
        text = _trim_for_tts(reply_text)
        safe = escape(text)
        if gather:
            twiml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<Response>"
                '<Gather input="speech" method="POST">'
                f"<Say>{safe}</Say>"
                "</Gather>"
                "</Response>"
            )
        else:
            twiml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                f"<Response><Say>{safe}</Say></Response>"
            )
        return {"say": text, "gather": gather, "twiml": twiml}

    async def place_call(
        self,
        *,
        to: str,
        from_: str | None = None,
        client: Any = None,
        url: str | None = None,
        twiml: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Initiate an OUTBOUND call — the agent phoning ``to`` to do a task.

        ``client`` is an injectable telephony client (mockable); it must expose the
        Twilio-style ``client.calls.create(to=, from_=, url=|twiml=)``. ``url`` points
        at a webhook returning TwiML for the call; alternatively inline ``twiml`` can
        be passed. The outbound call maps to the same chat conversation as inbound
        calls with ``to`` as the caller id. Returns the created call's ``sid``/status,
        or ``None`` on failure.
        """
        from_number = from_ or self._default_from
        if client is None:
            raise RuntimeError("place_call requires an injected telephony client")
        if not from_number:
            raise ValueError("place_call requires a 'from_' number (or VOICE_PHONE_FROM)")
        create_kwargs: dict[str, Any] = {"to": to, "from_": from_number, **kwargs}
        if url is not None:
            create_kwargs["url"] = url
        if twiml is not None:
            create_kwargs["twiml"] = twiml
        try:
            call = await _maybe_await(client.calls.create(**create_kwargs))
        except Exception as exc:  # surface as a soft failure, like peer adapters
            _log.warning("voice_phone.place_call.failed", to=to, error=str(exc))
            return None
        sid = getattr(call, "sid", None) or (
            call.get("sid") if isinstance(call, dict) else None
        )
        status = getattr(call, "status", None) or (
            call.get("status") if isinstance(call, dict) else "queued"
        )
        _log.info("voice_phone.call_placed", to=to, sid=sid)
        return {"sid": sid, "status": status, "to": to, "from": from_number}


def _trim_for_tts(text: str, max_chars: int = _MAX_TTS_CHARS) -> str:
    """Trim to a TTS-friendly length, ending on a sentence boundary when possible."""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    trimmed = text[:max_chars]
    last_dot = trimmed.rfind(".")
    return trimmed[: last_dot + 1] if last_dot > 0 else trimmed


def _as_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def _maybe_await(value: Any) -> Any:
    import inspect

    if inspect.isawaitable(value):
        return await value
    return value
