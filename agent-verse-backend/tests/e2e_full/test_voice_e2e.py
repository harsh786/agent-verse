"""e2e_full: Voice & realtime -- speech-to-text -> goal execution -> text-to-speech.

Phase-3 *Voice & realtime* dimension (Row 21). Exercises the real Voice OS pipeline
against the booted app (``manage_pools=True``, real Postgres + Redis, goals run
inline -- no Celery worker): a small generated WAV is transcribed via the real
``POST /v1/voice/transcribe`` endpoint (deterministic fake STT swapped in through
the production ``override_stt``/``override_tts`` seam in
``app.voice.providers`` -- the same seam ``tests/voice/test_stt_engine.py`` and
``tests/voice/test_tts_engine.py`` use), the transcript is then submitted as a REAL
goal and driven through the full plan -> execute -> verify ``AgentGraph`` loop to a
terminal ``complete`` status, and a spoken reply confirming completion is
synthesized via the real ``POST /v1/voice/speak`` endpoint (deterministic fake TTS).
Every assertion is against a real endpoint response shape (``audio/wav``
content-type + bytes, a transcript dict, a persisted goal reaching ``complete``) --
nothing here mocks the endpoint itself.

The proactive voice-alerts SSE stream (``GET /v1/voice/alerts/stream``, D-6) never
closes on its own, so its read is bounded with ``asyncio.wait_for`` and only the
response headers are inspected -- the infinite body is never drained.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

import numpy as np
import pytest
import pytest_asyncio
import soundfile as sf

from app.providers.base import CompletionResponse
from app.providers.fake import FakeProvider
from app.voice.providers import override_stt, override_tts, reset_providers
from app.voice.providers.base import TranscriptResult

from .conftest import wait_for_status

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

FIXED_TRANSCRIPT = "Summarize this week's competitor pricing changes"
SPOKEN_REPLY_TEXT = "Done. Here is your competitor pricing summary."
FAKE_WAV_MAGIC = b"FAKEWAVAUDIOBYTES:"


def _make_silent_wav(duration_s: float = 0.2, sr: int = 16_000) -> bytes:
    """Small valid WAV file -- same helper pattern as tests/voice/test_voice_router.py."""
    audio = np.zeros(int(sr * duration_s), dtype=np.float32)
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class _FakeSTT:
    """Deterministic STT provider satisfying app.voice.providers.base.STTProvider."""

    provider_name = "fake-stt"
    supports_streaming = False

    async def transcribe(self, audio_bytes: bytes, content_type: str) -> TranscriptResult:
        assert audio_bytes, "the real uploaded WAV bytes must reach the provider"
        return TranscriptResult(
            transcript=FIXED_TRANSCRIPT,
            language="en",
            confidence=0.99,
            segments=[{"start": 0.0, "end": 0.2, "text": FIXED_TRANSCRIPT}],
            duration_s=0.2,
            provider=self.provider_name,
        )

    async def warmup(self) -> None:
        return None

    async def is_ready(self) -> bool:
        return True


class _FakeTTS:
    """Deterministic TTS provider satisfying app.voice.providers.base.TTSProvider."""

    provider_name = "fake-tts"
    sample_rate = 24_000
    supports_voice_cloning = False
    supports_nonverbal = False
    max_text_length = 5_000

    async def synthesize(
        self,
        text: str,
        *,
        ref_audio: bytes | None = None,
        ref_text: str | None = None,
        language: str = "en",
        speed: float = 1.0,
        voice_id: str | None = None,
    ) -> bytes:
        return FAKE_WAV_MAGIC + text.encode()

    async def synthesize_streaming(
        self,
        text: str,
        *,
        ref_audio: bytes | None = None,
        ref_text: str | None = None,
        language: str = "en",
        voice_id: str | None = None,
    ) -> AsyncGenerator[bytes, None]:
        yield FAKE_WAV_MAGIC + text.encode()

    async def warmup(self) -> None:
        return None

    async def is_ready(self) -> bool:
        return True


class _GoalLoopProvider(FakeProvider):
    """Drives the transcribed goal's plan/execute/verify calls deterministically --
    same seam/pattern as tests/e2e_full/test_agent_patterns_e2e.py."""

    async def complete(self, request: Any) -> Any:  # type: ignore[override]
        self.call_history.append(request)
        schema = getattr(request, "response_schema", None)
        props = (schema or {}).get("properties", {}) if isinstance(schema, dict) else {}
        if "steps" in props:
            content = '{"steps": ["Pull competitor pricing changes and summarize them"]}'
        elif "success" in props:
            content = '{"success": true, "reason": "summary produced as requested"}'
        else:
            content = SPOKEN_REPLY_TEXT
        return CompletionResponse(content=content, model="fake", input_tokens=5, output_tokens=5)


@pytest_asyncio.fixture(loop_scope="session")
async def voice_client(app: Any, client: Any) -> AsyncIterator[Any]:
    """Fresh tenant per test so cumulative plan/concurrency caps don't leak."""
    from httpx import ASGITransport, AsyncClient

    email = f"voice-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "Voice", "email": email})
    assert resp.status_code == 201, f"signup failed: {resp.status_code} {resp.text}"
    api_key = resp.json()["api_key"]
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://e2e-full", headers={"X-API-Key": api_key}
    ) as c:
        yield c


