"""Comprehensive tests for app/memory/long_term.py — targeting 90%+ coverage."""
from __future__ import annotations

import pytest

from app.memory.long_term import LongTermMemory, LongTermMemoryStore
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="ltm-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")
_CTX2 = TenantContext(tenant_id="ltm-t2", plan=PlanTier.FREE, api_key_id="k2")


class _FailingDB:
    def __call__(self):
        return self
    async def __aenter__(self):
        raise RuntimeError("DB down")
    async def __aexit__(self, *args):
        pass


class TestLongTermMemory:
    def test_auto_memory_id(self):
        m = LongTermMemory(
            content="test content",
            source_goal_id="g1",
            memory_type="success_pattern",
        )
        assert m.memory_id is not None
        assert len(m.memory_id) == 32  # uuid4 hex

    def test_unique_ids(self):
        m1 = LongTermMemory(content="a", source_goal_id="g1", memory_type="domain_fact")
        m2 = LongTermMemory(content="a", source_goal_id="g1", memory_type="domain_fact")
        assert m1.memory_id != m2.memory_id

    def test_created_at_auto_set(self):
        m = LongTermMemory(content="x", source_goal_id="g1", memory_type="tool_preference")
        assert "T" in m.created_at  # ISO format contains T

    def test_default_confidence(self):
        m = LongTermMemory(content="x", source_goal_id="g1", memory_type="domain_fact")
        assert m.confidence == 1.0

    def test_tags_default_empty(self):
        m = LongTermMemory(content="x", source_goal_id="g1", memory_type="success_pattern")
        assert m.tags == []


class TestLongTermMemoryStoreSync:
    def test_store_and_recall(self):
        """Covers lines 43-44: store(), and lines 55-64: recall()."""
        store = LongTermMemoryStore()
        mem = LongTermMemory(
            content="GitHub PR review takes 2-3 hours typically",
            source_goal_id="g1",
            memory_type="domain_fact",
        )
        mid = store.store(memory=mem, tenant_ctx=_CTX)
        assert mid == mem.memory_id

        results = store.recall(query="GitHub PR review", tenant_ctx=_CTX)
        assert len(results) == 1
        assert results[0].content == mem.content

    def test_recall_with_memory_type_filter(self):
        """Covers the memory_type filter branch in recall()."""
        store = LongTermMemoryStore()
        m1 = LongTermMemory(content="tool_pref: use jq for JSON", source_goal_id="g1", memory_type="tool_preference")
        m2 = LongTermMemory(content="success: deployed service", source_goal_id="g2", memory_type="success_pattern")
        store.store(memory=m1, tenant_ctx=_CTX)
        store.store(memory=m2, tenant_ctx=_CTX)

        results = store.recall(query="tool", tenant_ctx=_CTX, memory_type="tool_preference")
        assert all(r.memory_type == "tool_preference" for r in results)

    def test_recall_keyword_scoring(self):
        """Higher keyword overlap should rank first."""
        store = LongTermMemoryStore()
        m1 = LongTermMemory(content="deploy kubernetes cluster", source_goal_id="g1", memory_type="success_pattern")
        m2 = LongTermMemory(content="send email notification", source_goal_id="g2", memory_type="success_pattern")
        store.store(memory=m1, tenant_ctx=_CTX)
        store.store(memory=m2, tenant_ctx=_CTX)

        results = store.recall(query="deploy kubernetes", tenant_ctx=_CTX)
        assert results[0].content == "deploy kubernetes cluster"

    def test_recall_top_k(self):
        store = LongTermMemoryStore()
        for i in range(10):
            store.store(
                memory=LongTermMemory(content=f"fact {i} about golang", source_goal_id=f"g{i}", memory_type="domain_fact"),
                tenant_ctx=_CTX
            )
        results = store.recall(query="golang", tenant_ctx=_CTX, top_k=3)
        assert len(results) == 3

    def test_recall_tenant_isolation(self):
        store = LongTermMemoryStore()
        m = LongTermMemory(content="private info", source_goal_id="g1", memory_type="domain_fact")
        store.store(memory=m, tenant_ctx=_CTX)
        results = store.recall(query="private", tenant_ctx=_CTX2)
        assert results == []

    def test_delete_existing(self):
        """Covers lines 67-72: delete() found."""
        store = LongTermMemoryStore()
        m = LongTermMemory(content="to delete", source_goal_id="g1", memory_type="failure_pattern")
        store.store(memory=m, tenant_ctx=_CTX)
        result = store.delete(memory_id=m.memory_id, tenant_ctx=_CTX)
        assert result is True
        assert store.recall(query="to delete", tenant_ctx=_CTX) == []

    def test_delete_not_found(self):
        """Covers the false return path of delete()."""
        store = LongTermMemoryStore()
        result = store.delete(memory_id="nonexistent", tenant_ctx=_CTX)
        assert result is False

    def test_delete_wrong_tenant(self):
        store = LongTermMemoryStore()
        m = LongTermMemory(content="content", source_goal_id="g1", memory_type="domain_fact")
        store.store(memory=m, tenant_ctx=_CTX)
        result = store.delete(memory_id=m.memory_id, tenant_ctx=_CTX2)
        assert result is False

    def test_list_all(self):
        """Covers line 75: list_all()."""
        store = LongTermMemoryStore()
        m1 = LongTermMemory(content="a", source_goal_id="g1", memory_type="domain_fact")
        m2 = LongTermMemory(content="b", source_goal_id="g2", memory_type="domain_fact")
        store.store(memory=m1, tenant_ctx=_CTX)
        store.store(memory=m2, tenant_ctx=_CTX)

        all_memories = store.list_all(tenant_ctx=_CTX)
        assert len(all_memories) == 2

    def test_list_all_empty(self):
        store = LongTermMemoryStore()
        assert store.list_all(tenant_ctx=_CTX) == []

    def test_extract_from_goal(self):
        """Covers lines 86-94: extract_from_goal()."""
        store = LongTermMemoryStore()
        memory_ids = store.extract_from_goal(
            goal="Fix production memory leak in Go service",
            result="Found and patched goroutine leak in HTTP handler",
            goal_id="g-fix-1",
            tenant_ctx=_CTX,
        )
        assert len(memory_ids) == 1
        all_memories = store.list_all(tenant_ctx=_CTX)
        assert len(all_memories) == 1
        assert "Fix production memory leak" in all_memories[0].content
        assert all_memories[0].memory_type == "success_pattern"
        assert all_memories[0].confidence == pytest.approx(0.8)
        assert "auto-extracted" in all_memories[0].tags


