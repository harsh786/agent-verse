# AgentVerse Voice Framework — Provider Abstraction Specification

**Location:** `agent-verse-backend/docs/superpowers/specs/`  
**Status:** Canonical  
**Date:** 2026-08-19  
**Supersedes:** `2026-08-19-native-voice-os-plan.md`, `2026-08-19-omnivoice-voice-integration.md`

---

## 1. Design Principle: Plug-and-Play Voice Providers

The voice framework is built around **two abstract provider protocols**. Swapping
from OmniVoice → Kokoro, or faster-whisper → Whisper API requires **one env-var
change** and **zero code changes** in routers, streaming sessions, or greeting engine.

```
env: VOICE_STT_PROVIDER=faster_whisper   →  FasterWhisperSTT
env: VOICE_STT_PROVIDER=whisper_api      →  WhisperAPISTT
env: VOICE_STT_PROVIDER=assemblyai       →  AssemblyAISTT

env: VOICE_TTS_PROVIDER=omnivoice        →  OmniVoiceTTS     (default — local, free)
env: VOICE_TTS_PROVIDER=kokoro           →  KokoroTTS        (MIT, local)
env: VOICE_TTS_PROVIDER=elevenlabs       →  ElevenLabsTTS    (paid API)
env: VOICE_TTS_PROVIDER=openai_tts       →  OpenAITTS        (paid API)
env: VOICE_TTS_PROVIDER=azure_tts        →  AzureTTS         (paid API)
```

All callers in `router.py`, `streaming.py`, `greeting.py`, and `alerts.py` use **only
the abstract interface** — never a concrete provider class directly.

---

## 2. Directory Layout

```
app/voice/
  __init__.py
  providers/
    __init__.py           ← exports get_stt(), get_tts()
    base.py               ← STTProvider + TTSProvider protocols (abstract)
    stt/
      __init__.py
      faster_whisper.py   ← FasterWhisperSTT  (default)
      whisper_api.py      ← WhisperAPISTT
      assemblyai.py       ← AssemblyAISTT
    tts/
      __init__.py
      omnivoice.py        ← OmniVoiceTTS      (default)
      kokoro.py           ← KokoroTTS
      elevenlabs.py       ← ElevenLabsTTS
      openai_tts.py       ← OpenAITTS
      azure_tts.py        ← AzureTTS
  router.py               ← FastAPI routes (uses providers.get_stt/get_tts only)
  streaming.py            ← WebSocket session (uses providers.get_stt/get_tts only)
  greeting.py             ← Login greeting builder
  intent_router.py        ← Voice command intent classifier
  alerts.py               ← Proactive TTS push via Redis pub/sub
  schemas.py              ← Pydantic request/response models
```

---

## 3. Abstract Provider Protocols

### File: `app/voice/providers/base.py`

