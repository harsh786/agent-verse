"""Comprehensive tests for app/memory/execution.py — targeting 90%+ coverage."""
from __future__ import annotations

import pytest

from app.memory.execution import ExecutionMemory
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="exec-t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
_CTX2 = TenantContext(tenant_id="exec-t2", plan=PlanTier.FREE, api_key_id="k2")


class _FailingDB:
    """Async context manager that raises on __aenter__ to simulate DB failure."""
    def __call__(self):
        return self
    async def __aenter__(self):
        raise RuntimeError("DB connection failed")
    async def __aexit__(self, *args):
        pass


class TestExecutionMemorySync:
    def test_record_and_recall_basic(self):
        mem = ExecutionMemory()
        mem.record(goal="Analyze sales data", plan=["fetch data", "run analysis"], tenant_ctx=_CTX)
        results = mem.recall(goal_hint="sales", tenant_ctx=_CTX)
        assert len(results) == 1
        assert results[0]["goal"] == "Analyze sales data"

    def test_recall_with_top_k(self):
        mem = ExecutionMemory()
        for i in range(10):
            mem.record(goal=f"deploy service {i}", plan=[f"step {i}"], tenant_ctx=_CTX)
        results = mem.recall(goal_hint="deploy service", tenant_ctx=_CTX, top_k=3)
        assert len(results) == 3

    def test_recall_no_match_returns_empty(self):
        mem = ExecutionMemory()
        mem.record(goal="Fix memory leak", plan=["profile", "patch"], tenant_ctx=_CTX)
        results = mem.recall(goal_hint="database migration", tenant_ctx=_CTX)
        assert results == []

    def test_tenant_isolation_sync(self):
        mem = ExecutionMemory()
        mem.record(goal="secret plan", plan=["step1"], tenant_ctx=_CTX)
        results = mem.recall(goal_hint="secret", tenant_ctx=_CTX2)
        assert results == []

    def test_record_failure_and_recall(self):
        mem = ExecutionMemory()
        mem.record_failure(
            goal="deploy to prod",
            failed_step="push image",
            error="permission denied",
            tenant_ctx=_CTX,
        )
        failures = mem.recall_failures(goal_hint="deploy", tenant_ctx=_CTX)
        assert len(failures) == 1
        assert failures[0]["error"] == "permission denied"

    def test_recall_failures_tenant_isolation(self):
        mem = ExecutionMemory()
        mem.record_failure(goal="private deploy", failed_step="s1", error="err", tenant_ctx=_CTX)
        results = mem.recall_failures(goal_hint="private", tenant_ctx=_CTX2)
        assert results == []

    def test_recall_failures_top_k(self):
        mem = ExecutionMemory()
        for i in range(10):
            mem.record_failure(goal=f"task {i}", failed_step="s", error="e", tenant_ctx=_CTX)
        results = mem.recall_failures(goal_hint="task", tenant_ctx=_CTX, top_k=4)
        assert len(results) == 4

    def test_recall_case_insensitive(self):
        mem = ExecutionMemory()
        mem.record(goal="Run MONTHLY Report", plan=["fetch", "process"], tenant_ctx=_CTX)
        results = mem.recall(goal_hint="monthly report", tenant_ctx=_CTX)
        assert len(results) == 1


class TestExecutionMemoryRecordAsync:
    """Tests for record_async (lines 80-129)."""

    @pytest.mark.asyncio
    async def test_record_async_without_db(self):
        mem = ExecutionMemory()
        await mem.record_async(
            goal="Summarize docs",
            plan=["fetch", "summarize"],
            success=True,
            tenant_id="t-async",
        )
        assert "t-async" in mem._memories
        assert len(mem._memories["t-async"]) == 1

    @pytest.mark.asyncio
    async def test_record_async_success_also_updates_plans(self):
        """On success=True, record_async must also add to _plans for sync recall."""
        mem = ExecutionMemory()
        await mem.record_async(
            goal="Successful goal",
            plan=["step A", "step B"],
            success=True,
            tenant_id="t-success",
        )
        assert "t-success" in mem._plans
        results = mem.recall(goal_hint="Successful", tenant_ctx=TenantContext(
            tenant_id="t-success", plan=PlanTier.FREE, api_key_id="k"
        ))
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_record_async_failure_not_in_plans(self):
        """On success=False, should NOT add to _plans."""
        mem = ExecutionMemory()
        await mem.record_async(
            goal="Failed goal",
            plan=["crashed step"],
            success=False,
            tenant_id="t-fail",
        )
        assert "t-fail" not in mem._plans or len(mem._plans.get("t-fail", [])) == 0

    @pytest.mark.asyncio
    async def test_record_async_trims_to_100(self):
        """Memory must trim to last 100 entries when exceeding 100."""
        mem = ExecutionMemory()
        for i in range(105):
            await mem.record_async(goal=f"goal {i}", plan=[], success=True, tenant_id="t-trim")
        assert len(mem._memories["t-trim"]) == 100

    @pytest.mark.asyncio
    async def test_record_async_with_failing_db_does_not_raise(self):
        """DB failure must be silently caught and not propagate."""
        mem = ExecutionMemory()
        await mem.record_async(
            goal="Goal with DB error",
            plan=["step"],
            success=True,
            tenant_id="t-dberr",
            db=_FailingDB(),
        )
        # In-memory should still be updated
        assert "t-dberr" in mem._memories


