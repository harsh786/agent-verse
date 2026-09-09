"""Row-6 wiring: the default RAG gateway path must rerank and surface a
calibrated confidence on each citation (previously it returned raw engine scores
with no reranking and no calibrated_confidence)."""

from __future__ import annotations

from app.rag.contracts import RAGExecutionRequest, RAGStrategy
from app.rag.engine import RetrievalResult
from app.rag.gateway import _canonical_result


def _req() -> RAGExecutionRequest:
    return RAGExecutionRequest(
        tenant_id="t1", query="what is the revenue?", requested_strategy_id="hybrid"
    )


def _result(chunk_id: str, score: float) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        content=f"content for {chunk_id}",
        score=score,
        source_metadata={"source": "doc-1"},
    )


def test_citations_carry_calibrated_confidence() -> None:
    results = [_result("c1", 0.9), _result("c2", 0.4), _result("c3", 0.7)]
    out = _canonical_result(_req(), RAGStrategy.HYBRID, results, [])

    # Additive: no citations dropped.
    assert len(out.citations) == 3
    # Every citation now carries a calibrated_confidence (was absent/None before).
    for cit in out.citations:
        conf = cit.metadata.get("calibrated_confidence")
        assert conf is not None
        assert 0.0 <= float(conf) <= 1.0


def test_strategy_order_is_preserved() -> None:
    # The gateway must NOT re-sort — the resolved strategy owns ranking (e.g.
    # code-RAG's symbol boost). Calibration is additive; order is untouched.
    results = [_result("c1", 0.4), _result("c2", 0.9), _result("c3", 0.7)]
    out = _canonical_result(_req(), RAGStrategy.HYBRID, results, [])
    assert [c.chunk_id for c in out.citations] == ["c1", "c2", "c3"]
    # Raw scores are preserved (never mutated by calibration).
    assert out.citations[0].score == 0.4
    # Higher raw score → higher calibrated confidence.
    conf = {c.chunk_id: c.metadata["calibrated_confidence"] for c in out.citations}
    assert conf["c2"] > conf["c1"]


def test_empty_results_are_safe() -> None:
    out = _canonical_result(_req(), RAGStrategy.HYBRID, [], [])
    assert out.citations == []
    assert out.grounded is False
