"""KB-REAL-DOCS and KB-REEMBED: real documents through the live ingestion stack.

Seven formats (PDF, DOCX, PPTX, XLSX, CSV, HTML, MD), each with one distinctive
fact. Asserts chunks + embeddings exist at the configured embedder's width,
that a natural-language search returns the right document for every fact, and
that a goal can answer questions only answerable from these docs, citing them.
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest

from tests.real_world.helpers import LiveAPI, mask, tag, wait_until
from tests.real_world.kb_fixtures import Doc, build_docs

GOAL_TIMEOUT = float(os.getenv("RW_GOAL_TIMEOUT", "480"))
URL_DOC = os.getenv("RW_KB_URL_DOC", "https://peps.python.org/pep-0020/")


def _source_file(hit: dict[str, Any]) -> str:
    meta = hit.get("metadata") or {}
    return str(hit.get("source_file") or meta.get("source_file") or meta.get("filename")
               or hit.get("source") or meta.get("source") or "")


def _norm(text: str) -> str:
    """Lower-case with typographic spaces/hyphens folded (models emit U+202F etc.)."""
    for ch in ("\u202f", "\u00a0", "\u2009", "\u2007"):
        text = text.replace(ch, " ")
    for ch in ("\u2011", "\u2010", "\u2013", "\u2014"):
        text = text.replace(ch, "-")
    return " ".join(text.lower().split())


def search(api: LiveAPI, cid: str, q: str, top_k: int = 3) -> list[dict[str, Any]]:
    body = api.json_ok("GET", "/knowledge/search", params={"q": q, "collection_id": cid,
                                                           "top_k": top_k})
    return list(body if isinstance(body, list) else body.get("results", []))


def rank_of(hits: list[dict[str, Any]], doc: Doc) -> int | None:
    for i, h in enumerate(hits):
        if _source_file(h) == doc.filename or doc.fact in str(h.get("content", "")):
            return i + 1
    return None


@pytest.fixture(scope="module")
def kb(api: LiveAPI) -> Any:
    """A collection with all seven documents uploaded (deleted at module end)."""
    name = f"rw-kb-{tag()}"
    body = api.json_ok("POST", "/knowledge/collections",
                       json={"name": name, "description": "real-world suite fixtures",
                             "embedder_type": "default"})
    cid = str(body.get("collection_id") or body.get("id"))
    docs = build_docs()
    uploads: dict[str, dict[str, Any]] = {}
    for d in docs:
        resp = api.post("/knowledge/ingest/file", data={"collection_id": cid},
                        files={"file": (d.filename, d.data, d.mime)})
        uploads[d.filename] = {"http": resp.status_code,
                               "body": resp.json() if resp.headers.get(
                                   "content-type", "").startswith("application/json")
                               else mask(resp.text[:300])}
    yield {"collection_id": cid, "name": name, "docs": docs, "uploads": uploads}
    api.delete(f"/knowledge/collections/{cid}")


def _documents(api: LiveAPI, cid: str) -> list[dict[str, Any]] | str:
    """The collection's document listing, or an error string when the endpoint fails."""
    resp = api.get(f"/knowledge/collections/{cid}/documents")
    if resp.status_code != 200:
        return "GET /knowledge/collections/{id}/documents -> " + str(resp.status_code) + ": " \
            + mask(resp.text[:200])
    body = resp.json()
    return list(body.get("documents", []) if isinstance(body, dict) else body)


