"""FLARE pattern adapter."""
from __future__ import annotations

from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState


class FLAREPattern(RAGPattern):
    @property
    def pattern_id(self) -> str:
        return "flare"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.PLANNED

    @property
    def description(self) -> str:
        return "FLARE: retrieve when model is uncertain about next sentence"
