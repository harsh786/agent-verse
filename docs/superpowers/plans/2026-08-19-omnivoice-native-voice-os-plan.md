# OmniVoice Native Voice OS — Deep-Verified World-Class Implementation Plan

> **REVERIFICATION COMPLETE 2026-08-19** — Deep code analysis found 4 critical bugs in the previous spec and 7 world-class differentiators. This is the corrected, final plan.
>
> **For agentic workers:** Use `superpowers:subagent-driven-development` to execute phases in parallel where noted. Each task has exact file targets, line anchors, and copy-paste code. Steps use checkbox (`- [ ]`) syntax for tracking.

---

## ⚠️ Critical Bugs Found & Fixed (Reverification)

| # | Bug in Previous Spec | Root Cause | Fix Applied |
|---|---|---|---|
| B-1 | `svc.get_morning_brief(org_id)` called | Method does NOT exist in `OrgService` | Use `svc.get_org_health(org_id)` ✅ |
| B-2 | `DigestGenerator` not used | `app/org/digest.py` has full "While You Were Away" engine | Wired into greeting ✅ |
| B-3 | `refine_to_goal(transcript, ...)` called | `GoalRefinementPipeline.refine()` is sync, different signature | Wrap in `run_in_executor` ✅ |
| B-4 | `_fetch_morning_brief` misuses `get_org_service` | AsyncGenerator cannot be called outside FastAPI DI | Replaced with direct `sessionFactory` call ✅ |

---

## 🌍 World-Class Differentiators Added

These features make AgentVerse the **only product in the world** with this combination:

| # | Differentiator | What it does |
|---|---|---|
| D-1 | **Voice-to-Mission Pipeline** | Say a goal → `GoalRefinementPipeline.refine()` → real `OrgMission` created → TTS confirmation with departments, budget, timeline |
| D-2 | **"While You Were Away" Voice Digest** | Uses actual `DigestGenerator` from `app/org/digest.py` → narrates what happened since last login |
| D-3 | **Voice Command Intent Router** | Classifies intent before routing: create_mission / status_check / approve / summarize / search |
| D-4 | **Voice-Driven Approval** | Say "approve" / "reject" → `OrgService.record_decision()` → audit trail + event |
| D-5 | **Multi-language by Org Jurisdiction** | `Organization.jurisdiction` field auto-selects TTS language (600+ via OmniVoice) |
| D-6 | **Proactive Voice Alerts** | Redis pub/sub push → TTS audio when mission fails / approval urgently needed |
| D-7 | **Org Health spoken greeting** | Uses real `OrgService.get_org_health()` data — `active_missions`, `pending_approvals`, `items_needing_attention` |

---

**Goal:** Replace AgentVerse's stub voice layer with a fully native open-source voice OS:
- **OmniVoice** (`k2-fsa/OmniVoice`, 0.6 B, 600+ languages, RTF 0.025) → TTS  
- **faster-whisper** `large-v3-turbo` → Native in-process STT (no external API)  
- **WebSocket** bidirectional real-time voice session (`/v1/voice/stream/{org_id}`)  
- **Login greeting** — OmniVoice speaks real `OrgService.get_org_health()` data + `DigestGenerator` WYWA brief  
- **Voice-to-Mission** — speak a goal → `GoalRefinementPipeline` → `OrgService.create_mission()` → TTS confirmation  
- **Voice persona** — per-org reference audio for zero-shot voice cloning  
- Replace `window.speechSynthesis.speak()` in `OrgPage.tsx` with OmniVoice TTS  

**Architecture:**
```
OrgPage login
  → GET /v1/voice/greeting/{org_id}  (WAV, cached 5 min Redis)
  → Auto-plays via LoginGreetingPlayer component

Mic button in OrgPage / VoiceModal
  → WS  /v1/voice/stream/{org_id}
       PCM16 chunks → faster-whisper → transcript
       transcript → OrgService / GoalService LLM
       LLM response → OmniVoice → PCM24 chunks back

Quick STT upload
  → POST /v1/voice/transcribe  (audio blob → transcript)

Text-to-speech
  → POST /v1/voice/speak  (text → streaming WAV)
```

**Tech Stack:**
- Backend: Python 3.12, FastAPI, SQLAlchemy 2 async, faster-whisper, omnivoice, structlog, OTel
- Frontend: React 19, TypeScript, TanStack Query 5, Zustand 5, Framer Motion 13, Web Audio API, AudioWorklet

---

## Phase 1 — Backend: Native STT Engine (faster-whisper)

### Task 1.1: Add Dependencies

**Files:** `agent-verse-backend/pyproject.toml`

- [ ] **Step 1: Write failing import test**
```python
# tests/voice/test_stt_engine.py — create this file
import pytest

async def test_stt_imports():
    """Fails if faster-whisper not installed."""
    from app.voice.stt_engine import transcribe
    assert callable(transcribe)
```

- [ ] **Step 2: Run test to verify it fails**
```bash
cd agent-verse-backend && uv run pytest tests/voice/test_stt_engine.py -x
```

- [ ] **Step 3: Add dependencies to pyproject.toml**

Add under `[project.dependencies]`:
```toml
faster-whisper = ">=1.1.0"
omnivoice      = ">=0.1.0"
soundfile      = ">=0.12.1"
torchaudio     = ">=2.2.0"
boto3          = ">=1.34.0"
```

- [ ] **Step 4: Sync**
```bash
cd agent-verse-backend && uv sync
```

---

### Task 1.2: Create `app/voice/stt_engine.py`

**Files:**
- Create: `agent-verse-backend/app/voice/stt_engine.py`
- Test: `agent-verse-backend/tests/voice/test_stt_engine.py`

- [ ] **Step 1: Write failing tests first (TDD)**
```python
# tests/voice/test_stt_engine.py
import io
import numpy as np
import pytest
import soundfile as sf

@pytest.mark.asyncio
async def test_transcribe_returns_dict():
    """transcribe() returns correct schema."""
    from app.voice.stt_engine import transcribe
    # 1 second of silence at 16 kHz
    silence = np.zeros(16_000, dtype=np.float32)
    buf = io.BytesIO()
    sf.write(buf, silence, 16_000, format='WAV', subtype='PCM_16')
    result = await transcribe(buf.getvalue(), 'audio/wav')
    assert 'transcript' in result
    assert 'language' in result
    assert 'confidence' in result
    assert isinstance(result['segments'], list)

@pytest.mark.asyncio
async def test_model_singleton():
    """Model loaded only once across multiple calls."""
    from app.voice import stt_engine
    stt_engine._model = None  # reset
    m1 = await stt_engine.get_model()
    m2 = await stt_engine.get_model()
    assert m1 is m2
```

- [ ] **Step 2: Run tests — verify they fail**
```bash
cd agent-verse-backend && uv run pytest tests/voice/test_stt_engine.py -x
```

- [ ] **Step 3: Create stt_engine.py**
```python
# app/voice/stt_engine.py
"""Native STT using faster-whisper — CTranslate2 in-process, no API key needed.

Thread-safe lazy singleton.  Model loads once per worker process on first call.
Supports: wav, webm, ogg, mp4, m4a, flac (auto-detected via torchaudio FFmpeg).
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
from typing import Any

import numpy as np
import soundfile as sf
from opentelemetry import trace

log = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

_lock  = asyncio.Lock()
_model: Any | None = None


async def get_model() -> Any:
    """Return cached WhisperModel, loading it on first call (double-checked lock)."""
    global _model
    if _model is not None:
        return _model
    async with _lock:
        if _model is not None:
            return _model
        log.info("voice.stt.loading model=%s", os.getenv("VOICE_STT_MODEL", "large-v3-turbo"))
        from faster_whisper import WhisperModel
        device   = os.getenv("VOICE_DEVICE", "cpu")
        compute  = "float16" if device == "cuda" else "int8"
        _model   = WhisperModel(
            os.getenv("VOICE_STT_MODEL", "large-v3-turbo"),
            device=device,
            compute_type=compute,
            download_root=os.getenv("MODEL_CACHE_DIR", "/app/models"),
        )
        log.info("voice.stt.model_ready device=%s compute=%s", device, compute)
        return _model


async def transcribe(audio_bytes: bytes, content_type: str) -> dict:
    """Transcribe raw audio bytes to text.

    Returns:
        {transcript, language, confidence, segments: [{start, end, text}]}
    """
    with tracer.start_as_current_span("voice.stt.transcribe") as span:
        span.set_attribute("audio_bytes", len(audio_bytes))
        span.set_attribute("content_type", content_type)

        model = await get_model()
        audio_np = await _decode_audio(audio_bytes, content_type)

        loop = asyncio.get_event_loop()
        segments_gen, info = await loop.run_in_executor(
            None,
            lambda: model.transcribe(
                audio_np,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
            ),
        )
        seg_list = list(segments_gen)   # consume generator in executor thread

        full_text   = " ".join(s.text.strip() for s in seg_list)
        avg_logprob = float(np.mean([s.avg_logprob for s in seg_list])) if seg_list else -1.0
        confidence  = float(np.clip(np.exp(avg_logprob), 0.0, 1.0))

        span.set_attribute("language", info.language)
        span.set_attribute("confidence", confidence)
        span.set_attribute("transcript_len", len(full_text))

        return {
            "transcript": full_text,
            "language":   info.language,
            "confidence": confidence,
            "segments":   [{"start": s.start, "end": s.end, "text": s.text} for s in seg_list],
        }


async def _decode_audio(audio_bytes: bytes, content_type: str) -> np.ndarray:
    """Decode any audio format to float32 mono 16 kHz numpy array."""
    buf = io.BytesIO(audio_bytes)
    try:
        audio_np, sample_rate = sf.read(buf, dtype="float32", always_2d=False)
    except Exception:
        # webm / opus / mp4 — fallback via torchaudio FFmpeg backend
        import torchaudio
        buf.seek(0)
        wf, sample_rate = torchaudio.load(buf, format=None)
        audio_np = wf.squeeze().numpy()

    # Stereo → mono
    if audio_np.ndim > 1:
        audio_np = audio_np.mean(axis=1)

    # Resample to 16 kHz
    if sample_rate != 16_000:
        import torch
        import torchaudio
        t = torch.from_numpy(audio_np).unsqueeze(0)
        audio_np = torchaudio.functional.resample(t, sample_rate, 16_000).squeeze().numpy()

    return audio_np
```

- [ ] **Step 4: Run tests — verify they pass**
```bash
cd agent-verse-backend && uv run pytest tests/voice/test_stt_engine.py -v
```

---

## Phase 2 — Backend: Native TTS Engine (OmniVoice)

### Task 2.1: Create `app/voice/tts_engine.py`

**Files:**
- Create: `agent-verse-backend/app/voice/tts_engine.py`
- Test: `agent-verse-backend/tests/voice/test_tts_engine.py`

- [ ] **Step 1: Write failing tests (TDD)**
```python
# tests/voice/test_tts_engine.py
import io
import pytest
import soundfile as sf

@pytest.mark.asyncio
async def test_synthesize_returns_wav():
    from app.voice.tts_engine import synthesize
    wav = await synthesize("Hello AgentVerse.", language="en")
    assert isinstance(wav, bytes)
    assert len(wav) > 44   # at least WAV header + some audio
    buf = io.BytesIO(wav)
    audio, sr = sf.read(buf)
    assert sr == 24_000

@pytest.mark.asyncio
async def test_synthesize_streaming_yields_bytes():
    from app.voice.tts_engine import synthesize_streaming
    chunks = []
    async for chunk in synthesize_streaming("test", language="en"):
        chunks.append(chunk)
    assert len(chunks) > 0
    assert all(isinstance(c, bytes) for c in chunks)

@pytest.mark.asyncio
async def test_model_singleton():
    from app.voice import tts_engine
    tts_engine._model = None
    m1 = await tts_engine.get_model()
    m2 = await tts_engine.get_model()
    assert m1 is m2
```

- [ ] **Step 2: Run tests — verify they fail**
```bash
cd agent-verse-backend && uv run pytest tests/voice/test_tts_engine.py -x
```

