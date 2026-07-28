"""Canonical public contracts for RAG strategy execution."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Any, ClassVar, Protocol, TypeGuard, runtime_checkable

from pydantic import BaseModel, Field


class RAGStrategy(StrEnum):
    """The complete public RAG strategy vocabulary."""

    NAIVE = "naive"
    HYBRID = "hybrid"
    HYDE = "hyde"
    MULTI_HOP = "multi_hop"
    GRAPH = "graph"
    CORRECTIVE = "corrective"
    ADAPTIVE = "adaptive"
    MODULAR = "modular"
    SPECULATIVE = "speculative"
    AGENTIC = "agentic"
    WEB_AUGMENTED = "web_augmented"
    FUSION = "fusion"
    SELF_RAG = "self_rag"
    FLARE = "flare"
    RAPTOR = "raptor"
    AGENTIC_CHUNKING = "agentic_chunking"
    COLBERT = "colbert"
    RAFT = "raft"


RAG_STRATEGY_ALIASES: Mapping[str, RAGStrategy] = MappingProxyType(
    {
        "fusion_rag": RAGStrategy.FUSION,
        "corrective_rag": RAGStrategy.CORRECTIVE,
        "speculative_rag": RAGStrategy.SPECULATIVE,
        "colbert_late_interaction": RAGStrategy.COLBERT,
        "multi_hop_rag": RAGStrategy.MULTI_HOP,
        "graph_rag": RAGStrategy.GRAPH,
    }
)


class RAGStrategyError(ValueError):
    """Base class for public RAG strategy contract errors."""


class UnknownRAGStrategyError(RAGStrategyError):
    """Raised when a requested strategy ID is not part of the public contract."""

    def __init__(self, strategy_id: str) -> None:
        super().__init__(f"Unknown RAG strategy: {strategy_id}")
        self.strategy_id = strategy_id


class UnavailableRAGStrategyError(RAGStrategyError):
    """Raised when a known strategy cannot run with the configured capabilities."""

    def __init__(self, strategy: RAGStrategy, reason: str = "") -> None:
        message = f"RAG strategy is unavailable: {strategy.value}"
        if reason:
            message = f"{message} ({reason})"
        super().__init__(message)
        self.strategy = strategy
        self.reason = reason


def resolve_rag_strategy(strategy_id: str | RAGStrategy) -> RAGStrategy:
    """Resolve a canonical or historical public strategy ID."""

    if isinstance(strategy_id, RAGStrategy):
        return strategy_id

    alias = RAG_STRATEGY_ALIASES.get(strategy_id)
    if alias is not None:
        return alias

    try:
        return RAGStrategy(strategy_id)
    except ValueError as exc:
        raise UnknownRAGStrategyError(strategy_id) from exc


class RAGCitation(BaseModel):
    """Evidence cited by a grounded RAG answer."""

    citation_id: str
    chunk_id: str
    content: str
    score: float
    source: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RAGRetrievalLeg(BaseModel):
    """Observable evidence for one retrieval operation."""

    strategy: RAGStrategy
    query: str
    result_count: int = Field(ge=0)
    score: float = 0.0
    latency_ms: float = Field(default=0.0, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RAGStrategyTrace(BaseModel):
    """One strategy-specific execution decision."""

    strategy: RAGStrategy
    action: str
    status: str
    detail: dict[str, Any] = Field(default_factory=dict)


class RAGExecutionRequest(BaseModel):
    """Tenant-scoped input accepted by every production RAG runtime adapter."""

    tenant_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    requested_strategy_id: str = Field(min_length=1)
    collection_id: str | None = None
    top_k: int = Field(default=5, ge=1)
    filters: dict[str, Any] = Field(default_factory=dict)


class RAGExecutionResult(BaseModel):
    """Normalized result returned by every RAG execution path."""

    requested_strategy_id: str
    resolved_strategy_id: RAGStrategy
    citations: list[RAGCitation] = Field(default_factory=list)
    retrieval_legs: list[RAGRetrievalLeg] = Field(default_factory=list)
    strategy_trace: list[RAGStrategyTrace] = Field(default_factory=list)
    answer: str = ""
    grounded: bool = False


@runtime_checkable
class RAGRuntimeAdapter(Protocol):
    """Common contract required before a strategy can be registered as available."""

    strategy: ClassVar[RAGStrategy]

    async def execute(self, request: RAGExecutionRequest) -> RAGExecutionResult: ...


def is_rag_runtime_adapter(
    strategy: RAGStrategy,
    adapter: object,
) -> TypeGuard[type[RAGRuntimeAdapter]]:
    """Return whether a registration is a concrete adapter for the keyed strategy."""

    return (
        isinstance(adapter, type)
        and not inspect.isabstract(adapter)
        and getattr(adapter, "strategy", None) is strategy
        and inspect.iscoroutinefunction(getattr(adapter, "execute", None))
    )


# Task 1 defines the vocabulary only. Strategies are registered here only after a
# tenant-scoped gateway adapter implements the shared runtime contract.
RAG_RUNTIME_CAPABILITIES: Mapping[RAGStrategy, type[RAGRuntimeAdapter]] = MappingProxyType(
    {}
)
