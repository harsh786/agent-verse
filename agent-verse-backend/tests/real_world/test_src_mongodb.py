"""SRC-MONGO-*: MongoDB as a knowledge source on the live stack (P1c / A5).

Throwaway servers on the compose network, operator-allowlisted for the connector
egress guard (labelled ``p1c=live-test``):

* ``rw-mongo`` (alias ``rw-mongo-alt``): a replica set ``rs0`` with keyFile auth —
  change streams work; the reader ``rwreader`` has ``read`` on ``rw_p1c`` only.
* ``rw-mongo-tls``: a standalone server with ``--tlsMode requireTLS`` (certificate
  from a private test CA); no change streams there, so it runs the timestamp cursor.
* ``rw-mongo-stall``: accepts TCP and never answers (a hung mongod).

Seeding runs from this host through the published ports as ``rwroot``.

Environment (else SKIPPED): ``RW_MONGO_ROOT_PASSWORD``, ``RW_MONGO_READER_PASSWORD``
(+ ``RW_MONGO_SEED_PORT`` 57017, ``RW_MONGO_TLS_SEED_PORT`` 57018, ``RW_TLS_DIR`` with
``ca.pem`` / ``other-ca.pem``; ``RW_PG_CONTAINER`` for SRC-MONGO-D2).
SRC-MONGO-KILL-SWITCH runs only with ``RW_MONGO_KILL_SWITCH=off`` (the stack then runs
with ``INGESTION_CONNECTOR_MONGODB_ENABLED=false``).
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from tests.real_world import kb
from tests.real_world import mongo_seed as ms
from tests.real_world import source_jobs as sj
from tests.real_world import sources as srcs
from tests.real_world.helpers import LiveAPI, mask, register_secret, tag, wait_until
from tests.real_world.metrics import norm, record

FAMILY = "nosql_database"
DB = "rw_p1c"
RS_URI = "mongodb://rw-mongo:27017/?replicaSet=rs0"
ORDERS, CUSTOMERS, PRODUCTS = (int(os.getenv("RW_MONGO_ORDERS", "160")),
                               int(os.getenv("RW_MONGO_CUSTOMERS", "30")),
                               int(os.getenv("RW_MONGO_PRODUCTS", "40")))


def _env(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        pytest.skip(f"needs {name}: the throwaway MongoDB servers (see module docstring)")
    register_secret(value)
    return value


def _seed_client(port_var: str = "RW_MONGO_SEED_PORT", default: str = "57017",
                 tls: bool = False) -> Any:
    import pymongo

    kw: dict[str, Any] = {"directConnection": True, "serverSelectionTimeoutMS": 8000,
                          "username": "rwroot", "password": _env("RW_MONGO_ROOT_PASSWORD"),
                          "authSource": "admin"}
    if tls:
        kw.update(tls=True, tlsCAFile=os.path.join(_tls_dir(), "ca.pem"))
    return pymongo.MongoClient(f"mongodb://127.0.0.1:{os.getenv(port_var, default)}/", **kw)


def _tls_dir() -> str:
    path = os.getenv("RW_TLS_DIR", "")
    if not path or not os.path.exists(os.path.join(path, "ca.pem")):
        pytest.skip("needs RW_TLS_DIR with the test CA (ca.pem, other-ca.pem)")
    return path


def _pem(name: str) -> str:
    with open(os.path.join(_tls_dir(), name), encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture
def mongo() -> Iterator[Any]:
    client = _seed_client()
    yield client
    client.close()


@pytest.fixture
def seeded(mongo: Any) -> Iterator[ms.MongoSeeded]:
    s = ms.seed(mongo, DB, tag(), orders=ORDERS, customers=CUSTOMERS, products=PRODUCTS)
    yield s
    with contextlib.suppress(Exception):
        ms.drop(mongo, s)


def _reader_config(s: ms.MongoSeeded, *collections: str, **over: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {"uri": RS_URI, "username": "rwreader",
                           "password": _env("RW_MONGO_READER_PASSWORD"),
                           "database": s.database, "collections": s.names(*collections),
                           "batch_size": 100}
    cfg.update(over)
    return cfg


def _source(api: LiveAPI, cleanup: Any, cid: str, cfg: dict[str, Any],
            expect: int = 201) -> dict[str, Any]:
    return sj.create_source(api, cleanup, family=FAMILY, source_type="mongodb", config=cfg,
                            collection_id=cid, expect=expect)


def _count(api: LiveAPI, cid: str) -> int:
    return int(kb.documents_page(api, cid, 1, 0).get("total") or 0)


def _hits(api: LiveAPI, cid: str, q: str, k: int = 5) -> list[dict[str, Any]]:
    return kb.search(api, cid, q, top_k=k)


# Ranking of structured (key: value) documents is P2's subject (the default
# cross-encoder rerank can push an exact keyword match down); ingestion is proven
# when the chunk is retrievable within the top 10 with its citation. Ranks are
# recorded as a metric.
PROBE_K = int(os.getenv("RW_PROBE_K", "10"))


def _hit_with(api: LiveAPI, cid: str, q: str, needle: str,
              k: int = PROBE_K) -> dict[str, Any] | None:
    for rank, h in enumerate(_hits(api, cid, q, k), start=1):
        if norm(needle) in norm(h.get("content")):
            return {**h, "_rank": rank}
    return None


def _citation(hit: dict[str, Any]) -> str:
    meta = hit.get("metadata") or {}
    return str(hit.get("source_url") or meta.get("source_url") or hit.get("source")
               or meta.get("source") or "")


def _jobs_after(api: LiveAPI, sid: str, job: dict[str, Any]) -> list[dict[str, Any]]:
    return [j for j in sj.jobs(api, sid)
            if str(j.get("created_at") or "") > str(job.get("started_at") or "")
            and not sj._same(j.get("job_id"), job.get("job_id"))]


def _psql(sql: str) -> str:
    container = os.getenv("RW_PG_CONTAINER", "agentverse-backend-postgres-1")
    out = subprocess.run(
        ["docker", "exec", container, "psql", "-U", "agentverse", "-d", "agentverse", "-Atc",
         sql], capture_output=True, text=True, timeout=60, check=False)
    assert out.returncode == 0, f"psql failed: {mask(out.stderr)[:300]}"
    return out.stdout.strip()


# ── SRC-MONGO-SYNC ──────────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-MONGO-SYNC")
def test_mongo_first_and_incremental_sync(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                                          mongo: Any, seeded: ms.MongoSeeded) -> None:
    """Replica set: first sync of 3 collections (BSON types, a 150-item array, deep
    nesting), then inserts (the _id scan), in-place updates (the change stream) and
    deletes (reconcile)."""
    cid = srcs.create_collection(api, cleanup, "rw-mongo")
    sid = _source(api, cleanup, cid, _reader_config(seeded))["id"]
    evidence.update(source_id=sid, collection_id=cid, collections=seeded.names())
    total = seeded.total

    job1 = sj.sync(api, sid, timeout=1800)
    evidence["sync1"] = job1
    soft: list[str] = []
    if str(job1.get("status")).lower() not in sj.COMPLETED:
        soft.append(f"sync 1 {job1.get('status')}: {job1.get('error_message')}")
    n1 = _count(api, cid)
    evidence["documents_after_sync1"] = n1
    if n1 != total:
        soft.append(f"sync 1 left {n1} documents, expected {total} ({seeded.counts})")
    probes = {
        "order note": ("Bhiwandi cold-chain annex bay C-14", ms.FACTS["order_note"]),
        "array item 3 of 150": ("customs Nhava Sheva gate 3 seal NS-4471",
                                 ms.FACTS["tracking_early"]),
        "8-level nested field": ("courier Thandiwe Mokoena e-cargo bike", ms.FACTS["deep"]),
        "string _id collection": ("Ishaan Raghunathan invoices Kannada", ms.FACTS["customer"]),
        "int _id collection": ("terracotta water cooler Kutch artisans", ms.FACTS["product"]),
        "Decimal128 exact": ("Diwali corporate gifting invoice total ledger sequence",
                             ms.FACTS["decimal"]),
        "Int64 beyond 2^53": ("Diwali corporate gifting invoice total ledger sequence",
                              ms.FACTS["int64"]),
    }
    cites: dict[str, str] = {}
    ranks: dict[str, int] = {}
    for name, (q, needle) in probes.items():
        hit = _hit_with(api, cid, q, needle)
        if hit is None:
            soft.append(f"not searchable: {name}")
            continue
        ranks[name] = hit["_rank"]
        cites[name] = _citation(hit)
        if not cites[name].startswith(f"mongodb://rw-mongo:27017/{DB}/"):
            soft.append(f"{name}: citation {cites[name]!r} does not name the MongoDB document")
    evidence.update(citations=cites, probe_ranks=ranks)
    late = _hit_with(api, cid, "pallet KX-998 rerouted late scan", "KX-998", 20)
    if late is not None:
        soft.append("array item 121 of 150 was indexed although arrays are bounded at 100")
    marker = _hit_with(api, cid, "tracking events hub scan more items not indexed",
                       "50 more item(s) of 150 not indexed")
    if marker is None:
        soft.append("the 150-item array carries no 'more items not indexed' marker")
    bson = _hit_with(api, cid, "Diwali corporate gifting signature sku pattern", "sku_pattern")
    if bson is not None:
        body = str(bson.get("content"))
        evidence["bson_rendering"] = body[:600]
        for needle in ("signature_png: <binary 72 bytes>", "<binary subtype 4, 16 bytes>",
                       "/^SKU-0[0-9]{3}$/i", "coupon: null", "gift_wrap: false",
                       "Timestamp(1790000000, 7)"):
            if needle.lower() not in body.lower():
                soft.append(f"BSON value rendered without {needle!r}")
    else:
        soft.append("the BSON-types order is not retrievable")

    # Upstream changes: 3 inserts, 2 in-place updates (no timestamp change: only the
    # change stream can see them), 2 deletes.
    orders = mongo[DB][seeded.collections["orders"]]
    from bson import ObjectId

    new_ids = [ObjectId() for _ in range(3)]  # > every existing _id: the scan finds them
    orders.insert_many([{"_id": oid, "order_no": f"ORD-NEW-{k}", "status": "placed",
                         "notes": (ms.INSERTED_NOTE if k == 0 else
                                   f"New order {k} for the Kochi store, festival stock."),
                         "lines": [{"sku": "SKU-0001", "qty": 2}]}
                        for k, oid in enumerate(new_ids)])
    old_note = orders.find_one({"_id": seeded.ids["update_a"]})["notes"]
    orders.update_one({"_id": seeded.ids["update_a"]},
                      {"$set": {"notes": ms.UPDATED["note"] + " (ticket RTO-5531)."}})
    orders.update_one({"_id": seeded.ids["update_b"]},
                      {"$set": {"status": ms.UPDATED["status"]}})
    orders.delete_many({"_id": {"$in": [seeded.ids["delete_a"], seeded.ids["delete_b"]]}})
    time.sleep(1)

    job2 = sj.sync(api, sid, timeout=900)
    evidence["sync2"] = job2
    if str(job2.get("status")).lower() not in sj.COMPLETED:
        soft.append(f"sync 2 {job2.get('status')}: {job2.get('error_message')}")
    later = _jobs_after(api, sid, job1)
    evidence["jobs_after_sync1"] = [(j.get("triggered_by"), j.get("status"),
                                     j.get("docs_indexed")) for j in later]
    changed = sum(int(j.get("docs_indexed") or 0) for j in later)
    if changed != 5:
        soft.append(f"{changed} documents indexed after sync 1, expected 5 (3 new + 2 updated)")
    if _hit_with(api, cid, "Onam festival rush order Kochi store", ms.INSERTED_NOTE) is None:
        soft.append("inserted document not searchable")
    if _hit_with(api, cid, "reroute Hosur micro-fulfilment centre RTO-5531",
                 ms.UPDATED["note"]) is None:
        soft.append("updated document's new text not searchable (change stream)")
    if _hit_with(api, cid, old_note[:60], old_note[:60], 20) is not None:
        soft.append("updated document's OLD text is still served")
    if _hit_with(api, cid, "returned to origin after failed KYC", ms.UPDATED["status"]) is None:
        soft.append("second updated document's new status not searchable")
    n2 = _count(api, cid)
    evidence["documents_after_sync2"] = n2
    # total + 3 until reconciled; total + 1 when the automatic post-sync reconcile
    # (queued 30 s after the clean sync 1, KB-44) already removed the 2 deletes.
    if n2 not in (total + 3, total + 1):
        soft.append(f"{n2} documents after sync 2, expected {total + 3} (or {total + 1} "
                    "once the automatic reconcile ran)")

    rec = api.post(f"/sources/{sid}/reconcile")
    evidence["reconcile_http"] = rec.status_code
    if rec.status_code != 202:
        soft.append(f"reconcile -> {rec.status_code}: {mask(rec.text)[:200]}")
    else:
        try:
            wait_until(lambda: _count(api, cid), timeout=300, interval=5,
                       desc="deleted documents removed", done=lambda n: n == total + 1)
        except AssertionError as exc:
            soft.append(f"reconcile did not remove exactly the 2 deleted documents: {exc}")
    evidence["documents_final"] = _count(api, cid)
    record(evidence, documents=total, sync1_s=job1.get("wall_s"), sync2_s=job2.get("wall_s"),
           docs_per_s=round(n1 / max(1.0, float(job1.get("wall_s") or 1)), 2),
           probe_mrr=round(sum(1 / r for r in ranks.values()) / max(1, len(probes)), 3))
    assert not soft, "; ".join(soft)


# ── SRC-MONGO-HOST-CHANGE ───────────────────────────────────────────────────


@pytest.mark.scenario("SRC-MONGO-HOST-CHANGE")
def test_mongo_host_change_keeps_ids(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                                     seeded: ms.MongoSeeded) -> None:
    """The same server under another name (a new seed list / DNS name): no duplicates."""
    cid = srcs.create_collection(api, cleanup, "rw-mongo-host")
    cfg = _reader_config(seeded, "products")
    sid = _source(api, cleanup, cid, cfg)["id"]
    job1 = sj.sync(api, sid, timeout=900)
    evidence["sync1"] = job1
    assert str(job1.get("status")).lower() in sj.COMPLETED, job1
    ids1 = {str(d.get("id")) for d in kb.all_documents(api, cid)[0]}
    assert len(ids1) == seeded.counts["products"], f"{len(ids1)} documents after sync 1"

    # Same server, another name; and a cursor_field change, which makes the next
    # sync re-read every document WITHOUT deleting anything first (unlike a reindex).
    resp = api.patch(f"/sources/{sid}", json={"connection_config": {
        **cfg, "uri": "mongodb://rw-mongo-alt:27017/?replicaSet=rs0",
        "cursor_field": "updated_at"}})
    assert resp.status_code == 200, f"PATCH -> {resp.status_code}: {mask(resp.text)[:300]}"
    job2 = sj.sync(api, sid, timeout=900)
    evidence["sync_after_host_change"] = job2
    ids2 = {str(d.get("id")) for d in kb.all_documents(api, cid)[0]}
    evidence.update(documents_before=len(ids1), documents_after=len(ids2))
    soft = []
    if str(job2.get("status")).lower() not in sj.COMPLETED:
        soft.append(f"re-read after host change {job2.get('status')}: "
                    f"{job2.get('error_message')}")
    if int(job2.get("docs_discovered") or 0) < len(ids1):
        soft.append(f"the re-read saw {job2.get('docs_discovered')} documents, expected "
                    f"{len(ids1)} (no full re-read happened)")
    if int(job2.get("docs_indexed") or 0) != 0:
        soft.append(f"the re-read indexed {job2.get('docs_indexed')} documents again "
                    "(expected 0: same ids, same content)")
    if ids2 != ids1:
        soft.append(f"document ids changed with the host: {len(ids2 - ids1)} new, "
                    f"{len(ids1 - ids2)} gone ({len(ids2)} total, expected {len(ids1)})")
    hit = _hit_with(api, cid, "terracotta water cooler Kutch artisans", ms.FACTS["product"])
    if hit is None:
        soft.append("content not searchable after the host change")
    else:
        evidence["citation_after"] = _citation(hit)
    assert not soft, "; ".join(soft)


# ── SRC-MONGO-D2 (one-time legacy id reindex) ───────────────────────────────


def _legacy_id(source_url: str) -> str:
    """Pre-v8 id: uuid5 of the document's host-based mongodb:// URL (unquoted key)."""
    from urllib.parse import unquote

    head, _, key = source_url.rpartition("/")
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{head}/{unquote(key)}"))


