"""P2-7: a strategy's own answer without [N] citations is not returned as 200.

Live P0 KB-STRATEGIES: the agentic strategy answered without citation markers,
so verification reported grounded=false, yet /rag/query returned 200. Now:
the agentic prompt requires [N] markers; an answer a strategy produced that
fails verification is re-synthesized from the citations (the citation-requiring
synthesis /knowledge/chat uses) and verified again; and an answer that is
still ungrounded is a 422 ``answer_ungrounded`` on /rag/query, as on
/knowledge/chat.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.rag.contracts import RAGCitation, RAGExecutionResult, RAGStrategy
from app.rag_platform.retriever import CitationVerification, RAGRetriever
from app.tenancy.context import TenantContext

_TENANT = TenantContext(tenant_id="t-ground", api_key_id="k", plan="enterprise")
_EVIDENCE = "Total H1 diesel_cost_inr=24979830"


class _Gateway:
    def __init__(self, result: RAGExecutionResult) -> None:
        self._result = result

    async def execute(self, tenant_ctx: Any, **_: Any) -> RAGExecutionResult:
        return self._result


class _MarkerVerifier:
    """Grounded iff the answer cites [1] (stands in for the entailment check)."""

    def __init__(self) -> None:
        self.answers: list[str] = []

    async def verify(self, answer: str, citations: list[RAGCitation]) -> CitationVerification:
        self.answers.append(answer)
        ok = "[1]" in answer
        return CitationVerification(ok, [] if ok else [answer], "supported" if ok else "unsupported")


def _result(strategy: RAGStrategy, answer: str) -> RAGExecutionResult:
    return RAGExecutionResult(
        requested_strategy_id=strategy.value,
        resolved_strategy_id=strategy,
        citations=[
            RAGCitation(citation_id="c1", chunk_id="k1", content=_EVIDENCE, score=0.9, source="s")
        ],
        answer=answer,
    )


def _retriever(
    result: RAGExecutionResult, synthesized: str | None
) -> tuple[RAGRetriever, _MarkerVerifier, list[str]]:
    verifier = _MarkerVerifier()
    retriever = RAGRetriever(gateway=_Gateway(result), citation_verifier=verifier)
    calls: list[str] = []

    async def synthesize(**kwargs: Any) -> str:
        calls.append(kwargs["query"])
        assert synthesized is not None
        return synthesized

    retriever.synthesize = synthesize  # type: ignore[method-assign]
    return retriever, verifier, calls


@pytest.mark.asyncio
async def test_uncited_strategy_answer_is_resynthesized_with_citations() -> None:
    retriever, verifier, calls = _retriever(
        _result(RAGStrategy.AGENTIC, "The H1 diesel cost was 24,979,830 INR."),
        "The H1 diesel cost was INR 24,979,830 [1].",
    )
    out = await retriever.retrieve("H1 diesel cost?", _TENANT, collection_id="c")
    assert out.grounded is True
    assert out.answer == "The H1 diesel cost was INR 24,979,830 [1]."
    assert calls == ["H1 diesel cost?"]
    assert len(verifier.answers) == 2
    resynth = [t for t in out.strategy_trace if t.action == "answer_resynthesized"]
    assert resynth and resynth[0].detail["original_reason"] == "unsupported"


@pytest.mark.asyncio
async def test_cited_strategy_answer_is_kept() -> None:
    retriever, _, calls = _retriever(
        _result(RAGStrategy.AGENTIC, "Diesel cost INR 24,979,830 [1]."), None
    )
    out = await retriever.retrieve("q", _TENANT, collection_id="c")
    assert out.grounded is True and calls == []


@pytest.mark.asyncio
async def test_still_ungrounded_after_resynthesis_stays_ungrounded() -> None:
    retriever, _, _ = _retriever(
        _result(RAGStrategy.AGENTIC, "uncited"), "still uncited, no markers"
    )
    out = await retriever.retrieve("q", _TENANT, collection_id="c")
    assert out.grounded is False


@pytest.mark.asyncio
async def test_raft_answers_are_never_resynthesized() -> None:
    retriever, _, calls = _retriever(_result(RAGStrategy.RAFT, "fine-tuned answer"), "x [1]")
    out = await retriever.retrieve("q", _TENANT, collection_id="c")
    assert out.grounded is False and calls == []


def test_agentic_prompt_requires_citation_markers() -> None:
    from app.rag.agentic.patterns import agentic

    assert "[N]" in agentic._DECISION_SYSTEM
    assert agentic._DECISION_MAX_TOKENS >= 512
