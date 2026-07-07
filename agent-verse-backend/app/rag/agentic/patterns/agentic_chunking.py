"""Agentic Chunking pattern adapter."""
from __future__ import annotations

from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState


class AgenticChunkingPattern(RAGPattern):
    @property
    def pattern_id(self) -> str:
        return "agentic_chunking"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.PLANNED

    @property
    def description(self) -> str:
        return "Agentic Chunking: LLM-driven proposition extraction as chunks"
