"""Real PostgreSQL coverage for the canonical persisted RAG store."""

from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.db.rls import sqlalchemy_rls_context
from app.providers.base import CompletionRequest, CompletionResponse, EmbedRequest, EmbedResponse
from app.rag.contracts import RAGExecutionRequest, RAGExecutionResult, RAGStrategy
from app.rag.engine import hybrid_search
from app.rag.gateway import (
    CollectionNotFoundError,
    ResolvedLLM,
    RetrievalDependencies,
    RetrievalExecutionContext,
    RetrievalGateway,
    RetrievalStrategyCapability,
    SQLCollectionAuthorizer,
    core_strategy_capabilities,
)
from app.rag.models import KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

SUPPORTED_EMBEDDING_DIMENSIONS = (768, 1024, 1536, 3072)
BACKEND_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class _Database:
    admin_factory: async_sessionmaker[AsyncSession]
    runtime_factory: async_sessionmaker[AsyncSession]


class _Embedder:
    def __init__(self, embedding: list[float]) -> None:
        self.embedding = embedding

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        return EmbedResponse(embeddings=[list(self.embedding) for _ in request.texts])


class _EmptyAdapter:
    async def execute(
        self,
        request: RAGExecutionRequest,
        context: RetrievalExecutionContext,
    ) -> RAGExecutionResult:
        return RAGExecutionResult(
            requested_strategy_id=request.requested_strategy_id,
            resolved_strategy_id=context.strategy,
        )


def _embedding(dimension: int, *, second: float = 0.0) -> list[float]:
    return [1.0, second, *([0.0] * (dimension - 2))]


def _runtime_url(admin_url: str, password: str) -> str:
    return make_url(admin_url).set(
        username="rag_runtime",
        password=password,
    ).render_as_string(hide_password=False)


async def _prepare_runtime_role(admin_url: str, password: str) -> None:
    engine = create_async_engine(admin_url)
    async with engine.begin() as connection:
        quoted_password = (
            await connection.execute(
                text("SELECT quote_literal(:password)"),
                {"password": password},
            )
        ).scalar_one()
        await connection.execute(
            text(
                "CREATE ROLE rag_runtime LOGIN PASSWORD "
                f"{quoted_password} NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
        )
        await connection.execute(text("GRANT CONNECT ON DATABASE test TO rag_runtime"))
        await connection.execute(text("GRANT USAGE ON SCHEMA public TO rag_runtime"))
        await connection.execute(
            text(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                "IN SCHEMA public TO rag_runtime"
            )
        )
        await connection.execute(
            text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rag_runtime")
        )
    await engine.dispose()


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        admin_url = postgres.get_connection_url()
        environment = {**os.environ, "DATABASE_URL": admin_url}
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )

        yield admin_url


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def postgres_database(postgres_url: str) -> AsyncIterator[_Database]:
    password = secrets.token_urlsafe(24)
    await _prepare_runtime_role(postgres_url, password)
    runtime_url = _runtime_url(postgres_url, password)

    admin_engine = create_async_engine(postgres_url)
    runtime_engine = create_async_engine(runtime_url, pool_size=8, max_overflow=0)
    yield _Database(
        admin_factory=async_sessionmaker(admin_engine, expire_on_commit=False),
        runtime_factory=async_sessionmaker(runtime_engine, expire_on_commit=False),
    )
    await runtime_engine.dispose()
    await admin_engine.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def tenants(
    postgres_database: _Database,
) -> AsyncIterator[tuple[TenantContext, TenantContext]]:
    suffix = uuid.uuid4().hex[:20]
    tenant_a = TenantContext(f"rag-a-{suffix}", PlanTier.ENTERPRISE, "key-a")
    tenant_b = TenantContext(f"rag-b-{suffix}", PlanTier.ENTERPRISE, "key-b")
    async with postgres_database.admin_factory() as session, session.begin():
        for tenant in (tenant_a, tenant_b):
            await session.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, :name, :email, 'enterprise', true)"
                ),
                {
                    "id": tenant.tenant_id,
                    "name": tenant.tenant_id,
                    "email": f"{tenant.tenant_id}@example.test",
                },
            )
    yield tenant_a, tenant_b
    async with postgres_database.admin_factory() as session, session.begin():
        for dimension in SUPPORTED_EMBEDDING_DIMENSIONS:
            await session.execute(
                text(f"DELETE FROM knowledge_chunks_{dimension} WHERE tenant_id IN (:a, :b)"),
                {"a": tenant_a.tenant_id, "b": tenant_b.tenant_id},
            )
        await session.execute(
            text("DELETE FROM knowledge_collections WHERE tenant_id IN (:a, :b)"),
            {"a": tenant_a.tenant_id, "b": tenant_b.tenant_id},
        )
        await session.execute(
            text("DELETE FROM tenants WHERE id IN (:a, :b)"),
            {"a": tenant_a.tenant_id, "b": tenant_b.tenant_id},
        )


async def _ingest(
    database: _Database,
    tenant: TenantContext,
    *,
    dimension: int = 768,
    collection_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    embedding: list[float] | None = None,
) -> tuple[str, str]:
    collection_id = collection_id or uuid.uuid4().hex
    store = KnowledgeStore(database.runtime_factory)
    await store.create_collection_async(
        KnowledgeCollection(name=f"collection-{collection_id}", collection_id=collection_id),
        tenant_ctx=tenant,
    )
    chunk_id = await store.ingest_document(
        collection_id=collection_id,
        content="Canonical persisted retrieval evidence",
        metadata=metadata or {"department": "legal"},
        tenant_ctx=tenant,
        embedder=_Embedder(embedding or _embedding(dimension)),
        source_url="https://example.test/handbook",
        source_type="policy",
        source_doc_id=f"document-{collection_id}",
        page_number=7,
        parent_chunk_id=collection_id,
        chunk_level="child",
        window_start=10,
        window_end=20,
        window_id=f"window-{collection_id}",
        hierarchy_level=2,
        is_proposition=True,
        strategy_metadata={"strategy": "sentence-window", "version": 1},
    )
    return collection_id, chunk_id


