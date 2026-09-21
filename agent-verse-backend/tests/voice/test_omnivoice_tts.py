"""Tests for app/voice/providers/tts/omnivoice.py — OmniVoiceTTS.

Neither `omnivoice` nor `torchaudio` is installed in this sandbox (torch is).
We inject fake modules into sys.modules so the lazy `from omnivoice import
OmniVoice` / `import torchaudio` calls inside the provider resolve to
deterministic fakes, without ever downloading real model weights.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import patch

import numpy as np
import pytest

from app.voice.providers.tts.omnivoice import (
    CHUNK_FRAMES,
    SAMPLE_RATE,
    WAV_HEADER_SZ,
    OmniVoiceTTS,
)


class _FakeModel:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return [np.zeros(2_400, dtype=np.float32)]


class _FakeOmniVoiceCls:
    instances: list[_FakeModel] = []
    last_kwargs: dict = {}

    @classmethod
    def from_pretrained(cls, *args, **kwargs):
        cls.last_kwargs = kwargs
        model = _FakeModel()
        cls.instances.append(model)
        return model


@pytest.fixture(autouse=True)
def _fake_omnivoice_module(monkeypatch):
    _FakeOmniVoiceCls.instances = []
    fake_mod = types.ModuleType("omnivoice")
    fake_mod.OmniVoice = _FakeOmniVoiceCls
    monkeypatch.setitem(sys.modules, "omnivoice", fake_mod)
    yield


def test_metadata():
    tts = OmniVoiceTTS()
    assert tts.provider_name == "omnivoice"
    assert tts.sample_rate == SAMPLE_RATE
    assert tts.supports_voice_cloning is True
    assert tts.supports_nonverbal is True
    assert tts.max_text_length == 4096


@pytest.mark.asyncio
async def test_is_ready_false_before_warmup():
    tts = OmniVoiceTTS()
    assert await tts.is_ready() is False


@pytest.mark.asyncio
async def test_warmup_loads_model_and_becomes_ready():
    tts = OmniVoiceTTS()
    await tts.warmup()
    assert await tts.is_ready() is True
    assert len(_FakeOmniVoiceCls.instances) == 1


@pytest.mark.asyncio
async def test_get_model_caches_instance():
    tts = OmniVoiceTTS()
    await tts.synthesize("hi")
    await tts.synthesize("there")
    assert len(_FakeOmniVoiceCls.instances) == 1


@pytest.mark.asyncio
async def test_get_model_double_checked_lock_returns_cached_model():
    """A caller that was waiting on the lock must see the cached model.

    Simulates the race the double-checked-locking guard exists for: hold the
    lock ourselves, start a `_get_model()` call (it blocks on the lock),
    populate `self._model` as a concurrent winner would, then release. The
    waiting call must return the cached instance via the inner `if
    self._model` re-check rather than building a second one.
    """
    import asyncio

    tts = OmniVoiceTTS()
    sentinel = object()
    await tts._lock.acquire()
    try:
        task = asyncio.create_task(tts._get_model())
        await asyncio.sleep(0)  # let the task reach lock.acquire() and block
        tts._model = sentinel
    finally:
        tts._lock.release()

    result = await task
    assert result is sentinel
    assert len(_FakeOmniVoiceCls.instances) == 0  # cached hit — never built


@pytest.mark.asyncio
async def test_synthesize_returns_wav_bytes():
    tts = OmniVoiceTTS()
    wav = await tts.synthesize("hello world")
    assert isinstance(wav, bytes)
    assert len(wav) > WAV_HEADER_SZ
    assert wav[:4] == b"RIFF"


@pytest.mark.asyncio
async def test_synthesize_with_ref_audio_and_text_passes_mono_reference():
    import io

    import soundfile as sf

    stereo = np.zeros((1_000, 2), dtype=np.float32)
    buf = io.BytesIO()
    sf.write(buf, stereo, SAMPLE_RATE, format="WAV")
    ref_audio = buf.getvalue()

    tts = OmniVoiceTTS()
    await tts.synthesize("hi", ref_audio=ref_audio, ref_text="reference text")

    model = _FakeOmniVoiceCls.instances[0]
    assert len(model.calls) == 1
    kwargs = model.calls[0]
    assert kwargs["ref_text"] == "reference text"
    assert kwargs["ref_audio"].ndim == 1  # stereo collapsed to mono


@pytest.mark.asyncio
async def test_synthesize_without_ref_audio_omits_reference_kwargs():
    tts = OmniVoiceTTS()
    await tts.synthesize("hi")
    kwargs = _FakeOmniVoiceCls.instances[0].calls[0]
    assert "ref_audio" not in kwargs
    assert "ref_text" not in kwargs


@pytest.mark.asyncio
async def test_synthesize_applies_speed_resample(monkeypatch):
    fake_torchaudio = types.ModuleType("torchaudio")
    fake_functional = types.ModuleType("torchaudio.functional")
    resample_calls = []

    def _resample(t, orig_freq, new_freq):
        resample_calls.append((orig_freq, new_freq))
        return t

    fake_functional.resample = _resample
    fake_torchaudio.functional = fake_functional
    monkeypatch.setitem(sys.modules, "torchaudio", fake_torchaudio)
    monkeypatch.setitem(sys.modules, "torchaudio.functional", fake_functional)

    tts = OmniVoiceTTS()
    wav = await tts.synthesize("hi", speed=1.5)

    assert isinstance(wav, bytes)
    assert len(resample_calls) == 1
    orig_freq, new_freq = resample_calls[0]
    assert new_freq == SAMPLE_RATE
    assert orig_freq == int(SAMPLE_RATE / 1.5)


@pytest.mark.asyncio
async def test_synthesize_skips_resample_when_speed_is_default():
    tts = OmniVoiceTTS()
    # speed=1.0 (default) must not attempt to import torchaudio at all
    with patch.dict(sys.modules, {"torchaudio": None}):
        wav = await tts.synthesize("hi", speed=1.0)
    assert isinstance(wav, bytes)


@pytest.mark.asyncio
async def test_synthesize_streaming_yields_pcm_chunks():
    class _LongModel(_FakeModel):
        def generate(self, **kwargs):
            self.calls.append(kwargs)
            return [np.zeros(SAMPLE_RATE * 2, dtype=np.float32)]

    class _LongOmniVoiceCls(_FakeOmniVoiceCls):
        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            model = _LongModel()
            cls.instances.append(model)
            return model

    fake_mod = types.ModuleType("omnivoice")
    fake_mod.OmniVoice = _LongOmniVoiceCls
    with patch.dict(sys.modules, {"omnivoice": fake_mod}):
        tts = OmniVoiceTTS()
        chunks = [c async for c in tts.synthesize_streaming("hello there")]

    assert len(chunks) > 1
    assert all(isinstance(c, bytes) for c in chunks)
    assert len(chunks[0]) == CHUNK_FRAMES * 2


@pytest.mark.asyncio
async def test_get_model_respects_device_and_cache_env(monkeypatch):
    monkeypatch.setenv("VOICE_DEVICE", "cuda")
    monkeypatch.setenv("VOICE_TTS_MODEL", "custom/model")
    monkeypatch.setenv("MODEL_CACHE_DIR", "/tmp/my-cache")

    tts = OmniVoiceTTS()
    await tts.warmup()

    kwargs = _FakeOmniVoiceCls.last_kwargs
    assert kwargs["device_map"] == "cuda"
    assert kwargs["cache_dir"] == "/tmp/my-cache"
    import torch

    assert kwargs["dtype"] == torch.float16
