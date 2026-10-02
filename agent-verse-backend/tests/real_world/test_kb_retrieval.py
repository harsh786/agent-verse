"""KB-RETRIEVAL-HARD, KB-TENANT-ISOLATION and KB-STRATEGIES over the complex corpus.

KB-RETRIEVAL-HARD: 29 known-answer questions (table lookups, cross-document
comparisons, negations, numeric facts, speaker notes, OCR, archive members).
Measures hit@1/hit@5/MRR through search and answer correctness + citation
correctness through the platform's own RAG answer (POST /rag/query), and asserts
thresholds (RW_HIT5_MIN, RW_ANSWER_ACC_MIN, RW_CITATION_ACC_MIN).

KB-TENANT-ISOLATION: a second tenant (RW_SECOND_TENANT_API_KEY) gets nothing from
the first tenant's collection — not by id, not by search, not by RAG.

KB-STRATEGIES: the same questions through every requestable RAG strategy (ColBERT
included once it reports ready); per-strategy quality + latency land in the report.
An unavailable strategy must answer 503 (never a 200 with nothing, never a 500).
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from tests.real_world import kb
from tests.real_world.corpus import CorpusDoc, load_questions
from tests.real_world.helpers import LiveAPI, env_float, mask
from tests.real_world.metrics import record, source_matches, source_of
from tests.real_world.retrieval_eval import citations_correct, judge_answer, question_rank, score

HIT5_MIN = env_float("RW_HIT5_MIN", 0.8)
ANSWER_ACC_MIN = env_float("RW_ANSWER_ACC_MIN", 0.7)
CITATION_ACC_MIN = env_float("RW_CITATION_ACC_MIN", 0.7)
STRATEGY_HIT5_MIN = env_float("RW_STRATEGY_HIT5_MIN", 0.5)
STRATEGY_QUESTIONS = int(os.getenv("RW_STRATEGY_QUESTIONS", "10"))


def _ingested(complex_kb: dict[str, Any]) -> set[str]:
    return {f for f, u in complex_kb["uploads"].items() if u["http"] in (200, 201)
            and u["chunks"] > 0}


def _answerable(complex_kb: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Questions whose documents were ingested; the rest are listed (not silently dropped)
    — their format failure is already reported by KB-COMPLEX-CORPUS."""
    ok = _ingested(complex_kb)
    keep, excluded = [], []
    for q in load_questions():
        if any(source_matches(f, q["expected_sources"]) for f in ok):
            keep.append(q)
        else:
            excluded.append(q["id"])
    return keep, excluded


def _expected_page(complex_kb: dict[str, Any], q: dict[str, Any]) -> int | None:
    if not q.get("page_fact"):
        return None
    pdf: CorpusDoc = next(d for d in complex_kb["docs"] if d.fmt == "pdf")
    return pdf.fact_pages.get(q["page_fact"])


def _run_questions(api: LiveAPI, complex_kb: dict[str, Any], questions: list[dict[str, Any]],
                   strategy: str = "hybrid", search_too: bool = True
                   ) -> tuple[list[dict[str, Any]], list[str]]:
    cid = complex_kb["collection_id"]
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for q in questions:
        row: dict[str, Any] = {"id": q["id"], "kind": q["kind"], "rank": None,
                               "answered": None, "cited": None}
        if search_too:
            import time

            started = time.monotonic()
            hits = kb.search(api, cid, q["question"], top_k=5)
            row["search_ms"] = (time.monotonic() - started) * 1000
            row["rank"] = question_rank(hits, q)
        status, body, ms = kb.rag_query(api, cid, q["question"], strategy=strategy, top_k=5)
        row["rag_ms"] = ms
        row["http"] = status
        if status != 200:
            errors.append(f"{q['id']}: /rag/query({strategy}) -> {status} "
                          f"{mask(body)[:160]}")
            rows.append(row)
            continue
        citations = list(body.get("citations") or [])
        if not search_too:
            row["rank"] = question_rank(citations, q)
        answer = kb.answer_text(body)
        if answer.strip():
            row["answered"] = judge_answer(answer, q)
            row["answer_head"] = answer[:160]
        row["cited"] = citations_correct(citations, q, _expected_page(complex_kb, q)) \
            if citations else False
        row["cited_sources"] = sorted({source_of(c) for c in citations})[:4]
        rows.append(row)
    return rows, errors