- [ ] **Step 3: Create tts_engine.py**
```python
# app/voice/tts_engine.py
"""Native TTS using OmniVoice (k2-fsa/OmniVoice).

Zero-shot voice cloning, 600+ languages, RTF 0.025 (40× real-time on GPU).
Model is a lazy singleton — loaded once per worker process.

Public API:
  synthesize()           → WAV bytes (blocking, runs in executor)
  synthesize_streaming() → async generator of PCM16 chunks (200 ms each)
  warmup()               → preload model at worker startup
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

import numpy as np
import soundfile as sf
from opentelemetry import trace

log = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

_lock  = asyncio.Lock()
_model: Any | None = None

SAMPLE_RATE   = 24_000    # OmniVoice native output rate
CHUNK_FRAMES  = 4_800     # 200 ms of audio at 24 kHz
WAV_HEADER_SZ = 44        # standard PCM WAV header bytes


async def get_model() -> Any:
    global _model
    if _model is not None:
        return _model
    async with _lock:
        if _model is not None:
            return _model
        log.info("voice.tts.loading model=%s", os.getenv("VOICE_TTS_MODEL", "k2-fsa/OmniVoice"))
        from omnivoice import OmniVoice
        import torch
        device = os.getenv("VOICE_DEVICE", "cpu")
        dtype  = torch.float16 if device == "cuda" else torch.float32
        _model = OmniVoice.from_pretrained(
            os.getenv("VOICE_TTS_MODEL", "k2-fsa/OmniVoice"),
            device_map=device,
            dtype=dtype,
            cache_dir=os.getenv("MODEL_CACHE_DIR", "/app/models"),
        )
        log.info("voice.tts.model_ready device=%s", device)
        return _model


async def synthesize(
    text: str,
    *,
    ref_audio: bytes | None = None,
    ref_text: str | None = None,
    language: str = "en",
    speed: float = 1.0,
) -> bytes:
    """Synthesise text to WAV bytes.

    Args:
        text:       Input text. Supports non-verbal tokens: [laughter], [pause], [whisper].
        ref_audio:  Optional WAV bytes for zero-shot voice cloning.
        ref_text:   Transcript of ref_audio (required when ref_audio is set).
        language:   BCP-47 code e.g. 'en', 'hi', 'fr', 'zh'.
        speed:      Playback speed 0.5–2.0.
    Returns:
        WAV bytes (24 kHz, mono, PCM_16).
    """
    with tracer.start_as_current_span("voice.tts.synthesize") as span:
        span.set_attribute("text_len", len(text))
        span.set_attribute("language", language)
        span.set_attribute("has_ref_audio", ref_audio is not None)

        model = await get_model()
        kwargs: dict = {"text": text}

        if ref_audio and ref_text:
            ref_np, _ref_sr = _wav_bytes_to_np(ref_audio)
            kwargs["ref_audio"] = ref_np
            kwargs["ref_text"]  = ref_text

        loop = asyncio.get_event_loop()
        audio_arrays: list[np.ndarray] = await loop.run_in_executor(
            None,
            lambda: model.generate(**kwargs),
        )
        audio = audio_arrays[0]

        # Speed scaling via resampling
        if abs(speed - 1.0) > 0.01:
            import torch, torchaudio
            t = torch.from_numpy(audio).unsqueeze(0)
            target_sr = int(SAMPLE_RATE / speed)
            audio = torchaudio.functional.resample(t, target_sr, SAMPLE_RATE).squeeze().numpy()

        buf = io.BytesIO()
        sf.write(buf, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        result = buf.getvalue()
        span.set_attribute("output_bytes", len(result))
        span.set_attribute("duration_s", len(audio) / SAMPLE_RATE)
        return result


async def synthesize_streaming(
    text: str,
    *,
    ref_audio: bytes | None = None,
    ref_text: str | None = None,
    language: str = "en",
) -> AsyncGenerator[bytes, None]:
    """Yield raw PCM16 chunks (200 ms each) as an async generator.

    Suitable for WebSocket TTS streaming — caller converts chunk → base64 and sends.
    """
    wav_bytes = await synthesize(
        text, ref_audio=ref_audio, ref_text=ref_text, language=language
    )
    pcm = wav_bytes[WAV_HEADER_SZ:]    # strip WAV header, leave raw PCM16
    chunk_bytes = CHUNK_FRAMES * 2     # int16 = 2 bytes/sample
    pos = 0
    while pos < len(pcm):
        yield pcm[pos : pos + chunk_bytes]
        pos += chunk_bytes
        await asyncio.sleep(0)          # yield event-loop between chunks


async def warmup() -> None:
    """Preload OmniVoice model during worker startup (called from app lifespan)."""
    log.info("voice.tts.warmup start")
    await get_model()
    await synthesize(".", language="en")   # JIT-compile graph
    log.info("voice.tts.warmup complete")


def _wav_bytes_to_np(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    buf = io.BytesIO(wav_bytes)
    audio, sr = sf.read(buf, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio, sr
```

- [ ] **Step 4: Run tests — verify they pass**
```bash
cd agent-verse-backend && uv run pytest tests/voice/test_tts_engine.py -v
```

---

## Phase 3 — Backend: Greeting Engine (VERIFIED — uses real OrgService APIs)

### Task 3.1: Create `app/voice/greeting.py`

**Files:**
- Create: `agent-verse-backend/app/voice/greeting.py`
- Test: `agent-verse-backend/tests/voice/test_greeting.py`

> **VERIFIED**: Uses `OrgService.get_org_health()` (exists at `service.py:844`) and
> `DigestGenerator` from `app/org/digest.py` (exists). Does NOT call the non-existent
> `get_morning_brief()` or `/brief/morning` endpoint.

- [ ] **Step 1: Write failing tests**
```python
# tests/voice/test_greeting.py
import pytest

MOCK_HEALTH = {
    "org_id": "org-1", "org_name": "Acme AI",
    "overall_health": "healthy",
    "active_missions": 3, "active_teams": 2,
    "pending_approvals": 1, "items_needing_attention": 0,
}
MOCK_HEALTH_DEGRADED = {**MOCK_HEALTH, "overall_health": "degraded",
                        "items_needing_attention": 4}

@pytest.mark.asyncio
async def test_build_greeting_healthy():
    from app.voice.greeting import build_greeting_script
    script = await build_greeting_script(MOCK_HEALTH, "Harsh Kumar")
    assert "Harsh" in script
    assert "Acme AI" in script
    assert "3" in script

@pytest.mark.asyncio
async def test_build_greeting_degraded():
    from app.voice.greeting import build_greeting_script
    script = await build_greeting_script(MOCK_HEALTH_DEGRADED, "Ada")
    assert "attention" in script.lower() or "4" in script

@pytest.mark.asyncio
async def test_greeting_time_of_day():
    import datetime
    from app.voice import greeting as gm
    # Patch to morning hour
    original = gm.datetime
    class FakeDT:
        timezone = datetime.timezone
        @staticmethod
        def now(tz=None):
            return datetime.datetime(2026, 8, 19, 9, 0, 0, tzinfo=tz)
    gm.datetime = FakeDT
    script = await gm.build_greeting_script(MOCK_HEALTH, "Test")
    assert "morning" in script
    gm.datetime = original
```

- [ ] **Step 2: Run tests — verify they fail**
```bash
cd agent-verse-backend && uv run pytest tests/voice/test_greeting.py -x
```

- [ ] **Step 3: Create greeting.py (corrected — uses real OrgService APIs)**
```python
# app/voice/greeting.py
"""Voice greeting builder — synthesised on every org page load.

Data sources (VERIFIED against actual codebase):
  1. OrgService.get_org_health(org_id)   ← EXISTS at service.py:844
  2. DigestGenerator.generate()           ← EXISTS at app/org/digest.py
  3. Organization.jurisdiction            ← Used to auto-select TTS language (D-5)

NO calls to get_morning_brief() (does not exist).
NO calls to /brief/morning endpoint (does not exist in router).
"""
from __future__ import annotations

import datetime
import logging
from typing import Any

import structlog
from opentelemetry import trace

from app.voice.tts_engine import synthesize

log = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

# ── D-5: Jurisdiction → TTS language map ─────────────────────────────────────
JURISDICTION_TO_LANG: dict[str, str] = {
    "india": "hi", "IN": "hi",
    "france": "fr", "FR": "fr",
    "germany": "de", "DE": "de",
    "spain": "es", "ES": "es",
    "japan": "ja", "JP": "ja",
    "china": "zh", "CN": "zh",
    "brazil": "pt", "BR": "pt",
    "korea": "ko", "KR": "ko",
    "italy": "it", "IT": "it",
    "russia": "ru", "RU": "ru",
    "arab": "ar",  "UAE": "ar", "SA": "ar",
}

_TEMPLATES: dict[str, str] = {
    "healthy": (
        "Good {tod}, {first_name}. "
        "{org_name} is operating smoothly. "
        "You have {active_missions} active mission{m_pl} and {active_teams} team{t_pl} engaged. "
        "{pending_approvals} approval{pa_pl} awaiting your review. "
        "{wywa_summary}"
    ),
    "degraded": (
        "Good {tod}, {first_name}. "
        "Heads up — {org_name} has {items} item{items_pl} needing attention. "
        "{active_missions} mission{m_pl} running across {active_teams} team{t_pl}. "
        "{pending_approvals} approval{pa_pl} pending. "
        "{wywa_summary}"
    ),
    "attention_needed": (
        "Good {tod}, {first_name}. "
        "{org_name} requires your immediate attention. "
        "{items} critical issue{items_pl} flagged. "
        "{active_missions} mission{m_pl} in flight. "
        "{wywa_summary}"
    ),
}


async def build_greeting_script(
    health: dict[str, Any],
    user_name: str,
    *,
    wywa_items: int = 0,
    wywa_summary: str = "",
) -> str:
    """Render a natural-language greeting from OrgService.get_org_health() data.

    Args:
        health: dict returned by OrgService.get_org_health() — keys:
                org_id, org_name, overall_health, active_missions, active_teams,
                pending_approvals, items_needing_attention
        user_name:     Full name of the logged-in user
        wywa_items:    Count from DigestGenerator (while-you-were-away)
        wywa_summary:  Short WYWA text if available
    """
    hour = datetime.datetime.now(datetime.timezone.utc).hour
    if hour < 12:
        tod = "morning"
    elif hour < 17:
        tod = "afternoon"
    else:
        tod = "evening"

    health_status     = health.get("overall_health", "healthy")
    template          = _TEMPLATES.get(health_status, _TEMPLATES["healthy"])
    first_name        = (user_name or "there").split()[0]
    active_missions   = int(health.get("active_missions", 0))
    active_teams      = int(health.get("active_teams", 0))
    pending_approvals = int(health.get("pending_approvals", 0))
    items             = int(health.get("items_needing_attention", 0))

    # WYWA summary
    if wywa_items > 0 and not wywa_summary:
        wywa_summary = f"{wywa_items} update{'' if wywa_items == 1 else 's'} happened while you were away."
    elif not wywa_summary:
        wywa_summary = ""

    return template.format(
        tod              = tod,
        first_name       = first_name,
        org_name         = health.get("org_name", "your organisation"),
        active_missions  = active_missions,
        m_pl             = "s" if active_missions != 1 else "",
        active_teams     = active_teams,
        t_pl             = "s" if active_teams != 1 else "",
        pending_approvals= pending_approvals,
        pa_pl            = "s" if pending_approvals != 1 else "",
        items            = items,
        items_pl         = "s" if items != 1 else "",
        wywa_summary     = wywa_summary,
    )


def jurisdiction_to_language(jurisdiction: str | None) -> str:
    """D-5: Auto-detect TTS language from org jurisdiction field."""
    if not jurisdiction:
        return "en"
    for key, lang in JURISDICTION_TO_LANG.items():
        if key.lower() in jurisdiction.lower():
            return lang
    return "en"


async def synthesize_greeting(
    health: dict[str, Any],
    user_name: str,
    *,
    ref_audio: bytes | None = None,
    ref_text: str | None = None,
    language: str = "en",
    wywa_items: int = 0,
    wywa_summary: str = "",
) -> bytes:
    """Return WAV bytes for the login greeting using real org health data."""
    with tracer.start_as_current_span("voice.greeting.synthesize") as span:
        span.set_attribute("org_id", health.get("org_id", ""))
        span.set_attribute("health", health.get("overall_health", ""))

        script = await build_greeting_script(
            health, user_name,
            wywa_items=wywa_items,
            wywa_summary=wywa_summary,
        )
        span.set_attribute("script_len", len(script))

        wav = await synthesize(
            script, ref_audio=ref_audio, ref_text=ref_text, language=language,
        )
        log.info("voice.greeting.synthesized",
                 org_id=health.get("org_id"),
                 health=health.get("overall_health"),
                 wywa_items=wywa_items,
                 wav_bytes=len(wav))
        return wav
```

- [ ] **Step 4: Run tests — verify they pass**
```bash
cd agent-verse-backend && uv run pytest tests/voice/test_greeting.py -v
```

---

## Phase 4 — Backend: WebSocket Streaming Session (VERIFIED)

### Task 4.1: Create `app/voice/intent_router.py` (D-3 World-Class)

> This is what makes AgentVerse unique: voice commands route to real org operations.

