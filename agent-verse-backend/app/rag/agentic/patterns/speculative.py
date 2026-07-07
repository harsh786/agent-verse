"""Speculative RAG pattern adapter."""
from __future__ import annotations

from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState


class SpeculativeRAGPattern(RAGPattern):
    @property
    def pattern_id(self) -> str:
        return "speculative_rag"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.PLANNED

    @property
    def description(self) -> str:
        return "Speculative RAG: parallel hypothesis generation + verification"