```python
"""Abstract STT and TTS provider protocols.

Every concrete provider MUST satisfy these structural protocols.
No inheritance required — Python's structural subtyping (Protocol) is used.

Adding a new provider:
  1. Create app/voice/providers/stt/<name>.py or tts/<name>.py
  2. Implement the protocol methods (no base class needed)
  3. Add one line to PROVIDER_REGISTRY in providers/__init__.py
  4. Set env var VOICE_STT_PROVIDER=<name> or VOICE_TTS_PROVIDER=<name>
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, Protocol, runtime_checkable


# ── STT Protocol ──────────────────────────────────────────────────────────────

@runtime_checkable
class STTProvider(Protocol):
    """Speech-to-Text provider contract.

    All STT providers must implement:
      transcribe(audio_bytes, content_type) → TranscriptResult

    Implementations are lazy singletons — they load their model on first call.
    """

    provider_name: str       # e.g. "faster_whisper", "whisper_api"
    supports_streaming: bool = False   # True if provider supports chunk-by-chunk STT

    async def transcribe(
        self,
        audio_bytes: bytes,
        content_type: str,
    ) -> "TranscriptResult":
        """Transcribe raw audio bytes to text.

        Args:
            audio_bytes:   Raw audio (WAV / WebM / OGG / MP4 / FLAC).
            content_type:  MIME type e.g. "audio/wav", "audio/webm".

        Returns:
            TranscriptResult with transcript, language, confidence, segments.
        """
        ...

    async def warmup(self) -> None:
        """Optional: preload model weights at worker startup."""
        ...

    async def is_ready(self) -> bool:
        """Return True if the provider is loaded and ready."""
        ...


# ── TTS Protocol ──────────────────────────────────────────────────────────────

@runtime_checkable
class TTSProvider(Protocol):
    """Text-to-Speech provider contract.

    All TTS providers must implement:
      synthesize()           → WAV bytes
      synthesize_streaming() → AsyncGenerator[bytes, None]

    Implementations are lazy singletons.
    """

    provider_name: str       # e.g. "omnivoice", "kokoro", "elevenlabs"
    sample_rate:   int       # native output sample rate (Hz)
    supports_voice_cloning: bool = False   # True if ref_audio supported
    supports_nonverbal:     bool = False   # True if [laughter] tokens supported
    max_text_length:        int  = 4096

    async def synthesize(
        self,
        text: str,
        *,
        ref_audio: bytes | None = None,
        ref_text:  str | None   = None,
        language:  str          = "en",
        speed:     float        = 1.0,
        voice_id:  str | None   = None,
    ) -> bytes:
        """Synthesise text to WAV bytes.

        Args:
            text:      Input text. Providers that support it accept
                       non-verbal tokens: [laughter], [pause], [whisper].
            ref_audio: Optional WAV bytes for zero-shot voice cloning
                       (ignored by providers that don't support it).
            ref_text:  Transcript of ref_audio (required when ref_audio set).
            language:  BCP-47 code e.g. 'en', 'hi', 'fr'.
            speed:     Playback speed 0.5–2.0.
            voice_id:  Provider-specific voice ID (ElevenLabs, Azure, etc.).

        Returns:
            Complete WAV bytes at self.sample_rate Hz, mono, PCM_16.
        """
        ...

    async def synthesize_streaming(
        self,
        text: str,
        *,
        ref_audio: bytes | None = None,
        ref_text:  str | None   = None,
        language:  str          = "en",
        voice_id:  str | None   = None,
    ) -> AsyncGenerator[bytes, None]:
        """Yield raw PCM16 chunks as an async generator.

        Chunk size is provider-defined (typically 200 ms of audio).
        Callers should not assume any particular chunk size.
        """
        ...

    async def warmup(self) -> None:
        """Optional: preload model weights at worker startup."""
        ...

    async def is_ready(self) -> bool:
        """Return True if the provider is loaded and ready."""
        ...


# ── Shared result types ───────────────────────────────────────────────────────

from dataclasses import dataclass, field


@dataclass
class TranscriptResult:
    """Normalised STT output — same shape regardless of provider."""
    transcript:  str
    language:    str
    confidence:  float   # 0.0 – 1.0
    segments:    list[dict[str, Any]] = field(default_factory=list)
    duration_s:  float = 0.0
    provider:    str   = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "transcript": self.transcript,
            "language":   self.language,
            "confidence": self.confidence,
            "segments":   self.segments,
            "duration_s": self.duration_s,
        }


@dataclass
class ProviderCapabilities:
    """Capabilities advertised by a provider at runtime."""
    provider_name:          str
    provider_type:          str    # "stt" | "tts"
    supports_streaming:     bool   = False
    supports_voice_cloning: bool   = False
    supports_nonverbal:     bool   = False
    languages:              list[str] = field(default_factory=list)
    max_text_length:        int    = 4096
    sample_rate:            int    = 24_000
    local:                  bool   = False   # True = runs in-process (no API cost)
    requires_api_key:       bool   = False
```

### File: `app/voice/providers/__init__.py`

