"""OCR-*: live OCR end to end with the stack's real engines (Tesseract + its vision model).

Deterministic scanned documents (``ocr_fixtures``: multi-page, rotated, low-quality,
two-column, a ruled table, Hindi, a JPG UI screenshot, a handwritten-looking note, a
readable page next to a noise page, and refusal inputs) go through:

* OCR-EXTRACT-PROVENANCE — ``POST /ocr/extract``: every planted fact read; per-page
  provenance (``page_engines`` one entry per page, ``tesseract`` / ``llm_vision``,
  ``vision_pages`` and ``engine_used`` consistent), no failed pages, not degraded.
* OCR-PARTIAL-AND-REFUSALS — a readable + noise PDF is a 200 (degraded with the failed
  page named, or the blank page in ``empty_pages``), never a 502 / 500; a truncated
  image and an unrasterisable PDF are refused honestly (502 / 4xx — never a silent empty
  200); an upload above ``OCR_MAX_UPLOAD_BYTES`` is a fast 413; an unsupported type 4xx.
* OCR-BATCH — ``POST /ocr/batch`` with per-item reasons: good items persisted to the KB
  (``persist_to_kb``), a broken image and a persist without a collection fail with their
  own reason, totals add up, 11 documents is a 422; persisted text is searchable.
* OCR-KB-UPLOAD — scans uploaded through ``/knowledge/ingest/file`` (OCR'd pages listed)
  and known-answer retrieval + RAG over the OCR'd text.
* OCR-VISION-FAILOVER — the Model Registry's OCR order pointed at a real unreachable
  vision endpoint: the page keeps the Tesseract text (or fails over to another real vision
  model), never a 5xx. Needs a platform admin (registry).
* OCR-PARALLEL — N concurrent OCR jobs vs serial: all complete, each result holds only
  its own document's facts (no cross-talk), and the wall time beats serial.

Nothing is mocked. Environment: ``RW_OCR_MAX_UPLOAD_BYTES`` (the stack's
``OCR_MAX_UPLOAD_BYTES``, default 25 MiB), ``RW_OCR_FACT_MIN``, ``RW_OCR_HIT5_MIN``,
``RW_OCR_ANSWER_MIN``, ``RW_OCR_PARALLEL``, ``RW_OCR_MIN_SPEEDUP``, ``RW_DEVANAGARI_FONT``;
``RW_PLATFORM_ADMIN_KEY`` for OCR-VISION-FAILOVER.
"""

from __future__ import annotations

import base64
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from tests.real_world import kb
from tests.real_world import ocr_fixtures as ocf
from tests.real_world import onprem as op
from tests.real_world import sources as srcs
from tests.real_world.helpers import LiveAPI, body_of, env_float, mask, tag
from tests.real_world.metrics import answer_correct, norm, record

MAX_UPLOAD = int(env_float("RW_OCR_MAX_UPLOAD_BYTES", 25 * 1024 * 1024))
FACT_MIN = env_float("RW_OCR_FACT_MIN", 0.8)
HIT5_MIN = env_float("RW_OCR_HIT5_MIN", 0.7)
ANSWER_MIN = env_float("RW_OCR_ANSWER_MIN", 0.6)
PARALLEL = int(env_float("RW_OCR_PARALLEL", 6))
MIN_SPEEDUP = env_float("RW_OCR_MIN_SPEEDUP", 1.3)
ENGINES = {"tesseract", "llm_vision"}


def extract(api: LiveAPI, doc_or_bytes: Any, filename: str = "", mime: str = "",
            **form: Any) -> tuple[int, dict[str, Any], float]:
    if isinstance(doc_or_bytes, ocf.OcrDoc):
        filename, mime, data = doc_or_bytes.filename, doc_or_bytes.mime, doc_or_bytes.data
    else:
        data = doc_or_bytes
    started = time.monotonic()
    resp = api.post("/ocr/extract", files={"file": (filename, data, mime)},
                           data={k: str(v).lower() if isinstance(v, bool) else v
                                 for k, v in form.items()}, timeout=600)
    body = body_of(resp)
    return resp.status_code, body if isinstance(body, dict) else {"raw": mask(str(body))[:300]}, \
        round(time.monotonic() - started, 2)


