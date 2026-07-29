"""Awaited persistence contracts for production knowledge API routes."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.knowledge import router as knowledge_router
from app.providers.base import EmbedRequest, EmbedResponse
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
        self._db = object()
        self.collection_committed = False
        self.chunks_committed = False

    def seed_collection(self, collection: KnowledgeCollection) -> None:
        database = self._db
        self._db = None
        KnowledgeStore.create_collection(self, collection, tenant_ctx=TENANT)
        self._db = database

    async def get_collection_async(
        self,
        collection_id: str,
        *,
        tenant_ctx: TenantContext,
    ) -> KnowledgeCollection | None:
        database = self._db
        self._db = None
        collection = self.get_collection(collection_id, tenant_ctx=tenant_ctx)
        self._db = database
        return collection

    async def list_collections_async(
        self,
        *,
        tenant_ctx: TenantContext,
    ) -> list[KnowledgeCollection]:
        database = self._db
        self._db = None
        collections = self.list_collections(tenant_ctx=tenant_ctx)
        self._db = database
        return collections

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
        database = self._db
        self._db = None
        collection_id = KnowledgeStore.create_collection(
            self,
            collection,
            tenant_ctx=tenant_ctx,
        )
        self._db = database
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
        database = self._db
        self._db = None
        for chunk in chunks:
            KnowledgeStore.ingest_chunk(
                self,
                chunk,
                collection_id=collection_id,
                tenant_ctx=tenant_ctx,
            )
        self._db = database
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
    store.seed_collection(collection)
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
    store.seed_collection(KnowledgeCollection(name="fails", collection_id="collection-1"))
    response = TestClient(_app(store), raise_server_exceptions=False).post(
        "/knowledge/ingest",
        json={"collection_id": "collection-1", "content": "must roll back"},
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Knowledge persistence is unavailable"}
    assert "private details" not in response.text


def test_orchestrated_document_ingest_returns_only_committed_ids() -> None:
    store = _AwaitedStore()
    store.seed_collection(
        KnowledgeCollection(name="orchestrated", collection_id="collection-1")
    )
    response = TestClient(_app(store), raise_server_exceptions=False).post(
        "/knowledge/collections/collection-1/documents",
        json={"content": "Persist this orchestrated document atomically."},
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 201
    assert store.chunks_committed
    assert response.json()["ingested"] == len(response.json()["chunk_ids"])
    assert response.json()["ingested"] > 0


def test_orchestrated_document_failure_returns_sanitized_non_2xx() -> None:
    store = _FailingStore()
    store.seed_collection(
        KnowledgeCollection(name="orchestrated", collection_id="collection-1")
    )
    response = TestClient(_app(store), raise_server_exceptions=False).post(
        "/knowledge/collections/collection-1/documents",
        json={"content": "This transaction must fail."},
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Knowledge persistence is unavailable"}
    assert store._data[(TENANT.tenant_id, "collection-1")].chunks == []


def test_orchestrated_dry_run_is_explicitly_non_persisted() -> None:
    store = KnowledgeStore()
    store.create_collection(
        KnowledgeCollection(name="dry-run", collection_id="collection-1"),
        tenant_ctx=TENANT,
    )
    response = TestClient(_app(store), raise_server_exceptions=False).post(
        "/knowledge/collections/collection-1/documents",
        json={"content": "Prepare but do not persist this document.", "dry_run": True},
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 201
    assert response.json()["persisted"] is False
    assert response.json()["ingested"] == 0
    assert response.json()["chunk_ids"] == []
    assert response.json()["chunks_prepared"] >= 1


def test_vector_ingest_without_embedder_returns_sanitized_503() -> None:
    store = _AwaitedStore()
    store.seed_collection(
        KnowledgeCollection(name="missing-embedder", collection_id="collection-1")
    )
    app = _app(store)
    app.state.embedder = None

    response = TestClient(app, raise_server_exceptions=False).post(
        "/knowledge/ingest",
        json={"collection_id": "collection-1", "content": "must not persist"},
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Embedding provider is unavailable"}
    assert store._data[(TENANT.tenant_id, "collection-1")].chunks == []


def test_vector_ingest_with_failing_embedder_returns_sanitized_503() -> None:
    class _FailingEmbedder:
        async def embed(self, request: EmbedRequest) -> EmbedResponse:
            raise RuntimeError("private provider failure")

    store = _AwaitedStore()
    store.seed_collection(
        KnowledgeCollection(name="failing-embedder", collection_id="collection-1")
    )
    app = _app(store)
    app.state.embedder = _FailingEmbedder()

    response = TestClient(app, raise_server_exceptions=False).post(
        "/knowledge/ingest",
        json={"collection_id": "collection-1", "content": "must not persist"},
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Embedding provider is unavailable"}
    assert "private provider failure" not in response.text
    assert store._data[(TENANT.tenant_id, "collection-1")].chunks == []


async def test_structured_source_chunks_share_document_identity_and_delete_together() -> None:
    from app.api.knowledge import _ingest_chunks_from_source

    store = KnowledgeStore()
    collection = KnowledgeCollection(name="structured", collection_id="collection-1")
    store.create_collection(collection, tenant_ctx=TENANT)

    count = await _ingest_chunks_from_source(
        store,
        [
            {"content": "First structured source chunk.", "source_doc_id": "source-1"},
            {"content": "Second structured source chunk.", "source_doc_id": "source-1"},
        ],
        collection.collection_id,
        TENANT,
        FakeProvider(embed_dim=768),
    )

    chunks = store._data[(TENANT.tenant_id, collection.collection_id)].chunks
    assert count == 2
    assert {chunk.document_id for chunk in chunks} == {"source-1"}
    assert [chunk.chunk_index for chunk in chunks] == [0, 1]
    assert collection.document_count == 1
    assert store.delete_document(
        "source-1",
        collection_id=collection.collection_id,
        tenant_ctx=TENANT,
    ) == 2
    assert store._data[(TENANT.tenant_id, collection.collection_id)].chunks == []
