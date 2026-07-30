"""Bounded capability-aware Adaptive RAG selection."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState
from app.rag.contracts import RAGStrategy
from app.rag.engine import RetrievalPlanner, retrieve


@dataclass(frozen=True, slots=True)
class AdaptiveDecision:
    strategy: RAGStrategy
    reason: str
    decision_count: int = 1


def select_adaptive_strategy(
    query: str,
    available_strategies: tuple[RAGStrategy, ...],
) -> AdaptiveDecision:
    """Make exactly one non-recursive decision from certified capabilities."""

    available = set(available_strategies) - {RAGStrategy.ADAPTIVE}
    if not available:
        raise ValueError("No certified retrieval strategy is currently available")
    normalized = query.lower()

    if any(term in normalized for term in ("graph", "relationship", "connected")):
        if RAGStrategy.GRAPH in available:
            return AdaptiveDecision(RAGStrategy.GRAPH, "graph_query; graph_available")
        unavailable_reason = "graph_unavailable; "
    else:
        unavailable_reason = ""

    if any(term in normalized for term in ("current", "latest", "web", "internet")):
        if RAGStrategy.WEB_AUGMENTED in available:
            return AdaptiveDecision(
                RAGStrategy.WEB_AUGMENTED,
                "freshness_query; web_augmented_available",
            )
        unavailable_reason += "web_augmented_unavailable; "
    if any(term in normalized for term in ("compare", "contrast", "across")):
        if RAGStrategy.MULTI_HOP in available:
            return AdaptiveDecision(RAGStrategy.MULTI_HOP, "comparison_query; multi_hop_available")
        unavailable_reason += "multi_hop_unavailable; "
    if normalized.startswith(("what is", "explain", "describe")):
        if RAGStrategy.HYDE in available:
            return AdaptiveDecision(RAGStrategy.HYDE, "abstract_query; hyde_available")
        unavailable_reason += "hyde_unavailable; "
    if any(term in normalized for term in ("verify", "uncertain", "correct")):
        if RAGStrategy.CORRECTIVE in available:
            return AdaptiveDecision(
                RAGStrategy.CORRECTIVE,
                "verification_query; corrective_available",
            )
        unavailable_reason += "corrective_unavailable; "
    if RAGStrategy.HYBRID in available:
        return AdaptiveDecision(
            RAGStrategy.HYBRID,
            f"{unavailable_reason}selected_default_hybrid",
        )
    if RAGStrategy.NAIVE in available:
        return AdaptiveDecision(
            RAGStrategy.NAIVE,
            f"{unavailable_reason}hybrid_unavailable; selected_naive",
        )
    selected = sorted(available, key=lambda strategy: strategy.value)[0]
    return AdaptiveDecision(
        selected,
        f"{unavailable_reason}selected_only_available_capability",
    )


class AdaptiveRAGPattern(RAGPattern):
    @property
    def pattern_id(self) -> str:
        return "adaptive_rag"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return (
            "Adaptive RAG: uses RetrievalPlanner to auto-select strategy "
            "(lexical / multi_hop / hyde / fusion / direct) based on query heuristics."
        )

    def is_compatible(self, goal_properties: Any) -> bool:
        return True

    async def execute(
        self,
        *,
        session: Any,
        query: str,
        query_embedding: list[float] | None,
        collection_id: str,
        top_k: int = 10,
        provider: Any = None,
        embedding_dim: int | None = None,
        force_strategy: str | None = None,
        **kwargs: Any,
    ) -> list[Any]:
        strategy = force_strategy or RetrievalPlanner().select_strategy(query)
        return await retrieve(
            session,
            query=query,
            query_embedding=query_embedding,
            collection_id=collection_id,
            top_k=top_k,
            strategy=strategy,
            provider=provider,
            embedding_dim=embedding_dim,
        )
