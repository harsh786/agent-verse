"""Regression tests for remaining Task 4 gateway boundaries."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any

import pytest

from app.rag.contracts import (
    RAGCitation,
    RAGExecutionRequest,
    RAGExecutionResult,
    RAGRetrievalLeg,
    RAGStrategy,
    RAGStrategyTrace,
    resolve_rag_strategy,
)
from app.rag.gateway import (
    ResolvedLLM,
    RetrievalDependencies,
    RetrievalExecutionContext,
    RetrievalGateway,
    RetrievalStrategyCapability,
)
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext("tenant-gap", PlanTier.PROFESSIONAL, "key-gap")


def test_production_capability_registry_contains_certified_adapters() -> None:
    from app.main import create_app

    gateway = create_app(manage_pools=False).state.retrieval_gateway

    assert set(gateway.dependencies.strategy_capabilities) == {
        RAGStrategy.NAIVE,
        RAGStrategy.HYBRID,
        RAGStrategy.HYDE,
        RAGStrategy.MULTI_HOP,
        RAGStrategy.FUSION,
        RAGStrategy.GRAPH,
        RAGStrategy.CORRECTIVE,
        RAGStrategy.ADAPTIVE,
        RAGStrategy.WEB_AUGMENTED,
    }
    assert all(
        capability.requires_database
        for capability in gateway.dependencies.strategy_capabilities.values()
    )


class RecordingGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[TenantContext, dict[str, Any]]] = []

    async def execute(self, tenant_context: TenantContext, **kwargs: Any) -> RAGExecutionResult:
        self.calls.append((tenant_context, kwargs))
        collection_id = str(kwargs["collection_id"])
        requested_strategy_id = str(kwargs["strategy_id"])
        strategy = resolve_rag_strategy(requested_strategy_id)
        return RAGExecutionResult(
            requested_strategy_id=requested_strategy_id,
            resolved_strategy_id=strategy,
            citations=[
                RAGCitation(
                    citation_id=f"citation-{collection_id}",
                    chunk_id=f"chunk-{collection_id}",
                    content=f"Evidence {collection_id}",
                    score=0.8,
                    source=f"source-{collection_id}",
                )
            ],
            retrieval_legs=[
                RAGRetrievalLeg(
                    strategy=strategy,
                    query=str(kwargs["query"]),
                    result_count=1,
                )
            ],
            strategy_trace=[
                RAGStrategyTrace(
                    strategy=strategy,
                    action="gateway",
                    status="complete",
                    detail={"collection_id": collection_id},
                )
            ],
            grounded=True,
        )


@pytest.mark.asyncio
async def test_smart_context_fetch_is_gateway_only() -> None:
    from app.pipeline.steps import smart_context_fetch

    gateway = RecordingGateway()
    result = await smart_context_fetch(
        goal="goal",
        step="step query",
        tenant_ctx=TENANT,
        retrieval_gateway=gateway,
        collection_ids=["collection-1"],
        strategy=RAGStrategy.FUSION,
        top_k=4,
        filters={"team": "legal"},
    )

    assert "Evidence collection-1" in result
    assert gateway.calls == [
        (
            TENANT,
            {
                "collection_id": "collection-1",
                "query": "step query",
                "strategy_id": RAGStrategy.FUSION,
                "top_k": 4,
                "filters": {"team": "legal"},
            },
        )
    ]


@pytest.mark.asyncio
async def test_retriever_tool_is_thin_gateway_adapter_without_fallback() -> None:
    from app.rag.agentic.retriever_tool import RetrieverTool

    gateway = RecordingGateway()
    tool = RetrieverTool(retrieval_gateway=gateway)

    result = await tool.retrieve(
        "policy",
        tenant_ctx=TENANT,
        strategy=RAGStrategy.GRAPH,
        collection_ids=["collection-1"],
        top_k=6,
        metadata_filter={"team": "legal"},
    )

    assert result.strategy_used == "graph"
    assert result.source == "knowledge_base"
    assert result.context_text == "Evidence collection-1"
    assert not result.fallback_used
    assert gateway.calls[0][1]["filters"] == {"team": "legal"}


@pytest.mark.asyncio
async def test_workflow_rag_node_requires_gateway_and_tenant_context() -> None:
    from app.agent.workflow_nodes import execute_rag_node

    gateway = RecordingGateway()
    result = await execute_rag_node(
        {
            "collection_id": "collection-1",
            "query_template": "{{goal}}",
            "strategy": "fusion",
            "top_k": 7,
            "filters": {"team": "legal"},
        },
        {"goal": "policy"},
        retrieval_gateway=gateway,
        tenant_ctx=TENANT,
    )

    assert result["requested_strategy_id"] == "fusion"
    assert result["resolved_strategy_id"] == "fusion"
    assert result["citations"][0]["citation_id"] == "citation-collection-1"
    assert gateway.calls[0][0] is TENANT


@pytest.mark.asyncio
async def test_workflow_boundary_resolves_alias_and_preserves_requested_id() -> None:
    from app.agent.workflow_nodes import execute_rag_node

    gateway = RecordingGateway()
    result = await execute_rag_node(
        {
            "collection_id": "collection-1",
            "query_template": "policy",
            "strategy": "fusion_rag",
        },
        {},
        retrieval_gateway=gateway,
        tenant_ctx=TENANT,
    )

    assert gateway.calls[0][1]["strategy_id"] == "fusion_rag"
    assert result["requested_strategy_id"] == "fusion_rag"
    assert result["resolved_strategy_id"] == "fusion"


@pytest.mark.asyncio
async def test_workflow_boundary_rejects_unknown_strategy() -> None:
    from app.agent.workflow_nodes import execute_rag_node

    with pytest.raises(ValueError, match="Unknown RAG strategy"):
        await execute_rag_node(
            {"collection_id": "collection-1", "strategy": "invented"},
            {},
            retrieval_gateway=RecordingGateway(),
            tenant_ctx=TENANT,
        )


@pytest.mark.parametrize(
    ("requested", "resolved"),
    [
        ("fusion_rag", "fusion"),
        ("corrective_rag", "corrective"),
        ("speculative_rag", "speculative"),
        ("colbert_late_interaction", "colbert"),
        ("multi_hop_rag", "multi_hop"),
        ("graph_rag", "graph"),
    ],
)
def test_workflow_config_deserialization_canonicalizes_approved_aliases(
    requested: str,
    resolved: str,
) -> None:
    from app.agent.workflow_planner import WorkflowPlan

    plan = WorkflowPlan.from_dict(
        {
            "steps": [
                {
                    "id": "rag-1",
                    "tool": "rag",
                    "strategy": requested,
                }
            ]
        },
        goal="policy",
    )

    assert plan.steps[0].config["strategy"] == resolved
    assert plan.steps[0].config["requested_strategy_id"] == requested


def test_workflow_config_deserialization_rejects_unknown_strategy() -> None:
    from app.agent.workflow_planner import WorkflowPlan

    with pytest.raises(ValueError, match="Unknown RAG strategy"):
        WorkflowPlan.from_dict(
            {"steps": [{"id": "rag-2", "tool": "rag", "strategy": "invented"}]},
            goal="policy",
        )


def test_production_retrieval_paths_have_no_store_or_engine_search() -> None:
    from app.agent import graph, workflow_nodes
    from app.pipeline import steps
    from app.rag.agentic import retriever_tool

    forbidden = ("hybrid_search_db", "knowledge_store.search", "app.rag.engine")
    for module in (steps, retriever_tool, workflow_nodes):
        source = inspect.getsource(module)
        assert not any(token in source for token in forbidden), module.__name__
    graph_source = inspect.getsource(graph)
    assert "RetrieverTool(knowledge_store" not in graph_source
    assert "web_search_auto" not in graph_source
    assert "retrieval_gateway=self._retrieval_gateway" in graph_source


class AllowedAuthorizer:
    async def authorize(
        self,
        session: Any,
        tenant_context: TenantContext,
        collection_id: str,
    ) -> bool:
        del session
        return tenant_context is TENANT and collection_id == "collection-1"


class ReadyAdapter:
    async def execute(
        self,
        request: RAGExecutionRequest,
        context: RetrievalExecutionContext,
    ) -> RAGExecutionResult:
        return RAGExecutionResult(
            requested_strategy_id=request.requested_strategy_id,
            resolved_strategy_id=context.strategy,
        )


def _gateway_for_readiness(
    *,
    adapter: Any = None,
    requires_provider: bool = False,
    llm_resolver: Any = None,
) -> RetrievalGateway:
    return RetrievalGateway(
        RetrievalDependencies(
            session_factory=None,
            collection_authorizer=AllowedAuthorizer(),
            strategy_capabilities={
                RAGStrategy.HYBRID: RetrievalStrategyCapability(
                    adapter=adapter if adapter is not None else ReadyAdapter(),
                    requires_provider=requires_provider,
                )
            },
            llm_resolver=llm_resolver,
        )
    )


@pytest.mark.asyncio
async def test_gateway_readiness_validates_adapter_and_collection_authorization() -> None:
    gateway = _gateway_for_readiness()

    ready = await gateway.readiness(
        TENANT,
        strategy_id=RAGStrategy.HYBRID,
        collection_id="collection-1",
    )
    denied = await gateway.readiness(
        TENANT,
        strategy_id=RAGStrategy.HYBRID,
        collection_id="foreign-collection",
    )

    assert ready.available
    assert ready.reason == "ready"
    assert not denied.available
    assert denied.reason == "collection_not_authorized"


@pytest.mark.asyncio
async def test_gateway_readiness_validates_tenant_provider_and_model() -> None:
    tenants: list[TenantContext] = []

    class Provider:
        async def complete(self, request: object) -> object:
            return request

    async def resolve_llm(
        tenant_ctx: TenantContext,
        strategy: RAGStrategy,
    ) -> ResolvedLLM:
        tenants.append(tenant_ctx)
        assert strategy is RAGStrategy.HYBRID
        return ResolvedLLM(provider=Provider(), model="tenant-model")

    readiness = await _gateway_for_readiness(
        requires_provider=True,
        llm_resolver=resolve_llm,
    ).readiness(TENANT, strategy_id=RAGStrategy.HYBRID)

    assert readiness.available
    assert readiness.reason == "ready"
    assert tenants == [TENANT]


@pytest.mark.asyncio
async def test_gateway_readiness_safely_rejects_invalid_adapter_and_provider() -> None:
    invalid = _gateway_for_readiness(adapter=SimpleNamespace(execute=lambda: None))

    async def broken_resolver(*_: object) -> object:
        raise RuntimeError("secret provider config")

    provider_failure = _gateway_for_readiness(
        requires_provider=True,
        llm_resolver=broken_resolver,
    )

    invalid_result = await invalid.readiness(TENANT, strategy_id=RAGStrategy.HYBRID)
    provider_result = await provider_failure.readiness(
        TENANT,
        strategy_id=RAGStrategy.HYBRID,
    )

    assert not invalid_result.available
    assert invalid_result.reason == "invalid_adapter"
    assert not provider_result.available
    assert provider_result.reason == "llm_provider_unavailable"
    assert "secret" not in repr(provider_result)


@pytest.mark.asyncio
async def test_federated_deduplication_merges_all_provenance_deterministically() -> None:
    from app.knowledge.federated_search import federated_search

    class DuplicateGateway(RecordingGateway):
        async def execute(
            self, tenant_context: TenantContext, **kwargs: Any
        ) -> RAGExecutionResult:
            collection_id = str(kwargs["collection_id"])
            score = 0.7 if collection_id == "collection-1" else 0.9
            return RAGExecutionResult(
                requested_strategy_id="hybrid",
                resolved_strategy_id=RAGStrategy.HYBRID,
                citations=[
                    RAGCitation(
                        citation_id=f"citation-{collection_id}",
                        chunk_id=f"chunk-{collection_id}",
                        content="Shared evidence",
                        score=score,
                        source=f"source-{collection_id}",
                        metadata={"content_hash": "shared"},
                    )
                ],
                retrieval_legs=[
                    RAGRetrievalLeg(
                        strategy=RAGStrategy.HYBRID,
                        query="policy",
                        result_count=1,
                        metadata={"collection_id": collection_id},
                    )
                ],
                strategy_trace=[
                    RAGStrategyTrace(
                        strategy=RAGStrategy.HYBRID,
                        action="search",
                        status="complete",
                        detail={"collection_id": collection_id},
                    )
                ],
            )

    results = await federated_search(
        query="policy",
        collection_ids=["collection-2", "collection-1"],
        gateway=DuplicateGateway(),
        tenant_ctx=TENANT,
        strategy=RAGStrategy.HYBRID,
        top_k=5,
    )

    assert len(results) == 1
    merged = results[0]
    assert merged["score"] == 0.9
    assert merged["collection_ids"] == ["collection-1", "collection-2"]
    assert merged["sources"] == ["source-collection-1", "source-collection-2"]
    assert merged["citation_refs"] == [
        "collection-1:citation-collection-1",
        "collection-2:citation-collection-2",
    ]
    assert len(merged["retrieval_legs"]) == 2
    assert len(merged["strategy_trace"]) == 2
