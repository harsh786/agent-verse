"""Adaptive RAG pattern adapter."""
from __future__ import annotations

from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState


class AdaptiveRAGPattern(RAGPattern):
    @property
    def pattern_id(self) -> str:
        return "adaptive_rag"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.PLANNED

    @property
    def description(self) -> str:
        return "Adaptive RAG: selects no-retrieval/single-hop/multi-hop per query"