def provenance_problems(body: dict[str, Any], pages: int) -> list[str]:
    engines = list(body.get("page_engines") or [])
    out = []
    if int(body.get("page_count") or 0) != pages:
        out.append(f"page_count {body.get('page_count')} != {pages}")
    if len(engines) != int(body.get("page_count") or 0):
        out.append(f"{len(engines)} page_engines for {body.get('page_count')} pages")
    if set(engines) - ENGINES:
        out.append(f"unknown engines {sorted(set(engines) - ENGINES)}")
    if int(body.get("vision_pages") or 0) != engines.count("llm_vision"):
        out.append(f"vision_pages {body.get('vision_pages')} but {engines.count('llm_vision')} "
                   "pages report llm_vision")
    want = ("mixed" if len(set(engines)) > 1 else engines[0]) if engines else None
    if want and body.get("engine_used") != want:
        out.append(f"engine_used {body.get('engine_used')!r}, pages say {want!r}")
    return out


# ── OCR-EXTRACT-PROVENANCE ──────────────────────────────────────────────────


@pytest.mark.scenario("OCR-EXTRACT-PROVENANCE")
def test_extract_reads_facts_with_page_provenance(api: LiveAPI, evidence: dict[str, Any]) -> None:
    docs = ocf.readable_docs()
    evidence["hindi_available"] = any(d.case == "hindi" for d in docs)
    soft: list[str] = []
    rows: dict[str, Any] = {}
    facts_total = facts_read = 0
    for d in docs:
        status, body, secs = extract(api, d)
        text = str(body.get("raw_text") or "")
        read = [f for f in d.facts if ocf.contains_fact(text, f)]
        facts_total += len(d.facts)
        facts_read += len(read)
        rows[d.case] = {"http": status, "s": secs, "engines": body.get("page_engines"),
                        "engine_used": body.get("engine_used"),
                        "confidence": body.get("overall_confidence"),
                        "measured": body.get("confidence_measured"),
                        "failed": body.get("failed_pages"), "empty": body.get("empty_pages"),
                        "degraded": body.get("degraded"), "read": read,
                        "missed": [f for f in d.facts if f not in read], "text": text[:160]}
        if status != 200:
            soft.append(f"{d.case}: /ocr/extract -> {status} {mask(body)[:160]}")
            continue
        soft += [f"{d.case}: {p}" for p in provenance_problems(body, d.pages)]
        if body.get("failed_pages") or body.get("degraded"):
            soft.append(f"{d.case}: degraded / failed pages {body.get('failed_pages')} on a "
                        "readable document")
        if body.get("confidence_measured") is False and "tesseract" in (
                body.get("page_engines") or []):
            soft.append(f"{d.case}: confidence marked unmeasured although Tesseract read a page")
    accuracy = facts_read / facts_total if facts_total else 0.0
    evidence["documents"] = rows
    engines = [e for r in rows.values() for e in (r.get("engines") or [])]
    record(evidence, documents=len(docs), fact_accuracy=round(accuracy, 3),
           pages=len(engines), tesseract_pages=engines.count("tesseract"),
           vision_pages=engines.count("llm_vision"),
           mean_s_per_doc=round(sum(r["s"] for r in rows.values()) / max(1, len(rows)), 2))
    if accuracy < FACT_MIN:
        soft.append(f"fact accuracy {accuracy:.2f} < {FACT_MIN} (missed: "
                    f"{ {k: v['missed'] for k, v in rows.items() if v['missed']} })")
    assert not soft, "; ".join(soft)


# ── OCR-PARTIAL-AND-REFUSALS ────────────────────────────────────────────────