```python
"""Provider registry — single source of truth for STT and TTS providers.

Usage (everywhere in the voice layer):
    from app.voice.providers import get_stt, get_tts

    stt = await get_stt()
    result = await stt.transcribe(audio_bytes, content_type)

    tts = await get_tts()
    wav = await tts.synthesize("Hello world")

Swapping providers:
    Set VOICE_STT_PROVIDER or VOICE_TTS_PROVIDER env var.
    No code changes required anywhere else.

Registering a new provider:
    1. Create the implementation file (see examples below).
    2. Add one entry to STT_REGISTRY or TTS_REGISTRY.
    3. That's it.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from app.voice.providers.base import STTProvider, TTSProvider

log = logging.getLogger(__name__)

# ── Provider registries ───────────────────────────────────────────────────────
# key = VOICE_STT_PROVIDER / VOICE_TTS_PROVIDER env var value
# value = lazy import path (imported only when selected)

STT_REGISTRY: dict[str, str] = {
    "faster_whisper": "app.voice.providers.stt.faster_whisper.FasterWhisperSTT",
    "whisper_api":    "app.voice.providers.stt.whisper_api.WhisperAPISTT",
    "assemblyai":     "app.voice.providers.stt.assemblyai.AssemblyAISTT",
}

TTS_REGISTRY: dict[str, str] = {
    "omnivoice":   "app.voice.providers.tts.omnivoice.OmniVoiceTTS",
    "kokoro":      "app.voice.providers.tts.kokoro.KokoroTTS",
    "elevenlabs":  "app.voice.providers.tts.elevenlabs.ElevenLabsTTS",
    "openai_tts":  "app.voice.providers.tts.openai_tts.OpenAITTS",
    "azure_tts":   "app.voice.providers.tts.azure_tts.AzureTTS",
}

# ── Singletons ────────────────────────────────────────────────────────────────

_stt_instance: STTProvider | None = None
_tts_instance: TTSProvider | None = None
_stt_lock = asyncio.Lock()
_tts_lock = asyncio.Lock()


def _import_class(dotted_path: str) -> Any:
    """Import a class from a dotted module.path.ClassName string."""
    module_path, class_name = dotted_path.rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


async def get_stt() -> STTProvider:
    """Return the configured STT provider singleton.

    Provider is resolved from VOICE_STT_PROVIDER env var (default: faster_whisper).
    Instantiated and loaded once per worker process.
    """
    global _stt_instance
    if _stt_instance is not None:
        return _stt_instance
    async with _stt_lock:
        if _stt_instance is not None:
            return _stt_instance
        provider_name = os.getenv("VOICE_STT_PROVIDER", "faster_whisper")
        dotted = STT_REGISTRY.get(provider_name)
        if dotted is None:
            raise ValueError(
                f"Unknown STT provider {provider_name!r}. "
                f"Available: {list(STT_REGISTRY)}"
            )
        cls = _import_class(dotted)
        _stt_instance = cls()
        log.info("voice.stt.provider_loaded", provider=provider_name)
        return _stt_instance


async def get_tts() -> TTSProvider:
    """Return the configured TTS provider singleton.

    Provider is resolved from VOICE_TTS_PROVIDER env var (default: omnivoice).
    Instantiated and loaded once per worker process.
    """
    global _tts_instance
    if _tts_instance is not None:
        return _tts_instance
    async with _tts_lock:
        if _tts_instance is not None:
            return _tts_instance
        provider_name = os.getenv("VOICE_TTS_PROVIDER", "omnivoice")
        dotted = TTS_REGISTRY.get(provider_name)
        if dotted is None:
            raise ValueError(
                f"Unknown TTS provider {provider_name!r}. "
                f"Available: {list(TTS_REGISTRY)}"
            )
        cls = _import_class(dotted)
        _tts_instance = cls()
        log.info("voice.tts.provider_loaded", provider=provider_name)
        return _tts_instance


async def warmup_providers() -> None:
    """Preload both providers at worker startup (called from app lifespan)."""
    stt = await get_stt()
    tts = await get_tts()
    await asyncio.gather(
        stt.warmup(),
        tts.warmup(),
        return_exceptions=True,
    )
    log.info("voice.providers.warmup_complete",
             stt=stt.provider_name, tts=tts.provider_name)


async def get_capabilities() -> dict:
    """Return capabilities of the currently loaded STT + TTS providers."""
    stt = await get_stt()
    tts = await get_tts()
    return {
        "stt": {
            "provider": stt.provider_name,
            "ready":    await stt.is_ready(),
            "streaming": stt.supports_streaming,
        },
        "tts": {
            "provider":       tts.provider_name,
            "ready":          await tts.is_ready(),
            "voice_cloning":  tts.supports_voice_cloning,
            "nonverbal":      tts.supports_nonverbal,
            "sample_rate":    tts.sample_rate,
        },
    }


# ── Provider swap helpers (for tests / hot-reload) ────────────────────────────

def override_stt(provider: STTProvider) -> None:
    """Inject a test/mock STT provider (bypasses env lookup). Call before get_stt()."""
    global _stt_instance
    _stt_instance = provider


def override_tts(provider: TTSProvider) -> None:
    """Inject a test/mock TTS provider (bypasses env lookup). Call before get_tts()."""
    global _tts_instance
    _tts_instance = provider


def reset_providers() -> None:
    """Reset singleton instances (used in tests between test cases)."""
    global _stt_instance, _tts_instance
    _stt_instance = None
    _tts_instance = None
```

---

## 4. Concrete Provider Implementations

### `app/voice/providers/stt/faster_whisper.py` (default STT)

