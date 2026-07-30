"""Retrieval-augmented generation primitives."""

from app.rag.catalogue import (
    RAG_CAPABILITY_CATALOGUE,
    RAG_RUNTIME_CAPABILITIES,
    RAGCapabilityCatalogueEntry,
    RAGRuntimeDependency,
    RAGRuntimeReadiness,
)
from app.rag.contracts import (
    RAG_STRATEGY_ALIASES,
    RAGCitation,
    RAGExecutionRequest,
    RAGExecutionResult,
    RAGRetrievalLeg,
    RAGRuntimeAdapter,
    RAGStrategy,
    RAGStrategyError,
    RAGStrategyTrace,
    UnavailableRAGStrategyError,
    UnknownRAGStrategyError,
    is_rag_runtime_adapter,
    resolve_rag_strategy,
)

__all__ = [
    "RAG_CAPABILITY_CATALOGUE",
    "RAG_RUNTIME_CAPABILITIES",
    "RAG_STRATEGY_ALIASES",
    "RAGCapabilityCatalogueEntry",
    "RAGCitation",
    "RAGExecutionRequest",
    "RAGExecutionResult",
    "RAGRetrievalLeg",
    "RAGRuntimeAdapter",
    "RAGRuntimeDependency",
    "RAGRuntimeReadiness",
    "RAGStrategy",
    "RAGStrategyError",
    "RAGStrategyTrace",
    "UnavailableRAGStrategyError",
    "UnknownRAGStrategyError",
    "is_rag_runtime_adapter",
    "resolve_rag_strategy",
]
