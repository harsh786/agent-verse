"""Adaptive RAG pattern adapter — wired to RetrievalPlanner.select_strategy()."""
from __future__ import annotations
from typing import Any

from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState
from app.rag.engine import retrieve, RetrievalPlanner


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
