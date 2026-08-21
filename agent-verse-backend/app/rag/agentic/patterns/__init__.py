"""RAG pattern adapters exposed by the agentic retrieval package."""

from __future__ import annotations

from app.rag.agentic.patterns.adaptive import AdaptiveRAGPattern
from app.rag.agentic.patterns.agentic import AgenticRAGRuntimeAdapter
from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern
from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState
from app.rag.agentic.patterns.colbert import ColBERTPattern, ColBERTRAGRuntimeAdapter
from app.rag.agentic.patterns.corrective import CorrectiveRAGPattern
from app.rag.agentic.patterns.flare import FLAREPattern, FLARERAGRuntimeAdapter
from app.rag.agentic.patterns.fusion import FusionRAGPattern
from app.rag.agentic.patterns.graph import GraphRAGPattern
from app.rag.agentic.patterns.modular import ModularRAGRuntimeAdapter
from app.rag.agentic.patterns.raft import RAFTRAGRuntimeAdapter
from app.rag.agentic.patterns.raptor import RAPTORPattern
from app.rag.agentic.patterns.self_rag import SelfRAGPattern, SelfRAGRuntimeAdapter
from app.rag.agentic.patterns.speculative import (
    SpeculativeRAGPattern,
    SpeculativeRAGRuntimeAdapter,
)
from app.rag.agentic.patterns.web_augmented import WebAugmentedRAGPattern


def __getattr__(name: str) -> object:
    if name == "RAG_RUNTIME_ADAPTERS":
        from app.rag.catalogue import RAG_RUNTIME_CAPABILITIES

        return RAG_RUNTIME_CAPABILITIES
    raise AttributeError(name)


__all__ = [
    "AdaptiveRAGPattern",
    "AgenticChunkingPattern",
    "AgenticRAGRuntimeAdapter",
    "ColBERTPattern",
    "ColBERTRAGRuntimeAdapter",
    "CorrectiveRAGPattern",
    "FLAREPattern",
    "FLARERAGRuntimeAdapter",
    "FusionRAGPattern",
    "GraphRAGPattern",
    "ModularRAGRuntimeAdapter",
    "RAFTRAGRuntimeAdapter",
    "RAGPattern",
    "RAGPatternState",
    "RAPTORPattern",
    "SelfRAGPattern",
    "SelfRAGRuntimeAdapter",
    "SpeculativeRAGPattern",
    "SpeculativeRAGRuntimeAdapter",
    "WebAugmentedRAGPattern",
]
