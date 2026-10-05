"""KB-SOURCES-SYNC: upstream changes between two syncs of a knowledge source.

For every source the same story: sync 1 indexes three items; upstream then keeps
one unchanged, edits one, deletes one and adds one; sync 2 must leave exactly three
documents — the unchanged one under the SAME document id, the edited one replaced
(new text served, old text gone), the deleted one removed, the new one indexed.

* RSS (KB-SOURCES-SYNC-RSS): the feed is served by the local fixture server, so it
  needs a URL the stack's egress guard accepts — RW_FIXTURE_PUBLIC_URL (a tunnel to
  RW_FIXTURE_PORT), or RW_FIXTURE_REACHABLE=1 when the operator allowlisted the host.
* Redis (SRC-REDIS-INCREMENTAL): RW_REDIS_URL (as the stack reaches it) + a seeding
  path: RW_REDIS_SEED_URL (as this host reaches it; default RW_REDIS_URL).
* MongoDB (SRC-MONGO-INCREMENTAL): RW_MONGO_URI (+ RW_MONGO_SEED_URI); incremental
  cursor on ``updated_at``.
* S3 / MinIO (SRC-S3-INCREMENTAL): RW_S3_ENDPOINT, RW_S3_BUCKET, RW_S3_ACCESS_KEY,
  RW_S3_SECRET_KEY (+ RW_S3_SEED_ENDPOINT, RW_S3_SOURCE_TYPE=minio|s3, RW_S3_REGION).
  Objects deleted upstream are removed by reconciliation, which syncs schedule at most
  once per INGESTION_RECONCILE_INTERVAL_SECONDS (KB-44, default a day) — so the test
  triggers it explicitly (POST /sources/{id}/reconcile) after sync 2 and waits for it.

Without its env a source scenario is SKIPPED with the exact variables it needs.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable
from typing import Any

import pytest

from tests.real_world import kb
from tests.real_world import sources as srcs
from tests.real_world.fixture_server import FixtureServer, rss_feed
from tests.real_world.helpers import (
    LiveAPI,
    mask,
    register_secret,
    require_env,
    tag,
    wait_until,
)
from tests.real_world.metrics import norm

COMPLETED = {"completed", "complete", "succeeded", "success"}

ITEMS_V1 = {
    "alpha": "The Osprey yard at Hosur switches to night loading from 5 October.",
    "bravo": "Dock 7 at the Pune hub is closed for resurfacing until 9 October.",
    "charlie": "The Nagpur cross-dock pilots RFID gate scanning in week 41.",
}
EDITED_BRAVO = "Dock 7 at the Pune hub reopens early on 6 October after resurfacing."
NEW_DELTA = "Guwahati inbound cut-off moves to 18:00 for the Durga Puja peak."


def _family(api: LiveAPI, source_type: str) -> str:
    for c in api.json_ok("GET", "/sources/catalogue"):
        if str(c.get("source_type")) == source_type:
            return str(c.get("family") or "")
    pytest.fail(f"no {source_type!r} connector in GET /sources/catalogue")


def _create(api: LiveAPI, cleanup: Any, source_type: str, config: dict[str, Any], cid: str,
            sync_mode: str) -> str:
    resp = api.post("/sources", json={
        "name": f"rw-{source_type}-sync-{tag()}", "family": _family(api, source_type),
        "source_type": source_type, "connection_config": config, "collection_id": cid,
        "sync_mode": sync_mode, "sync_interval_seconds": 86400, "pii_action": "none",
        "min_quality_score": 0.0,
    })
    assert resp.status_code in (200, 201), (
        f"POST /sources ({source_type}) -> {resp.status_code}: {mask(resp.text[:300])}"
    )
    sid = str(resp.json().get("source_id") or resp.json().get("id"))
    cleanup("DELETE", f"/sources/{sid}")
    return sid


def _sync(api: LiveAPI, sid: str, evidence: dict[str, Any], label: str) -> dict[str, Any]:
    result = srcs.sync_and_wait(api, sid, timeout=300)
    status = result.get("status") or {}
    evidence[label] = mask({k: status.get(k) for k in (
        "status", "docs_discovered", "docs_indexed", "docs_skipped", "docs_failed",
        "docs_deleted", "error_message")})
    assert str(status.get("status", "")).lower() in COMPLETED, (
        f"{label} did not complete: {evidence[label]}"
    )
    return dict(status)


def _reconcile(api: LiveAPI, sid: str, cid: str, gone_ids: set[str],
               evidence: dict[str, Any]) -> None:
    """Run upstream-deletion reconciliation now and wait until ``gone_ids`` are removed.

    Syncs only schedule it once per INGESTION_RECONCILE_INTERVAL_SECONDS (KB-44).
    """
    resp = api.post(f"/sources/{sid}/reconcile")
    assert resp.status_code == 202, (
        f"POST /sources/{sid}/reconcile -> {resp.status_code}: {mask(resp.text[:300])}"
    )
    evidence["reconcile"] = resp.json().get("status")
    assert evidence["reconcile"] in ("queued", "already_queued"), evidence["reconcile"]
    wait_until(
        lambda: {str(d.get("id")) for d in kb.all_documents(api, cid)[0]},
        timeout=180, interval=4, desc=f"reconciliation of source {sid}",
        done=lambda ids: not (ids & gone_ids),
    )


def _docs_by_marker(api: LiveAPI, cid: str, markers: dict[str, str]
                    ) -> dict[str, list[dict[str, Any]]]:
    """Documents of the collection grouped by which marker text they carry."""
    docs, _ = kb.all_documents(api, cid)
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in markers}
    for d in docs:
        blob = norm(f"{d.get('title')} {d.get('preview')} {d.get('source')}")
        for k, marker in markers.items():
            if norm(marker) in blob:
                out[k].append(d)
    out["_all"] = docs
    return out


def _served(api: LiveAPI, cid: str, text: str) -> bool:
    words = " ".join(text.split()[:9])
    return any(norm(text[:50]) in norm(h.get("content")) for h in kb.search(api, cid, words, 5))


def _assert_second_sync(api: LiveAPI, cid: str, before: dict[str, list[dict[str, Any]]],
                        evidence: dict[str, Any], key: Callable[[str], str]) -> None:
    """Shared assertions after the upstream change + second sync."""
    soft: list[str] = []
    after_docs, total = kb.all_documents(api, cid)
    evidence["documents_after"] = len(after_docs)
    if len(after_docs) != 3:
        soft.append(f"{len(after_docs)} documents after sync 2, expected 3 "
                    "(1 unchanged + 1 edited + 1 new; the deleted one removed)")
    ids_before = {k: {str(d.get("id")) for d in v} for k, v in before.items() if k != "_all"}
    ids_after = {str(d.get("id")) for d in after_docs}
    evidence["ids_before"] = {k: sorted(v) for k, v in ids_before.items()}
    if not ids_before["alpha"] or not ids_before["alpha"] <= ids_after:
        soft.append("the unchanged item does not keep its document id across syncs")
    if not _served(api, cid, key("bravo_new")):
        soft.append("the edited item's new text is not served")
    if _served(api, cid, key("bravo_old")):
        soft.append("the edited item's OLD text is still served (not replaced)")
    if ids_before["charlie"] & ids_after or _served(api, cid, key("charlie")):
        soft.append("the upstream-deleted item is still indexed")
    if not _served(api, cid, key("delta")):
        soft.append("the new upstream item was not indexed")
    assert not soft, "; ".join(soft)


def _key(k: str) -> str:
    return {"bravo_new": EDITED_BRAVO, "bravo_old": ITEMS_V1["bravo"],
            "charlie": ITEMS_V1["charlie"], "delta": NEW_DELTA}[k]


# ── RSS ──────────────────────────────────────────────────────────────────────


def _feed(items: dict[str, str], dates: dict[str, str]) -> str:
    return rss_feed([{"guid": f"rw-{k}", "title": f"Bulletin {k}: {v[:40]}",
                      "description": v, "pubDate": dates[k]} for k, v in items.items()])


@pytest.mark.scenario("KB-SOURCES-SYNC-RSS")
def test_rss_feed_changes_between_syncs(api: LiveAPI, cleanup: Any,
                                        fixture_server: FixtureServer,
                                        evidence: dict[str, Any]) -> None:
    if not (FixtureServer.is_public() or os.getenv("RW_FIXTURE_REACHABLE") == "1"):
        pytest.skip("needs RW_FIXTURE_PUBLIC_URL (a tunnel to the local fixture server; the "
                    "stack's connector egress guard refuses host.docker.internal) or "
                    "RW_FIXTURE_REACHABLE=1 when the operator allowlisted the fixture host")
    k = tag()
    dates = {"alpha": "Mon, 28 Sep 2026 08:00:00 GMT", "bravo": "Tue, 29 Sep 2026 08:00:00 GMT",
             "charlie": "Wed, 30 Sep 2026 08:00:00 GMT"}
    fixture_server.set_feed(k, _feed(ITEMS_V1, dates))
    url = f"{fixture_server.public_base}/feed/{k}.xml"
    cid = srcs.create_collection(api, cleanup, "rw-rss-sync")
    sid = _create(api, cleanup, "rss", {"url": url}, cid,
                  os.getenv("RW_RSS_SYNC_MODE", "full"))
    evidence.update(collection_id=cid, source_id=sid, feed=url)
    _sync(api, sid, evidence, "sync1")
    before = _docs_by_marker(api, cid, {k2: v[:30] for k2, v in ITEMS_V1.items()})
    evidence["documents_before"] = len(before["_all"])
    assert len(before["_all"]) == 3, f"sync 1 indexed {len(before['_all'])} items, expected 3"
    v2 = {"alpha": ITEMS_V1["alpha"], "bravo": EDITED_BRAVO, "delta": NEW_DELTA}
    fixture_server.set_feed(k, _feed(v2, {"alpha": dates["alpha"],
                                          "bravo": "Thu, 01 Oct 2026 09:30:00 GMT",
                                          "delta": "Fri, 02 Oct 2026 07:00:00 GMT"}))
    _sync(api, sid, evidence, "sync2")
    assert fixture_server.count("GET", f"/feed/{k}.xml") >= 2, "the stack never re-fetched"
    _assert_second_sync(api, cid, before, evidence, _key)


# ── Redis ────────────────────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-REDIS-INCREMENTAL")
def test_redis_source_incremental(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    env = require_env("RW_REDIS_URL", why="a Redis the stack can reach (egress-allowlisted)")
    import redis

    seed = redis.Redis.from_url(os.getenv("RW_REDIS_SEED_URL") or env["RW_REDIS_URL"])
    prefix = f"rw:sync:{tag()}"
    try:
        for k2, v in ITEMS_V1.items():
            seed.set(f"{prefix}:{k2}", v)
        cid = srcs.create_collection(api, cleanup, "rw-redis-sync")
        sid = _create(api, cleanup, "redis", {"uri": env["RW_REDIS_URL"],
                                              "key_patterns": f"{prefix}:*"}, cid,
                      "incremental")
        evidence.update(collection_id=cid, source_id=sid)
        _sync(api, sid, evidence, "sync1")
        before = _docs_by_marker(api, cid, {k2: v[:30] for k2, v in ITEMS_V1.items()})
        assert len(before["_all"]) == 3, f"sync 1 indexed {len(before['_all'])} keys"
        seed.set(f"{prefix}:bravo", EDITED_BRAVO)
        seed.delete(f"{prefix}:charlie")
        seed.set(f"{prefix}:delta", NEW_DELTA)
        _sync(api, sid, evidence, "sync2")
        _assert_second_sync(api, cid, before, evidence, _key)
    finally:
        for k2 in [*ITEMS_V1, "delta"]:
            with contextlib.suppress(Exception):
                seed.delete(f"{prefix}:{k2}")


# ── MongoDB ──────────────────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-MONGO-INCREMENTAL")
def test_mongo_source_incremental(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    env = require_env("RW_MONGO_URI", why="a MongoDB the stack can reach (egress-allowlisted)")
    register_secret(env["RW_MONGO_URI"])
    from datetime import UTC, datetime, timedelta

    import pymongo

    client: Any = pymongo.MongoClient(os.getenv("RW_MONGO_SEED_URI") or env["RW_MONGO_URI"],
                                      serverSelectionTimeoutMS=8000)
    dbname, coll = os.getenv("RW_MONGO_DB", "rw_demo"), f"rw_sync_{tag()}"
    col = client[dbname][coll]
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    try:
        col.insert_many([{"_id": k2, "body": v, "updated_at": t0} for k2, v in ITEMS_V1.items()])
        cid = srcs.create_collection(api, cleanup, "rw-mongo-sync")
        sid = _create(api, cleanup, "mongodb", {"uri": env["RW_MONGO_URI"], "database": dbname,
                                                "collections": [coll],
                                                "cursor_field": "updated_at"},
                      cid, "incremental")
        evidence.update(collection_id=cid, source_id=sid, mongo_collection=coll)
        _sync(api, sid, evidence, "sync1")
        before = _docs_by_marker(api, cid, {k2: v[:30] for k2, v in ITEMS_V1.items()})
        assert len(before["_all"]) == 3, f"sync 1 indexed {len(before['_all'])} documents"
        later = t0 + timedelta(hours=2)
        col.update_one({"_id": "bravo"}, {"$set": {"body": EDITED_BRAVO, "updated_at": later}})
        col.delete_one({"_id": "charlie"})
        col.insert_one({"_id": "delta", "body": NEW_DELTA, "updated_at": later})
        _sync(api, sid, evidence, "sync2")
        _assert_second_sync(api, cid, before, evidence, _key)
    finally:
        with contextlib.suppress(Exception):
            col.drop()
        client.close()


# ── S3 / MinIO ───────────────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-S3-INCREMENTAL")
def test_s3_source_incremental(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    env = require_env("RW_S3_ENDPOINT", "RW_S3_BUCKET", "RW_S3_ACCESS_KEY", "RW_S3_SECRET_KEY",
                      why="an S3/MinIO bucket the stack can reach (egress-allowlisted)")
    register_secret(env["RW_S3_SECRET_KEY"])
    register_secret(env["RW_S3_ACCESS_KEY"])
    import boto3

    region = os.getenv("RW_S3_REGION", "us-east-1")
    s3: Any = boto3.client("s3", endpoint_url=os.getenv("RW_S3_SEED_ENDPOINT")
                           or env["RW_S3_ENDPOINT"], region_name=region,
                           aws_access_key_id=env["RW_S3_ACCESS_KEY"],
                           aws_secret_access_key=env["RW_S3_SECRET_KEY"])
    bucket, prefix = env["RW_S3_BUCKET"], f"rw-sync-{tag()}/"
    try:
        for k2, v in ITEMS_V1.items():
            s3.put_object(Bucket=bucket, Key=f"{prefix}{k2}.txt", Body=v.encode())
        cid = srcs.create_collection(api, cleanup, "rw-s3-sync")
        stype = os.getenv("RW_S3_SOURCE_TYPE", "minio")
        sid = _create(api, cleanup, stype, {
            "endpoint_url": env["RW_S3_ENDPOINT"], "bucket": bucket, "prefix": prefix,
            "region": region, "credentials": {"access_key_id": env["RW_S3_ACCESS_KEY"],
                                              "secret_access_key": env["RW_S3_SECRET_KEY"]},
        }, cid, "incremental")
        evidence.update(collection_id=cid, source_id=sid, prefix=prefix)
        _sync(api, sid, evidence, "sync1")
        before = _docs_by_marker(api, cid, {k2: v[:30] for k2, v in ITEMS_V1.items()})
        assert len(before["_all"]) == 3, f"sync 1 indexed {len(before['_all'])} objects"
        s3.put_object(Bucket=bucket, Key=f"{prefix}bravo.txt", Body=EDITED_BRAVO.encode())
        s3.delete_object(Bucket=bucket, Key=f"{prefix}charlie.txt")
        s3.put_object(Bucket=bucket, Key=f"{prefix}delta.txt", Body=NEW_DELTA.encode())
        _sync(api, sid, evidence, "sync2")
        _reconcile(api, sid, cid, {str(d.get("id")) for d in before["charlie"]}, evidence)
        _assert_second_sync(api, cid, before, evidence, _key)
    finally:
        for k2 in [*ITEMS_V1, "delta"]:
            with contextlib.suppress(Exception):
                s3.delete_object(Bucket=bucket, Key=f"{prefix}{k2}.txt")
