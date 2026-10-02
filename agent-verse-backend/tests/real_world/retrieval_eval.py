"""Scoring of known-answer questions against search results and RAG answers.

Shared by KB-RETRIEVAL-HARD and KB-STRATEGIES; pure functions over API bodies so
the scoring itself is unit-tested offline.
"""

from __future__ import annotations

from typing import Any

from tests.real_world.metrics import (
    answer_correct,
    hit_at_k,
    hit_is_correct,
    mrr,
    norm,
    percentiles,
    rank_of,
    source_matches,
    source_of,
)


def question_rank(hits: list[dict[str, Any]], q: dict[str, Any], k: int = 5) -> int | None:
    """Rank of the first correct hit. A cross-document question also needs every
    expected source somewhere in the top ``k`` (it cannot be answered from one)."""
    r = rank_of(hits, q["expected_sources"], q["chunk_must_contain"])
    if q.get("kind") != "cross_doc":
        return r
    top = [source_of(h) for h in hits[:k]]
    groups = _source_groups(q["expected_sources"])
    if all(any(source_matches(s, g) for s in top) for g in groups):
        return r or k
    return None


def _source_groups(expected: list[str]) -> list[list[str]]:
    """Archive members are alternatives of their archive; distinct files are separate."""
    if any(e.endswith(".zip") for e in expected):
        return [expected]
    return [[e] for e in expected]


def citations_correct(citations: list[dict[str, Any]], q: dict[str, Any],
                      expected_page: int | None = None) -> bool:
    """A citation points at the right document AND the chunk holding the fact
    (and, for a PDF fact, the right page)."""
    for c in citations:
        if not hit_is_correct(c, q["expected_sources"], q["chunk_must_contain"]):
            continue
        if expected_page is None:
            return True
        page = (c.get("metadata") or {}).get("page", c.get("page"))
        if str(page) == str(expected_page):
            return True
    return False


def score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-question rows into the scenario metrics.

    A row: {id, kind, rank, answered (bool|None), cited (bool|None), search_ms, rag_ms}.
    ``answered``/``cited`` None = no RAG answer was produced for the question.
    """
    ranks = [r.get("rank") for r in rows]
    answered = [r for r in rows if r.get("answered") is not None]
    cited = [r for r in rows if r.get("cited") is not None]
    by_kind: dict[str, dict[str, Any]] = {}
    for r in rows:
        k = by_kind.setdefault(r["kind"], {"n": 0, "top5": 0, "correct": 0})
        k["n"] += 1
        k["top5"] += 1 if r.get("rank") and r["rank"] <= 5 else 0
        k["correct"] += 1 if r.get("answered") else 0
    return {
        "questions": len(rows),
        "hit_at_1": round(hit_at_k(ranks, 1), 3),
        "hit_at_5": round(hit_at_k(ranks, 5), 3),
        "mrr": round(mrr(ranks), 3),
        "answer_accuracy": round(sum(1 for r in answered if r["answered"]) / len(answered), 3)
        if answered else 0.0,
        "answers_produced": len(answered),
        "citation_accuracy": round(sum(1 for r in cited if r["cited"]) / len(cited), 3)
        if cited else 0.0,
        "search_latency_ms": percentiles([r["search_ms"] for r in rows if r.get("search_ms")]),
        "rag_latency_ms": percentiles([r["rag_ms"] for r in rows if r.get("rag_ms")]),
        "by_kind": by_kind,
    }


def judge_answer(answer: str, q: dict[str, Any]) -> bool:
    return answer_correct(answer, q)


def contains_fact(text: str, q: dict[str, Any]) -> bool:
    return norm(q["chunk_must_contain"]) in norm(text)