@pytest.mark.scenario("KB-REAL-DOCS")
def test_kb_real_documents(api: LiveAPI, kb: dict[str, Any], embedder_info: dict[str, Any],
                           cleanup: Any, evidence: dict[str, Any]) -> None:
    cid = kb["collection_id"]
    evidence["collection_id"] = cid
    evidence["uploads"] = {f: {"http": u["http"],
                               "chunks": (u["body"] or {}).get("chunks_created")
                               if isinstance(u["body"], dict) else u["body"]}
                           for f, u in kb["uploads"].items()}
    failed = {f: u for f, u in kb["uploads"].items() if u["http"] not in (200, 201)}
    assert not failed, f"uploads refused: {mask(failed)[:800]}"
    zero = [f for f, u in kb["uploads"].items() if not (u["body"] or {}).get("chunks_created")]
    assert not zero, f"uploads produced no chunks: {zero}"

    # Ingestion finished: every document listed with chunks. A broken listing is
    # recorded and the scenario continues (chunk counts come from the uploads).
    soft: list[str] = []
    total_chunks = sum(int((u["body"] or {}).get("chunks_created") or 0)
                       for u in kb["uploads"].values())
    docs = wait_until(lambda: _documents(api, cid), timeout=120, interval=3,
                      desc="all 7 documents listed with chunks",
                      done=lambda ds: isinstance(ds, str) or len(
                          [d for d in ds if (d.get("chunk_count") or 0) > 0]) >= len(kb["docs"]))
    if isinstance(docs, str):
        soft.append(f"document listing failed: {docs}")
        evidence["documents"] = docs
    else:
        evidence["documents"] = {str(d.get("title") or d.get("source")): d.get("chunk_count")
                                 for d in docs}
        total_chunks = sum(int(d.get("chunk_count") or 0) for d in docs)

    health = api.json_ok("GET", f"/embeddings/health/{cid}")
    evidence["embedding_health"] = {k: health.get(k) for k in (
        "total_chunks", "embedded_chunks", "coverage_pct", "embedding_dim", "model")}
    evidence["embedder"] = embedder_info
    assert health.get("embedding_dim") == embedder_info.get("dimension"), (
        f"collection dim {health.get('embedding_dim')} != embedder dim "
        f"{embedder_info.get('dimension')}"
    )
    assert health.get("embedded_chunks") == total_chunks and total_chunks >= len(kb["docs"]), (
        f"embedded {health.get('embedded_chunks')} of {total_chunks} chunks"
    )
    assert float(health.get("coverage_pct") or 0) >= 99.0

    ranks: dict[str, int | None] = {}
    for d in kb["docs"]:
        ranks[d.filename] = rank_of(search(api, cid, d.query), d)
    evidence["search_rank"] = ranks
    misses = [f for f, r in ranks.items() if r is None]
    assert not misses, f"search did not return the right document (top 3) for: {misses}"
    not_top = [f for f, r in ranks.items() if r != 1]
    evidence["not_rank_1"] = not_top

    # A goal that can only be answered from these documents, through an agent
    # bound to this collection.
    agent = api.json_ok("POST", "/agents", json={
        "name": f"rw-kb-analyst-{tag()}", "allowed_collection_ids": [cid],
        "system_prompt": "Answer strictly from the knowledge base and cite the source "
                         "document file name for every fact.",
        "max_iterations": 6, "timeout_seconds": 420})
    agent_id = str(agent.get("agent_id") or agent.get("id"))
    cleanup("DELETE", f"/agents/{agent_id}")
    goal = api.json_ok("POST", "/goals", json={"agent_id": agent_id, "goal": (
        "Using only our knowledge base, answer and cite the source document for each: "
        "(1) When is the Project Halcyon database migration window? "
        "(2) On what date does the Zephyrine Analytics contract auto-renew? "
        "(3) In which city does Quokka Pay launch first?")})
    goal_id = str(goal.get("goal_id") or goal.get("id"))
    evidence["goal_id"] = goal_id
    cleanup("POST", f"/goals/{goal_id}/cancel")
    final = wait_until(lambda: api.json_ok("GET", f"/goals/{goal_id}"), timeout=GOAL_TIMEOUT,
                       interval=5, desc=f"KB goal {goal_id}",
                       done=lambda g: g.get("status") in ("complete", "failed", "cancelled"))
    evidence["goal_status"] = final.get("status")
    art = final.get("result_artifact") or {}
    answer = "\n".join(str(x) for x in (final.get("result"), final.get("final_answer"),
                                         art.get("summary"), art.get("body"),
                                         art.get("markdown")) if x)
    evidence["answer_head"] = answer[:500]
    assert final.get("status") == "complete", f"goal ended {final.get('status')}: {answer[:300]}"
    low = _norm(answer)
    facts = {"halcyon": ("14 november" in low or "november 14" in low),
             "zephyrine": ("31 march 2027" in low or "march 31, 2027" in low),
             "quokka": "coimbatore" in low}
    evidence["facts_in_answer"] = facts
    assert all(facts.values()), f"answer misses KB facts {facts}: {answer[:400]}"

    timeline = api.get(f"/goals/{goal_id}/timeline")
    cited: set[str] = set()
    for ev in timeline.json() if timeline.status_code == 200 else []:
        for c in (ev.get("data") or {}).get("citations") or []:
            if str(c.get("collection_id")) == cid:
                cited.add(str((c.get("metadata") or {}).get("source_file")))
    named = {d.filename for d in kb["docs"] if d.filename.lower() in low}
    evidence["retrieved_sources"] = sorted(cited)
    evidence["sources_named_in_answer"] = sorted(named)
    assert {"halcyon-change-notice.pdf", "zephyrine-contract.docx",
            "quokka-pay-launch.pptx"} <= cited, f"goal did not retrieve the KB sources: {cited}"
    evidence_block = art.get("evidence") or {}
    if not (named or evidence_block.get("citations") or final.get("citations")):
        soft.append("the answer names none of the KB source documents (no citations)")
    assert not soft, "; ".join(soft)


