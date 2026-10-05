"""KB-UPLOAD-HARD: difficult real-world file uploads through the live stack (P1a, A1).

Per document (``corpus_hard.build_hard_docs``): the upload succeeds within its
time bound, the chunk count is in range, PDF page counts are right, every planted
fact is retrieved from the right document / page / archive member (search top 5),
superseded or boilerplate text is never served, unicode and code survive verbatim,
and ``/rag/query`` answers the question citing the right chunk (and page).

KB-UPLOAD-REFUSALS: encrypted, corrupt, empty and truncated files and zip bombs
are refused with an honest 4xx that says why, quickly, without storing anything
and without hurting the stack.

KB-UPLOAD-SIZE-LIMIT: a DOCX of exactly the upload limit is ingested; one byte
more is a 413.

KB-UPLOAD-DUPLICATES: the same bytes under another name (or extension) are
deduplicated and point at the stored document; two files sharing a paragraph are
both kept and both retrievable.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import pytest

from tests.real_world import corpus_hard as ch
from tests.real_world import kb
from tests.real_world.helpers import LiveAPI, body_of, mask
from tests.real_world.metrics import answer_correct, norm, record, source_matches, source_of

UPLOAD_LIMIT = int(os.getenv("RW_UPLOAD_LIMIT_BYTES", str(50 * 1024 * 1024)))

_DOCS = ch.build_hard_docs()
_CASES = [d.case for d in _DOCS]


def _post_file(api: LiveAPI, cid: str, filename: str, data: bytes, mime: str
               ) -> dict[str, Any]:
    started = time.monotonic()
    resp = api.post("/knowledge/ingest/file", data={"collection_id": cid},
                    files={"file": (filename, data, mime)}, timeout=900)
    body = body_of(resp)
    if not isinstance(body, dict):
        body = {"raw": mask(str(body)[:400])}
    return {"http": resp.status_code, "body": body,
            "s": round(time.monotonic() - started, 2),
            "chunks": int(body.get("chunks_created") or 0) if resp.status_code < 300 else 0}


def _search(api: LiveAPI, cid: str, q: str, top_k: int = 5,
            filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"q": q, "collection_id": cid, "top_k": top_k}
    if filters:
        params["filters"] = json.dumps(filters)
    body = api.json_ok("GET", "/knowledge/search", params=params)
    return list(body if isinstance(body, list) else body.get("results", []))


def _from(hit: dict[str, Any], doc: ch.HardDoc, member: str | None = None) -> bool:
    src = source_of(hit)
    if not source_matches(src, [doc.filename]):
        return False
    return member is None or member.lower() in src.lower()


def _doc_chunks(api: LiveAPI, cid: str, doc: ch.HardDoc, document_id: str | None
                ) -> list[dict[str, Any]]:
    """The document's stored chunks: document-filtered searches (the API caps top_k
    at 20) for its name and for every needle the scenario checks."""
    if not document_id:
        return []
    seen: dict[str, dict[str, Any]] = {}
    queries = [doc.filename.rsplit(".", 1)[0].replace("-", " "), *doc.must_index,
               *doc.must_not_index]
    for query in queries:
        for h in _search(api, cid, query[:300], top_k=20, filters={"document_id": document_id}):
            if _from(h, doc):
                seen[str(h.get("chunk_id"))] = h
    return list(seen.values())


@pytest.fixture(scope="module")
def hard_kb(api: LiveAPI) -> Any:
    cid = kb.create_collection(api, "rw-hard-kb", "P1a difficult uploads")
    uploads = {d.case: _post_file(api, cid, d.filename, d.data, d.mime) for d in _DOCS}
    yield {"collection_id": cid, "uploads": uploads}
    api.delete(f"/knowledge/collections/{cid}")


@pytest.mark.scenario("KB-UPLOAD-HARD")
@pytest.mark.parametrize("case", _CASES)
def test_hard_upload(case: str, api: LiveAPI, hard_kb: dict[str, Any],
                     evidence: dict[str, Any]) -> None:
    doc = next(d for d in _DOCS if d.case == case)
    cid = hard_kb["collection_id"]
    up = hard_kb["uploads"][case]
    body = up["body"]
    evidence.update(collection_id=cid, file=doc.filename, bytes=len(doc.data),
                    upload_http=up["http"], upload_s=up["s"], chunks=up["chunks"],
                    expected_chunks=list(doc.expected_chunks),
                    response={k: body.get(k) for k in ("pages", "ocr_pages", "archive",
                                                         "document_id", "replaced",
                                                         "truncated", "detail")
                              if k in body})
    record(evidence, upload_s=up["s"], chunks=up["chunks"])
    assert up["http"] in (200, 201), (
        f"{case}: upload of {doc.filename} refused: HTTP {up['http']} {mask(body)[:300]}"
    )
    soft: list[str] = []
    assert not body.get("deduplicated"), f"first upload reported as duplicate: {body}"
    lo, hi = doc.expected_chunks
    if not lo <= up["chunks"] <= hi:
        soft.append(f"{up['chunks']} chunks, expected {lo}-{hi}")
    if up["s"] > doc.max_upload_s:
        soft.append(f"upload took {up['s']} s (bound {doc.max_upload_s} s)")
    if doc.pages and int(body.get("pages") or 0) != doc.pages:
        soft.append(f"page count {body.get('pages')} != {doc.pages}")

    chunks = _doc_chunks(api, cid, doc, body.get("document_id"))
    evidence["chunks_seen"] = len(chunks)
    text = "\n".join(str(h.get("content") or "") for h in chunks)
    for needle in doc.must_index:
        if norm(needle) not in norm(text):
            soft.append(f"not indexed verbatim: {needle[:70]!r}")
    for needle in doc.must_not_index:
        if norm(needle) in norm(text):
            soft.append(f"boilerplate / removed text indexed: {needle[:60]!r}")
    if doc.case == "zip-nested":
        _check_archive(body, chunks, soft, evidence)

    rows: dict[str, Any] = {}
    for q in doc.questions:
        row: dict[str, Any] = {}
        started = time.monotonic()
        hits = _search(api, cid, q.question, top_k=5)
        row["search_ms"] = round((time.monotonic() - started) * 1000)
        rank = next((i + 1 for i, h in enumerate(hits) if _from(h, doc, q.member)
                     and norm(q.must_contain) in norm(h.get("content"))), None)
        row["rank"] = rank
        row["top_sources"] = [source_of(h) for h in hits[:3]]
        if rank is None:
            soft.append(f"{q.id}: not in the top 5 (top: {row['top_sources']})")
        elif q.page is not None and str(hits[rank - 1].get("page")) != str(q.page):
            soft.append(f"{q.id}: cites page {hits[rank - 1].get('page')}, fact is on {q.page}")
        if q.stale:
            stale = [h for h in _search(api, cid, q.question, top_k=10)
                     if _from(h, doc) and norm(q.stale) in norm(h.get("content"))]
            if stale:
                soft.append(f"{q.id}: superseded text {q.stale!r} is still served")
        if q.rag:
            status, rbody, ms = kb.rag_query(api, cid, q.question, top_k=5)
            retries = 0
            # The stack's only LLM provider throttles (P0 §4.13); an answer-synthesis
            # 503 / 429 is retried after a pause and every retry is recorded.
            while status in (429, 503) and retries < 2:
                retries += 1
                time.sleep(20)
                status, rbody, ms = kb.rag_query(api, cid, q.question, top_k=5)
            row.update(rag_http=status, rag_ms=round(ms), rag_retries=retries)
            if status != 200:
                soft.append(f"{q.id}: /rag/query -> {status} {mask(rbody)[:160]}")
            else:
                answer = kb.answer_text(rbody)
                cits = list(rbody.get("citations") or [])
                row["answer_head"] = answer[:160]
                row["answered"] = answer_correct(answer, {"answer_any": q.answer_any})
                row["cited"] = any(
                    _from(c, doc, q.member) and norm(q.must_contain) in norm(c.get("content"))
                    and (q.page is None or str((c.get("metadata") or {}).get(
                        "page", c.get("page"))) == str(q.page))
                    for c in cits)
                row["cited_sources"] = sorted({source_of(c) for c in cits})[:4]
                if not row["answered"]:
                    soft.append(f"{q.id}: wrong answer {answer[:120]!r}")
                if not row["cited"]:
                    soft.append(f"{q.id}: no citation of the right chunk/page "
                                f"({row['cited_sources']})")
        rows[q.id] = row
    evidence["questions"] = rows
    record(evidence, facts=len(rows), facts_top5=sum(1 for r in rows.values() if r["rank"]),
           rag_answered=sum(1 for r in rows.values() if r.get("answered")),
           rag_cited=sum(1 for r in rows.values() if r.get("cited")),
           rag_retries=sum(int(r.get("rag_retries") or 0) for r in rows.values()))
    assert not soft, "; ".join(soft)


def _check_archive(body: dict[str, Any], chunks: list[dict[str, Any]], soft: list[str],
                   evidence: dict[str, Any]) -> None:
    archive = body.get("archive") or {}
    evidence["archive"] = archive
    indexed = [str(m) for m in archive.get("members_indexed") or []]
    skipped = {str(s.get("name")): str(s.get("reason")) for s in archive.get("members_skipped")
               or []}
    for member in ("overtime-policy.docx", "tariff-extract.pdf", "contacts.csv", "history.md"):
        if not any(m.endswith(member) for m in indexed):
            soft.append(f"archive member {member} not reported as indexed ({indexed})")
    if not any(k.endswith("old-roster.doc") for k in skipped):
        soft.append(f"the unsupported legacy .doc is not reported as skipped ({skipped})")
    names = indexed + list(skipped) + [source_of(h) for h in chunks]
    if any("__MACOSX" in n or ".DS_Store" in n for n in indexed + [source_of(h) for h in chunks]):
        soft.append("macOS metadata files were indexed")
    if any(".." in n.split("/") for n in names):
        soft.append(f"a path-traversal member name survived: {[n for n in names if '..' in n]}")


@pytest.mark.scenario("KB-UPLOAD-REFUSALS")
def test_upload_refusals(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    cid = kb.create_collection(api, "rw-hard-refusals")
    cleanup("DELETE", f"/knowledge/collections/{cid}")
    results: dict[str, Any] = {}
    soft: list[str] = []
    for ref in ch.build_refusals():
        up = _post_file(api, cid, ref.filename, ref.data, ref.mime)
        detail = str(up["body"].get("detail") or up["body"])
        results[ref.case] = {"http": up["http"], "s": up["s"], "detail": mask(detail)[:200]}
        if up["http"] not in ref.statuses:
            soft.append(f"{ref.case}: HTTP {up['http']} (want {ref.statuses}) {detail[:160]}")
        elif not any(w in detail.lower() for w in ref.detail_any):
            soft.append(f"{ref.case}: the error does not say why: {detail[:160]}")
        if up["s"] > ref.max_s:
            soft.append(f"{ref.case}: took {up['s']} s to refuse (bound {ref.max_s} s)")
    evidence["refusals"] = results
    docs, total = kb.all_documents(api, cid)
    evidence["documents_after"] = total
    if docs:
        soft.append(f"refused uploads stored {len(docs)} documents: "
                    f"{[kb.doc_title(d) for d in docs]}")
    health = api.get("/health")
    evidence["health_after"] = health.status_code
    assert health.status_code == 200, "the stack is unhealthy after the refusal batch"
    record(evidence, refusals=len(results),
           refused_correctly=len(results) - len([s for s in soft if ": HTTP" in s]))
    assert not soft, "; ".join(soft)


@pytest.mark.scenario("KB-UPLOAD-SIZE-LIMIT")
def test_upload_size_limit(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    cid = kb.create_collection(api, "rw-hard-size")
    cleanup("DELETE", f"/knowledge/collections/{cid}")
    at_limit = ch.size_limit_docx(UPLOAD_LIMIT)
    over = ch.size_limit_docx(UPLOAD_LIMIT + 1)
    ok = _post_file(api, cid, "breakwater-brief-at-limit.docx", at_limit, ch.DOCX_MIME)
    big = _post_file(api, cid, "breakwater-brief-over-limit.docx", over, ch.DOCX_MIME)
    evidence.update(limit=UPLOAD_LIMIT, at_limit={k: ok[k] for k in ("http", "s", "chunks")},
                    over_limit={"http": big["http"], "s": big["s"],
                                "detail": str(big["body"].get("detail"))[:200]})
    record(evidence, at_limit_s=ok["s"], over_limit_s=big["s"])
    assert ok["http"] in (200, 201) and ok["chunks"] >= 1, (
        f"a file of exactly the limit was refused: {ok['http']} {mask(ok['body'])[:200]}"
    )
    hits = _search(api, cid, "How much sheltered quay does the Plover breakwater add?")
    assert any("640 metres" in str(h.get("content")) for h in hits), "fact of the at-limit file"
    assert big["http"] == 413, f"one byte over the limit -> {big['http']} (want 413)"
    assert "limit" in str(big["body"].get("detail", "")).lower()
    docs, _ = kb.all_documents(api, cid)
    assert len(docs) == 1, f"{len(docs)} documents stored (want only the at-limit one)"


@pytest.mark.scenario("KB-UPLOAD-DUPLICATES")
def test_upload_duplicates(api: LiveAPI, cleanup: Any, evidence: dict[str, Any]) -> None:
    cid = kb.create_collection(api, "rw-hard-dups")
    cleanup("DELETE", f"/knowledge/collections/{cid}")
    files = ch.duplicate_docs()
    res = {name: _post_file(api, cid, name, data,
                            "text/plain" if name.endswith(".txt") else "text/markdown")
           for name, data in files.items()}
    evidence["uploads"] = {n: {"http": r["http"], "chunks": r["chunks"],
                               "deduplicated": r["body"].get("deduplicated"),
                               "document_id": r["body"].get("document_id")}
                           for n, r in res.items()}
    first = res["ops-bulletin-31.md"]
    assert first["http"] == 201 and first["chunks"] >= 1, mask(first)[:300]
    soft: list[str] = []
    for name in ("ops-bulletin-31-copy.md", "ops-bulletin-31.txt"):
        r = res[name]
        if not (r["http"] in (200, 201) and r["body"].get("deduplicated") is True
                and r["chunks"] == 0):
            soft.append(f"{name}: identical content not deduplicated: {mask(r['body'])[:200]}")
        elif r["body"].get("document_id") != first["body"].get("document_id"):
            soft.append(f"{name}: dedup does not point at the stored document "
                        f"({r['body'].get('document_id')} vs "
                        f"{first['body'].get('document_id')})")
    second = res["ops-bulletin-32.md"]
    assert second["http"] == 201 and second["chunks"] >= 1, mask(second)[:300]
    docs, _ = kb.all_documents(api, cid)
    titles = sorted(kb.doc_title(d) for d in docs)
    evidence["documents"] = titles
    if len(docs) != 2:
        soft.append(f"{len(docs)} documents stored, want 2 (31 and 32): {titles}")
    hits = _search(api, cid, "When does the Kingfisher gate close for maintenance?", top_k=5)
    sources = {source_of(h) for h in hits if "kingfisher gate closes" in norm(h.get("content"))}
    evidence["shared_paragraph_sources"] = sorted(sources)
    if not any("ops-bulletin-31" in s for s in sources) or not any(
            "ops-bulletin-32" in s for s in sources):
        soft.append(f"the shared paragraph is not retrievable from both bulletins: {sources}")
    record(evidence, documents=len(docs))
    assert not soft, "; ".join(soft)