```python
"""faster-whisper STT provider — local, free, no API key, CTranslate2 in-process."""
from __future__ import annotations

import asyncio
import io
import os
from typing import Any

import numpy as np
from opentelemetry import trace

from app.voice.providers.base import STTProvider, TranscriptResult

tracer = trace.get_tracer(__name__)


class FasterWhisperSTT:
    provider_name:      str  = "faster_whisper"
    supports_streaming: bool = False

    def __init__(self) -> None:
        self._model: Any | None = None
        self._lock = asyncio.Lock()

    async def warmup(self) -> None:
        await self._get_model()

    async def is_ready(self) -> bool:
        return self._model is not None

    async def transcribe(self, audio_bytes: bytes, content_type: str) -> TranscriptResult:
        with tracer.start_as_current_span("stt.faster_whisper.transcribe") as span:
            span.set_attribute("audio_bytes", len(audio_bytes))
            model   = await self._get_model()
            audio   = await self._decode(audio_bytes, content_type)
            loop    = asyncio.get_event_loop()
            segs, info = await loop.run_in_executor(
                None,
                lambda: model.transcribe(audio, beam_size=5, vad_filter=True,
                                         vad_parameters={"min_silence_duration_ms": 300}),
            )
            seg_list = list(segs)
            text = " ".join(s.text.strip() for s in seg_list)
            avg  = float(np.mean([s.avg_logprob for s in seg_list])) if seg_list else -1.0
            conf = float(np.clip(np.exp(avg), 0.0, 1.0))
            span.set_attribute("language", info.language)
            return TranscriptResult(
                transcript=text, language=info.language, confidence=conf,
                segments=[{"start": s.start, "end": s.end, "text": s.text} for s in seg_list],
                provider=self.provider_name,
            )

    async def _get_model(self) -> Any:
        if self._model:
            return self._model
        async with self._lock:
            if self._model:
                return self._model
            from faster_whisper import WhisperModel
            device  = os.getenv("VOICE_DEVICE", "cpu")
            compute = "float16" if device == "cuda" else "int8"
            self._model = WhisperModel(
                os.getenv("VOICE_STT_MODEL", "large-v3-turbo"),
                device=device, compute_type=compute,
                download_root=os.getenv("MODEL_CACHE_DIR", "/app/models"),
            )
            return self._model

    @staticmethod
    async def _decode(audio_bytes: bytes, content_type: str) -> np.ndarray:
        import soundfile as sf
        buf = io.BytesIO(audio_bytes)
        try:
            audio, sr = sf.read(buf, dtype="float32", always_2d=False)
        except Exception:
            import torchaudio
            buf.seek(0)
            wf, sr = torchaudio.load(buf)
            audio  = wf.squeeze().numpy()
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != 16_000:
            import torch, torchaudio
            t     = torch.from_numpy(audio).unsqueeze(0)
            audio = torchaudio.functional.resample(t, sr, 16_000).squeeze().numpy()
        return audio
```

### `app/voice/providers/stt/whisper_api.py` (OpenAI API fallback)

```python
"""OpenAI Whisper API STT — requires OPENAI_API_KEY."""
from __future__ import annotations

import io
import os

from app.voice.providers.base import STTProvider, TranscriptResult


class WhisperAPISTT:
    provider_name:      str  = "whisper_api"
    supports_streaming: bool = False

    def __init__(self) -> None:
        self._ready = False

    async def warmup(self) -> None:
        self._ready = bool(os.getenv("OPENAI_API_KEY"))

    async def is_ready(self) -> bool:
        return self._ready

    async def transcribe(self, audio_bytes: bytes, content_type: str) -> TranscriptResult:
        import httpx
        key = os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set for whisper_api provider")
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                data={"model": "whisper-1"},
                files={"file": ("audio.wav", audio_bytes, content_type)},
            )
            r.raise_for_status()
            data = r.json()
        return TranscriptResult(
            transcript=data.get("text", ""),
            language=data.get("language", "en"),
            confidence=0.95,
            provider=self.provider_name,
        )
```

### `app/voice/providers/tts/omnivoice.py` (default TTS — OmniVoice)

