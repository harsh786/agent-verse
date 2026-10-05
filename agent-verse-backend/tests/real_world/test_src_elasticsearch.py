"""SRC-ES-*: Elasticsearch as a knowledge source on the live stack (P1c / A5).

A throwaway Elasticsearch 8 (``rw-es``, security on, HTTP) on the compose network,
operator-allowlisted for the connector egress guard, labelled ``p1c=live-test``.
The stack connects as ``rwreader`` (role ``rw_reader``: ``read`` +
``view_index_metadata`` on ``rw-*`` only) or with an API key derived from it;
seeding runs from this host as ``elastic``.

Data: a support desk's event logs in monthly indices (``rw-<t>-logs-2026.09``,
``…-2026.10``, read through the pattern ``rw-<t>-logs-*``; some ``_id`` values
exist in both months) and a knowledge-base index with an explicit mapping
(text, keyword, date, object, nested). More documents than one page.

Environment (else SKIPPED): ``RW_ES_PASSWORD`` (elastic), ``RW_ES_READER_PASSWORD``
(+ ``RW_ES_SEED_URL`` http://127.0.0.1:59200, ``RW_ES_URL`` http://rw-es:9200).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from tests.real_world import kb
from tests.real_world import source_jobs as sj
from tests.real_world import sources as srcs
from tests.real_world.helpers import LiveAPI, mask, register_secret, tag, wait_until
from tests.real_world.metrics import norm, record

FAMILY = "nosql_database"
LOGS_PER_MONTH = int(os.getenv("RW_ES_LOGS_PER_MONTH", "350"))
BATCH = int(os.getenv("RW_ES_BATCH", "200"))
FACTS = {
    "log": "Courier app crashed on Android 14 when scanning a damaged QR at the Hosur hub",
    "log_oct": "Refund webhook retried 7 times for order 55102 before the gateway answered",
    "kb": "To reset a locked handheld scanner hold F2 and the trigger for ten seconds",
    "nested": "Comment by Thandiwe Mokoena: the workaround also fixes the Zebra TC52 units",
}
T0 = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)


def _env(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        pytest.skip(f"needs {name}: the throwaway Elasticsearch (see module docstring)")
    register_secret(value)
    return value


def _es_url() -> str:
    return os.getenv("RW_ES_URL", "http://rw-es:9200")


class Seeder:
    def __init__(self) -> None:
        self.client = httpx.Client(base_url=os.getenv("RW_ES_SEED_URL", "http://127.0.0.1:59200"),
                                   auth=("elastic", _env("RW_ES_PASSWORD")), timeout=60)
        self.indices: list[str] = []

    def __repr__(self) -> str:
        return "Seeder()"

    def req(self, method: str, path: str, **kw: Any) -> Any:
        r = self.client.request(method, path, **kw)
        assert r.status_code < 300, f"{method} {path} -> {r.status_code}: {r.text[:300]}"
        return r.json() if r.content else {}

    def create(self, index: str, mappings: dict[str, Any] | None = None) -> None:
        body = {"settings": {"number_of_shards": 2, "number_of_replicas": 0}}
        if mappings:
            body["mappings"] = mappings
        self.req("PUT", f"/{index}", json=body)
        self.indices.append(index)

    def bulk(self, index: str, docs: list[tuple[str, dict[str, Any]]]) -> None:
        lines = []
        for doc_id, src in docs:
            lines.append(json.dumps({"index": {"_index": index, "_id": doc_id}}))
            lines.append(json.dumps(src, default=str))
        out = self.req("POST", "/_bulk?refresh=true", content=("\n".join(lines) + "\n").encode(),
                       headers={"Content-Type": "application/x-ndjson"})
        assert not out.get("errors"), str(out)[:300]

    def close(self) -> None:
        for index in self.indices:
            with contextlib.suppress(Exception):
                self.client.delete(f"/{index}")
        self.client.close()


@pytest.fixture
def es() -> Iterator[Seeder]:
    s = Seeder()
    yield s
    s.close()


def _log_doc(i: int, month: int) -> dict[str, Any]:
    ts = (T0 + dt.timedelta(days=30 * (month - 9), minutes=7 * i)).isoformat()
    return {"@timestamp": ts, "level": ["INFO", "WARN", "ERROR"][i % 3],
            "service": ["courier-app", "payments", "dispatch"][i % 3],
            "message": f"Event {i:05d} from the {['Pune', 'Kochi', 'Surat'][i % 3]} hub: "
                       f"handled ticket {(month * 10000) + i} within the SLA window."}


def _seed_logs(es: Seeder, t: str) -> dict[str, Any]:
    out: dict[str, Any] = {"indices": [], "total": 0}
    for month in (9, 10):
        index = f"rw-{t}-logs-2026.{month:02d}"
        es.create(index, {"properties": {"@timestamp": {"type": "date"},
                                         "level": {"type": "keyword"},
                                         "service": {"type": "keyword"},
                                         "message": {"type": "text"}}})
        docs = [(f"evt-{i:05d}", _log_doc(i, month)) for i in range(LOGS_PER_MONTH)]
        if month == 9:
            docs[17][1]["message"] = FACTS["log"] + ". Crash report CR-2209 attached."
        else:
            docs[23][1]["message"] = FACTS["log_oct"] + ". Gateway latency 41 s."
        es.bulk(index, docs)
        out["indices"].append(index)
        out["total"] += len(docs)
    return out


def _source(api: LiveAPI, cleanup: Any, cid: str, cfg: dict[str, Any],
            source_type: str = "elasticsearch", expect: int = 201) -> dict[str, Any]:
    return sj.create_source(api, cleanup, family=FAMILY, source_type=source_type, config=cfg,
                            collection_id=cid, expect=expect)


def _reader(**over: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {"url": _es_url(), "username": "rwreader",
                           "password": _env("RW_ES_READER_PASSWORD"), "batch_size": BATCH}
    cfg.update(over)
    return cfg


def _count(api: LiveAPI, cid: str) -> int:
    return int(kb.documents_page(api, cid, 1, 0).get("total") or 0)


def _hit_with(api: LiveAPI, cid: str, q: str, needle: str, k: int = 10) -> dict[str, Any] | None:
    for rank, h in enumerate(kb.search(api, cid, q, top_k=k), start=1):
        if norm(needle) in norm(h.get("content")):
            return {**h, "_rank": rank}
    return None


def _citation(hit: dict[str, Any]) -> str:
    meta = hit.get("metadata") or {}
    return str(hit.get("source_url") or meta.get("source_url") or hit.get("source")
               or meta.get("source") or "")


# ── SRC-ES-SYNC ─────────────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-ES-SYNC")
def test_es_index_pattern_pagination_and_incremental(api: LiveAPI, cleanup: Any,
                                                     evidence: dict[str, Any],
                                                     es: Seeder) -> None:
    """An index pattern over two monthly indices (ids repeat across them), more
    documents than a page; then new, updated (newer @timestamp) and deleted ones."""
    t = tag()
    seeded = _seed_logs(es, t)
    total = seeded["total"]
    cid = srcs.create_collection(api, cleanup, "rw-es")
    sid = _source(api, cleanup, cid, _reader(index=f"rw-{t}-logs-*",
                                             sort_field="@timestamp"))["id"]
    evidence.update(source_id=sid, collection_id=cid, indices=seeded["indices"], total=total)
    v = sj.validate(api, family=FAMILY, source_type="elasticsearch",
                    config=_reader(index=f"rw-{t}-logs-*"))
    evidence["validate"] = mask(v)[:300]
    soft: list[str] = []
    if not v.get("valid"):
        soft.append(f"validate: {mask(v.get('errors'))[:200]}")
    job1 = sj.sync(api, sid, timeout=2400)
    evidence["sync1"] = job1
    if str(job1.get("status")).lower() not in sj.COMPLETED:
        soft.append(f"sync 1 {job1.get('status')}: {job1.get('error_message')}")
    n1 = _count(api, cid)
    evidence["documents_after_sync1"] = n1
    if n1 != total:
        soft.append(f"sync 1 left {n1} documents, expected {total} (2 indices x "
                    f"{LOGS_PER_MONTH}; ids repeat across the indices)")
    ranks: dict[str, int] = {}
    for name, (q, needle, index) in {
        "september log": ("courier app crashed Android 14 damaged QR Hosur", FACTS["log"],
                          seeded["indices"][0]),
        "october log": ("refund webhook retried order 55102 gateway", FACTS["log_oct"],
                        seeded["indices"][1]),
    }.items():
        hit = _hit_with(api, cid, q, needle)
        if hit is None:
            soft.append(f"not searchable: {name}")
            continue
        ranks[name] = hit["_rank"]
        cite = _citation(hit)
        evidence.setdefault("citations", {})[name] = cite
        if index not in cite:
            soft.append(f"{name}: citation {cite!r} does not name the concrete index {index}")
    evidence["probe_ranks"] = ranks

    # Changes: 3 new, 2 updated (newer @timestamp), 2 deleted.
    later = (T0 + dt.timedelta(days=70)).isoformat()
    sep = seeded["indices"][0]
    es.bulk(seeded["indices"][1], [
        (f"new-{k}", {"@timestamp": later, "level": "ERROR", "service": "dispatch",
                      "message": (f"New incident {k}: Kochi dock door 4 sensor offline, "
                                  "trucks rerouted to door 6.")}) for k in range(3)])
    es.bulk(sep, [("evt-00040", {"@timestamp": later, "level": "WARN", "service": "payments",
                                 "message": "Updated: UPI collect requests time out for "
                                            "Federal Bank customers after 22:00."}),
                  ("evt-00041", {"@timestamp": later, "level": "INFO", "service": "payments",
                                 "message": "Updated: settlement file SF-0931 reconciled."})])
    for doc_id in ("evt-00050", "evt-00051"):
        es.req("DELETE", f"/{sep}/_doc/{doc_id}?refresh=true")
    job2 = sj.sync(api, sid, timeout=900)
    evidence["sync2"] = job2
    if str(job2.get("status")).lower() not in sj.COMPLETED:
        soft.append(f"sync 2 {job2.get('status')}: {job2.get('error_message')}")
    jobs_after = [j for j in sj.jobs(api, sid)
                  if str(j.get("created_at") or "") > str(job1.get("started_at") or "")
                  and not sj._same(j.get("job_id"), job1.get("job_id"))]
    changed = sum(int(j.get("docs_indexed") or 0) for j in jobs_after)
    evidence["jobs_after_sync1"] = [(j.get("triggered_by"), j.get("status"),
                                     j.get("docs_indexed")) for j in jobs_after]
    if changed != 5:
        soft.append(f"{changed} documents indexed after sync 1, expected 5 (3 new + 2 updated)")
    if _hit_with(api, cid, "UPI collect requests time out Federal Bank", "Federal Bank") is None:
        soft.append("updated document's new text not searchable")
    if _hit_with(api, cid, "Kochi dock door 4 sensor offline", "door 4 sensor") is None:
        soft.append("new document not searchable")
    rec = api.post(f"/sources/{sid}/reconcile")
    evidence["reconcile_http"] = rec.status_code
    if rec.status_code != 202:
        soft.append(f"deleted documents cannot be reconciled: {rec.status_code} "
                    f"{mask(rec.text)[:200]}")
    else:
        try:
            wait_until(lambda: _count(api, cid), timeout=300, interval=5,
                       desc="deleted documents removed", done=lambda c: c == total + 3 - 2)
        except AssertionError as exc:
            soft.append(f"reconcile did not remove exactly the 2 deleted documents: {exc}")
    evidence["documents_final"] = _count(api, cid)
    record(evidence, documents=total, sync1_s=job1.get("wall_s"), sync2_s=job2.get("wall_s"),
           docs_per_s=round(n1 / max(1.0, float(job1.get("wall_s") or 1)), 2))
    assert not soft, "; ".join(soft)


# ── SRC-ES-MAPPINGS ─────────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-ES-MAPPINGS")
def test_es_mapped_kb_index(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                            es: Seeder) -> None:
    """A knowledge-base index with an explicit mapping (no @timestamp; an
    ``updated_at`` date; nested comments; an object), documents without the sort
    field, and the default sort field on an index that does not map it."""
    t = tag()
    index = f"rw-{t}-kb"
    es.create(index, {"properties": {
        "title": {"type": "text"}, "body": {"type": "text"},
        "updated_at": {"type": "date"}, "tags": {"type": "keyword"},
        "author": {"properties": {"name": {"type": "keyword"}, "team": {"type": "keyword"}}},
        "comments": {"type": "nested", "properties": {"by": {"type": "keyword"},
                                                      "text": {"type": "text"}}}}})
    docs = []
    for i in range(30):
        docs.append((f"kb-{i:03d}", {
            "title": f"How-to {i:03d}: warehouse handheld procedures",
            "body": (FACTS["kb"] + "; then re-pair over Bluetooth." if i == 4 else
                     f"Article {i:03d} explains a routine step for the {['Pune', 'Kochi'][i % 2]} "
                     "warehouse handhelds and label printers."),
            "updated_at": (T0 + dt.timedelta(hours=i)).isoformat(),
            "tags": ["handheld", "warehouse"],
            "author": {"name": "Meenakshi Sundaram", "team": "ops-enablement"},
            "comments": ([{"by": "thandiwe", "text": FACTS["nested"]}] if i == 4 else [])}))
    docs.append(("kb-draft", {"title": "Draft without a timestamp",
                              "body": "A draft article about cold-chain seals with no "
                                      "updated_at value yet; still a document."}))
    es.bulk(index, docs)
    soft: list[str] = []
    cid = srcs.create_collection(api, cleanup, "rw-es-kb")
    sid = _source(api, cleanup, cid, _reader(index=index, sort_field="updated_at"))["id"]
    job = sj.sync(api, sid, timeout=900)
    evidence["sync_updated_at"] = job
    n = _count(api, cid)
    if str(job.get("status")).lower() not in sj.COMPLETED or n != len(docs):
        soft.append(f"sort_field=updated_at: {job.get('status')} {job.get('error_message')}; "
                    f"{n} documents (expected {len(docs)}, incl. the one without updated_at)")
    if _hit_with(api, cid, "reset locked handheld scanner F2 trigger", "hold F2") is None:
        soft.append("KB body not searchable")
    if _hit_with(api, cid, "Thandiwe Mokoena workaround Zebra TC52", "Zebra TC52") is None:
        soft.append("nested comment not searchable")
    # Default sort field (@timestamp) on an index without it: honest, or works.
    cid2 = srcs.create_collection(api, cleanup, "rw-es-kb2")
    sid2 = _source(api, cleanup, cid2, _reader(index=index))["id"]
    job2 = sj.sync(api, sid2, timeout=900)
    evidence["sync_default_sort"] = job2
    n2 = _count(api, cid2)
    ok = str(job2.get("status")).lower() in sj.COMPLETED and n2 == len(docs)
    honest = str(job2.get("status")).lower() == "failed" and "@timestamp" in str(
        job2.get("error_message"))
    if not (ok or honest):
        soft.append(f"default sort field on an index without @timestamp: {job2.get('status')}, "
                    f"{n2} documents, error {job2.get('error_message')!r}")
    evidence["documents"] = {"updated_at": n, "default": n2}
    assert not soft, "; ".join(soft)


# ── SRC-ES-AUTH-FAILURES ────────────────────────────────────────────────────


@pytest.mark.scenario("SRC-ES-AUTH-FAILURES")
def test_es_auth_and_honest_failures(api: LiveAPI, cleanup: Any, evidence: dict[str, Any],
                                     es: Seeder) -> None:
    t = tag()
    index = f"rw-{t}-kb"
    es.create(index)
    es.bulk(index, [(f"a-{i}", {"@timestamp": (T0 + dt.timedelta(minutes=i)).isoformat(),
                                "message": f"Shift handover note {i}: the Ranchi depot gate "
                                           "opens at 05:30 for line-haul trucks."})
                    for i in range(5)])
    secret = f"secret-{t}"
    es.create(secret)
    es.bulk(secret, [("s-1", {"@timestamp": T0.isoformat(),
                              "message": "Payroll export that the reader may never see."})])
    key = es.req("POST", "/_security/api_key", json={
        "name": f"rw-p1c-{t}", "expiration": "1d",
        "role_descriptors": {"rw": {"cluster": ["monitor"], "indices": [
            {"names": ["rw-*"], "privileges": ["read", "view_index_metadata"]}]}}})
    encoded = str(key.get("encoded") or "")
    register_secret(encoded)
    soft: list[str] = []
    out: dict[str, Any] = {}
    cid = srcs.create_collection(api, cleanup, "rw-es-auth")
    # API key auth.
    sid = _source(api, cleanup, cid, {"url": _es_url(), "index": index, "api_key": encoded})["id"]
    job = sj.sync(api, sid, timeout=600)
    out["api_key"] = (job.get("status"), job.get("docs_indexed"), mask(job.get("error_message")))
    if str(job.get("status")).lower() not in sj.COMPLETED or _count(api, cid) != 5:
        soft.append(f"API key auth: {job.get('status')} {mask(job.get('error_message'))}")
    failures = {
        "wrong password": (_reader(index=index, password="not-the-password-0000"),
                           ("401", "unauthor", "authentication")),
        "no credentials": ({"url": _es_url(), "index": index}, ("401", "unauthor",
                                                                "authentication", "missing")),
        "revoked / bogus API key": ({"url": _es_url(), "index": index,
                                     "api_key": "Ym9ndXM6a2V5"}, ("401", "unauthor", "api key")),
        "index the reader may not read": (_reader(index=secret), ("403", "forbidden",
                                                                 "unauthorized", "privilege")),
        "missing index": (_reader(index=f"rw-{t}-nope"), ("404", "index_not_found", "no such")),
        "unreachable port": (_reader(index=index, url="http://rw-es:9299"),
                             ("connect", "refused", "unreachable", "timed out")),
    }
    for name, (cfg, words) in failures.items():
        started = time.monotonic()
        v = sj.validate(api, family=FAMILY, source_type="elasticsearch", config=cfg)
        sid_f = _source(api, cleanup, cid, cfg)["id"]
        jf = sj.sync(api, sid_f, timeout=300)
        err = f"{jf.get('error_message')} {v.get('errors')}".lower()
        out[name] = {"validate": v.get("valid"), "sync": jf.get("status"),
                     "indexed": jf.get("docs_indexed"), "s": round(time.monotonic() - started, 1),
                     "error": mask(jf.get("error_message"))[:220]}
        if str(jf.get("status")).lower() in sj.COMPLETED:
            soft.append(f"{name}: sync completed ({jf.get('docs_indexed')} indexed)")
        elif not any(w in err for w in words):
            soft.append(f"{name}: no reason among {words}: {mask(err)[:200]}")
        if name != "unreachable port" and v.get("valid"):
            soft.append(f"{name}: validate said valid")
    refused = {
        "platform postgres": "http://postgres:5432",
        "platform redis": "http://redis:6379",
        "metadata": "http://169.254.169.254/latest/meta-data",
        "localhost": "http://localhost:9200",
        "backend": "http://backend:8000",
        "file": "file:///etc/passwd",
    }
    codes = {}
    for name, url in refused.items():
        r = api.post("/sources", json={"name": f"rw-es-ref-{tag()}", "family": FAMILY,
                                       "source_type": "elasticsearch",
                                       "connection_config": {"url": url, "index": "x"},
                                       "collection_id": cid})
        codes[name] = r.status_code
        if r.status_code < 300:
            cleanup("DELETE", f"/sources/{r.json().get('source_id')}")
    out["refusals"] = codes
    if any(c != 422 for c in codes.values()):
        soft.append(f"internal destinations not refused on save: {codes}")
    evidence.update(out)
    assert not soft, "; ".join(soft)
