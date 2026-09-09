"""Canonical public contracts for RAG strategy execution."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Any, ClassVar, Protocol, TypeGuard, runtime_checkable

from pydantic import BaseModel, Field, PrivateAttr


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
    MEMORY_AUGMENTED = "memory_augmented"
    CODE = "code"


DIRECT_CORE_RAG_STRATEGIES: frozenset[RAGStrategy] = frozenset(
    {
        RAGStrategy.NAIVE,
        RAGStrategy.HYBRID,
        RAGStrategy.HYDE,
        RAGStrategy.MULTI_HOP,
        RAGStrategy.GRAPH,
        RAGStrategy.CORRECTIVE,
        RAGStrategy.WEB_AUGMENTED,
        RAGStrategy.FUSION,
        RAGStrategy.RAPTOR,
        RAGStrategy.AGENTIC_CHUNKING,
        RAGStrategy.MEMORY_AUGMENTED,
        RAGStrategy.CODE,
    }
)


RAG_STRATEGY_ALIASES: Mapping[str, RAGStrategy] = MappingProxyType(
    {
        "fusion_rag": RAGStrategy.FUSION,
        "corrective_rag": RAGStrategy.CORRECTIVE,
        "speculative_rag": RAGStrategy.SPECULATIVE,
        "colbert_late_interaction": RAGStrategy.COLBERT,
        "multi_hop_rag": RAGStrategy.MULTI_HOP,
        "graph_rag": RAGStrategy.GRAPH,
        # D-9: promote the historical string-only entry points to the
        # canonical, certified enum members.
        "memory": RAGStrategy.MEMORY_AUGMENTED,
        "code_rag": RAGStrategy.CODE,
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
    execution_id: str = ""
    top_k: int = Field(default=5, ge=1, le=20)
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
    # WS-10: calibrated aggregate confidence in [0,1] over the retrieved set, and
    # a flag raised when it falls below the configured low-confidence threshold.
    retrieval_confidence: float = 0.0
    low_confidence: bool = False
    _budget_context: object | None = PrivateAttr(default=None)


@runtime_checkable
class RAGRuntimeAdapter(Protocol):
    """Common contract required before a strategy can be registered as available."""

    strategy: ClassVar[RAGStrategy]

    async def execute(
        self,
        request: RAGExecutionRequest,
        context: Any = None,
    ) -> RAGExecutionResult: ...

    @classmethod
    def probe_trace(cls) -> RAGStrategyTrace: ...


class _CoreRAGRuntimeAdapter(RAGRuntimeAdapter):
    """Concrete canonical adapter delegated to the tenant-scoped gateway runtime."""

    strategy: ClassVar[RAGStrategy]
    probe_action: ClassVar[str]
    probe_evidence: ClassVar[str]

    @classmethod
    def probe_trace(cls) -> RAGStrategyTrace:
        """Exercise the adapter-owned, dependency-free strategy identity probe."""
        return RAGStrategyTrace(
            strategy=cls.strategy,
            action=cls.probe_action,
            status="complete",
            detail={
                "adapter_strategy": cls.strategy.value,
                "evidence": cls.probe_evidence,
            },
        )

    async def execute(
        self,
        request: RAGExecutionRequest,
        context: Any = None,
    ) -> RAGExecutionResult:
        from app.rag.gateway import execute_core_strategy

        if context is None:
            raise UnavailableRAGStrategyError(
                self.strategy, "tenant-scoped gateway context is required"
            )
        return await execute_core_strategy(self.strategy, request, context)


class NaiveRAGRuntimeAdapter(_CoreRAGRuntimeAdapter):
    strategy = RAGStrategy.NAIVE
    probe_action = "probe_vector_retrieval"
    probe_evidence = "persisted vector retrieval"


class HybridRAGRuntimeAdapter(_CoreRAGRuntimeAdapter):
    strategy = RAGStrategy.HYBRID
    probe_action = "probe_four_leg_rrf"
    probe_evidence = "four-leg reciprocal rank fusion"


class HyDERAGRuntimeAdapter(_CoreRAGRuntimeAdapter):
    strategy = RAGStrategy.HYDE
    probe_action = "probe_hypothetical_document"
    probe_evidence = "hypothetical document embedding"


class MultiHopRAGRuntimeAdapter(_CoreRAGRuntimeAdapter):
    strategy = RAGStrategy.MULTI_HOP
    probe_action = "probe_hop_decomposition"
    probe_evidence = "decomposed hop retrieval"


class FusionRAGRuntimeAdapter(_CoreRAGRuntimeAdapter):
    strategy = RAGStrategy.FUSION
    probe_action = "probe_expanded_query_rrf"
    probe_evidence = "expanded-query reciprocal rank fusion"


class GraphRAGRuntimeAdapter(_CoreRAGRuntimeAdapter):
    strategy = RAGStrategy.GRAPH
    probe_action = "probe_graph_evidence"
    probe_evidence = "tenant-scoped graph evidence"


class CorrectiveRAGRuntimeAdapter(_CoreRAGRuntimeAdapter):
    strategy = RAGStrategy.CORRECTIVE
    probe_action = "probe_corrective_retry"
    probe_evidence = "graded corrective retry"


class AdaptiveRAGRuntimeAdapter(_CoreRAGRuntimeAdapter):
    strategy = RAGStrategy.ADAPTIVE
    probe_action = "probe_adaptive_routing"
    probe_evidence = "capability-aware adaptive routing"


class WebAugmentedRAGRuntimeAdapter(_CoreRAGRuntimeAdapter):
    strategy = RAGStrategy.WEB_AUGMENTED
    probe_action = "probe_web_augmentation"
    probe_evidence = "policy-authorized web augmentation"


class RAPTORRAGRuntimeAdapter(_CoreRAGRuntimeAdapter):
    strategy = RAGStrategy.RAPTOR
    probe_action = "probe_hierarchical_summary"
    probe_evidence = "hierarchical summary retrieval"


class AgenticChunkingRAGRuntimeAdapter(_CoreRAGRuntimeAdapter):
    strategy = RAGStrategy.AGENTIC_CHUNKING
    probe_action = "probe_semantic_boundaries"
    probe_evidence = "semantic boundary chunking"


class MemoryAugmentedRAGRuntimeAdapter(_CoreRAGRuntimeAdapter):
    strategy = RAGStrategy.MEMORY_AUGMENTED
    probe_action = "probe_long_term_memory_fusion"
    probe_evidence = "long-term memory and persisted evidence fusion"


class CodeRAGRuntimeAdapter(_CoreRAGRuntimeAdapter):
    strategy = RAGStrategy.CODE
    probe_action = "probe_symbol_boosted_retrieval"
    probe_evidence = "identifier-boosted code retrieval"


class ColBERTRAGRuntimeAdapter(_CoreRAGRuntimeAdapter):
    strategy = RAGStrategy.COLBERT
    probe_action = "probe_late_interaction"
    probe_evidence = "late-interaction token scoring"

    async def execute(
        self,
        request: RAGExecutionRequest,
        context: Any = None,
    ) -> RAGExecutionResult:
        from app.rag.agentic.patterns.colbert import (
            ColBERTRAGRuntimeAdapter as ColBERT,
        )

        return await ColBERT().execute(request, context)


class _ReasoningRAGRuntimeAdapter(RAGRuntimeAdapter):
    """Lazy contract adapter for a reasoning strategy implementation."""

    strategy: ClassVar[RAGStrategy]
    probe_action: ClassVar[str]
    probe_evidence: ClassVar[str]

    @classmethod
    def probe_trace(cls) -> RAGStrategyTrace:
        return RAGStrategyTrace(
            strategy=cls.strategy,
            action=cls.probe_action,
            status="complete",
            detail={
                "adapter_strategy": cls.strategy.value,
                "evidence": cls.probe_evidence,
            },
        )

    async def execute(
        self,
        request: RAGExecutionRequest,
        context: Any = None,
    ) -> RAGExecutionResult:
        from app.rag.agentic.patterns.agentic import AgenticRAGRuntimeAdapter as Agentic
        from app.rag.agentic.patterns.flare import FLARERAGRuntimeAdapter as Flare
        from app.rag.agentic.patterns.self_rag import SelfRAGRuntimeAdapter as SelfRAG
        from app.rag.agentic.patterns.speculative import (
            SpeculativeRAGRuntimeAdapter as Speculative,
        )

        adapters: dict[RAGStrategy, Callable[[], RAGRuntimeAdapter]] = {
            RAGStrategy.SPECULATIVE: Speculative,
            RAGStrategy.AGENTIC: Agentic,
            RAGStrategy.SELF_RAG: SelfRAG,
            RAGStrategy.FLARE: Flare,
        }
        return await adapters[self.strategy]().execute(request, context)


class SpeculativeRAGRuntimeAdapter(_ReasoningRAGRuntimeAdapter):
    strategy = RAGStrategy.SPECULATIVE
    probe_action = "probe_draft_verification"
    probe_evidence = "draft and evidence verification"


class AgenticRAGRuntimeAdapter(_ReasoningRAGRuntimeAdapter):
    strategy = RAGStrategy.AGENTIC
    probe_action = "probe_agentic_loop"
    probe_evidence = "bounded agentic retrieval loop"


class SelfRAGRuntimeAdapter(_ReasoningRAGRuntimeAdapter):
    strategy = RAGStrategy.SELF_RAG
    probe_action = "probe_self_critique"
    probe_evidence = "retrieval relevance self-critique"


class FLARERAGRuntimeAdapter(_ReasoningRAGRuntimeAdapter):
    strategy = RAGStrategy.FLARE
    probe_action = "probe_uncertainty_retrieval"
    probe_evidence = "uncertainty-triggered retrieval"


class ModularRAGRuntimeAdapter(RAGRuntimeAdapter):
    """Lazy canonical contract adapter for validated Modular RAG graphs."""

    strategy: ClassVar[RAGStrategy] = RAGStrategy.MODULAR

    @classmethod
    def probe_trace(cls) -> RAGStrategyTrace:
        return RAGStrategyTrace(
            strategy=cls.strategy,
            action="probe_modular_pipeline",
            status="complete",
            detail={
                "adapter_strategy": cls.strategy.value,
                "evidence": "validated modular pipeline",
            },
        )

    async def execute(
        self,
        request: RAGExecutionRequest,
        context: Any = None,
    ) -> RAGExecutionResult:
        from app.rag.agentic.patterns.modular import (
            ModularRAGRuntimeAdapter as Modular,
        )

        return await Modular().execute(request, context)


class RAFTRAGRuntimeAdapter(RAGRuntimeAdapter):
    """Lazy canonical adapter for completed RAFT models."""

    strategy: ClassVar[RAGStrategy] = RAGStrategy.RAFT

    @classmethod
    def probe_trace(cls) -> RAGStrategyTrace:
        return RAGStrategyTrace(
            strategy=cls.strategy,
            action="probe_completed_model",
            status="complete",
            detail={
                "adapter_strategy": cls.strategy.value,
                "evidence": "compatible completed RAFT model inference",
            },
        )

    async def execute(
        self,
        request: RAGExecutionRequest,
        context: Any = None,
    ) -> RAGExecutionResult:
        from app.rag.agentic.patterns.raft import RAFTRAGRuntimeAdapter as RAFTAdapter

        return await RAFTAdapter().execute(request, context)


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
        and callable(getattr(adapter, "probe_trace", None))
    )
