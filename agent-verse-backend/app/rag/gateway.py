"""Tenant-aware public gateway for canonical RAG strategy execution."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, TypeGuard, TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import sqlalchemy_rls_context
from app.observability.logging import get_logger
from app.rag import engine as rag_engine
from app.rag.catalogue import (
    RAG_CAPABILITY_CATALOGUE,
    RAGAdapterConfiguration,
    RAGRuntimeDependency,
    ReadinessContext,
    ReadinessFact,
)
from app.rag.contracts import (
    DIRECT_CORE_RAG_STRATEGIES,
    AgenticRAGRuntimeAdapter,
    FLARERAGRuntimeAdapter,
    RAGCitation,
    RAGExecutionRequest,
    RAGExecutionResult,
    RAGRetrievalLeg,
    RAGRuntimeAdapter,
    RAGStrategy,
    RAGStrategyTrace,
    SelfRAGRuntimeAdapter,
    SpeculativeRAGRuntimeAdapter,
    UnavailableRAGStrategyError,
    resolve_rag_strategy,
)
from app.rag.engine import (
    RetrievalResult as EngineRetrievalResult,
)
from app.rag.engine import (
    RetrievalStrategyExecutionError,
)
from app.rag.raft import RAFTService
from app.rag_platform.reranker_contract import AsyncCloseableProtocol
from app.tenancy.context import TenantContext

if TYPE_CHECKING:
    from app.rag.agentic.patterns.graph import GraphEvidence, GraphEvidenceQuery
    from app.rag.agentic.patterns.web_augmented import SafeWebSearchCapability
    from app.rag.modular import ModularPipelineSpec

T = TypeVar("T")
DatabaseOperation = Callable[[AsyncSession], Awaitable[T]]
logger = get_logger(__name__)

# Adaptive auto-selection (D-6) can now choose a non-core reasoning strategy.
# The adaptive leg dispatches those through their canonical contract adapters,
# which lazily resolve the concrete reasoning implementation.
_REASONING_ADAPTER_FACTORIES: Mapping[RAGStrategy, Callable[[], RAGRuntimeAdapter]] = (
    MappingProxyType(
        {
            RAGStrategy.SELF_RAG: SelfRAGRuntimeAdapter,
            RAGStrategy.SPECULATIVE: SpeculativeRAGRuntimeAdapter,
            RAGStrategy.FLARE: FLARERAGRuntimeAdapter,
            RAGStrategy.AGENTIC: AgenticRAGRuntimeAdapter,
        }
    )
)


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


class RAGCostController(Protocol):
    async def check_and_record(
        self,
        *,
        goal_id: str,
        cost_usd: float,
        tenant_ctx: TenantContext,
        tool_name: str = "",
        attempt_id: str = "",
    ) -> bool: ...


@dataclass(slots=True)
class _RAGCostEvent:
    operation: str
    reserved_usd: float
    actual_tokens: int = 0


class _RAGCostGuard:
    _ESTIMATED_COSTS: ClassVar[dict[str, float]] = {
        "embedding": 0.0001,
        "completion": 0.001,
        "web_retrieval": 0.001,
        "synthesis": 0.001,
        "citation_verification": 0.001,
    }

    def __init__(
        self,
        controller: RAGCostController | None,
        *,
        execution_id: str,
        invocation_id: str,
        tenant_context: TenantContext,
        strategy: RAGStrategy,
    ) -> None:
        self._controller = controller
        self._execution_id = execution_id
        self._invocation_id = invocation_id
        self._tenant_context = tenant_context
        self._strategy = strategy
        self._sequence = 0
        self.events: list[_RAGCostEvent] = []
        self._reservations: dict[str, int] = {}

    async def reserve(
        self,
        operation: str,
        *,
        reservation_key: str = "",
    ) -> int | None:
        if self._controller is None:
            return None
        if reservation_key and reservation_key in self._reservations:
            return self._reservations[reservation_key]
        self._sequence += 1
        estimated_cost = self._ESTIMATED_COSTS[operation]
        operation_key = reservation_key or f"{operation}:{self._sequence}"
        operation_hash = hashlib.sha256(operation_key.encode()).hexdigest()[:16]
        allowed = await self._controller.check_and_record(
            goal_id=self._execution_id,
            cost_usd=estimated_cost,
            tenant_ctx=self._tenant_context,
            tool_name=f"rag_{operation}",
            attempt_id=f"rag:{self._invocation_id}:{operation_hash}",
        )
        if not allowed:
            raise RetrievalStrategyExecutionError(
                self._strategy.value,
                f"budget_exhausted:{operation}",
            )
        self.events.append(_RAGCostEvent(operation, estimated_cost))
        event_index = len(self.events) - 1
        if reservation_key:
            self._reservations[reservation_key] = event_index
        return event_index

    def record_tokens(self, event_index: int | None, tokens: int) -> None:
        if event_index is not None and tokens > 0:
            self.events[event_index].actual_tokens = tokens

    @property
    def event_count(self) -> int:
        return len(self.events)

    def wrap_provider(self, provider: Any, operation: str) -> _BudgetedProvider:
        return _BudgetedProvider(provider, self, operation=operation)

    def traces(self, start: int = 0) -> list[RAGStrategyTrace]:
        execution_hash = hashlib.sha256(self._execution_id.encode("utf-8")).hexdigest()[:16]
        invocation_hash = hashlib.sha256(self._invocation_id.encode()).hexdigest()[:16]
        return [
            RAGStrategyTrace(
                strategy=self._strategy,
                action="rag_cost",
                status="complete",
                detail={
                    "operation": event.operation,
                    "reserved_usd": round(event.reserved_usd, 6),
                    "actual_tokens": event.actual_tokens,
                    "execution_id": f"sha256:{execution_hash}",
                    "invocation_id": f"sha256:{invocation_hash}",
                },
            )
            for event in self.events[start:]
        ]


class _BudgetedEmbedder:
    def __init__(self, embedder: Any, guard: _RAGCostGuard) -> None:
        self._embedder = embedder
        self._guard = guard

    async def embed(self, request: Any) -> Any:
        event_index = await self._guard.reserve("embedding")
        response = await self._embedder.embed(request)
        self._guard.record_tokens(event_index, int(getattr(response, "total_tokens", 0)))
        return response


class _BudgetedProvider:
    def __init__(
        self,
        provider: Any,
        guard: _RAGCostGuard,
        *,
        operation: str = "completion",
    ) -> None:
        self._provider = provider
        self._guard = guard
        self._operation = operation

    async def complete(self, request: Any) -> Any:
        event_index = await self._guard.reserve(self._operation)
        response = await self._provider.complete(request)
        self._guard.record_tokens(event_index, int(getattr(response, "total_tokens", 0)))
        return response


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

    async def retrieve_evidence(
        self,
        request: GraphEvidenceQuery,
    ) -> list[GraphEvidence]: ...


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

    async def retrieve_evidence(
        self,
        request: GraphEvidenceQuery,
    ) -> list[GraphEvidence]:
        from app.rag.agentic.patterns.graph import GraphEvidenceQuery, query_graph_evidence

        if not isinstance(request, GraphEvidenceQuery):
            raise TypeError("Graph evidence request has an invalid type")

        async def operation(session: AsyncSession) -> list[GraphEvidence]:
            return await query_graph_evidence(session, request)

        return await self.run_db_operation(operation)


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
    requires_web_policy: bool = False
    requires_raft_service: bool = False
    requires_raft_model: bool = False


def core_strategy_capabilities(
    configuration: RAGAdapterConfiguration | None = None,
) -> Mapping[RAGStrategy, RetrievalStrategyCapability]:
    """Return certified core strategy adapters."""
    capabilities: dict[RAGStrategy, RetrievalStrategyCapability] = {}
    for strategy, entry in RAG_CAPABILITY_CATALOGUE.items():
        required = set(entry.required_dependencies)
        capabilities[strategy] = RetrievalStrategyCapability(
            adapter=entry.create_adapter(configuration),
            requires_embedder=RAGRuntimeDependency.EMBEDDER in required,
            requires_provider=RAGRuntimeDependency.PROVIDER in required,
            requires_graph=RAGRuntimeDependency.GRAPH in required,
            requires_search=RAGRuntimeDependency.WEB in required,
            requires_database=RAGRuntimeDependency.DATABASE in required,
            requires_web_policy=RAGRuntimeDependency.WEB in required,
            requires_raft_service=RAGRuntimeDependency.RAFT_SERVICE in required,
            requires_raft_model=RAGRuntimeDependency.RAFT_MODEL in required,
        )
    return capabilities


def _has_async_method(dependency: object | None, method_name: str) -> bool:
    method = getattr(dependency, method_name, None)
    return callable(method) and inspect.iscoroutinefunction(method)


def _is_safe_web_capability(
    capability: SafeWebSearchCapability | None,
) -> TypeGuard[SafeWebSearchCapability]:
    from app.rag.agentic.patterns.web_augmented import SafeWebSearchCapability

    return (
        isinstance(capability, SafeWebSearchCapability)
        and capability.configured
        and inspect.iscoroutinefunction(capability.search)
    )


async def _probe_session_factory(factory: object | None, tenant_id: str) -> str | None:
    if not callable(factory):
        return "session_factory_unavailable"
    try:
        context = factory()
        if inspect.iscoroutine(context):
            context.close()
            return "session_factory_unavailable"
        if not (
            _has_async_method(context, "__aenter__") and _has_async_method(context, "__aexit__")
        ):
            return "session_factory_unavailable"
        async with context as session:
            begin = getattr(session, "begin", None)
            if not callable(begin):
                return "session_factory_unavailable"
            transaction = begin()
            if not (
                _has_async_method(transaction, "__aenter__")
                and _has_async_method(transaction, "__aexit__")
                and _has_async_method(session, "execute")
                and _has_async_method(session, "scalar")
            ):
                return "session_factory_unavailable"
            async with transaction, sqlalchemy_rls_context(session, tenant_id):
                if await session.scalar(text("SELECT 1")) != 1:
                    return "persistence_unavailable"
                persisted_capabilities = await session.scalar(
                    text(
                        "SELECT "
                        "to_regclass('public.knowledge_collections') IS NOT NULL "
                        "AND to_regclass('public.knowledge_chunks_768') IS NOT NULL "
                        "AND to_regclass('public.knowledge_chunks_1024') IS NOT NULL "
                        "AND to_regclass('public.knowledge_chunks_1536') IS NOT NULL "
                        "AND to_regclass('public.knowledge_chunks_3072') IS NOT NULL "
                        "AND "
                        "EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') "
                        "AND EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') "
                        "AND EXISTS (SELECT 1 FROM pg_operator WHERE oprname = '<=>') "
                        "AND EXISTS (SELECT 1 FROM pg_operator WHERE oprname = '%') "
                        "AND EXISTS (SELECT 1 FROM alembic_version) "
                        "AND NOT EXISTS ("
                        "  SELECT required.table_name, required.column_name FROM (VALUES "
                        "  ('knowledge_collections', 'tenant_id'), "
                        "  ('knowledge_collections', 'is_active'), "
                        "  ('knowledge_collections', 'embedding_dim'), "
                        "  ('knowledge_chunks_768', 'metadata'), "
                        "  ('knowledge_chunks_768', 'embedding'), "
                        "  ('knowledge_chunks_768', 'expires_at'), "
                        "  ('knowledge_chunks_768', 'parent_chunk_id'), "
                        "  ('knowledge_chunks_768', 'chunk_level'), "
                        "  ('knowledge_chunks_768', 'window_start'), "
                        "  ('knowledge_chunks_768', 'window_end'), "
                        "  ('knowledge_chunks_768', 'window_id'), "
                        "  ('knowledge_chunks_768', 'hierarchy_level'), "
                        "  ('knowledge_chunks_768', 'is_proposition'), "
                        "  ('knowledge_chunks_768', 'strategy_metadata'), "
                        "  ('knowledge_chunks_768', 'ingestion_job_id'), "
                        "  ('knowledge_chunks_1024', 'metadata'), "
                        "  ('knowledge_chunks_1024', 'embedding'), "
                        "  ('knowledge_chunks_1024', 'expires_at'), "
                        "  ('knowledge_chunks_1024', 'parent_chunk_id'), "
                        "  ('knowledge_chunks_1024', 'chunk_level'), "
                        "  ('knowledge_chunks_1024', 'window_start'), "
                        "  ('knowledge_chunks_1024', 'window_end'), "
                        "  ('knowledge_chunks_1024', 'window_id'), "
                        "  ('knowledge_chunks_1024', 'hierarchy_level'), "
                        "  ('knowledge_chunks_1024', 'is_proposition'), "
                        "  ('knowledge_chunks_1024', 'strategy_metadata'), "
                        "  ('knowledge_chunks_1024', 'ingestion_job_id'), "
                        "  ('knowledge_chunks_1536', 'metadata'), "
                        "  ('knowledge_chunks_1536', 'embedding'), "
                        "  ('knowledge_chunks_1536', 'expires_at'), "
                        "  ('knowledge_chunks_1536', 'parent_chunk_id'), "
                        "  ('knowledge_chunks_1536', 'chunk_level'), "
                        "  ('knowledge_chunks_1536', 'window_start'), "
                        "  ('knowledge_chunks_1536', 'window_end'), "
                        "  ('knowledge_chunks_1536', 'window_id'), "
                        "  ('knowledge_chunks_1536', 'hierarchy_level'), "
                        "  ('knowledge_chunks_1536', 'is_proposition'), "
                        "  ('knowledge_chunks_1536', 'strategy_metadata'), "
                        "  ('knowledge_chunks_1536', 'ingestion_job_id'), "
                        "  ('knowledge_chunks_3072', 'metadata'), "
                        "  ('knowledge_chunks_3072', 'embedding'), "
                        "  ('knowledge_chunks_3072', 'expires_at'), "
                        "  ('knowledge_chunks_3072', 'parent_chunk_id'), "
                        "  ('knowledge_chunks_3072', 'chunk_level'), "
                        "  ('knowledge_chunks_3072', 'window_start'), "
                        "  ('knowledge_chunks_3072', 'window_end'), "
                        "  ('knowledge_chunks_3072', 'window_id'), "
                        "  ('knowledge_chunks_3072', 'hierarchy_level'), "
                        "  ('knowledge_chunks_3072', 'is_proposition'), "
                        "  ('knowledge_chunks_3072', 'strategy_metadata'), "
                        "  ('knowledge_chunks_3072', 'ingestion_job_id')"
                        "  ) AS required(table_name, column_name) "
                        "  EXCEPT SELECT table_name, column_name "
                        "  FROM information_schema.columns WHERE table_schema = 'public'"
                        ") "
                        "AND (SELECT count(DISTINCT tablename) FROM pg_indexes "
                        "     WHERE schemaname = 'public' AND tablename LIKE 'knowledge_chunks_%' "
                        "     AND indexdef ILIKE '%hnsw%' "
                        "     AND indexdef ILIKE '%cosine_ops%') = 4 "
                        "AND (SELECT count(DISTINCT tablename) FROM pg_indexes "
                        "     WHERE schemaname = 'public' AND tablename LIKE 'knowledge_chunks_%' "
                        "     AND indexdef ILIKE '%gin_trgm_ops%') = 4 "
                        "AND (SELECT count(DISTINCT tablename) FROM pg_indexes "
                        "     WHERE schemaname = 'public' AND tablename LIKE 'knowledge_chunks_%' "
                        "     AND indexdef ILIKE '%to_tsvector%') = 4"
                        " AND (SELECT count(DISTINCT tablename) FROM pg_indexes "
                        "      WHERE schemaname = 'public' AND tablename LIKE 'knowledge_chunks_%' "
                        "      AND indexdef ILIKE '%metadata jsonb_path_ops%') = 4"
                    )
                )
                return None if persisted_capabilities is True else "persistence_unavailable"
    except Exception:
        return "persistence_unavailable"


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
    search_capability: SafeWebSearchCapability | None = None
    policy_services: tuple[object, ...] = ()
    cost_controller: RAGCostController | None = None
    modular_pipeline: ModularPipelineSpec | None = None
    raft_service: RAFTService | None = None
    colbert_checkpoint: str = "colbert-ir/colbertv2.0"
    strategy_timeout_seconds: float = 30.0
    statement_timeout_ms: int = 30_000


@dataclass(frozen=True, slots=True)
class RetrievalRuntimeDependencies:
    """Resolved capabilities and trusted configuration exposed to an adapter."""

    embedder: object | None
    llm: ResolvedLLM | None
    graph_capability: TenantScopedGraphCapability | None
    search_capability: SafeWebSearchCapability | None
    policy_services: tuple[object, ...]
    available_strategies: tuple[RAGStrategy, ...] = ()
    strategy_llms: Mapping[RAGStrategy, ResolvedLLM] = field(default_factory=dict)
    cost_guard: _RAGCostGuard | None = None
    modular_pipeline: ModularPipelineSpec | None = None
    raft_service: RAFTService | None = None


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
                "AND collection.is_active IS TRUE "
                "AND tenant.is_active IS TRUE LIMIT 1"
            ),
            {
                "collection_id": collection_id,
                "tenant_id": tenant_context.tenant_id,
            },
        )
        return result.scalar_one_or_none() is not None


async def _require_active_collection(
    session: AsyncSession,
    tenant_context: TenantContext,
    collection_id: str,
) -> None:
    if not await SQLCollectionAuthorizer().authorize(
        session,
        tenant_context,
        collection_id,
    ):
        raise CollectionNotFoundError(collection_id)


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
    statement_timeout_ms: int

    async def run(
        self,
        operation: DatabaseOperation[T],
        *,
        repeatable_read: bool = False,
    ) -> T:
        async with self.session_factory() as session, session.begin():
            if repeatable_read:
                await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            await session.execute(
                text("SELECT set_config('statement_timeout', :timeout, true)"),
                {"timeout": f"{self.statement_timeout_ms}ms"},
            )
            async with sqlalchemy_rls_context(session, self.tenant_context.tenant_id):
                return await operation(session)


@dataclass(frozen=True, slots=True)
class RetrievalExecutionContext:
    """Dependencies scoped to one authenticated retrieval execution."""

    tenant_context: TenantContext
    strategy: RAGStrategy
    filters: dict[str, Any]
    dependencies: RetrievalRuntimeDependencies
    _db_operation_runner: Callable[[DatabaseOperation[Any]], Awaitable[Any]] | None
    _repeatable_read_db_operation_runner: (
        Callable[[DatabaseOperation[Any]], Awaitable[Any]] | None
    ) = None

    @property
    def llm(self) -> ResolvedLLM | None:
        return self.dependencies.llm

    async def run_db_operation(
        self,
        operation: DatabaseOperation[T],
        *,
        repeatable_read: bool = False,
    ) -> T:
        """Run one DB operation in its own session, transaction, and RLS scope."""

        if self._db_operation_runner is None:
            raise RuntimeError("A database session factory is not configured")
        runner = (
            self._repeatable_read_db_operation_runner
            if repeatable_read
            else self._db_operation_runner
        )
        if runner is None and repeatable_read:
            runner = self._db_operation_runner
        if runner is None:
            raise RuntimeError("A repeatable-read database runner is not configured")
        result: T = await runner(operation)
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
                    await _require_active_collection(session, self.tenant_context, collection_id)
                    return await rag_engine.hybrid_search(
                        session,
                        query=variant_query,
                        query_embedding=variant_embedding,
                        collection_id=collection_id,
                        top_k=top_k,
                        metadata_filter=self.filters,
                        strict=True,
                    )

                return await self.run_db_operation(search, repeatable_read=True)

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
            await _require_active_collection(session, self.tenant_context, collection_id)
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

    async def reserve_cost(self, operation: str) -> None:
        if self.dependencies.cost_guard is not None:
            await self.dependencies.cost_guard.reserve(operation)


async def execute_core_strategy(
    strategy: RAGStrategy,
    request: RAGExecutionRequest,
    context: RetrievalExecutionContext,
) -> RAGExecutionResult:
    """Execute one certified strategy through tenant-scoped persistence boundaries."""

    if strategy not in DIRECT_CORE_RAG_STRATEGIES and strategy is not RAGStrategy.ADAPTIVE:
        raise RetrievalStrategyExecutionError(strategy.value, "adapter is not certified")

    collection_id = request.collection_id
    if not collection_id:
        raise RetrievalStrategyExecutionError(strategy.value, "collection is required")

    if strategy is RAGStrategy.NAIVE:
        embedding = await _embed_text(context, request.query, strategy)
        evidence: list[dict[str, Any]] = []
        results = await _search_persisted(
            context,
            request,
            query=request.query,
            embedding=embedding,
            retrieval_mode="vector",
            evidence=evidence,
        )
        for item in evidence:
            item["query"] = request.query
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

    if strategy is RAGStrategy.GRAPH:
        from app.rag.agentic.patterns.graph import (
            GraphEvidence,
            GraphEvidenceQuery,
            graph_results,
            sanitized_tenant_id,
        )

        graph_capability = context.dependencies.graph_capability
        if graph_capability is None:
            raise UnavailableRAGStrategyError(strategy, "graph capability is not configured")
        embedding = await _embed_text(context, request.query, strategy)
        evidence = []
        seeds = await _search_persisted(
            context,
            request,
            query=request.query,
            embedding=embedding,
            retrieval_mode="vector",
            evidence=evidence,
        )
        stable_tenant_id = sanitized_tenant_id(context.tenant_context.tenant_id)
        _mark_source_type(seeds, "persisted")
        for seed in seeds:
            seed.source_metadata["tenant_id"] = stable_tenant_id
        for item in evidence:
            item["tenant_id"] = stable_tenant_id
        seed_identifiers = tuple(
            dict.fromkeys(
                identifier
                for result in seeds
                for identifier in (
                    result.chunk_id,
                    str(result.source_metadata.get("source_id") or ""),
                    str(result.source_metadata.get("source_doc_id") or ""),
                    str(result.source_metadata.get("document_id") or ""),
                )
                if identifier
            )
        )
        raw_graph_evidence = await graph_capability.retrieve_evidence(
            GraphEvidenceQuery(
                tenant_id=context.tenant_context.tenant_id,
                query=request.query,
                seed_chunk_ids=seed_identifiers,
                filters=request.filters,
                max_per_type=min(request.top_k, 8),
            )
        )
        if not all(isinstance(item, GraphEvidence) for item in raw_graph_evidence):
            raise RetrievalStrategyExecutionError(strategy.value, "invalid graph evidence")
        typed_graph_evidence = [
            item for item in raw_graph_evidence if isinstance(item, GraphEvidence)
        ]
        graph_items = graph_results(
            typed_graph_evidence,
            tenant_id=context.tenant_context.tenant_id,
        )
        for evidence_type in ("entity", "path", "community"):
            typed_items = [
                item for item in typed_graph_evidence if item.evidence_type == evidence_type
            ]
            evidence.append(
                {
                    "component": f"graph_{evidence_type}",
                    "query": request.query,
                    "tenant_id": stable_tenant_id,
                    "result_count": len(typed_items),
                    "component_scores": {item.evidence_id: item.score for item in typed_items},
                }
            )
        results = rag_engine.merge_grounding_results(
            [seeds, graph_items],
            top_k=request.top_k,
        )
        result = _canonical_result(request, strategy, results, evidence)
        return _append_trace(
            result,
            RAGStrategyTrace(
                strategy=strategy,
                action="graph_evidence_merge",
                status="complete",
                detail={
                    "seed_count": len(seeds),
                    "graph_evidence_count": len(graph_items),
                    "result_count": len(results),
                    "tenant_id": stable_tenant_id,
                },
            ),
        )

    if strategy is RAGStrategy.WEB_AUGMENTED:
        from app.rag.agentic.patterns.web_augmented import (
            WebSearchCapabilityError,
            resolve_web_policy,
            retrieve_web_results,
        )

        web_capability = context.dependencies.search_capability
        if not _is_safe_web_capability(web_capability):
            raise UnavailableRAGStrategyError(strategy, "search capability is not configured")
        policy = await resolve_web_policy(
            context.dependencies.policy_services,
            context.tenant_context,
        )
        if not policy.allowed:
            raise UnavailableRAGStrategyError(strategy, policy.reason)
        embedding = await _embed_text(context, request.query, strategy)
        evidence = []
        persisted = await _search_persisted(
            context,
            request,
            query=request.query,
            embedding=embedding,
            retrieval_mode="hybrid",
            evidence=evidence,
        )
        _mark_source_type(persisted, "persisted")
        await context.reserve_cost("web_retrieval")
        try:
            web_results, web_evidence = await retrieve_web_results(
                web_capability,
                tenant_context=context.tenant_context,
                query=request.query,
                top_k=request.top_k,
                policy=policy,
            )
        except WebSearchCapabilityError as exc:
            raise RetrievalStrategyExecutionError(strategy.value, exc.reason) from exc
        evidence.append(web_evidence)
        results = rag_engine.merge_grounding_results(
            [persisted, web_results],
            top_k=request.top_k,
        )
        result = _canonical_result(request, strategy, results, evidence)
        return _append_trace(
            result,
            RAGStrategyTrace(
                strategy=strategy,
                action="web_persisted_merge",
                status="complete",
                detail={
                    "persisted_count": len(persisted),
                    "web_count": len(web_results),
                    "stop_reason": (
                        "web_search_empty" if not web_results else "web_persisted_merge_complete"
                    ),
                    "web_status": "empty" if not web_results else "complete",
                },
            ),
        )

    if strategy is RAGStrategy.ADAPTIVE:
        from dataclasses import replace

        from app.rag.agentic.patterns.adaptive import select_adaptive_strategy

        try:
            decision = select_adaptive_strategy(
                request.query,
                context.dependencies.available_strategies,
            )
        except ValueError as exc:
            raise UnavailableRAGStrategyError(strategy, str(exc)) from exc
        selected_dependencies = replace(
            context.dependencies,
            llm=context.dependencies.strategy_llms.get(decision.strategy, context.llm),
        )
        selected_context = replace(
            context,
            strategy=decision.strategy,
            dependencies=selected_dependencies,
        )
        if decision.strategy in DIRECT_CORE_RAG_STRATEGIES:
            selected = await execute_core_strategy(decision.strategy, request, selected_context)
        else:
            reasoning_factory = _REASONING_ADAPTER_FACTORIES.get(decision.strategy)
            if reasoning_factory is None:
                raise UnavailableRAGStrategyError(
                    strategy,
                    f"adaptive selected an undispatchable strategy: {decision.strategy.value}",
                )
            selected = await reasoning_factory().execute(request, selected_context)
        decision_trace = RAGStrategyTrace(
            strategy=strategy,
            action="adaptive_selection",
            status="complete",
            detail={
                "selected_strategy": decision.strategy.value,
                "reason": decision.reason,
                "decision_count": decision.decision_count,
            },
        )
        return selected.model_copy(
            update={
                "resolved_strategy_id": strategy,
                "strategy_trace": [decision_trace, *selected.strategy_trace],
            }
        )

    if strategy in {RAGStrategy.RAPTOR, RAGStrategy.AGENTIC_CHUNKING}:
        from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern
        from app.rag.agentic.patterns.raptor import RAPTORPattern

        embedding = await _embed_text(context, request.query, strategy)
        store = _GatewaySessionStore(context)
        pattern: Any = (
            RAPTORPattern() if strategy is RAGStrategy.RAPTOR else AgenticChunkingPattern()
        )
        # Drive the real precomputed-retrieval path (tree levels for RAPTOR,
        # propositions + parent windows for agentic chunking).
        precomputed = await pattern.retrieve_precomputed(
            store=store,
            query=request.query,
            query_embedding=embedding,
            collection_id=collection_id,
            tenant_ctx=context.tenant_context,
            top_k=request.top_k,
        )
        if precomputed:
            results = [_precomputed_dict_to_result(item, strategy) for item in precomputed]
            result = _canonical_result(
                request,
                strategy,
                results,
                [
                    {
                        "component": strategy.value,
                        "result_count": len(results),
                        "precomputed": True,
                    }
                ],
            )
            return _append_trace(
                result,
                RAGStrategyTrace(
                    strategy=strategy,
                    action="precomputed_index_retrieval",
                    status="complete",
                    detail={
                        "precomputed": True,
                        "source": "precomputed_index",
                        "result_count": len(results),
                        "hierarchy_levels": sorted(
                            {
                                int(result_item.source_metadata.get("hierarchy_level", 0))
                                for result_item in results
                            }
                        ),
                    },
                ),
            )
        # No precomputed index for this collection — degrade to a plain hybrid
        # retrieval and record the reason. Never silently return an empty result.
        fallback_evidence: list[dict[str, Any]] = []
        fallback_results = await _search_persisted(
            context,
            request,
            query=request.query,
            embedding=embedding,
            retrieval_mode="hybrid",
            evidence=fallback_evidence,
        )
        _mark_source_type(fallback_results, "persisted")
        result = _canonical_result(request, strategy, fallback_results, fallback_evidence)
        return _append_trace(
            result,
            RAGStrategyTrace(
                strategy=strategy,
                action="precomputed_index_fallback",
                status="degraded",
                detail={
                    "reason": "precomputed_index_absent",
                    "fallback_retrieval": "hybrid",
                    "result_count": len(fallback_results),
                },
            ),
        )

    llm = context.llm
    if llm is None or llm.provider is None or not llm.model:
        raise RetrievalStrategyExecutionError(strategy.value, "resolved LLM is required")

    if strategy is RAGStrategy.CORRECTIVE:
        from app.rag.agentic.patterns.corrective import (
            CORRECTIVE_RELEVANCE_THRESHOLD,
            MAX_CORRECTIVE_RETRIES,
            grade_evidence,
            reformulate_query,
        )
        from app.rag.agentic.patterns.web_augmented import (
            WebSearchCapabilityError,
            resolve_web_policy,
            retrieve_web_results,
        )

        current_query = request.query
        retained: list[EngineRetrievalResult] = []
        corrective_evidence: list[dict[str, Any]] = []
        trace: list[RAGStrategyTrace] = []
        for attempt in range(MAX_CORRECTIVE_RETRIES + 1):
            embedding = await _embed_text(context, current_query, strategy)
            attempt_evidence: list[dict[str, Any]] = []
            candidates = await _search_persisted(
                context,
                request,
                query=current_query,
                embedding=embedding,
                retrieval_mode="hybrid",
                evidence=attempt_evidence,
            )
            for item in attempt_evidence:
                item.update({"query": current_query, "attempt": attempt})
            corrective_evidence.extend(attempt_evidence)
            scores = await grade_evidence(
                provider=llm.provider,
                model=llm.model,
                query=current_query,
                results=candidates,
            )
            filtered = [
                result
                for result, score in zip(candidates, scores, strict=True)
                if score >= CORRECTIVE_RELEVANCE_THRESHOLD
            ]
            _mark_source_type(filtered, "persisted")
            retained = rag_engine.merge_grounding_results(
                [retained, filtered],
                top_k=request.top_k,
            )
            required_relevant = min(2, request.top_k)
            sufficient = len(retained) >= required_relevant
            trace.append(
                RAGStrategyTrace(
                    strategy=strategy,
                    action="evidence_grade",
                    status="complete",
                    detail={
                        "attempt": attempt,
                        "candidate_count": len(candidates),
                        "retained_count": len(filtered),
                        "total_retained_count": len(retained),
                        "required_relevant_count": required_relevant,
                        "scores": scores,
                        "decision": "sufficient" if sufficient else "reformulate_or_fallback",
                    },
                )
            )
            if sufficient:
                trace.append(
                    RAGStrategyTrace(
                        strategy=strategy,
                        action="corrective_stop",
                        status="complete",
                        detail={"stop_reason": "persisted_evidence_sufficient", "attempt": attempt},
                    )
                )
                result = _canonical_result(
                    request,
                    strategy,
                    retained,
                    corrective_evidence,
                )
                return _extend_trace(result, trace)
            if attempt < MAX_CORRECTIVE_RETRIES:
                current_query = await reformulate_query(
                    provider=llm.provider,
                    model=llm.model,
                    query=current_query,
                    attempt=attempt + 1,
                )
                trace.append(
                    RAGStrategyTrace(
                        strategy=strategy,
                        action="query_reformulation",
                        status="complete",
                        detail={"attempt": attempt + 1, "model": llm.model},
                    )
                )

        web_capability = context.dependencies.search_capability
        if not _is_safe_web_capability(web_capability):
            stop_reason = "web_capability_unavailable"
        else:
            policy = await resolve_web_policy(
                context.dependencies.policy_services,
                context.tenant_context,
            )
            stop_reason = policy.reason
            if policy.allowed:
                await context.reserve_cost("web_retrieval")
                try:
                    web_results, web_evidence = await retrieve_web_results(
                        web_capability,
                        tenant_context=context.tenant_context,
                        query=current_query,
                        top_k=request.top_k,
                        policy=policy,
                    )
                except WebSearchCapabilityError as exc:
                    raise RetrievalStrategyExecutionError(strategy.value, exc.reason) from exc
                corrective_evidence.append(web_evidence)
                retained = rag_engine.merge_grounding_results(
                    [retained, web_results],
                    top_k=request.top_k,
                )
                stop_reason = "web_fallback_empty" if not web_results else "web_fallback_complete"
        trace.append(
            RAGStrategyTrace(
                strategy=strategy,
                action="corrective_stop",
                status="complete",
                detail={
                    "stop_reason": stop_reason,
                    "attempts": MAX_CORRECTIVE_RETRIES + 1,
                    "web_status": (
                        "empty"
                        if stop_reason == "web_fallback_empty"
                        else "complete"
                        if stop_reason == "web_fallback_complete"
                        else "not_attempted"
                    ),
                },
            )
        )
        result = _canonical_result(request, strategy, retained, corrective_evidence)
        return _extend_trace(result, trace)

    if strategy is RAGStrategy.HYDE:
        generated_evidence: dict[str, Any] = {}
        hyde_started = time.perf_counter()

        async def operation(session: AsyncSession) -> list[EngineRetrievalResult]:
            await _require_active_collection(session, context.tenant_context, collection_id)
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
                "latency_ms": (time.perf_counter() - hyde_started) * 1000,
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
    search_latencies: dict[str, float] = {}

    async def search_operation(
        variant_query: str,
        variant_embedding: list[float] | None,
    ) -> list[EngineRetrievalResult]:
        search_started = time.perf_counter()
        try:
            return await _search_persisted(
                context,
                request,
                query=variant_query,
                embedding=variant_embedding,
                retrieval_mode="hybrid",
            )
        finally:
            search_latencies[variant_query] = (time.perf_counter() - search_started) * 1000

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
        for item in strategy_evidence:
            item["latency_ms"] = search_latencies.get(str(item.get("query")), 0.0)
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
        for item in strategy_evidence:
            item["latency_ms"] = search_latencies.get(str(item.get("query")), 0.0)
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


@dataclass(frozen=True, slots=True)
class _GatewaySessionStore:
    """Session-backed precomputed-index store bridging query patterns to the gateway.

    RAPTOR / agentic-chunking query patterns retrieve through a store's
    ``search_precomputed_index``. The gateway operates over tenant-scoped
    sessions rather than a long-lived store, so this adapter runs the same
    strategy-filtered precomputed retrieval the persistent store performs,
    inside the gateway's RLS-scoped session and fail-closed engine core.
    """

    _context: RetrievalExecutionContext

    async def search_precomputed_index(
        self,
        *,
        strategy: RAGStrategy,
        query: str,
        query_embedding: list[float],
        collection_id: str,
        tenant_ctx: TenantContext,
        top_k: int,
    ) -> list[dict[str, Any]]:
        context = self._context

        async def operation(session: AsyncSession) -> list[EngineRetrievalResult]:
            await _require_active_collection(session, tenant_ctx, collection_id)
            return await rag_engine.retrieve(
                session,
                query=query,
                query_embedding=query_embedding,
                collection_id=collection_id,
                top_k=top_k,
                strategy=strategy.value,
                provider=context.llm.provider if context.llm is not None else None,
                model=context.llm.model if context.llm is not None else "",
                metadata_filter=context.filters,
                embedder=context.dependencies.embedder,
                tenant_ctx=tenant_ctx,
                strict=True,
            )

        results = await context.run_db_operation(operation, repeatable_read=True)
        return [
            {
                "chunk_id": result.chunk_id,
                "content": result.content,
                "score": result.score,
                "metadata": dict(result.source_metadata),
                "citation_chunk_id": result.chunk_id,
                "citation_content": result.content,
            }
            for result in results
        ]


def _precomputed_dict_to_result(
    item: dict[str, Any],
    strategy: RAGStrategy,
) -> EngineRetrievalResult:
    """Normalize a precomputed-index dict back into a canonical engine result."""

    metadata = dict(item.get("metadata", {}))
    metadata.setdefault("precomputed", True)
    return EngineRetrievalResult(
        chunk_id=str(item.get("chunk_id", "")),
        content=str(item.get("content", "")),
        score=float(item.get("score", 0.0)),
        source_metadata=metadata,
        retrieval_legs=[strategy.value],
    )


async def _embed_text(
    context: RetrievalExecutionContext,
    text_value: str,
    strategy: RAGStrategy,
) -> list[float]:
    from app.providers.base import EmbedRequest

    embedder: Any = context.dependencies.embedder
    if embedder is None or not callable(getattr(embedder, "embed", None)):
        raise RetrievalStrategyExecutionError(strategy.value, "embedding provider is required")
    try:
        response = await embedder.embed(EmbedRequest(texts=[text_value], input_type="query"))
        embedding = response.embeddings[0] if response.embeddings else None
    except Exception as exc:
        if isinstance(exc, RetrievalStrategyExecutionError):
            raise
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
    top_k: int | None = None,
) -> list[EngineRetrievalResult]:
    async def operation(session: AsyncSession) -> list[EngineRetrievalResult]:
        await _require_active_collection(
            session,
            context.tenant_context,
            request.collection_id or "",
        )
        return await rag_engine.hybrid_search(
            session,
            query=query,
            query_embedding=embedding,
            collection_id=request.collection_id or "",
            top_k=top_k or request.top_k,
            retrieval_mode=retrieval_mode,
            metadata_filter=request.filters,
            strict=True,
            evidence=evidence,
        )

    return await context.run_db_operation(
        operation,
        repeatable_read=retrieval_mode == "hybrid",
    )


def _mark_source_type(results: list[EngineRetrievalResult], source_type: str) -> None:
    for result in results:
        result.source_metadata = {
            **result.source_metadata,
            "source_type": source_type,
        }


def _append_trace(
    result: RAGExecutionResult,
    trace: RAGStrategyTrace,
) -> RAGExecutionResult:
    return result.model_copy(update={"strategy_trace": [*result.strategy_trace, trace]})


def _extend_trace(
    result: RAGExecutionResult,
    trace: list[RAGStrategyTrace],
) -> RAGExecutionResult:
    return result.model_copy(update={"strategy_trace": [*result.strategy_trace, *trace]})


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
            latency_ms=float(item.get("latency_ms", 0.0)),
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
                detail={"rrf_scores": {result.chunk_id: result.rrf_score for result in results}},
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
        self._close_lock = asyncio.Lock()
        self._is_closed = False

    async def aclose(self) -> None:
        """Close each gateway-owned capability adapter at most once."""
        async with self._close_lock:
            if self._is_closed:
                return
            self._is_closed = True
            closeable_adapters: list[AsyncCloseableProtocol] = []
            seen_adapter_ids: set[int] = set()
            for capability in self.dependencies.strategy_capabilities.values():
                adapter = capability.adapter
                adapter_id = id(adapter)
                if adapter_id not in seen_adapter_ids and isinstance(
                    adapter, AsyncCloseableProtocol
                ):
                    seen_adapter_ids.add(adapter_id)
                    closeable_adapters.append(adapter)
            results = await asyncio.gather(
                *(adapter.aclose() for adapter in closeable_adapters),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    logger.warning(
                        "rag_capability_close_failed",
                        failure_type=type(result).__name__,
                    )

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
        context = await self.readiness_context(
            tenant_context,
            collection_id=collection_id,
            strategies=(strategy,),
        )
        decision = RAG_CAPABILITY_CATALOGUE[strategy].evaluate_readiness(context)
        if not decision.available:
            return RAGStrategyReadiness(strategy, False, decision.reason)
        if (
            isinstance(self.dependencies.collection_authorizer, SQLCollectionAuthorizer)
            and self.dependencies.session_factory is None
        ):
            return RAGStrategyReadiness(strategy, False, "session_factory_unavailable")
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

    async def readiness_all(
        self,
        tenant_context: TenantContext,
        *,
        collection_id: str | None = None,
    ) -> Mapping[RAGStrategy, RAGStrategyReadiness]:
        """Evaluate every catalogue predicate from one bounded shared-fact snapshot."""
        context = await self.readiness_context(
            tenant_context,
            collection_id=collection_id,
        )
        return MappingProxyType(
            {
                strategy: RAGStrategyReadiness(
                    strategy,
                    decision.available,
                    decision.reason,
                )
                for strategy, entry in RAG_CAPABILITY_CATALOGUE.items()
                for decision in (entry.evaluate_readiness(context),)
            }
        )

    async def readiness_context(
        self,
        tenant_context: TenantContext,
        *,
        collection_id: str | None = None,
        probe_database: bool = True,
        strategies: Sequence[RAGStrategy] | None = None,
        resolve_providers: bool = True,
    ) -> ReadinessContext:
        """Compute shared dependency facts once for one tenant discovery request."""
        from app.rag.readiness import probe_colbert_library, probe_colbert_readiness

        if probe_database:
            persistence_reason = await _probe_session_factory(
                self.dependencies.session_factory,
                tenant_context.tenant_id,
            )
        elif callable(self.dependencies.session_factory):
            persistence_reason = None
        else:
            persistence_reason = "session_factory_unavailable"
        database_fact = ReadinessFact(
            persistence_reason is None,
            persistence_reason or "ready",
        )
        embedder_available = _has_async_method(self.dependencies.embedder, "embed")
        embedder_fact = ReadinessFact(
            embedder_available,
            "ready" if embedder_available else "embedder_unavailable",
        )
        graph_available = self._has_graph_capability()
        graph_fact = ReadinessFact(
            graph_available,
            "ready" if graph_available else "graph_capability_unavailable",
        )
        if not _is_safe_web_capability(self.dependencies.search_capability):
            web_fact = ReadinessFact(False, "search_capability_unavailable")
        else:
            web_reason = await self._web_policy_reason(tenant_context)
            web_fact = ReadinessFact(
                web_reason == "web_policy_allowed",
                "ready" if web_reason == "web_policy_allowed" else web_reason,
            )
        service = self.dependencies.raft_service
        raft_service_fact = ReadinessFact(
            isinstance(service, RAFTService),
            "ready" if isinstance(service, RAFTService) else "raft_service_unavailable",
        )
        if collection_id is None:
            raft_model_fact = ReadinessFact(False, "raft_model_selection_required")
        else:
            try:
                has_raft_model = bool(
                    isinstance(service, RAFTService)
                    and await service.has_completed_model(
                        tenant_context,
                        collection_id=collection_id,
                    )
                )
            except Exception:
                has_raft_model = False
            raft_model_fact = ReadinessFact(
                has_raft_model,
                "ready" if has_raft_model else "raft_model_unavailable",
            )
        colbert_library_fact = probe_colbert_library()
        colbert_checkpoint_fact = probe_colbert_readiness(
            self.dependencies.colbert_checkpoint,
            library_fact=colbert_library_fact,
        )
        facts = MappingProxyType(
            {
                RAGRuntimeDependency.DATABASE: database_fact,
                RAGRuntimeDependency.EMBEDDER: embedder_fact,
                RAGRuntimeDependency.PROVIDER: ReadinessFact(True, "ready"),
                RAGRuntimeDependency.GRAPH: graph_fact,
                RAGRuntimeDependency.WEB: web_fact,
                RAGRuntimeDependency.COLBERT_LIBRARY: colbert_library_fact,
                RAGRuntimeDependency.COLBERT_CHECKPOINT: colbert_checkpoint_fact,
                RAGRuntimeDependency.RAFT_SERVICE: raft_service_fact,
                RAGRuntimeDependency.RAFT_MODEL: raft_model_fact,
            }
        )
        strategy_facts: dict[RAGStrategy, Mapping[RAGRuntimeDependency, ReadinessFact]] = {}
        adapter_facts: dict[RAGStrategy, ReadinessFact] = {}
        target_strategies = tuple(strategies or RAG_CAPABILITY_CATALOGUE)
        for strategy in target_strategies:
            entry = RAG_CAPABILITY_CATALOGUE[strategy]
            capability = self.dependencies.strategy_capabilities.get(strategy)
            if capability is None:
                adapter_facts[strategy] = ReadinessFact(False, "adapter_not_registered")
            else:
                execute = getattr(capability.adapter, "execute", None)
                is_valid = inspect.iscoroutinefunction(execute) and not inspect.isabstract(
                    capability.adapter
                )
                adapter_facts[strategy] = ReadinessFact(
                    is_valid,
                    "ready" if is_valid else "invalid_adapter",
                )
            if RAGRuntimeDependency.PROVIDER not in entry.required_dependencies:
                continue
            if not resolve_providers:
                provider_available = self.dependencies.llm_resolver is not None
                strategy_facts[strategy] = MappingProxyType(
                    {
                        RAGRuntimeDependency.PROVIDER: ReadinessFact(
                            provider_available,
                            "ready" if provider_available else "llm_provider_unavailable",
                        )
                    }
                )
                continue
            try:
                if capability is None:
                    raise UnavailableRAGStrategyError(strategy, "adapter is unavailable")
                await self._resolve_llm(strategy, capability, tenant_context)
            except Exception:
                provider_fact = ReadinessFact(False, "llm_provider_unavailable")
            else:
                provider_fact = ReadinessFact(True, "ready")
            strategy_facts[strategy] = MappingProxyType(
                {RAGRuntimeDependency.PROVIDER: provider_fact}
            )
        return ReadinessContext(
            facts,
            MappingProxyType(strategy_facts),
            MappingProxyType(adapter_facts),
        )

    async def execute(
        self,
        tenant_context: TenantContext,
        *,
        collection_id: str,
        query: str,
        strategy_id: str | RAGStrategy,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        execution_id: str = "",
    ) -> RAGExecutionResult:
        if not isinstance(tenant_context, TenantContext):
            raise TypeError("tenant_context must be a TenantContext")

        requested_strategy_id = (
            strategy_id.value if isinstance(strategy_id, RAGStrategy) else strategy_id
        )
        strategy = resolve_rag_strategy(strategy_id)
        request = RAGExecutionRequest(
            tenant_id=tenant_context.tenant_id,
            collection_id=collection_id,
            query=query,
            requested_strategy_id=requested_strategy_id,
            top_k=top_k,
            filters=filters or {},
            execution_id=execution_id or f"rag-{uuid.uuid4().hex}",
        )
        await self._validate_capabilities(
            strategy,
            tenant_context,
            collection_id=collection_id,
        )
        capability = self.dependencies.strategy_capabilities.get(strategy)
        if capability is None:
            raise UnavailableRAGStrategyError(strategy, "adapter_not_registered")
        llm = await self._resolve_llm(strategy, capability, tenant_context)
        runner = self._session_runner(tenant_context)
        await self._authorize_collection(runner, tenant_context, collection_id)

        async def run_repeatable_read(operation: DatabaseOperation[Any]) -> Any:
            if runner is None:
                raise RuntimeError("A database session factory is not configured")
            return await runner.run(operation, repeatable_read=True)

        bound_graph = (
            self.dependencies.graph_capability.bind(runner.run)
            if self.dependencies.graph_capability is not None and runner is not None
            else None
        )
        if strategy in {
            RAGStrategy.ADAPTIVE,
            RAGStrategy.AGENTIC,
            RAGStrategy.MODULAR,
        }:
            available_strategies, strategy_llms = await self._available_strategies(tenant_context)
        else:
            available_strategies, strategy_llms = (), {}
        cost_guard = _RAGCostGuard(
            self.dependencies.cost_controller,
            execution_id=request.execution_id,
            invocation_id=uuid.uuid4().hex,
            tenant_context=tenant_context,
            strategy=strategy,
        )
        runtime_embedder = (
            _BudgetedEmbedder(self.dependencies.embedder, cost_guard)
            if self.dependencies.cost_controller is not None
            and self.dependencies.embedder is not None
            else self.dependencies.embedder
        )
        runtime_llm = (
            ResolvedLLM(
                provider=_BudgetedProvider(llm.provider, cost_guard),
                model=llm.model,
                provider_type=llm.provider_type,
            )
            if self.dependencies.cost_controller is not None
            and llm is not None
            and llm.provider is not None
            else llm
        )
        budgeted_strategy_llms = (
            {
                candidate: ResolvedLLM(
                    provider=_BudgetedProvider(candidate_llm.provider, cost_guard),
                    model=candidate_llm.model,
                    provider_type=candidate_llm.provider_type,
                )
                for candidate, candidate_llm in strategy_llms.items()
            }
            if self.dependencies.cost_controller is not None
            else strategy_llms
        )
        context = RetrievalExecutionContext(
            tenant_context=tenant_context,
            strategy=strategy,
            filters=dict(request.filters),
            dependencies=RetrievalRuntimeDependencies(
                embedder=runtime_embedder,
                llm=runtime_llm,
                graph_capability=bound_graph,
                search_capability=self.dependencies.search_capability,
                policy_services=self.dependencies.policy_services,
                available_strategies=available_strategies,
                strategy_llms=budgeted_strategy_llms,
                cost_guard=cost_guard,
                modular_pipeline=self.dependencies.modular_pipeline,
                raft_service=self.dependencies.raft_service,
            ),
            _db_operation_runner=runner.run if runner is not None else None,
            _repeatable_read_db_operation_runner=(
                run_repeatable_read if runner is not None else None
            ),
        )
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.dependencies.strategy_timeout_seconds):
                result = await capability.adapter.execute(request, context)
        except TimeoutError as exc:
            logger.warning(
                "rag_strategy_failed",
                strategy=strategy.value,
                failure_type="deadline_exceeded",
                latency_ms=round((time.monotonic() - started) * 1000, 2),
            )
            raise RetrievalStrategyExecutionError(
                strategy.value, "strategy deadline exceeded"
            ) from exc
        except asyncio.CancelledError:
            logger.info(
                "rag_strategy_cancelled",
                strategy=strategy.value,
                latency_ms=round((time.monotonic() - started) * 1000, 2),
            )
            raise
        except Exception as exc:
            logger.warning(
                "rag_strategy_failed",
                strategy=strategy.value,
                failure_type=type(exc).__name__,
                latency_ms=round((time.monotonic() - started) * 1000, 2),
            )
            raise
        if isinstance(result, RAGExecutionResult):
            if result.resolved_strategy_id is not strategy:
                raise ValueError("RAG strategy adapter returned a mismatched strategy ID")
            normalized = result.model_copy(
                update={
                    "requested_strategy_id": requested_strategy_id,
                    "resolved_strategy_id": strategy,
                }
            )
        elif isinstance(result, (list, tuple)) and all(
            isinstance(item, EngineRetrievalResult) for item in result
        ):
            normalized = self._normalize_engine_results(request, strategy, list(result))
        else:
            raise TypeError("RAG strategy adapter returned an unsupported result type")

        total_latency_ms = (time.monotonic() - started) * 1000
        normalized = normalized.model_copy(
            update={
                "strategy_trace": [
                    *normalized.strategy_trace,
                    *cost_guard.traces(),
                ]
            }
        )
        logger.info(
            "rag_strategy_complete",
            strategy=strategy.value,
            latency_ms=round(total_latency_ms, 2),
        )
        final_result = normalized.model_copy(
            update={
                "strategy_trace": [
                    *normalized.strategy_trace,
                    RAGStrategyTrace(
                        strategy=strategy,
                        action="strategy_complete",
                        status="complete",
                        detail={"total_latency_ms": total_latency_ms},
                    ),
                ]
            }
        )
        final_result._budget_context = cost_guard
        return final_result

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
        return _TenantSessionRunner(
            self.dependencies.session_factory,
            tenant_context,
            self.dependencies.statement_timeout_ms,
        )

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

    async def _validate_capabilities(
        self,
        strategy: RAGStrategy,
        tenant_context: TenantContext,
        *,
        collection_id: str,
    ) -> None:
        context = await self.readiness_context(
            tenant_context,
            collection_id=collection_id,
            probe_database=False,
            strategies=(strategy,),
            resolve_providers=False,
        )
        decision = RAG_CAPABILITY_CATALOGUE[strategy].evaluate_readiness(context)
        if not decision.available:
            raise UnavailableRAGStrategyError(strategy, decision.reason)

    def _has_graph_capability(self) -> bool:
        return self.dependencies.session_factory is not None and callable(
            getattr(self.dependencies.graph_capability, "bind", None)
        )

    async def _web_policy_reason(self, tenant_context: TenantContext) -> str:
        from app.rag.agentic.patterns.web_augmented import resolve_web_policy

        try:
            decision = await resolve_web_policy(
                self.dependencies.policy_services,
                tenant_context,
            )
        except Exception:
            return "web_policy_unavailable"
        return decision.reason

    async def _available_strategies(
        self,
        tenant_context: TenantContext,
    ) -> tuple[tuple[RAGStrategy, ...], Mapping[RAGStrategy, ResolvedLLM]]:
        available: list[RAGStrategy] = []
        strategy_llms: dict[RAGStrategy, ResolvedLLM] = {}
        readiness = await self.readiness_context(
            tenant_context,
            probe_database=False,
        )
        for strategy, entry in RAG_CAPABILITY_CATALOGUE.items():
            if strategy is RAGStrategy.ADAPTIVE:
                continue
            decision = entry.evaluate_readiness(readiness)
            if not decision.available:
                continue
            capability = self.dependencies.strategy_capabilities[strategy]
            if RAGRuntimeDependency.PROVIDER in entry.required_dependencies:
                try:
                    candidate_llm = await self._resolve_llm(
                        strategy,
                        capability,
                        tenant_context,
                    )
                except UnavailableRAGStrategyError:
                    continue
                if candidate_llm is None:
                    continue
                strategy_llms[strategy] = candidate_llm
            available.append(strategy)
        return tuple(available), strategy_llms

    async def _resolve_llm(
        self,
        strategy: RAGStrategy,
        capability: RetrievalStrategyCapability,
        tenant_context: TenantContext,
    ) -> ResolvedLLM | None:
        del capability
        requires_provider = (
            RAGRuntimeDependency.PROVIDER
            in RAG_CAPABILITY_CATALOGUE[strategy].required_dependencies
        )
        resolver = self.dependencies.llm_resolver
        if resolver is None:
            if requires_provider:
                raise UnavailableRAGStrategyError(
                    strategy,
                    "LLM provider is not configured",
                )
            return None
        try:
            resolved = resolver(tenant_context, strategy)
            if inspect.isawaitable(resolved):
                resolved = await resolved
        except Exception as exc:
            if requires_provider:
                raise UnavailableRAGStrategyError(
                    strategy,
                    "LLM provider resolution failed",
                ) from exc
            return None
        if requires_provider and resolved is None:
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
