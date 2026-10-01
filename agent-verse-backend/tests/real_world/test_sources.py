"""KB-RSS, KB-RSS-LOCAL and the Redis / MongoDB source connectors.

KB-RSS ingests a public feed (container egress permitting) and asserts items are
searchable. KB-RSS-LOCAL serves a feed from a tiny HTTP server on this host and
points the connector at host.docker.internal: the stack's connector SSRF guard
must refuse it (internal sources are off unless the operator allowlists them),
and the refusal must be surfaced, not swallowed.

Redis / MongoDB sync depend on wave-12 fixes still being merged; they are
xfail(strict=False) until then and use RW_REDIS_URL / RW_MONGO_URI when given.
"""

from __future__ import annotations

import http.server
import os
import socket
import threading
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from tests.real_world import sources as srcs
from tests.real_world.helpers import LiveAPI, mask

RSS_URL = os.getenv("RW_RSS_URL", "https://github.com/python/cpython/releases.atom")
FEED_HOST = os.getenv("RW_FEED_HOST", "host.docker.internal")

LOCAL_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>RW Ops Bulletin</title>
<link>http://example.invalid/</link><description>real-world suite feed</description>
<item><title>Kestrel cluster maintenance moved to Thursday</title>
<link>http://example.invalid/kestrel</link><guid>rw-kestrel-1</guid>
<pubDate>Wed, 30 Sep 2026 10:00:00 GMT</pubDate>
<description>The Kestrel cluster maintenance window moves to Thursday 22:00 IST.</description>
</item></channel></rss>"""


def _feed_entries() -> list[dict[str, str]]:
    import feedparser

    raw = httpx.get(RSS_URL, timeout=20, follow_redirects=True).text
    return [{"title": e.get("title", ""), "link": e.get("link", "")}
            for e in feedparser.parse(raw).entries if e.get("title")]


@pytest.mark.scenario("KB-RSS")
def test_kb_rss_public_feed(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    try:
        entries = _feed_entries()
    except Exception as exc:
        pytest.skip(f"feed {RSS_URL} not reachable from this host: {exc}")
    assert entries, f"feed {RSS_URL} has no entries"
    evidence["feed"] = RSS_URL
    evidence["feed_entries"] = len(entries)
    cid = srcs.create_collection(api, cleanup, "rw-rss")
    evidence["collection_id"] = cid
    stype = "atom" if RSS_URL.endswith(".atom") else "rss"
    src = srcs.create_source(api, cleanup, family="web", source_type=stype,
                          config={"url": RSS_URL}, collection_id=cid)
    evidence["source_id"] = src["id"]
    health = api.get(f"/sources/{src['id']}/health")
    evidence["health"] = {"http": health.status_code, "body": mask(health.text[:300])}
    assert health.status_code == 200 and health.json().get("ok", health.json().get(
        "healthy", True)) is not False, f"source health: {mask(health.text[:300])}"

    result = srcs.sync_and_wait(api, src["id"])
    status = result.get("status") or {}
    evidence["sync"] = {k: status.get(k) for k in ("status", "documents_processed",
                                                   "documents_failed", "error",
                                                   "chunks_created")} or result
    assert str(status.get("status", "")).lower() in ("completed", "complete", "succeeded",
                                                     "success"), f"sync: {mask(result)[:600]}"
    docs = srcs.documents(api, cid)
    evidence["documents"] = len(docs)
    assert len(docs) >= min(3, len(entries)), f"only {len(docs)} feed items ingested"

    probe = entries[0]["title"]
    hits = srcs.search(api, cid, probe)
    evidence["search_probe"] = probe
    evidence["top_hit"] = str((hits[0] if hits else {}).get("content", ""))[:160]
    words = [w for w in probe.split() if len(w) > 2]
    assert hits and all(w.lower() in str(hits[0].get("content", "")).lower()
                        for w in words[:4]), f"feed item {probe!r} not searchable"


@pytest.fixture
def local_feed() -> Iterator[str]:
    body = LOCAL_FEED.encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            return

    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        port = s.getsockname()[1]
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{FEED_HOST}:{port}/feed.xml"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.scenario("KB-RSS-LOCAL")
def test_kb_rss_local_feed_is_guarded(api: LiveAPI, cleanup: Any, local_feed: str,
                                      evidence: dict[str, Any]) -> None:
    """Internal feed: refused by the egress guard (by design) with a visible error,
    or — if the operator allowlisted it — ingested and searchable."""
    evidence["feed"] = local_feed
    cid = srcs.create_collection(api, cleanup, "rw-rss-local")
    src = srcs.create_source(api, cleanup, family="web", source_type="rss",
                          config={"url": local_feed}, collection_id=cid)
    evidence["source_id"] = src["id"]
    health = api.get(f"/sources/{src['id']}/health")
    evidence["health"] = {"http": health.status_code, "body": mask(health.text[:300])}
    result = srcs.sync_and_wait(api, src["id"], timeout=120)
    status = result.get("status") or {}
    evidence["sync"] = mask({k: status.get(k) for k in ("status", "error",
                                                        "documents_processed")})
    docs = srcs.documents(api, cid)
    evidence["documents"] = len(docs)
    if docs:  # operator allowlisted internal sources: must then be searchable
        hits = srcs.search(api, cid, "Kestrel cluster maintenance Thursday")
        assert hits and "Kestrel" in str(hits[0].get("content", ""))
        return
    text = (health.text + " " + mask(status)).lower()
    assert any(k in text for k in ("ssrf", "egress", "blocked", "private", "internal",
                                   "not allowed")), (
        f"internal feed neither ingested nor refused with a visible reason: "
        f"health={mask(health.text[:200])} sync={mask(status)[:300]}"
    )


def _catalogue(api: LiveAPI) -> set[str]:
    return {str(c.get("source_type")) for c in api.json_ok("GET", "/sources/catalogue")}


@pytest.mark.scenario("SRC-REDIS")
@pytest.mark.xfail(strict=False, reason="pending wave12 merge (SRC-REDIS)")
def test_redis_source_connector(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    types = _catalogue(api)
    evidence["catalogue_has_redis"] = "redis" in types
    assert "redis" in types, "no 'redis' source connector in GET /sources/catalogue"
    url = os.getenv("RW_REDIS_URL", "")
    if not url:
        pytest.skip("connector exists; set RW_REDIS_URL to a Redis reachable from the stack")
    cid = srcs.create_collection(api, cleanup, "rw-redis")
    src = srcs.create_source(api, cleanup, family="nosql_database", source_type="redis",
                          config={"url": url, "key_pattern": os.getenv("RW_REDIS_KEYS",
                                                                       "rw:doc:*")},
                          collection_id=cid)
    evidence["source_id"] = src["id"]
    result = srcs.sync_and_wait(api, src["id"])
    evidence["sync"] = mask(result)[:400]
    assert srcs.documents(api, cid), "redis sync ingested nothing"


@pytest.mark.scenario("SRC-MONGO-SYNC")
@pytest.mark.xfail(strict=False, reason="pending wave12 merge (SRC-MONGO-SYNC)")
def test_mongodb_source_sync(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    types = _catalogue(api)
    evidence["catalogue_has_mongodb"] = "mongodb" in types
    assert "mongodb" in types, "no 'mongodb' source connector in GET /sources/catalogue"
    uri = os.getenv("RW_MONGO_URI", "")
    if not uri:
        pytest.skip("connector exists; set RW_MONGO_URI to a MongoDB reachable from the stack")
    cid = srcs.create_collection(api, cleanup, "rw-mongo")
    src = srcs.create_source(api, cleanup, family="nosql_database", source_type="mongodb",
                          config={"uri": uri,
                                  "database": os.getenv("RW_MONGO_DB", "rw_demo"),
                                  "collection": os.getenv("RW_MONGO_COLLECTION", "articles")},
                          collection_id=cid)
    evidence["source_id"] = src["id"]
    result = srcs.sync_and_wait(api, src["id"])
    evidence["sync"] = mask(result)[:400]
    assert srcs.documents(api, cid), "mongodb sync ingested nothing"