@pytest.mark.scenario("SRC-MONGO-D2")
def test_mongo_legacy_id_reindex(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                                 tenant_id: str, mongo: Any, seeded: ms.MongoSeeded) -> None:
    """Documents an earlier release indexed under host-based v5 ids are moved to v8 ids
    once, by the next sync: no duplicates, edits served, deleted ones removed."""
    cid = srcs.create_collection(api, cleanup, "rw-mongo-d2")
    sid = _source(api, cleanup, cid, _reader_config(seeded, "customers"))["id"]
    job1 = sj.sync(api, sid, timeout=900)
    assert str(job1.get("status")).lower() in sj.COMPLETED, job1
    docs = kb.all_documents(api, cid)[0]
    assert len(docs) == seeded.counts["customers"], f"{len(docs)} documents after sync 1"

    # Simulate the pre-upgrade index: 10 documents back under their legacy v5 ids, and
    # the Source's migration not yet run (as on the first sync after the upgrade).
    sid_hex = sid.replace("-", "")
    rows = _psql(
        "SELECT DISTINCT document_id, metadata->>'source_url' FROM ("
        + " UNION ALL ".join(
            f"SELECT document_id, metadata FROM knowledge_chunks_{d} "
            f"WHERE tenant_id = '{tenant_id}' AND collection_id = '{cid}'"
            for d in (768, 1024, 1536, 2048, 3072)) + ") c ORDER BY 1")
    pairs = [line.split("|", 1) for line in rows.splitlines() if "|" in line]
    assert len(pairs) == len(docs), f"chunk rows name {len(pairs)} documents"
    legacy = {v8: _legacy_id(url) for v8, url in pairs[:10]}
    for d in (768, 1024, 1536, 2048, 3072):
        for v8, v5 in legacy.items():
            _psql(f"UPDATE knowledge_chunks_{d} SET document_id = '{v5}' "
                  f"WHERE tenant_id = '{tenant_id}' AND document_id = '{v8}'")
    _psql(f"DELETE FROM ingestion_doc_id_migrations WHERE tenant_id = '{tenant_id}' "
          f"AND replace(source_id, '-', '') = '{sid_hex}'")
    legacy_urls = {v5: url for (v8, url) in pairs[:10] for v5 in [legacy[v8]]}
    # Upstream: one legacy document edited, one deleted.
    keys = [u.rsplit("/", 1)[1] for u in legacy_urls.values()]
    col = mongo[DB][seeded.collections["customers"]]
    col.update_one({"_id": keys[0]}, {"$set": {
        "notes": "Moved to quarterly billing with the Mangaluru distributor from Q4."}})
    col.delete_one({"_id": keys[1]})
    evidence.update(source_id=sid, legacy_documents=len(legacy), edited=keys[0],
                    deleted=keys[1])

    job2 = sj.sync(api, sid, timeout=900)
    evidence["sync2"] = job2
    soft: list[str] = []
    if str(job2.get("status")).lower() not in sj.COMPLETED:
        soft.append(f"sync 2 {job2.get('status')}: {job2.get('error_message')}")
    state = _psql(
        "SELECT status, scanned, migrated, deleted, skipped, failed FROM "
        f"ingestion_doc_id_migrations WHERE tenant_id = '{tenant_id}' AND "
        f"replace(source_id, '-', '') = '{sid_hex}'")
    evidence["migration_state"] = state
    if not state.startswith("completed|"):
        soft.append(f"migration did not complete: {state!r}")
    after = kb.all_documents(api, cid)[0]
    ids = {str(d.get("id")).replace("-", "") for d in after}
    stale = {v5 for v5 in legacy.values() if v5.replace("-", "") in ids}
    evidence.update(documents_after=len(after), stale_v5_left=len(stale))
    if stale:
        soft.append(f"{len(stale)} legacy v5 copies remain")
    if len(after) != seeded.counts["customers"] - 1:
        soft.append(f"{len(after)} documents after the migration, expected "
                    f"{seeded.counts['customers'] - 1} (one deleted upstream, no duplicates)")
    if _hit_with(api, cid, "quarterly billing Mangaluru distributor", "Mangaluru") is None:
        soft.append("the edited legacy document's new text is not served")
    assert not soft, "; ".join(soft)


