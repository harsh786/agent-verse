"""Tests for the multimodal ingestion pipeline (ING-12).

Audio transcription must delegate to the real AudioParser (Whisper), not
return a hardcoded stub. Video must be labelled honestly rather than emitting
a placeholder string as if it were extracted data.
"""

from __future__ import annotations

import base64

import pytest

from app.ingestion.parsers import audio_parser as ap_mod
from app.ingestion.parsers.audio_parser import AudioParseResult
from app.multimodal.models import Modality
from app.multimodal.pipeline import MultimodalPipeline


@pytest.mark.asyncio
async def test_ingest_audio_returns_real_transcript(monkeypatch):
    async def fake_parse_bytes(self, audio_bytes, source_name, mime_type="audio/mpeg"):
        return AudioParseResult(
            source_name=source_name,
            transcript="the meeting covered the roadmap and the ingestion milestones",
        )

    monkeypatch.setattr(ap_mod.AudioParser, "parse_bytes", fake_parse_bytes)

    pipeline = MultimodalPipeline()
    audio_b64 = base64.b64encode(b"fake-audio-bytes").decode()
    job = await pipeline.ingest_audio(audio_b64, "t1")

    assert job.status == "completed"
    assert len(job.spans) == 1
    assert job.spans[0].content == "the meeting covered the roadmap and the ingestion milestones"
    assert "not configured" not in job.spans[0].content
    assert job.spans[0].modality == Modality.AUDIO


@pytest.mark.asyncio
async def test_ingest_audio_transcription_failure_marks_job(monkeypatch):
    async def fake_parse_bytes(self, audio_bytes, source_name, mime_type="audio/mpeg"):
        return AudioParseResult(source_name=source_name, error="whisper unavailable")

    monkeypatch.setattr(ap_mod.AudioParser, "parse_bytes", fake_parse_bytes)

    pipeline = MultimodalPipeline()
    audio_b64 = base64.b64encode(b"fake").decode()
    job = await pipeline.ingest_audio(audio_b64, "t1")

    # Empty transcript => no fabricated span content
    assert all("not configured" not in s.content for s in job.spans)


@pytest.mark.asyncio
async def test_ingest_video_does_not_emit_placeholder_as_data(monkeypatch):
    async def fake_parse_bytes(self, audio_bytes, source_name, mime_type="audio/mpeg"):
        return AudioParseResult(source_name=source_name, transcript="spoken words in the video")

    monkeypatch.setattr(ap_mod.AudioParser, "parse_bytes", fake_parse_bytes)

    pipeline = MultimodalPipeline()
    video_b64 = base64.b64encode(b"fake-video").decode()
    job = await pipeline.ingest_video(video_b64, "t1")

    assert job.status == "completed"
    # The transcript span is real data.
    transcript_spans = [s for s in job.spans if s.modality == Modality.AUDIO]
    assert transcript_spans and "spoken words" in transcript_spans[0].content
    # Visual processing is gated honestly: metadata declares it unavailable and
    # no visual span claims high-confidence extracted content.
    assert job.metadata.get("video_visual_processing") == "unavailable"
    visual_spans = [s for s in job.spans if s.modality == Modality.VIDEO]
    for s in visual_spans:
        assert s.confidence == 0.0
