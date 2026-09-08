"""Contract tests for product retrieval entry points."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

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
        if request.response_schema is not None:
            return CompletionResponse(
                content='{"supported": true, "reason": "entailed"}',
                model=request.model,
            )
        return CompletionResponse(content="Tenant answer [1]", model=request.model)


class FailingProvider(RecordingProvider):
    async def complete(self, request: Any) -> CompletionResponse:
        self.requests.append(request)
        raise RuntimeError("provider secret credential")


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
        resolved = (
            RAGStrategy.FUSION
            if requested in {"fusion", "fusion_rag"}
            else RAGStrategy.HYBRID
        )
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


def test_rag_query_threads_explicit_execution_id_to_gateway() -> None:
    gateway = RecordingGateway(provider=RecordingProvider())
    client = TestClient(_app(gateway), raise_server_exceptions=False)

    response = client.post(
        "/rag/query",
        json={
            "query": "retention policy",
            "collection_id": "collection-1",
            "strategy": "hybrid",
            "execution_id": "api-execution-1",
        },
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert gateway.calls[0][1]["execution_id"] == "api-execution-1"


@pytest.mark.asyncio
async def test_synthesis_fails_before_provider_call_when_goal_budget_is_exhausted() -> None:
    from app.governance.cost import CostController
    from app.rag.engine import RetrievalStrategyExecutionError
    from app.rag.gateway import _RAGCostGuard
    from app.rag_platform.retriever import RAGRetriever

    provider = RecordingProvider()
    gateway = RecordingGateway(provider=provider)
    guard = _RAGCostGuard(
        CostController(per_goal_usd=0.0, per_tenant_daily_usd=10.0),
        execution_id="goal-synthesis",
        invocation_id="invocation-synthesis",
        tenant_context=TENANT,
        strategy=RAGStrategy.HYBRID,
    )
    result = _result(
        requested="hybrid",
        resolved=RAGStrategy.HYBRID,
        collection_id="collection-1",
    )
    result._budget_context = guard

    retriever = RAGRetriever(gateway=gateway)
    with pytest.raises(RetrievalStrategyExecutionError, match="budget_exhausted"):
        await retriever.synthesize(
            query="retention",
            tenant_ctx=TENANT,
            strategy=RAGStrategy.HYBRID,
            citations=result.citations,
            budget_context=result._budget_context,
        )
    assert provider.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "budget_scope",
    ["goal", "daily"],
)
async def test_multi_claim_verifier_stops_on_goal_or_daily_budget(
    budget_scope: str,
) -> None:
    import uuid

    from app.governance.cost import CostController
    from app.rag.engine import RetrievalStrategyExecutionError
    from app.rag.gateway import _RAGCostGuard
    from app.rag_platform.retriever import MinimalCitationVerifier, RAGRetriever

    controller = (
        CostController(per_goal_usd=0.0015, per_tenant_daily_usd=10.0)
        if budget_scope == "goal"
        else CostController(per_goal_usd=10.0, per_tenant_daily_usd=0.0015)
    )

    provider = RecordingProvider()
    guard = _RAGCostGuard(
        controller,
        execution_id="goal-verify",
        invocation_id=uuid.uuid4().hex,
        tenant_context=TENANT,
        strategy=RAGStrategy.HYBRID,
    )
    verifier = MinimalCitationVerifier(provider=provider, model="tenant-model")
    result = _result(
        requested="hybrid",
        resolved=RAGStrategy.HYBRID,
        collection_id="collection-1",
    ).model_copy(
        update={"answer": "First claim [1]. Second claim [1]."}
    )
    result._budget_context = guard
    retriever = RAGRetriever(gateway=RecordingGateway(), citation_verifier=verifier)

    with pytest.raises(RetrievalStrategyExecutionError, match="budget_exhausted"):
        await retriever.verify_result(result, tenant_ctx=TENANT)

    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_synthesis_and_verification_append_safe_shared_cost_trace() -> None:
    from app.governance.cost import CostController
    from app.rag.gateway import _RAGCostGuard
    from app.rag_platform.retriever import MinimalCitationVerifier, RAGRetriever

    provider = RecordingProvider()
    guard = _RAGCostGuard(
        CostController(per_goal_usd=10.0, per_tenant_daily_usd=10.0),
        execution_id="goal-shared-budget",
        invocation_id="server-only-invocation",
        tenant_context=TENANT,
        strategy=RAGStrategy.HYBRID,
    )
    gateway = RecordingGateway(provider=provider)
    retriever = RAGRetriever(
        gateway=gateway,
        citation_verifier=MinimalCitationVerifier(
            provider=provider,
            model="tenant-model",
        ),
    )
    result = _result(
        requested="hybrid",
        resolved=RAGStrategy.HYBRID,
        collection_id="collection-1",
    )
    result._budget_context = guard
    answer = await retriever.synthesize(
        query="retention",
        tenant_ctx=TENANT,
        strategy=RAGStrategy.HYBRID,
        citations=result.citations,
        budget_context=guard,
    )
    result = result.model_copy(update={"answer": answer})
    result._budget_context = guard

    verified = await retriever.verify_result(result, tenant_ctx=TENANT)

    cost_traces = [
        trace for trace in verified.strategy_trace if trace.action == "rag_cost"
    ]
    assert [trace.detail["operation"] for trace in cost_traces] == [
        "synthesis",
        "citation_verification",
    ]
    assert all(trace.detail["execution_id"].startswith("sha256:") for trace in cost_traces)
    assert all(trace.detail["invocation_id"].startswith("sha256:") for trace in cost_traces)
    assert "goal-shared-budget" not in str(cost_traces)
    assert "server-only-invocation" not in str(cost_traces)


def test_rag_strategies_exposes_exact_canonical_contract_with_availability_metadata() -> None:
    client = TestClient(_app(RecordingGateway()), raise_server_exceptions=False)

    response = client.get("/rag/strategies", headers=HEADERS)

    assert response.status_code == 200
    strategies = response.json()["strategies"]
    assert len(strategies) == len(RAGStrategy)
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


def test_knowledge_chat_synthesis_failure_is_sanitized_non_success() -> None:
    gateway = RecordingGateway(provider=FailingProvider())
    client = TestClient(_app(gateway), raise_server_exceptions=False)

    response = client.post(
        "/knowledge/chat",
        json={"question": "retention", "collection_ids": ["collection-1"]},
        headers=HEADERS,
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Answer synthesis is unavailable"}
    assert "secret" not in response.text


def test_knowledge_search_rejects_invalid_or_conflicting_limit() -> None:
    client = TestClient(_app(RecordingGateway()), raise_server_exceptions=False)

    invalid = client.get(
        "/knowledge/search",
        params={"q": "retention", "collection_id": "collection-1", "limit": 0},
        headers=HEADERS,
    )
    conflicting = client.get(
        "/knowledge/search",
        params={
            "q": "retention",
            "collection_id": "collection-1",
            "limit": 4,
            "top_k": 5,
        },
        headers=HEADERS,
    )

    assert invalid.status_code == 422
    assert conflicting.status_code == 422
    assert conflicting.json() == {"detail": "Use top_k or limit, not both"}


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
async def test_rag_retriever_marks_unsupported_claim_ungrounded() -> None:
    from app.rag_platform.retriever import RAGRetriever

    class Verifier:
        async def verify(self, answer: str, citations: list[Any]) -> Any:
            return SimpleNamespace(
                grounded=False,
                unsupported_claims=["Unsupported claim"],
            )

    gateway = RecordingGateway(provider=RecordingProvider())
    result = await RAGRetriever(
        gateway=gateway,
        citation_verifier=Verifier(),
    ).retrieve(
        query="retention policy",
        tenant_ctx=TENANT,
        collection_id="collection-1",
        strategy="hybrid",
    )

    assert not result.grounded
    assert result.citations
    assert result.strategy_trace[-1].action == "citation_verification"
    assert result.strategy_trace[-1].detail["unsupported_claims"] == [
        "Unsupported claim"
    ]


@pytest.mark.asyncio
async def test_rag_retriever_verifier_failure_is_explicitly_ungrounded() -> None:
    from app.rag_platform.retriever import RAGRetriever

    class BrokenVerifier:
        async def verify(self, answer: str, citations: list[Any]) -> Any:
            raise RuntimeError("private verifier secret")

    result = await RAGRetriever(
        gateway=RecordingGateway(provider=RecordingProvider()),
        citation_verifier=BrokenVerifier(),
    ).retrieve(
        query="retention policy",
        tenant_ctx=TENANT,
        collection_id="collection-1",
        strategy="hybrid",
    )

    assert not result.grounded
    assert result.strategy_trace[-1].status == "failed"
    assert "secret" not in str(result.strategy_trace[-1].detail)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "contents", "grounded"),
    [
        ("Retention is thirty days [1].", ["Retention is thirty days."], True),
        ("The moon is cheese [1].", ["Retention is thirty days."], False),
        (
            "Retention is thirty days [1]. Appeals take ten days [2].",
            ["Retention is thirty days.", "Appeals take ten days."],
            True,
        ),
    ],
)
async def test_default_citation_verifier_checks_claim_support(
    answer: str,
    contents: list[str],
    grounded: bool,
) -> None:
    from app.rag_platform.retriever import MinimalCitationVerifier

    citations = [
        RAGCitation(
            citation_id="citation-1",
            chunk_id="chunk-1",
            content=part,
            score=0.9,
            source="guide.pdf",
        )
        for part in contents
    ]
    result = await MinimalCitationVerifier().verify(answer, citations)

    assert result.grounded is grounded


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claim", "evidence"),
    [
        ("The policy permits exports [1].", "The policy prohibits exports."),
        ("Approval is required [1].", "Approval is not required."),
        ("Retention is 30 days [1].", "Retention is 60 days."),
        (
            "Retention is 30 days [1]. Exports are permitted [2].",
            "Retention is 30 days. | Exports are prohibited.",
        ),
        ("Retention is 30 days [2].", "Retention is 30 days."),
    ],
)
async def test_evidence_verifier_rejects_contradictions_and_invalid_references(
    claim: str,
    evidence: str,
) -> None:
    from app.rag_platform.retriever import MinimalCitationVerifier

    citations = [
        RAGCitation(
            citation_id="citation-1",
            chunk_id="chunk-1",
            content=part,
            score=0.9,
            source="guide.pdf",
        )
        for part in evidence.split(" | ")
    ]

    result = await MinimalCitationVerifier().verify(claim, citations)

    assert not result.grounded
    assert result.reason in {"contradiction", "unsupported", "invalid_citation"}


@pytest.mark.asyncio
async def test_evidence_verifier_accepts_valid_paraphrase_with_provider() -> None:
    from app.rag_platform.retriever import MinimalCitationVerifier

    result = await MinimalCitationVerifier(
        provider=RecordingProvider(),
        model="entailment-model",
    ).verify(
        "The policy allows exports [1].",
        [
            RAGCitation(
                citation_id="citation-1",
                chunk_id="chunk-1",
                content="Exports are permitted by the policy.",
                score=0.9,
                source="guide.pdf",
            )
        ],
    )

    assert result.grounded
    assert result.reason == "supported"


@pytest.mark.asyncio
async def test_ambiguous_entailment_uses_configured_model_and_strict_json() -> None:
    from app.rag_platform.retriever import MinimalCitationVerifier

    provider = RecordingProvider()
    provider.complete = AsyncMock(
        return_value=CompletionResponse(
            content='{"supported": true, "reason": "entailed"}',
            model="entailment-model",
        )
    )
    result = await MinimalCitationVerifier(
        provider=provider,
        model="entailment-model",
    ).verify(
        "The archival window spans one quarter [1].",
        [
            RAGCitation(
                citation_id="citation-1",
                chunk_id="chunk-1",
                content="Records remain archived for three months.",
                score=0.9,
                source="guide.pdf",
            )
        ],
    )

    assert result.grounded
    request = provider.complete.await_args.args[0]
    assert request.model == "entailment-model"
    assert request.response_schema is not None


@pytest.mark.asyncio
async def test_entailment_provider_failure_is_ungrounded_and_sanitized() -> None:
    from app.rag_platform.retriever import MinimalCitationVerifier

    provider = RecordingProvider()
    provider.complete = AsyncMock(side_effect=RuntimeError("private provider secret"))
    result = await MinimalCitationVerifier(
        provider=provider,
        model="entailment-model",
    ).verify(
        "The archival window spans one quarter [1].",
        [
            RAGCitation(
                citation_id="citation-1",
                chunk_id="chunk-1",
                content="Records remain archived for three months.",
                score=0.9,
                source="guide.pdf",
            )
        ],
    )

    assert not result.grounded
    assert result.reason == "verifier_failure"
    assert "secret" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claim", "evidence"),
    [
        (
            "Retention is 30 days and exports are permitted [1].",
            "Retention is 30 days.",
        ),
        ("The policy does not prohibit exports [1].", "The policy prohibits exports."),
        ("Alice approved Bob [1].", "Bob approved Alice."),
        (
            "Plan A is 10 days and Plan B is 20 days [1].",
            "Plan A is 20 days and Plan B is 10 days.",
        ),
    ],
)
async def test_deterministic_verifier_rejects_overlap_only_adversarial_claims(
    claim: str,
    evidence: str,
) -> None:
    from app.rag_platform.retriever import MinimalCitationVerifier

    result = await MinimalCitationVerifier().verify(
        claim,
        [
            RAGCitation(
                citation_id="citation-1",
                chunk_id="chunk-1",
                content=evidence,
                score=0.9,
                source="guide.pdf",
            )
        ],
    )

    assert not result.grounded


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        '{"supported": true, "reason": "not_entailed"}',
        '{"supported": true, "reason": "contradicted"}',
        '{"supported": true, "reason": "unknown"}',
        'prefix {"supported": true, "reason": "entailed"}',
    ],
)
async def test_structured_entailment_rejects_inconsistent_or_malformed_json(
    payload: str,
) -> None:
    from app.rag_platform.retriever import MinimalCitationVerifier

    provider = RecordingProvider()
    provider.complete = AsyncMock(
        return_value=CompletionResponse(content=payload, model="entailment-model")
    )
    result = await MinimalCitationVerifier(
        provider=provider,
        model="entailment-model",
    ).verify(
        "The archival window spans one quarter [1].",
        [
            RAGCitation(
                citation_id="citation-1",
                chunk_id="chunk-1",
                content="Records remain archived for three months.",
                score=0.9,
                source="guide.pdf",
            )
        ],
    )

    assert not result.grounded


@pytest.mark.parametrize(
    ("answer", "expected_status"),
    [
        ("The moon is cheese [1].", 422),
        ("Exports are permitted [1].", 422),
        ("Evidence from collection-1 [1].", 200),
    ],
)
def test_knowledge_chat_verifies_synthesized_answer(
    answer: str,
    expected_status: int,
) -> None:
    class AnswerProvider(RecordingProvider):
        async def complete(self, request: Any) -> CompletionResponse:
            self.requests.append(request)
            if request.response_schema is not None:
                return CompletionResponse(
                    content='{"supported": false, "reason": "not_entailed"}',
                    model=request.model,
                )
            return CompletionResponse(content=answer, model=request.model)

    client = TestClient(
        _app(RecordingGateway(provider=AnswerProvider())),
        raise_server_exceptions=False,
    )
    response = client.post(
        "/knowledge/chat",
        json={"question": "policy", "collection_ids": ["collection-1"]},
        headers=HEADERS,
    )

    assert response.status_code == expected_status
    if expected_status == 200:
        assert response.json()["grounded"] is True
    else:
        assert response.json()["detail"]["code"] == "answer_ungrounded"


def test_knowledge_chat_verifier_provider_failure_is_non_2xx() -> None:
    class BrokenVerificationProvider(RecordingProvider):
        async def complete(self, request: Any) -> CompletionResponse:
            if request.response_schema is not None:
                raise RuntimeError("private verifier secret")
            return CompletionResponse(
                content="An unrelated archival statement [1].",
                model=request.model,
            )

    response = TestClient(
        _app(RecordingGateway(provider=BrokenVerificationProvider())),
        raise_server_exceptions=False,
    ).post(
        "/knowledge/chat",
        json={"question": "policy", "collection_ids": ["collection-1"]},
        headers=HEADERS,
    )

    assert response.status_code == 422
    assert "secret" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "grounded"),
    [
        ("Cats are allowed [1]; dogs are prohibited [2].", True),
        ("Cats are allowed [2]; dogs are prohibited [1].", False),
        ("Cats are allowed [1] and dogs are prohibited [2].", True),
        ("Cats are allowed [2] and dogs are prohibited [1].", False),
    ],
)
async def test_verifier_binds_each_atomic_claim_to_its_local_citation(
    answer: str,
    grounded: bool,
) -> None:
    from app.rag_platform.retriever import MinimalCitationVerifier

    result = await MinimalCitationVerifier().verify(
        answer,
        [
            RAGCitation(
                citation_id="c1", chunk_id="ch1", content="Cats are allowed.",
                score=0.9, source="cats",
            ),
                RAGCitation(
                    citation_id="c2", chunk_id="ch2", content="dogs are prohibited.",
                score=0.9, source="dogs",
            ),
        ],
    )

    assert result.grounded is grounded


@pytest.mark.asyncio
async def test_exact_phrase_inside_negated_retraction_is_not_fast_path_supported() -> None:
    from app.rag_platform.retriever import MinimalCitationVerifier

    result = await MinimalCitationVerifier().verify(
        "Exports are permitted [1].",
        [
            RAGCitation(
                citation_id="c1",
                chunk_id="ch1",
                content=(
                    "Exports are permitted only in examples, but actual exports "
                    "are not permitted."
                ),
                score=0.9,
                source="policy",
            )
        ],
    )

    assert not result.grounded


@pytest.mark.asyncio
async def test_irrelevant_evidence_number_uses_provider_not_false_contradiction() -> None:
    from app.rag_platform.retriever import MinimalCitationVerifier

    provider = RecordingProvider()
    provider.complete = AsyncMock(
        return_value=CompletionResponse(
            content='{"supported": true, "reason": "entailed"}',
            model="entailment-model",
        )
    )
    citation = RAGCitation(
        citation_id="c1",
        chunk_id="ch1",
        content="Retention is 30 days. Appendix 5 describes examples.",
        score=0.9,
        source="policy",
    )
    result = await MinimalCitationVerifier(
        provider=provider,
        model="entailment-model",
    ).verify("Retention is 30 days [1].", [citation])

    assert result.grounded
    provider.complete.assert_awaited_once()
    conservative = await MinimalCitationVerifier().verify(
        "Retention is 30 days [1].",
        [citation],
    )
    assert not conservative.grounded


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "grounded"),
    [
        ("Cats are allowed [1], dogs are prohibited [2]", True),
        ("Cats are allowed [2], dogs are prohibited [1]", False),
        ("Cats are allowed [1]\ndogs are prohibited [2]", True),
        ("Cats are allowed [2]\ndogs are prohibited [1]", False),
    ],
)
async def test_marker_scope_keeps_comma_and_newline_claims_separate(
    answer: str,
    grounded: bool,
) -> None:
    from app.rag_platform.retriever import MinimalCitationVerifier

    result = await MinimalCitationVerifier().verify(
        answer,
        [
            RAGCitation(
                citation_id="c1", chunk_id="ch1", content="Cats are allowed",
                score=0.9, source="cats",
            ),
                RAGCitation(
                    citation_id="c2", chunk_id="ch2", content="dogs are prohibited",
                score=0.9, source="dogs",
            ),
        ],
    )

    assert result.grounded is grounded


@pytest.mark.asyncio
@pytest.mark.parametrize("markers", ["[1][2]", "[1,2]"])
async def test_consecutive_multi_citations_bind_one_claim(markers: str) -> None:
    from app.rag_platform.retriever import MinimalCitationVerifier

    provider = RecordingProvider()
    result = await MinimalCitationVerifier(
        provider=provider,
        model="entailment-model",
    ).verify(
        f"The combined policy applies {markers}",
        [
            RAGCitation(
                citation_id="c1", chunk_id="ch1", content="Policy part one",
                score=0.9, source="one",
            ),
            RAGCitation(
                citation_id="c2", chunk_id="ch2", content="Policy part two",
                score=0.9, source="two",
            ),
        ],
    )

    assert result.grounded
    assert provider.requests[-1].model == "entailment-model"


@pytest.mark.asyncio
async def test_marker_parser_preserves_decimals_abbreviations_and_urls() -> None:
    from app.rag_platform.retriever import MinimalCitationVerifier

    claim = "Version 3.5 is documented by Example Inc. at https://example.com/v3.5"
    result = await MinimalCitationVerifier().verify(
        f"{claim} [1]",
        [
            RAGCitation(
                citation_id="c1", chunk_id="ch1", content=claim,
                score=0.9, source="url",
            )
        ],
    )

    assert result.grounded


@pytest.mark.asyncio
async def test_encryption_contradiction_and_one_marker_compound_fail_closed() -> None:
    from app.rag_platform.retriever import MinimalCitationVerifier

    citation = RAGCitation(
        citation_id="c1",
        chunk_id="ch1",
        content="Encryption is disabled and audit logging is enabled",
        score=0.9,
        source="security",
    )
    contradiction = await MinimalCitationVerifier().verify(
        "Encryption is enabled [1]",
        [citation],
    )
    compound = await MinimalCitationVerifier().verify(
        "Encryption is disabled and audit logging is disabled [1]",
        [citation],
    )

    assert not contradiction.grounded
    assert not compound.grounded


@pytest.mark.asyncio
async def test_every_non_identical_claim_calls_provider_and_no_provider_fails_closed() -> None:
    from app.rag_platform.retriever import MinimalCitationVerifier

    provider = RecordingProvider()
    provider.complete = AsyncMock(
        return_value=CompletionResponse(
            content='{"supported": true, "reason": "entailed"}',
            model="entailment-model",
        )
    )
    citation = RAGCitation(
        citation_id="c1", chunk_id="ch1", content="Records last three months",
        score=0.9, source="records",
    )
    supported = await MinimalCitationVerifier(
        provider=provider,
        model="entailment-model",
    ).verify("Records last one quarter [1]", [citation])
    conservative = await MinimalCitationVerifier().verify(
        "Records last one quarter [1]",
        [citation],
    )

    assert supported.grounded
    provider.complete.assert_awaited_once()
    assert not conservative.grounded


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "The amount is 1,000 USD",
        "Smith, Jones, and Lee approved the policy",
        "Version 3.5 is supported",
    ],
)
async def test_providerless_exact_normalization_preserves_meaningful_punctuation(
    text: str,
) -> None:
    from app.rag_platform.retriever import MinimalCitationVerifier

    result = await MinimalCitationVerifier().verify(
        f"  {text}.   [1]",
        [
            RAGCitation(
                citation_id="c1", chunk_id="ch1", content=text,
                score=0.9, source="policy",
            )
        ],
    )

    assert result.grounded


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claim", "evidence"),
    [
        ("Version 3.5 is supported [1]", "Version 3-5 is supported"),
        (
            "Use https://example.com/Admin?Role=Owner [1]",
            "Use https://example.com/admin?Role=Owner",
        ),
    ],
)
async def test_providerless_normalization_rejects_value_or_url_case_changes(
    claim: str,
    evidence: str,
) -> None:
    from app.rag_platform.retriever import MinimalCitationVerifier

    result = await MinimalCitationVerifier().verify(
        claim,
        [
            RAGCitation(
                citation_id="c1", chunk_id="ch1", content=evidence,
                score=0.9, source="policy",
            )
        ],
    )

    assert not result.grounded


@pytest.mark.asyncio
async def test_non_identical_comma_prose_is_one_provider_verified_claim() -> None:
    from app.rag_platform.retriever import MinimalCitationVerifier

    provider = RecordingProvider()
    provider.complete = AsyncMock(
        return_value=CompletionResponse(
            content='{"supported": true, "reason": "entailed"}',
            model="entailment-model",
        )
    )
    result = await MinimalCitationVerifier(
        provider=provider,
        model="entailment-model",
    ).verify(
        "Smith, Jones, and Lee authorized the policy [1]",
        [
            RAGCitation(
                citation_id="c1",
                chunk_id="ch1",
                content="The policy was approved by Smith, Jones, and Lee",
                score=0.9,
                source="policy",
            )
        ],
    )

    assert result.grounded
    provider.complete.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claim", "evidence"),
    [
        ("Call parseJSON [1]", "Call parseJson"),
        ("Read /srv/Admin/config [1]", "Read /srv/admin/config"),
    ],
)
async def test_case_only_identifier_and_path_differences_are_not_exact(
    claim: str,
    evidence: str,
) -> None:
    from app.rag_platform.retriever import MinimalCitationVerifier

    citation = RAGCitation(
        citation_id="c1", chunk_id="ch1", content=evidence,
        score=0.9, source="code",
    )
    conservative = await MinimalCitationVerifier().verify(claim, [citation])
    provider = RecordingProvider()
    provider.complete = AsyncMock(
        return_value=CompletionResponse(
            content='{"supported": true, "reason": "entailed"}',
            model="entailment-model",
        )
    )
    entailed = await MinimalCitationVerifier(
        provider=provider,
        model="entailment-model",
    ).verify(claim, [citation])

    assert not conservative.grounded
    assert entailed.grounded
    provider.complete.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("markers", ["[1][2]", "[1] [2]", "[1], [2]", "[1,2]"])
async def test_common_grouped_citation_forms_include_all_evidence(markers: str) -> None:
    from app.rag_platform.retriever import MinimalCitationVerifier

    provider = RecordingProvider()
    provider.complete = AsyncMock(
        return_value=CompletionResponse(
            content='{"supported": true, "reason": "entailed"}',
            model="entailment-model",
        )
    )
    result = await MinimalCitationVerifier(
        provider=provider,
        model="entailment-model",
    ).verify(
        f"Combined requirement {markers}",
        [
            RAGCitation(
                citation_id="c1", chunk_id="ch1", content="Evidence one",
                score=0.9, source="one",
            ),
            RAGCitation(
                citation_id="c2", chunk_id="ch2", content="Evidence two",
                score=0.9, source="two",
            ),
        ],
    )

    assert result.grounded
    prompt = str(provider.complete.await_args.args[0].messages[0].content)
    assert "Evidence one Evidence two" in prompt


@pytest.mark.asyncio
async def test_grouped_citations_do_not_steal_following_claim_reference() -> None:
    from app.rag_platform.retriever import MinimalCitationVerifier

    provider = RecordingProvider()
    provider.complete = AsyncMock(
        return_value=CompletionResponse(
            content='{"supported": true, "reason": "entailed"}',
            model="entailment-model",
        )
    )
    result = await MinimalCitationVerifier(
        provider=provider,
        model="entailment-model",
    ).verify(
        "Combined requirement [1], [2]; separate rule [3]",
        [
            RAGCitation(
                citation_id=f"c{index}", chunk_id=f"ch{index}",
                content=f"Evidence {index}", score=0.9, source=str(index),
            )
            for index in range(1, 4)
        ],
    )

    assert result.grounded
    assert provider.complete.await_count == 2
    prompts = [str(call.args[0].messages[0].content) for call in provider.complete.await_args_list]
    assert "Evidence 1 Evidence 2" in prompts[0]
    assert "Evidence 3" not in prompts[0]
    assert "Evidence 3" in prompts[1]


def test_knowledge_chat_preserves_merged_repeated_id_provenance() -> None:
    class DuplicateGateway(RecordingGateway):
        async def execute(
            self, tenant_context: TenantContext, **kwargs: Any
        ) -> RAGExecutionResult:
            collection_id = str(kwargs["collection_id"])
            return RAGExecutionResult(
                requested_strategy_id="hybrid",
                resolved_strategy_id=RAGStrategy.HYBRID,
                citations=[
                    RAGCitation(
                        citation_id="citation-1",
                        chunk_id=f"chunk-{collection_id}",
                        content="Shared evidence",
                        score=0.9,
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

    gateway = DuplicateGateway(provider=RecordingProvider())
    client = TestClient(_app(gateway), raise_server_exceptions=False)
    response = client.post(
        "/knowledge/chat",
        json={
            "question": "policy",
            "collection_ids": ["collection-1", "collection-2"],
        },
        headers=HEADERS,
    )

    assert response.status_code == 200
    citation = response.json()["citations"][0]
    assert citation["collection_ids"] == ["collection-1", "collection-2"]
    assert citation["sources"] == ["source-collection-1", "source-collection-2"]
    assert citation["citation_refs"] == [
        "collection-1:citation-1",
        "collection-2:citation-1",
    ]
    assert len(citation["retrieval_legs"]) == 2
    assert len(citation["strategy_trace"]) == 2


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
            "execution_id": state.goal_id,
        }
    assert "Evidence from collection-1" in update["rag_context"]
    assert state.context["rag_requested_strategy_id"] == "fusion_rag"
    assert state.context["retrieval_strategy"] == "fusion"
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


@pytest.mark.asyncio
async def test_agent_graph_emits_canonical_success_and_sanitized_failure_events() -> None:
    from app.agent.graph import AgentGraph, RetrievalEntryPointError

    success_events: list[dict[str, Any]] = []

    async def record_success(event: dict[str, Any]) -> None:
        success_events.append(event)

    success_graph = AgentGraph(
        planner=FakeProvider(),
        executor=FakeProvider(),
        verifier=FakeProvider(),
        retrieval_gateway=RecordingGateway(),
    )
    success_graph._agent_collection_ids = ["collection-1"]
    success_graph._event_callback = record_success
    success_state = AgentState(
        goal="retention policy",
        tenant_ctx=TENANT,
        context={"retrieval_strategy": "hybrid"},
    )

    await success_graph._node_rag_retrieval(
        {"agent_state": success_state, "tenant_ctx": TENANT}
    )

    success = next(event for event in success_events if event["type"] == "knowledge_retrieved")
    assert success["requested_strategy_id"] == "hybrid"
    assert success["resolved_strategy_ids"] == ["hybrid"]
    assert success["citations"][0]["citation_id"] == "citation-collection-1"
    assert success["retrieval_legs"][0]["result_count"] == 1
    assert success["strategy_trace"][0]["status"] == "complete"

    async def record_failure(event: dict[str, Any]) -> None:
        failure_events.append(event)

    failure_events: list[dict[str, Any]] = []
    failure_graph = AgentGraph(
        planner=FakeProvider(),
        executor=FakeProvider(),
        verifier=FakeProvider(),
        retrieval_gateway=RecordingGateway(error=RuntimeError("database secret")),
    )
    failure_graph._agent_collection_ids = ["collection-1"]
    failure_graph._event_callback = record_failure
    failure_state = AgentState(goal="retention policy", tenant_ctx=TENANT)

    with pytest.raises(RetrievalEntryPointError):
        await failure_graph._node_rag_retrieval(
            {"agent_state": failure_state, "tenant_ctx": TENANT}
        )

    failure = next(
        event for event in failure_events if event["type"] == "knowledge_retrieval_failed"
    )
    assert failure["requested_strategy_id"] == "hybrid"
    assert failure["status"] == "failed"
    assert "secret" not in str(failure)


@pytest.mark.asyncio
async def test_agent_graph_rejects_noncanonical_strategy_without_promotion() -> None:
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
        context={"retrieval_strategy": "direct"},
    )

    with pytest.raises(RetrievalEntryPointError):
        await graph._node_rag_retrieval({"agent_state": state, "tenant_ctx": TENANT})

    assert gateway.calls == []
    assert state.context["rag_retrieval_status"] == "failed"


@pytest.mark.asyncio
async def test_agent_graph_does_not_use_unrequested_web_fallback() -> None:
    from app.agent.graph import AgentGraph

    class EmptyGateway(RecordingGateway):
        async def execute(
            self, tenant_context: TenantContext, **kwargs: Any
        ) -> RAGExecutionResult:
            self.calls.append((tenant_context, kwargs))
            return RAGExecutionResult(
                requested_strategy_id=str(kwargs["strategy_id"]),
                resolved_strategy_id=RAGStrategy.HYBRID,
            )

    web_search = SimpleNamespace(search=AsyncMock(return_value=[{"content": "web"}]))
    graph = AgentGraph(
        planner=FakeProvider(),
        executor=FakeProvider(),
        verifier=FakeProvider(),
        retrieval_gateway=EmptyGateway(),
    )
    graph._agent_collection_ids = ["collection-1"]
    graph._web_search_tool = web_search
    state = AgentState(
        goal="retention policy",
        tenant_ctx=TENANT,
        context={"retrieval_strategy": "hybrid"},
    )

    result = await graph._node_rag_retrieval(
        {"agent_state": state, "tenant_ctx": TENANT}
    )

    assert result["rag_context"] == ""
    web_search.search.assert_not_awaited()
