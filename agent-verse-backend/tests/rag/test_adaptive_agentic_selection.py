"""D-6: the adaptive RAG selector routes ~11 of 18 strategies but never selected
AGENTIC, even though the gateway has a dispatchable AgenticRAGRuntimeAdapter for
it. A multi-step research/investigation query should auto-select agentic RAG when
that capability is available — and fall through safely when it is not.
"""

from __future__ import annotations

from app.rag.agentic.patterns.adaptive import select_adaptive_strategy
from app.rag.contracts import RAGStrategy

_WITH_AGENTIC = (RAGStrategy.AGENTIC, RAGStrategy.HYBRID, RAGStrategy.NAIVE)
_WITHOUT_AGENTIC = (RAGStrategy.HYBRID, RAGStrategy.NAIVE)


def test_multi_step_research_query_selects_agentic_when_available() -> None:
    for query in (
        "research the origins of the framework step by step",
        "investigate why the deployment failed and gather the evidence",
        "look into how the auth flow works, multi-step",
    ):
        decision = select_adaptive_strategy(query, _WITH_AGENTIC)
        assert decision.strategy is RAGStrategy.AGENTIC, query
        assert "agentic" in decision.reason


def test_agentic_query_falls_through_when_agentic_unavailable() -> None:
    decision = select_adaptive_strategy(
        "research this step by step and investigate", _WITHOUT_AGENTIC
    )
    assert decision.strategy is not RAGStrategy.AGENTIC
    assert decision.strategy in {RAGStrategy.HYBRID, RAGStrategy.NAIVE}


def test_plain_lookup_does_not_select_agentic() -> None:
    decision = select_adaptive_strategy("what is a vector database", _WITH_AGENTIC)
    assert decision.strategy is not RAGStrategy.AGENTIC
