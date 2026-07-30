"""Awaited persistence contracts for production knowledge API routes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
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
        self.jobs: dict[str, dict[str, Any]] = {}

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

    async def delete_collection_async(
        self,
        collection_id: str,
        *,
        tenant_ctx: TenantContext,
    ) -> bool:
        database = self._db
        self._db = None
        deleted = await KnowledgeStore.delete_collection_async(
            self,
            collection_id,
            tenant_ctx=tenant_ctx,
        )
        self._db = database
        return deleted

    async def create_ingestion_job_async(
        self,
        *,
        collection_id: str,
        source_url: str,
        source_type: str,
        title: str,
        tenant_ctx: TenantContext,
    ) -> str:
        job_id = "job-1"
        self.jobs[job_id] = {
            "job_id": job_id,
            "collection_id": collection_id,
            "status": "queued",
            "chunk_count": 0,
            "error_message": None,
            "source_url": source_url,
        }
        return job_id

    async def update_ingestion_job_async(
        self,
        job_id: str,
        *,
        status: str,
        chunk_count: int,
        error_message: str | None,
        tenant_ctx: TenantContext,
        lease_owner: str | None = None,
    ) -> None:
        self.jobs[job_id].update(
            status=status,
            chunk_count=chunk_count,
            error_message=error_message,
        )

    async def get_ingestion_job_async(
        self,
        job_id: str,
        *,
        tenant_ctx: TenantContext,
    ) -> dict[str, Any] | None:
        return self.jobs.get(job_id)

    async def reconcile_stale_ingestion_jobs_async(
        self,
        *,
        tenant_ctx: TenantContext,
        stale_after_seconds: int,
    ) -> int:
        return 0

    async def claim_ingestion_job_async(
        self,
        job_id: str,
        *,
        collection_id: str,
        source_url: str,
        lease_owner: str,
        lease_seconds: int,
        tenant_ctx: TenantContext,
    ) -> bool:
        job = self.jobs.get(job_id)
        if job is None or job["status"] != "queued":
            return False
        job.update(status="running", lease_owner=lease_owner)
        return True

    async def heartbeat_ingestion_job_async(
        self,
        job_id: str,
        *,
        lease_owner: str,
        lease_seconds: int,
        tenant_ctx: TenantContext,
    ) -> bool:
        job = self.jobs.get(job_id)
        return bool(
            job
            and job["status"] == "running"
            and job.get("lease_owner") == lease_owner
        )

    async def fail_ingestion_job_async(
        self,
        job_id: str,
        *,
        lease_owner: str | None,
        error_message: str,
        tenant_ctx: TenantContext,
    ) -> str | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        if job["status"] == "queued" or (
            job["status"] == "running" and job.get("lease_owner") == lease_owner
        ):
            job.update(
                status="failed",
                error_message=error_message,
                lease_owner=None,
            )
        return str(job["status"])

    async def ingest_repository_chunks_async(
        self,
        chunks: list[Chunk],
        *,
        job_id: str,
        collection_id: str,
        source_url: str,
        lease_owner: str,
        tenant_ctx: TenantContext,
    ) -> list[str]:
        chunk_ids = await self.ingest_chunks_async(
            chunks,
            collection_id=collection_id,
            tenant_ctx=tenant_ctx,
        )
        job = self.jobs[job_id]
        if job["status"] != "running" or job.get("lease_owner") != lease_owner:
            raise RuntimeError("job lease lost")
        job.update(
            status="completed",
            chunk_count=len(chunks),
            error_message=None,
            lease_owner=None,
        )
        return chunk_ids

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


def test_repo_ingest_validates_collection_before_scheduling() -> None:
    store = _AwaitedStore()

    with patch("asyncio.create_task") as create_task:
        response = TestClient(_app(store), raise_server_exceptions=False).post(
            "/knowledge/ingest/repo",
            json={
                "collection_id": "foreign-or-missing",
                "repo_url": "https://github.com/example/repository",
            },
            headers={"X-API-Key": API_KEY},
        )

    assert response.status_code == 404
    create_task.assert_not_called()
    assert store.jobs == {}


def test_collection_delete_returns_204_only_after_awaited_persistence() -> None:
    store = _AwaitedStore()
    store.seed_collection(
        KnowledgeCollection(name="delete", collection_id="collection-1")
    )
    response = TestClient(_app(store), raise_server_exceptions=False).delete(
        "/knowledge/collections/collection-1",
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 204
    assert store.get_collection("collection-1", tenant_ctx=TENANT) is None


def test_collection_delete_persistence_failure_returns_503_without_cache_mutation() -> None:
    class _DeleteFailingStore(_AwaitedStore):
        async def delete_collection_async(
            self,
            collection_id: str,
            *,
            tenant_ctx: TenantContext,
        ) -> bool:
            raise RuntimeError("private delete failure")

    store = _DeleteFailingStore()
    store.seed_collection(
        KnowledgeCollection(name="delete", collection_id="collection-1")
    )
    response = TestClient(_app(store), raise_server_exceptions=False).delete(
        "/knowledge/collections/collection-1",
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Knowledge persistence is unavailable"}
    assert store.get_collection("collection-1", tenant_ctx=TENANT) is not None


@pytest.mark.parametrize(
    "repo_url",
    [
        "/tmp/repository",
        "file:///tmp/repository",
        "ssh://git@example.com/repository",
        "git@example.com:organization/repository.git",
        "https://user:secret@example.com/repository",
        "https://127.0.0.1/repository",
    ],
)
def test_repo_ingest_rejects_unsafe_urls_before_job_creation(repo_url: str) -> None:
    store = _AwaitedStore()
    store.seed_collection(
        KnowledgeCollection(name="repository", collection_id="collection-1")
    )

    response = TestClient(_app(store), raise_server_exceptions=False).post(
        "/knowledge/ingest/repo",
        json={"collection_id": "collection-1", "repo_url": repo_url},
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 400
    assert store.jobs == {}


def test_repo_ingest_rejects_unsafe_patterns_and_file_count_before_job() -> None:
    store = _AwaitedStore()
    store.seed_collection(
        KnowledgeCollection(name="repository", collection_id="collection-1")
    )
    client = TestClient(_app(store), raise_server_exceptions=False)

    with patch("app.net.ssrf_guard._resolve_host", return_value=["93.184.216.34"]):
        traversal = client.post(
            "/knowledge/ingest/repo",
            json={
                "collection_id": "collection-1",
                "repo_url": "https://example.com/repository",
                "file_patterns": ["../*.py"],
            },
            headers={"X-API-Key": API_KEY},
        )
        invalid_count = client.post(
            "/knowledge/ingest/repo",
            json={
                "collection_id": "collection-1",
                "repo_url": "https://example.com/repository",
                "max_files": 0,
            },
            headers={"X-API-Key": API_KEY},
        )

    assert traversal.status_code == 400
    assert invalid_count.status_code == 400
    assert store.jobs == {}


def test_repo_ingest_requires_usable_embedder_before_scheduling() -> None:
    class _FailingEmbedder:
        async def embed(self, request: EmbedRequest) -> EmbedResponse:
            raise RuntimeError("private provider failure")

    for embedder in (None, _FailingEmbedder()):
        store = _AwaitedStore()
        store.seed_collection(
            KnowledgeCollection(name="repository", collection_id="collection-1")
        )
        app = _app(store)
        app.state.embedder = embedder
        with patch("asyncio.create_task") as create_task:
            response = TestClient(app, raise_server_exceptions=False).post(
                "/knowledge/ingest/repo",
                json={
                    "collection_id": "collection-1",
                    "repo_url": "https://github.com/example/repository",
                },
                headers={"X-API-Key": API_KEY},
            )

        assert response.status_code == 503
        assert response.json() == {"detail": "Embedding provider is unavailable"}
        create_task.assert_not_called()
        assert store.jobs == {}


def test_repo_ingest_creates_queryable_job_before_scheduling() -> None:
    store = _AwaitedStore()
    store.seed_collection(
        KnowledgeCollection(name="repository", collection_id="collection-1")
    )

    def schedule(coroutine: Any) -> object:
        coroutine.close()
        return MagicMock()

    with patch("asyncio.create_task", side_effect=schedule):
        client = TestClient(_app(store), raise_server_exceptions=False)
        response = client.post(
            "/knowledge/ingest/repo",
            json={
                "collection_id": "collection-1",
                "repo_url": "https://github.com/example/repository",
            },
            headers={"X-API-Key": API_KEY},
        )
        status_response = client.get(
            "/knowledge/ingest/jobs/job-1",
            headers={"X-API-Key": API_KEY},
        )

    assert response.status_code == 202
    assert response.json()["job_id"] == "job-1"
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "queued"
    assert len(cast(Any, client.app).state.repository_ingestion_tasks) == 1


async def test_repo_background_failure_records_sanitized_durable_status() -> None:
    from app.api.knowledge import _ingest_repo_background

    store = _AwaitedStore()
    store.jobs["job-1"] = {
        "job_id": "job-1",
        "collection_id": "collection-1",
        "status": "queued",
        "chunk_count": 0,
        "error_message": None,
        "source_url": "https://github.com/example/repository",
    }
    process = AsyncMock()
    process.returncode = 1
    process.communicate = AsyncMock(return_value=(b"", b"private clone details"))

    with patch("asyncio.create_subprocess_exec", return_value=process):
        await _ingest_repo_background(
            job_id="job-1",
            repo_url="https://github.com/example/repository",
            collection_id="collection-1",
            branch="main",
            file_patterns=["**/*.py"],
            max_files=10,
            store=store,
            embedder=FakeProvider(embed_dim=768),
            tenant_ctx=TENANT,
        )

    assert store.jobs["job-1"]["status"] == "failed"
    assert store.jobs["job-1"]["chunk_count"] == 0
    assert store.jobs["job-1"]["error_message"] == "Repository ingestion failed"
    assert "private clone details" not in str(store.jobs["job-1"])


async def test_repo_background_success_commits_once_and_completes_job(tmp_path: Path) -> None:
    from app.api.knowledge import _ingest_repo_background

    (tmp_path / "service.py").write_text("def service():\n    return 'ok'\n")
    (tmp_path / "README.md").write_text("Repository documentation content.")
    store = _AwaitedStore()
    store.seed_collection(
        KnowledgeCollection(name="repository", collection_id="collection-1")
    )
    store.jobs["job-1"] = {
        "job_id": "job-1",
        "collection_id": "collection-1",
        "status": "queued",
        "chunk_count": 0,
        "error_message": None,
        "source_url": "https://github.com/example/repository",
    }
    process = AsyncMock()
    process.returncode = 0
    process.communicate = AsyncMock(return_value=(b"", b""))

    with (
        patch("asyncio.create_subprocess_exec", return_value=process),
        patch("tempfile.mkdtemp", return_value=str(tmp_path)),
        patch("shutil.rmtree"),
    ):
        await _ingest_repo_background(
            job_id="job-1",
            repo_url="https://github.com/example/repository",
            collection_id="collection-1",
            branch="main",
            file_patterns=["**/*.py", "**/*.md"],
            max_files=10,
            store=store,
            embedder=FakeProvider(embed_dim=768),
            tenant_ctx=TENANT,
        )

    chunks = store._data[(TENANT.tenant_id, "collection-1")].chunks
    assert store.jobs["job-1"]["status"] == "completed"
    assert store.jobs["job-1"]["chunk_count"] == len(chunks) == 2
    assert len({chunk.document_id for chunk in chunks}) == 2


async def test_repo_background_provider_failure_is_durable_and_atomic(tmp_path: Path) -> None:
    from app.api.knowledge import _ingest_repo_background

    class _FailingEmbedder:
        async def embed(self, request: EmbedRequest) -> EmbedResponse:
            raise RuntimeError("private provider outage")

    (tmp_path / "service.py").write_text("def service():\n    return 'ok'\n")
    store = _AwaitedStore()
    store.seed_collection(
        KnowledgeCollection(name="repository", collection_id="collection-1")
    )
    store.jobs["job-1"] = {
        "job_id": "job-1",
        "collection_id": "collection-1",
        "status": "queued",
        "chunk_count": 0,
        "error_message": None,
        "source_url": "https://github.com/example/repository",
    }
    process = AsyncMock()
    process.returncode = 0
    process.communicate = AsyncMock(return_value=(b"", b""))

    with (
        patch("asyncio.create_subprocess_exec", return_value=process),
        patch("tempfile.mkdtemp", return_value=str(tmp_path)),
        patch("shutil.rmtree"),
    ):
        await _ingest_repo_background(
            job_id="job-1",
            repo_url="https://github.com/example/repository",
            collection_id="collection-1",
            branch="main",
            file_patterns=["**/*.py"],
            max_files=10,
            store=store,
            embedder=_FailingEmbedder(),
            tenant_ctx=TENANT,
        )

    assert store.jobs["job-1"]["status"] == "failed"
    assert store.jobs["job-1"]["chunk_count"] == 0
    assert store.jobs["job-1"]["error_message"] == "Repository ingestion failed"
    assert "private provider outage" not in str(store.jobs["job-1"])
    assert store._data[(TENANT.tenant_id, "collection-1")].chunks == []


async def test_repo_background_secret_scanner_blocks_before_embedding(tmp_path: Path) -> None:
    from app.api.knowledge import _ingest_repo_background

    (tmp_path / "service.py").write_text("AWS_KEY = 'AKIAABCDEFGHIJKLMNOP'")
    store = _AwaitedStore()
    store.seed_collection(
        KnowledgeCollection(name="repository", collection_id="collection-1")
    )
    store.jobs["job-1"] = {
        "job_id": "job-1",
        "collection_id": "collection-1",
        "status": "queued",
        "chunk_count": 0,
        "error_message": None,
        "source_url": "https://github.com/example/repository",
    }
    process = AsyncMock()
    process.returncode = 0
    process.communicate = AsyncMock(return_value=(b"", b""))
    embedder = AsyncMock()

    with (
        patch("asyncio.create_subprocess_exec", return_value=process),
        patch("tempfile.mkdtemp", side_effect=[str(tmp_path), str(tmp_path / "config")]),
        patch("shutil.rmtree"),
    ):
        (tmp_path / "config").mkdir()
        await _ingest_repo_background(
            job_id="job-1",
            repo_url="https://github.com/example/repository",
            collection_id="collection-1",
            branch="main",
            file_patterns=["**/*.py"],
            max_files=10,
            store=store,
            embedder=embedder,
            tenant_ctx=TENANT,
            curl_resolve="github.com:443:93.184.216.34",
        )

    embedder.embed.assert_not_awaited()
    assert store.jobs["job-1"]["status"] == "failed"
    assert store._data[(TENANT.tenant_id, "collection-1")].chunks == []


async def test_repo_background_cancellation_records_failure_and_reraises() -> None:
    from app.api.knowledge import _ingest_repo_background

    store = _AwaitedStore()
    store.jobs["job-1"] = {
        "job_id": "job-1",
        "collection_id": "collection-1",
        "status": "queued",
        "chunk_count": 0,
        "error_message": None,
        "source_url": "https://github.com/example/repository",
    }
    process = AsyncMock()
    process.returncode = None
    process.communicate = AsyncMock(side_effect=asyncio.CancelledError())

    with (
        patch("asyncio.create_subprocess_exec", return_value=process) as create_process,
        pytest.raises(asyncio.CancelledError),
    ):
        await _ingest_repo_background(
            job_id="job-1",
            repo_url="https://github.com/example/repository",
            collection_id="collection-1",
            branch="main",
            file_patterns=["**/*.py"],
            max_files=10,
            store=store,
            embedder=FakeProvider(embed_dim=768),
            tenant_ctx=TENANT,
        )

    argv = create_process.call_args.args
    assert argv[0] == "git"
    assert "protocol.allow=never" in argv
    assert "protocol.https.allow=always" in argv
    assert "credential.helper=" in argv
    assert "--no-tags" in argv
    assert "--single-branch" in argv
    assert "http.followRedirects=false" in argv
    assert "http.curloptResolve=github.com:443:" in " ".join(argv)
    assert create_process.call_args.kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert set(create_process.call_args.kwargs["env"]) == {
        "HOME",
        "XDG_CONFIG_HOME",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_GLOBAL",
        "GIT_TERMINAL_PROMPT",
        "GIT_ALLOW_PROTOCOL",
        "GIT_OPTIONAL_LOCKS",
        "GIT_LFS_SKIP_SMUDGE",
        "GCM_INTERACTIVE",
        "LC_ALL",
        "PATH",
    }
    process.kill.assert_called_once()
    assert store.jobs["job-1"]["status"] == "failed"
    assert store.jobs["job-1"]["error_message"] == "Repository ingestion cancelled"


async def test_repo_worker_ignores_malicious_inherited_git_proxy_and_ssh_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.knowledge import _ingest_repo_background

    for name in (
        "GIT_CONFIG_GLOBAL",
        "GIT_SSH_COMMAND",
        "SSH_AUTH_SOCK",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "GIT_PROXY_COMMAND",
    ):
        monkeypatch.setenv(name, "malicious-value")
    store = _AwaitedStore()
    store.jobs["job-1"] = {
        "job_id": "job-1",
        "collection_id": "collection-1",
        "status": "queued",
        "chunk_count": 0,
        "error_message": None,
        "source_url": "https://github.com/example/repository",
    }
    process = AsyncMock()
    process.returncode = 1
    process.communicate = AsyncMock(return_value=(b"", b""))

    with patch("asyncio.create_subprocess_exec", return_value=process) as create_process:
        await _ingest_repo_background(
            job_id="job-1",
            repo_url="https://github.com/example/repository",
            collection_id="collection-1",
            branch="main",
            file_patterns=["**/*.py"],
            max_files=10,
            store=store,
            embedder=FakeProvider(embed_dim=768),
            tenant_ctx=TENANT,
        )

    environment = create_process.call_args.kwargs["env"]
    assert "GIT_SSH_COMMAND" not in environment
    assert "SSH_AUTH_SOCK" not in environment
    assert "HTTPS_PROXY" not in environment
    assert "ALL_PROXY" not in environment
    assert environment["GIT_CONFIG_GLOBAL"] != "malicious-value"


async def test_repo_clone_quota_kills_and_reaps_process(tmp_path: Path) -> None:
    from app.api.knowledge import _ingest_repo_background
    from app.ingestion.repository_security import RepositoryLimits

    store = _AwaitedStore()
    store.jobs["job-1"] = {
        "job_id": "job-1",
        "collection_id": "collection-1",
        "status": "queued",
        "chunk_count": 0,
        "error_message": None,
        "source_url": "https://github.com/example/repository",
    }
    stopped = asyncio.Event()
    process = MagicMock()
    process.returncode = None

    async def communicate() -> tuple[bytes, bytes]:
        (tmp_path / "oversized.pack").write_bytes(b"x" * 32)
        await stopped.wait()
        return b"", b""

    def kill() -> None:
        process.returncode = -9
        stopped.set()

    process.communicate = communicate
    process.kill = MagicMock(side_effect=kill)
    process.wait = AsyncMock(return_value=-9)

    with (
        patch("asyncio.create_subprocess_exec", return_value=process),
        patch("tempfile.mkdtemp", side_effect=[str(tmp_path), str(tmp_path / "config")]),
        patch("shutil.rmtree"),
    ):
        (tmp_path / "config").mkdir()
        await _ingest_repo_background(
            job_id="job-1",
            repo_url="https://github.com/example/repository",
            collection_id="collection-1",
            branch="main",
            file_patterns=["**/*.py"],
            max_files=10,
            store=store,
            embedder=FakeProvider(embed_dim=768),
            tenant_ctx=TENANT,
            limits=RepositoryLimits(
                max_files=10,
                max_file_bytes=16,
                max_total_bytes=16,
                max_repository_bytes=16,
                max_repository_files=10,
            ),
            clone_timeout_seconds=5,
            curl_resolve="github.com:443:93.184.216.34",
        )

    process.kill.assert_called_once()
    process.wait.assert_awaited()
    assert store.jobs["job-1"]["status"] == "failed"
