"""ColBERT late interaction pattern adapter."""
from __future__ import annotations

from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState


class ColBERTPattern(RAGPattern):
    @property
    def pattern_id(self) -> str:
        return "colbert_late_interaction"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.PLANNED

    @property
    def description(self) -> str:
        return "ColBERT: MaxSim late interaction reranking"