async def test_restricted_postgres_executes_all_five_core_strategies_with_rls(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    tenant, _ = tenants
    embedding = _embedding(768)
    collection_id, chunk_id = await _ingest(
        postgres_database,
        tenant,
        dimension=768,
        metadata={"department": "legal", "classification": "internal"},
        embedding=embedding,
    )
    evidence: list[dict[str, Any]] = []

    async with (
        postgres_database.runtime_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant.tenant_id),
    ):
        results = await hybrid_search(
            session,
            query="Canonical persisted retrieval evidence",
            query_embedding=embedding,
            collection_id=collection_id,
            top_k=5,
            embedding_dim=768,
            metadata_filter={"department": "legal"},
            strict=True,
            evidence=evidence,
        )

    assert results[0].chunk_id == chunk_id
    assert [leg["component"] for leg in evidence] == [
        "vector",
        "fts",
        "trigram",
        "bm25",
    ]
    assert all(leg["result_count"] >= 1 for leg in evidence)
    assert results[0].source_metadata["department"] == "legal"

    class Provider:
        async def complete(self, request: CompletionRequest) -> CompletionResponse:
            assert request.model == "tenant-model"
            prompt = " ".join(str(message.content) for message in request.messages)
            if "hypothetical document" in prompt:
                content = "Generated canonical persisted retrieval evidence"
            elif "Decompose" in prompt:
                content = '["canonical persisted", "retrieval evidence"]'
            else:
                content = "persisted retrieval evidence\ncanonical evidence retrieval"
            return CompletionResponse(content=content, model=request.model)

    class RecordingEmbedder:
        def __init__(self) -> None:
            self.texts: list[str] = []

        async def embed(self, request: EmbedRequest) -> EmbedResponse:
            self.texts.extend(request.texts)
            return EmbedResponse(embeddings=[list(embedding) for _ in request.texts])

    class RecordingSession:
        def __init__(self, session: AsyncSession) -> None:
            self._session = session

        def begin(self) -> Any:
            return self._session.begin()

        async def execute(
            self,
            statement: Any,
            params: dict[str, Any] | None = None,
        ) -> Any:
            sql = str(statement)
            if "set_config('app.tenant_id'" not in sql:
                current_tenant = (
                    await self._session.execute(
                        text("SELECT current_setting('app.tenant_id', true)")
                    )
                ).scalar_one()
                rls_observations.append((id(self), current_tenant, sql))
            return await self._session.execute(statement, params or {})

        async def scalar(
            self,
            statement: Any,
            params: dict[str, Any] | None = None,
        ) -> Any:
            return await self._session.scalar(statement, params or {})

    active_sessions = 0
    max_active_sessions = 0
    opened_sessions = 0
    session_identities: list[int] = []
    session_objects: list[RecordingSession] = []
    rls_observations: list[tuple[int, str, str]] = []
    embedder = RecordingEmbedder()

    @asynccontextmanager
    async def tracked_factory() -> AsyncIterator[Any]:
        nonlocal active_sessions, max_active_sessions, opened_sessions
        async with postgres_database.runtime_factory() as session:
            recording_session = RecordingSession(session)
            opened_sessions += 1
            active_sessions += 1
            max_active_sessions = max(max_active_sessions, active_sessions)
            session_objects.append(recording_session)
            session_identities.append(id(recording_session))
            try:
                yield recording_session
            finally:
                active_sessions -= 1

    gateway = RetrievalGateway(
        RetrievalDependencies(
            session_factory=tracked_factory,
            collection_authorizer=SQLCollectionAuthorizer(),
            strategy_capabilities=core_strategy_capabilities(),
            embedder=embedder,
            llm_resolver=lambda *_: ResolvedLLM(
                provider=Provider(), model="tenant-model", provider_type="test"
            ),
        )
    )

    def reset_recording() -> None:
        nonlocal max_active_sessions, opened_sessions
        max_active_sessions = 0
        opened_sessions = 0
        session_identities.clear()
        session_objects.clear()
        rls_observations.clear()
        embedder.texts.clear()

    readiness = await gateway.readiness(tenant, strategy_id=RAGStrategy.NAIVE)
    assert readiness.available
    assert readiness.reason == "ready"
    assert active_sessions == 0

    reset_recording()
    naive_result = await gateway.execute(
        tenant,
        collection_id=collection_id,
        query="Canonical persisted retrieval evidence",
        strategy_id=RAGStrategy.NAIVE,
        top_k=5,
        filters={"department": "legal"},
    )
    assert [leg.metadata["component"] for leg in naive_result.retrieval_legs] == ["vector"]
    assert embedder.texts == ["Canonical persisted retrieval evidence"]
    assert opened_sessions == 2
    assert len(set(session_identities)) == 2
    assert all(observation[1] == tenant.tenant_id for observation in rls_observations)

    reset_recording()
    hybrid_result = await gateway.execute(
        tenant,
        collection_id=collection_id,
        query="Canonical persisted retrieval evidence",
        strategy_id=RAGStrategy.HYBRID,
        top_k=5,
        filters={"department": "legal"},
    )
    assert [leg.metadata["component"] for leg in hybrid_result.retrieval_legs] == [
        "vector",
        "fts",
        "trigram",
        "bm25",
    ]
    assert all(
        "component_scores" in trace.detail
        for trace in hybrid_result.strategy_trace
        if trace.action == "retrieval_leg"
    )
    assert hybrid_result.strategy_trace[-1].action == "rrf_merge"
    assert hybrid_result.strategy_trace[-1].detail["rrf_scores"]
    assert opened_sessions == 2
    assert len(set(session_identities)) == 2
    assert all(observation[1] == tenant.tenant_id for observation in rls_observations)

    reset_recording()
    hyde_result = await gateway.execute(
        tenant,
        collection_id=collection_id,
        query="Canonical persisted retrieval evidence",
        strategy_id=RAGStrategy.HYDE,
        top_k=5,
        filters={"department": "legal"},
    )
    assert hyde_result.citations[0].chunk_id == chunk_id
    assert embedder.texts == ["Generated canonical persisted retrieval evidence"]
    assert opened_sessions == 2
    assert len(set(session_identities)) == 2
    assert all(observation[1] == tenant.tenant_id for observation in rls_observations)

    reset_recording()
    multi_hop_result = await gateway.execute(
        tenant,
        collection_id=collection_id,
        query="Compare canonical persisted retrieval evidence",
        strategy_id=RAGStrategy.MULTI_HOP,
        top_k=5,
        filters={"department": "legal"},
    )
    assert embedder.texts == ["canonical persisted", "retrieval evidence"]
    assert {leg.query for leg in multi_hop_result.retrieval_legs} == {
        "canonical persisted",
        "retrieval evidence",
    }
    assert opened_sessions == 3
    assert len(set(session_identities)) == 3
    assert max_active_sessions == 2
    assert all(observation[1] == tenant.tenant_id for observation in rls_observations)

    reset_recording()
    fusion_result = await gateway.execute(
        tenant,
        collection_id=collection_id,
        query="Canonical persisted retrieval evidence",
        strategy_id=RAGStrategy.FUSION,
        top_k=5,
        filters={"department": "legal"},
    )

    assert fusion_result.citations[0].chunk_id == chunk_id
    assert fusion_result.citations[0].metadata["department"] == "legal"
    assert embedder.texts == [
        "Canonical persisted retrieval evidence",
        "persisted retrieval evidence",
        "canonical evidence retrieval",
    ]
    assert len(fusion_result.retrieval_legs) == 3
    assert max_active_sessions == 3
    assert opened_sessions == 4
    assert len(set(session_identities)) == 4
    assert all(observation[1] == tenant.tenant_id for observation in rls_observations)