@pytest.mark.scenario("KB-REEMBED")
def test_kb_reembed_keeps_search_correct(api: LiveAPI, kb: dict[str, Any],
                                         embedder_info: dict[str, Any],
                                         evidence: dict[str, Any]) -> None:
    cid = kb["collection_id"]
    evidence["collection_id"] = cid
    before = api.json_ok("GET", f"/embeddings/health/{cid}")
    evidence["health_before"] = {k: before.get(k) for k in ("total_chunks", "embedding_dim",
                                                            "needs_reembed", "drift_severity")}

    # 1) URL-sourced document: ingest, then re-embed it through the reingest endpoint.
    soft: list[str] = []
    ing = api.post("/knowledge/ingest/url", json={"collection_id": cid, "url": URL_DOC})
    evidence["url_ingest_http"] = ing.status_code
    assert ing.status_code in (200, 201), f"ingest/url -> {ing.status_code}: {mask(ing.text[:300])}"
    evidence["url_ingest"] = {k: ing.json().get(k) for k in ("chunks_ingested", "document_id",
                                                              "deduplicated")}
    doc_id = str(ing.json().get("document_id") or "")
    if not doc_id:  # ingest/url returns no id: try the listing, then search hits
        listed = _documents(api, cid)
        if not isinstance(listed, str):
            doc_id = str(next((d.get("id") for d in listed if "pep" in str(
                d.get("source") or d.get("source_url") or d.get("title") or "").lower()), ""))
    if not doc_id:
        hits = search(api, cid, "Beautiful is better than ugly", 5)
        doc_id = str(next((h.get("document_id") for h in hits if h.get("document_id")), "") or "")
        evidence["search_hit_document_ids"] = [h.get("document_id") for h in hits]
    evidence["url_document_id"] = doc_id or None
    if not doc_id:
        soft.append("no API exposes the URL document's id (ingest/url returns none, the "
                    "document listing 503s, search hits carry document_id=null) - the "
                    "reingest endpoint cannot be reached")
    else:
        rr = api.post(f"/knowledge/collections/{cid}/documents/{doc_id}/reingest")
        evidence["reingest_http"] = rr.status_code
        evidence["reingest"] = rr.json() if rr.status_code == 200 else mask(rr.text[:300])
        if rr.status_code != 200 or not rr.json().get("chunks_ingested"):
            soft.append(f"reingest -> {rr.status_code}: {mask(rr.text[:200])}")

    # 2) Uploaded file: the documented re-embed path is delete + upload again.
    target = next(d for d in kb["docs"] if d.filename.endswith(".docx"))
    old_id = (kb["uploads"][target.filename]["body"] or {}).get("document_id")
    assert old_id, "upload response carried no document_id"
    dele = api.delete(f"/knowledge/collections/{cid}/documents/{old_id}")
    evidence["delete_http"] = dele.status_code
    assert dele.status_code in (200, 204), f"delete doc -> {dele.status_code}"
    up = api.post("/knowledge/ingest/file", data={"collection_id": cid},
                  files={"file": (target.filename, target.data, target.mime)})
    body = up.json() if up.status_code < 300 else {}
    evidence["reupload"] = {"http": up.status_code, "chunks": body.get("chunks_created"),
                            "deduplicated": body.get("deduplicated")}
    assert up.status_code in (200, 201) and body.get("chunks_created"), (
        f"re-upload after delete was not re-embedded: {mask(up.text[:300])}"
    )
    time.sleep(2)

    after = api.json_ok("GET", f"/embeddings/health/{cid}")
    evidence["health_after"] = {k: after.get(k) for k in ("total_chunks", "embedded_chunks",
                                                          "coverage_pct", "embedding_dim")}
    assert after.get("embedding_dim") == embedder_info.get("dimension")
    assert float(after.get("coverage_pct") or 0) >= 99.0
    ranks = {d.filename: rank_of(search(api, cid, d.query), d) for d in kb["docs"]}
    ranks["pep-0020 (url)"] = 1 if any("better than ugly" in str(h.get("content", "")).lower()
                                       for h in search(api, cid, "Beautiful is better than "
                                                       "ugly")) else None
    evidence["search_rank_after"] = ranks
    misses = [f for f, r in ranks.items() if r is None]
    assert not misses, f"search lost documents after re-embedding: {misses}"
    assert not soft, "; ".join(soft)