@pytest.mark.scenario("OCR-PARTIAL-AND-REFUSALS")
def test_partial_pages_and_refusals(api: LiveAPI, evidence: dict[str, Any]) -> None:
    soft: list[str] = []
    out: dict[str, Any] = {}
    mixed = ocf.readable_plus_noise()
    status, body, secs = extract(api, mixed)
    out["readable+noise"] = {k: body.get(k) for k in (
        "page_count", "page_engines", "failed_pages", "empty_pages", "degraded",
        "degradation_reason")} | {"http": status, "s": secs}
    if status != 200:
        soft.append(f"readable + noise PDF -> {status} (one readable page must give a 200)")
    else:
        if not ocf.contains_fact(str(body.get("raw_text")), "DR-3091"):
            soft.append("the readable page's text was lost next to the noise page")
        failed, empty = body.get("failed_pages") or [], body.get("empty_pages") or []
        if failed and not body.get("degraded"):
            soft.append(f"failed pages {failed} but degraded is false")
        if failed and 2 not in failed:
            soft.append(f"failed pages {failed} do not name page 2")
        if body.get("degraded") and not body.get("degradation_reason"):
            soft.append("degraded without a reason")
        evidence["noise_page_outcome"] = "failed (degraded)" if failed else (
            "empty" if 2 in empty else "read as text")

    cases = {
        "truncated PNG": (ocf.truncated_png(), "broken-scan.png", ocf.PNG, (502, 422, 400)),
        # A PDF no renderer can open is a 422 with the reason (it was a silent
        # zero-page 200); 502 only if the renderer itself failed.
        "unrasterisable PDF": (ocf.unrasterisable_pdf(), "garbage.pdf", ocf.PDF, (422, 502)),
        "unsupported type": (b"plain text, not an image", "notes.txt", "text/plain",
                             (415, 422, 400)),
        "over the upload cap": (ocf.oversize_png(MAX_UPLOAD), "huge-scan.png", ocf.PNG, (413,)),
        # Under the cap: never a 413 (the agent tool used to refuse past a
        # hardcoded 10 MiB). This body is not a decodable PNG, hence the 422.
        "between 10 MiB and the cap": (ocf.oversize_png(12 * 1024 * 1024 - 1), "big-scan.png",
                                       ocf.PNG, (422,)),
    }
    for name, (data, filename, mime, allowed) in cases.items():
        status, body, secs = extract(api, data, filename, mime)
        out[name] = {"http": status, "s": secs, "detail": mask(body.get("detail") or
                                                              body.get("raw") or "")[:200],
                     "page_count": body.get("page_count"), "degraded": body.get("degraded")}
        if status >= 500 and status != 502:
            soft.append(f"{name}: HTTP {status}")
        elif status not in allowed:
            soft.append(f"{name}: HTTP {status}, expected one of {allowed}")
        if status == 200 and not body.get("degraded") and not str(body.get("raw_text") or ""
                                                                   ).strip():
            soft.append(f"{name}: a silent empty 200 (nothing read, not flagged degraded)")
        if "10 MB" in str(body.get("detail") or ""):
            soft.append(f"{name}: refused at the old hardcoded 10 MB tool limit")
        if name == "over the upload cap" and secs > 30:
            soft.append(f"the over-cap upload took {secs}s to refuse")
    evidence["cases"] = out
    assert not soft, "; ".join(soft)


# ── OCR-BATCH ───────────────────────────────────────────────────────────────


def _b64_item(doc: ocf.OcrDoc | None, *, data: bytes = b"", filename: str = "",
              persist: bool, cid: str) -> dict[str, Any]:
    raw = doc.data if doc else data
    key = "pdf_base64" if (doc.mime if doc else "") == ocf.PDF else "image_base64"
    return {key: base64.b64encode(raw).decode(), "filename": doc.filename if doc else filename,
            "persist_to_kb": persist, "collection_id": cid}


@pytest.mark.scenario("OCR-BATCH")
def test_batch_per_item_reasons_and_persist(api: LiveAPI, cleanup: Any,
                                            evidence: dict[str, Any]) -> None:
    cid = srcs.create_collection(api, cleanup, "rw-ocr-batch")
    rotated, table = ocf.rotated(), ocf.table_scan()
    items = [_b64_item(rotated, persist=True, cid=cid),
             _b64_item(table, persist=True, cid=cid),
             _b64_item(None, data=ocf.truncated_png(), filename="broken.png", persist=True,
                       cid=cid),
             _b64_item(ocf.screenshot_jpg(), persist=True, cid="")]
    resp = api.post("/ocr/batch", json={"documents": items}, timeout=900)
    body = body_of(resp)
    soft: list[str] = []
    assert resp.status_code == 200, f"/ocr/batch -> {resp.status_code}: {mask(body)[:300]}"
    results, errors = body.get("results") or [], body.get("errors") or []
    evidence["batch"] = {"total": body.get("total"), "succeeded": body.get("succeeded"),
                         "failed": body.get("failed"), "errors": [mask(e)[:160] if e else None
                                                                  for e in errors],
                         "persisted": [(r or {}).get("kb_chunks_ingested") for r in results]}
    if (body.get("total"), body.get("succeeded"), body.get("failed")) != (4, 2, 2):
        soft.append(f"totals {body.get('total')}/{body.get('succeeded')}/{body.get('failed')}, "
                    "expected 4/2/2")
    if len(results) != 4 or len(errors) != 4:
        soft.append("results / errors are not one entry per document")
    else:
        for i in (0, 1):
            r = results[i] or {}
            if errors[i] or not r.get("kb_persisted") or not r.get("kb_chunks_ingested"):
                soft.append(f"item {i} not persisted: {mask(errors[i])} {mask(r)[:160]}")
            if r and str(r.get("kb_collection_id")) != cid:
                soft.append(f"item {i} persisted into {r.get('kb_collection_id')}")
        if not errors[2]:
            soft.append("the broken image has no per-item reason")
        if not errors[3] or "collection" not in str(errors[3]).lower():
            soft.append(f"persist without a collection: reason {mask(errors[3])}")
    hits = kb.search(api, cid, "Who is the driver on gate pass GP-77120?", 5)
    if not any("wieczorek" in norm(h.get("content")) for h in hits):
        soft.append("the persisted OCR text is not searchable")
    elif not any(str(h.get("source_url") or "").startswith("ocr://") for h in hits):
        soft.append("persisted OCR chunks do not cite an ocr:// source")
    eleven = api.post("/ocr/batch", json={"documents": [items[1]] * 11}, timeout=120)
    evidence["eleven_http"] = eleven.status_code
    if eleven.status_code != 422:
        soft.append(f"a batch of 11 answered {eleven.status_code}, expected 422")
    assert not soft, "; ".join(soft)