@pytest.mark.scenario("KB-RETRIEVAL-HARD")
def test_kb_retrieval_hard(api: LiveAPI, complex_kb: dict[str, Any],
                           evidence: dict[str, Any]) -> None:
    questions, excluded = _answerable(complex_kb)
    evidence.update(collection_id=complex_kb["collection_id"], excluded_not_ingested=excluded)
    assert len(questions) >= 25, (
        f"only {len(questions)} questions answerable: their documents were not ingested "
        f"({excluded})"
    )
    rows, errors = _run_questions(api, complex_kb, questions)
    metrics = score(rows)
    record(evidence, **{k: v for k, v in metrics.items() if k != "by_kind"})
    evidence["by_kind"] = metrics["by_kind"]
    evidence["misses"] = [r["id"] for r in rows if not r["rank"] or r["rank"] > 5]
    evidence["wrong_answers"] = {r["id"]: r.get("answer_head") for r in rows
                                 if r["answered"] is False}
    evidence["bad_citations"] = {r["id"]: r.get("cited_sources") for r in rows
                                 if r["cited"] is False}
    assert not errors, f"{len(errors)} RAG queries failed: {errors[:5]}"
    assert metrics["answers_produced"] == len(rows), (
        f"/rag/query produced no answer text for "
        f"{len(rows) - metrics['answers_produced']} of {len(rows)} questions"
    )
    soft = []
    if metrics["hit_at_5"] < HIT5_MIN:
        soft.append(f"hit@5 {metrics['hit_at_5']:.2f} < {HIT5_MIN} (misses {evidence['misses']})")
    if metrics["answer_accuracy"] < ANSWER_ACC_MIN:
        soft.append(f"answer accuracy {metrics['answer_accuracy']:.2f} < {ANSWER_ACC_MIN}")
    if metrics["citation_accuracy"] < CITATION_ACC_MIN:
        soft.append(f"citation accuracy {metrics['citation_accuracy']:.2f} < {CITATION_ACC_MIN}")
    assert not soft, "; ".join(soft)


@pytest.mark.scenario("KB-TENANT-ISOLATION")
def test_kb_tenant_isolation(api: LiveAPI, complex_kb: dict[str, Any],
                             second_tenant_api: LiveAPI | None, tenant_id: str,
                             evidence: dict[str, Any]) -> None:
    if second_tenant_api is None:
        pytest.skip("needs RW_SECOND_TENANT_API_KEY or RW_SECOND_TENANT_FILE (a key of a "
                    "different tenant) to prove cross-tenant isolation")
    other = second_tenant_api
    me = other.json_ok("GET", "/tenants/me")
    assert str(me.get("tenant_id")).replace("-", "") != tenant_id.replace("-", ""), (
        "RW_SECOND_TENANT_API_KEY belongs to the SAME tenant"
    )
    cid = complex_kb["collection_id"]
    leaks: list[str] = []
    probe = "What is the status of shipment SHP-2026-04417?"
    s = other.get("/knowledge/search", params={"q": probe, "collection_id": cid, "top_k": 5})
    evidence["foreign_search_http"] = s.status_code
    if s.status_code == 200 and s.json():
        leaks.append(f"search of the other tenant's collection returned {len(s.json())} hits")
    r = other.post("/rag/query", json={"query": probe, "collection_id": cid, "top_k": 5})
    evidence["foreign_rag_http"] = r.status_code
    if r.status_code == 200 and (r.json().get("citations") or "customs" in str(
            r.json().get("answer", "")).lower()):
        leaks.append("RAG over the other tenant's collection returned its content")
    d = other.get(f"/knowledge/collections/{cid}/documents")
    evidence["foreign_documents_http"] = d.status_code
    if d.status_code == 200 and (d.json().get("documents") or []):
        leaks.append(f"document listing of the other tenant's collection: "
                     f"{len(d.json()['documents'])} documents")
    cols = other.get("/knowledge/collections")
    listed = cols.json() if cols.status_code == 200 else []
    listed = listed.get("collections", listed) if isinstance(listed, dict) else listed
    if any(str(c.get("collection_id") or c.get("id")) == cid for c in listed or []):
        leaks.append("the other tenant's collection is listed")
    h = other.get(f"/embeddings/health/{cid}")
    evidence["foreign_health_http"] = h.status_code
    if h.status_code == 200 and int(h.json().get("total_chunks") or 0) > 0:
        leaks.append("embedding health of the other tenant's collection exposes its chunks")
    # Federated / collection-less RAG of the second tenant must not reach tenant 1's data.
    fr = other.post("/rag/query", json={"query": probe, "top_k": 5})
    evidence["second_tenant_global_rag_http"] = fr.status_code
    if fr.status_code == 200 and any("SHP-2026-04417" in str(c.get("content", ""))
                                     for c in fr.json().get("citations") or []):
        leaks.append("a collection-less RAG query of tenant 2 cited tenant 1's ledger")
    evidence["leaks"] = leaks
    assert not leaks, "; ".join(leaks)


