"""SCALE-MONGO-PIPELINE: a large MongoDB Source synced and queried under load, all real.

Opt-in: ``RW_SCALE=1``. ``RW_SCALE_DOCS`` (default 100,000) realistic order documents
(``commerce_seed.scale_docs``, deterministic, three planted needles) are bulk-inserted
into the real ``rw-mongo`` replica set, synced through the stack's MongoDB Source with
its REAL embedder, then searched by ``RW_SCALE_CONCURRENCY`` (default 50) concurrent
clients (``RW_SCALE_QUERIES``, default 3,000 queries).

Embedder (nothing is mocked; there is no fake embedder): ``RW_SCALE_EMBEDDER=onprem``
(default) requires the stack's active embedder to be the on-prem
``Qwen/Qwen3-Embedding-0.6B`` (``RW_ONPREM_EMBED_URL``, configured on the stack with
``ONPREM_EMBEDDING_BASE_URL`` / the Model Registry) and SKIPS saying so otherwise;
``RW_SCALE_EMBEDDER=stack`` accepts whatever real provider the stack runs. The report
labels every run with the embedder provider / model / dimension it used.

Measured: seed rate, ingestion throughput (docs/s, chunks/s), end-to-end time, the
indexing curve, error / DLQ rate, exactness of the 100k-document inventory (no missing,
no duplicates), search p50 / p95 / p99 / error rate under concurrency, needles found,
and tenant isolation under load (``RW_SECOND_TENANT_*`` hammering the collection id).
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from typing import Any

import pytest

from tests.real_world import commerce_seed as cs
from tests.real_world import live_mongo as lm
from tests.real_world import onprem as op
from tests.real_world import source_jobs as sj
from tests.real_world import sources as srcs
from tests.real_world.helpers import LiveAPI, env_float, load_api_key, mask, tag
from tests.real_world.loadgen import error_rate, latency_summary, run_load
from tests.real_world.metrics import norm, record

SCALE = os.getenv("RW_SCALE") == "1"
DOCS = int(os.getenv("RW_SCALE_DOCS", "100000"))
CONCURRENCY = int(os.getenv("RW_SCALE_CONCURRENCY", "50"))
QUERIES = int(os.getenv("RW_SCALE_QUERIES", "3000"))
EMBEDDER_MODE = os.getenv("RW_SCALE_EMBEDDER", "onprem").strip().lower()
SYNC_TIMEOUT = env_float("RW_SCALE_SYNC_TIMEOUT", 14400)
DB = lm.SOURCE_DB


def embedder_label(info: dict[str, Any]) -> dict[str, Any]:
    model = str(info.get("model") or "")
    return {"mode": EMBEDDER_MODE, "provider": info.get("provider"), "model": model,
            "dimension": info.get("dimension"),
            "kind": "on-prem (real)" if "qwen3-embedding" in model.lower() else "stack (real)"}


QUERY_TEMPLATES = [
    "{title} order for the {city} region", "status of order ORD-{n:06d}",
    "{title} shipped from the {city} hub", "recyclable cartons {city} surface dispatch",
    "orders with coupon FEST{c} in {city}",
]


def scale_query(i: int, needles: dict[int, str]) -> tuple[str, str | None]:
    """The i-th load query and, for every 50th, the needle text it must find."""
    if i % 50 == 0 and needles:
        n = sorted(needles)[(i // 50) % len(needles)]
        return cs.SCALE_NEEDLES[n].replace("Needle: ", ""), needles[n]
    tpl = QUERY_TEMPLATES[i % len(QUERY_TEMPLATES)]
    _, title = cs.PRODUCTS[i % len(cs.PRODUCTS)]
    return tpl.format(title=title, city=cs.CITIES[i % len(cs.CITIES)],
                      n=1 + (i * 37) % max(1, DOCS), c=10 + i % 20), None


@pytest.mark.scenario("SCALE-MONGO-PIPELINE")
@pytest.mark.skipif(not SCALE, reason="opt-in: set RW_SCALE=1 (syncs RW_SCALE_DOCS=100,000 "
                    "MongoDB documents through the real embedder, then load-tests search)")
def test_scale_mongo_sync_and_search(api: LiveAPI, enterprise_api: LiveAPI | None,
                                     second_tenant_api: LiveAPI | None, cleanup: Any,
                                     embedder_info: dict[str, Any],
                                     evidence: dict[str, Any]) -> None:
    label = embedder_label(embedder_info)
    evidence["embedder"] = label
    if EMBEDDER_MODE == "onprem" and "qwen3-embedding" not in label["model"].lower():
        pytest.skip(f"RW_SCALE_EMBEDDER=onprem but the stack embeds with {label['model']!r} "
                    f"({label['provider']}): point the stack at {op.EMBED_URL} "
                    f"({op.EMBED_MODEL}, e.g. ONPREM_EMBEDDING_BASE_URL) or set "
                    "RW_SCALE_EMBEDDER=stack to measure the configured real provider")
    if EMBEDDER_MODE not in ("onprem", "stack"):
        pytest.skip(f"RW_SCALE_EMBEDDER={EMBEDDER_MODE!r}: use onprem or stack (no fake "
                    "embedder exists)")
    client = enterprise_api or api
    evidence["tenant"] = "enterprise" if enterprise_api else "default"
    mongo = lm.seed_client()
    t = tag()
    name = f"scale_orders_{t}"
    coll = mongo[DB][name]
    try:
        started = time.monotonic()
        batch: list[dict[str, Any]] = []
        for doc in cs.scale_docs(DOCS):
            batch.append(doc)
            if len(batch) == 5000:
                coll.insert_many(batch, ordered=False)
                batch = []
        if batch:
            coll.insert_many(batch, ordered=False)
        seed_s = time.monotonic() - started
        assert coll.estimated_document_count() >= DOCS

        cid = srcs.create_collection(client, cleanup, "rw-scale-mongo")
        cfg = lm.reader_config([name], batch_size=1000, max_documents_per_sync=DOCS + 10)
        sid = lm.create_source(client, cleanup, cid, cfg)["id"]
        evidence.update(collection_id=cid, source_id=sid)
        curve: list[tuple[float, int]] = []
        stop = threading.Event()
        job_id = sj.trigger(client, sid)
        sync_started = time.monotonic()

        def sample() -> None:
            while not stop.is_set():
                with contextlib.suppress(Exception):
                    j = sj.job(client, sid, job_id) or {}
                    curve.append((round(time.monotonic() - sync_started, 1),
                                  int(j.get("docs_indexed") or 0)))
                stop.wait(30)

        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        try:
            job = sj.wait_job(client, sid, job_id, timeout=SYNC_TIMEOUT)
        finally:
            stop.set()
            sampler.join(timeout=60)
        e2e_s = time.monotonic() - sync_started
        dlq = sj.dlq(client, sid)
        expected = {cs.doc_path(DB, name, str(cs.object_id(cs.BASE_TS + i, f"scale:{i}")))
                    for i in range(1, DOCS + 1)}
        inv = lm.inventory(client, sid, cid, expected)
        indexed = int(job.get("docs_indexed") or 0)
        chunks = int(job.get("chunks_created") or inv["chunks_by_documents"])
        health = client.get("/health").json()
        evidence.update(sync=job, curve=curve[-40:], dlq=len(dlq),
                        inventory={k: v for k, v in inv.items() if k != "content_hash"},
                        queue_depth=health.get("queues") or "not exposed by the API")

        # Concurrent retrieval load (+ another tenant hammering the same collection id).
        needles = cs.needle_keys(DOCS)
        needle_tails = {k: cs.doc_path(DB, name, v) for k, v in needles.items()}

        def make(i: int) -> tuple[str, str, dict[str, Any]]:
            q, _ = scale_query(i, needles)
            return "GET", "/knowledge/search", {"params": {"q": q, "collection_id": cid,
                                                           "top_k": 10}}

        def check(i: int, resp: Any) -> str | None:
            q, want = scale_query(i, needle_tails)
            body = resp.json()
            hits = body if isinstance(body, list) else body.get("results", [])
            if want and not any(cs.hit_url(h).endswith(want) for h in hits):
                return f"needle missing for {q[:40]!r}"
            if cs.duplicate_hits(hits):
                return "duplicate chunks in one result list"
            return None

        breaches: list[str] = []
        other_rows: list[dict[str, Any]] = []

        def intruder() -> None:
            if second_tenant_api is None:
                return
            key = str(second_tenant_api.client.headers.get("X-API-Key", ""))
            def leaked(i: int, resp: Any) -> str | None:
                body = resp.json()
                hits = body if isinstance(body, list) else body.get("results", [])
                return f"{len(hits)} hits from another tenant's collection" if hits else None

            rows, _ = run_load(key, make, 200, 4, check=leaked)
            other_rows.extend(rows)

        intr = threading.Thread(target=intruder, daemon=True)
        intr.start()
        rows, load_s = run_load(load_api_key() if client is api else str(
            client.client.headers.get("X-API-Key", "")), make, QUERIES, CONCURRENCY,
            check=check)
        intr.join(timeout=600)
        if second_tenant_api is not None:
            key = str(second_tenant_api.client.headers.get("X-API-Key", ""))
            import httpx

            from tests.real_world.helpers import BASE_URL

            with httpx.Client(base_url=BASE_URL, headers={"X-API-Key": key}, timeout=30) as c:
                r = c.get("/knowledge/search", params={"q": norm(cs.SCALE_NEEDLES[17]),
                                                       "collection_id": cid, "top_k": 10})
                if r.status_code == 200 and r.json():
                    breaches.append(f"tenant B search returned {len(r.json())} hits")
            breaches += [f"tenant B request {x['i']}: {x['problem']}" for x in other_rows
                         if x.get("problem")][:3]
        lat = latency_summary([r["ms"] for r in rows if r.get("status") == 200])
        errs = error_rate(rows)
        problems = [r["problem"] for r in rows if r.get("problem")]
        record(evidence, embedder_kind=label["kind"], embedder_model=label["model"],
               docs=DOCS, seed_docs_per_s=round(DOCS / seed_s, 1), sync_e2e_s=round(e2e_s, 1),
               docs_per_s=round(indexed / e2e_s, 2) if e2e_s else 0.0,
               chunks_per_s=round(chunks / e2e_s, 2) if e2e_s else 0.0,
               docs_failed=int(job.get("docs_failed") or 0), dlq_entries=len(dlq),
               dlq_rate=round(len(dlq) / DOCS, 5), queries=QUERIES, concurrency=CONCURRENCY,
               search_qps=round(QUERIES / load_s, 1), search_latency_ms=lat, **{
                   f"search_{k}": v for k, v in errs.items()},
               correctness_problems=len(problems), isolation_breaches=len(breaches))
        evidence["first_problems"] = problems[:5]
        evidence["isolation"] = breaches[:5] or ("clean" if second_tenant_api else
                                                 "skipped: no RW_SECOND_TENANT_*")
        soft: list[str] = []
        if str(job.get("status")).lower() not in sj.COMPLETED:
            soft.append(f"scale sync {job.get('status')}: {mask(job.get('error_message'))[:160]}")
        soft += lm.exactness_problems(inv)
        if len(dlq) / DOCS > env_float("RW_SCALE_MAX_DLQ_RATE", 0.001):
            soft.append(f"DLQ rate {len(dlq) / DOCS:.4f} above the bound")
        rate = indexed / e2e_s if e2e_s else 0.0
        if rate < env_float("RW_SCALE_MIN_DOCS_PER_S", 5.0):
            soft.append(f"ingestion throughput {rate:.1f} docs/s below RW_SCALE_MIN_DOCS_PER_S")
        if (lat.get("p95") or 0) > env_float("RW_SCALE_MAX_P95_MS", 5000.0):
            soft.append(f"search p95 {lat.get('p95')} ms above RW_SCALE_MAX_P95_MS")
        if errs["error_rate"] > env_float("RW_SCALE_MAX_ERROR_RATE", 0.01):
            soft.append(f"search error rate {errs['error_rate']} above RW_SCALE_MAX_ERROR_RATE")
        if problems:
            soft.append(f"{len(problems)} wrong results under load: {problems[:3]}")
        if breaches:
            soft.append(f"tenant isolation breached under load: {breaches[:3]}")
        assert not soft, "; ".join(soft)
    finally:
        with contextlib.suppress(Exception):
            coll.drop()
        mongo.close()
