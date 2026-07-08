"""Fusion RAG pattern adapter — multi-query parallel retrieval with RRF fusion."""
from __future__ import annotations
from typing import Any
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState


class FusionRAGPattern(RAGPattern):
    @property
    def pattern_id(self) -> str:
        return "fusion_rag"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return (
            "Fusion RAG: expand query into N variants via QueryExpander, "
            "run hybrid_search in parallel for each, merge with RRF fusion."
        )

    def is_compatible(self, goal_properties: Any) -> bool:
        complexity = getattr(goal_properties, "complexity", None)
        if complexity is not None:
            return str(complexity).lower() in ("moderate", "complex", "expert")
        return True

    async def execute(
        self,
        *,
        session: Any,
        query: str,
        query_embedding: list[float] | None,
        collection_id: str,
        top_k: int = 10,
        max_variants: int = 3,
        embedding_dim: int | None = None,
        **kwargs: Any,
    ) -> list[Any]:
        from app.rag.engine import retrieve_fusion
        return await retrieve_fusion(
            session,
            query=query,
            query_embedding=query_embedding,
            collection_id=collection_id,
            top_k=top_k,
            max_variants=max_variants,
            embedding_dim=embedding_dim,
        )