async def test_ingest_and_search_share_persisted_contract_after_restart(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    tenant, _ = tenants
    collection_id, chunk_id = await _ingest(postgres_database, tenant)

    restarted = KnowledgeStore(postgres_database.runtime_factory)
    assert restarted._data == {}
    results = await restarted.search(
        "persisted retrieval evidence",
        collection_id,
        top_k=3,
        tenant_ctx=tenant,
    )

    assert [result["chunk_id"] for result in results] == [chunk_id]
    assert results[0]["metadata"]["source_doc_id"] == f"document-{collection_id}"


@pytest.mark.parametrize("dimension", SUPPORTED_EMBEDDING_DIMENSIONS)
async def test_ingest_routes_every_supported_dimension_and_persists_retrieval_fields(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
    dimension: int,
) -> None:
    tenant, _ = tenants
    collection_id, chunk_id = await _ingest(
        postgres_database,
        tenant,
        dimension=dimension,
    )

    async with (
        postgres_database.runtime_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant.tenant_id),
    ):
            collection_dimension = (
                await session.execute(
                    text("SELECT embedding_dim FROM knowledge_collections WHERE id = :id"),
                    {"id": collection_id},
                )
            ).scalar_one()
            row = (
                await session.execute(
                    text(
                        f"SELECT vector_dims(embedding), document_id, parent_chunk_id, "
                        f"window_id, hierarchy_level, is_proposition, strategy_metadata, "
                        f"chunk_level, window_start, window_end, metadata, content "
                        f"FROM knowledge_chunks_{dimension} WHERE id = :id"
                    ),
                    {"id": chunk_id},
                )
            ).one()

    assert collection_dimension == dimension
    assert row[0] == dimension
    assert row[1] == f"document-{collection_id}"
    assert row[2:10] == (
        collection_id,
        f"window-{collection_id}",
        2,
        True,
        {"strategy": "sentence-window", "version": 1},
        "child",
        10,
        20,
    )
    assert row[10]["department"] == "legal"
    assert row[11] == "Canonical persisted retrieval evidence"


