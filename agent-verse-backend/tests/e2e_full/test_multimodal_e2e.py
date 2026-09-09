"""e2e_full: multimodal ingestion (image + audio) processed end-to-end.

Proves the real ``MultimodalPipeline`` (``app/multimodal/pipeline.py``), wired
on ``app.state.multimodal_pipeline`` and exposed at ``POST /multimodal/ingest``
/ ``GET /multimodal/jobs/{job_id}`` (``app/api/multimodal.py``), actually
extracts content and persists a queryable job — not that a mock was called.

Two real assets are generated in-test (no opaque binary fixtures):

* a tiny PNG (via Pillow) sent through the image path, where the pipeline
  calls the vision-capable LLM (``_describe_image``) to produce a caption.
  The deterministic ``FakeProvider`` stands in for the vision LLM.
* a short silent WAV (via the stdlib ``wave`` module) sent through the audio
  path, where the pipeline delegates to the real
  ``app.ingestion.parsers.audio_parser.AudioParser``. Only the actual network
  call inside ``AudioParser._transcribe_with_whisper`` (OpenAI Whisper) is
  patched to a deterministic fake — every other line of the real parser,
  pipeline, and API runs unmodified.

Both jobs are read back via the real job-status endpoint, and a second tenant
is proven unable to reach the first tenant's job (isolation).
"""

from __future__ import annotations

import base64
import io
import uuid
import wave
from typing import Any

import pytest

from app.providers.fake import FakeProvider

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_IMAGE_CAPTION = "A single crimson square centered on a white background."
_AUDIO_TRANSCRIPT = "The nightingale audit begins at dawn over the reservoir."


def _tiny_png_base64() -> str:
    """A real, tiny (4x4 red) PNG — generated, not an opaque binary fixture."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(220, 20, 60)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _tiny_wav_base64() -> str:
    """A real, short (0.2s) silent mono WAV via the stdlib ``wave`` module."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 1600)  # 0.2s of silence at 8kHz
    return base64.b64encode(buf.getvalue()).decode("ascii")


@pytest.fixture
def _vision_provider(app: Any) -> Any:
    """Pin a deterministic vision-capable FakeProvider as the app-wide LLM.

    ``/multimodal/ingest`` reads ``app.state._app_provider`` fresh on every
    request (``app/api/multimodal.py::_get_pipeline`` + ``ingest_asset``), so
    unlike the ingestion-pipeline case this is a plain ``app.state`` swap.
    """
    fake = FakeProvider(responses=[_IMAGE_CAPTION], vision=True)
    prev = getattr(app.state, "_app_provider", None)
    app.state._app_provider = fake
    try:
        yield fake
    finally:
        app.state._app_provider = prev


@pytest.fixture
def _fake_whisper(monkeypatch: Any) -> None:
    """Patch only the real network call inside AudioParser to a deterministic fake.

    Everything else in ``AudioParser.parse_bytes`` (segment/transcript
    assembly) and the multimodal pipeline's ``_transcribe_audio`` runs for
    real against this fake transcription object.
    """
    from types import SimpleNamespace

    from app.ingestion.parsers.audio_parser import AudioParser

    async def _fake_transcribe(self: Any, audio_bytes: bytes, filename: str, mime_type: str) -> Any:
        assert audio_bytes, "pipeline must pass through the real decoded audio bytes"
        return SimpleNamespace(
            text=_AUDIO_TRANSCRIPT,
            segments=[SimpleNamespace(start=0.0, end=0.2, text=_AUDIO_TRANSCRIPT)],
            language="en",
        )

    monkeypatch.setattr(AudioParser, "_transcribe_with_whisper", _fake_transcribe)


async def test_image_asset_ingested_and_captioned(
    app: Any, client: Any, tenant_client: Any, _vision_provider: Any
) -> None:
    """An image submitted through the API is captioned and stored as a completed job."""
    resp = await tenant_client.post(
        "/multimodal/ingest",
        json={
            "modality": "image",
            "base64_data": _tiny_png_base64(),
            "filename": f"e2e-{uuid.uuid4().hex[:8]}.png",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed", body
    assert body["span_count"] == 1, body
    assert body["spans"][0]["content"] == _IMAGE_CAPTION, body
    assert body["spans"][0]["modality"] == "image"
    # D-11 honesty labels: a caption was embedded, not a native pixel embedding.
    assert body["embedding_strategy"] == "caption_then_text_embed"
    assert body["real_multimodal_embedding"] is False
    assert body["extractor_model"], "ModelOrchestrator must select a real extractor model"
    job_id = body["job_id"]

    # Retrievable via the real job-status endpoint, scoped to this tenant.
    status = await tenant_client.get(f"/multimodal/jobs/{job_id}")
    assert status.status_code == 200, status.text
    status_body = status.json()
    assert status_body["status"] == "completed"
    assert status_body["asset_type"] == "image"
    assert status_body["span_count"] == 1

    # Isolation: a second tenant must not be able to fetch this job.
    email = f"mm-e2e-b-{uuid.uuid4().hex[:12]}@example.com"
    signup = await client.post("/tenants/signup", json={"name": "MM Isolation B", "email": email})
    assert signup.status_code == 201, signup.text
    api_key_b = signup.json()["api_key"]

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://e2e-full",
        headers={"X-API-Key": api_key_b},
    ) as client_b:
        leaked = await client_b.get(f"/multimodal/jobs/{job_id}")
        assert leaked.status_code == 404, (
            f"tenant B fetched tenant A's multimodal job (isolation leak): {leaked.text}"
        )


async def test_audio_asset_transcribed_end_to_end(
    app: Any, client: Any, tenant_client: Any, _fake_whisper: None
) -> None:
    """A WAV submitted through the API is transcribed via the real AudioParser."""
    resp = await tenant_client.post(
        "/multimodal/ingest",
        json={"modality": "audio", "base64_data": _tiny_wav_base64()},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed", body
    assert body["span_count"] == 1, body
    assert body["spans"][0]["content"] == _AUDIO_TRANSCRIPT, body
    assert body["spans"][0]["modality"] == "audio"
    assert body["embedding_strategy"] == "transcript_then_text_embed"
    assert body["real_multimodal_embedding"] is False
    job_id = body["job_id"]

    status = await tenant_client.get(f"/multimodal/jobs/{job_id}")
    assert status.status_code == 200, status.text
    status_body = status.json()
    assert status_body["status"] == "completed"
    assert status_body["asset_type"] == "audio"
    assert status_body["span_count"] == 1

    # Isolation: a second tenant must not be able to fetch this job.
    email = f"mm-e2e-c-{uuid.uuid4().hex[:12]}@example.com"
    signup = await client.post("/tenants/signup", json={"name": "MM Isolation C", "email": email})
    assert signup.status_code == 201, signup.text
    api_key_c = signup.json()["api_key"]

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://e2e-full",
        headers={"X-API-Key": api_key_c},
    ) as client_c:
        leaked = await client_c.get(f"/multimodal/jobs/{job_id}")
        assert leaked.status_code == 404, (
            f"tenant C fetched tenant A's multimodal job (isolation leak): {leaked.text}"
        )
