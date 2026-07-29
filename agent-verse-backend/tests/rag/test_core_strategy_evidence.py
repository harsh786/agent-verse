"""Execution evidence for the first five certified RAG strategies."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from app.api.rag_platform import list_strategies
from app.orchestration.strategy_registry import (
    StrategyCategory,
    StrategyState,
    build_default_registry,
)
from app.providers.base import (
    CompletionRequest,
    CompletionResponse,
    EmbedRequest,
    EmbedResponse,
)
from app.rag.bm25 import BM25CorpusScorer
from app.rag.contracts import RAG_RUNTIME_CAPABILITIES, RAGExecutionRequest, RAGStrategy
from app.rag.engine import RetrievalLegExecutionError, RetrievalResult, hybrid_search
from app.rag.gateway import (
    KnowledgeStoreCollectionAuthorizer,
    ResolvedLLM,
    RetrievalDependencies,
    RetrievalExecutionContext,
    RetrievalGateway,
    RetrievalRuntimeDependencies,
    SQLCollectionAuthorizer,
    core_strategy_capabilities,
)
from app.tenancy.context import TenantContext


class _Embedder:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.input_types: list[str] = []

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        self.texts.extend(request.texts)
        self.input_types.append(request.input_type)
        return EmbedResponse(
            embeddings=[[float(len(self.texts)), 0.25] for _ in request.texts],
            model="embed-model",
        )


class _Provider:
    def __init__(self, responses: list[str]) -> None:
        self._responses = iter(responses)
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        return CompletionResponse(content=next(self._responses), model=request.model)


def _request(strategy: RAGStrategy) -> RAGExecutionRequest:
    return RAGExecutionRequest(
        tenant_id="tenant-1",
        query="private retention policy",
        requested_strategy_id=strategy.value,
        collection_id="collection-1",
        top_k=4,
        filters={"department": "legal"},
    )


def _context(
    strategy: RAGStrategy,
    *,
    embedder: object,
    provider: object | None = None,
    runner: Callable[[Callable[[Any], Awaitable[Any]]], Awaitable[Any]],
) -> RetrievalExecutionContext:
    return RetrievalExecutionContext(
        tenant_context=TenantContext(
            tenant_id="tenant-1", api_key_id="key-1", plan="enterprise"
        ),
        strategy=strategy,
        filters={"department": "legal"},
        dependencies=RetrievalRuntimeDependencies(
            embedder=embedder,
            llm=(
                ResolvedLLM(provider=provider, model="tenant-rag-model", provider_type="test")
                if provider is not None
                else None
            ),
            graph_capability=None,
            search_capability=None,
            policy_services=(),
        ),
        _db_operation_runner=runner,
    )


async def test_naive_is_one_persisted_vector_leg_only() -> None:
    embedder = _Embedder()
    calls: list[dict[str, Any]] = []

    async def runner(operation: Callable[[Any], Awaitable[Any]]) -> Any:
        return await operation(_AuthorizedSession())

    async def search(_session: object, **kwargs: Any) -> list[RetrievalResult]:
        calls.append(kwargs)
        kwargs["evidence"].append(
            {
                "component": "vector",
                "result_count": 1,
                "component_scores": {"chunk-1": 0.91},
                "latency_ms": 1.0,
            }
        )
        return [
            RetrievalResult(
                "chunk-1",
                "retention evidence",
                0.91,
                {"source": "policy.pdf"},
                ["vector"],
                component_scores={"vector": 0.91},
            )
        ]

    adapter = core_strategy_capabilities()[RAGStrategy.NAIVE].adapter
    with patch("app.rag.engine.hybrid_search", side_effect=search):
        result = await adapter.execute(
            _request(RAGStrategy.NAIVE),
            _context(RAGStrategy.NAIVE, embedder=embedder, runner=runner),
        )

    assert embedder.texts == ["private retention policy"]
    assert embedder.input_types == ["query"]
    assert len(calls) == 1
    assert calls[0]["retrieval_mode"] == "vector"
    assert calls[0]["strict"] is True
    assert calls[0]["metadata_filter"] == {"department": "legal"}
    assert [leg.metadata["component"] for leg in result.retrieval_legs] == ["vector"]
    assert result.resolved_strategy_id is RAGStrategy.NAIVE


class _Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class _AuthorizedResult:
    def scalar_one_or_none(self) -> str:
        return "collection-1"


class _AuthorizedSession:
    async def execute(self, statement: object, params: object = None) -> _AuthorizedResult:
        return _AuthorizedResult()


class _HybridSession:
    def __init__(self, *, fail_bm25: bool = False) -> None:
        self.sql: list[str] = []
        self.fail_bm25 = fail_bm25

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Rows:
        sql = str(statement)
        self.sql.append(sql)
        if "SELECT embedding_dim" in sql:
            return _Rows([(1536,)])
        if "set_config" in sql:
            return _Rows([])
        if "<=>" in sql and "SELECT id" in sql:
            return _Rows(
                [("vector", "vector evidence", {"source": "v", "department": "legal"}, 0.9)]
            )
        if "ts_rank_cd" in sql:
            return _Rows(
                [("shared", "shared evidence", {"source": "f", "department": "legal"}, 0.8)]
            )
        if "similarity(content" in sql:
            return _Rows(
                [("shared", "shared evidence", {"source": "f", "department": "legal"}, 0.7)]
            )
        if "ORDER BY id" in sql:
            if self.fail_bm25:
                raise RuntimeError("corpus unavailable")
            return _Rows(
                [
                    (
                        "bm25",
                        "private retention policy archive",
                        {"source": "b", "department": "legal"},
                    ),
                    ("other", "unrelated words", {"source": "x", "department": "legal"}),
                ]
            )
        raise AssertionError(sql)


class _PaginatedCorpusSession(_HybridSession):
    def __init__(self, *, every_document_matches: bool = False) -> None:
        super().__init__()
        self.corpus = [
            (
                f"chunk-{index:04d}",
                (
                    f"needle unique_token_{index}"
                    if every_document_matches
                    else f"ordinary unique_token_{index}"
                ),
                {"department": "legal"},
            )
            for index in range(501)
        ]
        if not every_document_matches:
            self.corpus[-1] = (
                "chunk-0500",
                "needle only appears after the first corpus page",
                {"department": "legal"},
            )

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Rows:
        sql = str(statement)
        if any(component in sql for component in ("<=>", "ts_rank_cd", "similarity(content")):
            self.sql.append(sql)
            return _Rows([])
        if "ORDER BY id ASC" not in sql or "SELECT id, content" not in sql:
            return await super().execute(statement, params)
        self.sql.append(sql)
        parameters = params or {}
        after_id = parameters.get("after_id")
        page_size = int(parameters.get("page_size", parameters.get("corpus_limit", 200)))
        rows = [row for row in self.corpus if after_id is None or row[0] > after_id]
        return _Rows(rows[:page_size])


async def test_hybrid_executes_four_real_legs_and_records_scores() -> None:
    session = _HybridSession()
    evidence: list[dict[str, Any]] = []

    results = await hybrid_search(
        session,  # type: ignore[arg-type]
        query="private retention policy",
        query_embedding=[0.1] * 1536,
        collection_id="collection-1",
        top_k=4,
        metadata_filter={"department": "legal"},
        strict=True,
        evidence=evidence,
    )

    assert [item["component"] for item in evidence] == ["vector", "fts", "trigram", "bm25"]
    assert all("result_count" in item for item in evidence)
    assert all(item["latency_ms"] > 0 for item in evidence)
    assert {leg for result in results for leg in result.retrieval_legs} == {
        "vector",
        "fts",
        "trigram",
        "bm25",
    }
    assert all(result.rrf_score == result.score for result in results)
    assert results == sorted(results, key=lambda item: (-item.score, item.chunk_id))
    assert all("component_scores" in item for item in evidence)
    assert "BM25" not in next(sql for sql in session.sql if "ts_rank_cd" in sql)


async def test_every_persisted_leg_filters_expired_chunks_in_sql() -> None:
    session = _HybridSession()
    await hybrid_search(
        session,  # type: ignore[arg-type]
        query="private retention policy",
        query_embedding=[0.1] * 1536,
        collection_id="collection-1",
        top_k=4,
        metadata_filter={"department": "legal"},
        strict=True,
        evidence=[],
    )

    retrieval_sql = [sql for sql in session.sql if "FROM knowledge_chunks_" in sql]
    assert len(retrieval_sql) == 5
    assert all(
        "expires_at IS NULL OR expires_at > now()" in sql for sql in retrieval_sql
    )


async def test_collection_authorizer_requires_active_collection() -> None:
    class Result:
        def scalar_one_or_none(self) -> None:
            return None

    class Session:
        sql = ""

        async def execute(self, statement: object, params: object) -> Result:
            self.sql = str(statement)
            return Result()

    session = Session()
    authorized = await SQLCollectionAuthorizer().authorize(
        session,  # type: ignore[arg-type]
        TenantContext(tenant_id="tenant-1", api_key_id="key-1", plan="enterprise"),
        "collection-1",
    )

    assert not authorized
    assert "collection.is_active IS TRUE" in session.sql


async def test_hybrid_strict_mode_fails_when_bm25_corpus_leg_fails() -> None:
    with pytest.raises(RetrievalLegExecutionError, match="bm25"):
        await hybrid_search(
            _HybridSession(fail_bm25=True),  # type: ignore[arg-type]
            query="retention",
            query_embedding=[0.1] * 1536,
            collection_id="collection-1",
            strict=True,
            evidence=[],
        )


async def test_bm25_scores_complete_collection_without_hidden_cap() -> None:
    session = _PaginatedCorpusSession()
    evidence: list[dict[str, Any]] = []

    results = await hybrid_search(
        session,  # type: ignore[arg-type]
        query="needle",
        query_embedding=[0.1] * 1536,
        collection_id="collection-1",
        top_k=1,
        metadata_filter={"department": "legal"},
        strict=True,
        evidence=evidence,
    )

    bm25_evidence = next(item for item in evidence if item["component"] == "bm25")
    assert results[0].chunk_id == "chunk-0500"
    assert bm25_evidence["corpus_size"] == 501
    assert bm25_evidence["scoring_mode"] == "application_okapi_bm25_two_pass_keyset"
    assert bm25_evidence["pages_scanned"] == 4
    assert all("corpus_limit" not in sql for sql in session.sql)


async def test_bm25_high_cardinality_stats_and_heap_are_query_bounded() -> None:
    scorer = BM25CorpusScorer(query="needle second")
    for index in range(10_000):
        scorer.observe(f"needle corpus_unique_{index}")

    assert scorer.tracked_term_count == 2
    assert scorer.document_frequency_terms == frozenset({"needle", "second"})

    evidence: list[dict[str, Any]] = []
    await hybrid_search(
        _PaginatedCorpusSession(every_document_matches=True),  # type: ignore[arg-type]
        query="needle",
        query_embedding=[0.1] * 1536,
        collection_id="collection-1",
        top_k=1,
        metadata_filter={"department": "legal"},
        strict=True,
        evidence=evidence,
    )

    bm25_evidence = next(item for item in evidence if item["component"] == "bm25")
    assert bm25_evidence["tracked_term_count"] == 1
    assert bm25_evidence["heap_capacity"] == 3
    assert bm25_evidence["max_heap_size"] == 3


async def test_full_corpus_bm25_scan_propagates_cancellation() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class SlowCorpusSession(_HybridSession):
        async def execute(
            self,
            statement: Any,
            params: dict[str, Any] | None = None,
        ) -> _Rows:
            if "SELECT id, content" in str(statement):
                started.set()
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            return await super().execute(statement, params)

    task = asyncio.create_task(
        hybrid_search(
            SlowCorpusSession(),  # type: ignore[arg-type]
            query="needle",
            query_embedding=[0.1] * 1536,
            collection_id="collection-1",
            strict=True,
            evidence=[],
        )
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


async def test_hyde_embeds_generated_document_and_hashes_provenance() -> None:
    sensitive_document = "Confidential hypothetical answer with account 12345"
    provider = _Provider([sensitive_document])
    embedder = _Embedder()

    async def runner(operation: Callable[[Any], Awaitable[Any]]) -> Any:
        return await operation(_AuthorizedSession())

    async def search(_session: object, **kwargs: Any) -> list[RetrievalResult]:
        assert kwargs["query_embedding"] == [1.0, 0.25]
        assert kwargs["retrieval_mode"] == "vector"
        return [RetrievalResult("hyde-1", "evidence", 0.8, {"source": "doc"}, ["vector"])]

    adapter = core_strategy_capabilities()[RAGStrategy.HYDE].adapter
    with patch("app.rag.engine.hybrid_search", side_effect=search):
        result = await adapter.execute(
            _request(RAGStrategy.HYDE),
            _context(
                RAGStrategy.HYDE,
                embedder=embedder,
                provider=provider,
                runner=runner,
            ),
        )

    assert embedder.texts == [sensitive_document]
    assert embedder.input_types == ["document"]
    assert provider.requests[0].model == "tenant-rag-model"
    detail = result.strategy_trace[0].detail
    assert len(detail["generated_text_sha256"]) == 64
    assert sensitive_document not in str(result.model_dump())


async def test_multi_hop_embeds_each_decomposition_and_dedupes_with_provenance() -> None:
    provider = _Provider(['["policy duration", "policy exceptions"]'])
    embedder = _Embedder()
    sessions: list[object] = []

    async def runner(operation: Callable[[Any], Awaitable[Any]]) -> Any:
        session = _AuthorizedSession()
        sessions.append(session)
        return await operation(session)

    async def search(_session: object, **kwargs: Any) -> list[RetrievalResult]:
        chunk_id = {
            "policy duration": "duration-chunk",
            "policy exceptions": "exceptions-chunk",
        }[kwargs["query"]]
        return [
            RetrievalResult(
                chunk_id,
                f"evidence for {kwargs['query']}",
                0.7,
                {"source": "policy.pdf"},
                ["vector"],
            ),
            RetrievalResult(
                "shared-chunk",
                "evidence shared by both hops",
                0.6,
                {"source": "shared.pdf"},
                ["vector"],
            ),
        ]

    adapter = core_strategy_capabilities()[RAGStrategy.MULTI_HOP].adapter
    with patch("app.rag.engine.hybrid_search", side_effect=search):
        result = await adapter.execute(
            _request(RAGStrategy.MULTI_HOP),
            _context(
                RAGStrategy.MULTI_HOP,
                embedder=embedder,
                provider=provider,
                runner=runner,
            ),
        )

    assert embedder.texts == ["policy duration", "policy exceptions"]
    assert embedder.input_types == ["query", "query"]
    assert len(sessions) == 2
    assert len({id(session) for session in sessions}) == 2
    assert [citation.chunk_id for citation in result.citations] == [
        "duration-chunk",
        "exceptions-chunk",
        "shared-chunk",
    ]
    assert [citation.metadata["hop_queries"] for citation in result.citations] == [
        ["policy duration"],
        ["policy exceptions"],
        ["policy duration", "policy exceptions"],
    ]
    assert [leg.query for leg in result.retrieval_legs] == [
        "policy duration",
        "policy exceptions",
    ]
    assert provider.requests[0].model == "tenant-rag-model"


def test_only_core_strategies_are_certified_implemented() -> None:
    expected = {
        RAGStrategy.NAIVE,
        RAGStrategy.HYBRID,
        RAGStrategy.HYDE,
        RAGStrategy.MULTI_HOP,
        RAGStrategy.FUSION,
    }
    registry = build_default_registry()
    implemented = {
        RAGStrategy(capability.strategy_id)
        for capability in registry.list_by_category(StrategyCategory.RAG)
        if capability.state is StrategyState.IMPLEMENTED
    }

    assert set(RAG_RUNTIME_CAPABILITIES) == expected
    assert set(core_strategy_capabilities()) == expected
    assert implemented == expected


class _CollectionStore:
    def get_collection(self, collection_id: str, *, tenant_ctx: TenantContext) -> object:
        return object()


class _ProbeTransaction:
    async def __aenter__(self) -> _ProbeTransaction:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class _ProbeSession:
    def begin(self) -> _ProbeTransaction:
        return _ProbeTransaction()

    async def execute(self, statement: object, params: object = None) -> object:
        return statement

    async def scalar(self, statement: object) -> object:
        return True if "to_regclass" in str(statement) else 1


@asynccontextmanager
async def _session_factory() -> Any:
    yield _ProbeSession()


async def test_readiness_reflects_core_dependencies() -> None:
    tenant = TenantContext(
        tenant_id="tenant-1", api_key_id="key-1", plan="enterprise"
    )
    provider = _Provider(["unused"])
    dependencies = RetrievalDependencies(
        session_factory=_session_factory,  # type: ignore[arg-type]
        collection_authorizer=KnowledgeStoreCollectionAuthorizer(_CollectionStore()),
        strategy_capabilities=core_strategy_capabilities(),
        embedder=_Embedder(),
        llm_resolver=lambda *_: ResolvedLLM(provider=provider, model="model"),
    )
    gateway = RetrievalGateway(dependencies)

    ready = {
        strategy: await gateway.readiness(tenant, strategy_id=strategy)
        for strategy in RAG_RUNTIME_CAPABILITIES
    }
    assert all(status.available for status in ready.values())

    unavailable = RetrievalGateway(
        RetrievalDependencies(
            session_factory=_session_factory,  # type: ignore[arg-type]
            collection_authorizer=dependencies.collection_authorizer,
            strategy_capabilities=core_strategy_capabilities(),
        )
    )
    statuses = {
        strategy: await unavailable.readiness(tenant, strategy_id=strategy)
        for strategy in RAG_RUNTIME_CAPABILITIES
    }
    assert all(not status.available for status in statuses.values())


@pytest.mark.parametrize(
    ("strategy", "embedder", "resolver", "reason"),
    [
        (RAGStrategy.NAIVE, object(), None, "embedder_unavailable"),
        (
            RAGStrategy.HYBRID,
            SimpleNamespace(embed=lambda _: None),
            None,
            "embedder_unavailable",
        ),
        (
            RAGStrategy.HYDE,
            _Embedder(),
            lambda *_: ResolvedLLM(provider=object(), model="model"),
            "llm_provider_unavailable",
        ),
        (
            RAGStrategy.FUSION,
            _Embedder(),
            lambda *_: ResolvedLLM(
                provider=SimpleNamespace(complete=lambda _: None), model="model"
            ),
            "llm_provider_unavailable",
        ),
        (
            RAGStrategy.MULTI_HOP,
            _Embedder(),
            lambda *_: ResolvedLLM(provider=_Provider(["unused"]), model=" "),
            "llm_provider_unavailable",
        ),
    ],
)
async def test_readiness_rejects_malformed_core_dependencies(
    strategy: RAGStrategy,
    embedder: object,
    resolver: Any,
    reason: str,
) -> None:
    gateway = RetrievalGateway(
        RetrievalDependencies(
            session_factory=_session_factory,  # type: ignore[arg-type]
            collection_authorizer=KnowledgeStoreCollectionAuthorizer(_CollectionStore()),
            strategy_capabilities=core_strategy_capabilities(),
            embedder=embedder,
            llm_resolver=resolver,
        )
    )

    readiness = await gateway.readiness(
        TenantContext(tenant_id="tenant-1", api_key_id="key-1", plan="enterprise"),
        strategy_id=strategy,
    )

    assert not readiness.available
    assert readiness.reason == reason


async def test_readiness_rejects_non_callable_session_factory() -> None:
    dependencies = RetrievalDependencies(
        session_factory=object(),  # type: ignore[arg-type]
        collection_authorizer=KnowledgeStoreCollectionAuthorizer(_CollectionStore()),
        strategy_capabilities=core_strategy_capabilities(),
        embedder=_Embedder(),
    )
    gateway = RetrievalGateway(dependencies)

    readiness = await gateway.readiness(
        TenantContext(tenant_id="tenant-1", api_key_id="key-1", plan="enterprise"),
        strategy_id=RAGStrategy.NAIVE,
    )

    assert not readiness.available
    assert readiness.reason == "session_factory_unavailable"


async def test_readiness_rejects_callable_without_async_session_context() -> None:
    gateway = RetrievalGateway(
        RetrievalDependencies(
            session_factory=lambda: object(),  # type: ignore[arg-type]
            collection_authorizer=KnowledgeStoreCollectionAuthorizer(_CollectionStore()),
            strategy_capabilities=core_strategy_capabilities(),
            embedder=_Embedder(),
        )
    )

    readiness = await gateway.readiness(
        TenantContext(tenant_id="tenant-1", api_key_id="key-1", plan="enterprise"),
        strategy_id=RAGStrategy.NAIVE,
    )

    assert not readiness.available
    assert readiness.reason == "session_factory_unavailable"


async def test_readiness_rejects_async_function_instead_of_context_factory() -> None:
    async def invalid_factory() -> object:
        return object()

    gateway = RetrievalGateway(
        RetrievalDependencies(
            session_factory=invalid_factory,  # type: ignore[arg-type]
            collection_authorizer=KnowledgeStoreCollectionAuthorizer(_CollectionStore()),
            strategy_capabilities=core_strategy_capabilities(),
            embedder=_Embedder(),
        )
    )

    readiness = await gateway.readiness(
        TenantContext(tenant_id="tenant-1", api_key_id="key-1", plan="enterprise"),
        strategy_id=RAGStrategy.NAIVE,
    )

    assert not readiness.available
    assert readiness.reason == "session_factory_unavailable"


async def test_readiness_rejects_invalid_object_yielded_by_async_context() -> None:
    @asynccontextmanager
    async def invalid_factory() -> Any:
        yield object()

    gateway = RetrievalGateway(
        RetrievalDependencies(
            session_factory=invalid_factory,
            collection_authorizer=KnowledgeStoreCollectionAuthorizer(_CollectionStore()),
            strategy_capabilities=core_strategy_capabilities(),
            embedder=_Embedder(),
        )
    )

    readiness = await gateway.readiness(
        TenantContext(tenant_id="tenant-1", api_key_id="key-1", plan="enterprise"),
        strategy_id=RAGStrategy.NAIVE,
    )

    assert not readiness.available
    assert readiness.reason == "session_factory_unavailable"


async def test_readiness_rejects_missing_persisted_schema_with_safe_probe() -> None:
    scalar_sql: list[str] = []

    class Session(_ProbeSession):
        async def scalar(self, statement: object) -> object:
            scalar_sql.append(str(statement))
            return False if "to_regclass" in str(statement) else 1

    @asynccontextmanager
    async def factory() -> Any:
        yield Session()

    gateway = RetrievalGateway(
        RetrievalDependencies(
            session_factory=factory,
            collection_authorizer=KnowledgeStoreCollectionAuthorizer(_CollectionStore()),
            strategy_capabilities=core_strategy_capabilities(),
            embedder=_Embedder(),
        )
    )
    readiness = await gateway.readiness(
        TenantContext(tenant_id="tenant-1", api_key_id="key-1", plan="enterprise"),
        strategy_id=RAGStrategy.NAIVE,
    )

    assert not readiness.available
    assert readiness.reason == "persistence_unavailable"
    assert any("SELECT 1" in sql for sql in scalar_sql)
    capability_sql = next(sql for sql in scalar_sql if "to_regclass" in sql)
    assert "pg_extension" in capability_sql
    assert "pg_trgm" in capability_sql
    assert "information_schema.columns" in capability_sql
    assert "pg_indexes" in capability_sql
    for column in (
        "embedding_dim",
        "expires_at",
        "metadata",
        "parent_chunk_id",
        "chunk_level",
        "window_id",
        "hierarchy_level",
        "is_proposition",
        "strategy_metadata",
        "ingestion_job_id",
    ):
        assert column in capability_sql


async def test_readiness_sanitizes_disconnected_probe() -> None:
    class DisconnectedContext:
        async def __aenter__(self) -> object:
            raise OSError("postgresql://user:secret@private-host")

        async def __aexit__(self, *_: object) -> None:
            return None

    gateway = RetrievalGateway(
        RetrievalDependencies(
            session_factory=lambda: DisconnectedContext(),  # type: ignore[arg-type]
            collection_authorizer=KnowledgeStoreCollectionAuthorizer(_CollectionStore()),
            strategy_capabilities=core_strategy_capabilities(),
            embedder=_Embedder(),
        )
    )
    readiness = await gateway.readiness(
        TenantContext(tenant_id="tenant-1", api_key_id="key-1", plan="enterprise"),
        strategy_id=RAGStrategy.NAIVE,
    )

    assert not readiness.available
    assert readiness.reason == "persistence_unavailable"
    assert "private-host" not in readiness.reason


async def test_api_discovery_exposes_exactly_ready_core_capabilities() -> None:
    tenant = TenantContext(
        tenant_id="tenant-1", api_key_id="key-1", plan="enterprise"
    )
    provider = _Provider(["unused"])
    configured = RetrievalGateway(
        RetrievalDependencies(
            session_factory=_session_factory,  # type: ignore[arg-type]
            collection_authorizer=KnowledgeStoreCollectionAuthorizer(_CollectionStore()),
            strategy_capabilities=core_strategy_capabilities(),
            embedder=_Embedder(),
            llm_resolver=lambda *_: ResolvedLLM(provider=provider, model="model"),
        )
    )
    configured_request = SimpleNamespace(
        state=SimpleNamespace(tenant=tenant),
        app=SimpleNamespace(state=SimpleNamespace(retrieval_gateway=configured)),
    )

    payload = await list_strategies(configured_request)  # type: ignore[arg-type]
    available = {item["id"] for item in payload["strategies"] if item["available"]}
    assert available == {strategy.value for strategy in RAG_RUNTIME_CAPABILITIES}

    unavailable = RetrievalGateway(
        RetrievalDependencies(
            session_factory=None,
            collection_authorizer=configured.dependencies.collection_authorizer,
            strategy_capabilities=core_strategy_capabilities(),
        )
    )
    unavailable_request = SimpleNamespace(
        state=SimpleNamespace(tenant=tenant),
        app=SimpleNamespace(state=SimpleNamespace(retrieval_gateway=unavailable)),
    )
    payload = await list_strategies(unavailable_request)  # type: ignore[arg-type]
    assert not any(item["available"] for item in payload["strategies"])
