"""Corrective RAG pattern adapter."""
from __future__ import annotations

from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState


class CorrectiveRAGPattern(RAGPattern):
    @property
    def pattern_id(self) -> str:
        return "corrective_rag"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.PARTIAL

    @property
    def description(self) -> str:
        return "CRAG: evaluate retrieval quality, correct via web search"
