"""Tests for app/voice/providers/tts/macos_say.py — MacOSSayTTS.

The real `say` binary is never invoked. `subprocess.run` is patched with a
fake that writes a real (silent) AIFF file to the requested output path, so
the surrounding soundfile read/write logic exercises real code paths.
"""
from __future__ import annotations

import os
import subprocess
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf

from app.voice.providers.tts.macos_say import (
    CHUNK_FRAMES,
    SAMPLE_RATE,
    WAV_HEADER_SZ,
    MacOSSayTTS,
)


def _fake_say_run(cmd, check=True, capture_output=True):
    """Stand-in for subprocess.run(["say", ...]) — writes a real AIFF file."""
    aiff_path = cmd[cmd.index("-o") + 1]
    samples = np.zeros(int(SAMPLE_RATE * 0.1), dtype=np.float32)
    sf.write(aiff_path, samples, SAMPLE_RATE, format="AIFF")
    return subprocess.CompletedProcess(cmd, 0)


def test_metadata():
    tts = MacOSSayTTS()
    assert tts.provider_name == "macos_say"
    assert tts.sample_rate == SAMPLE_RATE
    assert tts.supports_voice_cloning is False
    assert tts.supports_nonverbal is False
    assert tts.max_text_length == 4096


@pytest.mark.asyncio
async def test_warmup_is_noop():
    tts = MacOSSayTTS()
    await tts.warmup()  # must not raise


@pytest.mark.asyncio
async def test_is_ready_true_when_binary_present():
    with patch("os.path.exists", return_value=True):
        tts = MacOSSayTTS()
    assert await tts.is_ready() is True


@pytest.mark.asyncio
async def test_is_ready_false_when_binary_missing():
    with patch("os.path.exists", return_value=False):
        tts = MacOSSayTTS()
    assert await tts.is_ready() is False


@pytest.mark.asyncio
async def test_synthesize_returns_wav_bytes():
    tts = MacOSSayTTS()
    with patch("subprocess.run", side_effect=_fake_say_run):
        wav = await tts.synthesize("hello world")

    assert isinstance(wav, bytes)
    assert len(wav) > WAV_HEADER_SZ
    assert wav[:4] == b"RIFF"


@pytest.mark.asyncio
async def test_synthesize_uses_voice_id_override():
    tts = MacOSSayTTS()
    captured = {}

    def _capture(cmd, check=True, capture_output=True):
        captured["cmd"] = cmd
        return _fake_say_run(cmd, check=check, capture_output=capture_output)

    with patch("subprocess.run", side_effect=_capture):
        await tts.synthesize("hi", voice_id="Victoria")

    assert "-v" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("-v") + 1] == "Victoria"


@pytest.mark.asyncio
async def test_synthesize_uses_env_voice_default(monkeypatch):
    monkeypatch.setenv("MACOS_SAY_VOICE", "Daniel")
    tts = MacOSSayTTS()
    captured = {}

    def _capture(cmd, check=True, capture_output=True):
        captured["cmd"] = cmd
        return _fake_say_run(cmd, check=check, capture_output=capture_output)

    with patch("subprocess.run", side_effect=_capture):
        await tts.synthesize("hi")

    assert captured["cmd"][captured["cmd"].index("-v") + 1] == "Daniel"


@pytest.mark.asyncio
async def test_synthesize_scales_rate_with_speed():
    tts = MacOSSayTTS()
    captured = {}

    def _capture(cmd, check=True, capture_output=True):
        captured["cmd"] = cmd
        return _fake_say_run(cmd, check=check, capture_output=capture_output)

    with patch("subprocess.run", side_effect=_capture):
        await tts.synthesize("hi", speed=2.0)

    rate = int(captured["cmd"][captured["cmd"].index("-r") + 1])
    assert rate == 360  # 180 * 2.0


@pytest.mark.asyncio
async def test_synthesize_cleans_up_temp_file_on_subprocess_failure():
    """If `say` fails, the temp AIFF file must still be removed."""
    tts = MacOSSayTTS()
    captured_path = {}

    def _fail(cmd, check=True, capture_output=True):
        captured_path["aiff"] = cmd[cmd.index("-o") + 1]
        raise subprocess.CalledProcessError(1, cmd)

    with patch("subprocess.run", side_effect=_fail):
        with pytest.raises(subprocess.CalledProcessError):
            await tts.synthesize("hi")

    assert not os.path.exists(captured_path["aiff"])


@pytest.mark.asyncio
async def test_synthesize_streaming_yields_pcm_chunks():
    tts = MacOSSayTTS()

    def _long_say(cmd, check=True, capture_output=True):
        aiff_path = cmd[cmd.index("-o") + 1]
        samples = np.zeros(SAMPLE_RATE * 2, dtype=np.float32)  # 2s of audio
        sf.write(aiff_path, samples, SAMPLE_RATE, format="AIFF")
        return subprocess.CompletedProcess(cmd, 0)

    chunks = []
    with patch("subprocess.run", side_effect=_long_say):
        async for chunk in tts.synthesize_streaming("hello there"):
            chunks.append(chunk)

    assert len(chunks) > 1
    assert all(isinstance(c, bytes) for c in chunks)
    assert len(chunks[0]) == CHUNK_FRAMES * 2
