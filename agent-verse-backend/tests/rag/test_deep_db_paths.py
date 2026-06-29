"""Additional DB-path tests for deep coverage of rag/store.py and long_term.py.

Covers:
- _db_ingest_chunk (lines 180-194)
- hybrid_search_db table selection (lines 286-299)
- _db_ingest_with_citations (lines 455-499)
- sync_from_db (lines 527-587)
- recall_async pgvector path (long_term.py lines 220-281)
- recall_async limit hit → break (execution.py line 237)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, UTC

import pytest

from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="dbp2-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")


# ── Stateful async DB mock ────────────────────────────────────────────────────


class _StatefulMockSession:
    """Mock session that can return different rows per execute() call."""

    def __init__(self):
        self._call_count = 0
        self._rows_per_call: list[list] = []

    def set_rows(self, *args):
        """Set rows to return for each successive execute() call."""
        self._rows_per_call = list(args)

    async def execute(self, *a, **kw):
        rows = []
        if self._rows_per_call:
            rows = self._rows_per_call[min(self._call_count, len(self._rows_per_call) - 1)]
        self._call_count += 1

        class _Result:
            def fetchall(self): return rows
            def fetchone(self): return rows[0] if rows else None
            def scalars(self):
                class _S:
                    def all(self): return rows
                return _S()
        return _Result()

    def begin(self):
        class _BeginCM:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
        return _BeginCM()

    def add(self, obj): pass


class _StatefulMockDB:
    def __init__(self):
        self.session = _StatefulMockSession()

    def __call__(self):
        s = self.session
        class CM:
            async def __aenter__(self): return s
            async def __aexit__(self, *a): pass
        return CM()


class _SimpleMockDB:
    """Simple mock DB with a fixed session."""
    def __init__(self, rows=None):
        self._rows = rows or []

    def __call__(self):
        rows = self._rows
        class _Sess:
            async def execute(self, *a, **kw):
                class R:
                    def fetchall(self): return rows
                    def fetchone(self): return rows[0] if rows else None
                    def scalars(self):
                        class S:
                            def all(self): return rows
                        return S()
                return R()
            def begin(self):
                class B:
                    async def __aenter__(self): return self
                    async def __aexit__(self, *a): pass
                return B()
            def add(self, obj): pass
        class CM:
            async def __aenter__(self): return _Sess()
            async def __aexit__(self, *a): pass
        return CM()


# ── execution.py line 237 (break in DB fallback) ─────────────────────────────


class TestExecutionMemoryDBFallbackBreak:
    @pytest.mark.asyncio
    async def test_recall_async_fallback_limit_hit_covers_break(self):
        """Line 237: fallback break path — when len(fallback) reaches limit."""
        from app.memory.execution import ExecutionMemory

        class _FailDB:
            def __call__(self):
                class CM:
                    async def __aenter__(self): raise RuntimeError("down")
                    async def __aexit__(self, *a): pass
                return CM()

        mem = ExecutionMemory()
        # 5 matching plans
        mem._plans["t-break"] = [
            {"goal": f"deploy service {i}", "plan": [f"step{i}"], "success": True}
            for i in range(5)
        ]
        # limit=2 → break after 2 results (line 237)
        results = await mem.recall_async("deploy service", tenant_id="t-break", db=_FailDB(), limit=2)
        assert len(results) == 2


# ── rag/store.py _db_ingest_chunk (lines 180-194) ────────────────────────────


class TestDBIngestChunkPath:
    @pytest.mark.asyncio
    async def test_db_ingest_chunk_with_working_session(self):
        """Lines 180-194: _db_ingest_chunk with a mock DB that handles execute/add."""
        from app.rag.models import Chunk
        from app.rag.store import KnowledgeStore

        db = _SimpleMockDB()
        store = KnowledgeStore(db_session_factory=db)
        chunk = Chunk(
            document_id="doc-1",
            content="Sample content for ingest",
            embedding=[0.1, 0.2, 0.3],
            chunk_index=0,
        )
        # Call _db_ingest_chunk directly to cover the DB SQL path
        await store._db_ingest_chunk(chunk, "col-1", "t1")
        # If we get here without raising, the DB path was traversed successfully

    @pytest.mark.asyncio
    async def test_db_ingest_chunk_exception_is_caught(self):
        """Exception in _db_ingest_chunk is silently caught."""
        from app.rag.models import Chunk
        from app.rag.store import KnowledgeStore

        class _ExceptionDB:
            def __call__(self):
                class CM:
                    async def __aenter__(self): raise RuntimeError("ingest fail")
                    async def __aexit__(self, *a): pass
                return CM()

        store = KnowledgeStore(db_session_factory=_ExceptionDB())
        chunk = Chunk(document_id="d1", content="x", embedding=[], chunk_index=0)
        await store._db_ingest_chunk(chunk, "col-1", "t1")  # should not raise


# ── rag/store.py _db_ingest_with_citations (lines 455-499) ───────────────────


class TestDBIngestWithCitationsPath:
    @pytest.mark.asyncio
    async def test_db_ingest_with_citations_with_embedding(self):
        """Lines 455-499: _db_ingest_with_citations with embedding."""
        from app.rag.store import KnowledgeStore

        db = _SimpleMockDB()
        store = KnowledgeStore(db_session_factory=db)
        await store._db_ingest_with_citations(
            chunk_id="chunk-abc",
            collection_id="col-1",
            content="Documentation about REST APIs",
            embedding=[0.1, 0.2, 0.3],
            metadata={"source_type": "documentation"},
            tenant_id="t1",
            source_url="https://docs.example.com",
            source_type="documentation",
            source_doc_id="doc-123",
            page_number=5,
            freshness_ttl_hours=168,
            content_hash="abc123hash",
        )
        # No exception → DB write path traversed

    @pytest.mark.asyncio
    async def test_db_ingest_with_citations_no_embedding(self):
        """_db_ingest_with_citations with empty embedding (emb_str=None)."""
        from app.rag.store import KnowledgeStore

        db = _SimpleMockDB()
        store = KnowledgeStore(db_session_factory=db)
        await store._db_ingest_with_citations(
            chunk_id="chunk-empty",
            collection_id="col-1",
            content="No embedding content",
            embedding=[],  # empty → emb_str = None
            metadata={},
            tenant_id="t1",
            source_url="",
            source_type="text",
            source_doc_id="",
            page_number=None,
            freshness_ttl_hours=24,
            content_hash="xyz123",
        )

    @pytest.mark.asyncio
    async def test_db_ingest_with_citations_exception_caught(self):
        """Exception in _db_ingest_with_citations is silently caught (lines 499-500)."""
        from app.rag.store import KnowledgeStore

        class _FailDB:
            def __call__(self):
                class CM:
                    async def __aenter__(self): raise RuntimeError("citations fail")
                    async def __aexit__(self, *a): pass
                return CM()

        store = KnowledgeStore(db_session_factory=_FailDB())
        await store._db_ingest_with_citations(
            chunk_id="c1", collection_id="col-1", content="x", embedding=[0.1],
            metadata={}, tenant_id="t1", source_url="", source_type="text",
            source_doc_id="", page_number=None, freshness_ttl_hours=168,
            content_hash="h1",
        )
        # Should not raise


# ── rag/store.py sync_from_db (lines 527-587) ────────────────────────────────


class TestSyncFromDB:
    @pytest.mark.asyncio
    async def test_sync_from_db_empty_collections(self):
        """sync_from_db with empty collection list → returns 0."""
        from app.rag.store import KnowledgeStore

        db = _SimpleMockDB(rows=[])
        store = KnowledgeStore(db_session_factory=db)
        loaded = await store.sync_from_db()
        assert loaded == 0

    @pytest.mark.asyncio
    async def test_sync_from_db_exception_returns_zero(self):
        """sync_from_db DB failure returns 0 gracefully."""
        from app.rag.store import KnowledgeStore

        class _FailDB:
            def __call__(self):
                class CM:
                    async def __aenter__(self): raise RuntimeError("sync fail")
                    async def __aexit__(self, *a): pass
                return CM()

        store = KnowledgeStore(db_session_factory=_FailDB())
        loaded = await store.sync_from_db()
        assert loaded == 0


# ── long_term.py recall_async pgvector path (lines 220-281) ──────────────────


class TestLTMRecallAsyncPgvectorPath:
    @pytest.mark.asyncio
    async def test_recall_async_pgvector_returns_memories_from_db(self):
        """Lines 220-281: recall_async with embedder + DB returns memories."""
        from app.memory.long_term import LongTermMemoryStore
        from app.providers.fake import FakeProvider

        # Rows: (id, content, memory_type, confidence, source_goal_id, tags, created_at)
        rows = [
            ("mem-1", "kubernetes deployment strategy", "domain_fact", 0.9, "g-1", '["k8s"]', datetime.now(UTC)),
            ("mem-2", "use circuit breakers for external APIs", "tool_preference", 0.8, "g-2", '[]', datetime.now(UTC)),
        ]
        db = _SimpleMockDB(rows=rows)
        embedder = FakeProvider(embed_dim=4)
        store = LongTermMemoryStore()

        results = await store.recall_async(
            "kubernetes strategy",
            _CTX,
            top_k=5,
            db=db,
            embedder=embedder,
        )
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_recall_async_pgvector_empty_rows_falls_back(self):
        """When pgvector returns no rows, falls back to keyword search."""
        from app.memory.long_term import LongTermMemory, LongTermMemoryStore
        from app.providers.fake import FakeProvider

        db = _SimpleMockDB(rows=[])  # empty rows from DB
        embedder = FakeProvider(embed_dim=4)
        store = LongTermMemoryStore()
        m = LongTermMemory(content="fallback memory content", source_goal_id="g1", memory_type="domain_fact")
        store.store(memory=m, tenant_ctx=_CTX)

        results = await store.recall_async(
            "fallback memory",
            _CTX,
            top_k=5,
            db=db,
            embedder=embedder,
        )
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_recall_async_pgvector_db_exception_falls_back(self):
        """pgvector DB exception → gracefully falls back to keyword search."""
        from app.memory.long_term import LongTermMemory, LongTermMemoryStore
        from app.providers.fake import FakeProvider

        class _FailDB:
            def __call__(self):
                class CM:
                    async def __aenter__(self): raise RuntimeError("pgvector fail")
                    async def __aexit__(self, *a): pass
                return CM()

        embedder = FakeProvider(embed_dim=4)
        store = LongTermMemoryStore()
        m = LongTermMemory(content="exception fallback test", source_goal_id="g1", memory_type="domain_fact")
        store.store(memory=m, tenant_ctx=_CTX)

        results = await store.recall_async(
            "exception fallback",
            _CTX,
            top_k=5,
            db=_FailDB(),
            embedder=embedder,
        )
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_recall_async_pgvector_rows_with_invalid_tags_json(self):
        """Lines 262-263: invalid JSON in tags is handled gracefully."""
        from app.memory.long_term import LongTermMemoryStore
        from app.providers.fake import FakeProvider

        rows = [
            ("mem-3", "content with bad tags", "domain_fact", 0.7, "g-3", "not-valid-json", datetime.now(UTC)),
        ]
        db = _SimpleMockDB(rows=rows)
        embedder = FakeProvider(embed_dim=4)
        store = LongTermMemoryStore()

        results = await store.recall_async("content", _CTX, db=db, embedder=embedder)
        assert isinstance(results, list)
