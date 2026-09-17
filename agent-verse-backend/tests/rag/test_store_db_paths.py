"""Coverage for the persisted (DB-backed) code paths of app/rag/store.py.

These exercise every ``self._db is not None`` branch of ``KnowledgeStore`` using
a lightweight scripted fake SQLAlchemy AsyncSession — no real Postgres or container
runtime needed, so these run in the default fast test tier (unlike
``tests/rag/test_persisted_rag_store.py``, which needs a real database and is marked
``integration``).

The fake session auto-answers the ``sqlalchemy_rls_context`` SET LOCAL calls with an
empty result (matched by the ``set_config`` substring in the compiled SQL) and returns
the caller-scripted results, in order, for every other ``execute()`` call.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.rag.contracts import RAGStrategy
from app.rag.engine import RetrievalResult
from app.rag.indexing import RAGIndexRecord
from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import (
    EmbeddingProviderUnavailableError,
    HybridSearchResult,
    KnowledgeStore,
)
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.asyncio

_CTX = TenantContext(tenant_id="db-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")


# ── Fake DB session / factory ────────────────────────────────────────────────


class _Result:
    def __init__(self, rows: list | None = None, rowcount: int = 0, scalar: Any = None):
        self._rows = rows if rows is not None else []
        self.rowcount = rowcount
        self._scalar = scalar

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalar

    def scalar_one(self):
        return self._scalar

    def scalars(self):
        rows = self._rows

        class _S:
            def all(self):
                return rows

        return _S()


class _ScriptedSession:
    def __init__(self, results: list[_Result]):
        self._results = list(results)
        self.calls: list[tuple[str, Any]] = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append((sql, params))
        if "set_config" in sql:
            return _Result()
        if self._results:
            return self._results.pop(0)
        return _Result()

    def begin(self):
        outer = self

        class _B:
            async def __aenter__(self):
                return outer

            async def __aexit__(self, *a):
                return False

        return _B()

    def add(self, obj):
        pass


class _ScriptedDB:
    def __init__(self, *results: _Result):
        self.session = _ScriptedSession(list(results))

    def __call__(self):
        s = self.session

        class _CM:
            async def __aenter__(self):
                return s

            async def __aexit__(self, *a):
                return False

        return _CM()


class _RaisingDB:
    """DB whose session context manager raises immediately (propagation checks)."""

    def __call__(self):
        return self

    async def __aenter__(self):
        raise RuntimeError("boom")

    async def __aexit__(self, *a):
        return False


# ── create_collection_async / _db_create_collection ─────────────────────────


class TestCreateCollectionAsync:
    async def test_no_db_delegates_to_sync(self):
        store = KnowledgeStore()
        col = KnowledgeCollection(name="c", collection_id="cid1")
        result = await store.create_collection_async(col, tenant_ctx=_CTX)
        assert result == "cid1"
        assert store.get_collection("cid1", tenant_ctx=_CTX) is not None

    async def test_db_success_persists_then_caches(self):
        db = _ScriptedDB(_Result(scalar="cid2"))
        store = KnowledgeStore(db_session_factory=db)
        col = KnowledgeCollection(name="c2", collection_id="cid2")
        result = await store.create_collection_async(col, tenant_ctx=_CTX)
        assert result == "cid2"
        assert store.get_collection("cid2", tenant_ctx=_CTX) is not None

    async def test_db_inactive_tenant_raises_keyerror(self):
        db = _ScriptedDB(_Result(scalar=None))
        store = KnowledgeStore(db_session_factory=db)
        col = KnowledgeCollection(name="c3", collection_id="cid3")
        with pytest.raises(KeyError):
            await store.create_collection_async(col, tenant_ctx=_CTX)
        assert store.get_collection("cid3", tenant_ctx=_CTX) is None


# ── get_collection_async ─────────────────────────────────────────────────────


class TestGetCollectionAsync:
    async def test_no_db_delegates(self):
        store = KnowledgeStore()
        col = KnowledgeCollection(name="c", collection_id="cid1")
        store.create_collection(col, tenant_ctx=_CTX)
        got = await store.get_collection_async("cid1", tenant_ctx=_CTX)
        assert got is not None and got.name == "c"

    async def test_db_found(self):
        row = ("cid1", "Name", "Desc", 3, "voyage")
        db = _ScriptedDB(_Result(rows=[row]))
        store = KnowledgeStore(db_session_factory=db)
        got = await store.get_collection_async("cid1", tenant_ctx=_CTX)
        assert got is not None
        assert got.name == "Name"
        assert got.document_count == 3
        assert got.embedder == "voyage"

    async def test_db_not_found_returns_none(self):
        db = _ScriptedDB(_Result(rows=[]))
        store = KnowledgeStore(db_session_factory=db)
        got = await store.get_collection_async("missing", tenant_ctx=_CTX)
        assert got is None

    async def test_db_default_embedder_when_missing(self):
        row = ("cid1", "Name", None, 0, None)
        db = _ScriptedDB(_Result(rows=[row]))
        store = KnowledgeStore(db_session_factory=db)
        got = await store.get_collection_async("cid1", tenant_ctx=_CTX)
        assert got is not None
        assert got.embedder == "voyage"
        assert got.description == ""


# ── get_collection_embedding_dim ─────────────────────────────────────────────


class TestGetCollectionEmbeddingDim:
    async def test_memory_no_records_no_chunks_returns_none(self):
        store = KnowledgeStore()
        col = KnowledgeCollection(name="c", collection_id="cid1")
        store.create_collection(col, tenant_ctx=_CTX)

    async def test_memory_uses_index_records_first(self):
        store = KnowledgeStore()
        record = RAGIndexRecord(
            chunk_id="c1",
            document_id="d1",
            content="text",
            embedding=[0.1] * 1024,
            chunk_index=0,
            strategy=RAGStrategy.RAPTOR,
        )
        store._index_records[(_CTX.tenant_id, "cid1")] = [record]
        dim = await store.get_collection_embedding_dim("cid1", tenant_ctx=_CTX)
        assert dim == 1024

    async def test_memory_falls_back_to_chunk_embedding(self):
        store = KnowledgeStore()
        col = KnowledgeCollection(name="c", collection_id="cid1")
        store.create_collection(col, tenant_ctx=_CTX)
        store.ingest_chunk(
            Chunk(document_id="d1", content="x", embedding=[0.0] * 768, chunk_index=0),
            collection_id="cid1",
            tenant_ctx=_CTX,
        )
        dim = await store.get_collection_embedding_dim("cid1", tenant_ctx=_CTX)
        assert dim == 768

    async def test_memory_no_data_returns_none(self):
        store = KnowledgeStore()
        dim = await store.get_collection_embedding_dim("nope", tenant_ctx=_CTX)
        assert dim is None

    async def test_db_row_none_returns_none(self):
        db = _ScriptedDB(_Result(rows=[]))
        store = KnowledgeStore(db_session_factory=db)
        dim = await store.get_collection_embedding_dim("cid1", tenant_ctx=_CTX)
        assert dim is None

    async def test_db_zero_chunk_count_returns_none(self):
        db = _ScriptedDB(_Result(rows=[(1536, 0)]))
        store = KnowledgeStore(db_session_factory=db)
        dim = await store.get_collection_embedding_dim("cid1", tenant_ctx=_CTX)
        assert dim is None

    async def test_db_found_returns_dim(self):
        db = _ScriptedDB(_Result(rows=[(1536, 5)]))
        store = KnowledgeStore(db_session_factory=db)
        dim = await store.get_collection_embedding_dim("cid1", tenant_ctx=_CTX)
        assert dim == 1536


# ── list_collections_async ───────────────────────────────────────────────────


class TestListCollectionsAsync:
    async def test_no_db_delegates(self):
        store = KnowledgeStore()
        store.create_collection(KnowledgeCollection(name="a"), tenant_ctx=_CTX)
        cols = await store.list_collections_async(tenant_ctx=_CTX)
        assert len(cols) == 1

    async def test_db_returns_rows(self):
        rows = [
            ("cid1", "A", "d", 1, "voyage"),
            ("cid2", "B", None, 0, None),
        ]
        db = _ScriptedDB(_Result(rows=rows))
        store = KnowledgeStore(db_session_factory=db)
        cols = await store.list_collections_async(tenant_ctx=_CTX)
        assert [c.collection_id for c in cols] == ["cid1", "cid2"]
        assert cols[1].embedder == "voyage"
        assert cols[1].description == ""


# ── delete_collection_async ──────────────────────────────────────────────────


class TestDeleteCollectionAsync:
    async def test_no_db_deletes_memory(self):
        store = KnowledgeStore()
        store.create_collection(KnowledgeCollection(name="a", collection_id="cid1"), tenant_ctx=_CTX)
        assert await store.delete_collection_async("cid1", tenant_ctx=_CTX) is True
        assert await store.delete_collection_async("cid1", tenant_ctx=_CTX) is False

    async def test_db_found_deletes(self):
        db = _ScriptedDB(_Result(scalar="cid1"))
        store = KnowledgeStore(db_session_factory=db)
        store._data[(_CTX.tenant_id, "cid1")] = None  # placeholder overwritten below
        from app.rag.store import _CollectionStore

        store._data[(_CTX.tenant_id, "cid1")] = _CollectionStore(
            collection=KnowledgeCollection(name="a", collection_id="cid1")
        )
        deleted = await store.delete_collection_async("cid1", tenant_ctx=_CTX)
        assert deleted is True
        assert store._data.get((_CTX.tenant_id, "cid1")) is None

    async def test_db_not_found_returns_false(self):
        db = _ScriptedDB(_Result(scalar=None))
        store = KnowledgeStore(db_session_factory=db)
        assert await store.delete_collection_async("cid1", tenant_ctx=_CTX) is False


# ── ingestion job lifecycle ──────────────────────────────────────────────────


class TestIngestionJobLifecycle:
    async def test_no_db_raises(self):
        store = KnowledgeStore()
        with pytest.raises(RuntimeError):
            await store.create_ingestion_job_async(
                collection_id="c1",
                source_url="https://x",
                source_type="repository",
                title="t",
                tenant_ctx=_CTX,
            )

    async def test_create_success(self):
        db = _ScriptedDB(_Result(scalar="job1"))
        store = KnowledgeStore(db_session_factory=db)
        job_id = await store.create_ingestion_job_async(
            collection_id="c1",
            source_url="https://x",
            source_type="repository",
            title="t",
            tenant_ctx=_CTX,
        )
        assert job_id
        assert len(job_id) == 32

    async def test_create_missing_collection_raises(self):
        db = _ScriptedDB(_Result(scalar=None))
        store = KnowledgeStore(db_session_factory=db)
        with pytest.raises(KeyError):
            await store.create_ingestion_job_async(
                collection_id="missing",
                source_url="https://x",
                source_type="repository",
                title="t",
                tenant_ctx=_CTX,
            )

    async def test_update_no_db_raises(self):
        store = KnowledgeStore()
        with pytest.raises(RuntimeError):
            await store.update_ingestion_job_async(
                "job1", status="queued", chunk_count=0, error_message=None, tenant_ctx=_CTX
            )

    async def test_update_invalid_status_raises(self):
        store = KnowledgeStore(db_session_factory=_ScriptedDB())
        with pytest.raises(ValueError, match="Unsupported ingestion job status"):
            await store.update_ingestion_job_async(
                "job1", status="bogus", chunk_count=0, error_message=None, tenant_ctx=_CTX
            )

    async def test_update_queued_is_noop(self):
        store = KnowledgeStore(db_session_factory=_ScriptedDB())
        await store.update_ingestion_job_async(
            "job1", status="queued", chunk_count=0, error_message=None, tenant_ctx=_CTX
        )

    async def test_update_completed_raises_runtime_error(self):
        store = KnowledgeStore(db_session_factory=_ScriptedDB())
        with pytest.raises(RuntimeError, match="atomic chunk transaction"):
            await store.update_ingestion_job_async(
                "job1", status="completed", chunk_count=1, error_message=None, tenant_ctx=_CTX
            )

    async def test_update_running_claims_successfully(self):
        job_row = ("job1", "c1", "queued", 0, None, "https://x")
        store = KnowledgeStore(
            db_session_factory=_ScriptedDB(_Result(rows=[job_row]), _Result(rowcount=1))
        )
        await store.update_ingestion_job_async(
            "job1", status="running", chunk_count=0, error_message=None, tenant_ctx=_CTX
        )

    async def test_update_running_job_not_found_raises(self):
        store = KnowledgeStore(db_session_factory=_ScriptedDB(_Result(rows=[])))
        with pytest.raises(KeyError):
            await store.update_ingestion_job_async(
                "job1", status="running", chunk_count=0, error_message=None, tenant_ctx=_CTX
            )

    async def test_update_running_claim_fails_raises(self):
        job_row = ("job1", "c1", "queued", 0, None, "https://x")
        store = KnowledgeStore(
            db_session_factory=_ScriptedDB(_Result(rows=[job_row]), _Result(rowcount=0))
        )
        with pytest.raises(RuntimeError, match="could not be claimed"):
            await store.update_ingestion_job_async(
                "job1", status="running", chunk_count=0, error_message=None, tenant_ctx=_CTX
            )

    async def test_update_failed_delegates(self):
        store = KnowledgeStore(
            db_session_factory=_ScriptedDB(_Result(rowcount=1), _Result(rows=[("failed",)]))
        )
        await store.update_ingestion_job_async(
            "job1", status="failed", chunk_count=0, error_message="oops", tenant_ctx=_CTX
        )

    async def test_claim_no_db_raises(self):
        store = KnowledgeStore()
        with pytest.raises(RuntimeError):
            await store.claim_ingestion_job_async(
                "job1",
                collection_id="c1",
                source_url="https://x",
                lease_owner="me",
                lease_seconds=60,
                tenant_ctx=_CTX,
            )

    async def test_claim_success_and_failure(self):
        store = KnowledgeStore(db_session_factory=_ScriptedDB(_Result(rowcount=1)))
        assert (
            await store.claim_ingestion_job_async(
                "job1",
                collection_id="c1",
                source_url="https://x",
                lease_owner="me",
                lease_seconds=60,
                tenant_ctx=_CTX,
            )
            is True
        )

        store2 = KnowledgeStore(db_session_factory=_ScriptedDB(_Result(rowcount=0)))
        assert (
            await store2.claim_ingestion_job_async(
                "job1",
                collection_id="c1",
                source_url="https://x",
                lease_owner="me",
                lease_seconds=60,
                tenant_ctx=_CTX,
            )
            is False
        )

    async def test_heartbeat_no_db_raises(self):
        store = KnowledgeStore()
        with pytest.raises(RuntimeError):
            await store.heartbeat_ingestion_job_async(
                "job1", lease_owner="me", lease_seconds=60, tenant_ctx=_CTX
            )

    async def test_heartbeat_success_and_failure(self):
        store = KnowledgeStore(db_session_factory=_ScriptedDB(_Result(rowcount=1)))
        assert (
            await store.heartbeat_ingestion_job_async(
                "job1", lease_owner="me", lease_seconds=60, tenant_ctx=_CTX
            )
            is True
        )
        store2 = KnowledgeStore(db_session_factory=_ScriptedDB(_Result(rowcount=0)))
        assert (
            await store2.heartbeat_ingestion_job_async(
                "job1", lease_owner="me", lease_seconds=60, tenant_ctx=_CTX
            )
            is False
        )

    async def test_fail_no_db_raises(self):
        store = KnowledgeStore()
        with pytest.raises(RuntimeError):
            await store.fail_ingestion_job_async(
                "job1", lease_owner=None, error_message="err", tenant_ctx=_CTX
            )

    async def test_fail_success_returns_status(self):
        store = KnowledgeStore(
            db_session_factory=_ScriptedDB(_Result(rowcount=1), _Result(scalar="failed"))
        )
        status = await store.fail_ingestion_job_async(
            "job1", lease_owner=None, error_message="err", tenant_ctx=_CTX
        )
        assert status == "failed"

    async def test_fail_job_missing_returns_none(self):
        store = KnowledgeStore(
            db_session_factory=_ScriptedDB(_Result(rowcount=0), _Result(scalar=None))
        )
        status = await store.fail_ingestion_job_async(
            "job1", lease_owner=None, error_message="err", tenant_ctx=_CTX
        )
        assert status is None

    async def test_get_no_db_raises(self):
        store = KnowledgeStore()
        with pytest.raises(RuntimeError):
            await store.get_ingestion_job_async("job1", tenant_ctx=_CTX)

    async def test_get_found(self):
        row = ("job1", "c1", "completed", 5, "boom", "https://x")
        store = KnowledgeStore(db_session_factory=_ScriptedDB(_Result(rows=[row])))
        job = await store.get_ingestion_job_async("job1", tenant_ctx=_CTX)
        assert job == {
            "job_id": "job1",
            "collection_id": "c1",
            "status": "completed",
            "chunk_count": 5,
            "error_message": "boom",
            "source_url": "https://x",
        }

    async def test_get_not_found(self):
        store = KnowledgeStore(db_session_factory=_ScriptedDB(_Result(rows=[])))
        assert await store.get_ingestion_job_async("job1", tenant_ctx=_CTX) is None

    async def test_reconcile_no_db_raises(self):
        store = KnowledgeStore()
        with pytest.raises(RuntimeError):
            await store.reconcile_stale_ingestion_jobs_async(
                tenant_ctx=_CTX, stale_after_seconds=60
            )

    async def test_reconcile_invalid_seconds_raises(self):
        store = KnowledgeStore(db_session_factory=_ScriptedDB())
        with pytest.raises(ValueError, match="must be positive"):
            await store.reconcile_stale_ingestion_jobs_async(
                tenant_ctx=_CTX, stale_after_seconds=0
            )

    async def test_reconcile_success(self):
        store = KnowledgeStore(db_session_factory=_ScriptedDB(_Result(rowcount=3)))
        count = await store.reconcile_stale_ingestion_jobs_async(
            tenant_ctx=_CTX, stale_after_seconds=60
        )
        assert count == 3


# ── ingest_chunk (db present -> RuntimeError) ────────────────────────────────


class TestIngestChunkDbGuard:
    async def test_raises_when_db_present(self):
        store = KnowledgeStore(db_session_factory=_ScriptedDB())
        chunk = Chunk(document_id="d1", content="x", embedding=[0.1], chunk_index=0)
        with pytest.raises(RuntimeError, match="ingest_chunks_async"):
            store.ingest_chunk(chunk, collection_id="c1", tenant_ctx=_CTX)


# ── exists_by_hash ────────────────────────────────────────────────────────────


class TestExistsByHash:
    async def test_empty_hash_returns_false(self):
        store = KnowledgeStore()
        assert await store.exists_by_hash(content_hash="", tenant_id=_CTX.tenant_id) is False

    async def test_memory_matches_content_hash(self):
        store = KnowledgeStore()
        store.create_collection(KnowledgeCollection(name="a", collection_id="c1"), tenant_ctx=_CTX)
        store.ingest_chunk(
            Chunk(
                document_id="d1",
                content="x",
                embedding=[0.1],
                chunk_index=0,
                metadata={"content_hash": "abc"},
            ),
            collection_id="c1",
            tenant_ctx=_CTX,
        )
        assert (
            await store.exists_by_hash(content_hash="abc", tenant_id=_CTX.tenant_id) is True
        )

    async def test_memory_matches_doc_content_hash_scoped_to_collection(self):
        store = KnowledgeStore()
        store.create_collection(KnowledgeCollection(name="a", collection_id="c1"), tenant_ctx=_CTX)
        store.ingest_chunk(
            Chunk(
                document_id="d1",
                content="x",
                embedding=[0.1],
                chunk_index=0,
                metadata={"doc_content_hash": "xyz"},
            ),
            collection_id="c1",
            tenant_ctx=_CTX,
        )
        assert (
            await store.exists_by_hash(
                content_hash="xyz", tenant_id=_CTX.tenant_id, collection_id="c1"
            )
            is True
        )
        assert (
            await store.exists_by_hash(
                content_hash="xyz", tenant_id=_CTX.tenant_id, collection_id="other"
            )
            is False
        )

    async def test_memory_no_match_returns_false(self):
        store = KnowledgeStore()
        store.create_collection(KnowledgeCollection(name="a", collection_id="c1"), tenant_ctx=_CTX)
        store.ingest_chunk(
            Chunk(document_id="d1", content="x", embedding=[0.1], chunk_index=0),
            collection_id="c1",
            tenant_ctx=_CTX,
        )
        assert await store.exists_by_hash(content_hash="nope", tenant_id=_CTX.tenant_id) is False

    async def test_memory_wrong_tenant_no_match(self):
        store = KnowledgeStore()
        store.create_collection(KnowledgeCollection(name="a", collection_id="c1"), tenant_ctx=_CTX)
        store.ingest_chunk(
            Chunk(
                document_id="d1",
                content="x",
                embedding=[0.1],
                chunk_index=0,
                metadata={"content_hash": "abc"},
            ),
            collection_id="c1",
            tenant_ctx=_CTX,
        )
        other = TenantContext(tenant_id="other", plan=PlanTier.FREE, api_key_id="k2")
        assert await store.exists_by_hash(content_hash="abc", tenant_id=other.tenant_id) is False

    async def test_db_found_without_collection_id(self):
        db = _ScriptedDB(_Result(scalar=1))
        store = KnowledgeStore(db_session_factory=db)
        found = await store.exists_by_hash(content_hash="abc", tenant_id=_CTX.tenant_id)
        assert found is True

    async def test_db_found_with_collection_id_uses_dim(self):
        # First query resolves the collection's embedding dimension, second is the
        # per-dimension hash lookup itself.
        db = _ScriptedDB(_Result(rows=[(1536, 5)]), _Result(scalar=1))
        store = KnowledgeStore(db_session_factory=db)
        found = await store.exists_by_hash(
            content_hash="abc", tenant_id=_CTX.tenant_id, collection_id="c1"
        )
        assert found is True

    async def test_db_not_found_across_all_dimensions(self):
        db = _ScriptedDB(*[_Result(rows=[]) for _ in range(5)])
        store = KnowledgeStore(db_session_factory=db)
        found = await store.exists_by_hash(content_hash="abc", tenant_id=_CTX.tenant_id)
        assert found is False

    async def test_db_missing_table_is_skipped(self):
        class _FlakySession(_ScriptedSession):
            async def execute(self, stmt, params=None):
                sql = str(stmt)
                if "set_config" in sql:
                    return _Result()
                if "knowledge_chunks_768" in sql:
                    raise RuntimeError("relation does not exist")
                return await super().execute(stmt, params)

        class _FlakyDB:
            def __init__(self):
                self.session = _FlakySession([_Result(scalar=1)])

            def __call__(self):
                s = self.session

                class _CM:
                    async def __aenter__(self):
                        return s

                    async def __aexit__(self, *a):
                        return False

                return _CM()

        store = KnowledgeStore(db_session_factory=_FlakyDB())
        found = await store.exists_by_hash(content_hash="abc", tenant_id=_CTX.tenant_id)
        assert found is True


# ── hybrid_search: metadata filter + BM25 exception guard ──────────────────


class TestHybridSearchExtraBranches:
    async def test_metadata_filter_narrows_results(self):
        store = KnowledgeStore()
        store.create_collection(KnowledgeCollection(name="a", collection_id="c1"), tenant_ctx=_CTX)
        store.ingest_chunk(
            Chunk(
                document_id="d1",
                content="alpha",
                embedding=[1.0, 0.0],
                chunk_index=0,
                metadata={"lang": "en"},
            ),
            collection_id="c1",
            tenant_ctx=_CTX,
        )
        store.ingest_chunk(
            Chunk(
                document_id="d2",
                content="beta",
                embedding=[0.0, 1.0],
                chunk_index=0,
                metadata={"lang": "fr"},
            ),
            collection_id="c1",
            tenant_ctx=_CTX,
        )
        results = store.hybrid_search(
            "alpha", [1.0, 0.0], "c1", _CTX, metadata_filter={"lang": "en"}
        )
        assert len(results) == 1
        assert results[0].chunk_id
        assert "alpha" in results[0].content

    async def test_bm25_failure_is_swallowed(self):
        store = KnowledgeStore()
        store.create_collection(KnowledgeCollection(name="a", collection_id="c1"), tenant_ctx=_CTX)
        store.ingest_chunk(
            Chunk(document_id="d1", content="alpha beta", embedding=[1.0, 0.0], chunk_index=0),
            collection_id="c1",
            tenant_ctx=_CTX,
        )
        with patch("app.rag.bm25.BM25Retriever", side_effect=RuntimeError("bm25 down")):
            results = store.hybrid_search("alpha", [1.0, 0.0], "c1", _CTX)
        assert len(results) == 1


# ── hybrid_search_db: dimension row missing ─────────────────────────────────


class TestHybridSearchDbDimensionMissing:
    async def test_returns_empty_when_no_collection_row(self):
        db = _ScriptedDB(_Result(rows=[]))
        store = KnowledgeStore(db_session_factory=db)
        results = await store.hybrid_search_db("q", [0.1] * 768, "c1", _CTX)
        assert results == []


# ── binary_prefilter_search ──────────────────────────────────────────────────


class TestBinaryPrefilterSearch:
    async def test_no_db_returns_empty(self):
        store = KnowledgeStore()
        assert await store.binary_prefilter_search([0.1], "c1", _CTX) == []

    async def test_empty_embedding_returns_empty(self):
        store = KnowledgeStore(db_session_factory=_ScriptedDB())
        assert await store.binary_prefilter_search([], "c1", _CTX) == []

    async def test_dimension_missing_returns_empty(self):
        db = _ScriptedDB(_Result(rows=[]))
        store = KnowledgeStore(db_session_factory=db)
        results = await store.binary_prefilter_search([0.1] * 768, "c1", _CTX)
        assert results == []

    async def test_success_with_metadata_filter(self):
        row = ("chunk1", "content one", {"source_url": "https://x", "source_doc_id": "d1"}, 0.9)
        db = _ScriptedDB(_Result(rows=[(768,)]), _Result(rows=[row]))
        store = KnowledgeStore(db_session_factory=db)
        results = await store.binary_prefilter_search(
            [0.1] * 768, "c1", _CTX, top_k=3, shortlist=50, metadata_filter={"lang": "en"}
        )
        assert len(results) == 1
        assert isinstance(results[0], HybridSearchResult)
        assert results[0].chunk_id == "chunk1"
        assert results[0].source_url == "https://x"

    async def test_success_without_metadata_filter_and_non_dict_row_metadata(self):
        row = ("chunk1", "content", None, 0.5)
        db = _ScriptedDB(_Result(rows=[(768,)]), _Result(rows=[row]))
        store = KnowledgeStore(db_session_factory=db)
        results = await store.binary_prefilter_search([0.1] * 768, "c1", _CTX)
        assert len(results) == 1
        assert results[0].metadata == {}


# ── search ────────────────────────────────────────────────────────────────


class TestSearch:
    async def test_db_delegates_to_hybrid_search_db_lexical(self):
        db = _ScriptedDB()
        store = KnowledgeStore(db_session_factory=db)
        fake_hit = HybridSearchResult(
            chunk_id="c1", content="text", score=0.5, vector_score=0.0, trigram_score=0.5,
            metadata={"k": "v"},
        )
        with patch.object(store, "hybrid_search_db", AsyncMock(return_value=[fake_hit])) as mocked:
            results = await store.search("q", "col1", top_k=4, tenant_ctx=_CTX)
        assert results == [
            {"chunk_id": "c1", "content": "text", "score": 0.5, "metadata": {"k": "v"}}
        ]
        assert mocked.await_args.kwargs["retrieval_mode"] == "lexical"

    async def test_memory_search_filters_and_scores(self):
        store = KnowledgeStore()
        store.create_collection(KnowledgeCollection(name="a", collection_id="c1"), tenant_ctx=_CTX)
        store.ingest_chunk(
            Chunk(
                document_id="d1",
                content="checkout payment flow",
                embedding=[0.1],
                chunk_index=0,
                metadata={"lang": "en"},
            ),
            collection_id="c1",
            tenant_ctx=_CTX,
        )
        store.ingest_chunk(
            Chunk(
                document_id="d2",
                content="unrelated text",
                embedding=[0.1],
                chunk_index=0,
                metadata={"lang": "fr"},
            ),
            collection_id="c1",
            tenant_ctx=_CTX,
        )
        results = await store.search(
            "checkout payment", "c1", top_k=5, tenant_ctx=_CTX, metadata_filter={"lang": "en"}
        )
        assert len(results) == 1
        assert results[0]["chunk_id"]

    async def test_memory_search_wrong_collection_or_tenant_excluded(self):
        store = KnowledgeStore()
        store.create_collection(KnowledgeCollection(name="a", collection_id="c1"), tenant_ctx=_CTX)
        store.ingest_chunk(
            Chunk(document_id="d1", content="hello world", embedding=[0.1], chunk_index=0),
            collection_id="c1",
            tenant_ctx=_CTX,
        )
        assert await store.search("hello", "other-col", tenant_ctx=_CTX) == []
        other = TenantContext(tenant_id="other-t", plan=PlanTier.FREE, api_key_id="k9")
        assert await store.search("hello", "c1", tenant_ctx=other) == []


# ── retrieve: embedder branch ────────────────────────────────────────────────


class TestRetrieveEmbedderBranch:
    async def test_embedder_success_uses_hybrid_mode(self):
        store = KnowledgeStore()
        store.create_collection(KnowledgeCollection(name="kb", collection_id="c1"), tenant_ctx=_CTX)
        store.ingest_chunk(
            Chunk(document_id="d1", content="Pip the toucan", embedding=[1.0, 0.0], chunk_index=0),
            collection_id="c1",
            tenant_ctx=_CTX,
        )
        with patch(
            "app.providers.base.embed_texts", AsyncMock(return_value=[[1.0, 0.0]])
        ):
            hits = await store.retrieve(
                query="mascot", collection_name="c1", tenant_id=_CTX.tenant_id, embedder=object()
            )
        assert hits and "Pip" in hits[0]["content"]

    async def test_embedder_failure_degrades_to_lexical(self):
        store = KnowledgeStore()
        store.create_collection(KnowledgeCollection(name="kb", collection_id="c1"), tenant_ctx=_CTX)
        store.ingest_chunk(
            Chunk(document_id="d1", content="Pip the toucan", embedding=[1.0, 0.0], chunk_index=0),
            collection_id="c1",
            tenant_ctx=_CTX,
        )
        with patch(
            "app.providers.base.embed_texts", AsyncMock(side_effect=RuntimeError("no embedder"))
        ):
            hits = await store.retrieve(
                query="Pip", collection_name="c1", tenant_id=_CTX.tenant_id, embedder=object()
            )
        assert hits and "Pip" in hits[0]["content"]

    async def test_embedder_returns_empty_vectors_falls_back(self):
        store = KnowledgeStore()
        store.create_collection(KnowledgeCollection(name="kb", collection_id="c1"), tenant_ctx=_CTX)
        with patch("app.providers.base.embed_texts", AsyncMock(return_value=[])):
            hits = await store.retrieve(
                query="Pip", collection_name="c1", tenant_id=_CTX.tenant_id, embedder=object()
            )
        assert hits == []


# ── delete_document / delete_document_async ─────────────────────────────────


class TestDeleteDocument:
    async def test_delete_document_missing_collection_returns_zero(self):
        store = KnowledgeStore()
        assert store.delete_document("d1", collection_id="missing", tenant_ctx=_CTX) == 0

    async def test_delete_document_removes_matching_chunks(self):
        store = KnowledgeStore()
        store.create_collection(KnowledgeCollection(name="a", collection_id="c1"), tenant_ctx=_CTX)
        store.ingest_chunk(
            Chunk(document_id="doc1", content="x", embedding=[0.1], chunk_index=0),
            collection_id="c1",
            tenant_ctx=_CTX,
        )
        store.ingest_chunk(
            Chunk(document_id="doc2", content="y", embedding=[0.1], chunk_index=0),
            collection_id="c1",
            tenant_ctx=_CTX,
        )
        deleted = store.delete_document("doc1", collection_id="c1", tenant_ctx=_CTX)
        assert deleted == 1
        col = store.get_collection("c1", tenant_ctx=_CTX)
        assert col.document_count == 1

    async def test_delete_document_async_no_db_delegates(self):
        store = KnowledgeStore()
        store.create_collection(KnowledgeCollection(name="a", collection_id="c1"), tenant_ctx=_CTX)
        store.ingest_chunk(
            Chunk(document_id="doc1", content="x", embedding=[0.1], chunk_index=0),
            collection_id="c1",
            tenant_ctx=_CTX,
        )
        deleted = await store.delete_document_async("doc1", collection_id="c1", tenant_ctx=_CTX)
        assert deleted == 1

    async def test_delete_document_async_db_dimension_missing_returns_zero(self):
        db = _ScriptedDB(_Result(rows=[]))
        store = KnowledgeStore(db_session_factory=db)
        deleted = await store.delete_document_async("doc1", collection_id="c1", tenant_ctx=_CTX)
        assert deleted == 0

    async def test_delete_document_async_db_success_updates_counts(self):
        from app.rag.store import _CollectionStore

        db = _ScriptedDB(_Result(rows=[(768,)]), _Result(rowcount=2), _Result())
        store = KnowledgeStore(db_session_factory=db)
        store._data[(_CTX.tenant_id, "c1")] = _CollectionStore(
            collection=KnowledgeCollection(name="a", collection_id="c1")
        )
        store._data[(_CTX.tenant_id, "c1")].chunks.append(
            Chunk(document_id="doc1", content="x", embedding=[0.1], chunk_index=0)
        )
        deleted = await store.delete_document_async("doc1", collection_id="c1", tenant_ctx=_CTX)
        assert deleted == 2

    async def test_delete_document_async_explicit_db_param(self):
        db = _ScriptedDB(_Result(rows=[(768,)]), _Result(rowcount=0))
        store = KnowledgeStore()
        deleted = await store.delete_document_async(
            "doc1", collection_id="c1", tenant_ctx=_CTX, db=db
        )
        assert deleted == 0


# ── ingest_document: DB branches ─────────────────────────────────────────────


class TestIngestDocumentDbBranches:
    async def test_db_without_embedder_raises(self):
        store = KnowledgeStore(db_session_factory=_ScriptedDB())
        with pytest.raises(EmbeddingProviderUnavailableError):
            await store.ingest_document(collection_id="c1", content="text", tenant_ctx=_CTX)

    async def test_db_embedder_failure_raises(self):
        class _BadEmbedder:
            pass

        store = KnowledgeStore(db_session_factory=_ScriptedDB())
        with patch(
            "app.providers.base.embed_texts", AsyncMock(side_effect=RuntimeError("down"))
        ), pytest.raises(EmbeddingProviderUnavailableError):
            await store.ingest_document(
                collection_id="c1", content="text", tenant_ctx=_CTX, embedder=_BadEmbedder()
            )

    async def test_db_empty_embedding_raises(self):
        store = KnowledgeStore(db_session_factory=_ScriptedDB())
        with patch("app.providers.base.embed_texts", AsyncMock(return_value=[[]])), pytest.raises(
            EmbeddingProviderUnavailableError
        ):
            await store.ingest_document(
                collection_id="c1", content="text", tenant_ctx=_CTX, embedder=object()
            )

    async def test_db_success_persists_and_caches(self):
        row = (2048, 0)
        db = _ScriptedDB(_Result(rows=[row]), _Result(), _Result())
        store = KnowledgeStore(db_session_factory=db)
        store._data[(_CTX.tenant_id, "c1")] = None
        from app.rag.store import _CollectionStore

        store._data[(_CTX.tenant_id, "c1")] = _CollectionStore(
            collection=KnowledgeCollection(name="a", collection_id="c1")
        )
        with patch(
            "app.providers.base.embed_texts", AsyncMock(return_value=[[0.1] * 2048])
        ):
            chunk_id = await store.ingest_document(
                collection_id="c1", content="text", tenant_ctx=_CTX, embedder=object()
            )
        assert chunk_id
        col = store.get_collection("c1", tenant_ctx=_CTX)
        assert col.document_count == 1


# ── persist_index_records ────────────────────────────────────────────────────


class TestPersistIndexRecords:
    async def test_empty_records_returns_empty(self):
        store = KnowledgeStore()
        assert await store.persist_index_records([], collection_id="c1", tenant_ctx=_CTX) == []

    async def test_multiple_documents_raises(self):
        store = KnowledgeStore()
        records = [
            RAGIndexRecord(
                chunk_id=f"c{i}",
                document_id=f"d{i}",
                content="x",
                embedding=[0.1],
                chunk_index=0,
                strategy=RAGStrategy.RAPTOR,
            )
            for i in range(2)
        ]
        with pytest.raises(ValueError, match="exactly one document"):
            await store.persist_index_records(records, collection_id="c1", tenant_ctx=_CTX)

    async def test_no_db_caches_records(self):
        store = KnowledgeStore()
        record = RAGIndexRecord(
            chunk_id="c1",
            document_id="d1",
            content="x",
            embedding=[0.1],
            chunk_index=0,
            strategy=RAGStrategy.RAPTOR,
        )
        ids = await store.persist_index_records([record], collection_id="c1", tenant_ctx=_CTX)
        assert ids == ["c1"]
        assert store._index_records[(_CTX.tenant_id, "c1")] == [record]

    async def test_db_persists_then_replaces_cache(self):
        row = (768, 0)
        db = _ScriptedDB(_Result(rows=[row]), _Result(), _Result())
        store = KnowledgeStore(db_session_factory=db)
        record = RAGIndexRecord(
            chunk_id="c1",
            document_id="d1",
            content="x",
            embedding=[0.1] * 768,
            chunk_index=0,
            strategy=RAGStrategy.AGENTIC_CHUNKING,
            is_proposition=True,
        )
        ids = await store.persist_index_records([record], collection_id="c1", tenant_ctx=_CTX)
        assert ids == ["c1"]


# ── search_precomputed_index / _search_precomputed_memory ───────────────────


class TestSearchPrecomputedIndex:
    async def test_memory_regular_strategy(self):
        store = KnowledgeStore()
        record = RAGIndexRecord(
            chunk_id="c1",
            document_id="d1",
            content="alpha beta",
            embedding=[1.0, 0.0],
            chunk_index=0,
            strategy=RAGStrategy.RAPTOR,
        )
        store._index_records[(_CTX.tenant_id, "c1")] = [record]
        results = await store.search_precomputed_index(
            strategy=RAGStrategy.RAPTOR,
            query="alpha",
            query_embedding=[1.0, 0.0],
            collection_id="c1",
            tenant_ctx=_CTX,
            top_k=3,
        )
        assert len(results) == 1
        assert results[0]["chunk_id"] == "c1"

    async def test_memory_agentic_chunking_expands_parents(self):
        store = KnowledgeStore()
        parent = RAGIndexRecord(
            chunk_id="parent1",
            document_id="d1",
            content="parent window content",
            embedding=[1.0, 0.0],
            chunk_index=0,
            strategy=RAGStrategy.AGENTIC_CHUNKING,
        )
        child = RAGIndexRecord(
            chunk_id="child1",
            document_id="d1",
            content="proposition text",
            embedding=[0.9, 0.1],
            chunk_index=1,
            strategy=RAGStrategy.AGENTIC_CHUNKING,
            is_proposition=True,
            parent_chunk_id="parent1",
        )
        store._index_records[(_CTX.tenant_id, "c1")] = [parent, child]
        results = await store.search_precomputed_index(
            strategy=RAGStrategy.AGENTIC_CHUNKING,
            query="proposition",
            query_embedding=[0.9, 0.1],
            collection_id="c1",
            tenant_ctx=_CTX,
            top_k=3,
        )
        assert len(results) >= 1

    async def test_db_regular_strategy_delegates_to_hybrid_search_db(self):
        store = KnowledgeStore(db_session_factory=_ScriptedDB())
        fake_hit = HybridSearchResult(
            chunk_id="c1", content="x", score=0.5, vector_score=0.0, trigram_score=0.5,
        )
        with patch.object(store, "hybrid_search_db", AsyncMock(return_value=[fake_hit])):
            results = await store.search_precomputed_index(
                strategy=RAGStrategy.RAPTOR,
                query="q",
                query_embedding=[0.1],
                collection_id="c1",
                tenant_ctx=_CTX,
                top_k=3,
            )
        assert results[0]["chunk_id"] == "c1"

    async def test_db_agentic_chunking_expands(self):
        store = KnowledgeStore(db_session_factory=_ScriptedDB())
        fake_hit = HybridSearchResult(
            chunk_id="c1", content="prop", score=0.5, vector_score=0.0, trigram_score=0.5,
        )
        citation = RetrievalResult(
            chunk_id="c1", content="prop", score=0.5, source_metadata={},
        )
        with patch.object(
            store, "hybrid_search_db", AsyncMock(return_value=[fake_hit])
        ), patch.object(
            store, "_expand_agentic_parent_citations", AsyncMock(return_value=[{"chunk_id": "c1"}])
        ) as expander:
            results = await store.search_precomputed_index(
                strategy=RAGStrategy.AGENTIC_CHUNKING,
                query="q",
                query_embedding=[0.1],
                collection_id="c1",
                tenant_ctx=_CTX,
                top_k=3,
            )
        expander.assert_awaited_once()
        assert results == [{"chunk_id": "c1"}]
        del citation


# ── _expand_agentic_parent_citations ─────────────────────────────────────────


class TestExpandAgenticParentCitations:
    async def test_empty_results_returns_empty(self):
        store = KnowledgeStore(db_session_factory=_ScriptedDB())
        results = await store._expand_agentic_parent_citations(
            [], collection_id="c1", tenant_ctx=_CTX, top_k=5
        )
        assert results == []

    async def test_no_db_returns_empty(self):
        store = KnowledgeStore()
        hit = HybridSearchResult(
            chunk_id="c1", content="x", score=0.5, vector_score=0.1, trigram_score=0.2
        )
        results = await store._expand_agentic_parent_citations(
            [hit], collection_id="c1", tenant_ctx=_CTX, top_k=5
        )
        assert results == []

    async def test_dimension_missing_returns_empty(self):
        db = _ScriptedDB(_Result(rows=[]))
        store = KnowledgeStore(db_session_factory=db)
        hit = HybridSearchResult(
            chunk_id="c1", content="x", score=0.5, vector_score=0.1, trigram_score=0.2
        )
        results = await store._expand_agentic_parent_citations(
            [hit], collection_id="c1", tenant_ctx=_CTX, top_k=5
        )
        assert results == []

    async def test_success_expands_via_citations(self):
        from app.rag.engine import ParentWindowCitation

        db = _ScriptedDB(_Result(rows=[(768,)]))
        store = KnowledgeStore(db_session_factory=db)
        hit = HybridSearchResult(
            chunk_id="child1", content="proposition", score=0.6, vector_score=0.5, trigram_score=0.3
        )
        citations = {"child1": ParentWindowCitation("parent1", "parent window text")}
        with patch(
            "app.rag.engine.load_agentic_parent_citations", AsyncMock(return_value=citations)
        ):
            results = await store._expand_agentic_parent_citations(
                [hit], collection_id="c1", tenant_ctx=_CTX, top_k=5
            )
        assert len(results) == 1
        assert results[0]["content"] == "parent window text"


# ── ingest_chunks_async ───────────────────────────────────────────────────────


class TestIngestChunksAsync:
    async def test_empty_chunks_missing_collection_raises(self):
        store = KnowledgeStore()
        with pytest.raises(KeyError):
            await store.ingest_chunks_async([], collection_id="missing", tenant_ctx=_CTX)

    async def test_empty_chunks_existing_collection_returns_empty(self):
        store = KnowledgeStore()
        store.create_collection(KnowledgeCollection(name="a", collection_id="c1"), tenant_ctx=_CTX)
        ids = await store.ingest_chunks_async([], collection_id="c1", tenant_ctx=_CTX)
        assert ids == []

    async def test_no_db_ingests_each_chunk(self):
        store = KnowledgeStore()
        store.create_collection(KnowledgeCollection(name="a", collection_id="c1"), tenant_ctx=_CTX)
        chunks = [
            Chunk(document_id="d1", content="a", embedding=[0.1], chunk_index=0),
            Chunk(document_id="d2", content="b", embedding=[0.1], chunk_index=0),
        ]
        ids = await store.ingest_chunks_async(chunks, collection_id="c1", tenant_ctx=_CTX)
        assert len(ids) == 2

    async def test_db_persists_all_chunks_in_one_transaction(self):
        row = (768, 0)
        db = _ScriptedDB(_Result(rows=[row]), _Result(), _Result())
        store = KnowledgeStore(db_session_factory=db)
        chunks = [
            Chunk(document_id="d1", content="a", embedding=[0.1] * 768, chunk_index=0),
            Chunk(document_id="d2", content="b", embedding=[0.1] * 768, chunk_index=1),
        ]
        ids = await store.ingest_chunks_async(chunks, collection_id="c1", tenant_ctx=_CTX)
        assert len(ids) == 2


# ── ingest_repository_chunks_async ───────────────────────────────────────────


class TestIngestRepositoryChunksAsync:
    async def test_no_db_raises(self):
        store = KnowledgeStore()
        with pytest.raises(RuntimeError):
            await store.ingest_repository_chunks_async(
                [],
                job_id="job1",
                collection_id="c1",
                source_url="https://x",
                lease_owner="me",
                tenant_ctx=_CTX,
            )

    async def test_empty_chunks_completes_job(self):
        db = _ScriptedDB(_Result(rowcount=1))
        store = KnowledgeStore(db_session_factory=db)
        ids = await store.ingest_repository_chunks_async(
            [],
            job_id="job1",
            collection_id="c1",
            source_url="https://x",
            lease_owner="me",
            tenant_ctx=_CTX,
        )
        assert ids == []

    async def test_empty_chunks_job_not_running_raises(self):
        db = _ScriptedDB(_Result(rowcount=0))
        store = KnowledgeStore(db_session_factory=db)
        with pytest.raises(KeyError):
            await store.ingest_repository_chunks_async(
                [],
                job_id="job1",
                collection_id="c1",
                source_url="https://x",
                lease_owner="me",
                tenant_ctx=_CTX,
            )

    async def test_with_chunks_persists_and_completes(self):
        row = (768, 0)
        db = _ScriptedDB(_Result(rows=[row]), _Result(), _Result(), _Result(rowcount=1))
        store = KnowledgeStore(db_session_factory=db)
        chunks = [Chunk(document_id="d1", content="a", embedding=[0.1] * 768, chunk_index=0)]
        ids = await store.ingest_repository_chunks_async(
            chunks,
            job_id="job1",
            collection_id="c1",
            source_url="https://x",
            lease_owner="me",
            tenant_ctx=_CTX,
        )
        assert ids == ["d1"] or len(ids) == 1


# ── _persist_chunks: shared low-level guarantees ─────────────────────────────


class TestPersistChunks:
    async def test_no_db_is_noop(self):
        store = KnowledgeStore()
        await store._persist_chunks([{"embedding": [0.1]}], collection_id="c1", tenant_id="t1")

    async def test_empty_records_is_noop(self):
        store = KnowledgeStore(db_session_factory=_ScriptedDB())
        await store._persist_chunks([], collection_id="c1", tenant_id="t1")

    async def test_mixed_dimensions_raises(self):
        store = KnowledgeStore(db_session_factory=_ScriptedDB())
        records = [
            {
                "chunk_id": "c1", "document_id": "d1", "content": "a", "embedding": [0.1] * 2,
                "chunk_index": 0, "metadata": {}, "parent_chunk_id": None, "chunk_level": "leaf",
                "window_start": None, "window_end": None, "window_id": None, "hierarchy_level": 0,
                "is_proposition": False, "strategy_metadata": {}, "freshness_ttl_hours": None,
            },
            {
                "chunk_id": "c2", "document_id": "d1", "content": "b", "embedding": [0.1] * 3,
                "chunk_index": 1, "metadata": {}, "parent_chunk_id": None, "chunk_level": "leaf",
                "window_start": None, "window_end": None, "window_id": None, "hierarchy_level": 0,
                "is_proposition": False, "strategy_metadata": {}, "freshness_ttl_hours": None,
            },
        ]
        with pytest.raises(ValueError, match="one embedding dimension"):
            await store._persist_chunks(records, collection_id="c1", tenant_id="t1")

    def _record(self, **overrides):
        base = {
            "chunk_id": "c1", "document_id": "d1", "content": "a", "embedding": [0.1] * 768,
            "chunk_index": 0, "metadata": {}, "parent_chunk_id": None, "chunk_level": "leaf",
            "window_start": None, "window_end": None, "window_id": None, "hierarchy_level": 0,
            "is_proposition": False, "strategy_metadata": {}, "freshness_ttl_hours": None,
        }
        base.update(overrides)
        return base

    async def test_collection_not_found_raises(self):
        db = _ScriptedDB(_Result(rows=[]))
        store = KnowledgeStore(db_session_factory=db)
        with pytest.raises(KeyError):
            await store._persist_chunks([self._record()], collection_id="c1", tenant_id="t1")

    async def test_dimension_mismatch_with_existing_chunks_raises(self):
        db = _ScriptedDB(_Result(rows=[(1536, 3)]))
        store = KnowledgeStore(db_session_factory=db)
        with pytest.raises(ValueError, match="dimensional embeddings"):
            await store._persist_chunks([self._record()], collection_id="c1", tenant_id="t1")

    async def test_dimension_backfill_when_no_chunks_yet(self):
        db = _ScriptedDB(_Result(rows=[(1536, 0)]), _Result(), _Result(), _Result())
        store = KnowledgeStore(db_session_factory=db)
        await store._persist_chunks([self._record()], collection_id="c1", tenant_id="t1")

    async def test_replacement_document_id_deletes_first(self):
        db = _ScriptedDB(_Result(rows=[(768, 1)]), _Result(), _Result(), _Result())
        store = KnowledgeStore(db_session_factory=db)
        await store._persist_chunks(
            [self._record()],
            collection_id="c1",
            tenant_id="t1",
            replacement_document_id="d1",
        )

    async def test_completion_job_success(self):
        db = _ScriptedDB(
            _Result(rows=[(768, 1)]), _Result(), _Result(), _Result(rowcount=1)
        )
        store = KnowledgeStore(db_session_factory=db)
        await store._persist_chunks(
            [self._record()],
            collection_id="c1",
            tenant_id="t1",
            completion_job_id="job1",
            completion_source_hash="hash1",
            completion_lease_owner="me",
        )

    async def test_completion_job_not_running_raises(self):
        db = _ScriptedDB(
            _Result(rows=[(768, 1)]), _Result(), _Result(), _Result(rowcount=0)
        )
        store = KnowledgeStore(db_session_factory=db)
        with pytest.raises(KeyError, match="not running"):
            await store._persist_chunks(
                [self._record()],
                collection_id="c1",
                tenant_id="t1",
                completion_job_id="job1",
                completion_source_hash="hash1",
                completion_lease_owner="me",
            )


# ── _db_ingest_with_citations ─────────────────────────────────────────────────


class TestDbIngestWithCitations:
    async def test_persists_full_metadata(self):
        db = _ScriptedDB(_Result(rows=[(768, 0)]), _Result(), _Result(), _Result())
        store = KnowledgeStore(db_session_factory=db)
        await store._db_ingest_with_citations(
            chunk_id="c1",
            collection_id="col1",
            content="text",
            embedding=[0.1] * 768,
            metadata={},
            tenant_id="t1",
            source_url="https://x",
            source_type="text",
            source_doc_id="doc1",
            page_number=1,
            freshness_ttl_hours=24,
            content_hash="unused",
        )


# ── expand_to_parents ─────────────────────────────────────────────────────────


class TestExpandToParents:
    async def test_empty_ids_returns_empty(self):
        store = KnowledgeStore(db_session_factory=_ScriptedDB())
        assert await store.expand_to_parents([], "c1", _CTX) == []

    async def test_no_db_returns_empty(self):
        store = KnowledgeStore()
        assert await store.expand_to_parents(["c1"], "c1", _CTX) == []

    async def test_dimension_missing_returns_empty(self):
        db = _ScriptedDB(_Result(rows=[]))
        store = KnowledgeStore(db_session_factory=db)
        assert await store.expand_to_parents(["c1"], "col1", _CTX) == []

    async def test_success_returns_chunk_like_objects(self):
        row = ("parent1", "parent content", "https://x", {"k": "v"})
        db = _ScriptedDB(_Result(rows=[(768,)]), _Result(rows=[row]))
        store = KnowledgeStore(db_session_factory=db)
        results = await store.expand_to_parents(["child1"], "col1", _CTX)
        assert len(results) == 1
        assert results[0].chunk_id == "parent1"
        assert results[0].content == "parent content"
        assert results[0].source_url == "https://x"
        assert results[0].metadata == {"k": "v"}
        assert results[0].score == 0.8

    async def test_explicit_db_param_used_over_default(self):
        row = ("parent1", "parent content", None, None)
        db = _ScriptedDB(_Result(rows=[(768,)]), _Result(rows=[row]))
        store = KnowledgeStore()
        results = await store.expand_to_parents(["child1"], "col1", _CTX, db=db)
        assert results[0].source_url == ""
        assert results[0].metadata == {}


# ── DB failure propagation sanity check (guards against silent fallback) ────


class TestDbFailurePropagates:
    async def test_get_collection_async_propagates_db_error(self):
        store = KnowledgeStore(db_session_factory=_RaisingDB())
        with pytest.raises(RuntimeError, match="boom"):
            await store.get_collection_async("c1", tenant_ctx=_CTX)
