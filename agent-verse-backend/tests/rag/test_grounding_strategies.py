"""Behavioral evidence for grounding-aware RAG strategies."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from app.governance.policies import PolicyResult
from app.providers.base import (
    CompletionRequest,
    CompletionResponse,
    EmbedRequest,
    EmbedResponse,
)
from app.rag.agentic.patterns.web_augmented import (
    SafeWebSearchCapability,
    WebEvidence,
    WebSearchRequest,
)
from app.rag.contracts import (
    RAG_RUNTIME_CAPABILITIES,
    RAGExecutionRequest,
    RAGStrategy,
)
from app.rag.engine import RetrievalResult
from app.rag.gateway import (
    KnowledgeStoreCollectionAuthorizer,
    ResolvedLLM,
    RetrievalDependencies,
    RetrievalExecutionContext,
    RetrievalGateway,
    RetrievalRuntimeDependencies,
    TenantScopedGraphCapabilityAdapter,
    core_strategy_capabilities,
)
from app.tenancy.context import TenantContext

TENANT = TenantContext("tenant-1", "enterprise", "key-1")


class _Embedder:
    def __init__(self) -> None:
        self.requests: list[EmbedRequest] = []

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        self.requests.append(request)
        value = float(len(self.requests))
        return EmbedResponse(embeddings=[[value, 0.25]], model="embed-model")


class _CorrectiveProvider:
    def __init__(self) -> None:
        self.requests: list[CompletionRequest] = []
        self.reformulations = 0

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        if request.messages[0].content.startswith("Reformulate"):
            self.reformulations += 1
            content = f"reformulated policy query {self.reformulations}"
        else:
            content = '{"relevance": [0.1]}'
        return CompletionResponse(content=content, model=request.model)


class _MixedCorrectiveProvider:
    def __init__(self) -> None:
        self.grades = iter(
            [
                '{"relevance": [0.9, 0.2]}',
                '{"relevance": [0.85, 0.8]}',
            ]
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        content = (
            "focused retention query"
            if request.messages[0].content.startswith("Reformulate")
            else next(self.grades)
        )
        return CompletionResponse(content=content, model=request.model)


class _Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def scalar_one_or_none(self) -> str | None:
        return str(self._rows[0][0]) if self._rows else None


class _AuthorizedSession:
    async def execute(self, statement: object, params: object = None) -> _Rows:
        return _Rows([("collection-1",)])


class _GraphSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(
        self,
        statement: object,
        params: dict[str, Any] | None = None,
    ) -> _Rows:
        sql = str(statement)
        parameters = params or {}
        self.calls.append((sql, parameters))
        assert parameters["tenant_id"] == TENANT.tenant_id
        if "FROM knowledge_collections" in sql:
            return _Rows([("collection-1",)])
        if "graph_entity_evidence" in sql:
            return _Rows(
                [
                    (
                        "entity-1",
                        "Retention Schedule",
                        "The schedule governs archived records.",
                        "seed-1",
                        0.82,
                        {"origin": "policy.pdf"},
                    )
                ]
            )
        if "graph_path_evidence" in sql:
            return _Rows(
                [
                    (
                        "edge-1",
                        "entity-1",
                        "entity-2",
                        "depends_on",
                        "Retention depends on legal hold status.",
                        "policy.pdf#p4",
                        0.76,
                    )
                ]
            )
        if "graph_community_evidence" in sql:
            return _Rows(
                [
                    (
                        "community-1",
                        "Retention, Legal Hold",
                        "Retention and legal hold controls form one policy cluster.",
                        0.71,
                    )
                ]
            )
        raise AssertionError(sql)


class _WebCapability:
    configured = True

    def __init__(self) -> None:
        self.requests: list[WebSearchRequest] = []

    async def search(self, request: WebSearchRequest) -> list[WebEvidence]:
        self.requests.append(request)
        return [
            WebEvidence(
                title="Current retention guidance",
                url="https://8.8.8.8/retention",
                content="Current external retention guidance.",
                fetched_at=datetime(2026, 7, 30, tzinfo=UTC),
                source="safe-search",
                domain="8.8.8.8",
                freshness_seconds=0.0,
            ),
            WebEvidence(
                title="Result beyond requested bound",
                url="https://8.8.4.4/extra",
                content="This result must be bounded.",
                fetched_at=datetime(2026, 7, 30, tzinfo=UTC),
                source="safe-search",
                domain="8.8.4.4",
                freshness_seconds=0.0,
            ),
        ]


class _AllowWebPolicy:
    def evaluate(self, tool_name: str, *, tenant_ctx: TenantContext) -> PolicyResult:
        assert tool_name == "web_search"
        assert tenant_ctx is TENANT
        return PolicyResult.ALLOW

    def web_allowed_domains(self, tenant_ctx: TenantContext) -> tuple[str, ...]:
        assert tenant_ctx is TENANT
        return ("8.8.8.8",)


class _DenyWebPolicy:
    def evaluate(self, tool_name: str, *, tenant_ctx: TenantContext) -> PolicyResult:
        return PolicyResult.DENY


class _CollectionStore:
    def get_collection(self, collection_id: str, *, tenant_ctx: TenantContext) -> object:
        return object()


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        return None


class _ProbeSession:
    def begin(self) -> _Transaction:
        return _Transaction()

    async def execute(self, statement: object, params: object = None) -> object:
        return SimpleNamespace(scalar_one_or_none=lambda: "collection-1")

    async def scalar(self, statement: object) -> object:
        return True if "to_regclass" in str(statement) else 1


@asynccontextmanager
async def _session_factory() -> Any:
    yield _ProbeSession()


def _request(strategy: RAGStrategy, *, top_k: int = 6) -> RAGExecutionRequest:
    return RAGExecutionRequest(
        tenant_id=TENANT.tenant_id,
        query="current retention policy relationships",
        requested_strategy_id=strategy.value,
        collection_id="collection-1",
        top_k=top_k,
        filters={"department": "legal"},
    )


def _context(
    strategy: RAGStrategy,
    *,
    embedder: object | None = None,
    provider: object | None = None,
    graph: object | None = None,
    web: SafeWebSearchCapability | None = None,
    policies: tuple[object, ...] = (),
    available: tuple[RAGStrategy, ...] = (),
    runner: Callable[[Callable[[Any], Awaitable[Any]]], Awaitable[Any]] | None = None,
) -> RetrievalExecutionContext:
    return RetrievalExecutionContext(
        tenant_context=TENANT,
        strategy=strategy,
        filters={"department": "legal"},
        dependencies=RetrievalRuntimeDependencies(
            embedder=embedder,
            llm=(
                ResolvedLLM(provider=provider, model="tenant-rag-model", provider_type="test")
                if provider is not None
                else None
            ),
            graph_capability=graph,  # type: ignore[arg-type]
            search_capability=web,
            policy_services=policies,
            available_strategies=available,
        ),
        _db_operation_runner=runner,
    )


async def test_graph_merges_vector_entity_path_and_community_provenance() -> None:
    graph_session = _GraphSession()
    runner_calls = 0

    async def runner(operation: Callable[[Any], Awaitable[Any]]) -> Any:
        nonlocal runner_calls
        runner_calls += 1
        return await operation(graph_session)

    graph = TenantScopedGraphCapabilityAdapter().bind(runner)

    async def persisted_search(_session: object, **kwargs: Any) -> list[RetrievalResult]:
        assert kwargs["retrieval_mode"] == "vector"
        assert kwargs["metadata_filter"] == {"department": "legal"}
        assert kwargs["top_k"] == 6
        return [
            RetrievalResult(
                "seed-1",
                "Persisted retention policy seed.",
                0.91,
                {"source": "policy.pdf", "page": 2},
                ["vector"],
            )
        ]

    adapter = core_strategy_capabilities()[RAGStrategy.GRAPH].adapter
    with patch("app.rag.engine.hybrid_search", side_effect=persisted_search):
        result = await adapter.execute(
            _request(RAGStrategy.GRAPH),
            _context(
                RAGStrategy.GRAPH,
                embedder=_Embedder(),
                graph=graph,
                runner=runner,
            ),
        )

    assert runner_calls == 2  # vector seed operation plus graph capability operation
    assert {citation.metadata["source_type"] for citation in result.citations} == {
        "persisted",
        "graph",
    }
    assert {citation.metadata.get("graph_evidence_type") for citation in result.citations} == {
        None,
        "entity",
        "path",
        "community",
    }
    assert result.citations[0].metadata["source"] == "policy.pdf"
    assert all(
        citation.metadata.get("provenance")
        for citation in result.citations
        if citation.metadata["source_type"] == "graph"
    )
    graph_citations = [
        citation for citation in result.citations if citation.metadata["source_type"] == "graph"
    ]
    assert all(
        citation.metadata["tenant_id"].startswith("sha256:")
        for citation in result.citations
    )
    assert all(citation.metadata["tenant_id"] != TENANT.tenant_id for citation in result.citations)
    graph_trace = next(
        trace for trace in result.strategy_trace if trace.action == "graph_evidence_merge"
    )
    assert graph_trace.detail["tenant_id"] == graph_citations[0].metadata["tenant_id"]
    graph_leg_traces = [
        trace
        for trace in result.strategy_trace
        if trace.action == "retrieval_leg"
        and str(trace.detail.get("component", "")).startswith("graph_")
    ]
    assert all(
        trace.detail["tenant_id"] == graph_citations[0].metadata["tenant_id"]
        for trace in graph_leg_traces
    )
    assert all("tenant_id" in params for _, params in graph_session.calls)
    graph_sql = [sql for sql, _ in graph_session.calls if "graph_" in sql]
    assert all("metadata_filter" in sql for sql in graph_sql)


async def test_graph_is_unavailable_without_typed_graph_capability() -> None:
    gateway = RetrievalGateway(
        RetrievalDependencies(
            session_factory=_session_factory,  # type: ignore[arg-type]
            collection_authorizer=KnowledgeStoreCollectionAuthorizer(_CollectionStore()),
            strategy_capabilities=core_strategy_capabilities(),
            embedder=_Embedder(),
        )
    )

    readiness = await gateway.readiness(TENANT, strategy_id=RAGStrategy.GRAPH)

    assert not readiness.available
    assert readiness.reason == "graph_capability_unavailable"


async def test_corrective_grades_reformulates_bounded_retries_then_uses_web() -> None:
    provider = _CorrectiveProvider()
    embedder = _Embedder()
    web = _WebCapability()
    searched_queries: list[str] = []

    async def runner(operation: Callable[[Any], Awaitable[Any]]) -> Any:
        return await operation(_AuthorizedSession())

    async def persisted_search(_session: object, **kwargs: Any) -> list[RetrievalResult]:
        searched_queries.append(kwargs["query"])
        return [
            RetrievalResult(
                f"persisted-{len(searched_queries)}",
                "Insufficient persisted evidence.",
                0.2,
                {"source": "stale.pdf"},
                ["vector"],
            )
        ]

    adapter = core_strategy_capabilities()[RAGStrategy.CORRECTIVE].adapter
    with patch("app.rag.engine.hybrid_search", side_effect=persisted_search):
        result = await adapter.execute(
            _request(RAGStrategy.CORRECTIVE, top_k=1),
            _context(
                RAGStrategy.CORRECTIVE,
                embedder=embedder,
                provider=provider,
                web=web,
                policies=(_AllowWebPolicy(),),
                runner=runner,
            ),
        )

    assert searched_queries == [
        "current retention policy relationships",
        "reformulated policy query 1",
        "reformulated policy query 2",
    ]
    assert [request.texts[0] for request in embedder.requests] == searched_queries
    assert all(request.model == "tenant-rag-model" for request in provider.requests)
    assert [trace.action for trace in result.strategy_trace].count("evidence_grade") == 3
    assert [trace.action for trace in result.strategy_trace].count("query_reformulation") == 2
    assert result.strategy_trace[-1].detail["stop_reason"] == "web_fallback_complete"
    assert [citation.metadata["source_type"] for citation in result.citations] == ["web"]
    assert len(web.requests) == 1


async def test_corrective_filters_mixed_grades_and_retries_until_evidence_is_sufficient() -> None:
    provider = _MixedCorrectiveProvider()
    searched_queries: list[str] = []

    async def runner(operation: Callable[[Any], Awaitable[Any]]) -> Any:
        return await operation(_AuthorizedSession())

    async def persisted_search(_session: object, **kwargs: Any) -> list[RetrievalResult]:
        searched_queries.append(kwargs["query"])
        if len(searched_queries) == 1:
            return [
                RetrievalResult("keep-initial", "relevant", 0.8, {}, ["vector"]),
                RetrievalResult("drop-initial", "irrelevant", 0.7, {}, ["vector"]),
            ]
        return [
            RetrievalResult("keep-retry-1", "relevant retry", 0.75, {}, ["vector"]),
            RetrievalResult("keep-retry-2", "supporting retry", 0.7, {}, ["vector"]),
        ]

    adapter = core_strategy_capabilities()[RAGStrategy.CORRECTIVE].adapter
    with patch("app.rag.engine.hybrid_search", side_effect=persisted_search):
        result = await adapter.execute(
            _request(RAGStrategy.CORRECTIVE, top_k=3),
            _context(
                RAGStrategy.CORRECTIVE,
                embedder=_Embedder(),
                provider=provider,
                runner=runner,
            ),
        )

    assert searched_queries == [
        "current retention policy relationships",
        "focused retention query",
    ]
    assert {citation.chunk_id for citation in result.citations} == {
        "keep-initial",
        "keep-retry-1",
        "keep-retry-2",
    }
    assert "drop-initial" not in {citation.chunk_id for citation in result.citations}
    assert result.strategy_trace[-1].detail["stop_reason"] == "persisted_evidence_sufficient"


@pytest.mark.parametrize(
    ("web", "policies", "stop_reason"),
    [
        (None, (_AllowWebPolicy(),), "web_capability_unavailable"),
        (_WebCapability(), (), "web_policy_unavailable"),
        (_WebCapability(), (_DenyWebPolicy(),), "web_policy_denied"),
    ],
)
async def test_corrective_never_uses_web_without_capability_and_allow_policy(
    web: SafeWebSearchCapability | None,
    policies: tuple[object, ...],
    stop_reason: str,
) -> None:
    provider = _CorrectiveProvider()

    async def runner(operation: Callable[[Any], Awaitable[Any]]) -> Any:
        return await operation(_AuthorizedSession())

    async def persisted_search(_session: object, **_: Any) -> list[RetrievalResult]:
        return [RetrievalResult("low", "low evidence", 0.1, {}, ["vector"])]

    adapter = core_strategy_capabilities()[RAGStrategy.CORRECTIVE].adapter
    with patch("app.rag.engine.hybrid_search", side_effect=persisted_search):
        result = await adapter.execute(
            _request(RAGStrategy.CORRECTIVE),
            _context(
                RAGStrategy.CORRECTIVE,
                embedder=_Embedder(),
                provider=provider,
                web=web,
                policies=policies,
                runner=runner,
            ),
        )

    assert result.strategy_trace[-1].detail["stop_reason"] == stop_reason
    if isinstance(web, _WebCapability):
        assert web.requests == []


async def test_adaptive_selects_only_available_strategy_and_records_reason() -> None:
    async def runner(operation: Callable[[Any], Awaitable[Any]]) -> Any:
        return await operation(_AuthorizedSession())

    async def persisted_search(_session: object, **kwargs: Any) -> list[RetrievalResult]:
        assert kwargs["retrieval_mode"] == "hybrid"
        return [
            RetrievalResult(
                "hybrid-1",
                "Available persisted evidence.",
                0.8,
                {"source": "policy.pdf"},
                ["vector", "fts"],
            )
        ]

    adapter = core_strategy_capabilities()[RAGStrategy.ADAPTIVE].adapter
    with patch("app.rag.engine.hybrid_search", side_effect=persisted_search):
        result = await adapter.execute(
            RAGExecutionRequest(
                tenant_id=TENANT.tenant_id,
                query="show graph relationships for retention",
                requested_strategy_id="adaptive",
                collection_id="collection-1",
            ),
            _context(
                RAGStrategy.ADAPTIVE,
                embedder=_Embedder(),
                available=(RAGStrategy.HYBRID,),
                runner=runner,
            ),
        )

    decision = result.strategy_trace[0]
    assert result.resolved_strategy_id is RAGStrategy.ADAPTIVE
    assert decision.action == "adaptive_selection"
    assert decision.detail["selected_strategy"] == "hybrid"
    assert decision.detail["reason"] == "graph_unavailable; selected_default_hybrid"
    assert decision.detail["decision_count"] == 1


async def test_adaptive_resolves_the_selected_strategys_configured_model() -> None:
    class HyDEProvider:
        def __init__(self) -> None:
            self.requests: list[CompletionRequest] = []

        async def complete(self, request: CompletionRequest) -> CompletionResponse:
            self.requests.append(request)
            return CompletionResponse(
                content="A hypothetical document about orchestration.",
                model=request.model,
            )

    provider = HyDEProvider()
    resolved_strategies: list[RAGStrategy] = []

    def resolve_llm(
        tenant_context: TenantContext,
        strategy: RAGStrategy,
    ) -> ResolvedLLM | None:
        resolved_strategies.append(strategy)
        if strategy is RAGStrategy.HYDE:
            return ResolvedLLM(provider=provider, model="hyde-only-model")
        return None

    gateway = RetrievalGateway(
        RetrievalDependencies(
            session_factory=_session_factory,  # type: ignore[arg-type]
            collection_authorizer=KnowledgeStoreCollectionAuthorizer(_CollectionStore()),
            strategy_capabilities=core_strategy_capabilities(),
            embedder=_Embedder(),
            llm_resolver=resolve_llm,
        )
    )

    async def persisted_search(_session: object, **_: Any) -> list[RetrievalResult]:
        return [
            RetrievalResult(
                "hyde-1",
                "Grounded orchestration evidence.",
                0.8,
                {"source": "guide.pdf"},
                ["vector"],
            )
        ]

    with patch("app.rag.engine.hybrid_search", side_effect=persisted_search):
        result = await gateway.execute(
            TENANT,
            collection_id="collection-1",
            query="what is dynamic orchestration",
            strategy_id=RAGStrategy.ADAPTIVE,
        )

    assert result.strategy_trace[0].detail["selected_strategy"] == "hyde"
    assert provider.requests[0].model == "hyde-only-model"
    assert RAGStrategy.ADAPTIVE in resolved_strategies
    assert RAGStrategy.HYDE in resolved_strategies


async def test_web_augmented_bounds_safe_results_and_preserves_freshness() -> None:
    web = _WebCapability()

    async def runner(operation: Callable[[Any], Awaitable[Any]]) -> Any:
        return await operation(_AuthorizedSession())

    async def persisted_search(_session: object, **kwargs: Any) -> list[RetrievalResult]:
        assert kwargs["metadata_filter"] == {"department": "legal"}
        return [
            RetrievalResult(
                "persisted-1",
                "Persisted retention evidence.",
                0.9,
                {"source": "policy.pdf"},
                ["vector", "fts"],
            )
        ]

    adapter = core_strategy_capabilities()[RAGStrategy.WEB_AUGMENTED].adapter
    with patch("app.rag.engine.hybrid_search", side_effect=persisted_search):
        result = await adapter.execute(
            _request(RAGStrategy.WEB_AUGMENTED, top_k=2),
            _context(
                RAGStrategy.WEB_AUGMENTED,
                embedder=_Embedder(),
                web=web,
                policies=(_AllowWebPolicy(),),
                runner=runner,
            ),
        )

    assert len(result.citations) == 2
    assert {citation.metadata["source_type"] for citation in result.citations} == {
        "persisted",
        "web",
    }
    web_citation = next(
        citation for citation in result.citations if citation.metadata["source_type"] == "web"
    )
    assert web_citation.metadata["url"] == "https://8.8.8.8/retention"
    assert web_citation.metadata["domain"] == "8.8.8.8"
    assert web_citation.metadata["fetched_at"] == "2026-07-30T00:00:00+00:00"
    assert web_citation.metadata["freshness_seconds"] >= 0
    request = web.requests[0]
    assert request.tenant_id == TENANT.tenant_id
    assert request.allowed_domains == ("8.8.8.8",)
    assert request.max_results <= 2
    assert request.max_bytes <= 65_536
    assert request.timeout_seconds <= 10.0


@pytest.mark.parametrize(
    ("web", "policies", "reason"),
    [
        (None, (_AllowWebPolicy(),), "search_capability_unavailable"),
        (_WebCapability(), (), "web_policy_unavailable"),
        (_WebCapability(), (_DenyWebPolicy(),), "web_policy_denied"),
    ],
)
async def test_web_readiness_is_explicit_without_capability_or_allow_policy(
    web: SafeWebSearchCapability | None,
    policies: tuple[object, ...],
    reason: str,
) -> None:
    gateway = RetrievalGateway(
        RetrievalDependencies(
            session_factory=_session_factory,  # type: ignore[arg-type]
            collection_authorizer=KnowledgeStoreCollectionAuthorizer(_CollectionStore()),
            strategy_capabilities=core_strategy_capabilities(),
            embedder=_Embedder(),
            search_capability=web,
            policy_services=policies,
        )
    )

    readiness = await gateway.readiness(TENANT, strategy_id=RAGStrategy.WEB_AUGMENTED)

    assert not readiness.available
    assert readiness.reason == reason


def test_registry_promotes_exactly_tasks_one_through_six_strategies() -> None:
    from app.orchestration.strategy_registry import (
        StrategyCategory,
        StrategyState,
        build_default_registry,
    )

    expected = {
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
    implemented = {
        RAGStrategy(capability.strategy_id)
        for capability in build_default_registry().list_by_category(StrategyCategory.RAG)
        if capability.state is StrategyState.IMPLEMENTED
    }

    assert set(RAG_RUNTIME_CAPABILITIES) == expected
    assert set(core_strategy_capabilities()) == expected
    assert implemented == expected
