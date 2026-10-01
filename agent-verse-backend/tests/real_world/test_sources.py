"""KB-RSS, KB-RSS-LOCAL and the Redis / MongoDB source connectors.

KB-RSS ingests a public feed (container egress permitting) and asserts items are
searchable. KB-RSS-LOCAL serves a feed from a tiny HTTP server on this host and
points the connector at host.docker.internal: the stack's connector SSRF guard
must refuse it (internal sources are off unless the operator allowlists them),
and the refusal must be surfaced, not swallowed.

SRC-REDIS / SRC-MONGO-SYNC: the connectors must be registered. Against a
private address the connector egress guard must refuse with a visible reason;
with RW_REDIS_URL / RW_MONGO_URI (a source the stack may reach, e.g. an
operator-allowlisted host) real documents are ingested and searched. Redis keys
can be seeded through ``docker exec <RW_REDIS_SEED_CONTAINER> redis-cli`` (db
from the URL, keys ``rw:doc:*``, deleted afterwards); MongoDB must already hold
RW_MONGO_DB.RW_MONGO_COLLECTION documents containing RW_MONGO_FACT.
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
from tests.real_world.helpers import LiveAPI, mask, tag

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
    evidence["sync"] = {k: status.get(k) for k in ("status", "docs_discovered", "docs_indexed",
                                                   "docs_failed", "chunks_created",
                                                   "error_message")} or result
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
    evidence["sync"] = mask({k: status.get(k) for k in ("status", "error_message",
                                                        "docs_indexed")})
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


_REFUSAL_WORDS = ("ssrf", "egress", "blocked", "private", "internal", "not allowed",
                  "refused", "allow-list", "allowlist")


def _assert_guarded(api: LiveAPI, source_id: str, evidence: dict[str, Any]) -> None:
    """A private target must be refused with a reason the operator can see."""
    health = api.get(f"/sources/{source_id}/health")
    evidence["health"] = {"http": health.status_code, "body": mask(health.text[:300])}
    text = health.text.lower()
    if health.status_code == 200 and health.json().get("ok") is False and any(
            w in text for w in _REFUSAL_WORDS):
        return
    sync = api.post(f"/sources/{source_id}/sync")
    evidence["sync_http"] = sync.status_code
    if sync.status_code == 422:
        text += sync.text.lower()
    else:
        status = srcs.sync_and_wait(api, source_id, timeout=120).get("status") or {}
        evidence["sync"] = mask({k: status.get(k) for k in ("status", "error_message")})
        text += mask(status).lower()
    assert any(w in text for w in _REFUSAL_WORDS), (
        f"private source address was neither refused nor explained: {mask(text)[:400]}"
    )


def _redis_cli(container: str, db: str, *args: str) -> None:
    import subprocess

    subprocess.run(["docker", "exec", container, "redis-cli", "-n", db, *args],
                   check=True, capture_output=True, timeout=30)


@pytest.mark.scenario("SRC-REDIS")
def test_redis_source_connector(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    types = _catalogue(api)
    evidence["catalogue_has_redis"] = "redis" in types
    assert "redis" in types, "no 'redis' source connector in GET /sources/catalogue"
    cid = srcs.create_collection(api, cleanup, "rw-redis")
    url = os.getenv("RW_REDIS_URL", "")
    if not url:
        src = srcs.create_source(api, cleanup, family="nosql_database", source_type="redis",
                                 config={"uri": "redis://10.255.0.10:6379/0",
                                         "key_patterns": "rw:doc:*"}, collection_id=cid)
        evidence["source_id"] = src["id"]
        evidence["mode"] = "egress-guard (set RW_REDIS_URL for real ingestion)"
        _assert_guarded(api, src["id"], evidence)
        return

    container = os.getenv("RW_REDIS_SEED_CONTAINER", "")
    db = url.rstrip("/").rsplit("/", 1)[-1] if url.count("/") >= 3 else "0"
    key = f"rw:doc:{tag()}"
    fact = "The Heron data-retention review is scheduled for 9 December 2026."
    if container:
        _redis_cli(container, db, "SET", key, fact)
    try:
        src = srcs.create_source(api, cleanup, family="nosql_database", source_type="redis",
                                 config={"uri": url, "key_patterns": "rw:doc:*"},
                                 collection_id=cid)
        evidence["source_id"] = src["id"]
        health = api.get(f"/sources/{src['id']}/health")
        evidence["health"] = {"http": health.status_code, "body": mask(health.text[:300])}
        assert health.status_code == 200 and health.json().get("ok") is True, mask(health.text)
        status = srcs.sync_and_wait(api, src["id"]).get("status") or {}
        evidence["sync"] = mask({k: status.get(k) for k in ("status", "docs_indexed",
                                                            "error_message")})
        assert str(status.get("status")) == "completed", evidence["sync"]
        if container:
            hits = srcs.search(api, cid, "When is the Heron data-retention review?")
            evidence["top_hit"] = str((hits[0] if hits else {}).get("content", ""))[:160]
            assert hits and "Heron" in str(hits[0].get("content", "")), "seeded key not searchable"
    finally:
        if container:
            _redis_cli(container, db, "DEL", key)


@pytest.mark.scenario("SRC-MONGO-SYNC")
def test_mongodb_source_sync(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    types = _catalogue(api)
    evidence["catalogue_has_mongodb"] = "mongodb" in types
    assert "mongodb" in types, "no 'mongodb' source connector in GET /sources/catalogue"
    cid = srcs.create_collection(api, cleanup, "rw-mongo")
    uri = os.getenv("RW_MONGO_URI", "")
    database = os.getenv("RW_MONGO_DB", "rw_demo")
    collection = os.getenv("RW_MONGO_COLLECTION", "articles")
    if not uri:
        src = srcs.create_source(api, cleanup, family="nosql_database", source_type="mongodb",
                                 config={"uri": "mongodb://10.255.0.11:27017",
                                         "database": database, "collections": [collection]},
                                 collection_id=cid)
        evidence["source_id"] = src["id"]
        evidence["mode"] = "egress-guard (set RW_MONGO_URI for real ingestion)"
        _assert_guarded(api, src["id"], evidence)
        return
    src = srcs.create_source(api, cleanup, family="nosql_database", source_type="mongodb",
                             config={"uri": uri, "database": database,
                                     "collections": [collection]}, collection_id=cid)
    evidence["source_id"] = src["id"]
    status = srcs.sync_and_wait(api, src["id"]).get("status") or {}
    evidence["sync"] = mask({k: status.get(k) for k in ("status", "docs_indexed",
                                                        "error_message")})
    assert str(status.get("status")) == "completed", evidence["sync"]
    assert int(status.get("docs_indexed") or 0) > 0, "mongodb sync indexed nothing"
    fact = os.getenv("RW_MONGO_FACT", "")
    if fact:
        hits = srcs.search(api, cid, fact)
        assert hits and fact.split()[0] in str(hits[0].get("content", "")), "fact not searchable"
