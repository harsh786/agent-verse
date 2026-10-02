"""KB-COMPLEX-CORPUS: a realistic multi-format corpus through the live ingestion stack.

Per format (one test each, so one unsupported format never hides another):
a 60-120 page policy manual PDF (sections, tables, footnotes), a DOCX with headings
and tables, a PPTX with speaker notes, a multi-sheet XLSX with formulas, a ~5,000
row CSV, HTML with nested lists, Markdown with code blocks, a scanned-image PDF and
a PNG (the OCR path) and a ZIP of mixed files. Each asserts the chunk count is in
the expected range, every planted fact of that document is retrievable (right
document + right chunk), PDF hits cite the right page, tables keep a queried row
intact inside one chunk, and section headings travel with their facts.

KB-COMPLEX-EMBEDDINGS: every chunk is embedded at the configured dimension.
KB-COMPLEX-LIFECYCLE: dedup on identical re-upload, update on edit (only changed
chunks replaced, the stale clause is no longer served), delete (chunks + vectors
gone, the neighbouring document untouched).
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.real_world import kb
from tests.real_world.corpus import (
    CSV_NAME,
    DOCX_AMENDED_TERMINATION,
    DOCX_NAME,
    MD_NAME,
    CorpusDoc,
    build_docx,
    build_docx_edited,
    build_md,
    doc_rng,
    load_questions,
)
from tests.real_world.helpers import LiveAPI, env_float, mask, wait_until
from tests.real_world.metrics import norm, rank_of, record, source_matches, source_of

FORMATS = ["pdf", "docx", "pptx", "xlsx", "csv", "html", "md", "scan_pdf", "png", "zip"]
HEADING_ALIGN_MIN = env_float("RW_HEADING_ALIGN_MIN", 0.5)


def _doc(complex_kb: dict[str, Any], fmt: str) -> CorpusDoc:
    return next(d for d in complex_kb["docs"] if d.fmt == fmt)


def _questions_for(doc: CorpusDoc) -> list[dict[str, Any]]:
    return [q for q in load_questions()
            if q["kind"] != "cross_doc" and source_matches(doc.filename, q["expected_sources"])]


@pytest.mark.scenario("KB-COMPLEX-CORPUS")
@pytest.mark.parametrize("fmt", FORMATS)
def test_kb_complex_format(fmt: str, api: LiveAPI, complex_kb: dict[str, Any],
                           evidence: dict[str, Any]) -> None:
    doc = _doc(complex_kb, fmt)
    cid = complex_kb["collection_id"]
    up = complex_kb["uploads"][doc.filename]
    evidence.update(collection_id=cid, file=doc.filename, bytes=len(doc.data),
                    upload_http=up["http"], chunks=up["chunks"],
                    expected_chunks=list(doc.expected_chunks))
    record(evidence, upload_ms=up["ms"], chunks=up["chunks"])
    detail = mask(up["body"])[:400]
    if doc.needs_ocr and up["http"] == 503 and "ocr" in detail.lower():
        pytest.skip(f"the stack has no OCR engine / vision provider for {doc.filename}: "
                    f"{detail[:200]} (install Tesseract in the backend image or configure a "
                    "vision-capable provider)")
    assert up["http"] in (200, 201), (
        f"{fmt} upload of {doc.filename} refused: HTTP {up['http']} {detail}"
    )
    assert not up["body"].get("deduplicated"), f"first upload reported as duplicate: {detail}"
    lo, hi = doc.expected_chunks
    assert lo <= up["chunks"] <= hi, (
        f"{doc.filename}: {up['chunks']} chunks, expected {lo}-{hi} for this document's size"
    )
    if doc.pages:
        evidence["pages_reported"] = up["body"].get("pages")
        assert int(up["body"].get("pages") or 0) == doc.pages, (
            f"PDF page count {up['body'].get('pages')} != {doc.pages}"
        )
    if fmt == "xlsx":
        evidence["truncated"] = up["body"].get("truncated")
        assert not up["body"].get("truncated"), "small workbook reported as truncated"

    soft: list[str] = []
    # 1) Every planted fact of this document is retrievable: right doc + right chunk.
    ranks: dict[str, int | None] = {}
    pages: dict[str, Any] = {}
    for q in _questions_for(doc):
        hits = kb.search(api, cid, q["question"], top_k=5)
        r = rank_of(hits, q["expected_sources"], q["chunk_must_contain"])
        ranks[q["id"]] = r
        if r and q.get("page_fact") and doc.fact_pages.get(q["page_fact"]):
            got = hits[r - 1].get("page")
            pages[q["id"]] = {"cited": got, "expected": doc.fact_pages[q["page_fact"]]}
            if str(got) != str(doc.fact_pages[q["page_fact"]]):
                soft.append(f"{q['id']}: hit cites page {got}, the fact is on page "
                            f"{doc.fact_pages[q['page_fact']]}")
        if r is None:
            soft.append(f"{q['id']}: not in the top 5 (top sources: "
                        f"{[source_of(h) for h in hits[:3]]})")
    evidence["fact_ranks"] = ranks
    if pages:
        evidence["page_citations"] = pages
    record(evidence, facts=len(ranks), facts_top5=sum(1 for r in ranks.values() if r))

    # 2) Tables: a queried row stays intact inside one chunk (key + value together).
    if doc.table_probes:
        intact: dict[str, bool] = {}
        for key, value in doc.table_probes:
            hits = kb.search(api, cid, f"{key} {value}", top_k=5)
            intact[key] = any(source_matches(source_of(h), [doc.filename])
                              and norm(key) in norm(h.get("content"))
                              and norm(value) in norm(h.get("content")) for h in hits)
        evidence["table_rows_intact"] = intact
        broken = [k for k, ok in intact.items() if not ok]
        if broken:
            soft.append(f"table rows split across chunks or not retrievable: {broken}")

    # 3) Headings travel with their facts (structure-aware chunking).
    if doc.heading_probes:
        aligned: dict[str, bool] = {}
        for heading, fact in doc.heading_probes:
            hits = kb.search(api, cid, fact, top_k=5)
            fact_hit = next((h for h in hits if source_matches(source_of(h), [doc.filename])
                             and norm(fact) in norm(h.get("content"))), None)
            label = heading.split(". ", 1)[-1]
            aligned[heading] = bool(fact_hit) and norm(label) in norm(fact_hit.get("content"))
        evidence["heading_alignment"] = aligned
        ratio = sum(aligned.values()) / len(aligned)
        record(evidence, heading_alignment=ratio)
        if ratio < HEADING_ALIGN_MIN:
            soft.append(f"only {ratio:.0%} of facts are chunked with their section heading "
                        f"(min {HEADING_ALIGN_MIN:.0%}): {aligned}")
    assert not soft, "; ".join(soft)


@pytest.mark.scenario("KB-COMPLEX-EMBEDDINGS")
def test_kb_complex_embeddings(api: LiveAPI, complex_kb: dict[str, Any],
                               embedder_info: dict[str, Any], evidence: dict[str, Any]) -> None:
    cid = complex_kb["collection_id"]
    stored = sum(u["chunks"] for u in complex_kb["uploads"].values())
    health = kb.embedding_health(api, cid)
    evidence.update(collection_id=cid, embedder=embedder_info, chunks_uploaded=stored,
                    health={k: health.get(k) for k in ("total_chunks", "embedded_chunks",
                                                       "coverage_pct", "embedding_dim",
                                                       "model")})
    record(evidence, total_chunks=int(health.get("total_chunks") or 0),
           coverage_pct=float(health.get("coverage_pct") or 0))
    assert embedder_info.get("dimension"), f"/health reports no embedder dimension: {embedder_info}"
    assert health.get("embedding_dim") == embedder_info.get("dimension"), (
        f"collection vectors are {health.get('embedding_dim')}-d, the configured embedder is "
        f"{embedder_info.get('dimension')}-d"
    )
    assert int(health.get("total_chunks") or 0) == stored, (
        f"store holds {health.get('total_chunks')} chunks, uploads reported {stored}"
    )
    assert int(health.get("embedded_chunks") or 0) == stored, (
        f"only {health.get('embedded_chunks')} of {stored} chunks have a vector"
    )
    assert float(health.get("coverage_pct") or 0) >= 99.0


def _hits_with(api: LiveAPI, cid: str, q: str, needle: str, filename: str
               ) -> list[dict[str, Any]]:
    return [h for h in kb.search(api, cid, q, top_k=8)
            if source_matches(source_of(h), [filename]) and norm(needle) in norm(h.get("content"))]


@pytest.mark.scenario("KB-COMPLEX-LIFECYCLE")
def test_kb_dedup_update_delete(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    cid = kb.create_collection(api, "rw-kb-lifecycle")
    cleanup("DELETE", f"/knowledge/collections/{cid}")
    evidence["collection_id"] = cid
    v1 = build_docx(doc_rng("docx"))
    v2 = build_docx_edited(doc_rng("docx"))
    neighbour = build_md(doc_rng("md"))
    soft: list[str] = []

    first = kb.upload(api, cid, v1)
    other = kb.upload(api, cid, neighbour)
    assert first["http"] in (200, 201) and first["chunks"] > 0, mask(first)[:400]
    assert other["http"] in (200, 201) and other["chunks"] > 0, mask(other)[:400]
    evidence["v1"] = {"chunks": first["chunks"], "document_id": first["body"].get("document_id")}
    total_v1 = first["chunks"] + other["chunks"]

    # Dedup: the identical bytes again → nothing re-embedded or re-stored.
    again = kb.upload(api, cid, v1)
    evidence["reupload"] = {"http": again["http"], "chunks": again["chunks"],
                            "deduplicated": again["body"].get("deduplicated")}
    assert again["http"] in (200, 201), mask(again)[:300]
    assert again["body"].get("deduplicated") is True and again["chunks"] == 0, (
        f"identical re-upload was not deduplicated: {mask(again['body'])[:300]}"
    )
    health = kb.embedding_health(api, cid)
    assert int(health.get("total_chunks") or 0) == total_v1, (
        f"re-upload changed the chunk count: {health.get('total_chunks')} != {total_v1}"
    )

    # Baseline chunk ids for clauses the amendment does NOT touch.
    unchanged = {"liability": ("aggregate liability cap", "1.5 times"),
                 "invoices": ("invoice payment terms days", "within 40 days")}
    before = {k: {h.get("chunk_id") for h in _hits_with(api, cid, q, n, DOCX_NAME)}
              for k, (q, n) in unchanged.items()}
    evidence["unchanged_chunk_ids_before"] = {k: sorted(map(str, v)) for k, v in before.items()}
    assert all(before.values()), f"v1 clauses not retrievable before the edit: {before}"
    assert _hits_with(api, cid, "termination for convenience notice period",
                      "notice period of 75 days", DOCX_NAME), "v1 termination clause not served"

    # Update on edit: the amended agreement under the same file name.
    edited = kb.upload(api, cid, v2)
    evidence["edit_upload"] = {"http": edited["http"], "chunks": edited["chunks"],
                               "document_id": edited["body"].get("document_id")}
    assert edited["http"] in (200, 201), mask(edited)[:300]
    fresh = _hits_with(api, cid, "termination for convenience notice period",
                       "notice period of 120 days", DOCX_NAME)
    stale = _hits_with(api, cid, "termination for convenience notice period",
                       "notice period of 75 days", DOCX_NAME)
    evidence["after_edit"] = {"amended_clause_served": bool(fresh), "stale_clause_served":
                              bool(stale)}
    if not fresh:
        soft.append(f"the amended clause ({DOCX_AMENDED_TERMINATION[:40]}...) is not served")
    if stale:
        soft.append("the superseded clause (75 days) is still served after the edit: the "
                    "re-upload added a second copy instead of replacing the document")
    docs, _ = kb.all_documents(api, cid)
    copies = [d for d in docs if DOCX_NAME.lower() in kb.doc_title(d).lower()]
    evidence["docx_copies_listed"] = len(copies)
    if len(copies) != 1:
        soft.append(f"{len(copies)} documents listed for {DOCX_NAME} after the edit (want 1)")
    after = {k: {h.get("chunk_id") for h in _hits_with(api, cid, q, n, DOCX_NAME)}
             for k, (q, n) in unchanged.items()}
    preserved = {k: bool(before[k] & after[k]) for k in unchanged}
    evidence["unchanged_chunks_preserved"] = preserved
    record(evidence, unchanged_chunks_preserved=sum(preserved.values()) / len(preserved))
    if not all(preserved.values()):
        soft.append(f"unchanged clauses were re-chunked/re-embedded (chunk ids changed): "
                    f"{preserved} - an edit should only replace the changed chunks")
    health = kb.embedding_health(api, cid)
    evidence["chunks_after_edit"] = health.get("total_chunks")
    if int(health.get("total_chunks") or 0) > total_v1 + 2:
        soft.append(f"chunk count grew from {total_v1} to {health.get('total_chunks')} on an "
                    "edit that changed one clause")

    # Delete: every copy of the agreement → its chunks and vectors are gone.
    deleted = 0
    for d in copies or ([{"id": edited["body"].get("document_id")}]
                        if edited["body"].get("document_id") else []):
        resp = api.delete(f"/knowledge/collections/{cid}/documents/{d.get('id')}")
        evidence.setdefault("delete_http", []).append(resp.status_code)
        assert resp.status_code == 200, f"delete -> {resp.status_code}: {mask(resp.text[:200])}"
        deleted += int(resp.json().get("chunks_deleted") or 0)
    evidence["chunks_deleted"] = deleted
    health = wait_until(lambda: kb.embedding_health(api, cid), timeout=60, interval=3,
                        desc="chunk count to drop after delete",
                        done=lambda h: int(h.get("total_chunks") or 0) == other["chunks"])
    evidence["health_after_delete"] = {k: health.get(k) for k in ("total_chunks",
                                                                  "embedded_chunks")}
    assert int(health.get("embedded_chunks") or 0) == other["chunks"], (
        "vectors of the deleted document are still stored"
    )
    leftovers = [h for h in kb.search(api, cid, "Bramblewood termination liability", top_k=10)
                 if source_matches(source_of(h), [DOCX_NAME])]
    assert not leftovers, f"deleted document still searchable: {len(leftovers)} hits"
    md_hits = _hits_with(api, cid, "canary rollout command", "--canary 10", MD_NAME)
    assert md_hits, "deleting one document removed its neighbour's chunks too"
    assert not soft, "; ".join(soft)


@pytest.mark.scenario("KB-COMPLEX-CSV-SCALE")
def test_kb_csv_row_lookup_in_large_file(api: LiveAPI, complex_kb: dict[str, Any],
                                         evidence: dict[str, Any]) -> None:
    """A single row of a ~5,000-row ledger is found among ~100+ chunks."""
    cid = complex_kb["collection_id"]
    up = complex_kb["uploads"][CSV_NAME]
    assert up["http"] in (200, 201), mask(up)[:300]
    hits = kb.search(api, cid, "shipment SHP-2026-04417 status carrier", top_k=5)
    evidence["top_sources"] = [source_of(h) for h in hits]
    evidence["csv_chunks"] = up["chunks"]
    row = next((h for h in hits if "shp-2026-04417" in norm(h.get("content"))), None)
    assert row, "the exact shipment row is not in the top 5 of a 5,000-row ledger"
    assert "held at customs" in norm(row.get("content")) and "kestrel cargo" in norm(
        row.get("content")), "the shipment row was split from its status/carrier columns"