```python
"""OmniVoice TTS — local, free (CC-BY-NC), 600+ languages, RTF 0.025."""
from __future__ import annotations

import asyncio
import io
import os
from collections.abc import AsyncGenerator
from typing import Any

import numpy as np
import soundfile as sf
from opentelemetry import trace

from app.voice.providers.base import TTSProvider, TranscriptResult

tracer = trace.get_tracer(__name__)

SAMPLE_RATE   = 24_000
CHUNK_FRAMES  = 4_800    # 200 ms
WAV_HEADER_SZ = 44


class OmniVoiceTTS:
    provider_name:          str  = "omnivoice"
    sample_rate:            int  = SAMPLE_RATE
    supports_voice_cloning: bool = True
    supports_nonverbal:     bool = True    # [laughter], [pause], [whisper]
    max_text_length:        int  = 4096

    def __init__(self) -> None:
        self._model: Any | None = None
        self._lock = asyncio.Lock()

    async def warmup(self) -> None:
        await self._get_model()
        await self.synthesize(".")   # JIT compile

    async def is_ready(self) -> bool:
        return self._model is not None

    async def synthesize(
        self, text: str, *, ref_audio=None, ref_text=None,
        language="en", speed=1.0, voice_id=None,
    ) -> bytes:
        with tracer.start_as_current_span("tts.omnivoice.synthesize") as span:
            span.set_attribute("text_len", len(text))
            model = await self._get_model()
            kwargs: dict = {"text": text}
            if ref_audio and ref_text:
                ref_np, _ = self._wav_to_np(ref_audio)
                kwargs["ref_audio"] = ref_np
                kwargs["ref_text"]  = ref_text
            loop = asyncio.get_event_loop()
            arrays = await loop.run_in_executor(None, lambda: model.generate(**kwargs))
            audio  = arrays[0]
            if abs(speed - 1.0) > 0.01:
                import torch, torchaudio
                t     = torch.from_numpy(audio).unsqueeze(0)
                audio = torchaudio.functional.resample(
                    t, int(SAMPLE_RATE / speed), SAMPLE_RATE
                ).squeeze().numpy()
            buf = io.BytesIO()
            sf.write(buf, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
            return buf.getvalue()

    async def synthesize_streaming(
        self, text: str, *, ref_audio=None, ref_text=None,
        language="en", voice_id=None,
    ) -> AsyncGenerator[bytes, None]:
        wav = await self.synthesize(text, ref_audio=ref_audio, ref_text=ref_text,
                                     language=language)
        pcm = wav[WAV_HEADER_SZ:]
        chunk = CHUNK_FRAMES * 2
        pos   = 0
        while pos < len(pcm):
            yield pcm[pos:pos + chunk]
            pos += chunk
            await asyncio.sleep(0)

    async def _get_model(self) -> Any:
        if self._model:
            return self._model
        async with self._lock:
            if self._model:
                return self._model
            from omnivoice import OmniVoice
            import torch
            device = os.getenv("VOICE_DEVICE", "cpu")
            dtype  = torch.float16 if device == "cuda" else torch.float32
            self._model = OmniVoice.from_pretrained(
                os.getenv("VOICE_TTS_MODEL", "k2-fsa/OmniVoice"),
                device_map=device, dtype=dtype,
                cache_dir=os.getenv("MODEL_CACHE_DIR", "/app/models"),
            )
            return self._model

    @staticmethod
    def _wav_to_np(wav_bytes: bytes) -> tuple[np.ndarray, int]:
        buf = io.BytesIO(wav_bytes)
        audio, sr = sf.read(buf, dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        return audio, sr
```

### `app/voice/providers/tts/kokoro.py` (MIT-licensed alternative)

```python
"""Kokoro TTS — MIT-licensed, local, 82M params, high quality.
   pip install kokoro-onnx soundfile
"""
from __future__ import annotations

import asyncio
import io
import os
from collections.abc import AsyncGenerator
from typing import Any

import soundfile as sf
from app.voice.providers.base import TTSProvider

SAMPLE_RATE   = 24_000
CHUNK_FRAMES  = 4_800
WAV_HEADER_SZ = 44


class KokoroTTS:
    provider_name:          str  = "kokoro"
    sample_rate:            int  = SAMPLE_RATE
    supports_voice_cloning: bool = False
    supports_nonverbal:     bool = False
    max_text_length:        int  = 2048

    def __init__(self) -> None:
        self._pipeline: Any | None = None
        self._lock = asyncio.Lock()

    async def warmup(self) -> None:
        await self._get_pipeline()

    async def is_ready(self) -> bool:
        return self._pipeline is not None

    async def synthesize(
        self, text: str, *, ref_audio=None, ref_text=None,
        language="en", speed=1.0, voice_id=None,
    ) -> bytes:
        pipeline = await self._get_pipeline()
        voice    = voice_id or os.getenv("KOKORO_VOICE", "af_heart")
        loop     = asyncio.get_event_loop()
        samples  = await loop.run_in_executor(
            None,
            lambda: list(pipeline(text, voice=voice, speed=speed, lang=language)),
        )
        import numpy as np
        audio = np.concatenate([s for _, s in samples if s is not None])
        buf   = io.BytesIO()
        sf.write(buf, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        return buf.getvalue()

    async def synthesize_streaming(
        self, text: str, *, ref_audio=None, ref_text=None,
        language="en", voice_id=None,
    ) -> AsyncGenerator[bytes, None]:
        wav = await self.synthesize(text, language=language, voice_id=voice_id)
        pcm = wav[WAV_HEADER_SZ:]
        for pos in range(0, len(pcm), CHUNK_FRAMES * 2):
            yield pcm[pos:pos + CHUNK_FRAMES * 2]
            await asyncio.sleep(0)

    async def _get_pipeline(self) -> Any:
        if self._pipeline:
            return self._pipeline
        async with self._lock:
            if self._pipeline:
                return self._pipeline
            from kokoro import KPipeline
            lang = os.getenv("KOKORO_LANG", "a")   # "a"=American, "b"=British
            self._pipeline = KPipeline(lang_code=lang)
            return self._pipeline
```

