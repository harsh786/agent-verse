"""faster-whisper STT provider — local, free, CTranslate2 in-process.

Supports: wav, webm, ogg, mp4, m4a, flac (auto-detected via torchaudio FFmpeg).
"""

from __future__ import annotations

import asyncio
import io
import os
from typing import Any

import numpy as np
from opentelemetry import trace

from app.voice.providers.base import TranscriptResult

tracer = trace.get_tracer(__name__)


class FasterWhisperSTT:
    provider_name: str = "faster_whisper"
    supports_streaming: bool = False

    def __init__(self, model_name: str | None = None) -> None:
        # The Whisper size resolved from the Model Registry (provider "local"),
        # else VOICE_STT_MODEL, else the local default "tiny".
        self.model_name = model_name or _configured_model_name()
        self._model: Any | None = None
        self._lock = asyncio.Lock()

    async def warmup(self) -> None:
        await self._get_model()

    async def is_ready(self) -> bool:
        return self._model is not None

    async def transcribe(self, audio_bytes: bytes, content_type: str) -> TranscriptResult:
        with tracer.start_as_current_span("stt.faster_whisper.transcribe") as span:
            span.set_attribute("audio_bytes", len(audio_bytes))
            span.set_attribute("model", self.model_name)
            # Decode first: undecodable input fails fast, before a model load.
            audio = await _decode_audio(audio_bytes, content_type)
            model = await self._get_model()
            loop = asyncio.get_event_loop()
            segs_gen, info = await loop.run_in_executor(
                None,
                lambda: model.transcribe(
                    audio,
                    beam_size=5,
                    vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 300},
                ),
            )
            seg_list = list(segs_gen)
            text = " ".join(s.text.strip() for s in seg_list)
            avg = float(np.mean([s.avg_logprob for s in seg_list])) if seg_list else -1.0
            conf = float(np.clip(np.exp(avg), 0.0, 1.0))
            span.set_attribute("language", info.language)
            span.set_attribute("confidence", conf)
            return TranscriptResult(
                transcript=text,
                language=info.language,
                confidence=conf,
                segments=[{"start": s.start, "end": s.end, "text": s.text} for s in seg_list],
                provider=self.provider_name,
            )

    async def _get_model(self) -> Any:
        if self._model:
            return self._model
        async with self._lock:
            if self._model:
                return self._model
            # Model construction (and the first-run download) is blocking: run it in
            # a worker thread so it never stalls the API's event loop.
            self._model = await asyncio.to_thread(_build_whisper_model, self.model_name)
            return self._model


def _configured_model_name() -> str:
    from app.ai_router.speech import LOCAL_STT_DEFAULT, voice_setting

    return voice_setting("voice_stt_model") or LOCAL_STT_DEFAULT


def _build_whisper_model(model_name: str | None = None) -> Any:
    import pathlib as _pl

    from faster_whisper import WhisperModel

    from app.ai_router.speech import voice_setting

    device = voice_setting("voice_device") or "cpu"
    compute = "float16" if device == "cuda" else "int8"
    # Use a writable local cache — /app/models is Docker-only, read-only on macOS
    cache_dir = os.getenv("MODEL_CACHE_DIR") or str(
        _pl.Path.home() / ".cache" / "agentverse" / "models"
    )
    _pl.Path(cache_dir).mkdir(parents=True, exist_ok=True)
    # 'tiny' by default for local dev; VOICE_STT_MODEL (or a registry "local"
    # speech_to_text model such as large-v3-turbo) picks another size.
    model_name = model_name or _configured_model_name()
    return WhisperModel(
        model_name,
        device=device,
        compute_type=compute,
        download_root=cache_dir,
    )


async def _decode_audio(audio_bytes: bytes, content_type: str) -> np.ndarray:
    """Decode any audio format to float32 mono 16 kHz numpy array."""
    import soundfile as sf

    buf = io.BytesIO(audio_bytes)
    try:
        audio, sr = sf.read(buf, dtype="float32", always_2d=False)
    except Exception:
        import torchaudio

        buf.seek(0)
        wf, sr = torchaudio.load(buf)
        audio = wf.squeeze().numpy()
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16_000:
        import torch
        import torchaudio

        t = torch.from_numpy(audio).unsqueeze(0)
        audio = torchaudio.functional.resample(t, sr, 16_000).squeeze().numpy()
    return audio