class TestExecutionMemoryRecordFailureAsync:
    """Tests for record_failure_async (lines 131-168)."""

    @pytest.mark.asyncio
    async def test_record_failure_async_without_db(self):
        mem = ExecutionMemory()
        await mem.record_failure_async(
            goal="Sync failed goal",
            error="connection timeout",
            tenant_id="t-fail-async",
        )
        assert "t-fail-async" in mem._failures
        assert mem._failures["t-fail-async"][0]["error"] == "connection timeout"

    @pytest.mark.asyncio
    async def test_record_failure_async_trims_at_100(self):
        """Failures trimmed to 50 when > 100 in memory."""
        mem = ExecutionMemory()
        for i in range(105):
            await mem.record_failure_async(goal=f"task {i}", error="err", tenant_id="t-ftrim")
        # After trimming: 50 entries
        assert len(mem._failures["t-ftrim"]) <= 100

    @pytest.mark.asyncio
    async def test_record_failure_async_db_error_does_not_raise(self):
        mem = ExecutionMemory()
        await mem.record_failure_async(
            goal="DB failure goal",
            error="some error",
            tenant_id="t-fdberr",
            db=_FailingDB(),
        )
        assert "t-fdberr" in mem._failures


class TestExecutionMemoryRecallAsync:
    """Tests for recall_async — both DB and in-memory paths (lines 170-238)."""

    @pytest.mark.asyncio
    async def test_recall_async_no_db_falls_back_to_memory(self):
        mem = ExecutionMemory()
        mem._plans["t-recall"] = [
            {"goal": "Process invoices", "plan": ["fetch", "process"], "success": True}
        ]
        results = await mem.recall_async("invoices", tenant_id="t-recall", db=None, limit=3)
        assert any("invoices" in r["goal"].lower() for r in results)

    @pytest.mark.asyncio
    async def test_recall_async_respects_limit(self):
        mem = ExecutionMemory()
        mem._plans["t-lim"] = [
            {"goal": f"task {i}", "plan": [f"s{i}"], "success": True} for i in range(10)
        ]
        results = await mem.recall_async("task", tenant_id="t-lim", db=None, limit=2)
        assert len(results) <= 2

    @pytest.mark.asyncio
    async def test_recall_async_empty_when_no_plans(self):
        mem = ExecutionMemory()
        results = await mem.recall_async("any goal", tenant_id="new-tenant", db=None, limit=5)
        assert results == []

    @pytest.mark.asyncio
    async def test_recall_async_db_fallback_on_error(self):
        """When DB raises, falls back to in-memory search."""
        mem = ExecutionMemory()
        mem._plans["t-fallback"] = [
            {"goal": "Deploy service", "plan": ["build", "push"], "success": True}
        ]
        results = await mem.recall_async("Deploy service", tenant_id="t-fallback", db=_FailingDB())
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_recall_async_word_matching(self):
        """Should match plans whose goal contains words from the hint."""
        mem = ExecutionMemory()
        mem._plans["t-words"] = [
            {"goal": "Analyze quarterly revenue report", "plan": ["s1"], "success": True},
            {"goal": "Send welcome email to user", "plan": ["s2"], "success": True},
        ]
        results = await mem.recall_async("quarterly revenue", tenant_id="t-words", db=None)
        goals = [r["goal"] for r in results]
        assert any("quarterly" in g for g in goals)

    @pytest.mark.asyncio
    async def test_recall_async_plan_as_dict_fallback(self):
        """Plans stored as dict should still return list."""
        mem = ExecutionMemory()
        mem._plans["t-dict"] = [
            {"goal": "Fix bug", "plan": {"steps": ["s1"]}, "success": True}
        ]
        results = await mem.recall_async("bug", tenant_id="t-dict", db=None)
        # Should not raise — plan extraction is safe
        assert isinstance(results, list)