async def test_restricted_role_rls_and_gateway_reject_foreign_collection(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    tenant_a, tenant_b = tenants
    collection_id, _ = await _ingest(postgres_database, tenant_a)

    async with postgres_database.runtime_factory() as session, session.begin():
        role_flags = (
            await session.execute(
                text(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles "
                    "WHERE rolname = current_user"
                )
            )
        ).one()
        async with sqlalchemy_rls_context(session, tenant_b.tenant_id):
            visible = (
                await session.execute(
                    text("SELECT id FROM knowledge_collections WHERE id = :id"),
                    {"id": collection_id},
                )
            ).scalar_one_or_none()

    assert role_flags == (False, False)
    assert visible is None

    foreign_store = KnowledgeStore(postgres_database.runtime_factory)
    assert await foreign_store.hybrid_search_db(
        "persisted evidence", [], collection_id, tenant_b
    ) == []
    with pytest.raises(KeyError, match="not found"):
        await foreign_store.ingest_document(
            collection_id=collection_id,
            content="foreign write",
            tenant_ctx=tenant_b,
            embedder=_Embedder(_embedding(768)),
        )

    gateway = RetrievalGateway(
        RetrievalDependencies(
            session_factory=postgres_database.runtime_factory,
            collection_authorizer=SQLCollectionAuthorizer(),
            strategy_capabilities={
                RAGStrategy.HYBRID: RetrievalStrategyCapability(adapter=_EmptyAdapter())
            },
        )
    )
    with pytest.raises(CollectionNotFoundError, match=collection_id):
        await gateway.execute(
            tenant_b,
            collection_id=collection_id,
            query="persisted evidence",
            strategy_id=RAGStrategy.HYBRID,
        )


async def test_jsonb_filter_is_parameterized_and_applied_before_top_k(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    tenant, _ = tenants
    collection_id = uuid.uuid4().hex
    wrong_id, _ = await _ingest(
        postgres_database,
        tenant,
        collection_id=collection_id,
        metadata={"department": "finance"},
        embedding=_embedding(768),
    )
    del wrong_id
    store = KnowledgeStore(postgres_database.runtime_factory)
    matching_chunk = await store.ingest_document(
        collection_id=collection_id,
        content="Canonical persisted retrieval evidence",
        metadata={"department": "legal"},
        tenant_ctx=tenant,
        embedder=_Embedder(_embedding(768, second=0.2)),
        source_doc_id=f"matching-{uuid.uuid4().hex}",
    )

    results = await KnowledgeStore(postgres_database.runtime_factory).hybrid_search_db(
        "Canonical persisted retrieval evidence",
        _embedding(768),
        collection_id,
        tenant,
        top_k=1,
        metadata_filter={"department": "legal"},
    )
    injection_results = await KnowledgeStore(
        postgres_database.runtime_factory
    ).hybrid_search_db(
        "Canonical persisted retrieval evidence",
        _embedding(768),
        collection_id,
        tenant,
        top_k=1,
        metadata_filter={"department": "legal' OR true --"},
    )

    assert [result.chunk_id for result in results] == [matching_chunk]
    assert injection_results == []


async def test_failed_chunk_write_rolls_back_counts_and_chunk_state(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    tenant, _ = tenants
    collection_id, original_chunk = await _ingest(postgres_database, tenant)
    store = KnowledgeStore(postgres_database.runtime_factory)

    with pytest.raises(Exception, match=r"vector|NaN|finite"):
        await store.ingest_document(
            collection_id=collection_id,
            content="invalid vector must roll back",
            tenant_ctx=tenant,
            embedder=_Embedder([float("nan")] * 768),
            source_doc_id=f"invalid-{uuid.uuid4().hex}",
        )

    async with (
        postgres_database.runtime_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant.tenant_id),
    ):
            counts = (
                await session.execute(
                    text(
                        "SELECT document_count, chunk_count FROM knowledge_collections "
                        "WHERE id = :id"
                    ),
                    {"id": collection_id},
                )
            ).one()
            chunk_ids = (
                await session.execute(
                    text("SELECT id FROM knowledge_chunks_768 WHERE collection_id = :id"),
                    {"id": collection_id},
                )
            ).scalars().all()

    assert counts == (1, 1)
    assert chunk_ids == [original_chunk]


async def test_sync_from_db_hydrates_only_compatibility_collection_metadata(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    tenant, _ = tenants
    collection_id, chunk_id = await _ingest(postgres_database, tenant)
    compatibility_store = KnowledgeStore(postgres_database.runtime_factory)

    loaded_chunks = await compatibility_store.sync_from_db()

    assert loaded_chunks == 0
    assert compatibility_store._data == {}
    collection = await compatibility_store.get_collection_async(
        collection_id,
        tenant_ctx=tenant,
    )
    assert collection is not None
    listed = await compatibility_store.list_collections_async(tenant_ctx=tenant)
    assert [item.collection_id for item in listed] == [collection_id]
    results = await KnowledgeStore(postgres_database.runtime_factory).search(
        "persisted retrieval evidence",
        collection_id,
        top_k=3,
        tenant_ctx=tenant,
    )
    assert [result["chunk_id"] for result in results] == [chunk_id]


async def test_restricted_restart_lookup_does_not_expose_foreign_collection(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    tenant_a, tenant_b = tenants
    collection_id, _ = await _ingest(postgres_database, tenant_a)
    restarted = KnowledgeStore(postgres_database.runtime_factory)

    assert await restarted.get_collection_async(collection_id, tenant_ctx=tenant_b) is None
    assert await restarted.list_collections_async(tenant_ctx=tenant_b) == []


async def test_missing_or_failing_embedder_leaves_persisted_collection_empty(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    from app.rag.store import EmbeddingProviderUnavailableError

    class _FailingEmbedder:
        async def embed(self, request: EmbedRequest) -> EmbedResponse:
            raise RuntimeError("private provider failure")

    tenant, _ = tenants
    for embedder in (None, _FailingEmbedder()):
        collection_id = uuid.uuid4().hex
        store = KnowledgeStore(postgres_database.runtime_factory)
        await store.create_collection_async(
            KnowledgeCollection(name=f"embedder-{collection_id}", collection_id=collection_id),
            tenant_ctx=tenant,
        )
        with pytest.raises(EmbeddingProviderUnavailableError):
            await store.ingest_document(
                collection_id=collection_id,
                content="must not be written",
                tenant_ctx=tenant,
                embedder=embedder,
            )
        async with (
            postgres_database.runtime_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant.tenant_id),
        ):
            assert (
                await session.execute(
                    text(
                        "SELECT count(*) FROM knowledge_chunks_768 "
                        "WHERE collection_id = :id"
                    ),
                    {"id": collection_id},
                )
            ).scalar_one() == 0


async def test_empty_batch_still_authorizes_collection_ownership(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    tenant_a, tenant_b = tenants
    collection_id, _ = await _ingest(postgres_database, tenant_a)
    restarted = KnowledgeStore(postgres_database.runtime_factory)

    assert await restarted.ingest_chunks_async(
        [], collection_id=collection_id, tenant_ctx=tenant_a
    ) == []
    with pytest.raises(KeyError, match="not found"):
        await restarted.ingest_chunks_async(
            [], collection_id=collection_id, tenant_ctx=tenant_b
        )
    with pytest.raises(KeyError, match="not found"):
        await restarted.ingest_chunks_async(
            [], collection_id=uuid.uuid4().hex, tenant_ctx=tenant_a
        )


async def test_3072_vector_query_uses_halfvec_hnsw_index(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    tenant, _ = tenants
    collection_id, chunk_id = await _ingest(
        postgres_database,
        tenant,
        dimension=3072,
    )
    store = KnowledgeStore(postgres_database.runtime_factory)

    results = await store.hybrid_search_db(
        "Canonical persisted retrieval evidence",
        _embedding(3072),
        collection_id,
        tenant,
        top_k=1,
    )
    assert [result.chunk_id for result in results] == [chunk_id]

    async with (
        postgres_database.runtime_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant.tenant_id),
    ):
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(
            (
                await session.execute(
                    text("""
                        EXPLAIN SELECT id FROM knowledge_chunks_3072
                        WHERE collection_id = :collection_id
                        ORDER BY embedding::halfvec(3072)
                                 <=> CAST(:embedding AS halfvec(3072))
                        LIMIT 1
                    """),
                    {
                        "collection_id": collection_id,
                        "embedding": str(_embedding(3072)),
                    },
                )
                ).scalars()
        )
        index_valid = (
            await session.execute(
                text("""
                    SELECT index.indisvalid
                    FROM pg_index AS index
                    JOIN pg_class AS relation ON relation.oid = index.indexrelid
                    WHERE relation.relname = 'idx_knowledge_chunks_3072_vector_halfvec'
                """)
            )
        ).scalar_one()
    assert "halfvec(3072)" in plan
    assert index_valid is True


async def test_concurrent_gateway_operations_use_independent_restricted_sessions(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    tenant, _ = tenants
    collection_id, _ = await _ingest(postgres_database, tenant)
    observations: list[tuple[int, str, int]] = []

    class _ConcurrentAdapter:
        async def execute(
            self,
            request: RAGExecutionRequest,
            context: RetrievalExecutionContext,
        ) -> RAGExecutionResult:
            async def leg(session: AsyncSession) -> None:
                row = (
                    await session.execute(
                        text(
                            "SELECT pg_backend_pid(), current_setting('app.tenant_id'), "
                            "(SELECT count(*) FROM knowledge_collections WHERE id = :id), "
                            "pg_sleep(0.05)"
                        ),
                        {"id": collection_id},
                    )
                ).one()
                observations.append((row[0], row[1], row[2]))

            await asyncio.gather(*(context.run_db_operation(leg) for _ in range(3)))
            return RAGExecutionResult(
                requested_strategy_id=request.requested_strategy_id,
                resolved_strategy_id=context.strategy,
            )

    gateway = RetrievalGateway(
        RetrievalDependencies(
            session_factory=postgres_database.runtime_factory,
            collection_authorizer=SQLCollectionAuthorizer(),
            strategy_capabilities={
                RAGStrategy.FUSION: RetrievalStrategyCapability(adapter=_ConcurrentAdapter())
            },
        )
    )
    await gateway.execute(
        tenant,
        collection_id=collection_id,
        query="persisted evidence",
        strategy_id=RAGStrategy.FUSION,
    )

    assert len({pid for pid, _, _ in observations}) == 3
    assert observations == [(pid, tenant.tenant_id, 1) for pid, _, _ in observations]


async def test_atomic_batch_failure_rolls_back_every_chunk(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    from app.rag.models import Chunk

    tenant, _ = tenants
    collection_id = uuid.uuid4().hex
    store = KnowledgeStore(postgres_database.runtime_factory)
    await store.create_collection_async(
        KnowledgeCollection(name=f"atomic-{collection_id}", collection_id=collection_id),
        tenant_ctx=tenant,
    )
    chunks = [
        Chunk("document-1", "valid", _embedding(768), 0),
        Chunk("document-1", "invalid", [float("nan")] * 768, 1),
    ]

    with pytest.raises(DBAPIError):
        await store.ingest_chunks_async(
            chunks,
            collection_id=collection_id,
            tenant_ctx=tenant,
        )

    async with (
        postgres_database.runtime_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant.tenant_id),
    ):
        chunk_count = (
            await session.execute(
                text("SELECT count(*) FROM knowledge_chunks_768 WHERE collection_id = :id"),
                {"id": collection_id},
            )
        ).scalar_one()
        collection_counts = (
            await session.execute(
                text(
                    "SELECT document_count, chunk_count FROM knowledge_collections "
                    "WHERE id = :id"
                ),
                {"id": collection_id},
            )
        ).one()

    assert chunk_count == 0
    assert collection_counts == (0, 0)
    assert store._data[(tenant.tenant_id, collection_id)].chunks == []


async def test_orchestrated_batch_failure_rolls_back_without_ids(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    from app.ingestion.orchestrator import IngestionOrchestrator

    class _PartiallyInvalidEmbedder:
        async def embed(self, request: EmbedRequest) -> EmbedResponse:
            return EmbedResponse(
                embeddings=[_embedding(768), [float("nan")] * 768]
            )

    tenant, _ = tenants
    collection_id = uuid.uuid4().hex
    store = KnowledgeStore(postgres_database.runtime_factory)
    await store.create_collection_async(
        KnowledgeCollection(
            name=f"orchestrated-atomic-{collection_id}",
            collection_id=collection_id,
        ),
        tenant_ctx=tenant,
    )
    orchestrator = IngestionOrchestrator(
        knowledge_store=store,
        embedder=_PartiallyInvalidEmbedder(),
    )

    with pytest.raises(DBAPIError):
        await orchestrator.ingest(
            "First sufficiently long paragraph for atomic ingestion.\n\n"
            "Second sufficiently long paragraph for atomic ingestion.",
            content_type="text",
            collection_id=collection_id,
            tenant_ctx=tenant,
        )

    async with (
        postgres_database.runtime_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant.tenant_id),
    ):
        rows = (
            await session.execute(
                text(
                    "SELECT count(*) FROM knowledge_chunks_768 "
                    "WHERE collection_id = :id"
                ),
                {"id": collection_id},
            )
        ).scalar_one()
    assert rows == 0
    assert store._data[(tenant.tenant_id, collection_id)].chunks == []


async def test_orchestrated_chunks_share_document_count_and_delete_together(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    from app.ingestion.orchestrator import IngestionOrchestrator

    tenant, _ = tenants
    collection_id = uuid.uuid4().hex
    store = KnowledgeStore(postgres_database.runtime_factory)
    await store.create_collection_async(
        KnowledgeCollection(
            name=f"orchestrated-document-{collection_id}",
            collection_id=collection_id,
        ),
        tenant_ctx=tenant,
    )
    result = await IngestionOrchestrator(
        knowledge_store=store,
        embedder=_Embedder(_embedding(768)),
    ).ingest(
        "First sufficiently long paragraph for one document.\n\n"
        "Second sufficiently long paragraph for the same document.",
        content_type="text",
        collection_id=collection_id,
        tenant_ctx=tenant,
    )

    async with (
        postgres_database.runtime_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant.tenant_id),
    ):
        rows = (
            await session.execute(
                text(
                    "SELECT document_id, chunk_index FROM knowledge_chunks_768 "
                    "WHERE collection_id = :id ORDER BY chunk_index"
                ),
                {"id": collection_id},
            )
        ).all()
        counts = (
            await session.execute(
                text(
                    "SELECT document_count, chunk_count FROM knowledge_collections "
                    "WHERE id = :id"
                ),
                {"id": collection_id},
            )
        ).one()

    assert result.chunks_created == 2
    assert len({row[0] for row in rows}) == 1
    assert [row[1] for row in rows] == [0, 1]
    assert counts == (1, 2)
    assert await store.delete_document_async(
        rows[0][0],
        collection_id=collection_id,
        tenant_ctx=tenant,
    ) == 2


async def test_mixed_structured_documents_preserve_identity_across_restart_and_delete(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    from app.api.knowledge import _ingest_chunks_from_source
    from app.providers.fake import FakeProvider

    tenant, _ = tenants
    collection_id = uuid.uuid4().hex
    store = KnowledgeStore(postgres_database.runtime_factory)
    await store.create_collection_async(
        KnowledgeCollection(
            name=f"mixed-documents-{collection_id}",
            collection_id=collection_id,
        ),
        tenant_ctx=tenant,
    )
    await _ingest_chunks_from_source(
        store,
        [
            {"content": "Document A first chunk", "source_doc_id": "doc-a"},
            {"content": "Document B first chunk", "source_doc_id": "doc-b"},
            {"content": "Document A second chunk", "source_doc_id": "doc-a"},
            {"content": "Document B second chunk", "source_doc_id": "doc-b"},
        ],
        collection_id,
        tenant,
        FakeProvider(embed_dim=768),
    )

    restarted = KnowledgeStore(postgres_database.runtime_factory)
    collection = await restarted.get_collection_async(collection_id, tenant_ctx=tenant)
    assert collection is not None
    assert collection.document_count == 2
    async with (
        postgres_database.runtime_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant.tenant_id),
    ):
        rows = (
            await session.execute(
                text(
                    "SELECT document_id, chunk_index FROM knowledge_chunks_768 "
                    "WHERE collection_id = :id ORDER BY document_id, chunk_index"
                ),
                {"id": collection_id},
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        ("doc-a", 0),
        ("doc-a", 1),
        ("doc-b", 0),
        ("doc-b", 1),
    ]

    assert await restarted.delete_document_async(
        "doc-a",
        collection_id=collection_id,
        tenant_ctx=tenant,
    ) == 2
    async with (
        postgres_database.runtime_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant.tenant_id),
    ):
        remaining = (
            await session.execute(
                text(
                    "SELECT document_id, chunk_index FROM knowledge_chunks_768 "
                    "WHERE collection_id = :id ORDER BY chunk_index"
                ),
                {"id": collection_id},
            )
        ).all()
        counts = (
            await session.execute(
                text(
                    "SELECT document_count, chunk_count FROM knowledge_collections "
                    "WHERE id = :id"
                ),
                {"id": collection_id},
            )
        ).one()
    assert [tuple(row) for row in remaining] == [("doc-b", 0), ("doc-b", 1)]
    assert counts == (1, 2)


async def test_repository_ingestion_job_status_survives_restart_and_is_tenant_scoped(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    tenant_a, tenant_b = tenants
    collection_id = uuid.uuid4().hex
    store = KnowledgeStore(postgres_database.runtime_factory)
    await store.create_collection_async(
        KnowledgeCollection(name=f"jobs-{collection_id}", collection_id=collection_id),
        tenant_ctx=tenant_a,
    )
    job_id = await store.create_ingestion_job_async(
        collection_id=collection_id,
        source_url="https://github.com/example/repository",
        source_type="repository",
        title="example/repository",
        tenant_ctx=tenant_a,
    )
    await store.update_ingestion_job_async(
        job_id,
        status="failed",
        chunk_count=0,
        error_message="Repository ingestion failed",
        tenant_ctx=tenant_a,
    )

    restarted = KnowledgeStore(postgres_database.runtime_factory)
    status = await restarted.get_ingestion_job_async(job_id, tenant_ctx=tenant_a)
    assert status == {
        "job_id": job_id,
        "collection_id": collection_id,
        "status": "failed",
        "chunk_count": 0,
        "error_message": "Repository ingestion failed",
        "source_url": "https://github.com/example/repository",
    }
    assert await restarted.get_ingestion_job_async(job_id, tenant_ctx=tenant_b) is None


async def test_repository_completion_status_failure_rolls_back_chunks_and_counters(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    from app.rag.models import Chunk

    tenant, _ = tenants
    collection_id = uuid.uuid4().hex
    store = KnowledgeStore(postgres_database.runtime_factory)
    await store.create_collection_async(
        KnowledgeCollection(name=f"atomic-job-{collection_id}", collection_id=collection_id),
        tenant_ctx=tenant,
    )
    job_id = await store.create_ingestion_job_async(
        collection_id=collection_id,
        source_url="https://github.com/example/repository",
        source_type="repository",
        title="repository",
        tenant_ctx=tenant,
    )
    await store.update_ingestion_job_async(
        job_id,
        status="running",
        chunk_count=0,
        error_message=None,
        tenant_ctx=tenant,
    )
    chunks = [
        Chunk(
            "doc-a",
            "repository chunk",
            _embedding(768),
            0,
            metadata={"repo_url": "https://github.com/example/repository"},
        )
    ]

    with pytest.raises(KeyError, match="job"):
        await store.ingest_repository_chunks_async(
            chunks,
            job_id=job_id,
            collection_id=collection_id,
            source_url="https://github.com/example/repository",
            lease_owner="wrong-completion-owner",
            tenant_ctx=tenant,
        )

    async with (
        postgres_database.runtime_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant.tenant_id),
    ):
        chunk_count = (
            await session.execute(
                text("SELECT count(*) FROM knowledge_chunks_768 WHERE collection_id = :id"),
                {"id": collection_id},
            )
        ).scalar_one()
        counters = (
            await session.execute(
                text(
                    "SELECT document_count, chunk_count FROM knowledge_collections "
                    "WHERE id = :id"
                ),
                {"id": collection_id},
            )
        ).one()
    assert chunk_count == 0
    assert counters == (0, 0)


async def test_repository_chunks_are_associated_with_job_and_complete_atomically(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    from app.rag.models import Chunk

    tenant, _ = tenants
    collection_id = uuid.uuid4().hex
    store = KnowledgeStore(postgres_database.runtime_factory)
    await store.create_collection_async(
        KnowledgeCollection(name=f"associated-job-{collection_id}", collection_id=collection_id),
        tenant_ctx=tenant,
    )
    job_id = await store.create_ingestion_job_async(
        collection_id=collection_id,
        source_url="https://github.com/example/repository",
        source_type="repository",
        title="repository",
        tenant_ctx=tenant,
    )
    await store.update_ingestion_job_async(
        job_id,
        status="running",
        chunk_count=0,
        error_message=None,
        tenant_ctx=tenant,
    )
    chunks = [
        Chunk(
            "doc-a", "first repository chunk", _embedding(768), 0,
            metadata={"repo_url": "https://github.com/example/repository"},
        ),
        Chunk(
            "doc-b", "second repository chunk", _embedding(768), 0,
            metadata={"repo_url": "https://github.com/example/repository"},
        ),
    ]

    await store.ingest_repository_chunks_async(
        chunks,
        job_id=job_id,
        collection_id=collection_id,
        source_url="https://github.com/example/repository",
        lease_owner=f"legacy-{job_id}",
        tenant_ctx=tenant,
    )

    status = await store.get_ingestion_job_async(job_id, tenant_ctx=tenant)
    assert status is not None
    assert status["status"] == "completed"
    assert status["chunk_count"] == 2
    async with (
        postgres_database.runtime_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant.tenant_id),
    ):
        associations = (
            await session.execute(
                text(
                    "SELECT strategy_metadata->>'ingestion_job_id' "
                    "FROM knowledge_chunks_768 WHERE collection_id = :id ORDER BY id"
                ),
                {"id": collection_id},
            )
        ).scalars().all()
    assert associations == [job_id, job_id]


async def test_stale_running_repository_job_is_reconciled_after_restart(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    tenant, _ = tenants
    collection_id = uuid.uuid4().hex
    store = KnowledgeStore(postgres_database.runtime_factory)
    await store.create_collection_async(
        KnowledgeCollection(name=f"stale-job-{collection_id}", collection_id=collection_id),
        tenant_ctx=tenant,
    )
    job_id = await store.create_ingestion_job_async(
        collection_id=collection_id,
        source_url="https://github.com/example/repository",
        source_type="repository",
        title="repository",
        tenant_ctx=tenant,
    )
    await store.update_ingestion_job_async(
        job_id,
        status="running",
        chunk_count=0,
        error_message=None,
        tenant_ctx=tenant,
    )
    async with postgres_database.admin_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE knowledge_documents SET lease_expires_at = now() - interval '1 day' "
                "WHERE id = :id"
            ),
            {"id": job_id},
        )

    restarted = KnowledgeStore(postgres_database.runtime_factory)
    reconciled = await restarted.reconcile_stale_ingestion_jobs_async(
        tenant_ctx=tenant,
        stale_after_seconds=60,
    )
    status = await restarted.get_ingestion_job_async(job_id, tenant_ctx=tenant)

    assert reconciled == 1
    assert status is not None
    assert status["status"] == "failed"
    assert status["error_message"] == "Repository ingestion interrupted"


async def test_repository_job_rejects_collection_and_source_mismatch(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    from app.rag.models import Chunk

    tenant, _ = tenants
    store = KnowledgeStore(postgres_database.runtime_factory)
    collection_a = uuid.uuid4().hex
    collection_b = uuid.uuid4().hex
    for collection_id in (collection_a, collection_b):
        await store.create_collection_async(
            KnowledgeCollection(name=f"scope-{collection_id}", collection_id=collection_id),
            tenant_ctx=tenant,
        )
    source_url = "https://github.com/example/repository"
    job_id = await store.create_ingestion_job_async(
        collection_id=collection_a,
        source_url=source_url,
        source_type="repository",
        title="repository",
        tenant_ctx=tenant,
    )
    lease_owner = "scope-worker"
    assert await store.claim_ingestion_job_async(
        job_id,
        collection_id=collection_a,
        source_url=source_url,
        lease_owner=lease_owner,
        lease_seconds=60,
        tenant_ctx=tenant,
    )
    chunks = [
        Chunk(
            "doc-a", "scoped chunk", _embedding(768), 0,
            metadata={"repo_url": source_url},
        )
    ]

    with pytest.raises(DBAPIError):
        async with (
            postgres_database.runtime_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant.tenant_id),
        ):
            await session.execute(
                text("""
                    INSERT INTO knowledge_chunks_768
                        (id, tenant_id, collection_id, document_id, chunk_index,
                         content, content_hash, embedding, metadata,
                         strategy_metadata, ingestion_job_id)
                    VALUES
                        (:id, :tenant_id, :collection_id, 'doc-tamper', 0,
                         'tamper', 'hash', CAST(:embedding AS vector),
                         CAST(:metadata AS jsonb), CAST(:strategy AS jsonb), :job_id)
                """),
                {
                    "id": uuid.uuid4().hex,
                    "tenant_id": tenant.tenant_id,
                    "collection_id": collection_a,
                    "embedding": str(_embedding(768)),
                    "metadata": '{"repo_url":"https://github.com/example/other"}',
                    "strategy": f'{{"ingestion_job_id":"{job_id}"}}',
                    "job_id": job_id,
                },
            )
    with pytest.raises(DBAPIError):
        await store.ingest_repository_chunks_async(
            chunks,
            job_id=job_id,
            collection_id=collection_b,
            source_url=source_url,
            lease_owner=lease_owner,
            tenant_ctx=tenant,
        )
    with pytest.raises(KeyError, match="job"):
        await store.ingest_repository_chunks_async(
            chunks,
            job_id=job_id,
            collection_id=collection_a,
            source_url="https://github.com/example/other",
            lease_owner=lease_owner,
            tenant_ctx=tenant,
        )


async def test_concurrent_repository_completion_and_cancel_preserve_one_terminal_state(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    from app.rag.models import Chunk

    tenant, _ = tenants
    collection_id = uuid.uuid4().hex
    source_url = "https://github.com/example/repository"
    lease_owner = "race-worker"
    store = KnowledgeStore(postgres_database.runtime_factory)
    await store.create_collection_async(
        KnowledgeCollection(name=f"race-{collection_id}", collection_id=collection_id),
        tenant_ctx=tenant,
    )
    job_id = await store.create_ingestion_job_async(
        collection_id=collection_id,
        source_url=source_url,
        source_type="repository",
        title="repository",
        tenant_ctx=tenant,
    )
    assert await store.claim_ingestion_job_async(
        job_id,
        collection_id=collection_id,
        source_url=source_url,
        lease_owner=lease_owner,
        lease_seconds=60,
        tenant_ctx=tenant,
    )

    completion, cancellation = await asyncio.gather(
        store.ingest_repository_chunks_async(
            [
                Chunk(
                    "doc-a", "race chunk", _embedding(768), 0,
                    metadata={"repo_url": source_url},
                )
            ],
            job_id=job_id,
            collection_id=collection_id,
            source_url=source_url,
            lease_owner=lease_owner,
            tenant_ctx=tenant,
        ),
        store.fail_ingestion_job_async(
            job_id,
            lease_owner=lease_owner,
            error_message="Repository ingestion cancelled",
            tenant_ctx=tenant,
        ),
        return_exceptions=True,
    )
    status = await store.get_ingestion_job_async(job_id, tenant_ctx=tenant)
    assert status is not None
    assert status["status"] in {"completed", "failed"}
    if status["status"] == "completed":
        assert status["chunk_count"] == 1
        assert cancellation == "completed"
    else:
        assert status["chunk_count"] == 0
        assert isinstance(completion, Exception)


async def test_reconciliation_preserves_live_lease_and_fails_stale_queue(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    tenant, _ = tenants
    store = KnowledgeStore(postgres_database.runtime_factory)
    collection_id = uuid.uuid4().hex
    source_url = "https://github.com/example/repository"
    await store.create_collection_async(
        KnowledgeCollection(name=f"live-{collection_id}", collection_id=collection_id),
        tenant_ctx=tenant,
    )
    live_job = await store.create_ingestion_job_async(
        collection_id=collection_id,
        source_url=source_url,
        source_type="repository",
        title="live",
        tenant_ctx=tenant,
    )
    queued_job = await store.create_ingestion_job_async(
        collection_id=collection_id,
        source_url="https://github.com/example/queued",
        source_type="repository",
        title="queued",
        tenant_ctx=tenant,
    )
    assert await store.claim_ingestion_job_async(
        live_job,
        collection_id=collection_id,
        source_url=source_url,
        lease_owner="live-worker",
        lease_seconds=300,
        tenant_ctx=tenant,
    )
    async with postgres_database.admin_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE knowledge_documents SET created_at = now() - interval '1 day' "
                "WHERE id = :id"
            ),
            {"id": queued_job},
        )

    reconciled = await store.reconcile_stale_ingestion_jobs_async(
        tenant_ctx=tenant,
        stale_after_seconds=60,
    )
    live_status = await store.get_ingestion_job_async(live_job, tenant_ctx=tenant)
    queued_status = await store.get_ingestion_job_async(queued_job, tenant_ctx=tenant)

    assert reconciled == 1
    assert live_status is not None and live_status["status"] == "running"
    assert queued_status is not None and queued_status["status"] == "failed"


async def test_repository_chunk_job_association_is_immutable_against_direct_sql(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    from app.rag.models import Chunk

    tenant, _ = tenants
    collection_id = uuid.uuid4().hex
    source_url = "https://github.com/example/repository"
    store = KnowledgeStore(postgres_database.runtime_factory)
    await store.create_collection_async(
        KnowledgeCollection(name=f"immutable-{collection_id}", collection_id=collection_id),
        tenant_ctx=tenant,
    )
    job_id = await store.create_ingestion_job_async(
        collection_id=collection_id,
        source_url=source_url,
        source_type="repository",
        title="repository",
        tenant_ctx=tenant,
    )
    assert await store.claim_ingestion_job_async(
        job_id,
        collection_id=collection_id,
        source_url=source_url,
        lease_owner="immutable-worker",
        lease_seconds=60,
        tenant_ctx=tenant,
    )
    chunk = Chunk(
        "doc-a",
        "immutable association",
        _embedding(768),
        0,
        metadata={"repo_url": source_url},
    )
    await store.ingest_repository_chunks_async(
        [chunk],
        job_id=job_id,
        collection_id=collection_id,
        source_url=source_url,
        lease_owner="immutable-worker",
        tenant_ctx=tenant,
    )

    for new_value in (None, uuid.uuid4().hex):
        with pytest.raises(DBAPIError):
            async with (
                postgres_database.runtime_factory() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant.tenant_id),
            ):
                await session.execute(
                    text(
                        "UPDATE knowledge_chunks_768 SET ingestion_job_id = :job_id "
                        "WHERE id = :chunk_id"
                    ),
                    {"job_id": new_value, "chunk_id": chunk.chunk_id},
                )


async def test_expired_repository_lease_cannot_be_heartbeated_or_resurrected(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    tenant, _ = tenants
    collection_id = uuid.uuid4().hex
    source_url = "https://github.com/example/repository"
    store = KnowledgeStore(postgres_database.runtime_factory)
    await store.create_collection_async(
        KnowledgeCollection(name=f"expired-{collection_id}", collection_id=collection_id),
        tenant_ctx=tenant,
    )
    job_id = await store.create_ingestion_job_async(
        collection_id=collection_id,
        source_url=source_url,
        source_type="repository",
        title="repository",
        tenant_ctx=tenant,
    )
    assert await store.claim_ingestion_job_async(
        job_id,
        collection_id=collection_id,
        source_url=source_url,
        lease_owner="expired-worker",
        lease_seconds=60,
        tenant_ctx=tenant,
    )
    async with postgres_database.admin_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE knowledge_documents SET lease_expires_at = now() - interval '1 second' "
                "WHERE id = :id"
            ),
            {"id": job_id},
        )

    assert not await store.heartbeat_ingestion_job_async(
        job_id,
        lease_owner="expired-worker",
        lease_seconds=60,
        tenant_ctx=tenant,
    )
    assert await store.reconcile_stale_ingestion_jobs_async(
        tenant_ctx=tenant,
        stale_after_seconds=60,
    ) == 1
    status = await store.get_ingestion_job_async(job_id, tenant_ctx=tenant)
    assert status is not None and status["status"] == "failed"


async def test_zero_chunk_repository_completion_clears_all_lease_fields(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    tenant, _ = tenants
    collection_id = uuid.uuid4().hex
    source_url = "https://github.com/example/empty"
    store = KnowledgeStore(postgres_database.runtime_factory)
    await store.create_collection_async(
        KnowledgeCollection(name=f"empty-{collection_id}", collection_id=collection_id),
        tenant_ctx=tenant,
    )
    job_id = await store.create_ingestion_job_async(
        collection_id=collection_id,
        source_url=source_url,
        source_type="repository",
        title="empty",
        tenant_ctx=tenant,
    )
    assert await store.claim_ingestion_job_async(
        job_id,
        collection_id=collection_id,
        source_url=source_url,
        lease_owner="empty-worker",
        lease_seconds=60,
        tenant_ctx=tenant,
    )
    assert await store.ingest_repository_chunks_async(
        [],
        job_id=job_id,
        collection_id=collection_id,
        source_url=source_url,
        lease_owner="empty-worker",
        tenant_ctx=tenant,
    ) == []
    async with (
        postgres_database.runtime_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant.tenant_id),
    ):
        row = (
            await session.execute(
                text(
                    "SELECT status, lease_owner, lease_expires_at, heartbeat_at "
                    "FROM knowledge_documents WHERE id = :id"
                ),
                {"id": job_id},
            )
        ).one()
    assert tuple(row) == ("completed", None, None, None)


async def test_collection_delete_is_tenant_scoped_and_persisted(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    tenant_a, tenant_b = tenants
    store = KnowledgeStore(postgres_database.runtime_factory)
    collection_id, _ = await _ingest(postgres_database, tenant_a)

    assert not await store.delete_collection_async(collection_id, tenant_ctx=tenant_b)
    assert await store.get_collection_async(collection_id, tenant_ctx=tenant_a) is not None
    assert await store.delete_collection_async(collection_id, tenant_ctx=tenant_a)
    assert await store.get_collection_async(collection_id, tenant_ctx=tenant_a) is None
    async with postgres_database.admin_factory() as session:
        persisted = (
            await session.execute(
                text(
                    "SELECT (SELECT count(*) FROM knowledge_collections WHERE id = :id), "
                    "(SELECT count(*) FROM knowledge_chunks_768 WHERE collection_id = :id)"
                ),
                {"id": collection_id},
            )
        ).one()
    assert tuple(persisted) == (0, 0)


@pytest.mark.parametrize("dimension", SUPPORTED_EMBEDDING_DIMENSIONS)
async def test_delete_and_parent_expansion_route_to_collection_dimension(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
    dimension: int,
) -> None:
    tenant, _ = tenants
    collection_id, chunk_id = await _ingest(
        postgres_database,
        tenant,
        dimension=dimension,
    )
    restarted = KnowledgeStore(postgres_database.runtime_factory)

    expanded = await restarted.expand_to_parents([chunk_id], collection_id, tenant)
    deleted = await restarted.delete_document_async(
        f"document-{collection_id}",
        collection_id=collection_id,
        tenant_ctx=tenant,
    )

    assert [item.chunk_id for item in expanded] == [chunk_id]
    assert deleted == 1
    async with (
        postgres_database.runtime_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant.tenant_id),
    ):
        remaining = (
            await session.execute(
                text(
                    f"SELECT count(*) FROM knowledge_chunks_{dimension} "
                    "WHERE collection_id = :id"
                ),
                {"id": collection_id},
            )
        ).scalar_one()
    assert remaining == 0


async def test_restricted_role_enforces_rls_on_every_chunk_table(
    postgres_database: _Database,
    tenants: tuple[TenantContext, TenantContext],
) -> None:
    tenant_a, tenant_b = tenants
    collections: dict[int, str] = {}
    for dimension in SUPPORTED_EMBEDDING_DIMENSIONS:
        collection_id, _ = await _ingest(
            postgres_database,
            tenant_a,
            dimension=dimension,
        )
        collections[dimension] = collection_id

    async with postgres_database.runtime_factory() as session, session.begin():
        role = (
            await session.execute(
                text(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles "
                    "WHERE rolname = current_user"
                )
            )
        ).one()
        table_security = (
            await session.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname = ANY(:tables) ORDER BY relname"
                ),
                {
                    "tables": [
                        "knowledge_collections",
                        *(f"knowledge_chunks_{d}" for d in SUPPORTED_EMBEDDING_DIMENSIONS),
                    ]
                },
            )
        ).all()
        policies = (
            await session.execute(
                text(
                    "SELECT tablename, with_check FROM pg_policies "
                    "WHERE tablename = ANY(:tables)"
                ),
                {"tables": [f"knowledge_chunks_{d}" for d in SUPPORTED_EMBEDDING_DIMENSIONS]},
            )
        ).all()

    assert role == (False, False)
    assert len(table_security) == 5
    assert all(row[1] and row[2] for row in table_security)
    assert {row[0] for row in policies} == {
        f"knowledge_chunks_{dimension}" for dimension in SUPPORTED_EMBEDDING_DIMENSIONS
    }
    assert all("knowledge_collections" in str(row[1]) for row in policies)

    for dimension, collection_id in collections.items():
        table = f"knowledge_chunks_{dimension}"
        async with (
            postgres_database.runtime_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_b.tenant_id),
        ):
            assert (
                await session.execute(
                    text(f"SELECT count(*) FROM {table} WHERE collection_id = :id"),
                    {"id": collection_id},
                )
            ).scalar_one() == 0

        with pytest.raises(DBAPIError):
            async with (
                postgres_database.runtime_factory() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_b.tenant_id),
            ):
                await session.execute(
                    text(f"""
                        INSERT INTO {table}
                            (id, tenant_id, collection_id, document_id, chunk_index,
                             content, content_hash, embedding)
                        VALUES
                            (:id, :tenant_id, :collection_id, :document_id, 0,
                             'foreign', :hash, CAST(:embedding AS vector))
                    """),
                    {
                        "id": uuid.uuid4().hex,
                        "tenant_id": tenant_b.tenant_id,
                        "collection_id": collection_id,
                        "document_id": uuid.uuid4().hex,
                        "hash": uuid.uuid4().hex,
                        "embedding": str(_embedding(dimension)),
                    },
                )