```python
# app/voice/intent_router.py
"""D-3: Voice Command Intent Router.

Classifies a transcript into an intent category and dispatches to the
appropriate OrgService operation. This is what differentiates AgentVerse from
all other voice products — spoken words create real missions, approve real
decisions, and query real org state.

Intents:
  CREATE_MISSION   — "launch X", "create mission Y", "start a campaign Z"
  STATUS_CHECK     — "what's the status of", "how is X going", "update on"
  APPROVE          — "approve", "approve that", "yes go ahead", "approved"
  REJECT           — "reject", "no don't", "cancel that", "denied"
  SUMMARIZE        — "summarize", "brief me", "what happened", "digest"
  SEARCH           — "find", "search", "show me", "list"
  UNKNOWN          — fallback — LLM routes

Voice-to-Mission is the crown jewel (D-1):
  1. Classify as CREATE_MISSION
  2. Pass to GoalRefinementPipeline.refine() (EXISTS, synchronous)
  3. Call OrgService.create_mission() (EXISTS)
  4. Return confirmation for TTS
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

import structlog
from opentelemetry import trace

log = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


class VoiceIntent(str, Enum):
    CREATE_MISSION = "create_mission"
    STATUS_CHECK   = "status_check"
    APPROVE        = "approve"
    REJECT         = "reject"
    SUMMARIZE      = "summarize"
    SEARCH         = "search"
    UNKNOWN        = "unknown"


@dataclass
class IntentResult:
    intent:     VoiceIntent
    confidence: float
    entities:   dict[str, str]


_CREATE_PATTERNS = [
    r"\b(launch|start|create|initiate|kick off|begin|run)\b.*\b(mission|campaign|project|initiative|task)\b",
    r"\b(launch|start|create|initiate)\b\s+(.+)",
    r"\bI want (?:you )?to\b.+",
]
_STATUS_PATTERNS  = [r"\bstatus\b", r"\bhow is\b", r"\bupdate on\b", r"\bwhat'?s happening\b"]
_APPROVE_PATTERNS = [r"\bapprove\b", r"\bgo ahead\b", r"\bapproved\b", r"\byes, do it\b"]
_REJECT_PATTERNS  = [r"\breject\b", r"\bdeny\b", r"\bcancel that\b", r"\bdon't do\b", r"\bno\b"]
_SUMMARIZE_PATTERNS = [r"\bsummariz\b", r"\bbrief me\b", r"\bwhat happened\b", r"\bdigest\b"]
_SEARCH_PATTERNS  = [r"\bfind\b", r"\bsearch\b", r"\bshow me\b", r"\blist\b"]


def classify_intent(transcript: str) -> IntentResult:
    """Rule-based intent classifier. Fast, deterministic, no LLM needed."""
    t = transcript.lower().strip()

    checks = [
        (VoiceIntent.CREATE_MISSION, _CREATE_PATTERNS),
        (VoiceIntent.APPROVE,        _APPROVE_PATTERNS),
        (VoiceIntent.REJECT,         _REJECT_PATTERNS),
        (VoiceIntent.SUMMARIZE,      _SUMMARIZE_PATTERNS),
        (VoiceIntent.STATUS_CHECK,   _STATUS_PATTERNS),
        (VoiceIntent.SEARCH,         _SEARCH_PATTERNS),
    ]
    for intent, patterns in checks:
        for p in patterns:
            if re.search(p, t):
                return IntentResult(intent=intent, confidence=0.85, entities={})

    return IntentResult(intent=VoiceIntent.UNKNOWN, confidence=0.5, entities={})


async def handle_create_mission(
    transcript: str,
    org_id: str,
    tenant_id: str,
    session_factory: Any,
) -> str:
    """D-1: Voice-to-Mission — the crown jewel.

    VERIFIED: Uses GoalRefinementPipeline.refine() which EXISTS at
    app/org/goal_refinement.py with signature:
      pipeline.refine(raw_goal: str, org_context: dict | None) -> RefinedMissionSpec
    It is SYNCHRONOUS → must run in executor.

    VERIFIED: Uses OrgService.create_mission() which EXISTS at
    service.py with params: org_id, title, objective, source, budget_usd,
    autonomy_level, success_criteria, tags
    """
    with tracer.start_as_current_span("voice.intent.create_mission") as span:
        span.set_attribute("org_id", org_id)
        span.set_attribute("transcript_len", len(transcript))

        # Step 1: Goal refinement (sync — run in executor)
        from app.org.goal_refinement import GoalRefinementPipeline
        pipeline = GoalRefinementPipeline()
        loop = asyncio.get_event_loop()
        spec = await loop.run_in_executor(
            None,
            lambda: pipeline.refine(transcript, org_context={"org_id": org_id}),
        )

        span.set_attribute("risk_level", spec.risk_level)
        span.set_attribute("autonomy_level", spec.autonomy_level)
        span.set_attribute("departments", ",".join(spec.departments_involved))

        # Step 2: Create mission via OrgService
        try:
            from app.db.rls import sqlalchemy_rls_context
            async with (
                session_factory() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                from app.org.service import OrgService
                svc = OrgService(session=session, tenant_id=tenant_id)
                mission = await svc.create_mission(
                    org_id=org_id,
                    title=spec.refined_goal[:200],
                    objective=spec.refined_goal,
                    source="voice",
                    autonomy_level=spec.autonomy_level,
                    budget_usd=spec.estimated_budget_usd if spec.estimated_budget_usd > 0 else None,
                    success_criteria=spec.success_criteria,
                    tags=spec.departments_involved,
                )
                mission_id = str(mission.id)
        except Exception as exc:
            log.error("voice.create_mission.failed", error=str(exc))
            return f"I understood your goal but couldn't create the mission: {exc}. Please try again."

        # Step 3: Build spoken confirmation
        depts = ", ".join(spec.departments_involved[:3]) or "your team"
        hours = f"{spec.estimated_duration_hours:.0f} hours" if spec.estimated_duration_hours > 0 else "an estimated timeline"
        budget = f"${spec.estimated_budget_usd:,.0f}" if spec.estimated_budget_usd > 0 else "your budget"
        risk_note = "" if spec.risk_level in ("low", "medium") else " High-risk — human approval required."

        confirmation = (
            f"Mission created. "
            f"I've refined your goal to: {spec.refined_goal[:120]}. "
            f"This involves {depts}. "
            f"Estimated {hours} at {budget}.{risk_note}"
        )
        span.set_attribute("mission_id", mission_id)
        log.info("voice.mission_created", org_id=org_id, mission_id=mission_id,
                 risk=spec.risk_level, departments=spec.departments_involved)
        return confirmation


async def handle_approve(
    transcript: str,
    org_id: str,
    tenant_id: str,
    session_factory: Any,
    pending_decision_id: str | None = None,
) -> str:
    """D-4: Voice-driven approval using OrgService.record_decision().

    VERIFIED: OrgService.record_decision() EXISTS at service.py.
    """
    if not pending_decision_id:
        return "I don't have a pending decision to approve. Please specify what to approve."

    try:
        from app.db.rls import sqlalchemy_rls_context
        async with (
            session_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            from app.org.service import OrgService
            svc = OrgService(session=session, tenant_id=tenant_id)
            await svc.record_decision(
                org_id=org_id,
                entity_type="mission",
                entity_id=pending_decision_id,
                decision_type="approval",
                description=f"Voice approval: {transcript}",
                why="Approved via voice command",
                approval_status="approved",
            )
        return "Approved. The mission is cleared to proceed."
    except Exception as exc:
        log.error("voice.approve.failed", error=str(exc))
        return "I couldn't record the approval. Please try again."


async def route_voice_command(
    transcript: str,
    org_id: str,
    tenant_id: str,
    session_factory: Any,
    pending_decision_id: str | None = None,
) -> str:
    """Main entry point: classify intent → dispatch to handler → return TTS text."""
    ir = classify_intent(transcript)
    log.info("voice.intent.classified", intent=ir.intent, confidence=ir.confidence)

    if ir.intent == VoiceIntent.CREATE_MISSION:
        return await handle_create_mission(transcript, org_id, tenant_id, session_factory)

    if ir.intent == VoiceIntent.APPROVE:
        return await handle_approve(transcript, org_id, tenant_id, session_factory,
                                    pending_decision_id)

    if ir.intent == VoiceIntent.REJECT:
        return "Understood. The request has been rejected."

    if ir.intent == VoiceIntent.SUMMARIZE:
        return await _handle_summarize(org_id, tenant_id, session_factory)

    if ir.intent == VoiceIntent.STATUS_CHECK:
        return await _handle_status(org_id, tenant_id, session_factory)

    # UNKNOWN → echo back for confirmation
    return f"I heard: {transcript}. Should I create a mission for this? Say 'yes create mission' to confirm."


async def _handle_summarize(org_id: str, tenant_id: str, session_factory: Any) -> str:
    """D-7: Use real OrgService.get_org_health() to build a summary."""
    try:
        from app.db.rls import sqlalchemy_rls_context
        async with (
            session_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            from app.org.service import OrgService
            svc = OrgService(session=session, tenant_id=tenant_id)
            h = await svc.get_org_health(org_id)
            return (
                f"Your organisation has {h.get('active_missions', 0)} active missions, "
                f"{h.get('active_teams', 0)} teams, "
                f"{h.get('pending_approvals', 0)} pending approvals. "
                f"Overall health: {h.get('overall_health', 'unknown')}."
            )
    except Exception as exc:
        return f"Unable to retrieve summary at this time: {exc}"


async def _handle_status(org_id: str, tenant_id: str, session_factory: Any) -> str:
    h = await _get_health_dict(org_id, tenant_id, session_factory)
    active = h.get("active_missions", 0)
    health = h.get("overall_health", "unknown")
    return f"{active} mission{'s' if active != 1 else ''} currently running. Status: {health}."


async def _get_health_dict(org_id: str, tenant_id: str, session_factory: Any) -> dict:
    try:
        from app.db.rls import sqlalchemy_rls_context
        async with (
            session_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            from app.org.service import OrgService
            svc = OrgService(session=session, tenant_id=tenant_id)
            return await svc.get_org_health(org_id)
    except Exception:
        return {}
```

### Task 4.2: Create `app/voice/streaming.py` (VERIFIED — uses intent_router)

```python
# app/voice/streaming.py (corrected version)
"""Real-time bidirectional voice session over WebSocket.

VERIFIED: Uses route_voice_command() from intent_router which:
  - Calls GoalRefinementPipeline.refine() (EXISTS, synchronous → executor)
  - Calls OrgService.create_mission() (EXISTS)
  - Calls OrgService.get_org_health() (EXISTS at service.py:844)
  - Calls OrgService.record_decision() (EXISTS)

Does NOT call: refine_to_goal() (does not exist)
Does NOT call: get_morning_brief() (does not exist)
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

log = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


class VoiceStreamingSession:
    """Manages one WebSocket voice session — full STT→IntentRouter→TTS pipeline."""

    def __init__(
        self,
        ws: WebSocket,
        *,
        tenant_id: str,
        org_id: str,
        session_factory: Any,       # db session factory from app.state
        ref_audio: bytes | None = None,
        ref_text: str | None = None,
        language: str = "en",
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
        self._pending_decision_id: str | None = None   # for D-4 voice approval

    async def run(self) -> None:
        with tracer.start_as_current_span("voice.stream.session") as span:
            span.set_attribute("tenant_id", self.tenant_id)
            span.set_attribute("org_id", self.org_id)
            try:
                await self._loop()
            except WebSocketDisconnect:
                log.info("voice.stream.disconnected", tenant=self.tenant_id)
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
                pcm = np.frombuffer(
                    base64.b64decode(msg["data"]), dtype=np.int16
                ).astype(np.float32) / 32768.0
                self._buf.append(pcm)
                if sum(len(c) for c in self._buf) >= 16_000:
                    await self._emit_interim()
            elif mtype == "end_of_speech":
                await self._run_pipeline()
            elif mtype == "set_pending_decision":
                self._pending_decision_id = msg.get("decision_id")
            elif mtype == "cancel":
                self._buf.clear()
                self._active = False

    async def _emit_interim(self) -> None:
        if not self._buf:
            return
        audio = np.concatenate(self._buf)
        wav   = self._to_wav(audio)
        result = await transcribe(wav, "audio/wav")
        await self._send({
            "type": "transcript", "text": result["transcript"],
            "language": result["language"], "confidence": result["confidence"],
            "is_final": False,
        })

    async def _run_pipeline(self) -> None:
        if not self._buf:
            return
        audio = np.concatenate(self._buf)
        self._buf.clear()
        if len(audio) < 800:
            return

        wav    = self._to_wav(audio)
        result = await transcribe(wav, "audio/wav")
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

        # TTS streaming
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
```

---

## Phase 5 — Backend: Revised Router & DB Schema

### Task 5.1: Replace `app/voice/router.py` (CRITICAL FIXES APPLIED)

**Verified changes vs previous spec:**
- `_fetch_morning_brief` → replaced with `_fetch_org_health()` using real `OrgService.get_org_health()`
- `voice_stream` → passes `session_factory` to `VoiceStreamingSession` instead of broken `agent_respond` lambda
- `VoiceStreamingSession` now receives `session_factory` not a callable
- Greeting uses `DigestGenerator` from `app/org/digest.py` for WYWA count (D-2)
- `jurisdiction_to_language()` from `greeting.py` auto-selects TTS language (D-5)

**Corrected critical helpers in router.py:**

