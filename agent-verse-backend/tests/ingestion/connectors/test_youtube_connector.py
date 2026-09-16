"""Tests for YouTubeConnector — validate_connection, get_delta (explicit video
ids, channel discovery via YouTube Data API, transcript-disabled handling).

youtube-transcript-api is not installed in this environment, so a fake module
is injected for the success paths (mirrors the pypdf pattern used elsewhere);
the channel-discovery / metadata-enrichment paths use httpx, which patch as
in the other httpx-based connector tests."""
from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.connectors.youtube_connector import YouTubeConnector
from app.ingestion.source_config import SourceConfig


def _make_config(conn_config: dict | None = None) -> SourceConfig:
    return SourceConfig(
        source_id="src-yt",
        tenant_id="t1",
        name="Test YouTube",
        family="web",
        source_type="youtube",
        enabled=True,
        connection_config=conn_config or {"video_ids": ["vid1"]},
    )


class _FakeTranscriptsDisabledError(Exception):
    pass


def _install_fake_ytapi(transcript_result=None, get_transcript_side_effect=None):
    fake_mod = types.ModuleType("youtube_transcript_api")

    class FakeYouTubeTranscriptApi:
        @staticmethod
        def get_transcript(video_id, languages=None):
            if get_transcript_side_effect is not None:
                exc = get_transcript_side_effect
                if isinstance(exc, dict):
                    exc = exc.get(video_id)
                if exc is not None:
                    raise exc
            return transcript_result or [{"text": "hello"}, {"text": "world"}]

    fake_mod.YouTubeTranscriptApi = FakeYouTubeTranscriptApi
    fake_mod.TranscriptsDisabled = _FakeTranscriptsDisabledError
    sys.modules["youtube_transcript_api"] = fake_mod
    return fake_mod


@pytest.fixture(autouse=True)
def _clean_module():
    saved = sys.modules.get("youtube_transcript_api")
    yield
    if saved is not None:
        sys.modules["youtube_transcript_api"] = saved
    else:
        sys.modules.pop("youtube_transcript_api", None)


def _mock_async_client(get_impl):
    mock_client = AsyncMock()
    mock_client.get = get_impl
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


class TestValidateConnection:
    async def test_no_library_installed(self):
        sys.modules.pop("youtube_transcript_api", None)
        connector = YouTubeConnector()
        health = await connector.validate_connection(_make_config())
        assert health.ok is False
        assert "youtube-transcript-api" in health.error

    async def test_success(self):
        _install_fake_ytapi(transcript_result=[{"text": "a"}, {"text": "b"}, {"text": "c"}])
        connector = YouTubeConnector()
        health = await connector.validate_connection(_make_config())
        assert health.ok is True
        assert health.metadata["segments"] == 3

    async def test_generic_exception(self):
        _install_fake_ytapi(get_transcript_side_effect=RuntimeError("video unavailable"))
        connector = YouTubeConnector()
        health = await connector.validate_connection(_make_config())
        assert health.ok is False
        assert "video unavailable" in health.error


class TestGetDelta:
    async def test_no_library_yields_nothing(self):
        sys.modules.pop("youtube_transcript_api", None)
        connector = YouTubeConnector()
        docs = [d async for d in connector.get_delta(_make_config(), None)]
        assert docs == []

    async def test_explicit_video_ids_no_api_key(self):
        _install_fake_ytapi(transcript_result=[{"text": "hello"}, {"text": "world"}])
        connector = YouTubeConnector()
        config = _make_config({"video_ids": ["vid1", "vid2"]})
        results = [d async for d in connector.get_delta(config, None)]

        assert len(results) == 2
        doc0, cursor0 = results[0]
        assert "hello world" in doc0.content.decode()
        assert doc0.metadata["video_id"] == "vid1"
        assert cursor0 == "vid1"  # title fallback == video_id since no api_key

    async def test_channel_discovery_with_api_key(self):
        _install_fake_ytapi(transcript_result=[{"text": "seg"}])

        async def get(url, params=None):
            resp = MagicMock()
            resp.is_success = True
            if "search" in url:
                resp.json = MagicMock(
                    return_value={"items": [{"id": {"videoId": "vidA"}}]}
                )
            else:  # /videos enrichment
                resp.json = MagicMock(
                    return_value={
                        "items": [
                            {
                                "snippet": {
                                    "title": "My Video",
                                    "publishedAt": "2026-01-01T00:00:00Z",
                                }
                            }
                        ]
                    }
                )
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            connector = YouTubeConnector()
            config = _make_config({"channel_id": "chan1", "api_key": "key123"})
            results = [d async for d in connector.get_delta(config, None)]

        assert len(results) == 1
        doc, cursor = results[0]
        assert doc.metadata["title"] == "My Video"
        assert doc.metadata["video_id"] == "vidA"

    async def test_transcripts_disabled_is_skipped(self):
        _install_fake_ytapi(
            get_transcript_side_effect={"vid1": _FakeTranscriptsDisabledError("disabled")}
        )
        connector = YouTubeConnector()
        config = _make_config({"video_ids": ["vid1"]})
        results = [d async for d in connector.get_delta(config, None)]
        assert results == []

    async def test_generic_exception_per_video_is_skipped(self):
        _install_fake_ytapi(get_transcript_side_effect={"vid1": RuntimeError("boom")})
        connector = YouTubeConnector()
        config = _make_config({"video_ids": ["vid1", "vid2"]})
        # vid2 succeeds with default transcript
        results = [d async for d in connector.get_delta(config, None)]
        assert len(results) == 1
        assert results[0][0].metadata["video_id"] == "vid2"

    async def test_max_videos_limits_results(self):
        _install_fake_ytapi(transcript_result=[{"text": "x"}])
        connector = YouTubeConnector()
        config = _make_config({"video_ids": ["v1", "v2", "v3"], "max_videos": 2})
        results = [d async for d in connector.get_delta(config, None)]
        assert len(results) == 2


def test_source_type_and_registration():
    from app.ingestion.connector_registry import get_connector

    assert YouTubeConnector().source_type == "youtube"
    assert get_connector("youtube") is YouTubeConnector