### `app/voice/providers/tts/elevenlabs.py` (paid API — drop-in swap)

```python
"""ElevenLabs TTS — paid API, voice cloning, highest quality.
   Requires ELEVENLABS_API_KEY.
"""
from __future__ import annotations

import io
import os
from collections.abc import AsyncGenerator

import soundfile as sf
from app.voice.providers.base import TTSProvider

SAMPLE_RATE = 44_100


class ElevenLabsTTS:
    provider_name:          str  = "elevenlabs"
    sample_rate:            int  = SAMPLE_RATE
    supports_voice_cloning: bool = True
    supports_nonverbal:     bool = False
    max_text_length:        int  = 5000

    def __init__(self) -> None:
        self._key = os.getenv("ELEVENLABS_API_KEY", "")

    async def warmup(self) -> None:
        pass   # API — no model load needed

    async def is_ready(self) -> bool:
        return bool(self._key)

    async def synthesize(
        self, text: str, *, ref_audio=None, ref_text=None,
        language="en", speed=1.0, voice_id=None,
    ) -> bytes:
        import httpx
        vid = voice_id or os.getenv("ELEVENLABS_VOICE_ID", "rachel")
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
                headers={"xi-api-key": self._key, "Content-Type": "application/json"},
                json={
                    "text": text,
                    "model_id": "eleven_turbo_v2_5",
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                },
            )
            r.raise_for_status()
            mp3_bytes = r.content
        # Convert mp3 → wav
        import pydub
        seg = pydub.AudioSegment.from_mp3(io.BytesIO(mp3_bytes))
        buf = io.BytesIO()
        seg.export(buf, format="wav")
        return buf.getvalue()

    async def synthesize_streaming(
        self, text: str, *, ref_audio=None, ref_text=None,
        language="en", voice_id=None,
    ) -> AsyncGenerator[bytes, None]:
        # ElevenLabs has a streaming API but we use full for simplicity;
        # override this method for true streaming
        wav = await self.synthesize(text, language=language, voice_id=voice_id)
        CHUNK = 9600   # 100 ms at 44.1 kHz, int16
        pcm   = wav[44:]
        for pos in range(0, len(pcm), CHUNK):
            yield pcm[pos:pos + CHUNK]
```

---

## 5. Callers — Abstract API Usage Only

Every file in `app/voice/` that needs STT or TTS MUST use **only** these imports:

```python
# CORRECT — all callers use this pattern
from app.voice.providers import get_stt, get_tts

# In router.py (transcribe endpoint):
stt    = await get_stt()
result = await stt.transcribe(audio_bytes, content_type)
return TranscribeResponse(**result.to_dict())

# In router.py (speak endpoint):
tts     = await get_tts()
wav     = await tts.synthesize(body.text, language=body.language, speed=body.speed,
                                ref_audio=ref_audio, ref_text=ref_text)
return StreamingResponse(io.BytesIO(wav), media_type="audio/wav")

# In streaming.py (TTS chunks):
tts = await get_tts()
async for chunk in tts.synthesize_streaming(response, language=self.language,
                                             ref_audio=self.ref_audio):
    await self._send({"type": "tts_chunk", "data": b64encode(chunk).decode()})

# In greeting.py:
tts = await get_tts()
wav = await tts.synthesize(script, language=language, ref_audio=ref_audio)

# FORBIDDEN — never import a concrete class in a non-provider file
# from app.voice.providers.tts.omnivoice import OmniVoiceTTS  ← FORBIDDEN
```

---

## 6. `app/voice/router.py` — Updated Status Endpoint

