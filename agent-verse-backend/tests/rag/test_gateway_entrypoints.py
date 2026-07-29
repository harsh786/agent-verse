"""Contract tests for product retrieval entry points."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.state import AgentState, GoalStatus
from app.api.knowledge import router as knowledge_router
from app.api.rag_platform import router as rag_router
from app.providers.base import CompletionResponse
from app.providers.fake import FakeProvider
from app.rag.contracts import (
    RAGCitation,
    RAGExecutionResult,
    RAGRetrievalLeg,
    RAGStrategy,
    RAGStrategyTrace,
)
from app.rag.gateway import ResolvedLLM
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

TENANT = TenantContext("tenant-entrypoint", PlanTier.PROFESSIONAL, "key-entrypoint")
API_KEY = "av_entrypoint_key"
HEADERS = {"X-API-Key": API_KEY}


def _result(
    *,
    requested: str = "fusion_rag",
    resolved: RAGStrategy = RAGStrategy.FUSION,
    collection_id: str = "collection-1",
) -> RAGExecutionResult:
    citation = RAGCitation(
        citation_id=f"citation-{collection_id}",
        chunk_id=f"chunk-{collection_id}",
        content=f"Evidence from {collection_id}",
        score=0.9,
        source=f"{collection_id}.pdf",
        metadata={"collection_id": collection_id, "page_number": 2},
    )
    return RAGExecutionResult(
        requested_strategy_id=requested,
        resolved_strategy_id=resolved,
        citations=[citation],
        retrieval_legs=[
            RAGRetrievalLeg(
                strategy=resolved,
                query="retention policy",
                result_count=1,
                score=0.9,
            )
        ],
        strategy_trace=[
            RAGStrategyTrace(
                strategy=resolved,
                action="gateway_retrieval",
                status="complete",
            )
        ],
        grounded=True,
    )


class RecordingProvider:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def complete(self, request: Any) -> CompletionResponse:
        self.requests.append(request)
        return CompletionResponse(content="Tenant answer [1]", model=request.model)


class RecordingGateway:
    def __init__(
        self,
        *,
        provider: RecordingProvider | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[TenantContext, dict[str, Any]]] = []
        self.error = error
        self.provider = provider

        async def resolve_llm(
            tenant_context: TenantContext,
            strategy: RAGStrategy,
        ) -> ResolvedLLM | None:
            assert tenant_context is TENANT
            if self.provider is None:
                return None
            return ResolvedLLM(provider=self.provider, model="tenant-model")

        self.dependencies = SimpleNamespace(
            llm_resolver=resolve_llm,
            strategy_capabilities={},
            embedder=object(),
            graph_capability=None,
            search_capability=None,
        )

    async def execute(self, tenant_context: TenantContext, **kwargs: Any) -> RAGExecutionResult:
        self.calls.append((tenant_context, kwargs))
        if self.error is not None:
            raise self.error
        requested = str(kwargs["strategy_id"])
        resolved = RAGStrategy.FUSION if requested == "fusion_rag" else RAGStrategy.HYBRID
        return _result(
            requested=requested,
            resolved=resolved,
            collection_id=kwargs["collection_id"],
        )


def _app(gateway: RecordingGateway, *, knowledge_store: object | None = None) -> FastAPI:
    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        return TENANT if key == API_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    app.include_router(rag_router)
    app.include_router(knowledge_router)
    app.state.retrieval_gateway = gateway
    app.state.knowledge_store = knowledge_store or SimpleNamespace()
    return app


def test_rag_query_uses_app_gateway_and_preserves_alias_trace() -> None:
    gateway = RecordingGateway(provider=RecordingProvider())
    client = TestClient(_app(gateway), raise_server_exceptions=False)

    response = client.post(
        "/rag/query",
        json={
            "query": "retention policy",
            "collection_id": "collection-1",
            "strategy": "fusion_rag",
            "top_k": 7,
            "filters": {"department": "legal"},
        },
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert gateway.calls == [
        (
            TENANT,
            {
                "collection_id": "collection-1",
                "query": "retention policy",
                "strategy_id": "fusion_rag",
                "top_k": 7,
                "filters": {"department": "legal"},
            },
        )
    ]
    body = response.json()
    assert body["requested_strategy_id"] == "fusion_rag"
    assert body["resolved_strategy_id"] == "fusion"
    assert body["retrieval_legs"][0]["strategy"] == "fusion"
    assert body["strategy_trace"][0]["status"] == "complete"


def test_rag_strategies_exposes_exact_canonical_contract_with_availability_metadata() -> None:
    client = TestClient(_app(RecordingGateway()), raise_server_exceptions=False)

    response = client.get("/rag/strategies", headers=HEADERS)

    assert response.status_code == 200
    strategies = response.json()["strategies"]
    assert len(strategies) == 18
    assert {item["id"] for item in strategies} == {strategy.value for strategy in RAGStrategy}
    assert all(
        {"state", "registry_available", "capability_available", "available"}
        <= item.keys()
        for item in strategies
    )
    assert not any(item["available"] for item in strategies)


def test_knowledge_search_translates_limit_and_calls_gateway() -> None:
    gateway = RecordingGateway()
    client = TestClient(_app(gateway), raise_server_exceptions=False)

    response = client.get(
        "/knowledge/search",
        params={
            "q": "retention policy",
            "collection_id": "collection-1",
            "strategy": "fusion_rag",
            "limit": 4,
            "filters": '{"department":"legal"}',
            "threshold": 0.0,
        },
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert gateway.calls[0] == (
        TENANT,
        {
            "collection_id": "collection-1",
            "query": "retention policy",
            "strategy_id": "fusion_rag",
            "top_k": 4,
            "filters": {"department": "legal"},
        },
    )
    assert response.json()[0]["chunk_id"] == "chunk-collection-1"


def test_knowledge_chat_uses_gateway_and_tenant_model_for_synthesis() -> None:
    provider = RecordingProvider()
    gateway = RecordingGateway(provider=provider)
    client = TestClient(_app(gateway), raise_server_exceptions=False)

    response = client.post(
        "/knowledge/chat",
        json={
            "question": "retention policy",
            "collection_ids": ["collection-1"],
            "strategy": "fusion_rag",
            "top_k": 6,
            "filters": {"department": "legal"},
        },
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert gateway.calls[0][0] is TENANT
    assert gateway.calls[0][1] == {
        "collection_id": "collection-1",
        "query": "retention policy",
        "strategy_id": "fusion_rag",
        "top_k": 6,
        "filters": {"department": "legal"},
    }
    assert provider.requests[0].model == "tenant-model"
    assert response.json()["answer"] == "Tenant answer [1]"
    assert response.json()["requested_strategy_id"] == "fusion_rag"
    assert response.json()["resolved_strategy_ids"] == ["fusion"]


def test_gateway_failure_is_sanitized_non_success_for_query_and_chat() -> None:
    gateway = RecordingGateway(error=RuntimeError("postgresql://user:secret@private"))
    client = TestClient(_app(gateway), raise_server_exceptions=False)

    query_response = client.post(
        "/rag/query",
        json={"query": "retention", "collection_id": "collection-1"},
        headers=HEADERS,
    )
    chat_response = client.post(
        "/knowledge/chat",
        json={"question": "retention", "collection_ids": ["collection-1"]},
        headers=HEADERS,
    )

    assert query_response.status_code == 503
    assert chat_response.status_code == 503
    assert "secret" not in query_response.text
    assert "secret" not in chat_response.text


@pytest.mark.asyncio
async def test_federated_search_is_gateway_backed_and_preserves_provenance() -> None:
    from app.knowledge.federated_search import federated_search

    gateway = RecordingGateway()
    results = await federated_search(
        query="retention policy",
        collection_ids=["collection-1", "collection-2"],
        gateway=gateway,
        strategy="fusion_rag",
        top_k=5,
        tenant_ctx=TENANT,
        filters={"department": "legal"},
    )

    assert [call[1]["collection_id"] for call in gateway.calls] == [
        "collection-1",
        "collection-2",
    ]
    assert all(call[0] is TENANT for call in gateway.calls)
    assert all(call[1]["top_k"] == 10 for call in gateway.calls)
    assert all(call[1]["filters"] == {"department": "legal"} for call in gateway.calls)
    assert {result["collection_id"] for result in results} == {
        "collection-1",
        "collection-2",
    }
    assert all(result["source"].endswith(".pdf") for result in results)


@pytest.mark.asyncio
async def test_rag_retriever_is_thin_gateway_synthesis_layer() -> None:
    from app.rag_platform.retriever import RAGRetriever

    provider = RecordingProvider()
    gateway = RecordingGateway(provider=provider)
    retriever = RAGRetriever(gateway=gateway)

    result = await retriever.retrieve(
        query="retention policy",
        tenant_ctx=TENANT,
        collection_id="collection-1",
        strategy="fusion_rag",
        top_k=8,
        filters={"department": "legal"},
    )

    assert gateway.calls[0][1]["strategy_id"] == "fusion_rag"
    assert gateway.calls[0][1]["top_k"] == 8
    assert gateway.calls[0][1]["filters"] == {"department": "legal"}
    assert result.requested_strategy_id == "fusion_rag"
    assert result.answer == "Tenant answer [1]"
    assert provider.requests[0].model == "tenant-model"


@pytest.mark.asyncio
async def test_agent_graph_persists_gateway_trace_and_fails_closed() -> None:
    from app.agent.graph import AgentGraph, RetrievalEntryPointError

    gateway = RecordingGateway()
    graph = AgentGraph(
        planner=FakeProvider(),
        executor=FakeProvider(),
        verifier=FakeProvider(),
        retrieval_gateway=gateway,
    )
    graph._agent_collection_ids = ["collection-1"]
    state = AgentState(
        goal="retention policy",
        tenant_ctx=TENANT,
        context={
            "_rag_strategy_override": "fusion_rag",
            "retrieval_top_k": 9,
            "retrieval_filters": {"department": "legal"},
        },
    )

    update = await graph._node_rag_retrieval(
        {"agent_state": state, "tenant_ctx": TENANT}
    )

    assert gateway.calls[0][1] == {
        "collection_id": "collection-1",
        "query": "retention policy",
        "strategy_id": "fusion_rag",
        "top_k": 9,
        "filters": {"department": "legal"},
    }
    assert "Evidence from collection-1" in update["rag_context"]
    assert state.context["rag_requested_strategy_id"] == "fusion_rag"
    assert state.context["rag_resolved_strategy_ids"] == ["fusion"]
    assert state.context["rag_citations"][0]["citation_id"] == "citation-collection-1"
    assert state.context["rag_retrieval_legs"][0]["result_count"] == 1
    assert state.context["rag_strategy_trace"][0]["status"] == "complete"
    assert state.events[-1]["type"] == "knowledge_retrieved"

    failing_gateway = RecordingGateway(error=RuntimeError("secret database address"))
    failing_graph = AgentGraph(
        planner=FakeProvider(),
        executor=FakeProvider(),
        verifier=FakeProvider(),
        retrieval_gateway=failing_gateway,
    )
    failing_graph._agent_collection_ids = ["collection-1"]
    failing_state = AgentState(goal="retention policy", tenant_ctx=TENANT)

    with pytest.raises(RetrievalEntryPointError, match="Required retrieval failed"):
        await failing_graph._node_rag_retrieval(
            {"agent_state": failing_state, "tenant_ctx": TENANT}
        )

    assert failing_state.status is GoalStatus.FAILED
    assert "secret" not in failing_state.error_message
    assert failing_state.context["rag_retrieval_status"] == "failed"
    assert failing_state.events[-1]["type"] == "knowledge_retrieval_failed"


@pytest.mark.asyncio
async def test_agent_graph_run_preserves_required_retrieval_failure_trace() -> None:
    from app.agent.graph import AgentGraph

    graph = AgentGraph(
        planner=FakeProvider(),
        executor=FakeProvider(),
        verifier=FakeProvider(),
        retrieval_gateway=RecordingGateway(error=RuntimeError("private database secret")),
    )
    graph._agent_collection_ids = ["collection-1"]

    result = await graph.run(goal="retention policy", tenant_ctx=TENANT)

    assert result.status is GoalStatus.FAILED
    assert result.error_message == "Required retrieval failed"
    assert result.context["rag_retrieval_status"] == "failed"
    assert result.events[-1]["type"] == "knowledge_retrieval_failed"
    assert "secret" not in str(result.context)