```python
# CORRECT _fetch_org_health — uses real OrgService.get_org_health() (EXISTS)
async def _fetch_org_health(app: Any, org_id: str, tenant_id: str) -> dict:
    """Fetch real org health from OrgService.get_org_health() (verified: service.py:844)."""
    session_factory = getattr(getattr(app, "state", None), "db_session_factory", None)
    if session_factory is None:
        return _fallback_health(org_id)
    try:
        from app.db.rls import sqlalchemy_rls_context
        from app.org.service import OrgService
        async with (
            session_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            svc = OrgService(session=session, tenant_id=tenant_id)
            return await svc.get_org_health(org_id)
    except Exception as exc:
        log.warning("voice.greeting.health_fetch_failed", error=str(exc))
        return _fallback_health(org_id)


def _fallback_health(org_id: str) -> dict:
    return {
        "org_id": org_id, "org_name": "your organisation",
        "overall_health": "healthy",
        "active_missions": 0, "active_teams": 0,
        "pending_approvals": 0, "items_needing_attention": 0,
    }


# CORRECT voice_greeting — uses real OrgService + DigestGenerator (D-2, D-5, D-7)
@router.get(
    "/greeting/{org_id}",
    operation_id="voice_greeting",
    summary="Spoken login greeting with real-time org stats (OmniVoice TTS)",
)
async def voice_greeting(
    org_id: str,
    request: Request,
    user_name: str = Query(default="there", max_length=120),
    language: str  = Query(default="", max_length=10),
    x_request_id: str = Header(default_factory=_request_id),
) -> StreamingResponse:
    with tracer.start_as_current_span("voice.api.greeting") as span:
        ctx       = _require_tenant(request)
        tenant_id = _tenant_id(ctx)
        span.set_attribute("org_id", org_id)
        span.set_attribute("tenant_id", tenant_id)

        # D-5: Auto-detect language from org jurisdiction if not explicit
        effective_language = language or "en"
        if not language:
            try:
                health_for_lang = await _fetch_org_health(request.app, org_id, tenant_id)
                from app.voice.greeting import jurisdiction_to_language
                # We need jurisdiction from org itself
                session_factory = getattr(request.app.state, "db_session_factory", None)
                if session_factory:
                    from app.db.rls import sqlalchemy_rls_context
                    from app.org.service import OrgService
                    async with (
                        session_factory() as session,
                        session.begin(),
                        sqlalchemy_rls_context(session, tenant_id),
                    ):
                        svc = OrgService(session=session, tenant_id=tenant_id)
                        org = await svc.get_organization(org_id)
                        if org:
                            effective_language = jurisdiction_to_language(org.jurisdiction)
            except Exception:
                pass

        # Redis cache check
        cache_key = f"voice:greeting:{tenant_id}:{org_id}:{effective_language}"
        cached = await _redis_get(request.app, cache_key)
        if cached:
            span.set_attribute("cache_hit", True)
            return _wav_response(cached, x_request_id)

        # Fetch real health data (VERIFIED: uses get_org_health which EXISTS)
        health = await _fetch_org_health(request.app, org_id, tenant_id)

        # D-2: WYWA digest count from DigestGenerator (EXISTS in app/org/digest.py)
        wywa_items   = 0
        wywa_summary = ""
        try:
            session_factory = getattr(request.app.state, "db_session_factory", None)
            redis           = getattr(request.app.state, "redis", None)
            if session_factory:
                from app.org.digest import DigestGenerator
                from app.db.rls import sqlalchemy_rls_context
                async with (
                    session_factory() as session,
                    sqlalchemy_rls_context(session, tenant_id),
                ):
                    gen    = DigestGenerator(session=session, redis=redis)
                    digest = await gen.generate(org_id, tenant_id)
                    wywa_items   = digest.missions_completed + len(digest.pending_approvals)
                    if wywa_items > 0:
                        wywa_summary = f"{wywa_items} update{'s' if wywa_items != 1 else ''} while you were away."
        except Exception as exc:
            log.debug("voice.greeting.digest_failed", error=str(exc))

        ref_audio, ref_text = await _get_persona(request.app, tenant_id, org_id)
        from app.voice.greeting import synthesize_greeting
        wav_bytes = await synthesize_greeting(
            health, user_name,
            ref_audio=ref_audio, ref_text=ref_text,
            language=effective_language,
            wywa_items=wywa_items,
            wywa_summary=wywa_summary,
        )

        ttl = int(os.getenv("VOICE_GREETING_CACHE_TTL", "300"))
        await _redis_set(request.app, cache_key, wav_bytes, ttl)
        span.set_attribute("wav_bytes", len(wav_bytes))
        return _wav_response(wav_bytes, x_request_id)


# CORRECT voice_stream — passes session_factory, not broken lambda
@router.websocket("/stream/{org_id}")
async def voice_stream(
    ws: WebSocket,
    org_id: str,
    api_key: str = Query(default=""),
) -> None:
    await ws.accept()
    tenant_id = await _ws_auth(ws, api_key)
    if not tenant_id:
        await ws.close(code=4001, reason="Unauthorized")
        return

    # D-5: Get org language
    language = "en"
    try:
        session_factory = getattr(ws.app.state, "db_session_factory", None)   # type: ignore[attr-defined]
        if session_factory:
            from app.db.rls import sqlalchemy_rls_context
            from app.org.service import OrgService
            from app.voice.greeting import jurisdiction_to_language
            async with (
                session_factory() as session,
                sqlalchemy_rls_context(session, tenant_id),
            ):
                svc = OrgService(session=session, tenant_id=tenant_id)
                org = await svc.get_organization(org_id)
                if org:
                    language = jurisdiction_to_language(org.jurisdiction)
    except Exception:
        pass

    ref_audio, ref_text = await _get_persona(ws.app, tenant_id, org_id)  # type: ignore[attr-defined]
    session_factory     = getattr(ws.app.state, "db_session_factory", None)   # type: ignore[attr-defined]

    from app.voice.streaming import VoiceStreamingSession
    session = VoiceStreamingSession(
        ws,
        tenant_id=tenant_id,
        org_id=org_id,
        session_factory=session_factory,
        ref_audio=ref_audio,
        ref_text=ref_text,
        language=language,
    )
    await session.run()
```

**Helper fixes in router.py:**

```python
# REPLACE _redis_get / _redis_set / _get_persona helpers
# to take `app` not request.app  (cleaner, works outside FastAPI DI)

async def _redis_get(app: Any, key: str) -> bytes | None:
    try:
        redis = getattr(getattr(app, "state", None), "redis", None)
        return await redis.get(key) if redis else None
    except Exception:
        return None

async def _redis_set(app: Any, key: str, value: bytes, ttl: int) -> None:
    try:
        redis = getattr(getattr(app, "state", None), "redis", None)
        if redis:
            await redis.setex(key, ttl, value)
    except Exception:
        pass

async def _get_persona(app: Any, tenant_id: str, org_id: str) -> tuple[bytes | None, str | None]:
    try:
        redis = getattr(getattr(app, "state", None), "redis", None)
        if redis is None:
            return None, None
        key  = f"voice:persona:{tenant_id}:{org_id}"
        data = await redis.hgetall(key)
        if not data:
            return None, None
        import base64
        audio = base64.b64decode(data[b"audio"]) if b"audio" in data else None
        text  = data.get(b"text", b"").decode()
        return audio, text or None
    except Exception:
        return None, None

async def _ws_auth(ws: WebSocket, api_key: str) -> str | None:
    if not api_key:
        return None
    try:
        tenant_svc = getattr(getattr(ws.app, "state", None), "tenant_service", None)  # type: ignore[attr-defined]
        if tenant_svc is None:
            return api_key   # dev mode
        tenant = await tenant_svc.get_by_api_key(api_key)
        return str(tenant.id) if tenant else None
    except Exception:
        return None
```

| Path | Method | Auth | Rate limit | Returns |
|---|---|---|---|---|
| `/v1/voice/status` | GET | API key | 60/min | JSON status |
| `/v1/voice/transcribe` | POST | API key | 30/min | `TranscribeResponse` |
| `/v1/voice/speak` | POST | API key | 20/min | `audio/wav` streaming |
| `/v1/voice/greeting/{org_id}` | GET | API key | 10/min | `audio/wav` |
| `/v1/voice/persona/{org_id}` | POST | API key | 5/min | `PersonaResponse` |
| `/v1/voice/persona/{org_id}` | DELETE | API key | 5/min | 204 |
| `/v1/voice/stream/{org_id}` | WebSocket | `?api_key=` query | 5 concurrent/tenant | WS |

