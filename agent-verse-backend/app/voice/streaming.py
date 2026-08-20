"""Real-time bidirectional voice session over WebSocket.

STT → Intent Router → TTS pipeline.

VERIFIED API calls:
  - transcribe() from stt_engine (wraps provider)
  - route_voice_command() from intent_router (uses real OrgService)
  - synthesize_streaming() from tts_engine (wraps provider)
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
from typing import Any

import numpy as np
import soundfile as sf
from fastapi import WebSocket, WebSocketDisconnect
from opentelemetry import trace

from app.voice.intent_router import route_voice_command
from app.voice.stt_engine import transcribe
from app.voice.tts_engine import synthesize_streaming

log    = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


class VoiceStreamingSession:
    """Manages one WebSocket voice session — full STT → IntentRouter → TTS pipeline."""

    def __init__(
        self,
        ws: WebSocket,
        *,
        tenant_id: str,
        org_id: str,
        session_factory: Any,
        ref_audio: bytes | None = None,
        ref_text: str | None   = None,
        language: str          = "en",
    ) -> None:
        self.ws              = ws
        self.tenant_id       = tenant_id
        self.org_id          = org_id
        self._sf             = session_factory
        self.ref_audio       = ref_audio
        self.ref_text        = ref_text
        self.language        = language
        self._buf: list[np.ndarray] = []
        self._active         = True
        self._turns          = 0
        self._pending_decision_id: str | None = None   # D-4: voice approval

    async def run(self) -> None:
        with tracer.start_as_current_span("voice.stream.session") as span:
            span.set_attribute("tenant_id", self.tenant_id)
            span.set_attribute("org_id", self.org_id)
            try:
                await self._loop()
            except WebSocketDisconnect:
                log.info("voice.stream.disconnected tenant=%s", self.tenant_id)
            except Exception as exc:
                log.error("voice.stream.error", exc_info=exc)
                await self._send({"type": "error", "code": "internal", "detail": str(exc)})
            finally:
                span.set_attribute("turns", self._turns)
                await self._send({"type": "session_end"})

    async def _loop(self) -> None:
        async for raw in self.ws.iter_text():
            if not self._active:
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")
            if mtype == "audio_chunk":
                try:
                    pcm = np.frombuffer(
                        base64.b64decode(msg["data"]), dtype=np.int16
                    ).astype(np.float32) / 32768.0
                    self._buf.append(pcm)
                    if sum(len(c) for c in self._buf) >= 16_000:
                        await self._emit_interim()
                except Exception as exc:
                    log.debug("voice.stream.chunk_error error=%s", exc)
            elif mtype == "end_of_speech":
                await self._run_pipeline()
            elif mtype == "set_pending_decision":
                self._pending_decision_id = msg.get("decision_id")
            elif mtype == "cancel":
                self._buf.clear()
                self._active = False

    async def _emit_interim(self) -> None:
        """Send interim (non-final) transcript while user is still speaking."""
        if not self._buf:
            return
        audio  = np.concatenate(self._buf)
        wav    = self._to_wav(audio)
        result = await transcribe(wav, "audio/wav")
        await self._send({
            "type": "transcript", "text": result["transcript"],
            "language": result["language"], "confidence": result["confidence"],
            "is_final": False,
        })

    async def _run_pipeline(self) -> None:
        """Process complete utterance: STT → IntentRouter → TTS."""
        if not self._buf:
            return
        audio     = np.concatenate(self._buf)
        self._buf.clear()
        if len(audio) < 800:  # < 50 ms — skip noise
            return

        wav        = self._to_wav(audio)
        result     = await transcribe(wav, "audio/wav")
        transcript = result["transcript"].strip()
        if not transcript:
            return

        await self._send({
            "type": "transcript", "text": transcript,
            "language": result["language"], "confidence": result["confidence"],
            "is_final": True,
        })
        await self._send({"type": "agent_thinking"})

        # D-3: Intent router dispatches to real OrgService operations
        try:
            response = await route_voice_command(
                transcript,
                org_id=self.org_id,
                tenant_id=self.tenant_id,
                session_factory=self._sf,
                pending_decision_id=self._pending_decision_id,
            )
        except Exception as exc:
            log.error("voice.stream.route_error", exc_info=exc)
            response = "I'm sorry, something went wrong. Please try again."

        self._turns += 1
        await self._send({"type": "agent_response", "text": response})

        # TTS streaming — send PCM16 chunks back to browser
        try:
            async for chunk in synthesize_streaming(
                response[:2048],
                ref_audio=self.ref_audio,
                ref_text=self.ref_text,
                language=self.language,
            ):
                await self._send({
                    "type": "tts_chunk",
                    "data": base64.b64encode(chunk).decode(),
                })
        except Exception as exc:
            log.error("voice.stream.tts_error", exc_info=exc)
        await self._send({"type": "tts_done"})

    async def _send(self, payload: dict) -> None:
        try:
            await self.ws.send_text(json.dumps(payload))
        except Exception:
            self._active = False

    @staticmethod
    def _to_wav(audio: np.ndarray, sr: int = 16_000) -> bytes:
        buf = io.BytesIO()
        sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
        return buf.getvalue()
