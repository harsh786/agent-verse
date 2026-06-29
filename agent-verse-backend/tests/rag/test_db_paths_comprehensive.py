"""DB-mock tests to cover remaining async DB paths in memory and RAG modules.

Uses lightweight async context manager mocks to exercise DB write/read paths
without requiring a real PostgreSQL connection.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="db-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")


# ── Async DB session mock helpers ─────────────────────────────────────────────


class _MockBeginCM:
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass


class _MockSession:
    """Fake SQLAlchemy AsyncSession: execute() returns rows, begin() is a no-op CM."""

    def __init__(self, rows=None):
        self._rows = rows or []

    async def execute(self, *args, **kwargs):
        rows = self._rows

        class _Result:
            def fetchall(_self): return rows
            def fetchone(_self): return rows[0] if rows else None
            def scalars(_self):
                class _Scalars:
                    def all(_s): return rows
                return _Scalars()
        return _Result()

    def begin(self):
        return _MockBeginCM()

    def add(self, obj):
        pass


class _MockDB:
    """Factory that yields a _MockSession as an async context manager."""

    def __init__(self, rows=None):
        self._session = _MockSession(rows)

    def __call__(self):
        session = self._session

        class _SessionCM:
            async def __aenter__(self):
                return session
            async def __aexit__(self, *args):
                pass
        return _SessionCM()


# ── execution.py DB paths ─────────────────────────────────────────────────────


class TestExecutionMemoryDBPaths:
    """Tests for lines 201-223 (recall_async DB success path)."""

    @pytest.mark.asyncio
    async def test_recall_async_db_success_with_matching_rows(self):
        """Lines 201-223: DB returns rows, filter by keyword → result list."""
        from app.memory.execution import ExecutionMemory

        import json
        db = _MockDB(rows=[
            ("Deploy kubernetes service", json.dumps(["build", "push"]), True),
            ("Send welcome email", json.dumps(["compose", "send"]), True),
        ])
        mem = ExecutionMemory()
        results = await mem.recall_async("kubernetes deploy", tenant_id="t1", db=db, limit=3)
        assert isinstance(results, list)
        # Should find the kubernetes row
        goals = [r["goal"] for r in results]
        assert any("kubernetes" in g.lower() for g in goals)

    @pytest.mark.asyncio
    async def test_recall_async_db_success_no_matching_rows(self):
        """DB returns rows but none match the hint → empty filtered result."""
        from app.memory.execution import ExecutionMemory

        import json
        db = _MockDB(rows=[
            ("Send email to bob", json.dumps(["compose"]), True),
        ])
        mem = ExecutionMemory()
        results = await mem.recall_async("kubernetes deploy", tenant_id="t1", db=db, limit=3)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_recall_async_db_success_respects_limit(self):
        """Lines 221-222: limit is respected in DB path."""
        from app.memory.execution import ExecutionMemory

        import json
        db = _MockDB(rows=[
            (f"task number {i}", json.dumps([f"step{i}"]), True)
            for i in range(10)
        ])
        mem = ExecutionMemory()
        results = await mem.recall_async("task number", tenant_id="t1", db=db, limit=2)
        assert len(results) <= 2

    @pytest.mark.asyncio
    async def test_record_async_db_success_writes_to_db(self):
        """Lines 120: record_async successfully writes to DB session."""
        from app.memory.execution import ExecutionMemory

        db = _MockDB()
        mem = ExecutionMemory()
        await mem.record_async(
            goal="Write report",
            plan=["gather data", "analyze", "write"],
            success=True,
            tenant_id="t1",
            db=db,
        )
        # In-memory should be updated
        assert "t1" in mem._memories

    @pytest.mark.asyncio
    async def test_record_failure_async_db_success(self):
        """Line 153: record_failure_async DB write path."""
        from app.memory.execution import ExecutionMemory

        db = _MockDB()
        mem = ExecutionMemory()
        await mem.record_failure_async(
            goal="Deploy failed",
            error="connection refused",
            tenant_id="t1",
            db=db,
        )
        assert "t1" in mem._failures


# ── long_term.py DB paths ─────────────────────────────────────────────────────


class TestLongTermMemoryDBPaths:
    """Tests for store_async DB write paths (lines 148-181)."""

    @pytest.mark.asyncio
    async def test_store_async_with_db_no_embedder_uses_no_embedding_branch(self):
        """Lines 158-181: DB write without embedding (else branch)."""
        from app.memory.long_term import LongTermMemory, LongTermMemoryStore

        store = LongTermMemoryStore()
        m = LongTermMemory(
            content="kubernetes best practices for HA deployments",
            source_goal_id="g1",
            memory_type="domain_fact",
        )
        db = _MockDB()
        mid = await store.store_async(memory=m, tenant_ctx=_CTX, db=db, embedder=None)
        assert mid == m.memory_id

    @pytest.mark.asyncio
    async def test_store_async_with_db_and_embedder_uses_embedding_branch(self):
        """Lines 148-155: DB write WITH embedding (if branch)."""
        from app.memory.long_term import LongTermMemory, LongTermMemoryStore
        from app.providers.fake import FakeProvider

        store = LongTermMemoryStore()
        m = LongTermMemory(
            content="Use circuit breakers for external API calls",
            source_goal_id="g2",
            memory_type="tool_preference",
        )
        db = _MockDB()
        embedder = FakeProvider(embed_dim=8)
        mid = await store.store_async(memory=m, tenant_ctx=_CTX, db=db, embedder=embedder)
        assert mid == m.memory_id

    @pytest.mark.asyncio
    async def test_store_async_db_error_gracefully_logged(self):
        """Lines 199-201: DB exception is caught and logged."""
        class _BadDB:
            def __call__(self):
                class CM:
                    async def __aenter__(self): raise RuntimeError("SQL error")
                    async def __aexit__(self, *a): pass
                return CM()

        from app.memory.long_term import LongTermMemory, LongTermMemoryStore

        store = LongTermMemoryStore()
        m = LongTermMemory(content="test content", source_goal_id="g1", memory_type="domain_fact")
        # Should not raise; exception is caught inside store_async
        mid = await store.store_async(memory=m, tenant_ctx=_CTX, db=_BadDB())
        assert mid == m.memory_id


# ── tool_reliability.py DB paths ─────────────────────────────────────────────


class TestToolReliabilityDBPaths:
    """Tests for DB-backed record/get paths (lines 50, 74-81, 113-123)."""

    @pytest.mark.asyncio
    async def test_record_with_working_db_covers_line_50(self):
        """Line 50: DB write inside record()."""
        from app.memory.tool_reliability import ToolReliabilityStore

        db = _MockDB()
        store = ToolReliabilityStore(db_session_factory=db)
        await store.record(tenant_id="t1", tool_name="github_api", success=True, latency_ms=50.0)
        assert store._cache["t1:github_api"]["success_count"] == 1

    @pytest.mark.asyncio
    async def test_record_failure_with_working_db(self):
        """DB write for failure case."""
        from app.memory.tool_reliability import ToolReliabilityStore

        db = _MockDB()
        store = ToolReliabilityStore(db_session_factory=db)
        await store.record(tenant_id="t1", tool_name="slack_api", success=False, latency_ms=2000.0)
        assert store._cache["t1:slack_api"]["failure_count"] == 1

    @pytest.mark.asyncio
    async def test_get_reliability_db_row_found(self):
        """Lines 74-81: DB returns a row → parse it."""
        from app.memory.tool_reliability import ToolReliabilityStore

        row = (8, 2, 500.0, datetime.now(UTC))  # success, failure, latency, last_used_at
        db = _MockDB(rows=[row])
        store = ToolReliabilityStore(db_session_factory=db)
        result = await store.get_reliability(tenant_id="t1", tool_name="api_tool")
        assert result["success_count"] == 8
        assert result["failure_count"] == 2
        assert result["success_rate"] == pytest.approx(0.8)
        assert result["avg_latency_ms"] == pytest.approx(50.0)

    @pytest.mark.asyncio
    async def test_get_reliability_db_no_row_falls_to_cache(self):
        """DB returns no row → falls back to in-memory cache."""
        from app.memory.tool_reliability import ToolReliabilityStore

        db = _MockDB(rows=[])  # no rows
        store = ToolReliabilityStore(db_session_factory=db)
        # Pre-populate cache
        store._cache["t1:my_tool"] = {
            "tool_name": "my_tool",
            "success_count": 3,
            "failure_count": 1,
            "total_latency_ms": 400.0,
        }
        result = await store.get_reliability(tenant_id="t1", tool_name="my_tool")
        assert result["success_count"] == 3

    @pytest.mark.asyncio
    async def test_get_unreliable_tools_db_returns_rows(self):
        """Lines 113-123: get_unreliable_tools with DB returning results."""
        from app.memory.tool_reliability import ToolReliabilityStore

        rows = [
            ("flaky_webhook", 3, 7, 0.3),  # 30% success rate
            ("slow_api", 4, 6, 0.4),        # 40% success rate
        ]
        db = _MockDB(rows=rows)
        store = ToolReliabilityStore(db_session_factory=db)
        result = await store.get_unreliable_tools(tenant_id="t1", min_calls=5, max_success_rate=0.7)
        assert len(result) == 2
        tool_names = [r["tool_name"] for r in result]
        assert "flaky_webhook" in tool_names

    @pytest.mark.asyncio
    async def test_get_unreliable_tools_db_empty_results(self):
        """DB returns no unreliable tools."""
        from app.memory.tool_reliability import ToolReliabilityStore

        db = _MockDB(rows=[])
        store = ToolReliabilityStore(db_session_factory=db)
        result = await store.get_unreliable_tools(tenant_id="t1")
        assert result == []


# ── rag/store.py sync context → RuntimeError path ─────────────────────────────


class TestKnowledgeStoreAsyncFireAndForget:
    """Tests for fire-and-forget DB tasks in KnowledgeStore."""

    def test_create_collection_in_sync_context_with_db_hits_runtimeerror(self):
        """Lines 104-105: sync context → get_running_loop() raises RuntimeError → except pass."""
        from app.rag.models import KnowledgeCollection
        from app.rag.store import KnowledgeStore

        # In a sync test function there is NO running event loop, so
        # asyncio.get_running_loop() will raise RuntimeError → lines 104-105 are hit
        db = _MockDB()
        store = KnowledgeStore(db_session_factory=db)
        col_id = store.create_collection(
            KnowledgeCollection(name="sync-test-col"), tenant_ctx=_CTX
        )
        assert col_id is not None

    def test_ingest_chunk_in_sync_context_with_db_hits_runtimeerror(self):
        """Lines 165-166: sync context → ingest_chunk with DB hits RuntimeError."""
        from app.rag.models import Chunk, KnowledgeCollection
        from app.rag.store import KnowledgeStore

        db = _MockDB()
        store = KnowledgeStore(db_session_factory=db)
        col_id = store.create_collection(
            KnowledgeCollection(name="sync-ingest-col"), tenant_ctx=_CTX
        )
        chunk = Chunk(document_id="d1", content="sync content", embedding=[0.1, 0.2], chunk_index=0)
        # In sync context: get_running_loop() raises RuntimeError → lines 165-166 covered
        store.ingest_chunk(chunk, collection_id=col_id, tenant_ctx=_CTX)
        col = store.get_collection(col_id, tenant_ctx=_CTX)
        assert col.document_count == 1

    @pytest.mark.asyncio
    async def test_db_create_collection_none_db_returns_early(self):
        """Line 112: _db_create_collection with None db returns early."""
        from app.rag.models import KnowledgeCollection
        from app.rag.store import KnowledgeStore

        store = KnowledgeStore(db_session_factory=None)
        col = KnowledgeCollection(name="test")
        # Call directly — should return without doing anything
        await store._db_create_collection(col, "t1")
        # No exception; _db is None → early return

    @pytest.mark.asyncio
    async def test_db_ingest_chunk_none_db_returns_early(self):
        """Line 172: _db_ingest_chunk with None db returns early."""
        from app.rag.models import Chunk
        from app.rag.store import KnowledgeStore

        store = KnowledgeStore(db_session_factory=None)
        chunk = Chunk(document_id="d1", content="test", embedding=[0.1], chunk_index=0)
        await store._db_ingest_chunk(chunk, "col-1", "t1")
        # No exception; _db is None → early return

    @pytest.mark.asyncio
    async def test_db_ingest_citations_none_db_returns_early(self):
        """Line 453: _db_ingest_with_citations with None db returns early."""
        from app.rag.store import KnowledgeStore

        store = KnowledgeStore(db_session_factory=None)
        await store._db_ingest_with_citations(
            chunk_id="c1",
            collection_id="col-1",
            content="test content",
            embedding=[0.1, 0.2],
            metadata={},
            tenant_id="t1",
            source_url="",
            source_type="text",
            source_doc_id="",
            page_number=None,
            freshness_ttl_hours=168,
            content_hash="abc123",
        )
        # No exception; _db is None → early return

    @pytest.mark.asyncio
    async def test_ingest_document_with_db_in_async_context_creates_task(self):
        """Lines 407-426: In async context, ingest_document with DB creates a fire-and-forget task."""
        from app.rag.models import KnowledgeCollection
        from app.rag.store import KnowledgeStore

        db = _MockDB()
        store = KnowledgeStore(db_session_factory=db)
        col_id = store.create_collection(
            KnowledgeCollection(name="async-doc-col"), tenant_ctx=_CTX
        )
        # In async test context → get_running_loop() succeeds → create_task is called
        chunk_id = await store.ingest_document(
            collection_id=col_id,
            content="Async ingest test content here.",
            tenant_ctx=_CTX,
        )
        assert chunk_id is not None
        # Give the fire-and-forget task a chance to run (it will silently fail on mock)
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_hybrid_search_db_with_empty_embedding_falls_back(self):
        """Line 266: empty query_embedding → fallback to in-memory."""
        from app.rag.models import KnowledgeCollection
        from app.rag.store import KnowledgeStore

        db = _MockDB()
        store = KnowledgeStore(db_session_factory=db)
        col_id = store.create_collection(
            KnowledgeCollection(name="empty-emb-col"), tenant_ctx=_CTX
        )
        # Empty embedding → should fall back without hitting DB
        results = await store.hybrid_search_db("test query", [], col_id, _CTX, top_k=3)
        assert isinstance(results, list)
