"""Comprehensive tests for app/pipeline/steps.py — targeting 90%+ coverage.

Focuses on smart_context_fetch and stream_step_event which are not in the
basic test_pipeline_steps.py file.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.pipeline.steps import (
    circuit_breaker_check,
    cost_check,
    dedup_check,
    exec_memory_lookup,
    governance_check,
    hitl_gate,
    record_rollback_point,
    record_usage,
    result_processor_step,
    smart_context_fetch,
    stream_step_event,
)
from app.tenancy.context import PlanTier, TenantContext


def _tenant(tid: str = "pipe-t1") -> TenantContext:
    return TenantContext(tenant_id=tid, plan=PlanTier.PROFESSIONAL, api_key_id="k1")


# ── smart_context_fetch ──────────────────────────────────────────────────────

class TestSmartContextFetch:
    @pytest.mark.asyncio
    async def test_no_knowledge_store_returns_empty(self):
        result = await smart_context_fetch(
            goal="Summarize docs",
            step="fetch documents",
            tenant_ctx=_tenant(),
            knowledge_store=None,
        )
        assert result == ""

    @pytest.mark.asyncio
    async def test_no_query_embedding_returns_empty(self):
        """When query_embedding is None, skip RAG to avoid corrupting results."""
        mock_store = MagicMock()
        result = await smart_context_fetch(
            goal="Fix bug",
            step="analyze code",
            tenant_ctx=_tenant(),
            knowledge_store=mock_store,
            query_embedding=None,
        )
        assert result == ""
        # Store should not be called
        mock_store.list_collections.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_collections(self):
        mock_store = MagicMock()
        mock_store.list_collections.return_value = []
        mock_store.hybrid_search_db = AsyncMock(return_value=[])

        result = await smart_context_fetch(
            goal="Search goal",
            step="search step",
            tenant_ctx=_tenant(),
            knowledge_store=mock_store,
            query_embedding=[0.1, 0.2, 0.3],
        )
        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_context_when_results_found(self):
        from app.rag.store import HybridSearchResult

        mock_result = HybridSearchResult(
            chunk_id="c1",
            content="Kubernetes deployment requires a namespace configuration",
            score=0.92,
            vector_score=0.95,
            trigram_score=0.85,
        )
        mock_store = MagicMock()
        mock_col = MagicMock()
        mock_col.collection_id = "col-1"
        mock_store.list_collections.return_value = [mock_col]
        mock_store.hybrid_search_db = AsyncMock(return_value=[mock_result])

        result = await smart_context_fetch(
            goal="Deploy Kubernetes",
            step="configure deployment",
            tenant_ctx=_tenant(),
            knowledge_store=mock_store,
            query_embedding=[0.1] * 8,
        )
        assert "Context" in result
        assert "Kubernetes" in result or "0.92" in result

    @pytest.mark.asyncio
    async def test_caps_at_three_collections(self):
        """smart_context_fetch should query at most 3 collections."""
        mock_store = MagicMock()
        collections = []
        for i in range(5):
            col = MagicMock()
            col.collection_id = f"col-{i}"
            collections.append(col)
        mock_store.list_collections.return_value = collections
        mock_store.hybrid_search_db = AsyncMock(return_value=[])

        await smart_context_fetch(
            goal="test",
            step="test step",
            tenant_ctx=_tenant(),
            knowledge_store=mock_store,
            query_embedding=[0.1] * 4,
        )
        # hybrid_search_db called at most 3 times (cap at 3 collections)
        assert mock_store.hybrid_search_db.call_count <= 3

    @pytest.mark.asyncio
    async def test_caps_context_at_top_three_results(self):
        """Only top 3 results should appear in context output."""
        from app.rag.store import HybridSearchResult

        results = [
            HybridSearchResult(chunk_id=f"c{i}", content=f"Content chunk {i}", score=1.0 - i * 0.1, vector_score=0.9, trigram_score=0.8)
            for i in range(10)
        ]
        mock_store = MagicMock()
        mock_col = MagicMock()
        mock_col.collection_id = "col-1"
        mock_store.list_collections.return_value = [mock_col]
        mock_store.hybrid_search_db = AsyncMock(return_value=results)

        result = await smart_context_fetch(
            goal="test",
            step="test step",
            tenant_ctx=_tenant(),
            knowledge_store=mock_store,
            query_embedding=[0.1] * 4,
        )
        # Should show Context 1, 2, 3 at most
        assert result.count("[Context") <= 3

    @pytest.mark.asyncio
    async def test_filters_by_agent_allowed_collections(self):
        """When agent has allowed_collection_ids, only those collections are searched."""
        from app.rag.store import HybridSearchResult

        mock_result = HybridSearchResult(
            chunk_id="c1", content="Allowed content", score=0.9, vector_score=0.9, trigram_score=0.8
        )
        mock_store = MagicMock()
        col_a = MagicMock()
        col_a.collection_id = "allowed-col"
        col_b = MagicMock()
        col_b.collection_id = "not-allowed-col"
        mock_store.list_collections.return_value = [col_a, col_b]
        mock_store.hybrid_search_db = AsyncMock(return_value=[mock_result])

        mock_agent = {"allowed_collection_ids": ["allowed-col"]}
        mock_agent_store = MagicMock()
        mock_agent_store.get.return_value = mock_agent

        result = await smart_context_fetch(
            goal="test",
            step="test step",
            tenant_ctx=_tenant(),
            knowledge_store=mock_store,
            query_embedding=[0.1] * 4,
            context={"agent_id": "agent-x"},
            agent_store=mock_agent_store,
        )
        # Should only search allowed-col
        calls = [call.kwargs.get("collection_id", call.args[2] if len(call.args) > 2 else None)
                 for call in mock_store.hybrid_search_db.call_args_list]
        # Verify not-allowed-col was not searched
        assert "not-allowed-col" not in str(mock_store.hybrid_search_db.call_args_list)

    @pytest.mark.asyncio
    async def test_handles_exception_in_search_gracefully(self):
        """If hybrid_search_db raises for a collection, it continues to next."""
        mock_store = MagicMock()
        col = MagicMock()
        col.collection_id = "err-col"
        mock_store.list_collections.return_value = [col]
        mock_store.hybrid_search_db = AsyncMock(side_effect=RuntimeError("Search failed"))

        result = await smart_context_fetch(
            goal="test",
            step="test step",
            tenant_ctx=_tenant(),
            knowledge_store=mock_store,
            query_embedding=[0.1] * 4,
        )
        assert result == ""

    @pytest.mark.asyncio
    async def test_handles_exception_in_list_collections(self):
        """If list_collections raises, smart_context_fetch returns ''."""
        mock_store = MagicMock()
        mock_store.list_collections.side_effect = RuntimeError("Store error")

        result = await smart_context_fetch(
            goal="test",
            step="test step",
            tenant_ctx=_tenant(),
            knowledge_store=mock_store,
            query_embedding=[0.1] * 4,
        )
        assert result == ""

    @pytest.mark.asyncio
    async def test_agent_with_no_allowed_collections_searches_all(self):
        """Agent with empty allowed_collection_ids should search all collections."""
        mock_agent = {"allowed_collection_ids": []}
        mock_agent_store = MagicMock()
        mock_agent_store.get.return_value = mock_agent

        mock_store = MagicMock()
        col = MagicMock()
        col.collection_id = "open-col"
        mock_store.list_collections.return_value = [col]
        mock_store.hybrid_search_db = AsyncMock(return_value=[])

        await smart_context_fetch(
            goal="test",
            step="test step",
            tenant_ctx=_tenant(),
            knowledge_store=mock_store,
            query_embedding=[0.1] * 4,
            context={"agent_id": "any-agent"},
            agent_store=mock_agent_store,
        )
        # Should still call hybrid_search_db for the collection
        mock_store.hybrid_search_db.assert_called()

    @pytest.mark.asyncio
    async def test_agent_not_found_searches_all_collections(self):
        """When agent_store.get() returns None, search all collections."""
        mock_agent_store = MagicMock()
        mock_agent_store.get.return_value = None

        mock_store = MagicMock()
        col = MagicMock()
        col.collection_id = "any-col"
        mock_store.list_collections.return_value = [col]
        mock_store.hybrid_search_db = AsyncMock(return_value=[])

        await smart_context_fetch(
            goal="test",
            step="test step",
            tenant_ctx=_tenant(),
            knowledge_store=mock_store,
            query_embedding=[0.1] * 4,
            context={"agent_id": "missing-agent"},
            agent_store=mock_agent_store,
        )
        mock_store.hybrid_search_db.assert_called()

    @pytest.mark.asyncio
    async def test_no_context_arg_still_works(self):
        """smart_context_fetch with context=None should work fine."""
        mock_store = MagicMock()
        mock_store.list_collections.return_value = []

        result = await smart_context_fetch(
            goal="test",
            step="step",
            tenant_ctx=_tenant(),
            knowledge_store=mock_store,
            query_embedding=[0.1],
            context=None,
        )
        assert result == ""


# ── stream_step_event ─────────────────────────────────────────────────────────

class TestStreamStepEvent:
    @pytest.mark.asyncio
    async def test_stream_step_event_is_noop(self):
        """stream_step_event is a no-op stub; should not raise."""
        result = await stream_step_event(
            event={"type": "step_complete", "step": "analyze"},
            tenant_ctx=_tenant(),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_stream_step_event_empty_event(self):
        result = await stream_step_event(event={}, tenant_ctx=_tenant())
        assert result is None


# ── exec_memory_lookup ────────────────────────────────────────────────────────

class TestExecMemoryLookupComprehensive:
    @pytest.mark.asyncio
    async def test_exec_memory_lookup_no_memory_returns_empty(self):
        result = await exec_memory_lookup(
            goal="Any goal", tenant_ctx=_tenant(), memory=None
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_exec_memory_lookup_with_memory(self):
        from app.memory.execution import ExecutionMemory
        mem = ExecutionMemory()
        mem.record(
            goal="Deploy to staging",
            plan=["build", "push", "deploy"],
            tenant_ctx=_tenant(),
        )
        result = await exec_memory_lookup(
            goal="Deploy to staging environment",
            tenant_ctx=_tenant(),
            memory=mem,
        )
        assert isinstance(result, list)


# ── record_usage ──────────────────────────────────────────────────────────────

class TestRecordUsageComprehensive:
    @pytest.mark.asyncio
    async def test_record_usage_no_audit_log_is_noop(self):
        await record_usage(
            tool_name="read_file",
            tokens_used=100,
            tenant_ctx=_tenant(),
            audit_log=None,
        )
        # Should not raise

    @pytest.mark.asyncio
    async def test_record_usage_with_audit_log(self):
        from app.governance.audit import AuditLog
        audit = AuditLog()
        await record_usage(
            tool_name="write_file",
            tokens_used=250,
            tenant_ctx=_tenant(),
            audit_log=audit,
            goal_id="g-usage",
        )
        events = audit.query(tenant_ctx=_tenant())
        assert len(events) >= 1


# ── circuit_breaker_check ─────────────────────────────────────────────────────

class TestCircuitBreakerCheckComprehensive:
    @pytest.mark.asyncio
    async def test_circuit_breaker_closed_returns_false(self):
        from app.reliability.circuit_breaker import CircuitBreaker
        breaker = CircuitBreaker()
        result = await circuit_breaker_check(
            tool_name="external_api", tenant_ctx=_tenant(), breaker=breaker
        )
        assert result is False  # closed = calls allowed = not blocked

    @pytest.mark.asyncio
    async def test_circuit_breaker_no_breaker_returns_false(self):
        result = await circuit_breaker_check(
            tool_name="tool", tenant_ctx=_tenant(), breaker=None
        )
        assert result is False