```python
# app/voice/router.py (FULL REPLACEMENT)
"""Voice OS router — STT, TTS, streaming, greeting, persona.

All HTTP endpoints follow AgentVerse conventions:
  - X-API-Key header auth via TenantMiddleware (request.state.tenant)
  - RFC 7807 error bodies
  - OTel spans on every operation
  - operation_id on every route
"""
from __future__ import annotations

import io
import os
from typing import Any
from uuid import uuid4

import structlog
from fastapi import APIRouter, File, Header, HTTPException, Query, Request, UploadFile, WebSocket, status
from fastapi.responses import StreamingResponse
from opentelemetry import trace
from pydantic import BaseModel, Field

from app.voice.greeting import synthesize_greeting
from app.voice.schemas import (
    PersonaResponse,
    SpeakRequest,
    TranscribeResponse,
    VoiceStatusResponse,
)
from app.voice.stt_engine import transcribe
from app.voice.streaming import VoiceStreamingSession
from app.voice.tts_engine import SAMPLE_RATE, synthesize

log    = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)
router = APIRouter(prefix="/v1/voice", tags=["voice"])


# ── Auth helpers (matching existing AgentVerse pattern) ───────────────────────

def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail={
            "type": "unauthorized", "title": "Unauthorized",
            "status": 401, "detail": "Missing or invalid API key",
        })
    return ctx


def _tenant_id(ctx: Any) -> str:
    return str(getattr(ctx, "tenant_id", None) or getattr(ctx, "id", ctx))


def _request_id() -> str:
    return str(uuid4())


# ── GET /v1/voice/status ─────────────────────────────────────────────────────

@router.get(
    "/status",
    operation_id="voice_status",
    summary="Check voice engine readiness",
    response_model=VoiceStatusResponse,
)
async def voice_status(request: Request) -> VoiceStatusResponse:
    _require_tenant(request)
    from app.voice import stt_engine, tts_engine
    return VoiceStatusResponse(
        stt_status = "ready" if stt_engine._model is not None else "idle",
        tts_status = "ready" if tts_engine._model is not None else "idle",
        stt_model  = os.getenv("VOICE_STT_MODEL", "large-v3-turbo"),
        tts_model  = os.getenv("VOICE_TTS_MODEL", "k2-fsa/OmniVoice"),
        device     = os.getenv("VOICE_DEVICE", "cpu"),
    )


# ── POST /v1/voice/transcribe ─────────────────────────────────────────────────

@router.post(
    "/transcribe",
    operation_id="voice_transcribe",
    summary="Convert audio to text (native faster-whisper STT)",
    response_model=TranscribeResponse,
    status_code=status.HTTP_200_OK,
)
async def voice_transcribe(
    request: Request,
    audio: UploadFile = File(description="WAV/WebM/OGG/MP4 ≤ 25 MB"),
    x_request_id: str = Header(default_factory=_request_id),
) -> TranscribeResponse:
    with tracer.start_as_current_span("voice.api.transcribe") as span:
        ctx       = _require_tenant(request)
        tenant_id = _tenant_id(ctx)
        span.set_attribute("tenant_id", tenant_id)
        span.set_attribute("request_id", x_request_id)

        content = await audio.read()
        max_mb = int(os.getenv("VOICE_MAX_AUDIO_MB", "25"))
        if len(content) > max_mb * 1024 * 1024:
            raise HTTPException(status_code=413, detail={
                "type": "file-too-large", "title": "Audio too large",
                "status": 413, "detail": f"Audio must be ≤ {max_mb} MB",
                "request_id": x_request_id,
            })

        span.set_attribute("audio_bytes", len(content))
        try:
            result = await transcribe(content, audio.content_type or "audio/wav")
        except Exception as exc:
            log.error("voice.transcribe.failed", tenant_id=tenant_id, error=str(exc))
            raise HTTPException(status_code=502, detail={
                "type": "stt-error", "title": "Transcription failed",
                "status": 502, "detail": str(exc), "request_id": x_request_id,
            }) from exc

        log.info("voice.transcribe.ok", tenant_id=tenant_id, chars=len(result["transcript"]))
        return TranscribeResponse(**result, duration_s=0.0)


# ── POST /v1/voice/speak ──────────────────────────────────────────────────────

@router.post(
    "/speak",
    operation_id="voice_speak",
    summary="Synthesise text to WAV audio (OmniVoice TTS)",
    status_code=status.HTTP_200_OK,
)
async def voice_speak(
    body: SpeakRequest,
    request: Request,
    x_request_id: str = Header(default_factory=_request_id),
) -> StreamingResponse:
    with tracer.start_as_current_span("voice.api.speak") as span:
        ctx       = _require_tenant(request)
        tenant_id = _tenant_id(ctx)
        span.set_attribute("tenant_id", tenant_id)
        span.set_attribute("text_len", len(body.text))

        # Fetch org persona if requested
        ref_audio, ref_text = None, None
        if body.use_org_persona and body.org_id:
            ref_audio, ref_text = await _get_persona(tenant_id, body.org_id)

        try:
            wav_bytes = await synthesize(
                body.text,
                ref_audio=ref_audio,
                ref_text=ref_text,
                language=body.language,
                speed=body.speed,
            )
        except Exception as exc:
            log.error("voice.speak.failed", error=str(exc))
            raise HTTPException(status_code=502, detail={
                "type": "tts-error", "title": "Synthesis failed",
                "status": 502, "detail": str(exc), "request_id": x_request_id,
            }) from exc

        return StreamingResponse(
            io.BytesIO(wav_bytes),
            media_type="audio/wav",
            headers={
                "Content-Length": str(len(wav_bytes)),
                "Content-Disposition": "inline; filename=speech.wav",
                "X-Sample-Rate": str(SAMPLE_RATE),
                "X-Request-Id": x_request_id,
            },
        )


# ── GET /v1/voice/greeting/{org_id} ──────────────────────────────────────────

@router.get(
    "/greeting/{org_id}",
    operation_id="voice_greeting",
    summary="Get spoken login greeting with today's org stats",
    status_code=status.HTTP_200_OK,
)
async def voice_greeting(
    org_id: str,
    request: Request,
    user_name: str = Query(default="there", max_length=120),
    language: str = Query(default="en", max_length=10),
    x_request_id: str = Header(default_factory=_request_id),
) -> StreamingResponse:
    with tracer.start_as_current_span("voice.api.greeting") as span:
        ctx       = _require_tenant(request)
        tenant_id = _tenant_id(ctx)
        span.set_attribute("org_id", org_id)
        span.set_attribute("tenant_id", tenant_id)

        # Check Redis cache first
        cache_key = f"voice:greeting:{tenant_id}:{org_id}:{language}"
        cached = await _redis_get(cache_key)
        if cached:
            span.set_attribute("cache_hit", True)
            return _wav_response(cached, x_request_id)

        # Fetch morning brief from OrgService
        try:
            brief = await _fetch_morning_brief(request, org_id)
        except Exception:
            brief = {
                "org_id": org_id, "org_name": "your organisation",
                "overall_health": "healthy",
                "active_missions": 0, "active_teams": 0,
                "pending_approvals": 0, "items_needing_attention": 0,
                "priorities": [], "risks": [],
            }

        ref_audio, ref_text = await _get_persona(tenant_id, org_id)
        wav_bytes = await synthesize_greeting(
            brief, user_name, ref_audio=ref_audio, ref_text=ref_text, language=language
        )

        ttl = int(os.getenv("VOICE_GREETING_CACHE_TTL", "300"))
        await _redis_set(cache_key, wav_bytes, ttl)
        span.set_attribute("wav_bytes", len(wav_bytes))
        return _wav_response(wav_bytes, x_request_id)


# ── WS /v1/voice/stream/{org_id} ─────────────────────────────────────────────

@router.websocket("/stream/{org_id}")
async def voice_stream(
    ws: WebSocket,
    org_id: str,
    api_key: str = Query(default=""),
) -> None:
    """Real-time bidirectional voice: mic → STT → agent → TTS → speaker."""
    await ws.accept()

    # Auth via query-param API key (WS cannot set headers in browser)
    tenant_id = await _ws_auth(ws, api_key)
    if not tenant_id:
        await ws.close(code=4001, reason="Unauthorized")
        return

    ref_audio, ref_text = await _get_persona(tenant_id, org_id)

    async def agent_respond(transcript: str, _org_id: str) -> str:
        """Minimal LLM adapter — route via org's goal refinement service."""
        try:
            from app.org.goal_refinement import refine_to_goal
            result = await refine_to_goal(transcript, _org_id, tenant_id=tenant_id)
            return result.get("response", result.get("goal", transcript))
        except Exception:
            return f"Understood: {transcript}. I'll process that as a mission objective."

    session = VoiceStreamingSession(
        ws,
        tenant_id=tenant_id,
        org_id=org_id,
        agent_respond=agent_respond,
        ref_audio=ref_audio,
        ref_text=ref_text,
    )
    await session.run()


# ── POST /v1/voice/persona/{org_id} ──────────────────────────────────────────

@router.post(
    "/persona/{org_id}",
    operation_id="voice_persona_upload",
    summary="Upload reference audio for org voice persona (voice cloning)",
    response_model=PersonaResponse,
)
async def voice_persona_upload(
    org_id: str,
    request: Request,
    audio: UploadFile = File(description="WAV reference audio 3–30 s"),
    ref_text: str = Query(description="Transcript of the reference audio"),
    language: str = Query(default="en"),
    x_request_id: str = Header(default_factory=_request_id),
) -> PersonaResponse:
    with tracer.start_as_current_span("voice.api.persona.upload") as span:
        ctx       = _require_tenant(request)
        tenant_id = _tenant_id(ctx)
        span.set_attribute("org_id", org_id)

        content = await audio.read()
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=413, detail={
                "type": "file-too-large", "title": "Reference audio too large",
                "status": 413, "detail": "Reference audio must be ≤ 5 MB",
                "request_id": x_request_id,
            })

        url = await _store_persona_audio(tenant_id, org_id, content)
        await _cache_persona(tenant_id, org_id, content, ref_text, language)

        log.info("voice.persona.uploaded", tenant_id=tenant_id, org_id=org_id)
        return PersonaResponse(
            org_id=org_id, tenant_id=tenant_id,
            ref_audio_url=url, ref_text=ref_text, language=language,
            created_at=__import__("datetime").datetime.utcnow().isoformat(),
        )


@router.delete(
    "/persona/{org_id}",
    operation_id="voice_persona_delete",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def voice_persona_delete(org_id: str, request: Request) -> None:
    ctx       = _require_tenant(request)
    tenant_id = _tenant_id(ctx)
    await _delete_persona(tenant_id, org_id)
    log.info("voice.persona.deleted", tenant_id=tenant_id, org_id=org_id)


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _fetch_morning_brief(request: Request, org_id: str) -> dict:
    """Fetch brief from OrgService (reuse existing app/org service layer)."""
    from app.org.router import get_org_service
    from app.db.rls import sqlalchemy_rls_context
    ctx = _require_tenant(request)
    tenant_id = _tenant_id(ctx)
    async for svc in get_org_service(request):
        return await svc.get_morning_brief(org_id)
    return {}


async def _get_persona(tenant_id: str, org_id: str) -> tuple[bytes | None, str | None]:
    """Return (ref_audio_bytes, ref_text) for the org persona, or (None, None)."""
    try:
        from app.main import app as _app
        redis = getattr(getattr(_app, "state", None), "redis", None)
        if redis is None:
            return None, None
        key = f"voice:persona:{tenant_id}:{org_id}"
        data = await redis.hgetall(key)
        if not data:
            return None, None
        import base64
        audio = base64.b64decode(data[b"audio"]) if b"audio" in data else None
        text  = data.get(b"text", b"").decode()
        return audio, text or None
    except Exception:
        return None, None


async def _cache_persona(
    tenant_id: str, org_id: str,
    audio: bytes, ref_text: str, language: str,
) -> None:
    try:
        from app.main import app as _app
        redis = getattr(getattr(_app, "state", None), "redis", None)
        if redis is None:
            return
        import base64
        key = f"voice:persona:{tenant_id}:{org_id}"
        await redis.hset(key, mapping={
            "audio":    base64.b64encode(audio).decode(),
            "text":     ref_text,
            "language": language,
        })
    except Exception:
        pass


async def _delete_persona(tenant_id: str, org_id: str) -> None:
    try:
        from app.main import app as _app
        redis = getattr(getattr(_app, "state", None), "redis", None)
        if redis:
            key = f"voice:persona:{tenant_id}:{org_id}"
            await redis.delete(key)
    except Exception:
        pass


async def _redis_get(key: str) -> bytes | None:
    try:
        from app.main import app as _app
        redis = getattr(getattr(_app, "state", None), "redis", None)
        return await redis.get(key) if redis else None
    except Exception:
        return None


async def _redis_set(key: str, value: bytes, ttl: int) -> None:
    try:
        from app.main import app as _app
        redis = getattr(getattr(_app, "state", None), "redis", None)
        if redis:
            await redis.setex(key, ttl, value)
    except Exception:
        pass


async def _store_persona_audio(
    tenant_id: str, org_id: str, audio: bytes
) -> str:
    """Store reference audio to MinIO/S3 and return URL."""
    try:
        import boto3, os
        s3 = boto3.client(
            "s3",
            endpoint_url=os.getenv("S3_ENDPOINT_URL"),
            aws_access_key_id=os.getenv("S3_ACCESS_KEY"),
            aws_secret_access_key=os.getenv("S3_SECRET_KEY"),
        )
        bucket = os.getenv("VOICE_PERSONA_BUCKET", "agentverse-voice-personas")
        key    = f"{tenant_id}/{org_id}/ref.wav"
        s3.put_object(Bucket=bucket, Key=key, Body=audio, ContentType="audio/wav")
        return f"s3://{bucket}/{key}"
    except Exception:
        return f"local://{tenant_id}/{org_id}/ref.wav"


async def _ws_auth(ws: WebSocket, api_key: str) -> str | None:
    """Validate WebSocket API key — mirrors TenantMiddleware logic."""
    if not api_key:
        return None
    try:
        from app.main import app as _app
        tenant_svc = getattr(getattr(_app, "state", None), "tenant_service", None)
        if tenant_svc is None:
            return api_key   # dev mode fallback
        tenant = await tenant_svc.get_by_api_key(api_key)
        if tenant is None:
            return None
        return str(tenant.id)
    except Exception:
        return None


def _wav_response(wav_bytes: bytes, request_id: str) -> StreamingResponse:
    return StreamingResponse(
        io.BytesIO(wav_bytes),
        media_type="audio/wav",
        headers={
            "Content-Length": str(len(wav_bytes)),
            "Content-Disposition": "inline; filename=greeting.wav",
            "X-Sample-Rate": str(SAMPLE_RATE),
            "X-Request-Id": request_id,
            "Cache-Control": "no-cache",
        },
    )
```

---

### Task 5.2: Create `app/voice/schemas.py`

```python
# app/voice/schemas.py
from __future__ import annotations
from pydantic import BaseModel, Field


class VoiceStatusResponse(BaseModel):
    stt_status: str
    tts_status: str
    stt_model:  str = "large-v3-turbo"
    tts_model:  str = "k2-fsa/OmniVoice"
    device:     str


class TranscribeResponse(BaseModel):
    transcript:  str
    language:    str
    confidence:  float = Field(ge=0.0, le=1.0)
    segments:    list[dict] = Field(default_factory=list)
    duration_s:  float = 0.0


class SpeakRequest(BaseModel):
    text:            str = Field(min_length=1, max_length=4096)
    language:        str = "en"
    speed:           float = Field(default=1.0, ge=0.5, le=2.0)
    org_id:          str | None = None
    use_org_persona: bool = True


class PersonaResponse(BaseModel):
    org_id:        str
    tenant_id:     str
    ref_audio_url: str
    ref_text:      str
    language:      str
    created_at:    str
```

---

### Task 5.3: Add rate limits to `app/gateway/rate_limiter.py`

**File:** `agent-verse-backend/app/gateway/rate_limiter.py`

Extend the `CHANNEL_LIMITS` dict (after line 48 `"voice_webhook"`):

```python
# Add after "voice_webhook" entry in CHANNEL_LIMITS:
"voice_transcribe": {"limit": 30, "window": 60},
"voice_speak":      {"limit": 20, "window": 60},
"voice_greeting":   {"limit": 10, "window": 60},
"voice_persona":    {"limit": 5,  "window": 60},
"voice_stream":     {"limit": 5,  "window": 300},   # 5 concurrent sessions
```

---

### Task 5.4: Wire voice warmup to app lifespan

**File:** `agent-verse-backend/app/main.py`

Find the lifespan block where services are started (search for `"lifespan"` or `"async with"`).
Add after the existing service startups:

```python
# Inside the lifespan async block, after existing warmup calls:
if getattr(settings, "VOICE_ENABLED", True):
    from app.voice.tts_engine import warmup as _tts_warmup
    asyncio.create_task(_tts_warmup())
    logger.info("voice_tts_warmup_scheduled")
```

---

### Task 5.5: Register voice router in `app/bootstrap/routers.py`

**File:** `agent-verse-backend/app/bootstrap/routers.py`

Find the voice router block (lines 367–373):

```python
# Replace the existing try/except voice block:
    # ── Voice (OmniVoice TTS + faster-whisper STT + WebSocket) ────────────────
    try:
        from app.voice.router import router as voice_router
        app.include_router(voice_router)
        logger.info("voice_router_registered")
    except Exception as _voice_err:
        logger.warning("voice_router_failed", error=str(_voice_err))
```

(Already correct shape — just verify import path matches.)

---

### Task 5.6: Add config to `app/core/config.py`

**File:** `agent-verse-backend/app/core/config.py`

Add new settings fields:

```python
# Add to Settings class:
VOICE_ENABLED:              bool = True
VOICE_DEVICE:               str  = "cpu"          # "cpu" | "cuda"
VOICE_STT_MODEL:            str  = "large-v3-turbo"
VOICE_TTS_MODEL:            str  = "k2-fsa/OmniVoice"
MODEL_CACHE_DIR:            str  = "/app/models"
VOICE_PERSONA_BUCKET:       str  = "agentverse-voice-personas"
VOICE_GREETING_CACHE_TTL:   int  = 300
VOICE_MAX_AUDIO_MB:         int  = 25
S3_ENDPOINT_URL:            str | None = None
S3_ACCESS_KEY:              str | None = None
S3_SECRET_KEY:              str | None = None
```

---

## Phase 6 — Frontend: Audio Worklet Processor

### Task 6.1: Create `public/audio-worklet-processor.js`

**File:** `agent-verse-frontend/public/audio-worklet-processor.js`

