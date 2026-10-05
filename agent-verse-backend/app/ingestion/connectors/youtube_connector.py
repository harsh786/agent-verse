"""YouTubeConnector — YouTube video transcript ingestion.

Uses youtube-transcript-api for transcript extraction.
Cursor: video publishedAt timestamp or videoId.
Supports: channel videos, playlists, and individual video URLs.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    ConnectorUnavailableError,
    describe_fetch_error,
    ensure_success,
    fetch_failure_document,
    stable_doc_id,
)
from app.ingestion.connector_registry import register
from app.ingestion.sdk_executor import run_blocking

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)

# CONNECTOR_REPLAY_KEY "kind" of a video whose transcript a sync could not read.
_REPLAY_KIND = "youtube_video"
_YT_API = "https://www.googleapis.com/youtube/v3"
# Per-request timeout for transcript fetches: they run on a worker thread that
# cannot be interrupted, and requests has no default timeout.
_TRANSCRIPT_TIMEOUT_S = 30.0


def _fetch_transcript(api_cls: Any, video_id: str, languages: list[str]) -> list[dict[str, Any]]:
    """One video's transcript segments, via the youtube-transcript-api >= 1.0 API.

    The 0.x static ``YouTubeTranscriptApi.get_transcript`` was removed in 1.x;
    ``YouTubeTranscriptApi().fetch()`` returns a ``FetchedTranscript``. An
    instance holds a ``requests.Session`` and is not thread-safe, so each call
    (on the SDK pool) builds its own, with a session that bounds every request.
    """
    import requests

    class _BoundedSession(requests.Session):
        def request(self, method: Any, url: Any, *args: Any, **kwargs: Any) -> Any:
            kwargs.setdefault("timeout", _TRANSCRIPT_TIMEOUT_S)
            return super().request(method, url, *args, **kwargs)

    fetched = api_cls(http_client=_BoundedSession()).fetch(video_id, languages=languages)
    return list(fetched.to_raw_data())


@register("youtube", feature_flag="ingestion_connector_youtube_enabled")
class YouTubeConnector(BaseConnector):
    """YouTube connector — extracts transcripts from channel/playlist videos."""

    source_type = "youtube"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            from youtube_transcript_api import (
                YouTubeTranscriptApi,  # type: ignore[import-not-found]
            )

            # Test with a well-known public video
            test_id = config.connection_config.get("test_video_id", "dQw4w9WgXcQ")
            transcript = await run_blocking(
                _fetch_transcript, YouTubeTranscriptApi, test_id, ["en"]
            )
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(
                ok=True,
                latency_ms=latency,
                metadata={"test_video": test_id, "segments": len(transcript)},
            )
        except ImportError:
            return ConnectionHealth(ok=False, error="youtube-transcript-api not installed")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument

        try:
            from youtube_transcript_api import (  # type: ignore[import-not-found]
                TranscriptsDisabled,
                YouTubeTranscriptApi,
            )
        except ImportError as exc:
            # Returning nothing here reported a successful, empty sync.
            raise ConnectorUnavailableError(
                "youtube-transcript-api is not installed on this server; the connector cannot run"
            ) from exc

        cc = config.connection_config
        video_ids = cc.get("video_ids") or []
        channel_id = cc.get("channel_id", "")
        api_key = cc.get("api_key", "")
        max_videos = int(cc.get("max_videos", 50))
        languages = cc.get("languages") or ["en"]

        new_cursor = cursor or ""

        # Fetch video IDs from channel if not explicitly provided
        if not video_ids and channel_id and api_key:
            import httpx

            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"{_YT_API}/search",
                    params={
                        "key": api_key,
                        "channelId": channel_id,
                        "part": "id,snippet",
                        "order": "date",
                        "maxResults": max_videos,
                        "type": "video",
                        **({"publishedAfter": cursor} if cursor else {}),
                    },
                )
                # USR-1: a failed channel listing fails the sync (it used to sync
                # nothing and report success).
                ensure_success(r, source_type="youtube", what=f"video search of {channel_id}")
                for item in r.json().get("items", []):
                    video_ids.append(item["id"]["videoId"])

        for video_id in video_ids[:max_videos]:
            try:
                transcript_list = await run_blocking(
                    _fetch_transcript, YouTubeTranscriptApi, video_id, list(languages)
                )
                text = " ".join(seg["text"] for seg in transcript_list)
                title = video_id  # fallback; enrich via API if api_key provided
                if api_key:
                    import httpx

                    async with httpx.AsyncClient(timeout=10) as client:
                        r = await client.get(
                            f"{_YT_API}/videos",
                            params={"key": api_key, "id": video_id, "part": "snippet"},
                        )
                        if r.is_success:
                            items = r.json().get("items", [])
                            if items:
                                snippet = items[0].get("snippet", {})
                                title = snippet.get("title", video_id)
                                published = snippet.get("publishedAt", "")
                                new_cursor = max(new_cursor, published)

                full_text = f"# {title}\n\n{text}"
                doc = RawDocument(
                    doc_id=stable_doc_id(config, video_id),
                    source_id=config.source_id,
                    tenant_id=config.tenant_id,
                    source_url=f"https://www.youtube.com/watch?v={video_id}",
                    content=full_text.encode(),
                    content_type="text/plain",
                    metadata={"video_id": video_id, "title": title},
                )
                new_cursor = max(new_cursor, video_id)
                yield doc, new_cursor

            except ConnectorUnavailableError:
                raise
            except Exception as exc:
                # USR-1: a video whose transcript cannot be read is a counted
                # failure (→ DLQ) — it used to be logged and skipped.
                disabled = isinstance(exc, TranscriptsDisabled)
                _log.warning("youtube: cannot read %s: %s", video_id, exc)
                yield fetch_failure_document(
                    config,
                    doc_id=stable_doc_id(config, video_id),
                    reason=(
                        "transcripts are disabled for this video"
                        if disabled
                        else describe_fetch_error(exc)
                    ),
                    retryable=not disabled,
                    source_url=f"https://www.youtube.com/watch?v={video_id}",
                    replay=None if disabled else {"kind": _REPLAY_KIND, "video_id": video_id},
                    metadata={"video_id": video_id},
                ), new_cursor

    async def replay_event(
        self, config: SourceConfig, reference: dict[str, Any]
    ) -> AsyncIterator[RawDocument]:
        """Fetch again the transcript of a video a sync could not read (DLQ retry)."""
        from app.ingestion.source_config import RawDocument

        video_id = str(reference.get("video_id") or "")
        if reference.get("kind") != _REPLAY_KIND or not video_id:
            raise ValueError(f"not a YouTube video replay reference: {reference!r}")
        from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore[import-not-found]

        languages = config.connection_config.get("languages") or ["en"]
        transcript_list = await run_blocking(
            _fetch_transcript, YouTubeTranscriptApi, video_id, list(languages)
        )
        text = " ".join(seg["text"] for seg in transcript_list)
        yield RawDocument(
            doc_id=stable_doc_id(config, video_id),
            source_id=config.source_id,
            tenant_id=config.tenant_id,
            source_url=f"https://www.youtube.com/watch?v={video_id}",
            content=f"# {video_id}\n\n{text}".encode(),
            content_type="text/plain",
            metadata={"video_id": video_id, "title": video_id},
        )
