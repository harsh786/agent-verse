"""Corrective RAG (CRAG) pattern adapter."""
from __future__ import annotations
from typing import Any
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState


class CorrectiveRAGPattern(RAGPattern):
    @property
    def pattern_id(self) -> str:
        return "corrective_rag"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return (
            "CRAG: evaluate retrieval quality score and context gap signals; "
            "automatically fall back to web search when confidence < threshold "
            "or gap phrases detected."
        )

    def is_compatible(self, goal_properties: Any) -> bool:
        return True

    async def execute(
        self,
        *,
        retriever_tool: Any,
        query: str,
        tenant_ctx: Any,
        collection_ids: list[str] | None = None,
        top_k: int = 5,
        confidence_threshold: float = 0.5,
        **kwargs: Any,
    ) -> Any:
        return await retriever_tool.retrieve_corrective(
            query=query,
            tenant_ctx=tenant_ctx,
            collection_ids=collection_ids,
            top_k=top_k,
            confidence_threshold=confidence_threshold,
        )
