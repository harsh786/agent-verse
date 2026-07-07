"""Fusion RAG pattern adapter."""
from __future__ import annotations

from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState


class FusionRAGPattern(RAGPattern):
    @property
    def pattern_id(self) -> str:
        return "fusion_rag"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.PARTIAL

    @property
    def description(self) -> str:
        return "Fusion RAG: multi-query expansion + RRF result fusion"
