"""Contract tests for the single public RAG strategy vocabulary."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pytest

from app.orchestration.strategy_registry import (
    StrategyCategory,
    StrategyState,
    build_default_registry,
)
from app.rag.contracts import (
    RAG_RUNTIME_CAPABILITIES,
    RAG_STRATEGY_ALIASES,
    RAGCitation,
    RAGExecutionRequest,
    RAGExecutionResult,
    RAGRetrievalLeg,
    RAGRuntimeAdapter,
    RAGStrategy,
    RAGStrategyTrace,
    UnknownRAGStrategyError,
    resolve_rag_strategy,
)

CANONICAL_STRATEGY_IDS = {
    "naive",
    "hybrid",
    "hyde",
    "multi_hop",
    "graph",
    "corrective",
    "adaptive",
    "modular",
    "speculative",
    "agentic",
    "web_augmented",
    "fusion",
    "self_rag",
    "flare",
    "raptor",
    "agentic_chunking",
    "colbert",
    "raft",
}


def test_strategy_enum_and_registry_share_exactly_the_canonical_ids() -> None:
    registry = build_default_registry()
    registry_ids = {
        capability.strategy_id
        for capability in registry.list_by_category(StrategyCategory.RAG)
    }

    assert {strategy.value for strategy in RAGStrategy} == CANONICAL_STRATEGY_IDS
    assert registry_ids == CANONICAL_STRATEGY_IDS


@pytest.mark.parametrize(
    ("historical_id", "canonical"),
    [
        ("fusion_rag", RAGStrategy.FUSION),
        ("corrective_rag", RAGStrategy.CORRECTIVE),
        ("speculative_rag", RAGStrategy.SPECULATIVE),
        ("colbert_late_interaction", RAGStrategy.COLBERT),
        ("multi_hop_rag", RAGStrategy.MULTI_HOP),
        ("graph_rag", RAGStrategy.GRAPH),
    ],
)
def test_historical_strategy_ids_resolve_to_canonical_ids(
    historical_id: str,
    canonical: RAGStrategy,
) -> None:
    assert resolve_rag_strategy(historical_id) is canonical
    assert resolve_rag_strategy(canonical) is canonical


def test_unknown_strategy_is_an_explicit_contract_error() -> None:
    with pytest.raises(UnknownRAGStrategyError, match="unknown-rag"):
        resolve_rag_strategy("unknown-rag")


@pytest.mark.parametrize(
    "unsupported_id",
    [
        "auto",
        "direct",
        "multimodal",
        "naive_rag",
        "hybrid_rag",
        "adaptive_rag",
        "modular_rag",
        "agentic_rag",
        "web_augmented_rag",
    ],
)
def test_unapproved_compatibility_ids_are_rejected(unsupported_id: str) -> None:
    with pytest.raises(UnknownRAGStrategyError):
        resolve_rag_strategy(unsupported_id)


def test_one_alias_map_is_the_authoritative_compatibility_contract() -> None:
    assert RAG_STRATEGY_ALIASES == {
        "fusion_rag": RAGStrategy.FUSION,
        "corrective_rag": RAGStrategy.CORRECTIVE,
        "speculative_rag": RAGStrategy.SPECULATIVE,
        "colbert_late_interaction": RAGStrategy.COLBERT,
        "multi_hop_rag": RAGStrategy.MULTI_HOP,
        "graph_rag": RAGStrategy.GRAPH,
    }


def test_execution_result_serializes_strategy_evidence() -> None:
    result = RAGExecutionResult(
        requested_strategy_id="fusion_rag",
        resolved_strategy_id=RAGStrategy.FUSION,
        citations=[
            RAGCitation(
                citation_id="citation-1",
                chunk_id="chunk-1",
                content="Persisted tenant-scoped evidence.",
                score=0.94,
                source="handbook.pdf",
            )
        ],
        retrieval_legs=[
            RAGRetrievalLeg(
                strategy=RAGStrategy.HYBRID,
                query="tenant isolation",
                result_count=1,
                score=0.94,
                latency_ms=12.5,
            )
        ],
        strategy_trace=[
            RAGStrategyTrace(
                strategy=RAGStrategy.FUSION,
                action="rrf_merge",
                status="complete",
                detail={"variant_count": 3},
            )
        ],
        answer="The evidence is tenant scoped [citation-1].",
        grounded=True,
    )

    assert result.model_dump(mode="json") == {
        "requested_strategy_id": "fusion_rag",
        "resolved_strategy_id": "fusion",
        "citations": [
            {
                "citation_id": "citation-1",
                "chunk_id": "chunk-1",
                "content": "Persisted tenant-scoped evidence.",
                "score": 0.94,
                "source": "handbook.pdf",
                "metadata": {},
            }
        ],
        "retrieval_legs": [
            {
                "strategy": "hybrid",
                "query": "tenant isolation",
                "result_count": 1,
                "score": 0.94,
                "latency_ms": 12.5,
                "metadata": {},
            }
        ],
        "strategy_trace": [
            {
                "strategy": "fusion",
                "action": "rrf_merge",
                "status": "complete",
                "detail": {"variant_count": 3},
            }
        ],
        "answer": "The evidence is tenant scoped [citation-1].",
        "grounded": True,
    }


def test_runtime_adapter_contract_requires_tenant_scoped_requests() -> None:
    request = RAGExecutionRequest(
        tenant_id="tenant-1",
        query="What is the retention policy?",
        requested_strategy_id="hybrid_rag",
        collection_id="collection-1",
        top_k=7,
    )

    assert request.model_dump(mode="json") == {
        "tenant_id": "tenant-1",
        "query": "What is the retention policy?",
        "requested_strategy_id": "hybrid_rag",
        "collection_id": "collection-1",
        "top_k": 7,
        "filters": {},
    }
    assert hasattr(RAGRuntimeAdapter, "execute")


@pytest.mark.parametrize("top_k", [0, 21])
def test_execution_request_rejects_out_of_bounds_top_k(top_k: int) -> None:
    with pytest.raises(ValueError):
        RAGExecutionRequest(
            tenant_id="tenant-1",
            query="query",
            requested_strategy_id="naive",
            collection_id="collection-1",
            top_k=top_k,
        )


def test_registry_only_marks_registered_runtime_capabilities_implemented() -> None:
    registry = build_default_registry()
    rag_capabilities = registry.list_by_category(StrategyCategory.RAG)
    implemented_ids = {
        resolve_rag_strategy(capability.strategy_id)
        for capability in rag_capabilities
        if capability.state is StrategyState.IMPLEMENTED
    }

    expected = {
        RAGStrategy.NAIVE,
        RAGStrategy.HYBRID,
        RAGStrategy.HYDE,
        RAGStrategy.MULTI_HOP,
        RAGStrategy.FUSION,
    }
    assert set(RAG_RUNTIME_CAPABILITIES) == expected
    assert implemented_ids == expected
    assert set(RAG_RUNTIME_CAPABILITIES) == implemented_ids


def test_registry_derives_implemented_state_from_runtime_capabilities() -> None:
    class HybridRuntimeAdapter:
        strategy = RAGStrategy.HYBRID

        async def execute(self, request: RAGExecutionRequest) -> RAGExecutionResult:
            return RAGExecutionResult(
                requested_strategy_id=request.requested_strategy_id,
                resolved_strategy_id=self.strategy,
            )

    registry = build_default_registry(
        rag_runtime_capabilities={RAGStrategy.HYBRID: HybridRuntimeAdapter}
    )
    rag_capabilities = registry.list_by_category(StrategyCategory.RAG)
    implemented_ids = {
        capability.strategy_id
        for capability in rag_capabilities
        if capability.state is StrategyState.IMPLEMENTED
    }

    assert implemented_ids == {RAGStrategy.HYBRID.value}
    assert registry.is_available(RAGStrategy.HYBRID.value)


class MissingExecuteAdapter:
    strategy = RAGStrategy.HYBRID


class SyncExecuteAdapter:
    strategy = RAGStrategy.HYBRID

    def execute(self, request: RAGExecutionRequest) -> RAGExecutionResult:
        return RAGExecutionResult(
            requested_strategy_id=request.requested_strategy_id,
            resolved_strategy_id=self.strategy,
        )


@pytest.mark.parametrize("adapter", [MissingExecuteAdapter, SyncExecuteAdapter])
def test_registry_rejects_runtime_capabilities_without_async_execute(adapter: object) -> None:
    registry = build_default_registry(
        rag_runtime_capabilities={RAGStrategy.HYBRID: adapter}  # type: ignore[dict-item]
    )

    capability = registry.get(RAGStrategy.HYBRID.value)
    assert capability is not None
    assert capability.state is StrategyState.PARTIAL
    assert not registry.is_available(RAGStrategy.HYBRID.value)


def test_registry_rejects_runtime_capability_with_mismatched_strategy() -> None:
    class MismatchedRuntimeAdapter:
        strategy = RAGStrategy.NAIVE

        async def execute(self, request: RAGExecutionRequest) -> RAGExecutionResult:
            return RAGExecutionResult(
                requested_strategy_id=request.requested_strategy_id,
                resolved_strategy_id=self.strategy,
            )

    registry = build_default_registry(
        rag_runtime_capabilities={RAGStrategy.HYBRID: MismatchedRuntimeAdapter}
    )

    capability = registry.get(RAGStrategy.HYBRID.value)
    assert capability is not None
    assert capability.state is StrategyState.PARTIAL
    assert not registry.is_available(RAGStrategy.HYBRID.value)


def test_registry_rejects_abstract_runtime_capability() -> None:
    class AbstractRuntimeAdapter(ABC):
        strategy = RAGStrategy.HYBRID

        @abstractmethod
        async def execute(self, request: RAGExecutionRequest) -> RAGExecutionResult:
            raise NotImplementedError

    registry = build_default_registry(
        rag_runtime_capabilities={RAGStrategy.HYBRID: AbstractRuntimeAdapter}
    )

    capability = registry.get(RAGStrategy.HYBRID.value)
    assert capability is not None
    assert capability.state is StrategyState.PARTIAL
