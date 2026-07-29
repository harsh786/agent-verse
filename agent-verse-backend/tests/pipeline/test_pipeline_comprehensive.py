"""Comprehensive tests for app/pipeline/steps.py — targeting 90%+ coverage.

Focuses on smart_context_fetch and stream_step_event which are not in the
basic test_pipeline_steps.py file.
"""
from __future__ import annotations

import pytest

from app.pipeline.steps import (
    circuit_breaker_check,
    exec_memory_lookup,
    record_usage,
    smart_context_fetch,
    stream_step_event,
)
from app.rag.contracts import RAGCitation, RAGExecutionResult, RAGStrategy
from app.tenancy.context import PlanTier, TenantContext


def _tenant(tid: str = "pipe-t1") -> TenantContext:
    return TenantContext(tenant_id=tid, plan=PlanTier.PROFESSIONAL, api_key_id="k1")


# ── smart_context_fetch ──────────────────────────────────────────────────────

class RecordingGateway:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.error = error

    async def execute(self, tenant_ctx: TenantContext, **kwargs: object) -> RAGExecutionResult:
        assert tenant_ctx.tenant_id == "pipe-t1"
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        collection_id = str(kwargs["collection_id"])
        return RAGExecutionResult(
            requested_strategy_id="hybrid",
            resolved_strategy_id=RAGStrategy.HYBRID,
            citations=[
                RAGCitation(
                    citation_id=f"citation-{collection_id}",
                    chunk_id=f"chunk-{collection_id}",
                    content=f"Context from {collection_id}",
                    score=0.9,
                    source=collection_id,
                )
            ],
        )


class TestSmartContextFetch:
    @pytest.mark.asyncio
    async def test_no_collections_returns_empty(self) -> None:
        result = await smart_context_fetch(
            goal="Summarize docs",
            step="fetch documents",
            tenant_ctx=_tenant(),
            collection_ids=[],
        )
        assert result == ""

    @pytest.mark.asyncio
    async def test_bound_collections_require_gateway(self) -> None:
        with pytest.raises(RuntimeError, match="gateway"):
            await smart_context_fetch(
                step="fetch documents",
                tenant_ctx=_tenant(),
                collection_ids=["collection-1"],
            )

    @pytest.mark.asyncio
    async def test_gateway_receives_canonical_inputs(self) -> None:
        gateway = RecordingGateway()
        result = await smart_context_fetch(
            step="fetch documents",
            tenant_ctx=_tenant(),
            retrieval_gateway=gateway,
            collection_ids=["collection-1", "collection-2"],
            strategy=RAGStrategy.HYBRID,
            top_k=2,
            filters={"team": "platform"},
        )

        assert "Context from collection-1" in result
        assert len(gateway.calls) == 2
        assert gateway.calls[0]["strategy_id"] is RAGStrategy.HYBRID
        assert gateway.calls[0]["filters"] == {"team": "platform"}

    @pytest.mark.asyncio
    async def test_gateway_failure_propagates(self) -> None:
        with pytest.raises(RuntimeError, match="retrieval failed"):
            await smart_context_fetch(
                step="fetch documents",
                tenant_ctx=_tenant(),
                retrieval_gateway=RecordingGateway(
                    error=RuntimeError("retrieval failed")
                ),
                collection_ids=["collection-1"],
            )


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
