"""Tests for app/voice/providers/tts/kokoro.py — KokoroTTS.

kokoro-onnx is installed in this sandbox, but instantiating the real Kokoro
class requires local ONNX model files (downloaded on first use). We patch
`kokoro_onnx.Kokoro` with a fake class and point MODEL_CACHE_DIR at a tmp
directory so tests never hit the network or need real model weights.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import numpy as np
import pytest

from app.voice.providers.tts.kokoro import CHUNK_FRAMES, SAMPLE_RATE, WAV_HEADER_SZ, KokoroTTS


class _FakeKokoro:
    """Stand-in for kokoro_onnx.Kokoro."""

    def __init__(self, model_path: str, voices_path: str) -> None:
        self.model_path = model_path
        self.voices_path = voices_path

    def create(self, text: str, voice: str = "af_heart", speed: float = 1.0, lang: str = "en"):
        samples = np.zeros(int(SAMPLE_RATE * 0.1), dtype=np.float32)
        return samples, SAMPLE_RATE


@pytest.fixture(autouse=True)
def _tmp_model_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_CACHE_DIR", str(tmp_path))
    yield


def test_channel_metadata():
    tts = KokoroTTS()
    assert tts.provider_name == "kokoro"
    assert tts.sample_rate == SAMPLE_RATE
    assert tts.supports_voice_cloning is False
    assert tts.supports_nonverbal is False
    assert tts.max_text_length == 2048


@pytest.mark.asyncio
async def test_is_ready_false_before_warmup():
    tts = KokoroTTS()
    assert await tts.is_ready() is False


@pytest.mark.asyncio
async def test_warmup_and_is_ready_downloads_when_missing(tmp_path):
    """When model files are missing, _get_kokoro downloads them via urllib."""
    tts = KokoroTTS()

    downloaded = []

    def _fake_urlretrieve(url, path):
        downloaded.append((url, path))
        # simulate the file now existing after download
        with open(path, "wb") as f:
            f.write(b"fake")

    with (
        patch("kokoro_onnx.Kokoro", _FakeKokoro),
        patch("urllib.request.urlretrieve", side_effect=_fake_urlretrieve),
    ):
        await tts.warmup()

    assert await tts.is_ready() is True
    assert len(downloaded) == 2
    model_path = os.path.join(str(tmp_path), "kokoro-v1.0.onnx")
    assert os.path.exists(model_path)


@pytest.mark.asyncio
async def test_get_kokoro_skips_download_when_model_exists(tmp_path):
    model_path = os.path.join(str(tmp_path), "kokoro-v1.0.onnx")
    voices_path = os.path.join(str(tmp_path), "voices-v1.0.bin")
    with open(model_path, "wb") as f:
        f.write(b"exists")
    with open(voices_path, "wb") as f:
        f.write(b"exists")

    tts = KokoroTTS()
    with (
        patch("kokoro_onnx.Kokoro", _FakeKokoro),
        patch("urllib.request.urlretrieve") as mock_retrieve,
    ):
        await tts.warmup()

    mock_retrieve.assert_not_called()
    assert await tts.is_ready() is True


@pytest.mark.asyncio
async def test_get_kokoro_caches_instance(tmp_path):
    """Second call to _get_kokoro returns the cached instance (no re-instantiation)."""
    model_path = os.path.join(str(tmp_path), "kokoro-v1.0.onnx")
    voices_path = os.path.join(str(tmp_path), "voices-v1.0.bin")
    open(model_path, "wb").close()
    open(voices_path, "wb").close()

    call_count = {"n": 0}

    class _CountingKokoro(_FakeKokoro):
        def __init__(self, model_path, voices_path):
            call_count["n"] += 1
            super().__init__(model_path, voices_path)

    tts = KokoroTTS()
    with patch("kokoro_onnx.Kokoro", _CountingKokoro):
        first = await tts._get_kokoro()
        second = await tts._get_kokoro()

    assert first is second
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_synthesize_returns_wav_bytes(tmp_path):
    model_path = os.path.join(str(tmp_path), "kokoro-v1.0.onnx")
    voices_path = os.path.join(str(tmp_path), "voices-v1.0.bin")
    open(model_path, "wb").close()
    open(voices_path, "wb").close()

    tts = KokoroTTS()
    with patch("kokoro_onnx.Kokoro", _FakeKokoro):
        wav = await tts.synthesize("hello world")

    assert isinstance(wav, bytes)
    assert len(wav) > WAV_HEADER_SZ
    assert wav[:4] == b"RIFF"


@pytest.mark.asyncio
async def test_synthesize_converts_non_ndarray_samples(tmp_path):
    model_path = os.path.join(str(tmp_path), "kokoro-v1.0.onnx")
    voices_path = os.path.join(str(tmp_path), "voices-v1.0.bin")
    open(model_path, "wb").close()
    open(voices_path, "wb").close()

    class _ListSamplesKokoro(_FakeKokoro):
        def create(self, text, voice="af_heart", speed=1.0, lang="en"):
            # return a plain python list instead of an ndarray
            return [0.0] * 100, SAMPLE_RATE

    tts = KokoroTTS()
    with patch("kokoro_onnx.Kokoro", _ListSamplesKokoro):
        wav = await tts.synthesize("hi")

    assert isinstance(wav, bytes)
    assert len(wav) > WAV_HEADER_SZ


@pytest.mark.asyncio
async def test_synthesize_uses_voice_id_override(tmp_path):
    model_path = os.path.join(str(tmp_path), "kokoro-v1.0.onnx")
    voices_path = os.path.join(str(tmp_path), "voices-v1.0.bin")
    open(model_path, "wb").close()
    open(voices_path, "wb").close()

    used_voice = {}

    class _CapturingKokoro(_FakeKokoro):
        def create(self, text, voice="af_heart", speed=1.0, lang="en"):
            used_voice["voice"] = voice
            return np.zeros(10, dtype=np.float32), SAMPLE_RATE

    tts = KokoroTTS()
    with patch("kokoro_onnx.Kokoro", _CapturingKokoro):
        await tts.synthesize("hi", voice_id="af_bella")

    assert used_voice["voice"] == "af_bella"


@pytest.mark.asyncio
async def test_synthesize_uses_env_voice_default(tmp_path, monkeypatch):
    model_path = os.path.join(str(tmp_path), "kokoro-v1.0.onnx")
    voices_path = os.path.join(str(tmp_path), "voices-v1.0.bin")
    open(model_path, "wb").close()
    open(voices_path, "wb").close()
    monkeypatch.setenv("KOKORO_VOICE", "af_sky")

    used_voice = {}

    class _CapturingKokoro(_FakeKokoro):
        def create(self, text, voice="af_heart", speed=1.0, lang="en"):
            used_voice["voice"] = voice
            return np.zeros(10, dtype=np.float32), SAMPLE_RATE

    tts = KokoroTTS()
    with patch("kokoro_onnx.Kokoro", _CapturingKokoro):
        await tts.synthesize("hi")

    assert used_voice["voice"] == "af_sky"


@pytest.mark.asyncio
async def test_synthesize_streaming_yields_pcm_chunks(tmp_path):
    model_path = os.path.join(str(tmp_path), "kokoro-v1.0.onnx")
    voices_path = os.path.join(str(tmp_path), "voices-v1.0.bin")
    open(model_path, "wb").close()
    open(voices_path, "wb").close()

    class _LongKokoro(_FakeKokoro):
        def create(self, text, voice="af_heart", speed=1.0, lang="en"):
            samples = np.zeros(SAMPLE_RATE * 2, dtype=np.float32)  # 2s of audio
            return samples, SAMPLE_RATE

    tts = KokoroTTS()
    chunks = []
    with patch("kokoro_onnx.Kokoro", _LongKokoro):
        async for chunk in tts.synthesize_streaming("hello"):
            chunks.append(chunk)

    assert len(chunks) > 1
    assert all(isinstance(c, bytes) for c in chunks)
    # each non-final chunk should be CHUNK_FRAMES * 2 bytes (16-bit PCM)
    assert len(chunks[0]) == CHUNK_FRAMES * 2
