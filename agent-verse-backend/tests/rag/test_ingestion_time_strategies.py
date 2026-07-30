"""Persisted ingestion-time coverage for RAPTOR and agentic chunking."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.ingestion.orchestrator import IngestionOrchestrator
from app.providers.base import (
    CompletionRequest,
    CompletionResponse,
    EmbedRequest,
    EmbedResponse,
)
from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern
from app.rag.agentic.patterns.raptor import RAPTORPattern
from app.rag.contracts import RAGStrategy
from app.rag.gateway import (
    RetrievalDependencies,
    RetrievalGateway,
    SQLCollectionAuthorizer,
    core_strategy_capabilities,
)
from app.rag.indexing import (
    IndexingDependency,
    RAGIndexingConfig,
    RAGIndexingPipeline,
    RAGIndexRecord,
)
from app.rag.models import KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext


class RecordingProvider:
    def __init__(self) -> None:
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        prompt = str(request.messages[-1].content)
        payload = json.loads(prompt.split("\n", maxsplit=1)[1])
        system = str(request.messages[0].content).casefold()
        if "proposition" in system:
            content = json.dumps(
                [
                    [f"{chunk} is an indexed proposition."]
                    for chunk in payload
                ]
            )
        else:
            content = json.dumps(
                [f"Summary of {' '.join(group)[:80]}" for group in payload]
            )
        return CompletionResponse(content=content, model=request.model)


class RecordingEmbedder:
    def __init__(self, dimension: int = 7) -> None:
        self.dimension = dimension
        self.requests: list[EmbedRequest] = []

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        self.requests.append(request)
        return EmbedResponse(
            embeddings=[
                [float(index + 1), *([0.0] * (self.dimension - 1))]
                for index, _ in enumerate(request.texts)
            ]
        )


class RecordingStore:
    def __init__(self) -> None:
        self._db = object()
        self.records: list[RAGIndexRecord] = []
        self.persist_calls = 0

    async def ingest_chunks_async(self, *args: Any, **kwargs: Any) -> list[str]:
        raise AssertionError("configured strategy ingestion must use RAGIndexingPipeline")

    async def persist_index_records(
        self,
        records: list[RAGIndexRecord],
        *,
        collection_id: str,
        tenant_ctx: TenantContext,
    ) -> list[str]:
        del collection_id, tenant_ctx
        self.persist_calls += 1
        document_ids = {record.document_id for record in records}
        self.records[:] = [
            record for record in self.records if record.document_id not in document_ids
        ]
        self.records.extend(records)
        return [record.chunk_id for record in records]

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
        del query, query_embedding, collection_id, tenant_ctx
        matching = [
            record
            for record in self.records
            if record.strategy is strategy
            and (
                strategy is RAGStrategy.RAPTOR
                or record.is_proposition
            )
        ]
        if strategy is not RAGStrategy.AGENTIC_CHUNKING:
            return [record.to_search_result(score=1.0) for record in matching[:top_k]]

        from app.rag.engine import (
            ParentWindowCitation,
            RetrievalResult,
            expand_agentic_parent_results,
        )

        by_id = {record.chunk_id: record for record in self.records}
        candidates = matching[: min(top_k * 4, 100)]
        expanded = expand_agentic_parent_results(
            [
                RetrievalResult(
                    chunk_id=record.chunk_id,
                    content=record.content,
                    score=1.0,
                    source_metadata=record.to_search_result(score=1.0)["metadata"],
                )
                for record in candidates
            ],
            {
                record.chunk_id: ParentWindowCitation(
                    by_id[record.parent_chunk_id].chunk_id,
                    by_id[record.parent_chunk_id].content,
                )
                for record in candidates
                if record.parent_chunk_id in by_id
            },
            top_k=top_k,
        )
        return [
            {
                "chunk_id": result.chunk_id,
                "content": result.content,
                "score": result.score,
                "metadata": result.source_metadata,
                "citation_chunk_id": result.chunk_id,
                "citation_content": result.content,
            }
            for result in expanded
        ]


def tenant() -> TenantContext:
    return TenantContext("tenant-1", PlanTier.ENTERPRISE, "key-1")


async def test_pipeline_batches_and_persists_recursive_and_proposition_indexes() -> None:
    store = RecordingStore()
    provider = RecordingProvider()
    embedder = RecordingEmbedder(dimension=7)
    pipeline = RAGIndexingPipeline(
        store=store,
        embedder=embedder,
        dependencies={
            RAGStrategy.RAPTOR: IndexingDependency(provider, "raptor-model"),
            RAGStrategy.AGENTIC_CHUNKING: IndexingDependency(
                provider,
                "agentic-model",
            ),
        },
        config=RAGIndexingConfig(
            strategies=frozenset(
                {RAGStrategy.RAPTOR, RAGStrategy.AGENTIC_CHUNKING}
            ),
            raptor_cluster_size=2,
            raptor_max_levels=3,
            parent_window_size=1,
        ),
    )

    records = await pipeline.index_document(
        collection_id="collection-1",
        document_id="document-1",
        chunks=["Alpha", "Beta", "Gamma", "Delta"],
        tenant_ctx=tenant(),
        metadata={"source_url": "policy.pdf"},
    )

    raptor = [record for record in records if record.strategy is RAGStrategy.RAPTOR]
    agentic_records = [
        record
        for record in records
        if record.strategy is RAGStrategy.AGENTIC_CHUNKING
    ]
    propositions = [record for record in agentic_records if record.is_proposition]
    assert {record.hierarchy_level for record in raptor} == {0, 1, 2}
    assert all(
        record.parent_chunk_id is not None
        for record in raptor
        if record.hierarchy_level < 2
    )
    assert all(record.is_proposition for record in propositions)
    assert all(record.parent_chunk_id for record in propositions)
    assert all(record.window_id for record in propositions)
    assert any(not record.is_proposition for record in agentic_records)
    assert store.persist_calls == 1
    assert len(embedder.requests) == 1
    assert embedder.requests[0].texts == [record.content for record in records]
    assert len(provider.requests) == 3
    assert all(record.embedding_dimension == 7 for record in records)


async def test_pipeline_replaces_stable_source_index_after_retry_and_response_loss() -> None:
    store = RecordingStore()
    pipeline = RAGIndexingPipeline(
        store=store,
        embedder=RecordingEmbedder(dimension=7),
        dependencies={
            RAGStrategy.RAPTOR: IndexingDependency(
                RecordingProvider(),
                "raptor-model",
            )
        },
        config=RAGIndexingConfig(
            strategies=frozenset({RAGStrategy.RAPTOR}),
            raptor_cluster_size=2,
        ),
    )

    first = await pipeline.index_document(
        collection_id="collection-1",
        document_id="stable-document-id",
        chunks=["Alpha", "Beta"],
        tenant_ctx=tenant(),
    )
    first_ids = [record.chunk_id for record in first]

    # Simulate a committed response being lost and the client retrying the request.
    retried = await pipeline.index_document(
        collection_id="collection-1",
        document_id="stable-document-id",
        chunks=["Alpha", "Beta"],
        tenant_ctx=tenant(),
    )

    assert [record.chunk_id for record in retried] == first_ids
    assert [record.chunk_id for record in store.records] == first_ids
    assert store.persist_calls == 2


async def test_pipeline_bounds_completion_and_embedding_batches() -> None:
    store = RecordingStore()
    provider = RecordingProvider()
    embedder = RecordingEmbedder(dimension=7)
    pipeline = RAGIndexingPipeline(
        store=store,
        embedder=embedder,
        dependencies={
            RAGStrategy.AGENTIC_CHUNKING: IndexingDependency(
                provider,
                "agentic-model",
            )
        },
        config=RAGIndexingConfig(
            strategies=frozenset({RAGStrategy.AGENTIC_CHUNKING}),
            proposition_batch_size=2,
            embedding_batch_size=3,
        ),
    )

    records = await pipeline.index_document(
        collection_id="collection-1",
        document_id="document-1",
        chunks=["Alpha", "Beta", "Gamma", "Delta", "Epsilon"],
        tenant_ctx=tenant(),
    )

    assert [len(request.texts) for request in embedder.requests] == [3, 3, 3, 1]
    assert len(provider.requests) == 3
    assert all(
        len(json.loads(str(request.messages[-1].content).split("\n", 1)[1])) <= 2
        for request in provider.requests
    )
    assert len(records) == 10


async def test_pipeline_rejects_incomplete_individual_completion_batch() -> None:
    class IncompleteProvider(RecordingProvider):
        async def complete(self, request: CompletionRequest) -> CompletionResponse:
            response = await super().complete(request)
            if len(self.requests) == 2:
                return CompletionResponse(content="[]", model=request.model)
            return response

    pipeline = RAGIndexingPipeline(
        store=RecordingStore(),
        embedder=RecordingEmbedder(dimension=7),
        dependencies={
            RAGStrategy.AGENTIC_CHUNKING: IndexingDependency(
                IncompleteProvider(),
                "agentic-model",
            )
        },
        config=RAGIndexingConfig(
            strategies=frozenset({RAGStrategy.AGENTIC_CHUNKING}),
            proposition_batch_size=2,
        ),
    )

    with pytest.raises(RuntimeError, match="incomplete batch"):
        await pipeline.index_document(
            collection_id="collection-1",
            document_id="document-1",
            chunks=["Alpha", "Beta", "Gamma"],
            tenant_ctx=tenant(),
        )


async def test_pipeline_uses_independent_strategy_dependencies() -> None:
    raptor_provider = RecordingProvider()
    agentic_provider = RecordingProvider()
    pipeline = RAGIndexingPipeline(
        store=RecordingStore(),
        embedder=RecordingEmbedder(dimension=7),
        dependencies={
            RAGStrategy.RAPTOR: IndexingDependency(raptor_provider, "raptor-model"),
            RAGStrategy.AGENTIC_CHUNKING: IndexingDependency(
                agentic_provider,
                "agentic-model",
            ),
        },
        config=RAGIndexingConfig(
            strategies=frozenset(
                {RAGStrategy.RAPTOR, RAGStrategy.AGENTIC_CHUNKING}
            ),
            raptor_cluster_size=2,
        ),
    )

    await pipeline.index_document(
        collection_id="collection-1",
        document_id="document-1",
        chunks=["Alpha", "Beta"],
        tenant_ctx=tenant(),
    )

    assert raptor_provider.requests
    assert agentic_provider.requests
    assert {request.model for request in raptor_provider.requests} == {"raptor-model"}
    assert {request.model for request in agentic_provider.requests} == {"agentic-model"}


async def test_query_patterns_use_precomputed_indexes_without_provider_calls() -> None:
    store = RecordingStore()
    base = RAGIndexRecord(
        chunk_id="leaf-1",
        document_id="document-1",
        content="Alpha parent window",
        embedding=[1.0, 0.0, 0.0],
        chunk_index=0,
        strategy=RAGStrategy.RAPTOR,
        hierarchy_level=0,
        strategy_metadata={"node_type": "leaf"},
    )
    store.records = [
        base,
        replace(
            base,
            chunk_id="summary-1",
            content="Alpha hierarchy summary",
            hierarchy_level=1,
            strategy_metadata={"node_type": "summary"},
        ),
        replace(
            base,
            chunk_id="prop-1",
            content="Alpha has a seven-year retention period.",
            strategy=RAGStrategy.AGENTIC_CHUNKING,
            parent_chunk_id="leaf-1",
            window_id="window-document-1-0",
            is_proposition=True,
            strategy_metadata={"node_type": "proposition"},
        ),
        replace(
            base,
            chunk_id="prop-2",
            content="Alpha retention is reviewed annually.",
            strategy=RAGStrategy.AGENTIC_CHUNKING,
            parent_chunk_id="leaf-1",
            window_id="window-document-1-0",
            is_proposition=True,
            strategy_metadata={"node_type": "proposition"},
        ),
    ]

    raptor = await RAPTORPattern().retrieve_precomputed(
        store=store,
        query="Alpha retention",
        query_embedding=[1.0, 0.0, 0.0],
        collection_id="collection-1",
        tenant_ctx=tenant(),
        top_k=5,
    )
    agentic = await AgenticChunkingPattern().retrieve_precomputed(
        store=store,
        query="Alpha retention",
        query_embedding=[1.0, 0.0, 0.0],
        collection_id="collection-1",
        tenant_ctx=tenant(),
        top_k=5,
    )

    assert {item["metadata"]["node_type"] for item in raptor} == {"leaf", "summary"}
    assert agentic[0]["chunk_id"] == "leaf-1"
    assert agentic[0]["citation_chunk_id"] == "leaf-1"
    assert agentic[0]["citation_content"] == "Alpha parent window"
    assert agentic[0]["metadata"]["proposition_chunk_id"] == "prop-1"
    assert agentic[0]["metadata"]["proposition_content"] == (
        "Alpha has a seven-year retention period."
    )
    assert len(agentic[0]["metadata"]["propositions"]) == 2


async def test_restart_reads_persisted_indexes_without_rebuilding() -> None:
    persisted_records: list[RAGIndexRecord] = []
    first_store = RecordingStore()
    first_store.records = persisted_records
    pipeline = RAGIndexingPipeline(
        store=first_store,
        embedder=RecordingEmbedder(dimension=7),
        dependencies={
            RAGStrategy.RAPTOR: IndexingDependency(
                RecordingProvider(),
                "indexing-model",
            )
        },
        config=RAGIndexingConfig(
            strategies=frozenset({RAGStrategy.RAPTOR}),
            raptor_cluster_size=2,
        ),
    )
    await pipeline.index_document(
        collection_id="collection-1",
        document_id="document-1",
        chunks=["Alpha", "Beta"],
        tenant_ctx=tenant(),
    )

    restarted_store = RecordingStore()
    restarted_store.records = persisted_records
    results = await RAPTORPattern().retrieve_precomputed(
        store=restarted_store,
        query="Alpha",
        query_embedding=[1.0, *([0.0] * 6)],
        collection_id="collection-1",
        tenant_ctx=tenant(),
        top_k=10,
    )

    assert {result["metadata"]["node_type"] for result in results} == {
        "leaf",
        "summary",
    }
    assert restarted_store.persist_calls == 0


def test_gateway_registers_ingestion_time_query_adapters_without_llm_dependency() -> None:
    capabilities = core_strategy_capabilities()

    for strategy in (RAGStrategy.RAPTOR, RAGStrategy.AGENTIC_CHUNKING):
        capability = capabilities[strategy]
        assert capability.requires_database
        assert capability.requires_embedder
        assert not capability.requires_provider


async def test_orchestrator_selects_pipeline_from_ingestion_config() -> None:
    store = RecordingStore()
    provider = RecordingProvider()
    embedder = RecordingEmbedder(dimension=7)
    orchestrator = IngestionOrchestrator(
        knowledge_store=store,
        embedder=embedder,
        indexing_dependencies={
            RAGStrategy.AGENTIC_CHUNKING: IndexingDependency(
                provider,
                "indexing-model",
            )
        },
        rag_indexing_config=RAGIndexingConfig(
            strategies=frozenset({RAGStrategy.AGENTIC_CHUNKING}),
            parent_window_size=1,
        ),
    )

    result = await orchestrator.ingest(
        "Alpha policy paragraph has enough detail for indexing.",
        collection_id="collection-1",
        tenant_ctx=tenant(),
        source_identity="policy-handbook",
    )

    assert result.persisted
    assert result.chunks_created == len(store.records)
    assert any(record.is_proposition for record in store.records)
    assert store.persist_calls == 1


async def test_orchestrator_rejects_indexed_in_memory_mode_before_persistence() -> None:
    store = RecordingStore()
    orchestrator = IngestionOrchestrator(
        knowledge_store=store,
        embedder=RecordingEmbedder(dimension=7),
        indexing_dependencies={
            RAGStrategy.RAPTOR: IndexingDependency(
                RecordingProvider(),
                "indexing-model",
            )
        },
        rag_indexing_config=RAGIndexingConfig(
            strategies=frozenset({RAGStrategy.RAPTOR})
        ),
    )

    with pytest.raises(ValueError, match="in_memory_only"):
        await orchestrator.ingest(
            "Alpha policy paragraph has enough detail for indexing.",
            collection_id="collection-1",
            tenant_ctx=tenant(),
            source_identity="policy-handbook",
            in_memory_only=True,
        )

    assert store.persist_calls == 0


@pytest.mark.integration
async def test_postgres_restart_preserves_both_precomputed_indexes() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        database_url = postgres.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=backend_root,
            env={**os.environ, "DATABASE_URL": database_url},
            check=True,
            capture_output=True,
            text=True,
        )
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        suffix = uuid.uuid4().hex[:16]
        tenant_ctx = TenantContext(
            f"index-{suffix}",
            PlanTier.ENTERPRISE,
            "key-1",
        )
        collection_id = f"collection-{suffix}"
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, :name, :email, 'enterprise', true)"
                ),
                {
                    "id": tenant_ctx.tenant_id,
                    "name": tenant_ctx.tenant_id,
                    "email": f"{tenant_ctx.tenant_id}@example.test",
                },
            )

        store = KnowledgeStore(session_factory)
        await store.create_collection_async(
            KnowledgeCollection(name="Indexed policies", collection_id=collection_id),
            tenant_ctx=tenant_ctx,
        )
        provider = RecordingProvider()
        embedder = RecordingEmbedder(dimension=768)
        pipeline = RAGIndexingPipeline(
            store=store,
            embedder=embedder,
            dependencies={
                RAGStrategy.RAPTOR: IndexingDependency(provider, "raptor-model"),
                RAGStrategy.AGENTIC_CHUNKING: IndexingDependency(
                    provider,
                    "agentic-model",
                ),
            },
            config=RAGIndexingConfig(
                strategies=frozenset(
                    {RAGStrategy.RAPTOR, RAGStrategy.AGENTIC_CHUNKING}
                ),
                raptor_cluster_size=2,
                parent_window_size=1,
            ),
        )
        await pipeline.index_document(
            collection_id=collection_id,
            document_id=f"document-{suffix}",
            chunks=["Alpha policy", "Beta review", "Gamma approval", "Delta scope"],
            tenant_ctx=tenant_ctx,
        )
        indexing_call_count = len(provider.requests)

        await asyncio.gather(
            pipeline.index_document(
                collection_id=collection_id,
                document_id=f"document-{suffix}",
                chunks=[
                    "Alpha policy",
                    "Beta review",
                    "Gamma approval",
                    "Delta scope",
                ],
                tenant_ctx=tenant_ctx,
            ),
            pipeline.index_document(
                collection_id=collection_id,
                document_id=f"document-{suffix}",
                chunks=[
                    "Alpha policy",
                    "Beta review",
                    "Gamma approval",
                    "Delta scope",
                ],
                tenant_ctx=tenant_ctx,
            ),
        )
        post_retry_indexing_count = len(provider.requests)
        assert post_retry_indexing_count > indexing_call_count

        async with session_factory() as session, session.begin():
            persisted = (
                await session.execute(
                    text(
                        "SELECT id, hierarchy_level, is_proposition, "
                        "parent_chunk_id, window_id "
                        "FROM knowledge_chunks_768 WHERE collection_id = :collection_id"
                    ),
                    {"collection_id": collection_id},
                )
            ).fetchall()
        assert len({str(row[0]) for row in persisted}) == len(persisted)
        assert {int(row[1]) for row in persisted} >= {0, 1, 2}
        assert any(
            bool(row[2]) and row[3] is not None and row[4] is not None
            for row in persisted
        )

        restarted_store = KnowledgeStore(session_factory)
        raptor_results = await restarted_store.search_precomputed_index(
            strategy=RAGStrategy.RAPTOR,
            query="Alpha policy",
            query_embedding=[1.0, *([0.0] * 767)],
            collection_id=collection_id,
            tenant_ctx=tenant_ctx,
            top_k=20,
        )
        agentic_results = await restarted_store.search_precomputed_index(
            strategy=RAGStrategy.AGENTIC_CHUNKING,
            query="Alpha policy",
            query_embedding=[1.0, *([0.0] * 767)],
            collection_id=collection_id,
            tenant_ctx=tenant_ctx,
            top_k=20,
        )
        assert {result["metadata"]["node_type"] for result in raptor_results} >= {
            "leaf",
            "summary",
        }
        assert agentic_results
        assert "Alpha policy" in agentic_results[0]["citation_content"]
        assert agentic_results[0]["chunk_id"] == agentic_results[0]["citation_chunk_id"]
        assert agentic_results[0]["metadata"]["proposition_chunk_id"]
        assert agentic_results[0]["metadata"]["proposition_content"]

        gateway = RetrievalGateway(
            RetrievalDependencies(
                session_factory=session_factory,
                collection_authorizer=SQLCollectionAuthorizer(),
                strategy_capabilities=core_strategy_capabilities(),
                embedder=embedder,
            )
        )
        gateway_result = await gateway.execute(
            tenant_ctx,
            collection_id=collection_id,
            query="Alpha policy",
            strategy_id=RAGStrategy.AGENTIC_CHUNKING,
            top_k=1,
        )
        assert len(gateway_result.citations) == 1
        citation = gateway_result.citations[0]
        assert citation.chunk_id == agentic_results[0]["citation_chunk_id"]
        assert "Alpha policy" in citation.content
        assert citation.metadata["proposition_chunk_id"]
        assert citation.metadata["proposition_content"]
        assert len(provider.requests) == post_retry_indexing_count
        await engine.dispose()
