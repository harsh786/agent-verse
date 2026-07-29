"""Tenant-aware public gateway for canonical RAG strategy execution."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import sqlalchemy_rls_context
from app.rag import engine as rag_engine
from app.rag.contracts import (
    FusionRAGRuntimeAdapter,
    HybridRAGRuntimeAdapter,
    HyDERAGRuntimeAdapter,
    MultiHopRAGRuntimeAdapter,
    NaiveRAGRuntimeAdapter,
    RAGCitation,
    RAGExecutionRequest,
    RAGExecutionResult,
    RAGRetrievalLeg,
    RAGStrategy,
    RAGStrategyTrace,
    UnavailableRAGStrategyError,
    resolve_rag_strategy,
)
from app.rag.engine import (
    RetrievalResult as EngineRetrievalResult,
)
from app.rag.engine import (
    RetrievalStrategyExecutionError,
)
from app.tenancy.context import TenantContext

T = TypeVar("T")
DatabaseOperation = Callable[[AsyncSession], Awaitable[T]]


class AsyncSessionFactory(Protocol):
    """Factory returning one async context-managed SQLAlchemy session."""

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]: ...


class CollectionAuthorizer(Protocol):
    """Verify that a collection exists for the authenticated tenant."""

    async def authorize(
        self,
        session: AsyncSession | None,
        tenant_context: TenantContext,
        collection_id: str,
    ) -> bool: ...


class KnowledgeCollectionStore(Protocol):
    """Minimal in-memory collection lookup used during application assembly."""

    def get_collection(
        self,
        collection_id: str,
        *,
        tenant_ctx: TenantContext,
    ) -> object | None: ...


class TenantScopedGraphCapability(Protocol):
    """Graph persistence boundary available to one authenticated execution."""

    async def run_db_operation(self, operation: DatabaseOperation[T]) -> T: ...


class GraphCapabilityAdapter(Protocol):
    """Bind graph persistence to a tenant-scoped DB operation runner."""

    def bind(
        self,
        db_operation_runner: Callable[[DatabaseOperation[Any]], Awaitable[Any]],
    ) -> TenantScopedGraphCapability: ...


@dataclass(frozen=True, slots=True)
class _BoundTenantScopedGraphCapability:
    _db_operation_runner: Callable[[DatabaseOperation[Any]], Awaitable[Any]]

    async def run_db_operation(self, operation: DatabaseOperation[T]) -> T:
        result: T = await self._db_operation_runner(operation)
        return result


class TenantScopedGraphCapabilityAdapter:
    """Create graph capabilities without exposing a store or session factory."""

    def bind(
        self,
        db_operation_runner: Callable[[DatabaseOperation[Any]], Awaitable[Any]],
    ) -> TenantScopedGraphCapability:
        return _BoundTenantScopedGraphCapability(db_operation_runner)


@dataclass(frozen=True, slots=True)
class ResolvedLLM:
    """Provider and model selected for one tenant-scoped execution."""

    provider: object | None
    model: str
    provider_type: str = ""


LLMResolver = Callable[
    [TenantContext, RAGStrategy],
    ResolvedLLM | None | Awaitable[ResolvedLLM | None],
]


class RetrievalStrategyAdapter(Protocol):
    """Gateway-specific adapter with access to scoped retrieval dependencies."""

    async def execute(
        self,
        request: RAGExecutionRequest,
        context: RetrievalExecutionContext,
    ) -> RAGExecutionResult | Sequence[EngineRetrievalResult]: ...


@dataclass(frozen=True, slots=True)
class RetrievalStrategyCapability:
    """A concrete adapter and the capabilities it requires to execute."""

    adapter: RetrievalStrategyAdapter
    requires_embedder: bool = False
    requires_provider: bool = False
    requires_graph: bool = False
    requires_search: bool = False
    requires_database: bool = False


def core_strategy_capabilities() -> Mapping[RAGStrategy, RetrievalStrategyCapability]:
    """Return certified core strategy adapters."""

    return {
        RAGStrategy.NAIVE: RetrievalStrategyCapability(
            NaiveRAGRuntimeAdapter(),
            requires_embedder=True,
            requires_database=True,
        ),
        RAGStrategy.HYBRID: RetrievalStrategyCapability(
            HybridRAGRuntimeAdapter(),
            requires_embedder=True,
            requires_database=True,
        ),
        RAGStrategy.HYDE: RetrievalStrategyCapability(
            HyDERAGRuntimeAdapter(),
            requires_embedder=True,
            requires_provider=True,
            requires_database=True,
        ),
        RAGStrategy.MULTI_HOP: RetrievalStrategyCapability(
            MultiHopRAGRuntimeAdapter(),
            requires_embedder=True,
            requires_provider=True,
            requires_database=True,
        ),
        RAGStrategy.FUSION: RetrievalStrategyCapability(
            FusionRAGRuntimeAdapter(),
            requires_embedder=True,
            requires_provider=True,
            requires_database=True,
        ),
    }


def _has_async_method(dependency: object | None, method_name: str) -> bool:
    method = getattr(dependency, method_name, None)
    return callable(method) and inspect.iscoroutinefunction(method)


def _has_async_context_factory(factory: object | None) -> bool:
    if not callable(factory):
        return False
    try:
        context = factory()
    except Exception:
        return False
    if inspect.iscoroutine(context):
        context.close()
        return False
    return _has_async_method(context, "__aenter__") and _has_async_method(
        context, "__aexit__"
    )


async def _probe_session_factory(factory: object | None) -> bool:
    if not callable(factory):
        return False
    try:
        context = factory()
        if inspect.iscoroutine(context):
            context.close()
            return False
        if not (
            _has_async_method(context, "__aenter__")
            and _has_async_method(context, "__aexit__")
        ):
            return False
        async with context as session:
            begin = getattr(session, "begin", None)
            if not callable(begin):
                return False
            transaction = begin()
            return (
                _has_async_method(transaction, "__aenter__")
                and _has_async_method(transaction, "__aexit__")
                and _has_async_method(session, "execute")
                and _has_async_method(session, "scalar")
            )
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class RAGStrategyReadiness:
    """Sanitized tenant-specific strategy readiness without retrieval execution."""

    strategy: RAGStrategy
    available: bool
    reason: str


@dataclass(frozen=True, slots=True)
class RetrievalDependencies:
    """Typed process dependencies used by the tenant-aware retrieval gateway."""

    session_factory: AsyncSessionFactory | None
    collection_authorizer: CollectionAuthorizer
    strategy_capabilities: Mapping[RAGStrategy, RetrievalStrategyCapability]
    embedder: object | None = None
    llm_resolver: LLMResolver | None = None
    graph_capability: GraphCapabilityAdapter | None = None
    search_capability: object | None = None
    policy_services: tuple[object, ...] = ()


@dataclass(frozen=True, slots=True)
class RetrievalRuntimeDependencies:
    """Non-authoritative capabilities safe to expose to a strategy adapter."""

    embedder: object | None
    llm: ResolvedLLM | None
    graph_capability: TenantScopedGraphCapability | None
    search_capability: object | None
    policy_services: tuple[object, ...]


class CollectionNotFoundError(LookupError):
    """Collection is absent or is not owned by the authenticated tenant."""

    def __init__(self, collection_id: str) -> None:
        super().__init__(f"Knowledge collection not found: {collection_id}")
        self.collection_id = collection_id


class SQLCollectionAuthorizer:
    """Authorize collection access against tenant-filtered persisted state."""

    async def authorize(
        self,
        session: AsyncSession | None,
        tenant_context: TenantContext,
        collection_id: str,
    ) -> bool:
        if session is None:
            return False
        result = await session.execute(
            text(
                "SELECT collection.id FROM knowledge_collections AS collection "
                "JOIN tenants AS tenant ON tenant.id = collection.tenant_id "
                "WHERE collection.id = :collection_id "
                "AND collection.tenant_id = :tenant_id "
                "AND tenant.is_active IS TRUE LIMIT 1"
            ),
            {
                "collection_id": collection_id,
                "tenant_id": tenant_context.tenant_id,
            },
        )
        return result.scalar_one_or_none() is not None


class KnowledgeStoreCollectionAuthorizer:
    """Authorize collections in the non-persistent application phase."""

    def __init__(self, knowledge_store: KnowledgeCollectionStore) -> None:
        self._knowledge_store = knowledge_store

    async def authorize(
        self,
        session: AsyncSession | None,
        tenant_context: TenantContext,
        collection_id: str,
    ) -> bool:
        del session
        return (
            self._knowledge_store.get_collection(
                collection_id,
                tenant_ctx=tenant_context,
            )
            is not None
        )


@dataclass(frozen=True, slots=True)
class _TenantSessionRunner:
    session_factory: AsyncSessionFactory
    tenant_context: TenantContext

    async def run(self, operation: DatabaseOperation[T]) -> T:
        async with (
            self.session_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, self.tenant_context.tenant_id),
        ):
            return await operation(session)


@dataclass(frozen=True, slots=True)
class RetrievalExecutionContext:
    """Dependencies scoped to one authenticated retrieval execution."""

    tenant_context: TenantContext
    strategy: RAGStrategy
    filters: dict[str, Any]
    dependencies: RetrievalRuntimeDependencies
    _db_operation_runner: Callable[[DatabaseOperation[Any]], Awaitable[Any]] | None

    @property
    def llm(self) -> ResolvedLLM | None:
        return self.dependencies.llm

    async def run_db_operation(self, operation: DatabaseOperation[T]) -> T:
        """Run one DB operation in its own session, transaction, and RLS scope."""

        if self._db_operation_runner is None:
            raise RuntimeError("A database session factory is not configured")
        result: T = await self._db_operation_runner(operation)
        return result

    async def retrieve_engine(
        self,
        *,
        query: str,
        query_embedding: list[float] | None,
        collection_id: str,
        top_k: int,
    ) -> list[EngineRetrievalResult]:
        """Execute the legacy engine core with canonical fail-closed semantics."""

        if self.strategy is RAGStrategy.FUSION:
            async def search_operation(
                variant_query: str,
                variant_embedding: list[float] | None,
            ) -> list[EngineRetrievalResult]:
                async def search(session: AsyncSession) -> list[EngineRetrievalResult]:
                    return await rag_engine.hybrid_search(
                        session,
                        query=variant_query,
                        query_embedding=variant_embedding,
                        collection_id=collection_id,
                        top_k=top_k,
                        metadata_filter=self.filters,
                        strict=True,
                    )

                return await self.run_db_operation(search)

            return await rag_engine.retrieve_fusion(
                None,
                query=query,
                query_embedding=query_embedding,
                collection_id=collection_id,
                top_k=top_k,
                embedder=self.dependencies.embedder,
                provider=self.llm.provider if self.llm is not None else None,
                model=self.llm.model if self.llm is not None else "",
                metadata_filter=self.filters,
                strict=True,
                search_operation=search_operation,
            )

        async def operation(session: AsyncSession) -> list[EngineRetrievalResult]:
            return await rag_engine.retrieve(
                session,
                query=query,
                query_embedding=query_embedding,
                collection_id=collection_id,
                top_k=top_k,
                strategy=self.strategy.value,
                provider=self.llm.provider if self.llm is not None else None,
                model=self.llm.model if self.llm is not None else "",
                metadata_filter=self.filters,
                embedder=self.dependencies.embedder,
                tenant_ctx=self.tenant_context,
                strict=True,
            )

        return await self.run_db_operation(operation)


async def execute_core_strategy(
    strategy: RAGStrategy,
    request: RAGExecutionRequest,
    context: RetrievalExecutionContext,
) -> RAGExecutionResult:
    """Execute one certified strategy through tenant-scoped persistence boundaries."""

    collection_id = request.collection_id
    if not collection_id:
        raise RetrievalStrategyExecutionError(strategy.value, "collection is required")

    if strategy is RAGStrategy.NAIVE:
        embedding = await _embed_text(context, request.query, strategy)
        results = await _search_persisted(
            context,
            request,
            query=request.query,
            embedding=embedding,
            retrieval_mode="vector",
        )
        evidence = [
            {
                "component": "vector",
                "query": request.query,
                "result_count": len(results),
                "component_scores": {
                    result.chunk_id: result.component_scores.get("vector", result.score)
                    for result in results
                },
            }
        ]
        return _canonical_result(request, strategy, results, evidence)

    if strategy is RAGStrategy.HYBRID:
        embedding = await _embed_text(context, request.query, strategy)
        evidence = []
        results = await _search_persisted(
            context,
            request,
            query=request.query,
            embedding=embedding,
            retrieval_mode="hybrid",
            evidence=evidence,
        )
        return _canonical_result(request, strategy, results, evidence, rrf=True)

    llm = context.llm
    if llm is None or llm.provider is None or not llm.model:
        raise RetrievalStrategyExecutionError(strategy.value, "resolved LLM is required")

    if strategy is RAGStrategy.HYDE:
        generated_evidence: dict[str, Any] = {}

        async def operation(session: AsyncSession) -> list[EngineRetrievalResult]:
            return await rag_engine.retrieve_hyde(
                session,
                query=request.query,
                query_embedding=None,
                collection_id=collection_id,
                provider=llm.provider,
                model=llm.model,
                top_k=request.top_k,
                metadata_filter=request.filters,
                embedder=context.dependencies.embedder,
                strict=True,
                strategy_evidence=generated_evidence,
            )

        results = await context.run_db_operation(operation)
        generated_evidence["provider_type"] = llm.provider_type
        evidence = [
            {
                "component": "vector",
                "query": request.query,
                "result_count": len(results),
                "component_scores": {
                    result.chunk_id: result.component_scores.get("vector", result.score)
                    for result in results
                },
            }
        ]
        return _canonical_result(
            request,
            strategy,
            results,
            evidence,
            initial_trace=("hypothetical_document", generated_evidence),
        )

    strategy_evidence: list[dict[str, Any]] = []

    async def search_operation(
        variant_query: str,
        variant_embedding: list[float] | None,
    ) -> list[EngineRetrievalResult]:
        return await _search_persisted(
            context,
            request,
            query=variant_query,
            embedding=variant_embedding,
            retrieval_mode="hybrid",
        )

    if strategy is RAGStrategy.MULTI_HOP:
        results = await rag_engine.retrieve_multi_hop(
            None,
            query=request.query,
            query_embedding=None,
            collection_id=collection_id,
            provider=llm.provider,
            model=llm.model,
            top_k=request.top_k,
            metadata_filter=request.filters,
            embedder=context.dependencies.embedder,
            strict=True,
            search_operation=search_operation,
            strategy_evidence=strategy_evidence,
        )
        return _canonical_result(
            request,
            strategy,
            results,
            strategy_evidence,
            initial_trace=(
                "query_decomposition",
                {
                    "model": llm.model,
                    "provider_type": llm.provider_type,
                    "hop_count": len(strategy_evidence),
                },
            ),
        )

    if strategy is RAGStrategy.FUSION:
        results = await rag_engine.retrieve_fusion(
            None,
            query=request.query,
            query_embedding=None,
            collection_id=collection_id,
            provider=llm.provider,
            model=llm.model,
            top_k=request.top_k,
            embedder=context.dependencies.embedder,
            metadata_filter=request.filters,
            strict=True,
            search_operation=search_operation,
            strategy_evidence=strategy_evidence,
        )
        return _canonical_result(
            request,
            strategy,
            results,
            strategy_evidence,
            rrf=True,
            initial_trace=(
                "query_expansion",
                {
                    "model": llm.model,
                    "provider_type": llm.provider_type,
                    "variant_count": len(strategy_evidence),
                },
            ),
        )

    raise RetrievalStrategyExecutionError(strategy.value, "adapter is not certified")


async def _embed_text(
    context: RetrievalExecutionContext,
    text_value: str,
    strategy: RAGStrategy,
) -> list[float]:
    from app.providers.base import EmbedRequest

    embedder: Any = context.dependencies.embedder
    if embedder is None or not callable(getattr(embedder, "embed", None)):
        raise RetrievalStrategyExecutionError(
            strategy.value, "embedding provider is required"
        )
    try:
        response = await embedder.embed(EmbedRequest(texts=[text_value], input_type="query"))
        embedding = response.embeddings[0] if response.embeddings else None
    except Exception as exc:
        raise RetrievalStrategyExecutionError(strategy.value, "embedding failed") from exc
    if not embedding:
        raise RetrievalStrategyExecutionError(strategy.value, "embedding response was empty")
    return list(embedding)


async def _search_persisted(
    context: RetrievalExecutionContext,
    request: RAGExecutionRequest,
    *,
    query: str,
    embedding: list[float] | None,
    retrieval_mode: str,
    evidence: list[dict[str, Any]] | None = None,
) -> list[EngineRetrievalResult]:
    async def operation(session: AsyncSession) -> list[EngineRetrievalResult]:
        return await rag_engine.hybrid_search(
            session,
            query=query,
            query_embedding=embedding,
            collection_id=request.collection_id or "",
            top_k=request.top_k,
            retrieval_mode=retrieval_mode,
            metadata_filter=request.filters,
            strict=True,
            evidence=evidence,
        )

    return await context.run_db_operation(operation)


def _canonical_result(
    request: RAGExecutionRequest,
    strategy: RAGStrategy,
    results: list[EngineRetrievalResult],
    evidence: list[dict[str, Any]],
    *,
    rrf: bool = False,
    initial_trace: tuple[str, dict[str, Any]] | None = None,
) -> RAGExecutionResult:
    citations = [
        RAGCitation(
            citation_id=f"citation-{index}",
            chunk_id=result.chunk_id,
            content=result.content,
            score=result.score,
            source=str(
                result.source_metadata.get("source")
                or result.source_metadata.get("source_url")
                or result.source_metadata.get("source_doc_id")
                or request.collection_id
                or "unknown"
            ),
            metadata={
                **result.source_metadata,
                "component_scores": dict(result.component_scores),
                "rrf_score": result.rrf_score,
            },
        )
        for index, result in enumerate(results, start=1)
    ]
    retrieval_legs = [
        RAGRetrievalLeg(
            strategy=strategy,
            query=str(item.get("query") or request.query),
            result_count=int(item.get("result_count", 0)),
            score=max(
                (float(score) for score in item.get("component_scores", {}).values()),
                default=0.0,
            ),
            metadata=dict(item),
        )
        for item in evidence
    ]
    trace: list[RAGStrategyTrace] = []
    if initial_trace is not None:
        trace.append(
            RAGStrategyTrace(
                strategy=strategy,
                action=initial_trace[0],
                status="complete",
                detail=initial_trace[1],
            )
        )
    trace.extend(
        RAGStrategyTrace(
            strategy=strategy,
            action="retrieval_leg",
            status="complete",
            detail=dict(item),
        )
        for item in evidence
    )
    if rrf:
        trace.append(
            RAGStrategyTrace(
                strategy=strategy,
                action="rrf_merge",
                status="complete",
                detail={
                    "rrf_scores": {
                        result.chunk_id: result.rrf_score for result in results
                    }
                },
            )
        )
    return RAGExecutionResult(
        requested_strategy_id=request.requested_strategy_id,
        resolved_strategy_id=strategy,
        citations=citations,
        retrieval_legs=retrieval_legs,
        strategy_trace=trace,
        grounded=bool(citations),
    )


class RetrievalGateway:
    """Authenticate, authorize, and dispatch one canonical RAG execution."""

    def __init__(self, dependencies: RetrievalDependencies) -> None:
        self.dependencies = dependencies

    async def readiness(
        self,
        tenant_context: TenantContext,
        *,
        strategy_id: str | RAGStrategy,
        collection_id: str | None = None,
    ) -> RAGStrategyReadiness:
        """Validate adapter and tenant capabilities without retrieving evidence."""

        if not isinstance(tenant_context, TenantContext):
            raise TypeError("tenant_context must be a TenantContext")
        strategy = resolve_rag_strategy(strategy_id)
        capability = self.dependencies.strategy_capabilities.get(strategy)
        if capability is None:
            return RAGStrategyReadiness(strategy, False, "adapter_not_registered")
        execute = getattr(capability.adapter, "execute", None)
        if not inspect.iscoroutinefunction(execute) or inspect.isabstract(capability.adapter):
            return RAGStrategyReadiness(strategy, False, "invalid_adapter")
        if capability.requires_embedder and not _has_async_method(
            self.dependencies.embedder, "embed"
        ):
            return RAGStrategyReadiness(strategy, False, "embedder_unavailable")
        if capability.requires_database and not await _probe_session_factory(
            self.dependencies.session_factory
        ):
            return RAGStrategyReadiness(strategy, False, "session_factory_unavailable")
        if capability.requires_graph and (
            self.dependencies.graph_capability is None
            or self.dependencies.session_factory is None
        ):
            return RAGStrategyReadiness(strategy, False, "graph_capability_unavailable")
        if capability.requires_search and self.dependencies.search_capability is None:
            return RAGStrategyReadiness(strategy, False, "search_capability_unavailable")
        if (
            isinstance(self.dependencies.collection_authorizer, SQLCollectionAuthorizer)
            and self.dependencies.session_factory is None
        ):
            return RAGStrategyReadiness(strategy, False, "session_factory_unavailable")
        if capability.requires_provider:
            try:
                await self._resolve_llm(strategy, capability, tenant_context)
            except UnavailableRAGStrategyError:
                return RAGStrategyReadiness(strategy, False, "llm_provider_unavailable")
        if collection_id is not None:
            try:
                await self._authorize_collection(
                    self._session_runner(tenant_context),
                    tenant_context,
                    collection_id,
                )
            except CollectionNotFoundError:
                return RAGStrategyReadiness(strategy, False, "collection_not_authorized")
            except Exception:
                return RAGStrategyReadiness(
                    strategy,
                    False,
                    "collection_authorization_unavailable",
                )
        return RAGStrategyReadiness(strategy, True, "ready")

    async def execute(
        self,
        tenant_context: TenantContext,
        *,
        collection_id: str,
        query: str,
        strategy_id: str | RAGStrategy,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> RAGExecutionResult:
        if not isinstance(tenant_context, TenantContext):
            raise TypeError("tenant_context must be a TenantContext")

        requested_strategy_id = (
            strategy_id.value if isinstance(strategy_id, RAGStrategy) else strategy_id
        )
        strategy = resolve_rag_strategy(strategy_id)
        capability = self.dependencies.strategy_capabilities.get(strategy)
        if capability is None:
            raise UnavailableRAGStrategyError(
                strategy,
                "no runtime adapter is configured",
            )

        self._validate_capabilities(strategy, capability)
        llm = await self._resolve_llm(strategy, capability, tenant_context)
        runner = self._session_runner(tenant_context)
        await self._authorize_collection(runner, tenant_context, collection_id)

        request = RAGExecutionRequest(
            tenant_id=tenant_context.tenant_id,
            collection_id=collection_id,
            query=query,
            requested_strategy_id=requested_strategy_id,
            top_k=top_k,
            filters=filters or {},
        )
        context = RetrievalExecutionContext(
            tenant_context=tenant_context,
            strategy=strategy,
            filters=dict(request.filters),
            dependencies=RetrievalRuntimeDependencies(
                embedder=self.dependencies.embedder,
                llm=llm,
                graph_capability=(
                    self.dependencies.graph_capability.bind(runner.run)
                    if self.dependencies.graph_capability is not None and runner is not None
                    else None
                ),
                search_capability=self.dependencies.search_capability,
                policy_services=self.dependencies.policy_services,
            ),
            _db_operation_runner=runner.run if runner is not None else None,
        )
        result = await capability.adapter.execute(request, context)
        if isinstance(result, RAGExecutionResult):
            if result.resolved_strategy_id is not strategy:
                raise ValueError("RAG strategy adapter returned a mismatched strategy ID")
            return result.model_copy(
                update={
                    "requested_strategy_id": requested_strategy_id,
                    "resolved_strategy_id": strategy,
                }
            )
        if (
            isinstance(result, (list, tuple))
            and all(
            isinstance(item, EngineRetrievalResult) for item in result
            )
        ):
            return self._normalize_engine_results(request, strategy, list(result))
        raise TypeError("RAG strategy adapter returned an unsupported result type")

    @staticmethod
    def _normalize_engine_results(
        request: RAGExecutionRequest,
        strategy: RAGStrategy,
        results: list[EngineRetrievalResult],
    ) -> RAGExecutionResult:
        engine_legs = sorted({leg for result in results for leg in result.retrieval_legs})
        citations = [
            RAGCitation(
                citation_id=f"citation-{index}",
                chunk_id=result.chunk_id,
                content=result.content,
                score=result.score,
                source=str(
                    result.source_metadata.get("source")
                    or result.source_metadata.get("source_url")
                    or result.source_metadata.get("source_doc_id")
                    or request.collection_id
                    or "unknown"
                ),
                metadata=dict(result.source_metadata),
            )
            for index, result in enumerate(results, start=1)
        ]
        trace_detail: dict[str, Any] = {
            "result_count": len(results),
            "engine_legs": engine_legs,
        }
        return RAGExecutionResult(
            requested_strategy_id=request.requested_strategy_id,
            resolved_strategy_id=strategy,
            citations=citations,
            retrieval_legs=[
                RAGRetrievalLeg(
                    strategy=strategy,
                    query=request.query,
                    result_count=len(results),
                    score=max((result.score for result in results), default=0.0),
                    metadata={"engine_legs": engine_legs},
                )
            ],
            strategy_trace=[
                RAGStrategyTrace(
                    strategy=strategy,
                    action="engine_retrieval",
                    status="complete",
                    detail=trace_detail,
                )
            ],
            grounded=bool(citations),
        )

    def _session_runner(
        self,
        tenant_context: TenantContext,
    ) -> _TenantSessionRunner | None:
        if self.dependencies.session_factory is None:
            return None
        return _TenantSessionRunner(self.dependencies.session_factory, tenant_context)

    async def _authorize_collection(
        self,
        runner: _TenantSessionRunner | None,
        tenant_context: TenantContext,
        collection_id: str,
    ) -> None:
        if runner is None:
            authorized = await self.dependencies.collection_authorizer.authorize(
                None,
                tenant_context,
                collection_id,
            )
        else:
            authorized = await runner.run(
                lambda session: self.dependencies.collection_authorizer.authorize(
                    session,
                    tenant_context,
                    collection_id,
                )
            )
        if not authorized:
            raise CollectionNotFoundError(collection_id)

    def _validate_capabilities(
        self,
        strategy: RAGStrategy,
        capability: RetrievalStrategyCapability,
    ) -> None:
        if capability.requires_embedder and not _has_async_method(
            self.dependencies.embedder, "embed"
        ):
            raise UnavailableRAGStrategyError(strategy, "embedding provider is not configured")
        if capability.requires_database and not _has_async_context_factory(
            self.dependencies.session_factory
        ):
            raise UnavailableRAGStrategyError(
                strategy, "database session factory is not configured"
            )
        if capability.requires_provider and self.dependencies.llm_resolver is None:
            raise UnavailableRAGStrategyError(strategy, "LLM provider is not configured")
        if capability.requires_graph and (
            self.dependencies.graph_capability is None
            or self.dependencies.session_factory is None
        ):
            raise UnavailableRAGStrategyError(strategy, "graph capability is not configured")
        if capability.requires_search and self.dependencies.search_capability is None:
            raise UnavailableRAGStrategyError(strategy, "search capability is not configured")

    async def _resolve_llm(
        self,
        strategy: RAGStrategy,
        capability: RetrievalStrategyCapability,
        tenant_context: TenantContext,
    ) -> ResolvedLLM | None:
        resolver = self.dependencies.llm_resolver
        if resolver is None:
            return None
        try:
            resolved = resolver(tenant_context, strategy)
            if inspect.isawaitable(resolved):
                resolved = await resolved
        except Exception as exc:
            if capability.requires_provider:
                raise UnavailableRAGStrategyError(
                    strategy,
                    "LLM provider resolution failed",
                ) from exc
            return None
        if capability.requires_provider and resolved is None:
            raise UnavailableRAGStrategyError(strategy, "LLM provider is not configured")
        if resolved is not None:
            if not _has_async_method(resolved.provider, "complete"):
                raise UnavailableRAGStrategyError(strategy, "LLM provider is not configured")
            if not isinstance(resolved.model, str) or not resolved.model.strip():
                raise UnavailableRAGStrategyError(strategy, "LLM model is not configured")
            return ResolvedLLM(
                provider=resolved.provider,
                model=resolved.model.strip(),
                provider_type=resolved.provider_type,
            )
        return resolved
