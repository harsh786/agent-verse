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

from app.ingestion.base_connector import ConnectorUnavailableError

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


FETCH_CALLS: list[tuple] = []


def _install_fake_ytapi(transcript_result=None, get_transcript_side_effect=None):
    """A fake of the youtube-transcript-api >= 1.0 instance API
    (``YouTubeTranscriptApi(http_client=...).fetch(video_id, languages=...)``
    returning a FetchedTranscript with ``to_raw_data()``). The static 0.x
    ``get_transcript`` no longer exists, so the fake fails if it is used."""
    fake_mod = types.ModuleType("youtube_transcript_api")
    FETCH_CALLS.clear()

    class _Fetched:
        def __init__(self, raw):
            self._raw = raw

        def to_raw_data(self):
            return self._raw

    class FakeYouTubeTranscriptApi:
        def __init__(self, proxy_config=None, http_client=None):
            self.http_client = http_client

        def fetch(self, video_id, languages=("en",)):
            FETCH_CALLS.append((video_id, tuple(languages), self.http_client))
            if get_transcript_side_effect is not None:
                exc = get_transcript_side_effect
                if isinstance(exc, dict):
                    exc = exc.get(video_id)
                if exc is not None:
                    raise exc
            return _Fetched(transcript_result or [{"text": "hello"}, {"text": "world"}])

        @staticmethod
        def get_transcript(*_a, **_k):
            raise AssertionError("get_transcript was removed in youtube-transcript-api 1.x")

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
        sys.modules["youtube_transcript_api"] = None  # simulate a server without it
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
    async def test_no_library_fails_loudly(self):
        sys.modules["youtube_transcript_api"] = None  # simulate a server without it
        connector = YouTubeConnector()
        with pytest.raises(ConnectorUnavailableError):
            [d async for d in connector.get_delta(_make_config(), None)]

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

    async def test_uses_the_1x_instance_api_with_a_bounded_session(self):
        """UNPIN-SDKS: one API instance per fetch (it is not thread-safe), the
        configured languages, and an HTTP session whose requests carry a timeout
        (the fetch runs on a worker thread that cannot be interrupted)."""
        _install_fake_ytapi(transcript_result=[{"text": "hola"}])
        config = _make_config({"video_ids": ["v1", "v2"], "languages": ["es", "en"]})
        [d async for d in YouTubeConnector().get_delta(config, None)]
        assert [(vid, langs) for vid, langs, _s in FETCH_CALLS] == [
            ("v1", ("es", "en")),
            ("v2", ("es", "en")),
        ]
        sessions = [s for _v, _l, s in FETCH_CALLS]
        assert sessions[0] is not sessions[1]
        with patch("requests.Session.request", return_value="ok") as request:
            sessions[0].request("GET", "https://www.youtube.com/watch")
        assert request.call_args.kwargs["timeout"] > 0

    async def test_transcript_fetch_does_not_block_the_event_loop(self):
        import asyncio
        import time

        def _slow(*_a, **_k):
            time.sleep(0.5)
            return [{"text": "x"}]

        _install_fake_ytapi()
        sys.modules["youtube_transcript_api"].YouTubeTranscriptApi.fetch = (
            lambda self, video_id, languages=("en",): type(
                "F", (), {"to_raw_data": staticmethod(_slow)}
            )()
        )
        lags: list[float] = []
        done = asyncio.Event()

        async def _probe():
            loop = asyncio.get_running_loop()
            while not done.is_set():
                start = loop.time()
                await asyncio.sleep(0.01)
                lags.append(loop.time() - start - 0.01)

        probe = asyncio.create_task(_probe())
        await asyncio.sleep(0)
        docs = [d async for d in YouTubeConnector().get_delta(_make_config(), None)]
        done.set()
        await probe
        assert len(docs) == 1
        assert max(lags) < 0.3, f"event loop blocked for {max(lags):.3f}s"

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
