"""MONGO-FAIL-*: failure cases of the MongoDB pipeline — honest outcomes AND recovery.

Every failure is a real one: wrong credentials against the real replica set, a closed
port, the hung ``rw-mongo-stall`` server, the requireTLS ``rw-mongo-tls`` server, a
per-run MongoDB user whose role is really revoked mid-sync and re-granted, poison
documents really stored in MongoDB, a real schema change between syncs, the built-in
MCP connector refusing real operators, a real HITL timeout and a real cancel. No
fake servers and no stubbed responses; a missing prerequisite SKIPS with its variable.

Expected outcomes are the honest ones: a failed / partial job with a sanitised reason
and an error id (never ``completed`` with documents silently missing), bounded time,
nothing indexed that should not be, and — after the operator fixes the cause — a
retry / re-sync that converges on EXACTLY the source documents with no duplicates.

Environment: ``RW_MONGO_ROOT_PASSWORD``, ``RW_MONGO_READER_PASSWORD``,
``RW_MONGO_TOOL_PASSWORD`` (+ ``RW_MONGO_SEED_PORT``); ``RW_TLS_DIR`` for MONGO-FAIL-TLS;
``RW_APPROVAL_SWEEP_WAIT`` (seconds to wait for the HITL timeout escalation).
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterator
from typing import Any

import pytest

from tests.real_world import commerce_seed as cs
from tests.real_world import kb
from tests.real_world import live_mongo as lm
from tests.real_world import source_jobs as sj
from tests.real_world import sources as srcs
from tests.real_world import wf_mongo as wfm
from tests.real_world import workflows as wfx
from tests.real_world.helpers import LiveAPI, env_float, mask, tag, wait_until
from tests.real_world.metrics import norm, record

DB = lm.SOURCE_DB
SWEEP_WAIT = env_float("RW_APPROVAL_SWEEP_WAIT", 600)


@pytest.fixture
def mongo() -> Iterator[Any]:
    client = lm.seed_client()
    yield client
    client.close()


@pytest.fixture
def small_set(mongo: Any) -> Iterator[dict[str, Any]]:
    """A 600-document commerce subset in this run's own collections."""
    t = tag()
    data = cs.generate(600)
    names = cs.seed(mongo, DB, t, data)
    yield {"data": data, "names": names, "tag": t}
    with contextlib.suppress(Exception):
        cs.drop(mongo, DB, names)


def _honest_error(job: dict[str, Any]) -> list[str]:
    err = str(job.get("error_message") or "")
    out = []
    if "TopologyDescription" in err or "Traceback" in err:
        out.append(f"raw driver text in the job error: {err[:160]}")
    if "error id" not in err:
        out.append(f"job error without an error id: {err[:160]}")
    return out


def _expected(names: dict[str, str], data: cs.CommerceData, logical: tuple[str, ...]) -> set[str]:
    return lm.expected_tails(DB, {k: names[k] for k in logical},
                             {k: data.keys(k) for k in logical})


# ── MONGO-FAIL-AUTH / NETWORK ───────────────────────────────────────────────