# ── OCR-KB-UPLOAD ───────────────────────────────────────────────────────────


@pytest.mark.scenario("OCR-KB-UPLOAD")
def test_scanned_uploads_known_answers(api: LiveAPI, cleanup: Any,
                                       evidence: dict[str, Any]) -> None:
    cid = srcs.create_collection(api, cleanup, "rw-ocr-kb")
    docs = [d for d in ocf.readable_docs() if d.question]
    soft: list[str] = []
    uploads: dict[str, Any] = {}
    for d in docs:
        resp = api.post("/knowledge/ingest/file", data={"collection_id": cid},
                        files={"file": (d.filename, d.data, d.mime)}, timeout=900)
        body = body_of(resp) if resp.content else {}
        body = body if isinstance(body, dict) else {}
        uploads[d.case] = {"http": resp.status_code, "chunks": body.get("chunks_created"),
                           "pages": body.get("pages"), "ocr_pages": body.get("ocr_pages"),
                           "warnings": body.get("warnings")}
        if resp.status_code == 503:
            pytest.skip(f"the stack reports no OCR engine for uploads (503): "
                        f"{mask(body)[:160]}")
        if resp.status_code not in (200, 201) or not body.get("chunks_created"):
            soft.append(f"{d.case}: upload {resp.status_code} {mask(body)[:160]}")
        elif d.mime == ocf.PDF and list(body.get("ocr_pages") or []) != list(
                range(1, d.pages + 1)):
            soft.append(f"{d.case}: ocr_pages {body.get('ocr_pages')} for a {d.pages}-page scan")
    rows: dict[str, Any] = {}
    hit5 = answered = 0
    for d in docs:
        hits = kb.search(api, cid, d.question, 5)
        rank = next((i for i, h in enumerate(hits, 1)
                     if d.filename.lower() in str(h.get("source_file") or h.get("source_url")
                                                  ).lower()
                     and any(ocf.contains_fact(str(h.get("content")), f) for f in d.facts)),
                    None)
        status, rag, _ = kb.rag_query(api, cid, d.question, top_k=5)
        answer = kb.answer_text(rag) if status == 200 else ""
        ok = answer_correct(answer, {"answer_any": d.answer_any})
        hit5 += 1 if rank else 0
        answered += 1 if ok else 0
        rows[d.case] = {"rank": rank, "rag_http": status, "answer_ok": ok,
                        "answer": answer[:120]}
    n = len(docs)
    record(evidence, documents=n, hit_at_5=round(hit5 / n, 3), answer_accuracy=round(
        answered / n, 3))
    evidence.update(uploads=uploads, questions=rows)
    if hit5 / n < HIT5_MIN:
        soft.append(f"hit@5 {hit5 / n:.2f} < {HIT5_MIN}: "
                    f"{[k for k, v in rows.items() if not v['rank']]}")
    if answered / n < ANSWER_MIN:
        soft.append(f"answer accuracy {answered / n:.2f} < {ANSWER_MIN}")
    assert not soft, "; ".join(soft)


# ── OCR-VISION-FAILOVER ─────────────────────────────────────────────────────


