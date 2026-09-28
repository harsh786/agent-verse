"""RSSConnector — RSS and Atom feed ingestion.

Cursor: pubDate/updated of the latest fetched entry (RFC 2822 / ISO 8601).
Supports: any RSS 2.0, Atom 1.0, or RDF feed URL.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_egress import assert_source_url, guarded_request
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)

_MAX_FEED_BYTES = 20 * 1024 * 1024


async def _fetch_feed(url: str) -> bytes:
    """Fetch the feed through the egress guard and return its bytes.

    ``feedparser.parse(url)`` must never see the tenant's string: it fetches with
    urllib (following redirects to anywhere, including 169.254.169.254) and, given
    a path or ``file://`` URI, reads the platform's own filesystem. The URL is
    guarded here, every redirect hop re-checked, and feedparser only parses bytes.
    """
    import httpx

    assert_source_url(url, context="rss")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await guarded_request(client, "GET", url, context="rss")
        r.raise_for_status()
        return bytes(r.content[:_MAX_FEED_BYTES])


@register("rss")
@register("atom")
class RSSConnector(BaseConnector):
    """RSS/Atom feed connector — polls any feed URL and yields new entries."""

    source_type = "rss"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            import feedparser  # type: ignore[import-not-found]

            url = config.connection_config.get("url", "")
            feed = feedparser.parse(await _fetch_feed(url))
            if feed.bozo and not feed.entries:
                raise ValueError(str(feed.bozo_exception))
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(
                ok=True,
                latency_ms=latency,
                metadata={"title": feed.feed.get("title"), "entries": len(feed.entries)},
            )
        except ImportError:
            return ConnectionHealth(
                ok=False, error="feedparser not installed — pip install feedparser"
            )
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument

        url = config.connection_config.get("url", "")
        assert_source_url(url, context="rss")
        try:
            import feedparser  # type: ignore[import-not-found]
        except ImportError:
            _log.error("feedparser not installed")
            return

        max_entries = int(config.connection_config.get("max_entries", 200))

        feed = feedparser.parse(await _fetch_feed(url))
        new_cursor = cursor or ""

        for entry in feed.entries[:max_entries]:
            entry.get("id") or entry.get("link") or str(uuid.uuid4())
            published = entry.get("published") or entry.get("updated") or ""
            # Skip if already processed
            if cursor and published and published <= cursor:
                continue
            new_cursor = max(new_cursor, published)

            title = entry.get("title", "")
            summary = entry.get("summary", "") or entry.get("description", "")
            link = entry.get("link", "")
            text = f"# {title}\n\n{summary}\n\nSource: {link}"

            doc = RawDocument(
                doc_id=str(uuid.uuid4()),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=link or url,
                content=text.encode(),
                content_type="text/plain",
                metadata={"title": title, "link": link, "published": published, "feed": url},
            )
            yield doc, new_cursor
