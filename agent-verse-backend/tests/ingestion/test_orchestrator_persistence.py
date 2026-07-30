"""Atomic persistence contracts for IngestionOrchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion.orchestrator import IngestionOrchestrator
from app.rag.models import KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext("orchestrator-tenant", PlanTier.PROFESSIONAL, "key-1")


async def test_orchestrator_persists_all_prepared_chunks_in_one_awaited_call() -> None:
    store = MagicMock()
    store._db = object()
    store.ingest_chunks_async = AsyncMock(
        side_effect=lambda chunks, **_: [chunk.chunk_id for chunk in chunks]
    )
    orchestrator = IngestionOrchestrator(knowledge_store=store)

    result = await orchestrator.ingest(
        "First sufficiently long paragraph for ingestion.\n\n"
        "Second sufficiently long paragraph for ingestion.",
        content_type="text",
        collection_id="collection-1",
        tenant_ctx=TENANT,
    )

    store.ingest_chunks_async.assert_awaited_once()
    persisted_chunks = store.ingest_chunks_async.await_args.args[0]
    assert len(persisted_chunks) == result.chunks_prepared
    assert result.chunk_ids == [chunk.chunk_id for chunk in persisted_chunks]
    assert result.chunks_created == len(result.chunk_ids)
    assert result.persisted


async def test_orchestrator_failure_propagates_without_result_or_memory_mutation() -> None:
    store = MagicMock()
    store._db = object()
    store.ingest_chunks_async = AsyncMock(side_effect=RuntimeError("transaction rolled back"))
    store._data = {"sentinel": "unchanged"}
    orchestrator = IngestionOrchestrator(knowledge_store=store)

    with pytest.raises(RuntimeError, match="transaction rolled back"):
        await orchestrator.ingest(
            "First sufficiently long paragraph for ingestion.\n\n"
            "Second sufficiently long paragraph for ingestion.",
            content_type="text",
            collection_id="collection-1",
            tenant_ctx=TENANT,
        )

    store.ingest_chunks_async.assert_awaited_once()
    assert store._data == {"sentinel": "unchanged"}


async def test_orchestrator_rejects_ids_not_owned_by_prepared_chunks() -> None:
    store = MagicMock()
    store._db = object()
    store.ingest_chunks_async = AsyncMock(return_value=["fabricated-id"])
    orchestrator = IngestionOrchestrator(knowledge_store=store)

    with pytest.raises(RuntimeError, match="commit every prepared chunk"):
        await orchestrator.ingest(
            "One sufficiently long paragraph for ingestion.",
            content_type="text",
            collection_id="collection-1",
            tenant_ctx=TENANT,
        )


async def test_dry_run_is_explicit_and_returns_no_persisted_ids() -> None:
    store = MagicMock()
    store.ingest_chunks_async = AsyncMock()
    orchestrator = IngestionOrchestrator(knowledge_store=store)

    result = await orchestrator.ingest(
        "Content prepared but intentionally not persisted.",
        content_type="text",
        collection_id="collection-1",
        tenant_ctx=TENANT,
        dry_run=True,
    )

    store.ingest_chunks_async.assert_not_awaited()
    assert result.chunks_prepared >= 1
    assert result.chunks_created == 0
    assert result.chunk_ids == []
    assert not result.persisted


async def test_in_memory_only_mode_is_explicitly_non_persisted() -> None:
    store = KnowledgeStore()
    store.create_collection(
        KnowledgeCollection(name="memory", collection_id="collection-1"),
        tenant_ctx=TENANT,
    )
    orchestrator = IngestionOrchestrator(knowledge_store=store)

    result = await orchestrator.ingest(
        "Content intentionally retained only in process memory.",
        content_type="text",
        collection_id="collection-1",
        tenant_ctx=TENANT,
        in_memory_only=True,
    )

    assert result.chunks_prepared >= 1
    assert result.chunks_created == 0
    assert result.chunk_ids == []
    assert not result.persisted
    assert store._data[(TENANT.tenant_id, "collection-1")].chunks
