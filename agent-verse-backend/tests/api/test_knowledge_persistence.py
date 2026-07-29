"""Awaited persistence contracts for production knowledge API routes."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.knowledge import router as knowledge_router
from app.providers.fake import FakeProvider
from app.rag.models import Chunk, KnowledgeCollection
from app.rag.semantic_cache import SemanticCache
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

TENANT = TenantContext("knowledge-persist", PlanTier.PROFESSIONAL, "key-1")
API_KEY = "av_persist_test"


def _app(store: KnowledgeStore) -> FastAPI:
    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        return TENANT if key == API_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    app.include_router(knowledge_router)
    app.state.knowledge_store = store
    app.state.semantic_cache = SemanticCache()
    app.state.embedder = FakeProvider(embed_dim=768)
    return app


class _AwaitedStore(KnowledgeStore):
    def __init__(self) -> None:
        super().__init__()
        self.collection_committed = False
        self.chunks_committed = False

    def create_collection(
        self,
        collection: KnowledgeCollection,
        *,
        tenant_ctx: TenantContext,
    ) -> str:
        raise AssertionError("production route used synchronous collection creation")

    async def create_collection_async(
        self,
        collection: KnowledgeCollection,
        *,
        tenant_ctx: TenantContext,
    ) -> str:
        await asyncio.sleep(0)
        collection_id = KnowledgeStore.create_collection(
            self,
            collection,
            tenant_ctx=tenant_ctx,
        )
        self.collection_committed = True
        return collection_id

    def ingest_chunk(
        self,
        chunk: Chunk,
        *,
        collection_id: str,
        tenant_ctx: TenantContext,
    ) -> None:
        raise AssertionError("production route used synchronous chunk ingestion")

    async def ingest_chunks_async(
        self,
        chunks: list[Chunk],
        *,
        collection_id: str,
        tenant_ctx: TenantContext,
    ) -> list[str]:
        await asyncio.sleep(0)
        for chunk in chunks:
            KnowledgeStore.ingest_chunk(
                self,
                chunk,
                collection_id=collection_id,
                tenant_ctx=tenant_ctx,
            )
        self.chunks_committed = True
        return [chunk.chunk_id for chunk in chunks]


def test_collection_response_waits_for_async_commit() -> None:
    store = _AwaitedStore()
    client = TestClient(_app(store), raise_server_exceptions=False)

    response = client.post(
        "/knowledge/collections",
        json={"name": "awaited"},
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 201
    assert store.collection_committed


def test_ingest_response_waits_for_atomic_chunk_commit() -> None:
    store = _AwaitedStore()
    collection = KnowledgeCollection(name="awaited-ingest", collection_id="collection-1")
    KnowledgeStore.create_collection(store, collection, tenant_ctx=TENANT)
    client = TestClient(_app(store), raise_server_exceptions=False)

    response = client.post(
        "/knowledge/ingest",
        json={
            "collection_id": collection.collection_id,
            "content": "Persist every generated chunk before returning success.",
        },
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 201
    assert store.chunks_committed
    assert response.json()["chunks_created"] >= 1


class _FailingStore(_AwaitedStore):
    async def create_collection_async(
        self,
        collection: KnowledgeCollection,
        *,
        tenant_ctx: TenantContext,
    ) -> str:
        raise RuntimeError("database unavailable with private details")

    async def ingest_chunks_async(
        self,
        chunks: list[Chunk],
        *,
        collection_id: str,
        tenant_ctx: TenantContext,
    ) -> list[str]:
        raise RuntimeError("database unavailable with private details")


def test_collection_persistence_failure_returns_structured_non_2xx() -> None:
    response = TestClient(_app(_FailingStore()), raise_server_exceptions=False).post(
        "/knowledge/collections",
        json={"name": "fails"},
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Knowledge persistence is unavailable"}


def test_ingest_persistence_failure_returns_structured_non_2xx() -> None:
    store = _FailingStore()
    KnowledgeStore.create_collection(
        store,
        KnowledgeCollection(name="fails", collection_id="collection-1"),
        tenant_ctx=TENANT,
    )
    response = TestClient(_app(store), raise_server_exceptions=False).post(
        "/knowledge/ingest",
        json={"collection_id": "collection-1", "content": "must roll back"},
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Knowledge persistence is unavailable"}
    assert "private details" not in response.text
