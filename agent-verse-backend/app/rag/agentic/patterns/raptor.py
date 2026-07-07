"""RAPTOR pattern adapter."""
from __future__ import annotations

from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState


class RAPTORPattern(RAGPattern):
    @property
    def pattern_id(self) -> str:
        return "raptor"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.PLANNED

    @property
    def description(self) -> str:
        return "RAPTOR: hierarchical summarization + tree retrieval"