```python
# GET /v1/voice/status — now shows provider abstraction info
@router.get("/status", operation_id="voice_status")
async def voice_status(request: Request) -> VoiceStatusResponse:
    _require_tenant(request)
    from app.voice.providers import get_capabilities
    caps = await get_capabilities()
    return VoiceStatusResponse(
        stt_provider      = caps["stt"]["provider"],
        stt_status        = "ready" if caps["stt"]["ready"] else "loading",
        stt_streaming     = caps["stt"]["streaming"],
        tts_provider      = caps["tts"]["provider"],
        tts_status        = "ready" if caps["tts"]["ready"] else "loading",
        tts_voice_cloning = caps["tts"]["voice_cloning"],
        tts_nonverbal     = caps["tts"]["nonverbal"],
        tts_sample_rate   = caps["tts"]["sample_rate"],
    )
```

Updated `schemas.py` for the enhanced status response:

```python
class VoiceStatusResponse(BaseModel):
    stt_provider:       str
    stt_status:         str    # "ready" | "loading" | "error"
    stt_streaming:      bool
    tts_provider:       str
    tts_status:         str
    tts_voice_cloning:  bool
    tts_nonverbal:      bool
    tts_sample_rate:    int
```

---

## 7. Lifespan Warmup Update

**File:** `app/main.py` — replace provider-specific warmup with abstract:

```python
# In lifespan block:
if getattr(settings, "VOICE_ENABLED", True):
    from app.voice.providers import warmup_providers
    asyncio.create_task(warmup_providers())
    logger.info("voice_providers_warmup_scheduled")
```

---

## 8. Adding a New Provider in 4 Steps

Example: adding **Coqui TTS** (open-source, Mozilla):

```
Step 1: Create app/voice/providers/tts/coqui.py
        Implement the 4 required methods:
          - synthesize()
          - synthesize_streaming()
          - warmup()
          - is_ready()
        Set: provider_name = "coqui"
             sample_rate   = 22_050
             supports_voice_cloning = True

Step 2: Register in app/voice/providers/__init__.py
        TTS_REGISTRY["coqui"] = "app.voice.providers.tts.coqui.CoquiTTS"

Step 3: Set env var:
        VOICE_TTS_PROVIDER=coqui

Step 4: That's it. Zero changes to router.py, streaming.py, greeting.py, alerts.py.
```

---

## 9. Testing Strategy

```python
# tests/voice/conftest.py

from app.voice.providers.base import TTSProvider, STTProvider, TranscriptResult
from app.voice.providers import override_stt, override_tts, reset_providers
import pytest, asyncio, io
import soundfile as sf
import numpy as np


class FakeSTT:
    """Deterministic fake STT for unit tests — no model loading."""
    provider_name      = "fake_stt"
    supports_streaming = False

    async def warmup(self)     -> None: pass
    async def is_ready(self)   -> bool: return True

    async def transcribe(self, audio_bytes, content_type) -> TranscriptResult:
        return TranscriptResult(
            transcript="test goal from voice",
            language="en",
            confidence=0.99,
            provider="fake_stt",
        )


class FakeTTS:
    """Deterministic fake TTS — returns 100 ms of silence."""
    provider_name          = "fake_tts"
    sample_rate            = 24_000
    supports_voice_cloning = False
    supports_nonverbal     = False
    max_text_length        = 4096

    async def warmup(self)     -> None: pass
    async def is_ready(self)   -> bool: return True

    async def synthesize(self, text, **kwargs) -> bytes:
        silence = np.zeros(2_400, dtype=np.float32)
        buf = io.BytesIO()
        sf.write(buf, silence, 24_000, format="WAV", subtype="PCM_16")
        return buf.getvalue()

    async def synthesize_streaming(self, text, **kwargs):
        wav = await self.synthesize(text)
        yield wav[44:]


@pytest.fixture(autouse=True)
def inject_fake_providers():
    """Auto-inject fake providers for all voice tests.
    Override in individual tests if a real provider is needed.
    """
    override_stt(FakeSTT())
    override_tts(FakeTTS())
    yield
    reset_providers()


# Usage in a test:
async def test_transcribe_uses_stt_provider(client):
    r = client.post("/v1/voice/transcribe",
                    headers={"X-API-Key": "test"},
                    files={"audio": ("x.wav", b"fake", "audio/wav")})
    assert r.status_code == 200
    assert r.json()["transcript"] == "test goal from voice"
    # Works with ANY STT provider — test doesn't know or care which one
```

---

## 10. Config Summary

