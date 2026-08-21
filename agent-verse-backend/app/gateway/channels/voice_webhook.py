"""Voice webhook adapter — Q6 of spec.

Handles inbound voice webhook events from transcription services.
Voice notes are first transcribed externally (Whisper, AssemblyAI, etc.)
then POSTed to this endpoint as JSON with `transcript` field.

Setup: VOICE_WEBHOOK_SECRET for HMAC verification.
Webhook URL: POST /v1/gateway/{org_id}/voice/webhook
"""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from typing import Any

import structlog
from opentelemetry import trace

from app.gateway.channels.base import ChannelAdapter
from app.gateway.command import OrgCommand, OrgResponse

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


class VoiceWebhookAdapter(ChannelAdapter):
    """
    Voice command adapter — receives pre-transcribed voice input.

    Typical flow:
      1. User sends voice note (Telegram/WhatsApp/phone)
      2. External STT service (Whisper / AssemblyAI) transcribes
      3. Transcription POSTed here as JSON
      4. Text → OrgCommand → processed normally
    """

    channel_name = "voice_webhook"

    def __init__(self, webhook_secret: str | None = None) -> None:
        self._secret = webhook_secret or os.getenv("VOICE_WEBHOOK_SECRET", "")

    async def verify_auth(
        self, request_headers: dict[str, str], raw_payload: dict[str, Any]
    ) -> bool:
        """Verify HMAC-SHA256 signature on the payload."""
        if not self._secret:
            return True  # no secret = dev mode
        sig = request_headers.get("X-Voice-Signature", "")
        body_str = raw_payload.get("_raw_body", "")
        expected = hmac.new(self._secret.encode(), body_str.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, f"sha256={expected}")

    async def normalize(
        self, raw_payload: dict[str, Any], tenant_id: str, org_id: str
    ) -> OrgCommand:
        with _tracer.start_as_current_span("voice_webhook.normalize") as span:
            command_id = str(uuid.uuid4())
            transcript = (
                raw_payload.get("transcript")
                or raw_payload.get("text")
                or raw_payload.get("transcription", "")
            ).strip()
            actor_id = raw_payload.get("user_id", "voice_user")
            actor_name = raw_payload.get("user_name")
            origin_chan = raw_payload.get("origin_channel", "voice")  # e.g. "telegram_voice"
            urgency = raw_payload.get("urgency", "normal")
            confidence = raw_payload.get("confidence", 1.0)

            if confidence < 0.70:
                # Low confidence transcript — add note to prompt clarification
                transcript = f"[Low-confidence transcript, please confirm] {transcript}"

            span.set_attribute("voice.confidence", confidence)
            span.set_attribute("voice.origin", origin_chan)
            span.set_attribute("voice.transcript_len", len(transcript))

            return OrgCommand(
                command_id=command_id,
                tenant_id=tenant_id,
                org_id=org_id,
                text=transcript,
                actor_id=actor_id,
                actor_name=actor_name,
                actor_channel=origin_chan,
                urgency=urgency,
                raw_payload=raw_payload,
            )

    def format_response(self, response: OrgResponse) -> dict[str, Any]:
        """Return a TTS-optimised response (shorter, conversational)."""
        voice_text = response.voice_text or self._trim_for_tts(response.text)
        return {
            "text": voice_text,
            "ssml": f"<speak>{voice_text}</speak>",
            "tts_engine": "auto",
        }

    @staticmethod
    def _trim_for_tts(text: str, max_chars: int = 500) -> str:
        """Trim text to TTS-friendly length, ending at sentence boundary."""
        if len(text) <= max_chars:
            return text
        trimmed = text[:max_chars]
        last_dot = trimmed.rfind(".")
        return trimmed[: last_dot + 1] if last_dot > 0 else trimmed