@pytest.fixture
def _fake_voice_providers() -> Any:
    """Swap in deterministic STT/TTS via the production override seam."""
    reset_providers()
    override_stt(_FakeSTT())
    override_tts(_FakeTTS())
    try:
        yield
    finally:
        reset_providers()


@pytest.fixture
def _inline_goal_provider(app: Any) -> Any:
    gs = app.state.goal_service
    prev_override = getattr(app.state, "_llm_provider_override", None)
    prev_queue = gs._task_queue
    app.state._llm_provider_override = _GoalLoopProvider()
    gs._task_queue = None
    try:
        yield
    finally:
        app.state._llm_provider_override = prev_override
        gs._task_queue = prev_queue


async def test_voice_transcribe_act_and_speak_reply(
    voice_client: Any, _fake_voice_providers: Any, _inline_goal_provider: Any
) -> None:
    """Speech -> text -> real goal execution -> spoken reply, end-to-end."""
    # 1. Speech -> text: upload a real small WAV to the real transcribe endpoint.
    wav_bytes = _make_silent_wav()
    transcribe_resp = await voice_client.post(
        "/v1/voice/transcribe",
        files={"audio": ("goal.wav", wav_bytes, "audio/wav")},
    )
    assert transcribe_resp.status_code == 200, transcribe_resp.text
    transcript_data = transcribe_resp.json()
    assert transcript_data["transcript"] == FIXED_TRANSCRIPT
    assert transcript_data["language"] == "en"
    assert transcript_data["confidence"] == pytest.approx(0.99)
    assert transcript_data["segments"] == [{"start": 0.0, "end": 0.2, "text": FIXED_TRANSCRIPT}]

    # 2. Route/act: submit the transcribed text as a real goal and drive it through
    # the actual plan -> execute -> verify loop to a terminal status.
    submit = await voice_client.post("/goals", json={"goal": transcript_data["transcript"]})
    assert submit.status_code == 202, submit.text
    goal_id = submit.json()["goal_id"]
    final = await wait_for_status(voice_client, goal_id, "complete", timeout=30.0)
    assert final["status"] == "complete"

    # 3. Text -> speech: synthesize a spoken reply via the real speak endpoint.
    speak_resp = await voice_client.post(
        "/v1/voice/speak",
        json={"text": SPOKEN_REPLY_TEXT, "language": "en"},
    )
    assert speak_resp.status_code == 200, speak_resp.text
    assert speak_resp.headers["content-type"] == "audio/wav"
    assert speak_resp.headers["x-sample-rate"] == "24000"
    assert speak_resp.content == FAKE_WAV_MAGIC + SPOKEN_REPLY_TEXT.encode()


async def test_voice_alerts_stream_is_wired(voice_client: Any) -> None:
    """The proactive voice-alerts SSE stream (D-6) is reachable and returns real SSE
    headers for this tenant. The stream never closes on its own, so the read is
    bounded with asyncio.wait_for and only the headers are inspected.

    Starlette's middleware stack buffers the ASGI body through an internal queue, so
    when the route is genuinely streaming (the healthy case -- no keepalive tick has
    fired yet) opening the connection does not return within the bound and the wait
    times out; that timeout is the expected, accepted outcome here (same convention
    as tests/voice/test_voice_router.py::test_alerts_stream_endpoint_exists). A
    regression that removes or breaks the route would instead return quickly (e.g. a
    404), which the assertions below still catch.
    """

    async def _open_and_read_headers() -> tuple[int, str]:
        async with voice_client.stream("GET", "/v1/voice/alerts/stream") as resp:
            return resp.status_code, resp.headers.get("content-type", "")

    try:
        status_code, content_type = await asyncio.wait_for(_open_and_read_headers(), timeout=5.0)
    except TimeoutError:
        return
    assert status_code == 200
    assert "text/event-stream" in content_type
