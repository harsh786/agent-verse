"""RSSConnector — RSS and Atom feed ingestion.

Cursor: the ISO 8601 UTC timestamp (``published``/``updated``) of the newest
entry processed. Entries are yielded oldest first, so the cursor only moves
forward and an interrupted sync resumes where it stopped.
Supports: any RSS 0.9x/2.0, Atom 0.3/1.0, or RDF feed URL.

Connection config:
    url          The feed URL (``feed_url`` and a one-line ``urls`` are accepted
                 for Sources created by older UI builds).
    max_entries  Cap on entries ingested per sync (default 200).
"""

from __future__ import annotations

import calendar
import datetime
import logging
import uuid
from collections.abc import AsyncIterator
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    ConnectorUnavailableError,
)
from app.ingestion.connector_egress import assert_source_url, guarded_request, source_client
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)

_MAX_FEED_BYTES = 20 * 1024 * 1024
_FEEDPARSER_MISSING = "feedparser is not installed on this server; the RSS connector cannot run"


def _feed_url(cc: dict[str, Any]) -> str:
    """The configured feed URL (``url``; ``feed_url`` / ``urls`` from older UI payloads)."""
    for key in ("url", "feed_url"):
        value = str(cc.get(key) or "").strip()
        if value:
            return value
    urls = cc.get("urls")
    if isinstance(urls, list | tuple):
        urls = "\n".join(str(u) for u in urls)
    for line in str(urls or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _feedparser() -> Any:
    try:
        import feedparser  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ConnectorUnavailableError(_FEEDPARSER_MISSING) from exc
    return feedparser


async def _fetch_feed(url: str) -> bytes:
    """Fetch the feed through the egress guard and return its bytes.

    ``feedparser.parse(url)`` must never see the tenant's string: it fetches with
    urllib (following redirects to anywhere, including 169.254.169.254) and, given
    a path or ``file://`` URI, reads the platform's own filesystem. The URL is
    guarded here, every redirect hop re-checked, and feedparser only parses bytes.
    """

    assert_source_url(url, context="rss")
    async with source_client(timeout=30) as client:
        r = await guarded_request(client, "GET", url, context="rss")
        r.raise_for_status()
        return bytes(r.content[:_MAX_FEED_BYTES])


def _entry_timestamp(entry: Any) -> str:
    """The entry's published/updated time as ISO 8601 UTC, or ``""`` if unknown.

    RFC 2822 strings (``Thu, 01 Jan 2026 ...``) must never be compared as text —
    weekday names do not sort chronologically — so every form is normalised.
    """
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                ts = calendar.timegm(parsed)
                return datetime.datetime.fromtimestamp(ts, datetime.UTC).isoformat()
            except (TypeError, ValueError, OverflowError):
                continue
    for key in ("published", "updated"):
        raw = str(entry.get(key) or "").strip()
        if not raw:
            continue
        dt: datetime.datetime | None
        try:
            dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = parsedate_to_datetime(raw)
            except (TypeError, ValueError):
                dt = None
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.UTC)
            return dt.astimezone(datetime.UTC).isoformat()
    return ""


def _normalise_cursor(cursor: str | None) -> str:
    """Cursors written before timestamps were normalised may be RFC 2822."""
    if not cursor:
        return ""
    return _entry_timestamp({"published": cursor}) or cursor


@register("rss")
@register("atom")
class RSSConnector(BaseConnector):
    """RSS/Atom feed connector — polls any feed URL and yields new entries."""

    source_type = "rss"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            feedparser = _feedparser()
            feed = feedparser.parse(await _fetch_feed(_feed_url(config.connection_config)))
            if not feed.entries and (feed.bozo or not feed.get("version")):
                reason = getattr(feed, "bozo_exception", None) or "no RSS/Atom entries found"
                raise ValueError(f"URL did not return a valid RSS/Atom feed: {reason}")
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(
                ok=True,
                latency_ms=latency,
                metadata={"title": feed.feed.get("title"), "entries": len(feed.entries)},
            )
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument

        url = _feed_url(config.connection_config)
        assert_source_url(url, context="rss")
        feedparser = _feedparser()  # raises: an empty sync must not look successful

        max_entries = int(config.connection_config.get("max_entries", 200))
        feed = feedparser.parse(await _fetch_feed(url))
        if not feed.entries and feed.bozo:
            raise ValueError(
                f"URL did not return a valid RSS/Atom feed: {getattr(feed, 'bozo_exception', None)}"
            )

        since = _normalise_cursor(cursor)
        stamped = [(_entry_timestamp(entry), entry) for entry in feed.entries]
        # Oldest first (undated entries last, in feed order) so the cursor only
        # advances and max_entries leaves the newer entries for the next sync.
        stamped.sort(key=lambda pair: (pair[0] == "", pair[0]))
        new_cursor = since

        emitted = 0
        for published, entry in stamped:
            if emitted >= max_entries:
                break
            if since and published and published <= since:
                continue
            if since and not published:
                continue  # undated entries are only ingested on a full sync
            new_cursor = max(new_cursor, published)

            title = entry.get("title", "")
            summary = entry.get("summary", "") or entry.get("description", "")
            link = entry.get("link", "")
            entry_key = entry.get("id") or link or f"{title}|{published}"
            text = f"# {title}\n\n{summary}\n\nSource: {link}"

            doc = RawDocument(
                doc_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{url}#{entry_key}")),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=link or url,
                content=text.encode(),
                content_type="text/plain",
                metadata={"title": title, "link": link, "published": published, "feed": url},
            )
            emitted += 1
            yield doc, new_cursor