@pytest.mark.scenario("MONGO-FAIL-CONNECT")
def test_connect_failures_are_honest_and_bounded(api: LiveAPI, cleanup: Any,
                                                 evidence: dict[str, Any],
                                                 small_set: dict[str, Any]) -> None:
    names = small_set["names"]
    good = lm.reader_config([names["postmortems"]])
    cases: dict[str, tuple[dict[str, Any], tuple[str, ...], float]] = {
        "bad password": ({**good, "password": "definitely-not-the-password-42"},
                         ("authentication", "credentials"), 60),
        "unknown user": ({**good, "username": f"nobody_{tag()}"},
                         ("authentication", "credentials"), 60),
        "unreachable host (closed port)": (
            {**good, "uri": "mongodb://rw-mongo:27999/?directConnection=true",
             "timeout_ms": 4000}, ("could not connect", "unreachable", "timed out", "timeout",
                                   "refused"), 90),
        "stalled server": ({"uri": "mongodb://rw-mongo-stall:27017/?timeoutMS=0",
                            "database": "shop", "collections": ["orders"]},
                           ("timed out", "timeout", "could not connect", "unreachable"), 150),
    }
    cid = srcs.create_collection(api, cleanup, "rw-mongo-fail-connect")
    soft: list[str] = []
    out: dict[str, Any] = {}
    for name, (cfg, words, bound) in cases.items():
        started = time.monotonic()
        v = sj.validate(api, family=lm.FAMILY, source_type="mongodb", config=cfg)
        validate_s = round(time.monotonic() - started, 1)
        sid = lm.create_source(api, cleanup, cid, cfg)["id"]
        job = sj.sync(api, sid, timeout=600)
        out[name] = {"validate_valid": v.get("valid"), "validate_s": validate_s,
                     "validate": mask(v.get("errors"))[:200], "sync": job.get("status"),
                     "indexed": job.get("docs_indexed"),
                     "error": str(job.get("error_message"))[:200]}
        if v.get("valid"):
            soft.append(f"{name}: validate said valid")
        if validate_s > 45:
            soft.append(f"{name}: validate took {validate_s}s")
        if str(job.get("status")).lower() in sj.COMPLETED:
            soft.append(f"{name}: sync completed instead of failing")
        else:
            soft += [f"{name}: {p}" for p in _honest_error(job)]
            text = (str(job.get("error_message")) + str(v.get("errors"))).lower()
            if not any(w in text for w in words):
                soft.append(f"{name}: no reason among {words}")
        if float(job.get("wall_s") or 0) > bound:
            soft.append(f"{name}: the failing sync took {job.get('wall_s')}s (bound {bound}s)")
        if int(job.get("docs_indexed") or 0):
            soft.append(f"{name}: indexed {job.get('docs_indexed')} documents")
    evidence["cases"] = out
    if kb.documents_page(api, cid, 1, 0).get("total"):
        soft.append("documents reached the collection through a failing Source")
    record(evidence, cases=len(cases))
    assert not soft, "; ".join(soft)