# ── SRC-MONGO-TLS ───────────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-MONGO-TLS")
def test_mongo_tls_required_server(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    """requireTLS server: the CA makes it work (timestamp cursor on a standalone);
    no CA / a wrong CA fail honestly; every way to switch verification off is refused."""
    client = _seed_client("RW_MONGO_TLS_SEED_PORT", "57018", tls=True)
    t = tag()
    coll = client[DB][f"suppliers_{t}"]
    import datetime as dt

    t0 = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    coll.insert_many([{"_id": f"SUP-{i:03d}", "updated_at": t0 + dt.timedelta(minutes=i),
                       "notes": (f"Supplier {i:03d} delivers jute bags weekly to the "
                                 f"{ms.CITIES[i % 8]} warehouse.")} for i in range(1, 26)])
    coll.update_one({"_id": "SUP-007"}, {"$set": {
        "notes": "Supplier SUP-007 holds the ISO 22000 audit report for the Siliguri tea unit."}})
    base = {"uri": "mongodb://rw-mongo-tls:27017/", "username": "rwreader",
            "password": _env("RW_MONGO_READER_PASSWORD"), "database": DB,
            "collections": [coll.name], "cursor_field": "updated_at"}
    soft: list[str] = []
    try:
        cid = srcs.create_collection(api, cleanup, "rw-mongo-tls")
        # 1. CA supplied -> works.
        sid = _source(api, cleanup, cid, {**base, "tls": True, "tls_ca_pem": _pem("ca.pem")})["id"]
        job1 = sj.sync(api, sid, timeout=900)
        evidence["sync_with_ca"] = job1
        if str(job1.get("status")).lower() not in sj.COMPLETED or _count(api, cid) != 25:
            soft.append(f"TLS sync with the CA: {job1.get('status')} {job1.get('error_message')}"
                        f", {_count(api, cid)} documents (expected 25)")
        # Timestamp cursor on a standalone (no change stream): bumping updated_at re-reads.
        coll.update_one({"_id": "SUP-011"}, {"$set": {
            "notes": "Supplier SUP-011 switched to biodegradable mailers for Kolkata.",
            "updated_at": t0 + dt.timedelta(days=3)}})
        job2 = sj.sync(api, sid, timeout=600)
        evidence["sync2"] = job2
        if int(job2.get("docs_indexed") or 0) != 1:
            soft.append(f"timestamp-cursor sync 2 indexed {job2.get('docs_indexed')}, expected 1")
        if _hit_with(api, cid, "biodegradable mailers Kolkata", "biodegradable") is None:
            soft.append("updated supplier not searchable after the timestamp-cursor sync")
        if _hit_with(api, cid, "ISO 22000 audit Siliguri tea", "ISO 22000") is None:
            soft.append("TLS-synced content not searchable")
        # 2. Honest failures.
        cases = {
            "no CA (system trust)": {**base, "tls": True},
            "wrong CA": {**base, "tls": True, "tls_ca_pem": _pem("other-ca.pem")},
            "plain TCP to a TLS server": {**base},
        }
        failures: dict[str, Any] = {}
        for name, cfg in cases.items():
            started = time.monotonic()
            v = sj.validate(api, family=FAMILY, source_type="mongodb", config=cfg)
            failures[name] = {"valid": v.get("valid"), "s": round(time.monotonic() - started, 1),
                              "errors": mask(v.get("errors"))[:300]}
            text = str(v.get("errors"))
            if v.get("valid"):
                soft.append(f"{name}: validate said valid")
            elif "TopologyDescription" in text or "error id" not in text:
                soft.append(f"{name}: unsanitised or id-less error {mask(text)[:200]}")
            if time.monotonic() - started > 45:
                soft.append(f"{name}: validate took {time.monotonic() - started:.0f}s")
        evidence["failures"] = failures
        # 3. Verification can never be switched off (MDB-07), incl. the ';' separator.
        refused = {
            "?tlsInsecure=true": {**base, "uri": "mongodb://rw-mongo-tls:27017/?tlsInsecure=true"},
            "?tlsAllowInvalidCertificates=true": {
                **base, "uri": "mongodb://rw-mongo-tls:27017/?tls=true&tlsAllowInvalidCertificates=true"},
            ";tlsAllowInvalidHostnames=true": {
                **base, "uri": "mongodb://rw-mongo-tls:27017/?tls=true;tlsAllowInvalidHostnames=true"},
            "%3B-encoded tlsInsecure": {
                **base, "uri": "mongodb://rw-mongo-tls:27017/?tls=true%3BtlsInsecure=true"},
            "tls_allow_invalid_certificates field": {**base, "tls": True,
                                                     "tls_allow_invalid_certificates": True},
            "tls=false": {**base, "uri": "mongodb://rw-mongo-tls:27017/?tls=false"},
        }
        outcomes = {}
        for name, cfg in refused.items():
            resp = api.post("/sources", json={
                "name": f"rw-mongo-weak-{tag()}", "family": FAMILY, "source_type": "mongodb",
                "connection_config": cfg, "collection_id": cid, "sync_mode": "incremental",
                "sync_interval_seconds": 86400})
            outcomes[name] = resp.status_code
            if resp.status_code < 300:
                cleanup("DELETE", f"/sources/{resp.json().get('source_id')}")
                job = sj.sync(api, str(resp.json().get("source_id")), timeout=300)
                outcomes[name] = f"created; sync {job.get('status')}"
                if str(job.get("status")).lower() in sj.COMPLETED:
                    soft.append(f"{name}: accepted AND synced with verification off")
        evidence["weakening_refused"] = outcomes
        not_refused = [n for n, c in outcomes.items() if c != 422]
        if not_refused:
            soft.append(f"not refused with 422: {not_refused}")
    finally:
        with contextlib.suppress(Exception):
            coll.drop()
        client.close()
    assert not soft, "; ".join(soft)


# ── SRC-MONGO-STALL ─────────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-MONGO-STALL")
def test_mongo_stalled_server_times_out(api: LiveAPI, cleanup: Any,
                                        evidence: dict[str, Any]) -> None:
    """A server that accepts TCP and never answers, with the tenant's URI trying to
    disable every timeout (timeoutMS=0, socketTimeoutMS=0 ...): bounded, honest."""
    cfg = {"uri": "mongodb://rw-mongo-stall:27017/?timeoutMS=0&socketTimeoutMS=0"
                  ";serverSelectionTimeoutMS=0&connectTimeoutMS=0&maxTimeMS=0",
           "database": "shop", "collections": ["orders"]}
    started = time.monotonic()
    v = sj.validate(api, family=FAMILY, source_type="mongodb", config=cfg)
    validate_s = round(time.monotonic() - started, 1)
    cid = srcs.create_collection(api, cleanup, "rw-mongo-stall")
    sid = _source(api, cleanup, cid, cfg)["id"]
    job = sj.sync(api, sid, timeout=600)
    evidence.update(validate_s=validate_s, validate=mask(v)[:500], sync=job)
    soft = []
    if v.get("valid") or validate_s > 45:
        soft.append(f"validate valid={v.get('valid')} after {validate_s}s (expected invalid "
                    "within 45 s)")
    if str(job.get("status")).lower() != "failed":
        soft.append(f"sync ended {job.get('status')}, expected failed")
    err = str(job.get("error_message") or "")
    if "TopologyDescription" in err or "error id" not in err:
        soft.append(f"sync error is unsanitised or has no error id: {err[:200]}")
    started_at, done_at = job.get("started_at"), job.get("completed_at")
    if started_at and done_at:
        from datetime import datetime

        took = (datetime.fromisoformat(str(done_at)) -
                datetime.fromisoformat(str(started_at))).total_seconds()
        evidence["sync_run_s"] = round(took, 1)
        if took > 120:
            soft.append(f"the sync ran {took:.0f}s against a stalled server (bound: 120 s)")
    record(evidence, validate_s=validate_s, sync_wall_s=job.get("wall_s"))
    assert not soft, "; ".join(soft)


# ── SRC-MONGO-REFUSAL ───────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-MONGO-REFUSAL")
def test_mongo_refusals(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    """URI options that read platform files, use a proxy or ambient credentials —
    also behind the ';' separator — and internal hosts are refused on save (422)."""
    cid = srcs.create_collection(api, cleanup, "rw-mongo-refusal")
    base = "mongodb://rw-mongo:27017/"
    cases = {
        "tlsCAFile platform file": f"{base}?tls=true&tlsCAFile=/etc/ssl/certs/ca-certificates.crt",
        "';' tlsCertificateKeyFile": f"{base}?replicaSet=rs0;tlsCertificateKeyFile=/app/.env",
        "';' proxyHost": f"{base}?replicaSet=rs0;proxyHost=evil.example.com;proxyPort=1080",
        "%3B proxyHost": f"{base}?replicaSet=rs0%3BproxyHost=evil.example.com",
        "MONGODB-AWS ambient creds": f"{base}?authMechanism=MONGODB-AWS",
        "MONGODB-OIDC": f"{base}?authMechanism=MONGODB-OIDC",
        "GSSAPI": f"{base}?authMechanism=GSSAPI",
        "platform postgres": "mongodb://postgres:5432/",
        "platform redis": "mongodb://redis:6379/",
        "metadata IP": "mongodb://169.254.169.254:80/",
        "localhost": "mongodb://localhost:27017/",
        "backend": "mongodb://backend:8000/",
        "multi-host with one internal": "mongodb://rw-mongo:27017,postgres:5432/?replicaSet=rs0",
        "srv to internal": "mongodb+srv://redis/",
    }
    outcomes: dict[str, Any] = {}
    for name, uri in cases.items():
        resp = api.post("/sources", json={
            "name": f"rw-mongo-ref-{tag()}", "family": FAMILY, "source_type": "mongodb",
            "connection_config": {"uri": uri, "database": DB}, "collection_id": cid,
            "sync_mode": "incremental", "sync_interval_seconds": 86400})
        outcomes[name] = resp.status_code
        if resp.status_code < 300:
            cleanup("DELETE", f"/sources/{resp.json().get('source_id')}")
    # The allowlisted server itself is accepted.
    ok = api.post("/sources", json={
        "name": f"rw-mongo-ok-{tag()}", "family": FAMILY, "source_type": "mongodb",
        "connection_config": {"uri": RS_URI, "database": DB}, "collection_id": cid,
        "sync_mode": "incremental", "sync_interval_seconds": 86400})
    if ok.status_code < 300:
        cleanup("DELETE", f"/sources/{ok.json().get('source_id')}")
    outcomes["allowlisted rw-mongo"] = ok.status_code
    evidence["outcomes"] = outcomes
    wrong = {n: c for n, c in outcomes.items()
             if (c != 201 if n == "allowlisted rw-mongo" else c != 422)}
    assert not wrong, f"unexpected answers: {wrong}"


# ── SRC-MONGO-FAILURES ──────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-MONGO-FAILURES")
def test_mongo_honest_failures(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                               seeded: ms.MongoSeeded) -> None:
    good = _reader_config(seeded, "products")
    cases = {
        "wrong password": ({**good, "password": "not-the-password-0000"},
                           ("authentication", "credentials")),
        "unknown user": ({**good, "username": "nobody_p1c"}, ("authentication", "credentials")),
        "database without privilege": ({**good, "database": "admin", "collections": ["system.version"]},
                                       ("not authorized", "permission", "privilege")),
        "unreachable port": ({**good, "uri": "mongodb://rw-mongo:27999/?directConnection=true",
                              "timeout_ms": 4000}, ("could not connect", "unreachable",
                                                    "timed out", "timeout", "refused")),
        "missing collection": ({**good, "collections": ["no_such_collection_p1c"]},
                               ("no_such_collection_p1c", "not found", "does not exist",
                                "missing")),
    }
    cid = srcs.create_collection(api, cleanup, "rw-mongo-fail")
    soft: list[str] = []
    out: dict[str, Any] = {}
    for name, (cfg, words) in cases.items():
        v = sj.validate(api, family=FAMILY, source_type="mongodb", config=cfg)
        sid = _source(api, cleanup, cid, cfg)["id"]
        job = sj.sync(api, sid, timeout=300)
        err = str(job.get("error_message") or "")
        out[name] = {"validate_valid": v.get("valid"), "validate": mask(v.get("errors"))[:240],
                     "sync": job.get("status"), "indexed": job.get("docs_indexed"),
                     "error": err[:240]}
        if v.get("valid"):
            soft.append(f"{name}: validate said valid")
        if str(job.get("status")).lower() in sj.COMPLETED:
            soft.append(f"{name}: sync completed ({job.get('docs_indexed')} indexed) instead of "
                        "failing")
        elif not any(w in (err + str(v.get("errors"))).lower() for w in words):
            soft.append(f"{name}: no reason among {words} in {err[:160]!r}")
        if "TopologyDescription" in err:
            soft.append(f"{name}: raw driver topology in the job error")
    evidence["cases"] = out
    assert not soft, "; ".join(soft)


# ── SRC-MONGO-KILL-SWITCH ───────────────────────────────────────────────────


@pytest.mark.scenario("SRC-MONGO-KILL-SWITCH")
def test_mongo_kill_switch(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    """With INGESTION_CONNECTOR_MONGODB_ENABLED=false: create / validate refused with the
    reason, an existing Source's sync fails with it, health is refused."""
    if os.getenv("RW_MONGO_KILL_SWITCH") != "off":
        pytest.skip("run with RW_MONGO_KILL_SWITCH=off against a stack started with "
                    "INGESTION_CONNECTOR_MONGODB_ENABLED=false (RW_MONGO_KILL_SOURCE_ID = a "
                    "MongoDB Source created before the switch)")
    cid = srcs.create_collection(api, cleanup, "rw-mongo-kill")
    cfg = {"uri": RS_URI, "database": DB, "collections": ["x"]}
    created = api.post("/sources", json={
        "name": f"rw-mongo-kill-{tag()}", "family": FAMILY, "source_type": "mongodb",
        "connection_config": cfg, "collection_id": cid})
    v = sj.validate(api, family=FAMILY, source_type="mongodb", config=cfg)
    out: dict[str, Any] = {"create": created.status_code, "create_detail": created.text[:200],
                           "validate": v}
    soft = []
    if created.status_code != 422 or "disabled" not in created.text.lower():
        soft.append(f"create -> {created.status_code} {created.text[:160]}")
    if v.get("valid") or "disabled" not in str(v.get("errors")).lower():
        soft.append(f"validate: {v}")
    existing = os.getenv("RW_MONGO_KILL_SOURCE_ID", "")
    if existing:
        sync = api.post(f"/sources/{existing}/sync")
        health = api.get(f"/sources/{existing}/health")
        out.update(sync=sync.status_code, sync_detail=sync.text[:200],
                   health=health.status_code, health_detail=health.text[:200])
        if sync.status_code == 202:
            job = sj.wait_job(api, existing, str(sync.json().get("job_id")), 300)
            out["sync_job"] = sj.mask_job(job)
            if str(job.get("status")).lower() != "failed" or "disabled" not in str(
                    job.get("error_message")).lower():
                soft.append(f"existing source's sync: {job.get('status')} "
                            f"{job.get('error_message')}")
        elif sync.status_code != 422 or "disabled" not in sync.text.lower():
            soft.append(f"existing source's sync -> {sync.status_code} {sync.text[:160]}")
        if "disabled" not in health.text.lower():
            soft.append(f"health -> {health.status_code} {health.text[:160]}")
    evidence.update(out)
    assert not soft, "; ".join(soft)
