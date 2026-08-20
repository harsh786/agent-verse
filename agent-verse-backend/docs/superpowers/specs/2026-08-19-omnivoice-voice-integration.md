# OmniVoice Native Voice Integration — World-Class Specification

**Status:** Draft  
**Date:** 2026-08-19  
**Author:** Platform Engineering  
**Model:** [k2-fsa/OmniVoice](https://huggingface.co/k2-fsa/OmniVoice) · [arXiv:2604.00688](https://arxiv.org/abs/2604.00688)

---

## 1. Executive Summary

This document specifies a ground-up replacement of AgentVerse's stub voice
layer with a fully native, **open-source**, GPU/CPU-ready voice stack powered by:

| Capability | Engine | Model size |
|---|---|---|
| Text-to-Speech (TTS) | **OmniVoice** (`k2-fsa/OmniVoice`) | 0.6 B params |
| Speech-to-Text (STT) | **faster-whisper** (CTranslate2) | `large-v3-turbo` ≈ 809 MB |
| Real-time streaming | FastAPI **WebSocket** endpoint | — |
| Login greeting | OmniVoice TTS + morning-brief API | — |

**Zero external API cost.** All inference runs in-process on the same worker
pod. No Whisper API key, no AssemblyAI subscription, no ElevenLabs.

### Delivered capabilities

| # | Feature | Description |
|---|---|---|
| V-1 | **Native STT** | Upload or stream audio → `faster-whisper` → transcript in-process |
| V-2 | **Native TTS** | Text → OmniVoice → streamed PCM/WAV audio back to browser |
| V-3 | **Real-time WebSocket voice** | Bidirectional continuous voice session (STT + LLM + TTS pipeline) |
| V-4 | **Login greeting** | On every login, OmniVoice narrates the org's daily stats dashboard |
| V-5 | **Voice-cloned org persona** | Each org can upload a 5-10 s reference audio; responses are spoken in that voice |
| V-6 | **Multi-language** | 600+ languages via OmniVoice; language auto-detected from transcript |
| V-7 | **Non-verbal controls** | `[laughter]`, `[pause]`, `[whisper]` via OmniVoice fine-grained tokens |
| V-8 | **Tenant voice preferences** | Per-tenant: voice_id, speed, pitch, reference audio, language |

---

## 2. Architecture Overview

```
Browser
  │
  ├─ HTTP POST /v1/voice/transcribe (audio blob → transcript)       [V-1]
  ├─ HTTP POST /v1/voice/speak      (text → audio stream)           [V-2]
  ├─ HTTP GET  /v1/voice/greeting/{org_id} (login narration audio)  [V-4]
  ├─ HTTP GET/POST /v1/voice/persona (upload reference audio)       [V-5]
  │
  └─ WebSocket  /v1/voice/stream/{org_id}                           [V-3]
       Client sends: chunks of PCM16 (16 kHz, mono)
       Server sends: JSON events + PCM16 chunks interleaved
         { type: "transcript", text: "...", is_final: true }
         { type: "agent_response", text: "..." }
         { type: "tts_chunk", data: "<base64 PCM>" }
         { type: "tts_done" }

Backend (FastAPI worker)
  │
  ├─ app/voice/
  │     ├─ __init__.py
  │     ├─ router.py            (FastAPI routes)
  │     ├─ service.py           (orchestration logic)
  │     ├─ stt_engine.py        (faster-whisper wrapper)
  │     ├─ tts_engine.py        (OmniVoice wrapper)
  │     ├─ streaming.py         (WebSocket session manager)
  │     ├─ greeting.py          (login greeting builder)
  │     ├─ persona.py           (per-org voice persona store)
  │     ├─ schemas.py           (Pydantic models)
  │     ├─ models.py            (SQLAlchemy ORM)
  │     └─ exceptions.py
  │
  ├─ app/db/models/voice.py     (voice_sessions, voice_personas tables)
  └─ app/db/migrations/NNNN_add_voice_tables.py

Object Storage (S3/MinIO)
  └─ voice-personas/{tenant_id}/{org_id}/ref.wav
```

### Inference isolation

Each worker pod loads models lazily at first use and keeps them in memory:

```
process memory
  ├─ STT: WhisperModel("large-v3-turbo", device="cuda"|"cpu")
  └─ TTS: OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map=...)
```

Model loading is protected by an `asyncio.Lock` to prevent thundering herd on
cold start. GPU VRAM budget: ~4 GB for both models on a single A10G.

---

## 3. Backend Implementation

### 3.1 New Python dependencies (`pyproject.toml`)

```toml
[project.dependencies]
# Voice stack
faster-whisper     = ">=1.1.0"    # CTranslate2-based Whisper (CPU/GPU)
omnivoice          = ">=0.1.0"    # k2-fsa TTS model
soundfile          = ">=0.12.1"   # audio I/O
numpy              = ">=1.26"     # audio array handling
torch              = ">=2.2.0"    # OmniVoice dep (already likely present)
torchaudio         = ">=2.2.0"    # resampling helpers
boto3              = ">=1.34"     # S3/MinIO for persona audio storage
```

> `torch` + `torchaudio` are GPU-pinned in the Dockerfile (CUDA 12.1). CPU-only
> workers use the standard PyPI wheels.

### 3.2 `app/voice/stt_engine.py` — Native STT

```python
"""Native STT using faster-whisper (CTranslate2).

Thread-safe singleton — model loads once per worker process.
"""
from __future__ import annotations

import asyncio
import io
import logging
from functools import lru_cache
from typing import Any

import numpy as np
import soundfile as sf
from opentelemetry import trace

log = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)
_lock = asyncio.Lock()
_model: Any | None = None


async def get_model() -> Any:
    """Return cached WhisperModel, loading it on first call."""
    global _model
    if _model is not None:
        return _model
    async with _lock:
        if _model is not None:  # double-checked
            return _model
        log.info("voice.stt.loading model large-v3-turbo")
        from faster_whisper import WhisperModel
        import os
        device = "cuda" if os.getenv("VOICE_DEVICE", "cpu") == "cuda" else "cpu"
        compute = "float16" if device == "cuda" else "int8"
        _model = WhisperModel("large-v3-turbo", device=device, compute_type=compute)
        log.info("voice.stt.model_ready", device=device, compute=compute)
        return _model


async def transcribe(audio_bytes: bytes, content_type: str) -> dict:
    """Transcribe raw audio bytes to text.

    Returns:
        {
            "transcript": str,
            "language": str,
            "confidence": float,      # mean log-prob → [0,1] mapped
            "segments": list[dict],
        }
    """
    with tracer.start_as_current_span("voice.stt.transcribe") as span:
        model = await get_model()
        span.set_attribute("audio_bytes", len(audio_bytes))

        # Decode to numpy float32 mono 16 kHz
        buf = io.BytesIO(audio_bytes)
        try:
            audio_np, sample_rate = sf.read(buf, dtype="float32", always_2d=False)
        except Exception:
            # webm/opus — convert via ffmpeg-python or torchaudio
            audio_np, sample_rate = _decode_fallback(audio_bytes)

        if audio_np.ndim > 1:
            audio_np = audio_np.mean(axis=1)  # stereo → mono

        if sample_rate != 16_000:
            import torchaudio
            import torch
            t = torch.from_numpy(audio_np).unsqueeze(0)
            resampler = torchaudio.transforms.Resample(sample_rate, 16_000)
            audio_np = resampler(t).squeeze().numpy()

        loop = asyncio.get_event_loop()
        segments, info = await loop.run_in_executor(
            None,
            lambda: model.transcribe(
                audio_np,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
            ),
        )
        seg_list = list(segments)  # consume generator in executor thread

        full_text = " ".join(s.text.strip() for s in seg_list)
        avg_logprob = np.mean([s.avg_logprob for s in seg_list]) if seg_list else -1.0
        confidence = float(np.clip(np.exp(avg_logprob), 0.0, 1.0))

        span.set_attribute("language", info.language)
        span.set_attribute("confidence", confidence)
        span.set_attribute("transcript_len", len(full_text))

        return {
            "transcript": full_text,
            "language": info.language,
            "confidence": confidence,
            "segments": [
                {
                    "start": s.start,
                    "end": s.end,
                    "text": s.text,
                }
                for s in seg_list
            ],
        }


def _decode_fallback(audio_bytes: bytes):
    """Decode webm/opus/mp4 via torchaudio FFmpeg backend."""
    import io
    import torchaudio
    buf = io.BytesIO(audio_bytes)
    waveform, sr = torchaudio.load(buf, format=None)
    return waveform.squeeze().numpy(), sr
```

### 3.3 `app/voice/tts_engine.py` — OmniVoice TTS

```python
"""Native TTS using OmniVoice (k2-fsa/OmniVoice).

Provides:
  - synthesize(): text → WAV bytes
  - synthesize_streaming(): text → async generator of PCM chunks
  - warmup(): preloads model at worker startup
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
from typing import AsyncGenerator, Any

import numpy as np
import soundfile as sf
from opentelemetry import trace

log = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)
_lock = asyncio.Lock()
_model: Any | None = None

SAMPLE_RATE = 24_000   # OmniVoice native output rate
CHUNK_FRAMES = 4_800   # 200 ms chunks at 24 kHz


async def get_model() -> Any:
    global _model
    if _model is not None:
        return _model
    async with _lock:
        if _model is not None:
            return _model
        log.info("voice.tts.loading OmniVoice")
        from omnivoice import OmniVoice
        import torch
        device = os.getenv("VOICE_DEVICE", "cpu")
        dtype = torch.float16 if device == "cuda" else torch.float32
        _model = OmniVoice.from_pretrained(
            "k2-fsa/OmniVoice",
            device_map=device,
            dtype=dtype,
            cache_dir=os.getenv("MODEL_CACHE_DIR", "/app/models"),
        )
        log.info("voice.tts.model_ready", device=device)
        return _model


async def synthesize(
    text: str,
    *,
    ref_audio: bytes | None = None,
    ref_text: str | None = None,
    language: str = "en",
    speed: float = 1.0,
) -> bytes:
    """Return complete WAV bytes for the given text.

    Args:
        text:       Text to synthesise. Supports non-verbal tokens: [laughter] etc.
        ref_audio:  Optional WAV bytes for zero-shot voice cloning.
        ref_text:   Transcript of ref_audio (required when ref_audio is set).
        language:   BCP-47 language code e.g. 'en', 'fr', 'hi'.
        speed:      Playback speed multiplier (0.5 – 2.0).
    """
    with tracer.start_as_current_span("voice.tts.synthesize") as span:
        model = await get_model()
        span.set_attribute("text_len", len(text))
        span.set_attribute("language", language)
        span.set_attribute("has_ref_audio", ref_audio is not None)

        kwargs: dict = {"text": text}
        if ref_audio and ref_text:
            # Write ref to tmp buffer; OmniVoice expects a file path or np array
            ref_np, ref_sr = _wav_bytes_to_np(ref_audio)
            kwargs["ref_audio"] = ref_np
            kwargs["ref_text"] = ref_text

        loop = asyncio.get_event_loop()
        audio_arrays: list[np.ndarray] = await loop.run_in_executor(
            None,
            lambda: model.generate(**kwargs),
        )
        audio = audio_arrays[0]

        # Apply speed scaling via resampling
        if abs(speed - 1.0) > 0.01:
            target_sr = int(SAMPLE_RATE / speed)
            import torchaudio, torch
            t = torch.from_numpy(audio).unsqueeze(0)
            audio = torchaudio.functional.resample(t, target_sr, SAMPLE_RATE).squeeze().numpy()

        buf = io.BytesIO()
        sf.write(buf, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        span.set_attribute("output_bytes", buf.tell())
        return buf.getvalue()


async def synthesize_streaming(
    text: str,
    *,
    ref_audio: bytes | None = None,
    ref_text: str | None = None,
    language: str = "en",
) -> AsyncGenerator[bytes, None]:
    """Yield PCM16 audio chunks as they are generated.

    Because OmniVoice generates the full array (not a true streaming model),
    we synthesise and then slice into 200 ms PCM16 chunks for WebSocket delivery.
    """
    wav_bytes = await synthesize(
        text,
        ref_audio=ref_audio,
        ref_text=ref_text,
        language=language,
    )
    # Strip 44-byte WAV header, emit raw PCM16 chunks
    pcm = wav_bytes[44:]
    pos = 0
    chunk_bytes = CHUNK_FRAMES * 2  # int16 = 2 bytes/sample
    while pos < len(pcm):
        yield pcm[pos : pos + chunk_bytes]
        pos += chunk_bytes
        await asyncio.sleep(0)   # yield event loop between chunks


async def warmup() -> None:
    """Pre-load TTS model at worker startup (called from lifespan)."""
    log.info("voice.tts.warmup started")
    await get_model()
    # Synthesise a silent warmup phrase to JIT-compile the graph
    await synthesize(".", language="en")
    log.info("voice.tts.warmup complete")


def _wav_bytes_to_np(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    buf = io.BytesIO(wav_bytes)
    audio, sr = sf.read(buf, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio, sr
```

### 3.4 `app/voice/greeting.py` — Login Narration

```python
"""Voice greeting builder — spoken on login for each org.

Flow:
  1. Fetch morning-brief stats for org (existing /v1/org/{org_id}/brief/morning)
  2. Build a natural-language narration script
  3. Synthesise via OmniVoice (with org persona voice if configured)
  4. Return WAV bytes (cached in Redis for 5 minutes)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import structlog
from opentelemetry import trace

from app.voice.tts_engine import synthesize

log = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

_GREETING_TEMPLATES = {
    "healthy": (
        "Good {time_of_day}, {user_name}. "
        "{org_name} is running smoothly. "
        "You have {active_missions} active missions and {active_teams} teams engaged. "
        "{pending_approvals} items are waiting for your approval. "
        "Today's top priority: {top_priority}."
    ),
    "degraded": (
        "Good {time_of_day}, {user_name}. "
        "Heads up — {org_name} has {items_needing_attention} items needing attention. "
        "{active_missions} missions are running across {active_teams} teams. "
        "Please review {pending_approvals} pending approvals. "
        "Critical alert: {top_risk}."
    ),
    "attention_needed": (
        "Good {time_of_day}, {user_name}. "
        "{org_name} requires your attention right now. "
        "{items_needing_attention} critical issues have been flagged. "
        "{active_missions} missions are in flight. "
        "Immediate action needed: {top_risk}."
    ),
}


async def build_greeting_script(
    brief: dict,
    user_name: str,
) -> str:
    """Render a natural-language greeting from morning brief data."""
    hour = datetime.now(timezone.utc).hour
    if hour < 12:
        time_of_day = "morning"
    elif hour < 17:
        time_of_day = "afternoon"
    else:
        time_of_day = "evening"

    health = brief.get("overall_health", "healthy")
    template = _GREETING_TEMPLATES.get(health, _GREETING_TEMPLATES["healthy"])

    priorities = brief.get("priorities", [])
    risks = brief.get("risks", [])
    top_priority = priorities[0]["title"] if priorities else "continue monitoring"
    top_risk = risks[0]["description"] if risks else "no critical risks detected"

    script = template.format(
        time_of_day=time_of_day,
        user_name=user_name.split()[0] if user_name else "there",
        org_name=brief.get("org_name", "your organization"),
        active_missions=brief.get("active_missions", 0),
        active_teams=brief.get("active_teams", 0),
        pending_approvals=brief.get("pending_approvals", 0),
        items_needing_attention=brief.get("items_needing_attention", 0),
        top_priority=top_priority,
        top_risk=top_risk,
    )
    return script


async def synthesize_greeting(
    brief: dict,
    user_name: str,
    ref_audio: bytes | None = None,
    ref_text: str | None = None,
    language: str = "en",
) -> bytes:
    """Return WAV bytes for the login greeting narration."""
    with tracer.start_as_current_span("voice.greeting.synthesize") as span:
        span.set_attribute("org_id", brief.get("org_id", ""))
        span.set_attribute("health", brief.get("overall_health", ""))

        script = await build_greeting_script(brief, user_name)
        span.set_attribute("script_len", len(script))

        wav_bytes = await synthesize(
            script,
            ref_audio=ref_audio,
            ref_text=ref_text,
            language=language,
        )
        log.info(
            "voice.greeting.synthesized",
            org_id=brief.get("org_id"),
            health=brief.get("overall_health"),
            wav_bytes=len(wav_bytes),
        )
        return wav_bytes
```

### 3.5 `app/voice/streaming.py` — WebSocket Real-Time Session

```python
"""WebSocket voice streaming session — real-time STT → LLM → TTS pipeline.

Protocol (JSON envelopes over WebSocket binary/text frames):
  Client → Server:
    { "type": "audio_chunk",   "data": "<base64 PCM16 16kHz mono>" }
    { "type": "end_of_speech" }
    { "type": "cancel" }

  Server → Client:
    { "type": "transcript",      "text": "...", "is_final": false }
    { "type": "transcript",      "text": "...", "is_final": true }
    { "type": "agent_thinking" }
    { "type": "agent_response",  "text": "..." }
    { "type": "tts_chunk",       "data": "<base64 PCM24kHz>" }
    { "type": "tts_done" }
    { "type": "error",           "code": "...", "detail": "..." }
    { "type": "session_end" }
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect
from opentelemetry import trace

from app.voice.stt_engine import transcribe
from app.voice.tts_engine import synthesize_streaming

log = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

VAD_SILENCE_FRAMES = 8_000  # 0.5 s of silence @ 16 kHz


class VoiceStreamingSession:
    """Manages a single WebSocket voice session."""

    def __init__(
        self,
        ws: WebSocket,
        tenant_id: str,
        org_id: str,
        agent_respond_fn,      # async fn(transcript: str, org_id: str) -> str
        ref_audio: bytes | None = None,
        ref_text: str | None = None,
        language: str = "en",
    ) -> None:
        self.ws = ws
        self.tenant_id = tenant_id
        self.org_id = org_id
        self._agent_respond = agent_respond_fn
        self.ref_audio = ref_audio
        self.ref_text = ref_text
        self.language = language
        self._audio_buf: list[np.ndarray] = []
        self._active = True

    async def run(self) -> None:
        with tracer.start_as_current_span("voice.stream.session") as span:
            span.set_attribute("tenant_id", self.tenant_id)
            span.set_attribute("org_id", self.org_id)
            try:
                await self._main_loop()
            except WebSocketDisconnect:
                log.info("voice.stream.disconnected", tenant_id=self.tenant_id)
            except Exception as exc:
                log.error("voice.stream.error", exc_info=exc)
                await self._send({"type": "error", "code": "internal", "detail": str(exc)})
            finally:
                self._active = False
                await self._send({"type": "session_end"})

    async def _main_loop(self) -> None:
        async for message in self.ws.iter_text():
            if not self._active:
                break
            try:
                envelope = json.loads(message)
            except json.JSONDecodeError:
                continue

            msg_type = envelope.get("type")

            if msg_type == "audio_chunk":
                raw = base64.b64decode(envelope["data"])
                chunk = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                self._audio_buf.append(chunk)
                # Send interim transcript every ~1 s of audio
                if sum(len(c) for c in self._audio_buf) >= 16_000:
                    await self._flush_transcript(is_final=False)

            elif msg_type == "end_of_speech":
                await self._flush_transcript(is_final=True)
                await self._run_agent_pipeline()

            elif msg_type == "cancel":
                self._audio_buf.clear()
                self._active = False
                break

    async def _flush_transcript(self, *, is_final: bool) -> None:
        if not self._audio_buf:
            return
        audio = np.concatenate(self._audio_buf)
        import soundfile as sf, io
        buf = io.BytesIO()
        sf.write(buf, audio, 16_000, format="WAV", subtype="PCM_16")
        result = await transcribe(buf.getvalue(), "audio/wav")
        if is_final:
            self._audio_buf.clear()
        await self._send({
            "type": "transcript",
            "text": result["transcript"],
            "language": result["language"],
            "confidence": result["confidence"],
            "is_final": is_final,
        })

    async def _run_agent_pipeline(self) -> None:
        # 1. Get final transcript
        audio = np.concatenate(self._audio_buf) if self._audio_buf else np.array([], dtype=np.float32)
        self._audio_buf.clear()

        if len(audio) < 100:
            return

        import soundfile as sf, io
        buf = io.BytesIO()
        sf.write(buf, audio, 16_000, format="WAV", subtype="PCM_16")
        result = await transcribe(buf.getvalue(), "audio/wav")
        transcript = result["transcript"]
        if not transcript.strip():
            return

        await self._send({"type": "transcript", "text": transcript, "is_final": True})
        await self._send({"type": "agent_thinking"})

        # 2. Call agent / org LLM
        try:
            agent_response = await self._agent_respond(transcript, self.org_id)
        except Exception as exc:
            log.error("voice.stream.agent_error", exc_info=exc)
            agent_response = "I'm sorry, I encountered an error processing your request."

        await self._send({"type": "agent_response", "text": agent_response})

        # 3. Synthesise TTS and stream chunks
        async for chunk in synthesize_streaming(
            agent_response,
            ref_audio=self.ref_audio,
            ref_text=self.ref_text,
            language=self.language,
        ):
            await self._send({
                "type": "tts_chunk",
                "data": base64.b64encode(chunk).decode(),
            })
        await self._send({"type": "tts_done"})

    async def _send(self, payload: dict) -> None:
        try:
            await self.ws.send_text(json.dumps(payload))
        except Exception:
            self._active = False
```

### 3.6 `app/voice/router.py` — Revised Endpoints

Replace the existing stub router entirely with:

```
GET  /v1/voice/status
POST /v1/voice/transcribe         (audio upload → transcript)
POST /v1/voice/speak              (text → WAV audio stream)
GET  /v1/voice/greeting/{org_id}  (login narration audio)
POST /v1/voice/persona/{org_id}   (upload reference audio)
DELETE /v1/voice/persona/{org_id} (delete reference audio)
WS   /v1/voice/stream/{org_id}    (real-time bidirectional)
```

Full endpoint table with auth, rate limits, and response types:

| Endpoint | Auth | Rate limit | Returns |
|---|---|---|---|
| `GET /v1/voice/status` | API key | 60/min | `{"stt": "ready", "tts": "ready", "model": "large-v3-turbo"}` |
| `POST /v1/voice/transcribe` | API key | 30/min | `TranscribeResponse` |
| `POST /v1/voice/speak` | API key | 20/min | `audio/wav` (streaming) |
| `GET /v1/voice/greeting/{org_id}` | API key | 10/min | `audio/wav` |
| `POST /v1/voice/persona/{org_id}` | API key | 5/min | `PersonaResponse` |
| `DELETE /v1/voice/persona/{org_id}` | API key | 5/min | `204 No Content` |
| `WS /v1/voice/stream/{org_id}` | API key (query param) | 5 concurrent/tenant | WebSocket |

### 3.7 `app/voice/schemas.py`

```python
from __future__ import annotations
from pydantic import BaseModel, Field


class VoiceStatusResponse(BaseModel):
    stt_status: str     # "ready" | "loading" | "error"
    tts_status: str
    stt_model: str = "large-v3-turbo"
    tts_model: str = "k2-fsa/OmniVoice"
    device: str         # "cpu" | "cuda"


class TranscribeResponse(BaseModel):
    transcript: str
    language: str
    confidence: float = Field(ge=0.0, le=1.0)
    segments: list[dict] = Field(default_factory=list)
    duration_s: float = 0.0


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    language: str = "en"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    nonverbal_tokens: list[str] = Field(default_factory=list,
        description="Inject non-verbal tokens e.g. ['[laughter]', '[pause]']")
    use_org_persona: bool = Field(default=True,
        description="Use org-uploaded reference voice if available")


class GreetingRequest(BaseModel):
    user_name: str = Field(min_length=1, max_length=120)
    language: str = "en"


class PersonaResponse(BaseModel):
    org_id: str
    tenant_id: str
    ref_audio_url: str
    ref_text: str
    language: str
    created_at: str
```

### 3.8 Database: `app/db/models/voice.py`

```python
from __future__ import annotations
from sqlalchemy import Column, String, Text, Boolean, DateTime, Float, func
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base
import uuid


class VoicePersona(Base):
    """Per-org reference audio for OmniVoice zero-shot cloning."""
    __tablename__ = "voice_personas"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id   = Column(UUID(as_uuid=True), nullable=False, index=True)
    org_id      = Column(String(64), nullable=False, index=True)
    ref_audio_s3_key = Column(String(512), nullable=False)
    ref_text    = Column(Text, nullable=False)
    language    = Column(String(16), nullable=False, server_default="en")
    created_at  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(),
                         onupdate=func.now(), nullable=False)


class VoiceSession(Base):
    """Audit log of voice streaming sessions."""
    __tablename__ = "voice_sessions"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id   = Column(UUID(as_uuid=True), nullable=False, index=True)
    org_id      = Column(String(64), nullable=False, index=True)
    duration_s  = Column(Float, nullable=True)
    stt_chars   = Column(Float, nullable=True)
    tts_chars   = Column(Float, nullable=True)
    turns       = Column(Float, nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(),
                         onupdate=func.now(), nullable=False)
```

### 3.9 Worker startup (`app/main.py` lifespan)

Add to the existing lifespan block:

```python
# Voice engine warmup (non-blocking — runs concurrently with other inits)
if settings.VOICE_ENABLED:
    import asyncio
    from app.voice.tts_engine import warmup as tts_warmup
    asyncio.create_task(tts_warmup())
```

### 3.10 Settings (`app/core/config.py`) — New env vars

```python
VOICE_ENABLED: bool = True
VOICE_DEVICE: str = "cpu"          # "cpu" | "cuda"
VOICE_STT_MODEL: str = "large-v3-turbo"
VOICE_TTS_MODEL: str = "k2-fsa/OmniVoice"
MODEL_CACHE_DIR: str = "/app/models"
VOICE_PERSONA_BUCKET: str = "agentverse-voice-personas"
VOICE_GREETING_CACHE_TTL: int = 300   # seconds
VOICE_MAX_CONCURRENT_STREAMS: int = 5  # per tenant
VOICE_MAX_AUDIO_MB: int = 25
```

---

## 4. Frontend Implementation

### 4.1 New files

```
src/
├─ lib/
│   └─ voice/
│       ├─ useVoiceStream.ts       # WebSocket streaming hook
│       ├─ useVoiceTTS.ts          # TTS audio playback hook
│       └─ useLoginGreeting.ts     # Login narration hook
├─ components/
│   └─ voice/
│       ├─ VoiceGoalInput.tsx      # existing — extend with native STT
│       ├─ VoiceStreamWidget.tsx   # new — real-time voice session
│       └─ LoginGreetingPlayer.tsx # new — auto-plays on login
└─ features/
    └─ org/
        └─ components/
            └─ VoiceModal.tsx      # existing — wire up WebSocket backend
```

### 4.2 `src/lib/voice/useVoiceStream.ts` — WebSocket Hook

```typescript
/**
 * useVoiceStream — manages the real-time WebSocket voice session.
 *
 * State machine:
 *   idle → connecting → listening → processing → speaking → idle
 *                                              └─ error ──────┘
 */
import { useCallback, useEffect, useRef, useState } from 'react';

export type VoiceStreamState =
  | 'idle'
  | 'connecting'
  | 'listening'
  | 'processing'
  | 'speaking'
  | 'error';

export interface VoiceStreamEvent {
  type: 'transcript' | 'agent_response' | 'tts_chunk' | 'tts_done' | 'error' | 'agent_thinking';
  text?: string;
  data?: string;         // base64 PCM for tts_chunk
  is_final?: boolean;
  confidence?: number;
  language?: string;
}

interface UseVoiceStreamOptions {
  orgId: string;
  apiKey: string;
  onTranscript?: (text: string, isFinal: boolean) => void;
  onAgentResponse?: (text: string) => void;
  onTTSChunk?: (pcmChunk: ArrayBuffer) => void;
  onTTSDone?: () => void;
  onError?: (msg: string) => void;
}

const PCM_SAMPLE_RATE = 16_000;
const CHUNK_DURATION_MS = 100;
const CHUNK_FRAMES = Math.floor((PCM_SAMPLE_RATE * CHUNK_DURATION_MS) / 1000);

export function useVoiceStream(opts: UseVoiceStreamOptions) {
  const [state, setState] = useState<VoiceStreamState>('idle');
  const wsRef = useRef<WebSocket | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const ttsQueueRef = useRef<ArrayBuffer[]>([]);
  const ttsPlayingRef = useRef(false);

  const connect = useCallback(async () => {
    setState('connecting');
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const host = import.meta.env.VITE_API_BASE_URL?.replace(/^https?/, '') || '//localhost:8000';
    const url = `${protocol}:${host}/v1/voice/stream/${opts.orgId}?api_key=${opts.apiKey}`;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => setState('listening');
    ws.onerror = () => {
      setState('error');
      opts.onError?.('WebSocket connection failed');
    };
    ws.onclose = () => {
      if (state !== 'idle') setState('idle');
    };
    ws.onmessage = (ev) => {
      const event: VoiceStreamEvent = JSON.parse(ev.data as string);
      handleServerEvent(event);
    };
  }, [opts.orgId, opts.apiKey]);

  const handleServerEvent = (event: VoiceStreamEvent) => {
    switch (event.type) {
      case 'transcript':
        opts.onTranscript?.(event.text ?? '', event.is_final ?? false);
        break;
      case 'agent_thinking':
        setState('processing');
        break;
      case 'agent_response':
        opts.onAgentResponse?.(event.text ?? '');
        setState('speaking');
        break;
      case 'tts_chunk': {
        const raw = base64ToArrayBuffer(event.data ?? '');
        ttsQueueRef.current.push(raw);
        opts.onTTSChunk?.(raw);
        if (!ttsPlayingRef.current) playNextChunk();
        break;
      }
      case 'tts_done':
        opts.onTTSDone?.();
        setState('listening');
        break;
      case 'error':
        opts.onError?.(event.text ?? 'Unknown error');
        setState('error');
        break;
    }
  };

  const startMic = useCallback(async () => {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { sampleRate: PCM_SAMPLE_RATE, channelCount: 1, echoCancellation: true },
    });
    mediaStreamRef.current = stream;

    const ctx = new AudioContext({ sampleRate: PCM_SAMPLE_RATE });
    audioCtxRef.current = ctx;

    await ctx.audioWorklet.addModule('/audio-worklet-processor.js');
    const source = ctx.createMediaStreamSource(stream);
    const worklet = new AudioWorkletNode(ctx, 'pcm-capture-processor');
    workletRef.current = worklet;

    worklet.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) return;
      const b64 = arrayBufferToBase64(e.data);
      wsRef.current.send(JSON.stringify({ type: 'audio_chunk', data: b64 }));
    };

    source.connect(worklet);
    worklet.connect(ctx.destination);
  }, []);

  const stopMic = useCallback(() => {
    mediaStreamRef.current?.getTracks().forEach(t => t.stop());
    workletRef.current?.disconnect();
    audioCtxRef.current?.close();
    wsRef.current?.send(JSON.stringify({ type: 'end_of_speech' }));
  }, []);

  const disconnect = useCallback(() => {
    stopMic();
    wsRef.current?.close();
    setState('idle');
  }, [stopMic]);

  const playNextChunk = () => {
    const queue = ttsQueueRef.current;
    if (queue.length === 0) { ttsPlayingRef.current = false; return; }
    ttsPlayingRef.current = true;
    const chunk = queue.shift()!;
    // Convert PCM24kHz int16 → AudioBuffer → play
    const ctx = new AudioContext({ sampleRate: 24_000 });
    const int16 = new Int16Array(chunk);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768;
    const audioBuf = ctx.createBuffer(1, float32.length, 24_000);
    audioBuf.copyToChannel(float32, 0);
    const src = ctx.createBufferSource();
    src.buffer = audioBuf;
    src.connect(ctx.destination);
    src.onended = playNextChunk;
    src.start();
  };

  return { state, connect, startMic, stopMic, disconnect };
}

function base64ToArrayBuffer(b64: string): ArrayBuffer {
  const bin = atob(b64);
  const buf = new ArrayBuffer(bin.length);
  const view = new Uint8Array(buf);
  for (let i = 0; i < bin.length; i++) view[i] = bin.charCodeAt(i);
  return buf;
}

function arrayBufferToBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let binary = '';
  for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}
```

### 4.3 `src/lib/voice/useLoginGreeting.ts` — Login Narration Hook

```typescript
/**
 * useLoginGreeting — fetches and auto-plays the OmniVoice login greeting.
 *
 * Triggers once per session on first org page load.
 * Respects prefers-reduced-motion and user mute preference.
 */
import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

interface UseLoginGreetingOptions {
  orgId: string;
  userName: string;
  language?: string;
  enabled?: boolean;
}

export function useLoginGreeting({
  orgId,
  userName,
  language = 'en',
  enabled = true,
}: UseLoginGreetingOptions) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [hasPlayed, setHasPlayed] = useState(() => {
    // Only greet once per browser session
    return sessionStorage.getItem(`greeting-played-${orgId}`) === '1';
  });
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const prefersReducedMotion =
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const { data: audioBlob } = useQuery<Blob>({
    queryKey: ['voice-greeting', orgId, userName, language],
    queryFn: async () => {
      const res = await fetch(
        `/api/v1/voice/greeting/${orgId}?user_name=${encodeURIComponent(userName)}&language=${language}`,
        { headers: { 'X-Api-Key': localStorage.getItem('av_api_key') ?? '' } },
      );
      if (!res.ok) throw new Error('Greeting fetch failed');
      return res.blob();
    },
    enabled: enabled && !hasPlayed && !prefersReducedMotion,
    staleTime: 5 * 60_000,
    retry: 1,
  });

  useEffect(() => {
    if (!audioBlob || hasPlayed) return;
    const url = URL.createObjectURL(audioBlob);
    const audio = new Audio(url);
    audioRef.current = audio;

    audio.onplay = () => setIsPlaying(true);
    audio.onended = () => {
      setIsPlaying(false);
      setHasPlayed(true);
      sessionStorage.setItem(`greeting-played-${orgId}`, '1');
      URL.revokeObjectURL(url);
    };
    audio.onerror = () => setIsPlaying(false);

    // Small delay so page renders before audio starts
    const tid = setTimeout(() => audio.play().catch(() => {}), 800);
    return () => clearTimeout(tid);
  }, [audioBlob, hasPlayed, orgId]);

  const stop = () => {
    audioRef.current?.pause();
    setIsPlaying(false);
  };

  return { isPlaying, hasPlayed, stop };
}
```

### 4.4 `src/components/voice/LoginGreetingPlayer.tsx`

```tsx
/**
 * LoginGreetingPlayer — subtle audio indicator for the login greeting.
 *
 * Renders a minimal waveform animation while greeting audio plays.
 * Provides a "mute" button respecting accessibility.
 */
import { motion, AnimatePresence } from 'framer-motion';
import { Volume2, VolumeX } from 'lucide-react';
import { useLoginGreeting } from '@/lib/voice/useLoginGreeting';
import { cn } from '@/lib/utils';

interface LoginGreetingPlayerProps {
  orgId: string;
  userName: string;
  language?: string;
  className?: string;
}

const BAR_COUNT = 5;

export function LoginGreetingPlayer({
  orgId,
  userName,
  language = 'en',
  className,
}: LoginGreetingPlayerProps) {
  const { isPlaying, stop } = useLoginGreeting({ orgId, userName, language });

  return (
    <AnimatePresence>
      {isPlaying && (
        <motion.div
          key="greeting-player"
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.9 }}
          transition={{ type: 'spring', stiffness: 400, damping: 30 }}
          className={cn(
            'flex items-center gap-2 px-3 py-1.5 rounded-full',
            'bg-[#0A0D14]/80 border border-[#00D4FF]/20 backdrop-blur-sm',
            className,
          )}
          role="status"
          aria-label="Voice greeting playing"
          aria-live="polite"
        >
          {/* Mini waveform */}
          <div className="flex items-end gap-0.5 h-4" aria-hidden>
            {Array.from({ length: BAR_COUNT }).map((_, i) => (
              <motion.div
                key={i}
                className="w-0.5 rounded-full bg-[#00D4FF]"
                animate={{ height: ['4px', `${8 + Math.random() * 8}px`, '4px'] }}
                transition={{
                  duration: 0.5 + i * 0.08,
                  repeat: Infinity,
                  ease: 'easeInOut',
                  delay: i * 0.06,
                }}
              />
            ))}
          </div>
          <span className="text-[11px] text-[#94A3B8] font-medium select-none">
            Daily brief
          </span>
          <button
            onClick={stop}
            className="p-0.5 rounded text-[#94A3B8] hover:text-[#F1F5F9] transition-colors"
            aria-label="Stop greeting"
          >
            <VolumeX className="h-3.5 w-3.5" />
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
```

### 4.5 Audio Worklet (`public/audio-worklet-processor.js`)

Must be served as a static file from Vite's `public/` dir:

```javascript
/**
 * PCM Capture Processor — runs in AudioWorklet thread.
 * Accumulates 100 ms of PCM16 mono 16 kHz, posts to main thread.
 */
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf = [];
    this._targetFrames = Math.floor((16000 * 100) / 1000); // 100 ms
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;

    const channel = input[0];
    for (let i = 0; i < channel.length; i++) {
      const s = Math.max(-1, Math.min(1, channel[i]));
      this._buf.push(s < 0 ? s * 0x8000 : s * 0x7fff);
    }

    if (this._buf.length >= this._targetFrames) {
      const int16 = new Int16Array(this._buf.splice(0, this._targetFrames));
      this.port.postMessage(int16.buffer, [int16.buffer]);
    }
    return true;
  }
}

registerProcessor('pcm-capture-processor', PcmCaptureProcessor);
```

### 4.6 `OrgPage.tsx` integration changes

Wire the login greeting into the existing `OrgPage` header:

```tsx
// In OrgPage.tsx, after existing imports:
import { LoginGreetingPlayer } from '@/components/voice/LoginGreetingPlayer';

// In the component, get user name from auth context:
const { user } = useAuth();    // existing auth hook

// In the JSX header:
<header className="flex items-center justify-between px-6 py-4 border-b border-[#1E2535] shrink-0">
  {/* ... existing header content ... */}
  <div className="flex items-center gap-3">
    {/* ... existing action buttons ... */}
    <LoginGreetingPlayer
      orgId={orgId}
      userName={user?.name ?? 'there'}
      language={user?.language ?? 'en'}
      className="hidden sm:flex"
    />
  </div>
</header>
```

---

## 5. Voice Persona Management (V-5)

### Flow: Upload Reference Audio

```
1. User uploads 5-10s WAV from OrgSettings page
2. Frontend: POST /v1/voice/persona/{org_id} with audio file
3. Backend:
   a. Validate: WAV/MP3, 3-30s, < 5 MB, SNR > 15 dB
   b. Convert to 24kHz mono WAV
   c. Store to S3: voice-personas/{tenant_id}/{org_id}/ref.wav
   d. Store ref_text (user-provided transcript) in voice_personas table
   e. Invalidate greeting cache for org
4. All subsequent TTS calls for this org use the reference audio
```

### Voice design attributes (no reference needed)

```python
# If org has no reference audio, use voice_design mode:
await synthesize(
    text=text,
    voice_attributes="female, calm, professional, en-US",
)
```

---

## 6. Rate Limits & Resource Governance

| Resource | Limit | Notes |
|---|---|---|
| `POST /v1/voice/transcribe` | 30 req/min/tenant | 25 MB audio max |
| `POST /v1/voice/speak` | 20 req/min/tenant | 4096 char text max |
| `GET /v1/voice/greeting/{org_id}` | 10 req/min/tenant | Redis-cached 5 min |
| `POST /v1/voice/persona/{org_id}` | 5 req/min/tenant | 5 MB audio max |
| WebSocket sessions | 5 concurrent/tenant | 300s max session duration |
| TTS text per session turn | 2048 chars | Hard limit |
| Audio streaming queue | 50 frames | Backpressure via flow control |

---

## 7. Helm / Infrastructure Changes

### 7.1 `helm/agentverse/values.yaml` — new voice section

```yaml
voice:
  enabled: true
  device: cpu        # or "cuda"
  modelCacheDir: /app/models
  sttModel: large-v3-turbo
  ttsModel: k2-fsa/OmniVoice
  resources:
    cpu:
      requests: { cpu: "2", memory: "4Gi" }
      limits:   { cpu: "4", memory: "8Gi" }
    gpu:
      requests: { cpu: "4", memory: "8Gi", nvidia.com/gpu: "1" }
      limits:   { cpu: "8", memory: "16Gi", nvidia.com/gpu: "1" }
  persistence:
    modelCache:
      enabled: true
      storageClass: standard
      size: 10Gi       # stores both STT and TTS model weights
  greeting:
    cacheTtlSeconds: 300
  minio:
    bucket: agentverse-voice-personas
```

### 7.2 Model pre-download init container

```yaml
# In deployment.yaml:
initContainers:
  - name: voice-model-download
    image: python:3.12-slim
    command:
      - /bin/sh
      - -c
      - |
        pip install faster-whisper omnivoice huggingface_hub -q
        python -c "
        from faster_whisper import WhisperModel
        WhisperModel('large-v3-turbo', device='cpu', download_root='/models')
        from omnivoice import OmniVoice
        OmniVoice.from_pretrained('k2-fsa/OmniVoice', cache_dir='/models')
        print('Models ready')
        "
    volumeMounts:
      - name: model-cache
        mountPath: /models
    env:
      - name: HF_HUB_CACHE
        value: /models
```

---

## 8. Migration Plan: Existing Voice Stubs

| Existing | Action |
|---|---|
| `app/voice/router.py` (stub) | **Replace entirely** with new implementation |
| `app/voice/__init__.py` | Extend public exports |
| `app/gateway/channels/voice_webhook.py` | **Keep** — receives externally transcribed voice; add optional local re-transcription |
| `src/components/voice/VoiceGoalInput.tsx` | Extend: add native STT fallback via `/v1/voice/transcribe` when Web Speech API unavailable |
| `src/features/org/components/VoiceModal.tsx` | Extend: wire `useVoiceStream` for full real-time session instead of browser-only Web Speech |

---

## 9. Observability

Every voice operation MUST emit:

```python
# Spans (OpenTelemetry)
voice.stt.transcribe        # attributes: audio_bytes, language, confidence, duration_ms
voice.tts.synthesize        # attributes: text_len, language, has_ref_audio, duration_ms, output_bytes
voice.stream.session        # attributes: tenant_id, org_id, turns, stt_chars, tts_chars
voice.greeting.synthesize   # attributes: org_id, health, script_len

# Metrics (Prometheus)
voice_stt_requests_total{status, language}
voice_stt_duration_seconds{quantile}
voice_tts_requests_total{status, language}
voice_tts_duration_seconds{quantile}
voice_tts_chars_total{language}
voice_stream_sessions_active{org_id}
voice_stream_turns_total{org_id}
voice_greeting_played_total{org_id, health}

# Structlog fields on every voice log
tenant_id, org_id, request_id, operation, duration_ms
```

---

## 10. Security Controls

| Control | Implementation |
|---|---|
| **Input validation** | Audio: MIME type, size ≤ 25 MB, duration ≤ 120s, SNR check |
| **Persona audio validation** | Duration 3-30s, SNR > 15 dB, format allowlist |
| **Transcript sanitisation** | Strip null bytes, max 4096 chars, run through `app/guardrails_v2/toxicity.py` |
| **TTS text sanitisation** | Reject `[injection-attempt]` tokens not in OmniVoice allowlist |
| **Persona access** | RLS: `voice_personas.tenant_id = current_tenant_id` |
| **WebSocket auth** | API key passed as `?api_key=` query param (over WSS only in prod) |
| **Rate limiting** | Per-tenant sliding window via existing `app/gateway/rate_limiter.py` |
| **CORS** | Voice endpoints follow global CORS config; audio blobs returned with `Content-Disposition: inline` |
| **License** | OmniVoice CC-BY-NC — production use requires commercial licence negotiation with k2-fsa |

> **Important:** OmniVoice is licensed CC-BY-NC. For SaaS commercial use, contact
> k2-fsa to obtain a commercial licence or substitute a commercially-licensed TTS
> (e.g., Kokoro, StyleTTS2, or an ElevenLabs enterprise plan).

---

## 11. Implementation Phases

### Phase 1 — Native STT (1 sprint)
- [ ] Add `faster-whisper` dependency
- [ ] Implement `app/voice/stt_engine.py`
- [ ] Replace stub `_call_stt_backend()` in router
- [ ] Add `GET /v1/voice/status` endpoint
- [ ] Update Dockerfile with `faster-whisper` + model download
- [ ] Unit tests: `tests/voice/test_stt_engine.py`

### Phase 2 — Native TTS + speak endpoint (1 sprint)
- [ ] Add `omnivoice` dependency
- [ ] Implement `app/voice/tts_engine.py`
- [ ] Add `POST /v1/voice/speak` (streaming WAV response)
- [ ] Lifespan warmup
- [ ] Unit tests: `tests/voice/test_tts_engine.py`

### Phase 3 — Login greeting (0.5 sprint)
- [ ] Implement `app/voice/greeting.py`
- [ ] Add `GET /v1/voice/greeting/{org_id}` endpoint
- [ ] Redis cache for greeting audio (5 min TTL)
- [ ] Frontend: `src/lib/voice/useLoginGreeting.ts`
- [ ] Frontend: `src/components/voice/LoginGreetingPlayer.tsx`
- [ ] Wire into `OrgPage.tsx`

### Phase 4 — Real-time WebSocket streaming (1.5 sprints)
- [ ] Implement `app/voice/streaming.py`
- [ ] Add `WS /v1/voice/stream/{org_id}` endpoint
- [ ] `public/audio-worklet-processor.js`
- [ ] Frontend: `src/lib/voice/useVoiceStream.ts`
- [ ] Frontend: `src/components/voice/VoiceStreamWidget.tsx`
- [ ] Wire `VoiceModal.tsx` to real-time backend
- [ ] E2E test: `agent-verse-frontend/e2e/voice-stream.spec.ts`

### Phase 5 — Voice persona (0.5 sprint)
- [ ] Database migration: `voice_personas` table
- [ ] `app/voice/persona.py` (S3 upload/download)
- [ ] `POST/DELETE /v1/voice/persona/{org_id}`
- [ ] Frontend: persona upload UI in OrgSettings

### Phase 6 — Observability + Helm (0.5 sprint)
- [ ] Prometheus metrics for all voice operations
- [ ] OTel span attributes
- [ ] Helm values for voice resources
- [ ] Init container for model pre-download
- [ ] Grafana dashboard: `voice-overview.json`

---

## 12. Test Requirements

### Backend unit tests

```python
# tests/voice/test_stt_engine.py
async def test_transcribe_english_wav():
async def test_transcribe_low_confidence_triggers_flag():
async def test_transcribe_webm_fallback():
async def test_model_singleton_no_double_load():

# tests/voice/test_tts_engine.py
async def test_synthesize_returns_valid_wav():
async def test_synthesize_streaming_yields_chunks():
async def test_synthesize_with_ref_audio():
async def test_synthesize_speed_scaling():

# tests/voice/test_greeting.py
async def test_build_greeting_script_healthy():
async def test_build_greeting_script_degraded():
async def test_time_of_day_morning():
async def test_time_of_day_evening():

# tests/voice/test_router.py (integration, TestClient)
async def test_transcribe_endpoint_stub_no_audio():
async def test_speak_endpoint_returns_audio():
async def test_greeting_endpoint_cached():
async def test_greeting_endpoint_cache_invalidation():
async def test_persona_upload_and_delete():
```

### Frontend unit tests

```typescript
// src/lib/voice/useLoginGreeting.test.ts
it('fetches greeting audio and plays on first org load')
it('does not play if sessionStorage flag is set')
it('does not play if prefers-reduced-motion')
it('stop() pauses audio and sets isPlaying false')

// src/components/voice/LoginGreetingPlayer.test.tsx
it('renders waveform when isPlaying=true')
it('renders nothing when isPlaying=false')
it('stop button triggers stop callback')
it('has aria-label for screen readers')
```

---

## 13. Non-Functional Requirements

| NFR | Target |
|---|---|
| STT latency (10s audio, CPU) | ≤ 3 s |
| STT latency (10s audio, GPU) | ≤ 0.5 s |
| TTS latency first byte (CPU) | ≤ 4 s |
| TTS latency first byte (GPU) | ≤ 0.8 s |
| TTS RTF (OmniVoice spec) | 0.025 (40× real-time) |
| Login greeting total latency | ≤ 800 ms (cached) / ≤ 5 s (cold) |
| WebSocket E2E round-trip | ≤ 6 s STT→agent→TTS start |
| Concurrent WebSocket sessions | 5 per tenant |
| Model load time at cold start | ≤ 30 s (init container pre-downloads) |
| Audio quality | 24 kHz PCM16 (OmniVoice native) |
| Language support | 600+ (OmniVoice) / 99 (Whisper large-v3) |

---

## 14. Open Questions

1. **OmniVoice commercial licence** — CC-BY-NC blocks SaaS use. Need legal sign-off or alternative (Kokoro TTS is MIT-licensed with comparable quality).
2. **GPU node availability** — CPU-only inference is feasible but 10-20× slower. Confirm Kubernetes node pool has A10G/A100 option.
3. **Mobile support** — AudioWorklet is Chrome/Edge/Firefox only. iOS Safari requires ScriptProcessorNode fallback.
4. **Multilingual greeting** — OmniVoice auto-detects; user language preference stored where? (Suggest: `users.language` column or JWT claim).
5. **Persona audio abuse** — Voice impersonation risk. Should persona upload require MFA confirmation?
