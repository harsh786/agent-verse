"""MONGO-FAIL-DUPLICATE-SOURCE (defence in depth): a document indexed twice is
served once.

Deployments that registered the same upstream target twice into one collection
(before ``POST /sources`` refused it) hold every document twice: same
``source_url``, different ``source_id``. Retrieval collapses hits by canonical
document identity (canonical ``source_url`` + passage text), keeping the
best-ranked copy; different passages of one document and hits without a URL are
all kept.
"""

from __future__ import annotations

from app.rag.contracts import RAGCitation, RAGExecutionResult, RAGStrategy
from app.rag.duplicate_documents import collapse_duplicate_documents, document_identity
from app.rag.engine import RetrievalResult
from app.rag.gateway import _collapse_duplicate_citations

_PM = "PM-2026-014 settlement webhook outage: root cause was an expired TLS certificate."
_URL_A = "mongodb://rs-a.example.com:27017,rs-b.example.com:27017/rw_source/postmortems/PM-2026-014"
# The same document as the duplicate Source spelled it: other host order, user-info.
_URL_B = "mongodb://reader@RS-B.example.com,rs-a.example.com/rw_source/postmortems/PM-2026-014"


def _result(chunk: str, score: float, url: str, content: str, source: str) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk,
        content=content,
        score=score,
        source_metadata={"source_url": url, "source_id": source},
    )


def test_same_passage_from_two_sources_is_returned_once_best_first() -> None:
    results = [
        _result("c1", 0.9, _URL_A, _PM, "src-1"),
        _result("c2", 0.8, _URL_B, "  " + _PM.replace(" ", "  ") + "\n", "src-2"),
        _result("c3", 0.7, _URL_A, "Timeline: 09:14 alerts fired.", "src-1"),
        _result("c4", 0.6, "", _PM, "upload"),  # no URL: never collapsed
    ]
    kept, dropped = collapse_duplicate_documents(
        results, lambda r: document_identity(r.source_metadata, r.content)
    )
    assert [r.chunk_id for r in kept] == ["c1", "c3", "c4"]
    assert dropped == 1


def test_different_documents_with_identical_text_are_both_kept() -> None:
    a = document_identity({"source_url": _URL_A}, _PM)
    b = document_identity({"source_url": _URL_A.replace("PM-2026-014", "PM-2026-015")}, _PM)
    assert a is not None and b is not None and a != b


def _citation(index: int, url: str, content: str, source: str) -> RAGCitation:
    return RAGCitation(
        citation_id=f"citation-{index}",
        chunk_id=f"chunk-{index}",
        content=content,
        score=1.0 / index,
        source=url,
        metadata={"source_url": url, "source_id": source},
    )


def test_gateway_never_cites_the_same_postmortem_twice() -> None:
    result = RAGExecutionResult(
        requested_strategy_id="hybrid",
        resolved_strategy_id=RAGStrategy.HYBRID,
        citations=[
            _citation(1, _URL_A, _PM, "src-1"),
            _citation(2, _URL_B, _PM, "src-2"),
            _citation(3, "https://wiki.example.com/tls-runbook", "Rotate certs.", "src-3"),
        ],
        grounded=True,
    )
    out = _collapse_duplicate_citations(result, RAGStrategy.HYBRID)
    assert [c.chunk_id for c in out.citations] == ["chunk-1", "chunk-3"]
    assert out.grounded is True
    assert out.strategy_trace[-1].action == "duplicate_documents_collapsed"
    assert out.strategy_trace[-1].detail == {"dropped": 1}


def test_gateway_leaves_a_result_without_duplicates_untouched() -> None:
    result = RAGExecutionResult(
        requested_strategy_id="hybrid",
        resolved_strategy_id=RAGStrategy.HYBRID,
        citations=[_citation(1, _URL_A, _PM, "src-1")],
        grounded=True,
    )
    assert _collapse_duplicate_citations(result, RAGStrategy.HYBRID) is result
