"""SRC-RSS: the RSS/Atom connector end to end against a real feed served locally.

No mocks of feedparser or of the fetch: a real HTTP server on 127.0.0.1 serves
real RSS 2.0 / Atom payloads, the connector fetches them through the SSRF-pinned
egress client (127.0.0.1 is put on the operator allowlist for the test, exactly
as an on-prem deployment would), and the real ``feedparser`` parses the bytes.
"""

from __future__ import annotations

import http.server
import sys
import threading
from collections.abc import Iterator
from typing import Any

import pytest

from app.ingestion.base_connector import ConnectorUnavailableError
from app.ingestion.connectors.rss_connector import RSSConnector
from app.ingestion.source_config import SourceConfig, SourceFamily

# Weekday names deliberately out of lexical order: "Thu" < "Wed" as strings,
# while Thu 01 Jan 2026 is *after* Wed 31 Dec 2025.
_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Local Test Feed</title>
  <link>http://127.0.0.1/</link>
  <description>fixture</description>
  <item>
    <title>Newest post</title>
    <link>http://127.0.0.1/posts/3</link>
    <guid>post-3</guid>
    <pubDate>Fri, 02 Jan 2026 09:00:00 GMT</pubDate>
    <description>third body</description>
  </item>
  <item>
    <title>Middle post</title>
    <link>http://127.0.0.1/posts/2</link>
    <guid>post-2</guid>
    <pubDate>Thu, 01 Jan 2026 09:00:00 GMT</pubDate>
    <description>second body</description>
  </item>
  <item>
    <title>Oldest post</title>
    <link>http://127.0.0.1/posts/1</link>
    <guid>post-1</guid>
    <pubDate>Wed, 31 Dec 2025 09:00:00 GMT</pubDate>
    <description>first body</description>
  </item>
</channel></rss>
"""

_ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Local Atom Feed</title>
  <id>urn:uuid:feed</id>
  <updated>2026-03-02T10:00:00Z</updated>
  <entry>
    <title>Atom entry B</title>
    <id>urn:uuid:b</id>
    <link href="http://127.0.0.1/atom/b"/>
    <updated>2026-03-02T10:00:00Z</updated>
    <summary>entry b</summary>
  </entry>
  <entry>
    <title>Atom entry A</title>
    <id>urn:uuid:a</id>
    <link href="http://127.0.0.1/atom/a"/>
    <updated>2026-03-01T10:00:00Z</updated>
    <summary>entry a</summary>
  </entry>
</feed>
"""


class _FeedHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        routes = {
            "/feed.rss": (_RSS, "application/rss+xml"),
            "/feed.atom": (_ATOM, "application/atom+xml"),
            "/not-a-feed": (b"<html><body>hello</body></html>", "text/html"),
        }
        if self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", "/feed.rss")
            self.end_headers()
            return
        body, ctype = routes.get(self.path, (b"", ""))
        if not body:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def feed_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    from app.core.config import get_settings

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FeedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "127.0.0.1")
    get_settings.cache_clear()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        get_settings.cache_clear()


def _config(conn: dict[str, Any]) -> SourceConfig:
    return SourceConfig(
        source_id="src-rss",
        tenant_id="t-rss",
        name="feed",
        family=SourceFamily.WEB,
        source_type="rss",
        connection_config=conn,
    )


async def _collect(config: SourceConfig, cursor: str | None) -> list[tuple[Any, str]]:
    return [pair async for pair in RSSConnector().get_delta(config, cursor)]


async def test_validate_connection_against_a_real_feed(feed_server: str) -> None:
    health = await RSSConnector().validate_connection(_config({"url": f"{feed_server}/feed.rss"}))

    assert health.ok, health.error
    assert health.metadata == {"title": "Local Test Feed", "entries": 3}


async def test_validate_follows_a_redirect_through_the_guard(feed_server: str) -> None:
    health = await RSSConnector().validate_connection(_config({"url": f"{feed_server}/moved"}))

    assert health.ok, health.error


async def test_validate_rejects_a_page_that_is_not_a_feed(feed_server: str) -> None:
    health = await RSSConnector().validate_connection(
        _config({"url": f"{feed_server}/not-a-feed"})
    )

    assert health.ok is False
    assert "feed" in health.error.lower()