class TestLongTermMemoryStoreAsync:
    """Tests for store_async and recall_async (lines 124-284)."""

    @pytest.mark.asyncio
    async def test_store_async_without_db(self):
        """store_async without DB only updates in-memory store."""
        store = LongTermMemoryStore()
        m = LongTermMemory(content="test async store", source_goal_id="g1", memory_type="domain_fact")
        mid = await store.store_async(memory=m, tenant_ctx=_CTX, db=None, embedder=None)
        assert mid == m.memory_id
        memories = store.list_all(tenant_ctx=_CTX)
        assert len(memories) == 1

    @pytest.mark.asyncio
    async def test_store_async_with_failing_db_does_not_raise(self):
        """DB failure in store_async must be silently caught."""
        store = LongTermMemoryStore()
        m = LongTermMemory(content="safe content", source_goal_id="g1", memory_type="success_pattern")
        mid = await store.store_async(memory=m, tenant_ctx=_CTX, db=_FailingDB())
        assert mid == m.memory_id

    @pytest.mark.asyncio
    async def test_store_async_with_embedder_no_db(self):
        """store_async with embedder but no DB should still work."""
        from app.providers.fake import FakeProvider
        store = LongTermMemoryStore()
        embedder = FakeProvider()
        m = LongTermMemory(content="embedding test content", source_goal_id="g1", memory_type="domain_fact")
        mid = await store.store_async(memory=m, tenant_ctx=_CTX, db=None, embedder=embedder)
        assert mid == m.memory_id

    @pytest.mark.asyncio
    async def test_recall_async_keyword_fallback_no_db(self):
        """recall_async without DB falls back to keyword matching."""
        store = LongTermMemoryStore()
        m = LongTermMemory(
            content="kubernetes deployment best practices",
            source_goal_id="g1",
            memory_type="domain_fact",
        )
        store.store(memory=m, tenant_ctx=_CTX)

        results = await store.recall_async("kubernetes deployment", _CTX, db=None)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_recall_async_no_db_no_embedder_keyword_fallback(self):
        """recall_async with no db AND no embedder → keyword fallback."""
        store = LongTermMemoryStore()
        m = LongTermMemory(content="python performance tips", source_goal_id="g1", memory_type="domain_fact")
        store.store(memory=m, tenant_ctx=_CTX)

        results = await store.recall_async("python performance", _CTX, top_k=5)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_recall_async_with_failing_db_falls_back(self):
        """DB failure in recall_async should fall back to keyword search."""
        store = LongTermMemoryStore()
        m = LongTermMemory(content="fallback content about redis", source_goal_id="g1", memory_type="domain_fact")
        store.store(memory=m, tenant_ctx=_CTX)

        results = await store.recall_async("redis", _CTX, db=_FailingDB())
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_extract_from_goal_async(self):
        """extract_from_goal_async updates in-memory and calls store_async."""
        store = LongTermMemoryStore()
        memory = await store.extract_from_goal_async(
            goal="Migrate database schema",
            result="Migration completed successfully in 2 minutes",
            tenant_ctx=_CTX,
            db=None,
            embedder=None,
        )
        assert "Migrate database schema" in memory.content
        assert memory.memory_type == "success_pattern"

        # Should be in in-memory store
        all_memories = store.list_all(tenant_ctx=_CTX)
        assert len(all_memories) >= 1

    @pytest.mark.asyncio
    async def test_store_async_embedder_failure_is_nonfatal(self):
        """Embedding failure in store_async must not prevent memory storage."""
        class _BadEmbedder:
            async def embed(self, req):
                raise RuntimeError("Embedding service down")

        store = LongTermMemoryStore()
        m = LongTermMemory(
            content="content when embedder fails",
            source_goal_id="g1",
            memory_type="domain_fact"
        )
        # Should not raise even if embedder fails
        mid = await store.store_async(memory=m, tenant_ctx=_CTX, db=None, embedder=_BadEmbedder())
        assert mid == m.memory_id