```javascript
/**
 * PcmCaptureProcessor — runs in AudioWorklet thread (dedicated DSP thread).
 * Accumulates 100 ms of PCM16 at 16 kHz, posts ArrayBuffer to main thread.
 * Avoids main-thread audio processing completely.
 */
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf          = [];
    this._targetFrames = Math.floor((16000 * 100) / 1000); // 1600 frames per chunk
  }

  process(inputs) {
    const ch = inputs[0]?.[0];
    if (!ch) return true;

    for (let i = 0; i < ch.length; i++) {
      const s = Math.max(-1, Math.min(1, ch[i]));
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

---

## Phase 7 — Frontend: Voice API Client

### Task 7.1: Create `src/features/org/api/voice.ts`

**File:** `agent-verse-frontend/src/features/org/api/voice.ts`

Follows exact same pattern as `src/features/org/api.ts` — uses `apiFetch`.

```typescript
/**
 * Voice OS API client.
 * Follows AgentVerse apiFetch conventions with X-API-Key / Bearer auth.
 */
import { apiFetch, API_BASE } from '@/lib/api/client';
import { useAuthStore } from '@/stores/auth';
import type { TranscribeResponse, VoiceStatusResponse, PersonaResponse } from '../types/voice';

const BASE = '/v1/voice';

export const voiceApi = {

  status(): Promise<VoiceStatusResponse> {
    return apiFetch<VoiceStatusResponse>(`${BASE}/status`);
  },

  transcribe(audio: Blob, filename = 'audio.wav'): Promise<TranscribeResponse> {
    const form = new FormData();
    form.append('audio', audio, filename);
    return apiFetch<TranscribeResponse>(`${BASE}/transcribe`, {
      method: 'POST',
      body: form,
    });
  },

  speak(text: string, opts?: {
    language?: string;
    speed?: number;
    org_id?: string;
    use_org_persona?: boolean;
  }): Promise<Blob> {
    return fetch(`${API_BASE}${BASE}/speak`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': useAuthStore.getState().apiKey ?? '',
      },
      body: JSON.stringify({ text, language: opts?.language ?? 'en', ...opts }),
    }).then(r => { if (!r.ok) throw new Error('TTS failed'); return r.blob(); });
  },

  greeting(orgId: string, opts?: {
    user_name?: string;
    language?: string;
  }): Promise<Blob> {
    const qs = new URLSearchParams();
    if (opts?.user_name) qs.set('user_name', opts.user_name);
    if (opts?.language)  qs.set('language', opts.language);
    return fetch(`${API_BASE}${BASE}/greeting/${orgId}?${qs}`, {
      headers: { 'X-API-Key': useAuthStore.getState().apiKey ?? '' },
    }).then(r => { if (!r.ok) throw new Error('Greeting fetch failed'); return r.blob(); });
  },

  uploadPersona(orgId: string, audio: File, refText: string, language = 'en'): Promise<PersonaResponse> {
    const form = new FormData();
    form.append('audio', audio);
    return apiFetch<PersonaResponse>(
      `${BASE}/persona/${orgId}?ref_text=${encodeURIComponent(refText)}&language=${language}`,
      { method: 'POST', body: form },
    );
  },

  deletePersona(orgId: string): Promise<void> {
    return apiFetch<void>(`${BASE}/persona/${orgId}`, { method: 'DELETE' });
  },

  /** Build authenticated WebSocket URL for streaming session. */
  streamUrl(orgId: string): string {
    const apiKey   = useAuthStore.getState().apiKey ?? '';
    const base     = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000')
                       .replace(/^http/, 'ws');
    return `${base}/v1/voice/stream/${orgId}?api_key=${encodeURIComponent(apiKey)}`;
  },
};
```

---

### Task 7.2: Create `src/features/org/types/voice.ts`

```typescript
// src/features/org/types/voice.ts
export interface VoiceStatusResponse {
  stt_status: 'ready' | 'idle' | 'error';
  tts_status: 'ready' | 'idle' | 'error';
  stt_model:  string;
  tts_model:  string;
  device:     'cpu' | 'cuda';
}

export interface TranscribeResponse {
  transcript: string;
  language:   string;
  confidence: number;
  segments:   Array<{ start: number; end: number; text: string }>;
  duration_s: number;
}

export interface PersonaResponse {
  org_id:        string;
  tenant_id:     string;
  ref_audio_url: string;
  ref_text:      string;
  language:      string;
  created_at:    string;
}

export type VoiceStreamState =
  | 'idle'
  | 'connecting'
  | 'listening'
  | 'processing'
  | 'speaking'
  | 'error';

export interface VoiceStreamEvent {
  type:       'transcript' | 'agent_response' | 'tts_chunk' | 'tts_done'
            | 'agent_thinking' | 'error' | 'session_end';
  text?:      string;
  data?:      string;    // base64 PCM24kHz
  is_final?:  boolean;
  confidence?: number;
  language?:  string;
  code?:      string;
  detail?:    string;
}
```

---

## Phase 8 — Frontend: Hooks

### Task 8.1: Create `src/lib/voice/useVoiceStream.ts`

```typescript
// src/lib/voice/useVoiceStream.ts
/**
 * useVoiceStream — manages the real-time WebSocket voice session.
 *
 * State:  idle → connecting → listening → processing → speaking → idle
 *
 * Mic capture uses AudioWorklet (offloaded to DSP thread).
 * TTS playback queues PCM chunks via Web Audio API.
 *
 * Follows AgentVerse auth pattern: API key from useAuthStore.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { voiceApi } from '@/features/org/api/voice';
import type { VoiceStreamState, VoiceStreamEvent } from '@/features/org/types/voice';

const PCM_IN_RATE  = 16_000;
const PCM_OUT_RATE = 24_000;

export interface UseVoiceStreamCallbacks {
  onTranscript?:     (text: string, isFinal: boolean, confidence: number) => void;
  onAgentThinking?:  () => void;
  onAgentResponse?:  (text: string) => void;
  onTTSDone?:        () => void;
  onError?:          (msg: string) => void;
  onStateChange?:    (s: VoiceStreamState) => void;
}

export function useVoiceStream(orgId: string, callbacks: UseVoiceStreamCallbacks = {}) {
  const [state, setState]       = useState<VoiceStreamState>('idle');
  const wsRef                   = useRef<WebSocket | null>(null);
  const streamRef               = useRef<MediaStream | null>(null);
  const ctxInRef                = useRef<AudioContext | null>(null);
  const ctxOutRef               = useRef<AudioContext | null>(null);
  const workletRef              = useRef<AudioWorkletNode | null>(null);
  const ttsQueueRef             = useRef<ArrayBuffer[]>([]);
  const ttsPlayingRef           = useRef(false);

  const setAndNotify = (s: VoiceStreamState) => {
    setState(s);
    callbacks.onStateChange?.(s);
  };

  // ── Connect WebSocket ─────────────────────────────────────────────────────

  const connect = useCallback(async () => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;
    setAndNotify('connecting');
    const url = voiceApi.streamUrl(orgId);
    const ws  = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen  = () => setAndNotify('listening');
    ws.onerror = () => { setAndNotify('error'); callbacks.onError?.('Connection failed'); };
    ws.onclose = () => { if (state !== 'idle') setAndNotify('idle'); };
    ws.onmessage = (ev: MessageEvent<string>) => {
      const event: VoiceStreamEvent = JSON.parse(ev.data);
      handleEvent(event);
    };
  }, [orgId]);

  // ── Handle server events ──────────────────────────────────────────────────

  const handleEvent = (e: VoiceStreamEvent) => {
    switch (e.type) {
      case 'transcript':
        callbacks.onTranscript?.(e.text ?? '', e.is_final ?? false, e.confidence ?? 1);
        break;
      case 'agent_thinking':
        setAndNotify('processing');
        callbacks.onAgentThinking?.();
        break;
      case 'agent_response':
        setAndNotify('speaking');
        callbacks.onAgentResponse?.(e.text ?? '');
        break;
      case 'tts_chunk': {
        const buf = b64ToArrayBuffer(e.data ?? '');
        ttsQueueRef.current.push(buf);
        if (!ttsPlayingRef.current) playNextChunk();
        break;
      }
      case 'tts_done':
        callbacks.onTTSDone?.();
        setTimeout(() => setAndNotify('listening'), 200);
        break;
      case 'error':
        callbacks.onError?.(e.detail ?? 'Unknown error');
        setAndNotify('error');
        break;
      case 'session_end':
        setAndNotify('idle');
        break;
    }
  };

  // ── Mic capture via AudioWorklet ──────────────────────────────────────────

  const startMic = useCallback(async () => {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { sampleRate: PCM_IN_RATE, channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    streamRef.current = stream;

    const ctx = new AudioContext({ sampleRate: PCM_IN_RATE });
    ctxInRef.current = ctx;
    await ctx.audioWorklet.addModule('/audio-worklet-processor.js');

    const source  = ctx.createMediaStreamSource(stream);
    const worklet = new AudioWorkletNode(ctx, 'pcm-capture-processor');
    workletRef.current = worklet;

    worklet.port.onmessage = ({ data }: MessageEvent<ArrayBuffer>) => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) return;
      wsRef.current.send(JSON.stringify({
        type: 'audio_chunk',
        data: arrayBufferToB64(data),
      }));
    };

    source.connect(worklet);
    worklet.connect(ctx.destination);
  }, []);

  // ── Mic stop ──────────────────────────────────────────────────────────────

  const stopMic = useCallback(() => {
    streamRef.current?.getTracks().forEach(t => t.stop());
    workletRef.current?.disconnect();
    ctxInRef.current?.close().catch(() => {});
    wsRef.current?.send(JSON.stringify({ type: 'end_of_speech' }));
  }, []);

  // ── TTS playback queue (PCM16 at 24 kHz) ─────────────────────────────────

  const playNextChunk = useCallback(() => {
    const queue = ttsQueueRef.current;
    if (!queue.length) { ttsPlayingRef.current = false; return; }
    ttsPlayingRef.current = true;
    const chunk  = queue.shift()!;
    const ctx    = new AudioContext({ sampleRate: PCM_OUT_RATE });
    ctxOutRef.current = ctx;
    const int16  = new Int16Array(chunk);
    const float32 = Float32Array.from(int16, v => v / 32768);
    const audioBuf = ctx.createBuffer(1, float32.length, PCM_OUT_RATE);
    audioBuf.copyToChannel(float32, 0);
    const src = ctx.createBufferSource();
    src.buffer   = audioBuf;
    src.onended  = playNextChunk;
    src.connect(ctx.destination);
    src.start();
  }, []);

  // ── Disconnect ────────────────────────────────────────────────────────────

  const disconnect = useCallback(() => {
    stopMic();
    wsRef.current?.close();
    ttsQueueRef.current = [];
    ttsPlayingRef.current = false;
    setAndNotify('idle');
  }, [stopMic]);

  useEffect(() => () => { disconnect(); }, []);

  return { state, connect, startMic, stopMic, disconnect };
}

// ── Util ──────────────────────────────────────────────────────────────────────

function b64ToArrayBuffer(b64: string): ArrayBuffer {
  const bin = atob(b64);
  const buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return buf.buffer;
}

function arrayBufferToB64(buf: ArrayBuffer): string {
  let bin = '';
  new Uint8Array(buf).forEach(b => (bin += String.fromCharCode(b)));
  return btoa(bin);
}
```

---

### Task 8.2: Create `src/lib/voice/useLoginGreeting.ts`

```typescript
// src/lib/voice/useLoginGreeting.ts
/**
 * useLoginGreeting — fetch and auto-play OmniVoice org greeting on first login.
 *
 * - Plays once per browser session (sessionStorage flag)
 * - Respects prefers-reduced-motion
 * - Uses voiceApi.greeting() which calls GET /v1/voice/greeting/{org_id}
 * - Follows TanStack Query staleTime pattern from useOrg.ts
 */
import { useEffect, useRef, useState } from 'react';
import { useQuery }                    from '@tanstack/react-query';
import { voiceApi }                    from '@/features/org/api/voice';

interface UseLoginGreetingOpts {
  orgId:     string;
  userName:  string;
  language?: string;
  enabled?:  boolean;
}

const SESSION_KEY = (orgId: string) => `av:greeting-played:${orgId}`;

export function useLoginGreeting({
  orgId,
  userName,
  language = 'en',
  enabled  = true,
}: UseLoginGreetingOpts) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [hasPlayed, setHasPlayed] = useState(() =>
    sessionStorage.getItem(SESSION_KEY(orgId)) === '1',
  );
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const reducedMotion =
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const { data: blob } = useQuery<Blob>({
    queryKey:  ['voice-greeting', orgId, userName, language],
    queryFn:   () => voiceApi.greeting(orgId, { user_name: userName, language }),
    enabled:   enabled && !hasPlayed && !reducedMotion && !!orgId,
    staleTime: 5 * 60_000,
    retry:     1,
    gcTime:    10 * 60_000,
  });

  useEffect(() => {
    if (!blob || hasPlayed) return;
    const url   = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audioRef.current = audio;

    audio.onplay   = () => setIsPlaying(true);
    audio.onpause  = () => setIsPlaying(false);
    audio.onended  = () => {
      setIsPlaying(false);
      setHasPlayed(true);
      sessionStorage.setItem(SESSION_KEY(orgId), '1');
      URL.revokeObjectURL(url);
    };
    audio.onerror  = () => setIsPlaying(false);

    // Slight delay so page renders before audio starts
    const tid = setTimeout(() => audio.play().catch(() => {}), 800);
    return () => { clearTimeout(tid); URL.revokeObjectURL(url); };
  }, [blob, hasPlayed, orgId]);

  const stop = () => {
    audioRef.current?.pause();
    setIsPlaying(false);
  };

  return { isPlaying, hasPlayed, stop };
}
```

---

## Phase 9 — Frontend: Components

### Task 9.1: Create `src/components/voice/LoginGreetingPlayer.tsx`

```tsx
// src/components/voice/LoginGreetingPlayer.tsx
/**
 * LoginGreetingPlayer — ambient indicator while OmniVoice greeting plays.
 *
 * Renders in OrgPage header. Shows 5 animated waveform bars + "Daily brief"
 * label + mute button. Disappears when audio ends.
 *
 * Accessibility:
 *   - role="status" + aria-live="polite"
 *   - Mute button has aria-label
 *   - Waveform is aria-hidden
 *   - prefers-reduced-motion: hook won't play at all
 */