@pytest.mark.scenario("KB-STRATEGIES")
def test_kb_strategies(api: LiveAPI, complex_kb: dict[str, Any],
                       evidence: dict[str, Any]) -> None:
    listing = api.json_ok("GET", "/rag/strategies").get("strategies") or []
    wanted = [s.strip() for s in os.getenv("RW_STRATEGIES", "").split(",") if s.strip()]
    catalogue = {s["id"]: s for s in listing}
    evidence["catalogue"] = {k: {"available": v.get("available"), "state": v.get("state"),
                                 "reason": v.get("unavailable_reason")}
                             for k, v in catalogue.items()}
    assert "hybrid" in catalogue and catalogue["hybrid"].get("available"), (
        f"the default hybrid strategy is not available: {catalogue.get('hybrid')}"
    )
    questions, _ = _answerable(complex_kb)
    # A fixed, mixed subset (every kind represented early) keeps LLM-heavy strategies cheap.
    subset: list[dict[str, Any]] = []
    for kind in dict.fromkeys(q["kind"] for q in questions):
        subset.extend([q for q in questions if q["kind"] == kind][:1])
    subset += [q for q in questions if q not in subset]
    subset = subset[:STRATEGY_QUESTIONS]
    results: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for sid in wanted or list(catalogue):
        entry = catalogue.get(sid)
        if entry is None:
            problems.append(f"{sid}: not in GET /rag/strategies")
            continue
        if not entry.get("available"):
            status, body, _ = kb.rag_query(api, complex_kb["collection_id"], subset[0]["question"],
                                           strategy=sid)
            results[sid] = {"available": False, "reason": entry.get("unavailable_reason"),
                            "probe_http": status}
            if status != 503:
                problems.append(f"{sid} is unavailable ({entry.get('unavailable_reason')}) but "
                                f"/rag/query answered {status}, not 503: {mask(body)[:120]}")
            continue
        rows, errors = _run_questions(api, complex_kb, subset, strategy=sid, search_too=False)
        m = score(rows)
        results[sid] = {"available": True, "hit_at_5": m["hit_at_5"], "mrr": m["mrr"],
                        "answer_accuracy": m["answer_accuracy"],
                        "citation_accuracy": m["citation_accuracy"],
                        "p50_ms": m["rag_latency_ms"].get("p50"),
                        "p95_ms": m["rag_latency_ms"].get("p95"), "errors": len(errors)}
        if errors:
            problems.append(f"{sid}: {len(errors)} failed queries ({errors[0]})")
        elif m["hit_at_5"] < STRATEGY_HIT5_MIN:
            problems.append(f"{sid}: hit@5 {m['hit_at_5']:.2f} < {STRATEGY_HIT5_MIN}")
    record(evidence, strategies=results, questions_per_strategy=len(subset))
    evidence["colbert"] = results.get("colbert") or catalogue.get("colbert") or "not listed"
    unknown = api.post("/rag/query", json={"query": "x", "collection_id":
                                           complex_kb["collection_id"], "strategy": "bogus"})
    evidence["unknown_strategy_http"] = unknown.status_code
    if unknown.status_code != 422:
        problems.append(f"an unknown strategy answered {unknown.status_code}, not 422")
    assert not problems, "; ".join(problems)