@pytest.mark.scenario("OCR-VISION-FAILOVER")
def test_vision_failure_keeps_tesseract_text(api: LiveAPI, evidence: dict[str, Any]) -> None:
    admin = op.admin_client(api)
    op.require_registry_admin(admin)
    probe = ocf.low_quality()
    status0, base, _ = extract(api, probe)
    evidence["baseline"] = {"http": status0, "engines": base.get("page_engines"),
                            "confidence": base.get("overall_confidence")}
    _, clean, _ = extract(api, ocf.table_scan())
    tesseract_on = "tesseract" in (clean.get("page_engines") or []) + (
        base.get("page_engines") or [])
    evidence["tesseract_enabled"] = tesseract_on
    dead = {"model_id": f"rw-dead-vision-{tag()}", "display_name": "unreachable vision model",
            "capabilities": ["ocr", "vision"], "supports_vision": True,
            "base_url": "http://10.255.255.1:8000/v1", "cost_per_1k_input": 0,
            "cost_per_1k_output": 0}
    soft: list[str] = []
    with op.RegistrySandbox(admin) as box:
        reg = box.register(dead)
        assert reg["_http"] < 300, f"register the dead vision model -> {reg}"
        order = box.set_order("ocr", [f"{op.PROVIDER}/{dead['model_id']}"])
        status, body, secs = extract(api, probe)
    evidence.update(order=order, status=status, seconds=secs,
                    engines=body.get("page_engines"), failed=body.get("failed_pages"),
                    text=str(body.get("raw_text") or "")[:160], registry_log=box.log)
    if status >= 500 and status != 502:
        soft.append(f"a dead vision model made /ocr/extract answer {status}")
    if tesseract_on:
        if status != 200:
            soft.append(f"with Tesseract on, a dead vision model gave {status} instead of "
                        "keeping the Tesseract text")
        elif not str(body.get("raw_text") or "").strip() or body.get("failed_pages"):
            soft.append(f"the Tesseract text was not kept: failed {body.get('failed_pages')}")
    elif status == 502 and "vision" not in str(body).lower():
        soft.append(f"502 without naming the vision failure: {mask(body)[:160]}")
    if admin is not api:
        admin.close()
    assert not soft, "; ".join(soft)


# ── OCR-PARALLEL ────────────────────────────────────────────────────────────


@pytest.mark.scenario("OCR-PARALLEL")
def test_concurrent_ocr_jobs(api: LiveAPI, evidence: dict[str, Any]) -> None:
    docs = ocf.readable_docs()[:max(2, PARALLEL)]
    by_case = {d.case: d for d in docs}
    serial: dict[str, Any] = {}
    started = time.monotonic()
    for d in docs:
        status, body, secs = extract(api, d)
        serial[d.case] = {"http": status, "s": secs, "text": str(body.get("raw_text") or "")}
    serial_s = time.monotonic() - started

    def one(d: ocf.OcrDoc) -> tuple[str, int, str, float]:
        status, body, secs = extract(api, d)
        return d.case, status, str(body.get("raw_text") or ""), secs

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=len(docs)) as pool:
        concurrent = list(pool.map(one, docs))
    parallel_s = time.monotonic() - started
    texts = {case: text for case, _, text, _ in concurrent}
    soft: list[str] = []
    for case, status, text, _ in concurrent:
        if status != 200:
            soft.append(f"{case}: concurrent OCR -> {status}")
        expected = [f for f in by_case[case].facts if ocf.contains_fact(serial[case]["text"], f)]
        lost = [f for f in expected if not ocf.contains_fact(text, f)]
        if lost:
            soft.append(f"{case}: facts read serially but not concurrently: {lost}")
    talk = ocf.cross_talk(texts, by_case)
    if talk:
        soft.append(f"cross-talk between concurrent results: {talk[:3]}")
    speedup = serial_s / parallel_s if parallel_s else 0.0
    record(evidence, jobs=len(docs), serial_s=round(serial_s, 1),
           parallel_s=round(parallel_s, 1), speedup=round(speedup, 2))
    evidence["per_job_s"] = {c: s for c, _, _, s in concurrent}
    if speedup < MIN_SPEEDUP:
        soft.append(f"{len(docs)} concurrent OCR jobs took {parallel_s:.0f}s vs {serial_s:.0f}s "
                    f"serially (speedup {speedup:.2f} < {MIN_SPEEDUP})")
    assert not soft, "; ".join(soft)
