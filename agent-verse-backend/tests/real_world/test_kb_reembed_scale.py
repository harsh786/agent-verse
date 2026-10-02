"""KB-REEMBED-MIGRATION and KB-SCALE-SMOKE.

KB-REEMBED-MIGRATION: a collection is re-embedded (POST /knowledge/collections/{id}/
re-embed) while a reader thread keeps querying it. No query may fail or come back
empty during the migration, and afterwards the same questions return the same top
documents (stable results) with hit@5 not lower than before.

KB-SCALE-SMOKE (opt-in, RW_SCALE=1): ~5,000 small documents ingested concurrently
(RW_SCALE_DOCS, RW_SCALE_CONCURRENCY). Asserts no ingest errors, a throughput floor
(RW_SCALE_MIN_DOCS_PER_S), bounded API latency for a reader during the ingest
(RW_SCALE_MAX_P95_MS) and that paginating the document listing reaches every
document. Uses RW_ENTERPRISE_API_KEY when given (the free plan's request budget
and document quota are too small for 5,000 documents).
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from tests.real_world import kb
from tests.real_world.corpus import build_docx, build_html, build_md, build_pptx, doc_rng
from tests.real_world.corpus import load_questions as _questions
from tests.real_world.helpers import LiveAPI, env_float, mask, tag, wait_until
from tests.real_world.metrics import Stopwatch, hit_at_k, percentiles, record, source_of, throughput
from tests.real_world.retrieval_eval import question_rank

REEMBED_TIMEOUT = float(os.getenv("RW_REEMBED_TIMEOUT", "600"))


@pytest.mark.scenario("KB-REEMBED-MIGRATION")
def test_kb_reembed_while_querying(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    cid = kb.create_collection(api, "rw-reembed-live")
    cleanup("DELETE", f"/knowledge/collections/{cid}")
    evidence["collection_id"] = cid
    docs = [build_docx(doc_rng("docx")), build_pptx(doc_rng("pptx")),
            build_html(doc_rng("html")), build_md(doc_rng("md"))]
    for d in docs:
        up = kb.upload(api, cid, d)
        assert up["http"] in (200, 201) and up["chunks"], f"{d.filename}: {mask(up)[:300]}"
    names = {d.filename for d in docs}
    questions = [q for q in _questions() if q["kind"] != "cross_doc"
                 and set(q["expected_sources"]) & names]
    assert len(questions) >= 8, f"only {len(questions)} questions for the re-embed subset"

    def snapshot() -> tuple[dict[str, str], list[int | None]]:
        tops: dict[str, str] = {}
        ranks: list[int | None] = []
        for q in questions:
            hits = kb.search(api, cid, q["question"], top_k=5)
            tops[q["id"]] = source_of(hits[0]) if hits else ""
            ranks.append(question_rank(hits, q))
        return tops, ranks

    top_before, ranks_before = snapshot()
    hit5_before = hit_at_k(ranks_before, 5)

    stop = threading.Event()
    reads: list[dict[str, Any]] = []

    def reader() -> None:
        i = 0
        while not stop.is_set():
            q = questions[i % len(questions)]
            started = time.monotonic()
            try:
                resp = api.get("/knowledge/search", params={"q": q["question"],
                                                           "collection_id": cid, "top_k": 5})
                ok = resp.status_code == 200 and bool(resp.json())
                reads.append({"ok": ok, "http": resp.status_code,
                              "ms": (time.monotonic() - started) * 1000})
            except Exception as exc:  # a dropped connection is a failed query too
                reads.append({"ok": False, "http": type(exc).__name__,
                              "ms": (time.monotonic() - started) * 1000})
            i += 1
            time.sleep(0.25)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        started = api.post(f"/knowledge/collections/{cid}/re-embed")
        evidence["reembed_http"] = started.status_code
        assert started.status_code == 202, f"re-embed -> {started.status_code}: " \
            f"{mask(started.text[:300])}"
        progress = wait_until(
            lambda: api.json_ok("GET", f"/knowledge/collections/{cid}/re-embed"),
            timeout=REEMBED_TIMEOUT, interval=2, desc="re-embed to finish",
            done=lambda p: str(p.get("status")) in ("completed", "failed", "cancelled"))
    finally:
        time.sleep(2)
        stop.set()
        thread.join(timeout=30)
    evidence["reembed"] = {k: progress.get(k) for k in ("status", "total", "processed",
                                                        "model", "error")}
    failed = [r for r in reads if not r["ok"]]
    record(evidence, queries_during_reembed=len(reads), failed_queries=len(failed),
           query_latency_ms=percentiles([r["ms"] for r in reads]))
    assert progress.get("status") == "completed", f"re-embed ended {mask(progress)[:300]}"
    assert len(reads) >= 3, f"only {len(reads)} queries ran during the re-embed"
    assert not failed, f"{len(failed)} of {len(reads)} queries failed or came back empty " \
        f"during the re-embed: {failed[:5]}"
    top_after, ranks_after = snapshot()
    stable = sum(1 for k in top_before if top_before[k] == top_after.get(k)) / len(top_before)
    hit5_after = hit_at_k(ranks_after, 5)
    record(evidence, top1_stability=stable, hit_at_5_before=hit5_before,
           hit_at_5_after=hit5_after)
    evidence["changed_top1"] = {k: [top_before[k], top_after.get(k)] for k in top_before
                                if top_before[k] != top_after.get(k)}
    assert stable >= env_float("RW_REEMBED_STABILITY_MIN", 0.9), (
        f"only {stable:.0%} of questions keep their top document after re-embedding"
    )
    assert hit5_after >= hit5_before, f"hit@5 dropped {hit5_before:.2f} -> {hit5_after:.2f}"


@pytest.mark.scenario("KB-SCALE-SMOKE")
@pytest.mark.skipif(os.getenv("RW_SCALE") != "1",
                    reason="opt-in: set RW_SCALE=1 (ingests ~5,000 documents; best with "
                           "RW_ENTERPRISE_API_KEY)")
def test_kb_scale_smoke(api: LiveAPI, enterprise_api: LiveAPI | None,
                        evidence: dict[str, Any]) -> None:
    client = enterprise_api or api
    evidence["tenant"] = "enterprise" if enterprise_api else "default"
    total = int(os.getenv("RW_SCALE_DOCS", "5000"))
    workers = int(os.getenv("RW_SCALE_CONCURRENCY", "8"))
    cid = kb.create_collection(client, "rw-scale")
    evidence["collection_id"] = cid
    run = tag()
    errors: list[str] = []
    watch = Stopwatch()
    stop = threading.Event()

    def ingest(i: int) -> None:
        body = {"collection_id": cid, "source_type": "text",
                "content": (f"Scale note {run}-{i:05d}. Consignment LX-{i:05d} left hub "
                            f"{['Hosur', 'Pune', 'Nagpur'][i % 3]} on day {i % 28 + 1} carrying "
                            f"{(i * 37) % 900 + 50} kg of {['spares', 'pharma', 'textiles'][i % 3]}."),
                "metadata": {"rw_run": run, "seq": i}}
        started = time.monotonic()
        try:
            resp = client.post("/knowledge/ingest", json=body, timeout=120)
            if resp.status_code not in (200, 201):
                errors.append(f"#{i}: {resp.status_code} {mask(resp.text[:120])}")
        except Exception as exc:
            errors.append(f"#{i}: {type(exc).__name__}")
        watch.add("ingest", (time.monotonic() - started) * 1000)

    def probe() -> None:
        while not stop.is_set():
            with watch.time("probe_health"):
                client.get("/health")
            with watch.time("probe_search"):
                client.get("/knowledge/search", params={"q": "consignment pharma Hosur",
                                                       "collection_id": cid, "top_k": 3})
            time.sleep(1)

    prober = threading.Thread(target=probe, daemon=True)
    prober.start()
    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(ingest, range(total)))
    finally:
        elapsed = time.monotonic() - started
        stop.set()
        prober.join(timeout=30)
    rate = throughput(total - len(errors), elapsed)
    summary = watch.summary()
    record(evidence, docs=total, errors=len(errors), elapsed_s=round(elapsed, 1),
           docs_per_s=rate, latency_ms=summary, rate_limited_retries=client.rate_limited)
    evidence["first_errors"] = errors[:10]
    try:
        assert not errors, f"{len(errors)} of {total} ingests failed: {errors[:5]}"
        assert rate >= env_float("RW_SCALE_MIN_DOCS_PER_S", 5.0), (
            f"throughput {rate} docs/s below the floor"
        )
        p95 = summary.get("probe_search", {}).get("p95") or 0
        assert p95 <= env_float("RW_SCALE_MAX_P95_MS", 5000.0), (
            f"search p95 {p95} ms during ingest exceeds the bound"
        )
        docs, reported = kb.all_documents(client, cid)
        record(evidence, listed=len(docs), reported_total=reported)
        assert len(docs) == total, f"paginating the listing reached {len(docs)} of {total}"
        assert len({d.get('id') for d in docs}) == total, "pagination returned duplicates"
    finally:
        client.delete(f"/knowledge/collections/{cid}")
