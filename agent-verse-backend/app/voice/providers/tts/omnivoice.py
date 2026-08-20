"""OmniVoice TTS — k2-fsa/OmniVoice, local, 600+ languages, RTF 0.025.

Requires: pip install omnivoice (or huggingface-hub + model weights).
Falls back automatically if not installed — provider registry handles fallback.
"""
from __future__ import annotations

import asyncio
import io
import os
from collections.abc import AsyncGenerator
from typing import Any

import soundfile as sf
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

SAMPLE_RATE   = 24_000
CHUNK_FRAMES  = 4_800   # 200 ms
WAV_HEADER_SZ = 44


class OmniVoiceTTS:
    provider_name:          str  = "omnivoice"
    sample_rate:            int  = SAMPLE_RATE
    supports_voice_cloning: bool = True
    supports_nonverbal:     bool = True
    max_text_length:        int  = 4096

    def __init__(self) -> None:
        self._model: Any | None = None
        self._lock = asyncio.Lock()

    async def warmup(self) -> None:
        await self._get_model()
        await self.synthesize(".")

    async def is_ready(self) -> bool:
        return self._model is not None

    async def synthesize(
        self, text: str, *, ref_audio: bytes | None = None, ref_text: str | None = None,
        language: str = "en", speed: float = 1.0, voice_id: str | None = None,
    ) -> bytes:
        with tracer.start_as_current_span("tts.omnivoice.synthesize") as span:
            span.set_attribute("text_len", len(text))
            model  = await self._get_model()
            kwargs: dict = {"text": text}
            if ref_audio and ref_text:
                import numpy as np
                buf   = io.BytesIO(ref_audio)
                ref_a, _ = sf.read(buf, dtype="float32", always_2d=False)
                if ref_a.ndim > 1:
                    ref_a = ref_a.mean(axis=1)
                kwargs["ref_audio"] = ref_a
                kwargs["ref_text"]  = ref_text
            loop   = asyncio.get_event_loop()
            arrays = await loop.run_in_executor(None, lambda: model.generate(**kwargs))
            import numpy as np
            audio  = arrays[0]
            if abs(speed - 1.0) > 0.01:
                import torch
                import torchaudio
                t     = torch.from_numpy(audio).unsqueeze(0)
                audio = torchaudio.functional.resample(
                    t, int(SAMPLE_RATE / speed), SAMPLE_RATE
                ).squeeze().numpy()
            buf = io.BytesIO()
            sf.write(buf, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
            return buf.getvalue()

    async def synthesize_streaming(
        self, text: str, *, ref_audio: bytes | None = None, ref_text: str | None = None,
        language: str = "en", voice_id: str | None = None,
    ) -> AsyncGenerator[bytes, None]:
        wav = await self.synthesize(text, ref_audio=ref_audio, ref_text=ref_text, language=language)
        pcm = wav[WAV_HEADER_SZ:]
        for pos in range(0, len(pcm), CHUNK_FRAMES * 2):
            yield pcm[pos:pos + CHUNK_FRAMES * 2]
            await asyncio.sleep(0)

    async def _get_model(self) -> Any:
        if self._model:
            return self._model
        async with self._lock:
            if self._model:
                return self._model
            from omnivoice import OmniVoice  # raises ImportError if not installed
            import torch
            device = os.getenv("VOICE_DEVICE", "cpu")
            dtype  = torch.float16 if device == "cuda" else torch.float32
            self._model = OmniVoice.from_pretrained(
                os.getenv("VOICE_TTS_MODEL", "k2-fsa/OmniVoice"),
                device_map=device, dtype=dtype,
                cache_dir=os.getenv("MODEL_CACHE_DIR", "/app/models"),
            )
            return self._model