async def test_validate_reports_http_errors(feed_server: str) -> None:
    health = await RSSConnector().validate_connection(_config({"url": f"{feed_server}/missing"}))

    assert health.ok is False
    assert "404" in health.error


async def test_sync_yields_every_entry_oldest_first_with_iso_cursor(feed_server: str) -> None:
    docs = await _collect(_config({"url": f"{feed_server}/feed.rss"}), None)

    titles = [d.metadata["title"] for d, _ in docs]
    assert titles == ["Oldest post", "Middle post", "Newest post"]
    cursors = [c for _, c in docs]
    assert cursors == sorted(cursors)
    assert cursors[-1] == "2026-01-02T09:00:00+00:00"
    first = docs[0][0]
    assert b"first body" in first.content
    assert first.source_url.endswith("/posts/1")
    assert first.tenant_id == "t-rss"


async def test_incremental_sync_uses_dates_not_weekday_strings(feed_server: str) -> None:
    config = _config({"url": f"{feed_server}/feed.rss"})

    # A cursor at the oldest entry: "Thu, 01 Jan 2026" sorts before
    # "Wed, 31 Dec 2025" as a string, so a string compare dropped the new posts.
    docs = await _collect(config, "2025-12-31T09:00:00+00:00")

    assert [d.metadata["title"] for d, _ in docs] == ["Middle post", "Newest post"]
    assert await _collect(config, docs[-1][1]) == []


async def test_doc_ids_are_stable_across_syncs(feed_server: str) -> None:
    config = _config({"url": f"{feed_server}/feed.rss"})

    first = [d.doc_id for d, _ in await _collect(config, None)]
    second = [d.doc_id for d, _ in await _collect(config, None)]

    assert first == second
    assert len(set(first)) == 3


async def test_atom_feed(feed_server: str) -> None:
    docs = await _collect(_config({"url": f"{feed_server}/feed.atom"}), None)

    assert [d.metadata["title"] for d, _ in docs] == ["Atom entry A", "Atom entry B"]
    assert docs[-1][1] == "2026-03-02T10:00:00+00:00"


async def test_max_entries_keeps_the_oldest_unseen_entries(feed_server: str) -> None:
    docs = await _collect(_config({"url": f"{feed_server}/feed.rss", "max_entries": 2}), None)

    # Oldest first, so the next sync resumes from the cursor and picks up the rest.
    assert [d.metadata["title"] for d, _ in docs] == ["Oldest post", "Middle post"]


async def test_feed_url_from_older_ui_payloads_is_accepted(feed_server: str) -> None:
    # The Sources UI used to send the feed as ``urls`` (textarea) / ``feed_url``.
    for conn in (
        {"feed_url": f"{feed_server}/feed.rss"},
        {"urls": f"{feed_server}/feed.rss\n"},
    ):
        health = await RSSConnector().validate_connection(_config(conn))
        assert health.ok, (conn, health.error)


async def test_internal_url_is_still_refused_without_the_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "false")
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "")
    get_settings.cache_clear()
    try:
        health = await RSSConnector().validate_connection(
            _config({"url": "http://127.0.0.1:9/feed.rss"})
        )
        assert health.ok is False
        assert "SSRF" in health.error
        with pytest.raises(Exception, match="SSRF"):
            await _collect(_config({"url": "http://127.0.0.1:9/feed.rss"}), None)
    finally:
        get_settings.cache_clear()


async def test_missing_feedparser_fails_the_sync_loudly(
    feed_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "feedparser", None)
    config = _config({"url": f"{feed_server}/feed.rss"})

    health = await RSSConnector().validate_connection(config)
    assert health.ok is False
    assert "feedparser" in health.error

    # An empty generator would be recorded as a successful, empty sync.
    with pytest.raises(ConnectorUnavailableError, match="feedparser"):
        await _collect(config, None)


def test_feedparser_is_a_core_dependency() -> None:
    """The production image installs without the dev group — feedparser must be core."""
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[3] / "pyproject.toml").read_text()
    )
    core = [d.split(">")[0].split("=")[0].strip() for d in pyproject["project"]["dependencies"]]
    assert "feedparser" in core
