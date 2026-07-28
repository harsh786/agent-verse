"""Tenant and lifecycle contract tests for the RAG retrieval gateway."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest

from app.rag.contracts import (
    RAGCitation,
    RAGExecutionRequest,
    RAGExecutionResult,
    RAGRetrievalLeg,
    RAGStrategy,
    RAGStrategyTrace,
    UnavailableRAGStrategyError,
)
from app.rag.gateway import (
    CollectionNotFoundError,
    ResolvedLLM,
    RetrievalDependencies,
    RetrievalExecutionContext,
    RetrievalGateway,
    RetrievalStrategyCapability,
    SQLCollectionAuthorizer,
)
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(
    tenant_id="tenant-1",
    plan=PlanTier.PROFESSIONAL,
    api_key_id="key-1",
)


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


class RecordingSession:
    def __init__(self, session_id: int) -> None:
        self.session_id = session_id
        self.rls_tenant_id: str | None = None
        self.closed = False

    async def __aenter__(self) -> RecordingSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        self.closed = True

    def begin(self) -> _Transaction:
        return _Transaction()


class RecordingSessionFactory:
    def __init__(self) -> None:
        self.sessions: list[RecordingSession] = []

    def __call__(self) -> RecordingSession:
        session = RecordingSession(len(self.sessions) + 1)
        self.sessions.append(session)
        return session


class RecordingAuthorizer:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[RecordingSession | None, TenantContext, str]] = []

    async def authorize(
        self,
        session: Any,
        tenant_context: TenantContext,
        collection_id: str,
    ) -> bool:
        assert session is None or session.rls_tenant_id == tenant_context.tenant_id
        self.calls.append((session, tenant_context, collection_id))
        return self.allowed


class StaticAdapter:
    def __init__(
        self,
        result: RAGExecutionResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or RAGExecutionResult(
            requested_strategy_id="placeholder",
            resolved_strategy_id=RAGStrategy.FUSION,
        )
        self.error = error
        self.calls: list[tuple[RAGExecutionRequest, RetrievalExecutionContext]] = []

    async def execute(
        self,
        request: RAGExecutionRequest,
        context: RetrievalExecutionContext,
    ) -> RAGExecutionResult:
        self.calls.append((request, context))
        if self.error is not None:
            raise self.error
        return self.result


def _gateway(
    *,
    adapter: Any | None = None,
    strategy: RAGStrategy = RAGStrategy.FUSION,
    authorizer: RecordingAuthorizer | None = None,
    session_factory: RecordingSessionFactory | None = None,
    embedder: object | None = object(),
    llm_resolver: Callable[..., object] | None = None,
    requires_embedder: bool = False,
    requires_provider: bool = False,
    graph_capability: object | None = None,
    requires_graph: bool = False,
    search_capability: object | None = None,
    requires_search: bool = False,
) -> tuple[RetrievalGateway, RecordingAuthorizer, RecordingSessionFactory]:
    factory = session_factory or RecordingSessionFactory()
    collection_authorizer = authorizer or RecordingAuthorizer()
    capabilities = {}
    if adapter is not None:
        capabilities[strategy] = RetrievalStrategyCapability(
            adapter=adapter,
            requires_embedder=requires_embedder,
            requires_provider=requires_provider,
            requires_graph=requires_graph,
            requires_search=requires_search,
        )
    dependencies = RetrievalDependencies(
        session_factory=factory,
        embedder=embedder,
        llm_resolver=llm_resolver,
        graph_capability=graph_capability,
        search_capability=search_capability,
        policy_services=(),
        collection_authorizer=collection_authorizer,
        strategy_capabilities=capabilities,
    )
    return RetrievalGateway(dependencies), collection_authorizer, factory


@pytest.fixture
def record_rls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str]]:
    calls: list[tuple[int, str]] = []

    @asynccontextmanager
    async def recording_rls(
        session: RecordingSession,
        tenant_id: str,
    ) -> AsyncIterator[RecordingSession]:
        assert session.rls_tenant_id is None
        session.rls_tenant_id = tenant_id
        calls.append((session.session_id, tenant_id))
        try:
            yield session
        finally:
            session.rls_tenant_id = None

    monkeypatch.setattr("app.rag.gateway.sqlalchemy_rls_context", recording_rls)
    return calls


@pytest.mark.asyncio
async def test_gateway_requires_tenant_context_not_bare_tenant_id() -> None:
    gateway, authorizer, _ = _gateway(adapter=StaticAdapter())

    with pytest.raises(TypeError, match="TenantContext"):
        await gateway.execute(
            "tenant-1",  # type: ignore[arg-type]
            collection_id="collection-1",
            query="retention policy",
            strategy_id="fusion",
        )

    assert authorizer.calls == []


@pytest.mark.asyncio
async def test_gateway_authorizes_collection_ownership_before_adapter_execution(
    record_rls: list[tuple[int, str]],
) -> None:
    authorizer = RecordingAuthorizer(allowed=False)
    adapter = StaticAdapter()
    gateway, _, _ = _gateway(adapter=adapter, authorizer=authorizer)

    with pytest.raises(CollectionNotFoundError, match="collection-1"):
        await gateway.execute(
            TENANT,
            collection_id="collection-1",
            query="retention policy",
            strategy_id="fusion",
        )

    assert authorizer.calls[0][1] is TENANT
    assert adapter.calls == []
    assert record_rls == [(1, TENANT.tenant_id)]


@pytest.mark.asyncio
async def test_sql_authorizer_requires_active_tenant_and_owned_collection() -> None:
    class ScalarResult:
        def scalar_one_or_none(self) -> str:
            return "collection-1"

    class SQLRecordingSession:
        def __init__(self) -> None:
            self.statement = ""
            self.parameters: dict[str, str] = {}

        async def execute(self, statement: object, parameters: dict[str, str]) -> ScalarResult:
            self.statement = str(statement)
            self.parameters = parameters
            return ScalarResult()

    session = SQLRecordingSession()

    authorized = await SQLCollectionAuthorizer().authorize(
        session,  # type: ignore[arg-type]
        TENANT,
        "collection-1",
    )

    assert authorized
    assert "JOIN tenants" in session.statement
    assert "is_active IS TRUE" in session.statement
    assert session.parameters == {
        "collection_id": "collection-1",
        "tenant_id": TENANT.tenant_id,
    }


@pytest.mark.asyncio
async def test_each_concurrent_retrieval_leg_owns_a_session_and_rls_context(
    record_rls: list[tuple[int, str]],
) -> None:
    observed_sessions: list[RecordingSession] = []

    class ConcurrentAdapter:
        async def execute(
            self,
            request: RAGExecutionRequest,
            context: RetrievalExecutionContext,
        ) -> RAGExecutionResult:
            async def leg(session: Any) -> None:
                assert session.rls_tenant_id == TENANT.tenant_id
                observed_sessions.append(session)
                await asyncio.sleep(0)
                assert session.rls_tenant_id == TENANT.tenant_id

            await asyncio.gather(
                context.run_db_operation(leg),
                context.run_db_operation(leg),
                context.run_db_operation(leg),
            )
            return RAGExecutionResult(
                requested_strategy_id=request.requested_strategy_id,
                resolved_strategy_id=RAGStrategy.FUSION,
            )

    gateway, _, factory = _gateway(adapter=ConcurrentAdapter())

    await gateway.execute(
        TENANT,
        collection_id="collection-1",
        query="retention policy",
        strategy_id="fusion",
    )

    assert len(observed_sessions) == 3
    assert len({id(session) for session in observed_sessions}) == 3
    assert len(factory.sessions) == 4  # authorization plus three retrieval legs
    assert all(session.closed for session in factory.sessions)
    assert record_rls == [
        (1, TENANT.tenant_id),
        (2, TENANT.tenant_id),
        (3, TENANT.tenant_id),
        (4, TENANT.tenant_id),
    ]


@pytest.mark.asyncio
async def test_fusion_engine_uses_gateway_owned_session_for_each_parallel_variant(
    record_rls: list[tuple[int, str]],
) -> None:
    from app.rag.engine import RetrievalResult, retrieve_fusion

    observed_sessions: list[RecordingSession] = []
    gateway, _, factory = _gateway(adapter=StaticAdapter())
    runner = gateway._session_runner(TENANT)
    assert runner is not None

    async def search_variant(
        query: str,
        query_embedding: list[float] | None,
    ) -> list[RetrievalResult]:
        async def search(session: Any) -> list[RetrievalResult]:
            assert session.rls_tenant_id == TENANT.tenant_id
            observed_sessions.append(session)
            await asyncio.sleep(0)
            return [
                RetrievalResult(
                    chunk_id=query,
                    content=query,
                    score=0.8,
                    source_metadata={},
                    retrieval_legs=["vector"],
                )
            ]

        return await runner.run(search)

    with patch(
        "app.rag.agentic.query_expander.QueryExpander.expand_for_fusion",
        return_value=["variant-1", "variant-2", "variant-3"],
    ):
        results = await retrieve_fusion(
            None,
            query="retention policy",
            query_embedding=[0.1],
            collection_id="collection-1",
            search_operation=search_variant,
        )

    assert len(results) == 3
    assert len({id(session) for session in observed_sessions}) == 3
    assert len(factory.sessions) == 3
    assert record_rls == [
        (1, TENANT.tenant_id),
        (2, TENANT.tenant_id),
        (3, TENANT.tenant_id),
    ]


@pytest.mark.asyncio
async def test_fusion_engine_propagates_gateway_operation_failure() -> None:
    from app.rag.engine import retrieve_fusion

    async def failing_search(
        query: str,
        query_embedding: list[float] | None,
    ) -> list[Any]:
        raise RuntimeError("variant retrieval failed")

    with (
        patch(
            "app.rag.agentic.query_expander.QueryExpander.expand_for_fusion",
            return_value=["variant-1"],
        ),
        pytest.raises(RuntimeError, match="variant retrieval failed"),
    ):
        await retrieve_fusion(
            None,
            query="retention policy",
            query_embedding=[0.1],
            collection_id="collection-1",
            search_operation=failing_search,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gateway_kwargs", "reason"),
    [
        ({}, "runtime adapter"),
        (
            {"adapter": StaticAdapter(), "embedder": None, "requires_embedder": True},
            "embedding provider",
        ),
        (
            {"adapter": StaticAdapter(), "requires_provider": True},
            "LLM provider",
        ),
        (
            {"adapter": StaticAdapter(), "requires_graph": True},
            "graph capability",
        ),
        (
            {"adapter": StaticAdapter(), "requires_search": True},
            "search capability",
        ),
    ],
)
async def test_missing_strategy_dependencies_raise_sanitized_unavailable_error(
    gateway_kwargs: dict[str, Any],
    reason: str,
) -> None:
    gateway, _, _ = _gateway(**gateway_kwargs)

    with pytest.raises(UnavailableRAGStrategyError) as exc_info:
        await gateway.execute(
            TENANT,
            collection_id="collection-1",
            query="retention policy",
            strategy_id="fusion",
        )

    assert reason in exc_info.value.reason
    assert "secret-value" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_provider_resolver_failure_is_sanitized() -> None:
    def broken_resolver(*_: object) -> ResolvedLLM:
        raise RuntimeError("secret-value postgresql://private")

    gateway, _, _ = _gateway(
        adapter=StaticAdapter(),
        requires_provider=True,
        llm_resolver=broken_resolver,
    )

    with pytest.raises(UnavailableRAGStrategyError) as exc_info:
        await gateway.execute(
            TENANT,
            collection_id="collection-1",
            query="retention policy",
            strategy_id="fusion",
        )

    assert exc_info.value.reason == "LLM provider resolution failed"
    assert "secret-value" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_historical_alias_retains_requested_and_resolved_ids_and_trace(
    record_rls: list[tuple[int, str]],
) -> None:
    citation = RAGCitation(
        citation_id="citation-1",
        chunk_id="chunk-1",
        content="Tenant-scoped evidence",
        score=0.9,
        source="handbook.pdf",
    )
    leg = RAGRetrievalLeg(
        strategy=RAGStrategy.HYBRID,
        query="retention policy",
        result_count=1,
    )
    trace = RAGStrategyTrace(
        strategy=RAGStrategy.FUSION,
        action="rrf_merge",
        status="complete",
    )
    adapter = StaticAdapter(
        RAGExecutionResult(
            requested_strategy_id="incorrect-adapter-value",
            resolved_strategy_id=RAGStrategy.FUSION,
            citations=[citation],
            retrieval_legs=[leg],
            strategy_trace=[trace],
            grounded=True,
        )
    )
    gateway, _, _ = _gateway(adapter=adapter)

    result = await gateway.execute(
        TENANT,
        collection_id="collection-1",
        query="retention policy",
        strategy_id="fusion_rag",
        top_k=7,
        filters={"department": "legal"},
    )

    assert result.requested_strategy_id == "fusion_rag"
    assert result.resolved_strategy_id is RAGStrategy.FUSION
    assert result.citations == [citation]
    assert result.retrieval_legs == [leg]
    assert result.strategy_trace == [trace]
    request, _ = adapter.calls[0]
    assert request.tenant_id == TENANT.tenant_id
    assert request.top_k == 7
    assert request.filters == {"department": "legal"}
    assert record_rls == [(1, TENANT.tenant_id)]


@pytest.mark.asyncio
async def test_algorithm_failure_propagates_without_empty_success_fallback(
    record_rls: list[tuple[int, str]],
) -> None:
    failure = RuntimeError("deterministic algorithm failure")
    gateway, _, _ = _gateway(adapter=StaticAdapter(error=failure))

    with pytest.raises(RuntimeError, match="deterministic algorithm failure") as exc_info:
        await gateway.execute(
            TENANT,
            collection_id="collection-1",
            query="retention policy",
            strategy_id="fusion",
        )

    assert exc_info.value is failure
    assert record_rls == [(1, TENANT.tenant_id)]


@pytest.mark.asyncio
async def test_resolved_provider_and_model_are_passed_to_adapter(
    record_rls: list[tuple[int, str]],
) -> None:
    class DeterministicProvider:
        pass

    provider = DeterministicProvider()

    async def resolve_llm(
        tenant_context: TenantContext,
        strategy: RAGStrategy,
    ) -> ResolvedLLM:
        assert tenant_context is TENANT
        assert strategy is RAGStrategy.FUSION
        return ResolvedLLM(provider=provider, model="deterministic-model")

    class ProviderAdapter(StaticAdapter):
        async def execute(
            self,
            request: RAGExecutionRequest,
            context: RetrievalExecutionContext,
        ) -> RAGExecutionResult:
            assert context.llm == ResolvedLLM(
                provider=provider,
                model="deterministic-model",
            )
            return await super().execute(request, context)

    gateway, _, _ = _gateway(
        adapter=ProviderAdapter(),
        requires_provider=True,
        llm_resolver=resolve_llm,
    )

    await gateway.execute(
        TENANT,
        collection_id="collection-1",
        query="retention policy",
        strategy_id="fusion",
    )


def test_create_app_wires_in_memory_retrieval_gateway() -> None:
    from app.main import create_app

    app = create_app(manage_pools=False)

    assert isinstance(app.state.retrieval_gateway, RetrievalGateway)
    assert app.state.retrieval_gateway.dependencies.session_factory is None
