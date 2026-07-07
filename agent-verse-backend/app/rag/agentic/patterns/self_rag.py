"""Self-RAG pattern adapter."""
from __future__ import annotations

from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState


class SelfRAGPattern(RAGPattern):
    @property
    def pattern_id(self) -> str:
        return "self_rag"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.PLANNED

    @property
    def description(self) -> str:
        return "Self-RAG: retrieve on demand + self-critique tokens"
