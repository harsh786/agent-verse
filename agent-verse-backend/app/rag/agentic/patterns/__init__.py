"""RAG pattern adapters — registry of all 9 doc-1 RAG patterns."""
from __future__ import annotations

from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState
from app.rag.agentic.patterns.corrective import CorrectiveRAGPattern
from app.rag.agentic.patterns.adaptive import AdaptiveRAGPattern
from app.rag.agentic.patterns.self_rag import SelfRAGPattern
from app.rag.agentic.patterns.speculative import SpeculativeRAGPattern
from app.rag.agentic.patterns.fusion import FusionRAGPattern
from app.rag.agentic.patterns.flare import FLAREPattern
from app.rag.agentic.patterns.raptor import RAPTORPattern
from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern
from app.rag.agentic.patterns.colbert import ColBERTPattern

ALL_RAG_PATTERNS: list[RAGPattern] = [
    CorrectiveRAGPattern(),
    AdaptiveRAGPattern(),
    SelfRAGPattern(),
    SpeculativeRAGPattern(),
    FusionRAGPattern(),
    FLAREPattern(),
    RAPTORPattern(),
    AgenticChunkingPattern(),
    ColBERTPattern(),
]

__all__ = [
    "RAGPattern",
    "RAGPatternState",
    "ALL_RAG_PATTERNS",
    "CorrectiveRAGPattern",
    "AdaptiveRAGPattern",
    "SelfRAGPattern",
    "SpeculativeRAGPattern",
    "FusionRAGPattern",
    "FLAREPattern",
    "RAPTORPattern",
    "AgenticChunkingPattern",
    "ColBERTPattern",
]
