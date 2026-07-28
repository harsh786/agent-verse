"""Retrieval-augmented generation primitives."""

from app.rag.contracts import (
    RAG_RUNTIME_CAPABILITIES,
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
    resolve_rag_strategy,
)

__all__ = [
    "RAG_RUNTIME_CAPABILITIES",
    "RAG_STRATEGY_ALIASES",
    "RAGCitation",
    "RAGExecutionRequest",
    "RAGExecutionResult",
    "RAGRetrievalLeg",
    "RAGRuntimeAdapter",
    "RAGStrategy",
    "RAGStrategyError",
    "RAGStrategyTrace",
    "UnavailableRAGStrategyError",
    "UnknownRAGStrategyError",
    "resolve_rag_strategy",
]