```bash
# app/core/config.py additions
VOICE_ENABLED         = true
VOICE_STT_PROVIDER    = faster_whisper   # faster_whisper|whisper_api|assemblyai
VOICE_TTS_PROVIDER    = omnivoice        # omnivoice|kokoro|elevenlabs|openai_tts|azure_tts
VOICE_DEVICE          = cpu              # cpu|cuda
VOICE_STT_MODEL       = large-v3-turbo
VOICE_TTS_MODEL       = k2-fsa/OmniVoice
MODEL_CACHE_DIR       = /app/models
VOICE_GREETING_CACHE_TTL = 300
VOICE_MAX_AUDIO_MB    = 25

# Provider-specific (only needed for the selected provider)
OPENAI_API_KEY        =                  # required for whisper_api
ASSEMBLY_AI_KEY       =                  # required for assemblyai
ELEVENLABS_API_KEY    =                  # required for elevenlabs
ELEVENLABS_VOICE_ID   = rachel
AZURE_TTS_KEY         =                  # required for azure_tts
AZURE_TTS_REGION      = eastus
KOKORO_VOICE          = af_heart
KOKORO_LANG           = a               # a=American, b=British
```

---

## 11. Provider Comparison Matrix

| Provider | Type | Model | Size | Language | Voice Cloning | Cost | Requires GPU |
|---|---|---|---|---|---|---|---|
| **faster_whisper** | STT | large-v3-turbo | 809 MB | 99 langs | — | Free | No (fast on CPU) |
| **whisper_api** | STT | whisper-1 | API | 57 langs | — | $0.006/min | No |
| **assemblyai** | STT | Universal-2 | API | 12 langs | — | $0.0065/min | No |
| **omnivoice** | TTS | k2-fsa/OmniVoice | 0.6 B | 600+ langs | ✅ Zero-shot | Free (CC-BY-NC) | No (RTF 0.025) |
| **kokoro** | TTS | Kokoro-82M | 82 MB | EN/JP/ZH/ES/FR/PT/KO/HI | ❌ | Free (MIT) | No |
| **elevenlabs** | TTS | Turbo v2.5 | API | 32 langs | ✅ Instant | $0.30/1k chars | No |
| **openai_tts** | TTS | tts-1-hd | API | 57 langs | ❌ | $0.030/1k chars | No |
| **azure_tts** | TTS | Neural | API | 140+ langs | ✅ Custom | $16/1M chars | No |

**Recommended defaults:**
- Dev / on-prem: `STT=faster_whisper` + `TTS=omnivoice`
- Production (no GPU): `STT=faster_whisper` + `TTS=kokoro` (MIT-licensed)
- Production (premium): `STT=faster_whisper` + `TTS=elevenlabs`
- Enterprise: All providers swappable per-tenant via org settings

---

## 12. Implementation Checklist

```
Provider abstraction
  [ ] app/voice/providers/base.py          — STTProvider + TTSProvider protocols
  [ ] app/voice/providers/__init__.py      — get_stt(), get_tts(), registry, warmup
  [ ] app/voice/providers/stt/__init__.py
  [ ] app/voice/providers/stt/faster_whisper.py   (default)
  [ ] app/voice/providers/stt/whisper_api.py
  [ ] app/voice/providers/stt/assemblyai.py
  [ ] app/voice/providers/tts/__init__.py
  [ ] app/voice/providers/tts/omnivoice.py        (default)
  [ ] app/voice/providers/tts/kokoro.py
  [ ] app/voice/providers/tts/elevenlabs.py
  [ ] app/voice/providers/tts/openai_tts.py
  [ ] app/voice/providers/tts/azure_tts.py

Callers — use only get_stt() / get_tts()
  [ ] router.py       — replace stt_engine/tts_engine imports with providers
  [ ] streaming.py    — replace tts_engine imports with providers
  [ ] greeting.py     — replace tts_engine imports with providers
  [ ] alerts.py       — replace tts_engine imports with providers
  [ ] router.py /status endpoint shows provider name (not model name)

Tests
  [ ] tests/voice/conftest.py             — FakeSTT + FakeTTS fixtures
  [ ] tests/voice/test_providers.py       — registry, swap, reset
  [ ] tests/voice/test_stt_engine.py      — update to use override_stt(FakeSTT())
  [ ] tests/voice/test_tts_engine.py      — update to use override_tts(FakeTTS())
  [ ] tests/voice/test_router.py          — all use fake providers via conftest

Lifespan
  [ ] app/main.py — warmup_providers() replaces tts_engine.warmup()

Config
  [ ] app/core/config.py — VOICE_STT_PROVIDER, VOICE_TTS_PROVIDER added
  [ ] .env.example updated
```