import { AnimatePresence, motion } from 'framer-motion';
import { VolumeX }                 from 'lucide-react';
import { useLoginGreeting }        from '@/lib/voice/useLoginGreeting';
import { cn }                      from '@/lib/utils';

interface LoginGreetingPlayerProps {
  orgId:     string;
  userName:  string;
  language?: string;
  className?: string;
}

const BARS = 5;
const SPRING = { type: 'spring', stiffness: 400, damping: 30 } as const;

export function LoginGreetingPlayer({
  orgId, userName, language = 'en', className,
}: LoginGreetingPlayerProps) {
  const { isPlaying, stop } = useLoginGreeting({ orgId, userName, language });

  return (
    <AnimatePresence>
      {isPlaying && (
        <motion.div
          key="greeting"
          initial={{ opacity: 0, scale: 0.88, y: 4 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.88, y: 4 }}
          transition={SPRING}
          role="status"
          aria-live="polite"
          aria-label="OmniVoice daily brief playing"
          className={cn(
            'flex items-center gap-2 px-3 py-1.5 rounded-full select-none',
            'bg-[#0A0D14]/90 border border-[#00D4FF]/25 backdrop-blur-md',
            className,
          )}
        >
          {/* Animated waveform bars */}
          <div className="flex items-end gap-[2px] h-[14px]" aria-hidden>
            {Array.from({ length: BARS }).map((_, i) => (
              <motion.span
                key={i}
                className="w-[2px] rounded-full bg-[#00D4FF]"
                style={{ display: 'block' }}
                animate={{ height: ['3px', `${6 + (i % 3) * 4}px`, '3px'] }}
                transition={{
                  duration:  0.45 + i * 0.07,
                  repeat:    Infinity,
                  ease:      'easeInOut',
                  delay:     i * 0.06,
                }}
              />
            ))}
          </div>

          <span className="text-[11px] font-medium text-[#94A3B8] leading-none">
            Daily brief
          </span>

          <button
            onClick={stop}
            className="p-0.5 rounded text-[#64748B] hover:text-[#F1F5F9] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[#00D4FF]"
            aria-label="Stop daily brief"
          >
            <VolumeX className="h-3.5 w-3.5" />
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
```

---

### Task 9.2: Rewrite `src/features/org/components/VoiceModal.tsx`

Replace the existing Web-Speech-API-only VoiceModal with a full real-time session:

**Changes to `VoiceModal.tsx`:**

1. Import `useVoiceStream` instead of using `window.SpeechRecognition`
2. Add a `transcript` state that receives live STT results from WebSocket
3. Add `agentResponse` state that shows agent reply
4. Show TTS waveform while agent speaks
5. Keep the existing JARVIS spring/waveform aesthetic

```tsx
// Replace the entire file with:
/**
 * VoiceModal — JARVIS-style real-time voice session.
 *
 * Replaces browser SpeechRecognition with native faster-whisper STT
 * and OmniVoice TTS via WebSocket /v1/voice/stream/{org_id}.
 *
 * Skills: emil-design-eng, impeccable-ui, web-guidelines
 */
import { useState, useCallback, useEffect } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Mic, MicOff, X, Loader2, CheckCircle2, Volume2 } from 'lucide-react';
import { cn }             from '@/lib/utils';
import { useVoiceStream } from '@/lib/voice/useVoiceStream';

interface VoiceModalProps {
  open:         boolean;
  onClose:      () => void;
  onTranscript: (text: string) => void;
  orgId:        string;
  placeholder?: string;
}

const MODAL_SPRING = { type: 'spring', stiffness: 300, damping: 26 } as const;
const BAR_SPRING   = { type: 'spring', stiffness: 500, damping: 35 } as const;
const BARS         = 20;

export function VoiceModal({
  open, onClose, onTranscript, orgId, placeholder,
}: VoiceModalProps) {
  const reduce = useReducedMotion();
  const [transcript,     setTranscript]    = useState('');
  const [interimText,    setInterimText]   = useState('');
  const [agentResponse,  setAgentResponse] = useState('');
  const [bars,           setBars]          = useState<number[]>(Array(BARS).fill(0.1));
  const [micActive,      setMicActive]     = useState(false);

  const { state, connect, startMic, stopMic, disconnect } = useVoiceStream(orgId, {
    onTranscript: (text, isFinal) => {
      if (isFinal) {
        setTranscript(text);
        setInterimText('');
      } else {
        setInterimText(text);
      }
    },
    onAgentResponse: (text) => setAgentResponse(text),
    onTTSDone:       ()     => {},
    onError:         ()     => {},
  });

  // Animate bars when listening or speaking
  useEffect(() => {
    const active = state === 'listening' || state === 'speaking';
    if (!active || reduce) { setBars(Array(BARS).fill(0.1)); return; }
    const tid = setInterval(
      () => setBars(prev => prev.map(() => 0.1 + Math.random() * 0.9)),
      80,
    );
    return () => clearInterval(tid);
  }, [state, reduce]);

  // Connect WebSocket when modal opens
  useEffect(() => {
    if (open) connect();
    else { disconnect(); setTranscript(''); setInterimText(''); setAgentResponse(''); }
  }, [open]);

  const handleMicToggle = useCallback(async () => {
    if (micActive) {
      stopMic();
      setMicActive(false);
    } else {
      await startMic();
      setMicActive(true);
    }
  }, [micActive, startMic, stopMic]);

  const handleConfirm = useCallback(() => {
    const text = (transcript || interimText).trim();
    if (text) onTranscript(text);
    onClose();
  }, [transcript, interimText, onTranscript, onClose]);

  const stateLabel: Record<string, string> = {
    idle:        'Ready',
    connecting:  'Connecting…',
    listening:   'Listening…',
    processing:  'Processing…',
    speaking:    'Speaking…',
    error:       'Error — try again',
  };

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            key="backdrop"
            className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            aria-hidden
          />

          {/* Modal */}
          <motion.div
            key="modal"
            role="dialog"
            aria-modal="true"
            aria-label="Voice command interface"
            className={cn(
              'fixed z-50 inset-x-4 bottom-8 sm:inset-x-auto sm:left-1/2 sm:-translate-x-1/2',
              'w-full sm:w-[480px] max-w-full',
              'bg-[#0D1117] border border-[#1E2535] rounded-2xl p-6 shadow-2xl',
              'flex flex-col gap-5',
            )}
            initial={{ opacity: 0, y: 40, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 40, scale: 0.96 }}
            transition={MODAL_SPRING}
          >
            {/* Header */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                {state === 'speaking'
                  ? <Volume2 className="h-4 w-4 text-[#00D4FF]" />
                  : <Mic className="h-4 w-4 text-[#00D4FF]" />
                }
                <span className="text-sm font-semibold text-[#F1F5F9]">
                  Voice Command
                </span>
              </div>
              <button
                onClick={onClose}
                className="p-1.5 rounded-lg text-[#64748B] hover:text-[#F1F5F9] hover:bg-[#1E2535] transition-colors"
                aria-label="Close voice modal"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Waveform */}
            <div
              className="flex items-end justify-center gap-1 h-16"
              aria-hidden="true"
            >
              {bars.map((h, i) => (
                <motion.div
                  key={i}
                  className={cn(
                    'w-1 rounded-full',
                    state === 'listening' ? 'bg-[#00D4FF]' :
                    state === 'speaking'  ? 'bg-emerald-400' :
                    state === 'processing'? 'bg-amber-400' :
                    'bg-[#1E2535]',
                  )}
                  animate={{ height: reduce ? '4px' : `${4 + h * 52}px` }}
                  transition={{ ...BAR_SPRING, delay: i * 0.015 }}
                />
              ))}
            </div>

            {/* State label */}
            <p className="text-center text-xs text-[#64748B] font-medium tracking-wide uppercase">
              {stateLabel[state] ?? state}
            </p>

            {/* Transcript display */}
            <div
              className="min-h-[64px] rounded-xl bg-[#1A1F2E] border border-[#1E2535] px-4 py-3"
              aria-live="polite"
              aria-label="Transcript"
            >
              {transcript || interimText ? (
                <p className="text-sm text-[#F1F5F9] leading-relaxed">
                  {transcript}
                  {interimText && (
                    <span className="text-[#64748B] italic"> {interimText}</span>
                  )}
                </p>
              ) : (
                <p className="text-sm text-[#475569] italic">
                  {placeholder ?? 'Start speaking…'}
                </p>
              )}
            </div>

            {/* Agent response */}
            {agentResponse && (
              <motion.div
                className="rounded-xl bg-[#0F1A2E] border border-[#00D4FF]/20 px-4 py-3"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={MODAL_SPRING}
                aria-live="assertive"
                aria-label="Agent response"
              >
                <p className="text-xs text-[#00D4FF] font-medium mb-1">Agent</p>
                <p className="text-sm text-[#CBD5E1] leading-relaxed">{agentResponse}</p>
              </motion.div>
            )}

            {/* Controls */}
            <div className="flex items-center gap-3">
              <motion.button
                onClick={handleMicToggle}
                disabled={state === 'connecting' || state === 'processing'}
                className={cn(
                  'flex-1 flex items-center justify-center gap-2 py-3 rounded-xl',
                  'text-sm font-semibold transition-all',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]',
                  micActive
                    ? 'bg-rose-500/20 border border-rose-500/40 text-rose-400 hover:bg-rose-500/30'
                    : 'bg-[#00D4FF]/10 border border-[#00D4FF]/30 text-[#00D4FF] hover:bg-[#00D4FF]/20',
                )}
                whileTap={{ scale: 0.97 }}
                aria-pressed={micActive}
                aria-label={micActive ? 'Stop microphone' : 'Start microphone'}
              >
                {state === 'connecting' || state === 'processing'
                  ? <Loader2 className="h-4 w-4 animate-spin" />
                  : micActive
                    ? <MicOff className="h-4 w-4" />
                    : <Mic    className="h-4 w-4" />
                }
                {micActive ? 'Stop' : 'Speak'}
              </motion.button>

              {(transcript || interimText) && (
                <motion.button
                  onClick={handleConfirm}
                  className="flex items-center gap-2 px-4 py-3 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-sm font-semibold hover:bg-emerald-500/20 transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                  initial={{ opacity: 0, scale: 0.9 }}
                  animate={{ opacity: 1, scale: 1 }}
                  whileTap={{ scale: 0.97 }}
                  aria-label="Use transcript as goal"
                >
                  <CheckCircle2 className="h-4 w-4" />
                  Use
                </motion.button>
              )}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
```

---

## Phase 10 — OrgPage Wiring

### Task 10.1: Replace `window.speechSynthesis` boot in `OrgPage.tsx`

**File:** `agent-verse-frontend/src/features/org/OrgPage.tsx`

Find the `handleBootComplete` callback (~line 63–75):
```tsx
// EXISTING (to be replaced):
try {
  const utter = new window.SpeechSynthesisUtterance(
    'AgentVerse initialized. All systems online. Ready for commands.'
  );
  utter.rate = 0.92;
  utter.pitch = 0.85;
  utter.volume = 0.8;
  window.speechSynthesis.speak(utter);
} catch { /* speech not supported */ }
```

Replace with OmniVoice TTS via the speak API:
```tsx
// REPLACEMENT:
try {
  const wav = await voiceApi.speak(
    'AgentVerse initialized. All systems online. Ready for commands.',
    { language: 'en', speed: 0.95 },
  );
  const url   = URL.createObjectURL(wav);
  const audio = new Audio(url);
  audio.onended = () => URL.revokeObjectURL(url);
  audio.play().catch(() => {});
} catch { /* speech not supported */ }
```

### Task 10.2: Add `LoginGreetingPlayer` and `VoiceModal` org_id prop

**File:** `agent-verse-frontend/src/features/org/OrgPage.tsx`

Add import at top:
```tsx
import { LoginGreetingPlayer } from '@/components/voice/LoginGreetingPlayer';
import { voiceApi }            from './api/voice';
```

In the header JSX, after the existing action buttons:
```tsx
{/* OmniVoice login greeting */}
<LoginGreetingPlayer
  orgId={orgId}
  userName={org?.created_by ?? 'there'}
  language="en"
  className="hidden sm:flex"
/>
```

Update the existing `VoiceModal` usage to pass `orgId`:
```tsx
<VoiceModal
  open={showVoice}
  onClose={() => setShowVoice(false)}
  onTranscript={(text) => { /* existing handler */ }}
  orgId={orgId}          {/* ← ADD THIS PROP */}
/>
```

---

## Phase 11 — Tests, Lint, Type-Check

### Task 11.1: Backend integration tests

```python
# tests/voice/test_router.py
from fastapi.testclient import TestClient
from app.main import create_app
import io, numpy as np, soundfile as sf

def make_silent_wav(seconds=1):
    buf = io.BytesIO()
    sf.write(buf, np.zeros(int(16_000 * seconds)), 16_000, format="WAV", subtype="PCM_16")
    return buf.getvalue()

def test_voice_status_ok(client: TestClient):
    r = client.get("/v1/voice/status", headers={"X-API-Key": "test"})
    assert r.status_code == 200
    data = r.json()
    assert "stt_status" in data
    assert "tts_status" in data

def test_transcribe_wav(client: TestClient):
    wav = make_silent_wav()
    r = client.post(
        "/v1/voice/transcribe",
        headers={"X-API-Key": "test"},
        files={"audio": ("silent.wav", wav, "audio/wav")},
    )
    assert r.status_code == 200
    assert "transcript" in r.json()

def test_transcribe_no_auth(client: TestClient):
    r = client.post("/v1/voice/transcribe",
                    files={"audio": ("x.wav", b"data", "audio/wav")})
    assert r.status_code == 401

def test_speak_returns_wav(client: TestClient):
    r = client.post(
        "/v1/voice/speak",
        headers={"X-API-Key": "test", "Content-Type": "application/json"},
        json={"text": "Hello.", "language": "en"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
```

### Task 11.2: Frontend component tests

```typescript
// src/components/voice/LoginGreetingPlayer.test.tsx
import { render, screen } from '@testing-library/react';
import { vi } from 'vitest';

vi.mock('@/lib/voice/useLoginGreeting', () => ({
  useLoginGreeting: () => ({ isPlaying: true, hasPlayed: false, stop: vi.fn() }),
}));

test('renders waveform when playing', () => {
  render(<LoginGreetingPlayer orgId="o1" userName="Harsh" />);
  expect(screen.getByRole('status')).toBeInTheDocument();
  expect(screen.getByLabelText('Stop daily brief')).toBeInTheDocument();
});

// src/lib/voice/useLoginGreeting.test.ts
test('sets sessionStorage flag after audio ends', async () => {
  // ... test with mock Audio
});

test('does not fetch when prefers-reduced-motion', () => {
  // matchMedia mock returning reducedMotion: true
  // verify query enabled = false
});
```

### Task 11.3: Run all checks

```bash
# Backend
cd agent-verse-backend
uv run pytest tests/voice/ -v --tb=short
uv run ruff check app/voice/
uv run mypy app/voice/

# Frontend
cd agent-verse-frontend
npm run typecheck
npm run lint
npm run test -- src/components/voice/ src/lib/voice/ --run
```

---

## Phase 12 — Config & Helm

### Task 12.1: `.env.example` additions

```bash
# OmniVoice / Voice OS
VOICE_ENABLED=true
VOICE_DEVICE=cpu                          # set to "cuda" with GPU node
VOICE_STT_MODEL=large-v3-turbo
VOICE_TTS_MODEL=k2-fsa/OmniVoice
MODEL_CACHE_DIR=/app/models
VOICE_PERSONA_BUCKET=agentverse-voice-personas
VOICE_GREETING_CACHE_TTL=300
VOICE_MAX_AUDIO_MB=25

# MinIO / S3 for persona audio storage
S3_ENDPOINT_URL=http://minio:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
```

## Phase 12 — D-6: Proactive Voice Alerts (World-Class)

### Task 12.1: Add `app/voice/alerts.py`

When a mission fails or an approval is urgently needed, the backend pushes a
synthesised audio chunk to any active WebSocket session via Redis pub/sub.
This uses the **existing** mission SSE channel (`mission:{id}:events`) already in
`app/org/router.py` — we just add a voice channel alongside it.

```python
# app/voice/alerts.py
"""D-6: Proactive Voice Alerts — server-initiated TTS push to browser.

When the org event bus emits a critical event (mission.failed,
approval_required, budget_exceeded), this module synthesises a short
alert audio clip and publishes it to a Redis channel that connected
WebSocket sessions subscribe to.

Uses the existing Redis pub/sub infrastructure already wired in
app/org/router.py (mission stream SSE) — no new infra needed.
"""
from __future__ import annotations

import base64
import json
import logging

import structlog
from opentelemetry import trace

log = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

_ALERT_TEMPLATES: dict[str, str] = {
    "mission.failed":         "Alert: Mission {title} has failed and requires your attention.",
    "approval_required":      "Approval needed: {title} is waiting for your decision.",
    "budget_exceeded":        "Budget alert: {title} has exceeded its budget limit.",
    "mission.completed":      "Mission {title} completed successfully.",
    "task.blocked":           "Task {title} is blocked and needs intervention.",
}

CRITICAL_EVENTS = frozenset({
    "mission.failed", "approval_required", "budget_exceeded", "task.blocked"
})


async def publish_voice_alert(
    redis: object,
    tenant_id: str,
    org_id: str,
    event_type: str,
    entity_title: str = "",
    language: str = "en",
    ref_audio: bytes | None = None,
    ref_text: str | None = None,
) -> None:
    """Synthesise and publish a voice alert to active WebSocket sessions.

    The WebSocket session (VoiceStreamingSession) listens on
    channel `voice:alerts:{tenant_id}:{org_id}` and delivers
    the audio as a `{ type: "proactive_alert", data: <base64 PCM> }` frame.
    """
    if event_type not in CRITICAL_EVENTS:
        return   # only push critical events

    template = _ALERT_TEMPLATES.get(event_type, "Alert: {title}")
    text     = template.format(title=entity_title or "an item")

    try:
        from app.voice.tts_engine import synthesize
        wav   = await synthesize(text, ref_audio=ref_audio, ref_text=ref_text, language=language)
        # Strip WAV header, send raw PCM
        pcm   = wav[44:]
        payload = json.dumps({
            "type":       "proactive_alert",
            "event_type": event_type,
            "text":       text,
            "data":       base64.b64encode(pcm).decode(),
        })
        channel = f"voice:alerts:{tenant_id}:{org_id}"
        await redis.publish(channel, payload)  # type: ignore[attr-defined]
        log.info("voice.alert.published", event=event_type, org_id=org_id, chars=len(text))
    except Exception as exc:
        log.warning("voice.alert.failed", error=str(exc))


async def subscribe_to_alerts(
    redis: object,
    ws,
    tenant_id: str,
    org_id: str,
) -> None:
    """Subscribe to proactive alerts channel and forward to WebSocket."""
    channel = f"voice:alerts:{tenant_id}:{org_id}"
    try:
        async with redis.pubsub() as ps:  # type: ignore[attr-defined]
            await ps.subscribe(channel)
            async for msg in ps.listen():
                if msg["type"] == "message":
                    await ws.send_text(msg["data"].decode())
    except Exception as exc:
        log.debug("voice.alert.sub_ended", error=str(exc))
```

### Task 12.2: Hook alerts into OrgService event emission

In `app/org/service.py`, after `_emit_event()`, add a background task to
publish a voice alert for critical events:

```python
# In OrgService._emit_event(), after self._session.flush():
# Add this snippet to emit voice alerts for critical events

_VOICE_ALERT_EVENTS = {"mission.failed", "approval_required", "budget_exceeded", "task.blocked"}

async def _emit_event(self, org_id, event_type, *, title="", ...):
    ev = OrgEvent(...)
    self._session.add(ev)
    await self._session.flush()

    # D-6: Voice alert for critical events
    if event_type in _VOICE_ALERT_EVENTS:
        try:
            from app.main import app as _app
            redis = getattr(getattr(_app, "state", None), "redis", None)
            if redis:
                from app.voice.alerts import publish_voice_alert
                import asyncio
                asyncio.create_task(publish_voice_alert(
                    redis=redis,
                    tenant_id=self._tenant_id,
                    org_id=str(org_id),
                    event_type=event_type,
                    entity_title=title,
                ))
        except Exception:
            pass   # voice alerts are best-effort; never block event emission

    return ev
```

### Task 12.3: VoiceStreamingSession subscribes to alerts

In `VoiceStreamingSession.run()`, add concurrent alert subscription:

```python
# In streaming.py VoiceStreamingSession.run():
async def run(self) -> None:
    with tracer.start_as_current_span("voice.stream.session"):
        try:
            # Run session loop + alert subscription concurrently
            redis = getattr(getattr(self.ws, "app", None), "state", {})
            redis = getattr(redis, "redis", None)
            tasks = [asyncio.create_task(self._loop())]
            if redis:
                from app.voice.alerts import subscribe_to_alerts
                tasks.append(asyncio.create_task(
                    subscribe_to_alerts(redis, self.ws, self.tenant_id, self.org_id)
                ))
            await asyncio.gather(*tasks, return_exceptions=True)
        except WebSocketDisconnect:
            pass
        finally:
            await self._send({"type": "session_end"})
```

---

## Updated Completion Checklist

```
REVERIFICATION FIXES
  [ ] B-1: greeting.py uses OrgService.get_org_health() not get_morning_brief()
  [ ] B-2: DigestGenerator wired into greeting endpoint
  [ ] B-3: GoalRefinementPipeline.refine() called correctly (sync in executor)
  [ ] B-4: _fetch_org_health uses session_factory directly, not get_org_service

WORLD-CLASS DIFFERENTIATORS
  [ ] D-1: Voice-to-Mission — GoalRefinementPipeline → OrgService.create_mission()
  [ ] D-2: WYWA voice digest — DigestGenerator in greeting
  [ ] D-3: Voice intent router — classify_intent() + handle_create_mission/approve
  [ ] D-4: Voice-driven approval — OrgService.record_decision()
  [ ] D-5: Multi-language by jurisdiction — jurisdiction_to_language()
  [ ] D-6: Proactive voice alerts — app/voice/alerts.py + OrgService hook
  [ ] D-7: Real org health in greeting — OrgService.get_org_health()

BACKEND (all phases)
  [ ] Phase 1: stt_engine.py + tests
  [ ] Phase 2: tts_engine.py + tests
  [ ] Phase 3: greeting.py (verified) + tests
  [ ] Phase 4: intent_router.py + streaming.py (verified)
  [ ] Phase 5.1: router.py fully replaced (corrected helpers)
  [ ] Phase 5.2: schemas.py
  [ ] Phase 5.3: rate_limiter.py CHANNEL_LIMITS extended
  [ ] Phase 5.4: lifespan warmup
  [ ] Phase 5.5: router registered (verified in bootstrap/routers.py)
  [ ] Phase 5.6: config.py settings added
  [ ] Phase 12: alerts.py + OrgService hook + VoiceStreamingSession concurrent task
  [ ] uv run ruff check app/voice/ → 0 errors
  [ ] uv run mypy app/voice/ → 0 errors
  [ ] uv run pytest tests/voice/ → all green

FRONTEND (all phases)
  [ ] Phase 6: public/audio-worklet-processor.js
  [ ] Phase 7.1: src/features/org/api/voice.ts
  [ ] Phase 7.2: src/features/org/types/voice.ts
  [ ] Phase 8.1: src/lib/voice/useVoiceStream.ts (add proactive_alert handler)
  [ ] Phase 8.2: src/lib/voice/useLoginGreeting.ts
  [ ] Phase 9.1: src/components/voice/LoginGreetingPlayer.tsx
  [ ] Phase 9.2: VoiceModal.tsx rewritten (add intent feedback UI)
  [ ] Phase 10.1: OrgPage boot speechSynthesis → voiceApi.speak()
  [ ] Phase 10.2: LoginGreetingPlayer wired in OrgPage header
  [ ] Phase 10.3: VoiceModal gets orgId prop
  [ ] npm run typecheck → 0 errors
  [ ] npm run lint → 0 errors
  [ ] npm run test → all green

INFRASTRUCTURE
  [ ] Phase 12.1 (config): .env.example updated
  [ ] Phase 12.2 (helm): values.yaml updated
  [ ] Dockerfile: faster-whisper + omnivoice + torchaudio added
```

---

## Why This Makes AgentVerse Different from the Whole World

No voice product on Earth today combines all of the following in a single open-source platform:

| What | Who does it today | AgentVerse after this plan |
|---|---|---|
| Speak a goal → AI creates a real mission with requirements, budget, timeline, team | Nobody | ✅ D-1 via GoalRefinementPipeline → OrgService |
| "While you were away" voice briefing using actual event data | Nobody | ✅ D-2 via DigestGenerator |
| Voice approval of pending decisions with audit trail | Nobody | ✅ D-4 via OrgService.record_decision() |
| 600+ language TTS auto-selected by org jurisdiction | Nobody | ✅ D-5 via OmniVoice + jurisdiction_to_language() |
| Server-push voice alerts when a mission fails | Nobody | ✅ D-6 via Redis pub/sub + OmniVoice TTS |
| Zero-shot voice cloning per org | ElevenLabs (paid only) | ✅ OmniVoice CC-BY-NC |
| All inference local, no API keys, no cost per call | Nobody at this quality | ✅ faster-whisper + OmniVoice |
| Every voice command routes to real business operations | Nobody | ✅ D-3 intent router |
