"""YouTubeConnector — YouTube video transcript ingestion.

Uses youtube-transcript-api for transcript extraction.
Cursor: video publishedAt timestamp or videoId.
Supports: channel videos, playlists, and individual video URLs.
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, AsyncIterator

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)
_YT_API = "https://www.googleapis.com/youtube/v3"


@register("youtube", feature_flag="ingestion_connector_youtube_enabled")
class YouTubeConnector(BaseConnector):
    """YouTube connector — extracts transcripts from channel/playlist videos."""

    source_type = "youtube"

    async def validate_connection(self, config: "SourceConfig") -> ConnectionHealth:
        import time
        t0 = time.perf_counter()
        try:
            from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore[import-not-found]
            # Test with a well-known public video
            test_id = config.connection_config.get("test_video_id", "dQw4w9WgXcQ")
            transcript = YouTubeTranscriptApi.get_transcript(test_id)
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(
                ok=True, latency_ms=latency,
                metadata={"test_video": test_id, "segments": len(transcript)},
            )
        except ImportError:
            return ConnectionHealth(ok=False, error="youtube-transcript-api not installed")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: "SourceConfig", cursor: str | None
    ) -> AsyncIterator[tuple["RawDocument", str]]:
        from app.ingestion.source_config import RawDocument
        try:
            from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled  # type: ignore[import-not-found]
        except ImportError:
            _log.error("youtube-transcript-api not installed"); return

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
                if r.is_success:
                    for item in r.json().get("items", []):
                        video_ids.append(item["id"]["videoId"])

        for video_id in video_ids[:max_videos]:
            try:
                transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
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
                    doc_id=str(uuid.uuid4()),
                    source_id=config.source_id,
                    tenant_id=config.tenant_id,
                    source_url=f"https://www.youtube.com/watch?v={video_id}",
                    content=full_text.encode(),
                    content_type="text/plain",
                    metadata={"video_id": video_id, "title": title},
                )
                new_cursor = max(new_cursor, video_id)
                yield doc, new_cursor

            except TranscriptsDisabled:
                _log.info("youtube: transcripts disabled for %s", video_id)
            except Exception as exc:
                _log.warning("youtube: skip %s: %s", video_id, exc)