@pytest.mark.scenario("MONGO-FAIL-TLS")
def test_tls_misconfig_refused(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    tls_dir = os.getenv("RW_TLS_DIR", "")
    if not tls_dir or not os.path.exists(os.path.join(tls_dir, "other-ca.pem")):
        pytest.skip("needs RW_TLS_DIR with ca.pem / other-ca.pem (the rw-mongo-tls test CA)")
    with open(os.path.join(tls_dir, "other-ca.pem"), encoding="utf-8") as fh:
        other_ca = fh.read()
    base = {"uri": "mongodb://rw-mongo-tls:27017/", "username": "rwreader",
            "password": lm.env_secret("RW_MONGO_READER_PASSWORD"), "database": DB,
            "collections": ["orders"]}
    cid = srcs.create_collection(api, cleanup, "rw-mongo-fail-tls")
    soft: list[str] = []
    out: dict[str, Any] = {}
    wrong = lm.create_source(api, cleanup, cid, {**base, "tls": True, "tls_ca_pem": other_ca})
    job = sj.sync(api, wrong["id"], timeout=300)
    out["wrong CA"] = {"sync": job.get("status"), "error": str(job.get("error_message"))[:200]}
    if str(job.get("status")).lower() in sj.COMPLETED:
        soft.append("a server certificate from another CA was accepted")
    else:
        soft += [f"wrong CA: {p}" for p in _honest_error(job)]
    weakened = {
        "tls_allow_invalid_certificates": {**base, "tls": True,
                                           "tls_allow_invalid_certificates": True},
        "tlsInsecure in URI": {**base, "uri": "mongodb://rw-mongo-tls:27017/?tlsInsecure=true"},
        "tlsAllowInvalidHostnames via ;": {
            **base, "uri": "mongodb://rw-mongo-tls:27017/?tls=true;tlsAllowInvalidHostnames=true"},
    }
    for name, cfg in weakened.items():
        resp = api.post("/sources", json={
            "name": f"rw-mongo-weak-{tag()}", "family": lm.FAMILY, "source_type": "mongodb",
            "connection_config": cfg, "collection_id": cid, "sync_mode": "incremental",
            "sync_interval_seconds": 86400})
        out[name] = resp.status_code
        if resp.status_code < 300:
            cleanup("DELETE", f"/sources/{resp.json().get('source_id')}")
        if resp.status_code != 422:
            soft.append(f"{name}: answered {resp.status_code}, expected 422 refusal")
    evidence["cases"] = out
    assert not soft, "; ".join(soft)


# ── MONGO-FAIL-PERMISSION-RECOVERY ──────────────────────────────────────────


@pytest.mark.scenario("MONGO-FAIL-PERMISSION-RECOVERY")
def test_permission_revoked_mid_sync_then_regranted(api: LiveAPI, cleanup: Any,
                                                    evidence: dict[str, Any], mongo: Any,
                                                    small_set: dict[str, Any]) -> None:
    """A real per-run user loses ``read`` while a sync runs: the job ends partial / failed
    honestly with only correct documents indexed; once re-granted, a re-sync completes
    with EXACTLY the source documents and no duplicates."""
    data, names = small_set["data"], small_set["names"]
    logical = ("orders", "payment_events", "support_tickets")
    expected = _expected(names, data, logical)
    soft: list[str] = []
    with lm.temp_reader(mongo) as user:
        cid = srcs.create_collection(api, cleanup, "rw-mongo-perm")
        cfg = lm.reader_config([names[k] for k in logical], username=user["user"],
                               password=user["password"], batch_size=50)
        sid = lm.create_source(api, cleanup, cid, cfg)["id"]
        job_id = sj.trigger(api, sid)
        seen = lm.wait_running_job(api, sid, job_id, min_indexed=40, timeout=900)
        lm.revoke_read(mongo, user["user"])
        revoked_at = seen.get("docs_indexed")
        job1 = sj.wait_job(api, sid, job_id, timeout=1800)
        evidence.update(source_id=sid, revoked_after_indexed=revoked_at, job_revoked=job1)
        status1 = str(job1.get("status")).lower()
        inv1 = lm.inventory(api, sid, cid, expected)
        evidence["inventory_while_revoked"] = {k: inv1[k] for k in (
            "documents", "extra_count", "duplicate_ids", "duplicate_urls")}
        if status1 in sj.COMPLETED and inv1["missing_count"]:
            soft.append(f"the sync reported completed with {inv1['missing_count']} documents "
                        "missing (silent loss after the revoke)")
        if status1 not in sj.COMPLETED:
            soft += [f"revoked: {p}" for p in _honest_error(job1)]
            if "not authorized" not in str(job1.get("error_message")).lower() and \
                    "unauthorized" not in str(job1.get("error_message")).lower():
                soft.append(f"the revoked sync's reason does not say not authorized: "
                            f"{str(job1.get('error_message'))[:160]}")
        else:
            # The read finished before the revoke bit: prove the revoke on fresh data.
            evidence["note"] = "sync finished before the revoke; verified on a fresh insert"
            mongo[DB][names["orders"]].insert_many(cs.new_orders(3))
            expected |= {cs.doc_path(DB, names["orders"], str(d["_id"])) for d in
                         mongo[DB][names["orders"]].find({"order_no": {"$regex": "^ORD-88"}},
                                                         {"_id": 1})}
            job_r = sj.sync(api, sid, timeout=600)
            evidence["job_after_revoke"] = job_r
            if str(job_r.get("status")).lower() in sj.COMPLETED:
                soft.append("a sync with the read role revoked completed")
        if inv1["extra_count"] or inv1["duplicate_ids"] or inv1["duplicate_urls"]:
            soft.append("the partial sync indexed extra or duplicate documents")

        lm.grant_read(mongo, user["user"])
        job2 = sj.sync(api, sid, timeout=1800)
        evidence["job_regranted"] = job2
        if str(job2.get("status")).lower() not in sj.COMPLETED:
            soft.append(f"re-sync after the re-grant {job2.get('status')}: "
                        f"{job2.get('error_message')}")
        inv2 = lm.inventory(api, sid, cid, expected)
        evidence["inventory_after_regrant"] = {k: v for k, v in inv2.items()
                                               if k != "content_hash"}
        soft += lm.exactness_problems(inv2)
        record(evidence, indexed_before_revoke=revoked_at,
               documents_while_revoked=inv1["documents"], documents_final=inv2["documents"],
               resync_s=job2.get("wall_s"))
    assert not soft, "; ".join(soft)


# ── MONGO-FAIL-POISON ───────────────────────────────────────────────────────


@pytest.mark.scenario("MONGO-FAIL-POISON")
def test_poison_documents_dlq_and_operator_retry(api: LiveAPI, cleanup: Any,
                                                 evidence: dict[str, Any], mongo: Any) -> None:
    """Over-size, near-limit nesting, a 20,000-item array, odd BSON and an invalid-UTF-8
    string among 50 normal tickets: no silent loss — each poison document is indexed
    (with its truncation reported), skipped with a reason or dead-lettered with a reason;
    after the operator fixes them upstream (and retries the DLQ entries) a re-sync holds
    EXACTLY every document."""
    t = tag()
    coll = mongo[DB][f"poison_{t}"]
    normal = [{"_id": f"OK-{i:03d}", "kind": "normal",
               "notes": f"Routine merchant ticket {i:03d} about a delayed settlement."}
              for i in range(50)]
    coll.insert_many(normal)
    poison = cs.poison_docs()
    coll.insert_many(list(poison.values()))
    planted_utf8 = False
    try:
        from bson.raw_bson import RawBSONDocument

        coll.insert_one(RawBSONDocument(cs.invalid_utf8_raw("POISON-UTF8")))
        planted_utf8 = True
    except Exception as exc:  # the server refused it: recorded, the rest still runs
        evidence["invalid_utf8_planted"] = f"refused by the server: {type(exc).__name__}"
    keys = [d["_id"] for d in normal] + [d["_id"] for d in poison.values()] + (
        ["POISON-UTF8"] if planted_utf8 else [])
    expected = {cs.doc_path(DB, coll.name, k) for k in keys}
    soft: list[str] = []
    try:
        cid = srcs.create_collection(api, cleanup, "rw-mongo-poison")
        sid = lm.create_source(api, cleanup, cid, lm.reader_config([coll.name],
                                                                   batch_size=20))["id"]
        job1 = sj.sync(api, sid, timeout=1800)
        evidence.update(source_id=sid, job1=job1)
        dlq1 = sj.dlq(api, sid)
        missing = {u.rsplit("/", 1)[-1] for u in expected} - {
            lm.url_tail(str(d.get("source_url"))).rsplit("/", 1)[-1]
            for d in lm.source_documents(api, sid)}
        evidence["first_pass"] = {"status": job1.get("status"), "missing": sorted(missing),
                                  "dlq": [{k: mask(e.get(k))[:160] for k in (
                                      "doc_id", "failed_stage", "failure_type",
                                      "error_message", "last_error")} for e in dlq1]}
        evidence["indexed_count"] = len(keys) - len(missing)
        status1 = str(job1.get("status")).lower()
        accounted = int(job1.get("docs_skipped") or 0) + int(job1.get("docs_failed") or 0)
        if status1 in sj.COMPLETED and len(missing) > accounted:
            soft.append(f"{len(missing)} documents missing but only {accounted} skipped/failed "
                        f"were reported: silent loss ({sorted(missing)[:5]})")
        if status1 not in sj.COMPLETED:
            soft += [f"poison sync: {p}" for p in _honest_error(job1)]
        for entry in dlq1:
            if not (entry.get("error_message") or entry.get("last_error")):
                soft.append(f"DLQ entry {entry.get('id')} has no reason")
        normal_missing = [k for k in missing if str(k).startswith("OK-")]
        if status1 in sj.COMPLETED and normal_missing:
            soft.append(f"normal documents lost next to the poison ones: {normal_missing[:5]}")
        if "POISON-DEEP" not in missing:
            hit = [h for h in kb.search(api, cid, "Bottom of the 95-level settlement tree", 10)
                   if "95-level" in str(h.get("content"))]
            if not hit:
                soft.append("the 95-level document is indexed but its leaf is not searchable")
        if "POISON-ARRAY" not in missing:
            hit = [h for h in kb.search(api, cid, "click-stream events more items not indexed",
                                        10) if "19900 more item(s) of 20000" in
                   str(h.get("content"))]
            if not hit:
                soft.append("the 20,000-item array carries no '19900 more item(s)' marker")
        if "POISON-OVERSIZE" not in missing:
            soft.append("an 11 MiB document was indexed despite the 10 MiB per-document cap")

        # Operator fix: replace every poison document with valid, bounded content.
        coll.replace_one({"_id": "POISON-OVERSIZE"}, {
            "kind": "oversize-fixed", "notes": "Archived gateway dump trimmed to a summary: "
                                               "1,204 settlement callbacks, 3 retries."})
        if planted_utf8:
            coll.replace_one({"_id": "POISON-UTF8"}, {
                "kind": "utf8-fixed", "notes": "Merchant note re-exported as valid UTF-8 "
                                               "from the legacy system."})
        retried: list[Any] = []
        for entry in sj.dlq(api, sid):
            resp = api.post(f"/ingestion/dlq/{entry.get('id')}/retry")
            retried.append((resp.status_code, mask(resp.text)[:120]))
            if resp.status_code != 202:
                soft.append(f"DLQ retry -> {resp.status_code}: {mask(resp.text)[:120]}")
        evidence["dlq_retries"] = retried
        if retried:
            try:
                wait_until(lambda: sj.dlq(api, sid), timeout=600, interval=10,
                           desc="retried DLQ entries resolved", done=lambda left: not left)
            except AssertionError:
                left = sj.dlq(api, sid)
                evidence["dlq_left"] = [{k: mask(e.get(k))[:160] for k in (
                    "doc_id", "retry_count", "last_error")} for e in left]
        job2 = sj.sync(api, sid, timeout=1800)
        evidence["job_after_fix"] = job2
        if str(job2.get("status")).lower() not in sj.COMPLETED:
            soft.append(f"re-sync after the fix {job2.get('status')}: {job2.get('error_message')}")
        inv2 = wait_until(lambda: lm.inventory(api, sid, cid, expected), timeout=300,
                          interval=10, desc="every fixed document indexed",
                          done=lambda i: not lm.exactness_problems(i))
        soft += lm.exactness_problems(inv2)
        resolved = sj.dlq(api, sid, include_resolved=True)
        evidence["dlq_final"] = [{"open": e.get("resolved_at") is None,
                                  "retries": e.get("retry_count")} for e in resolved]
        if any(e.get("resolved_at") is None for e in resolved):
            soft.append("DLQ entries still open after the fix and re-sync")
        if not [h for h in kb.search(api, cid, "gateway dump trimmed settlement callbacks", 10)
                if "1,204 settlement callbacks" in str(h.get("content"))]:
            soft.append("the fixed over-size document is not searchable")
        record(evidence, poison_docs=len(keys) - len(normal), dlq_entries=len(dlq1),
               missing_first_pass=len(missing), documents_final=inv2["documents"])
    except AssertionError as exc:
        soft.append(str(exc)[:400])
    finally:
        with contextlib.suppress(Exception):
            coll.drop()
    assert not soft, "; ".join(soft)


# ── MONGO-FAIL-SCHEMA-DRIFT ─────────────────────────────────────────────────


@pytest.mark.scenario("MONGO-FAIL-SCHEMA-DRIFT")
def test_schema_drift_between_syncs(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                                    mongo: Any, small_set: dict[str, Any]) -> None:
    data, names = small_set["data"], small_set["names"]
    coll = mongo[DB][names["payment_events"]]
    cid = srcs.create_collection(api, cleanup, "rw-mongo-drift")
    sid = lm.create_source(api, cleanup, cid, lm.reader_config([names["payment_events"]]))["id"]
    job1 = sj.sync(api, sid, timeout=900)
    drifted = cs.drifted_payments(6)
    coll.insert_many(drifted)
    victims = data.keys("payment_events")[30:34]
    from bson import ObjectId

    for k in victims:  # v1 -> v2 in place: Decimal128 amount -> integer paise, method -> object
        coll.update_one({"_id": ObjectId(k)}, {
            "$unset": {"amount": ""},
            "$set": {"schema_version": 2, "amount_minor": 4242000,
                     "method": {"type": "card", "network": "RuPay"}}})
    job2 = sj.sync(api, sid, timeout=900)
    expected = {cs.doc_path(DB, names["payment_events"], k) for k in data.keys("payment_events")}
    expected |= {cs.doc_path(DB, names["payment_events"], str(d["_id"])) for d in drifted}
    inv = lm.inventory(api, sid, cid, expected)
    evidence.update(job1=job1, job2=job2,
                    inventory={k: v for k, v in inv.items() if k != "content_hash"})
    soft: list[str] = []
    for label, job in (("v1 sync", job1), ("drift sync", job2)):
        if str(job.get("status")).lower() not in sj.COMPLETED or int(job.get("docs_failed") or 0):
            soft.append(f"{label}: {job.get('status')} failed={job.get('docs_failed')} "
                        f"{job.get('error_message')}")
    if int(job2.get("docs_indexed") or 0) != len(drifted) + len(victims):
        soft.append(f"drift sync indexed {job2.get('docs_indexed')}, expected "
                    f"{len(drifted) + len(victims)}")
    soft += lm.exactness_problems(inv)
    if not [h for h in kb.search(api, cid, "schema v2 settlement Thrissur jewellery merchant", 10)
            if "thrissur" in norm(h.get("content"))]:
        soft.append("a schema-v2 document is not searchable")
    hit = [h for h in kb.search(api, cid, "RuPay card amount_minor 4242000", 20)
           if cs.hit_is(h, DB, names["payment_events"], victims[0])]
    if not hit or "4242000" not in str(hit[0].get("content")):
        soft.append("a document migrated in place to v2 does not serve its new fields")
    elif "amount: " in str(hit[0].get("content")):
        soft.append("the migrated document still serves its removed v1 amount field")
    assert not soft, "; ".join(soft)


# ── MONGO-FAIL-DUPLICATE-SOURCE ─────────────────────────────────────────────


@pytest.mark.scenario("MONGO-FAIL-DUPLICATE-SOURCE")
def test_duplicate_source_registration(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                                       small_set: dict[str, Any]) -> None:
    """The same MongoDB collection registered twice into one KB collection: refused, or
    accepted without ever serving a document twice; removing the duplicate keeps the
    content of the first."""
    data, names = small_set["data"], small_set["names"]
    cid = srcs.create_collection(api, cleanup, "rw-mongo-dup")
    cfg = lm.reader_config([names["postmortems"]])
    first = lm.create_source(api, cleanup, cid, cfg)["id"]
    job1 = sj.sync(api, first, timeout=900)
    resp = api.post("/sources", json={
        "name": f"rw-mongodb-dup-{tag()}", "family": lm.FAMILY, "source_type": "mongodb",
        "connection_config": cfg, "collection_id": cid, "sync_mode": "incremental",
        "sync_interval_seconds": 86400, "pii_action": "none", "min_quality_score": 0.0})
    evidence.update(job1=job1, second_http=resp.status_code, second=mask(resp.text)[:200])
    soft: list[str] = []
    if resp.status_code >= 300:
        if resp.status_code not in (409, 422):
            soft.append(f"duplicate registration answered {resp.status_code}")
        assert not soft, "; ".join(soft)
        return
    second = str(resp.json().get("source_id") or resp.json().get("id"))
    cleanup("DELETE", f"/sources/{second}")
    job2 = sj.sync(api, second, timeout=900)
    docs, _ = kb.all_documents(api, cid)
    urls = [lm.url_tail(str(d.get("source_url"))) for d in docs]
    dups = cs.duplicates(urls)
    evidence.update(job2=job2, documents=len(docs), duplicate_urls=sorted(dups)[:5])
    if dups:
        soft.append(f"{len(dups)} MongoDB documents are served twice after the duplicate "
                    "registration")
    hits = kb.search(api, cid, "settlement webhook outage expired TLS certificate root cause", 10)
    if cs.duplicate_hits(hits) or len({cs.hit_url(h) for h in hits if "PM-2026-014" in
                                       cs.hit_url(h)}) > 1:
        soft.append("search returns the same postmortem twice")
    api.delete(f"/sources/{second}")
    time.sleep(5)
    left = [h for h in kb.search(api, cid, "settlement webhook outage expired TLS certificate",
                                 10) if "PM-2026-014" in cs.hit_url(h)]
    if not left:
        soft.append("deleting the duplicate Source removed the first Source's content")
    record(evidence, documents=len(docs), expected=len(data.docs["postmortems"]))
    assert not soft, "; ".join(soft)


# ── MONGO-FAIL-MCP-TOOLS ────────────────────────────────────────────────────


@pytest.mark.scenario("MONGO-FAIL-MCP-TOOLS")
def test_mcp_tool_errors_retry_and_failure_path(api: LiveAPI, cleanup: Any,
                                                evidence: dict[str, Any], mongo: Any) -> None:
    t = tag()
    orders = f"orders_{t}"
    mongo[lm.TOOL_DB][orders].insert_many(cs.generate(300).docs["orders"])
    try:
        good = lm.register_connector(api, cleanup, f"commerce-db-{t}", lm.tool_dsn())
        stalled = lm.register_connector(api, cleanup, f"commerce-db-stall-{t}",
                                        "mongodb://rw-mongo-stall:27017/shop?timeoutMS=0")
        wf_id = wfx.import_yaml(api, cleanup, wfm.tool_failures_yaml(
            f"rw-mongo-tool-failures-{t}", server_id=good["server_id"],
            stalled_server_id=stalled["server_id"], orders=orders))
        run_id = wfx.trigger_run(api, cleanup, wf_id)
        run = wfx.wait_status(api, run_id, {"complete"}, 900)
        steps = wfx.get_steps(api, run_id)
        evidence.update(run_id=run_id, status=run.get("status"), steps={
            k: {"status": v.get("status"), "output": mask(wfx.step_output(v))[:220],
                "started": v.get("started_at"), "finished": v.get("finished_at") or
                v.get("completed_at")} for k, v in steps.items()})
        soft: list[str] = []
        if run.get("status") != "complete":
            soft.append(f"run ended {run.get('status')} at {run.get('error_step_id')}: "
                        f"{mask(run.get('error'))[:160]} (the failure path should complete)")

        def out(sid: str) -> dict[str, Any]:
            o = wfx.output_of(steps, sid)
            return o if isinstance(o, dict) else {"raw": o}

        bw = json.dumps(out("bad_write")).lower()
        if not out("bad_write").get("_skipped") or not any(
                w in bw for w in ("refused", "not allowed", "write stage")):
            soft.append(f"$out was not refused: {mask(out('bad_write'))[:160]}")
        if f"{orders}_pwn" in mongo[lm.TOOL_DB].list_collection_names():
            soft.append("$out wrote a collection")
        un = json.dumps(out("unauthorized"))
        if not out("unauthorized").get("_skipped") or "not authorized" not in un.lower() or \
                "error id" not in un:
            soft.append(f"unauthorized database: {mask(un)[:160]}")
        unknown = lm.tool_result(steps.get("unknown_collection"))
        if unknown.get("count") != 0:
            soft.append(f"unknown collection counted {unknown.get('count')} (MongoDB reads a "
                        "missing collection as empty)")
        slow = steps.get("slow_server") or {}
        if not out("slow_server").get("_skipped"):
            soft.append(f"the hung server's step was not failed: {mask(out('slow_server'))[:160]}")
        from datetime import datetime

        s_at, f_at = slow.get("started_at"), slow.get("finished_at") or slow.get("completed_at")
        if s_at and f_at:
            took = (datetime.fromisoformat(str(f_at)) -
                    datetime.fromisoformat(str(s_at))).total_seconds()
            evidence["slow_server_s"] = round(took, 1)
            if took < 15:
                soft.append(f"the hung-server step gave up after {took:.0f}s: 3 attempts x 10 s "
                            "with backoff cannot have run")
            if took > 240:
                soft.append(f"the hung-server step took {took:.0f}s (unbounded)")
        comp = out("compensate")
        evidence["compensation"] = comp
        if sorted(comp.get("compensated") or []) != ["bad_write", "slow_server", "unauthorized"]:
            soft.append(f"compensation saw {comp.get('compensated')}")
    finally:
        with contextlib.suppress(Exception):
            mongo[lm.TOOL_DB].drop_collection(orders)
            mongo[lm.TOOL_DB].drop_collection(f"{orders}_pwn")
    assert not soft, "; ".join(soft)


# ── MONGO-FAIL-APPROVAL-TIMEOUT ─────────────────────────────────────────────


@pytest.mark.scenario("MONGO-FAIL-APPROVAL-TIMEOUT")
def test_approval_timeout_never_auto_proceeds(api: LiveAPI, cleanup: Any,
                                              evidence: dict[str, Any], mongo: Any) -> None:
    """A 1-minute gate (timeout_action: escalate) left alone: the request is escalated,
    the run keeps waiting and the gated write never happens; a late reject ends it."""
    t = tag()
    ledger = f"refund_ledger_{t}"
    server = lm.register_connector(api, cleanup, f"commerce-db-{t}", lm.tool_dsn())
    wf_id = wfx.import_yaml(api, cleanup, wfm.approval_timeout_yaml(
        f"rw-refund-timeout-{t}", server_id=server["server_id"], ledger=ledger))
    run_id = wfx.trigger_run(api, cleanup, wf_id)
    soft: list[str] = []
    try:
        run = wfx.wait_status(api, run_id, {"waiting_hitl"}, wfx.GATE_TIMEOUT)
        assert run.get("status") == "waiting_hitl", f"run never reached the gate: {run}"
        approval = wfx.find_approval(api, run_id)
        rid = str(approval["request_id"])
        started = time.monotonic()

        def state() -> dict[str, Any]:
            return dict(api.json_ok("GET", f"{wfx.V1}/approvals/{rid}"))

        try:
            escalated = wait_until(state, timeout=SWEEP_WAIT, interval=15,
                                   desc="the timed-out approval to be escalated",
                                   done=lambda a: "escalat" in json.dumps(a).lower()
                                   and (a.get("escalation_level") or a.get("escalated_at")
                                        or a.get("status") == "escalated"))
            evidence["escalated_after_s"] = round(time.monotonic() - started, 1)
        except AssertionError:
            escalated = state()
            soft.append(f"no escalation {SWEEP_WAIT:.0f}s after a 1-minute timeout")
        evidence["approval_after_timeout"] = {k: escalated.get(k) for k in (
            "status", "escalation_level", "escalated_at", "action_taken", "expires_at")}
        if escalated.get("status") in ("approved",) or escalated.get("action_taken") == "approve":
            soft.append("the timed-out approval was auto-approved")
        run = wfx.get_run(api, run_id)
        if run.get("status") != "waiting_hitl":
            soft.append(f"after the timeout the run is {run.get('status')} (expected still "
                        "waiting for a human)")
        if mongo[lm.TOOL_DB][ledger].count_documents({"run_id": run_id}):
            soft.append("the gated write ran without an approval")
        wfx.decide(api, rid, "reject", "Refund window closed without finance sign-off.")
        final = wfx.wait_status(api, run_id, {"failed", "rejected"}, 120)
        evidence["final"] = final.get("status")
        if final.get("status") == "complete":
            soft.append("the rejected run completed")
        if mongo[lm.TOOL_DB][ledger].count_documents({"run_id": run_id}):
            soft.append("the rejected run wrote the refund")
    finally:
        with contextlib.suppress(Exception):
            mongo[lm.TOOL_DB].drop_collection(ledger)
    assert not soft, "; ".join(soft)


# ── MONGO-FAIL-CANCEL ───────────────────────────────────────────────────────


@pytest.mark.scenario("MONGO-FAIL-CANCEL")
def test_cancel_mid_run_stops_side_effects(api: LiveAPI, cleanup: Any,
                                           evidence: dict[str, Any], mongo: Any) -> None:
    t = tag()
    ledger = f"dispatch_ledger_{t}"
    server = lm.register_connector(api, cleanup, f"commerce-db-{t}", lm.tool_dsn())
    wf_id = wfx.import_yaml(api, cleanup, wfm.two_writes_yaml(
        f"rw-dispatch-cancel-{t}", server_id=server["server_id"], ledger=ledger, wait="2m"))
    run_id = wfx.trigger_run(api, cleanup, wf_id)
    col = mongo[lm.TOOL_DB][ledger]
    soft: list[str] = []
    try:
        def booked() -> dict[str, Any]:
            for item in wfx.pending_for_run(api, run_id):
                if item.get("step_id") == "book":
                    wfx.decide(api, str(item["request_id"]), "approve", "Carrier slot confirmed.")
            return {"run": wfx.get_run(api, run_id),
                    "n": col.count_documents({"run_id": run_id, "n": 1})}

        state = wait_until(booked, timeout=wfx.GATE_TIMEOUT, interval=4,
                           desc="the first write done and the run in its wait step",
                           done=lambda s: s["n"] == 1 or str(s["run"].get("status"))
                           in wfx.TERMINAL)
        evidence["before_cancel"] = {"status": state["run"].get("status"), "writes": state["n"]}
        assert state["n"] == 1, f"the first write never happened: {state['run']}"
        resp = api.post(f"{wfx.V1}/runs/{run_id}/cancel")
        evidence["cancel_http"] = resp.status_code
        if resp.status_code not in (200, 202):
            soft.append(f"cancel -> {resp.status_code}: {mask(resp.text)[:160]}")
        final = wfx.wait_status(api, run_id, {"cancelled"}, 120)
        evidence["final"] = final.get("status")
        if final.get("status") != "cancelled":
            soft.append(f"run ended {final.get('status')} instead of cancelled")
        time.sleep(150)  # past the 2-minute carrier window
        writes = col.count_documents({"run_id": run_id})
        later = [i for i in wfx.pending_for_run(api, run_id) if i.get("step_id") == "confirm"]
        steps = wfx.get_steps(api, run_id)
        evidence.update(writes_after=writes, confirm_approvals=len(later),
                        steps={k: v.get("status") for k, v in steps.items()},
                        run_after=wfx.get_run(api, run_id).get("status"))
        if writes != 1:
            soft.append(f"{writes} ledger writes after the cancel (expected exactly the first)")
        if later:
            soft.append("the cancelled run still raised an approval for its second write")
        if steps.get("confirm", {}).get("status") == "complete":
            soft.append("the confirm step ran on a cancelled run")
        if evidence["run_after"] != "cancelled":
            soft.append(f"the cancelled run became {evidence['run_after']}")
    finally:
        with contextlib.suppress(Exception):
            col.drop()
    assert not soft, "; ".join(soft)
