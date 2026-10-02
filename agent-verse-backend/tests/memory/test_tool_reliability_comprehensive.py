"""Comprehensive tests for app/memory/tool_reliability.py — targeting 90%+ coverage."""
from __future__ import annotations

import pytest

from app.memory.tool_reliability import ToolReliabilityStore


class _FailingDB:
    def __call__(self):
        return self
    async def __aenter__(self):
        raise RuntimeError("DB down")
    async def __aexit__(self, *args):
        pass


class TestToolReliabilityStoreInMemory:
    """Tests that exercise the in-memory cache path (no DB)."""

    @pytest.mark.asyncio
    async def test_record_success(self):
        store = ToolReliabilityStore()
        await store.record(tenant_id="t1", tool_name="github_api", success=True, latency_ms=120.0)
        key = "t1:github_api"
        assert store._cache[key]["success_count"] == 1
        assert store._cache[key]["failure_count"] == 0

    @pytest.mark.asyncio
    async def test_record_failure(self):
        store = ToolReliabilityStore()
        await store.record(tenant_id="t1", tool_name="jira_api", success=False, latency_ms=500.0)
        key = "t1:jira_api"
        assert store._cache[key]["failure_count"] == 1
        assert store._cache[key]["success_count"] == 0

    @pytest.mark.asyncio
    async def test_record_accumulates_latency(self):
        store = ToolReliabilityStore()
        await store.record(tenant_id="t1", tool_name="tool_x", success=True, latency_ms=100.0)
        await store.record(tenant_id="t1", tool_name="tool_x", success=True, latency_ms=200.0)
        key = "t1:tool_x"
        assert store._cache[key]["total_latency_ms"] == pytest.approx(300.0)

    @pytest.mark.asyncio
    async def test_get_reliability_in_memory_no_calls(self):
        """get_reliability for an unknown tool returns 100% success rate (no data)."""
        store = ToolReliabilityStore()
        result = await store.get_reliability(tenant_id="t1", tool_name="unknown_tool")
        assert result["success_rate"] == 1.0
        assert result["success_count"] == 0
        assert result["failure_count"] == 0

    @pytest.mark.asyncio
    async def test_get_reliability_after_records(self):
        store = ToolReliabilityStore()
        await store.record(tenant_id="t1", tool_name="slack", success=True, latency_ms=50.0)
        await store.record(tenant_id="t1", tool_name="slack", success=True, latency_ms=60.0)
        await store.record(tenant_id="t1", tool_name="slack", success=False, latency_ms=1000.0)

        result = await store.get_reliability(tenant_id="t1", tool_name="slack")
        assert result["success_count"] == 2
        assert result["failure_count"] == 1
        assert result["success_rate"] == pytest.approx(2 / 3)
        assert result["avg_latency_ms"] == pytest.approx((50.0 + 60.0 + 1000.0) / 3)

    @pytest.mark.asyncio
    async def test_get_reliability_tenant_isolation(self):
        """Different tenants should have separate counters."""
        store = ToolReliabilityStore()
        await store.record(tenant_id="t1", tool_name="tool_a", success=True)
        await store.record(tenant_id="t2", tool_name="tool_a", success=False)

        r1 = await store.get_reliability(tenant_id="t1", tool_name="tool_a")
        r2 = await store.get_reliability(tenant_id="t2", tool_name="tool_a")
        assert r1["success_count"] == 1
        assert r2["failure_count"] == 1

    @pytest.mark.asyncio
    async def test_get_reliability_zero_latency_default(self):
        store = ToolReliabilityStore()
        await store.record(tenant_id="t1", tool_name="fast_tool", success=True)
        result = await store.get_reliability(tenant_id="t1", tool_name="fast_tool")
        assert result["avg_latency_ms"] == pytest.approx(0.0)


class TestToolReliabilityStoreWithDBError:
    """Tests that exercise the DB fallback paths."""

    @pytest.mark.asyncio
    async def test_record_with_failing_db_still_updates_cache(self):
        """DB failure must not prevent in-memory update."""
        store = ToolReliabilityStore(db_session_factory=_FailingDB())
        await store.record(tenant_id="t1", tool_name="api_tool", success=True, latency_ms=75.0)
        key = "t1:api_tool"
        assert store._cache[key]["success_count"] == 1

    @pytest.mark.asyncio
    async def test_get_reliability_with_failing_db_raises(self):
        """MEM-01: a DB failure is an error, not a stale per-process answer."""
        from app.memory.tool_reliability import ToolReliabilityUnavailableError

        store = ToolReliabilityStore(db_session_factory=_FailingDB())
        with pytest.raises(ToolReliabilityUnavailableError):
            await store.get_reliability(tenant_id="t1", tool_name="cached_tool")

    @pytest.mark.asyncio
    async def test_get_unreliable_tools_no_db_uses_recorded_calls(self):
        """Without a DB, unreliable tools come from this process's recorded calls."""
        store = ToolReliabilityStore()
        assert await store.get_unreliable_tools(tenant_id="t1") == []
        for _ in range(3):
            await store.record(tenant_id="t1", tool_name="flaky", success=False)
        await store.record(tenant_id="t1", tool_name="solid", success=True)
        rows = await store.get_unreliable_tools(tenant_id="t1")
        assert [r["tool_name"] for r in rows] == ["flaky"]
        assert await store.get_unreliable_tools(tenant_id="t2") == []

    @pytest.mark.asyncio
    async def test_get_unreliable_tools_with_failing_db_raises(self):
        """MEM-01: DB failure must not read as "no unreliable tools"."""
        from app.memory.tool_reliability import ToolReliabilityUnavailableError

        store = ToolReliabilityStore(db_session_factory=_FailingDB())
        with pytest.raises(ToolReliabilityUnavailableError):
            await store.get_unreliable_tools(tenant_id="t1")

    @pytest.mark.asyncio
    async def test_blacklist_is_a_flag_not_a_failure(self):
        store = ToolReliabilityStore()
        await store.blacklist(tenant_id="t1", tool_name="bad", reason="self_improvement")
        stats = await store.get_reliability(tenant_id="t1", tool_name="bad")
        assert stats["failure_count"] == 0 and stats["total_calls"] == 0
        assert stats["blacklisted"] is True
        rows = await store.get_unreliable_tools(tenant_id="t1")
        assert rows[0]["tool_name"] == "bad" and rows[0]["blacklisted"] is True

    @pytest.mark.asyncio
    async def test_list_tools_reports_every_recorded_tool(self):
        store = ToolReliabilityStore()
        await store.record(tenant_id="t1", tool_name="solid", success=True, latency_ms=10)
        for _ in range(3):
            await store.record(tenant_id="t1", tool_name="flaky", success=False)
        rows = await store.list_tools(tenant_id="t1")
        assert [r["tool_name"] for r in rows] == ["flaky", "solid"]
        assert rows[0]["unreliable"] is True and rows[1]["unreliable"] is False

    @pytest.mark.asyncio
    async def test_record_no_db_field_None(self):
        """record() with explicit db_session_factory=None skips DB path."""
        store = ToolReliabilityStore(db_session_factory=None)
        await store.record(tenant_id="t1", tool_name="safe_tool", success=True)
        assert store._cache["t1:safe_tool"]["success_count"] == 1


class TestToolReliabilityStoreMetrics:
    @pytest.mark.asyncio
    async def test_mixed_success_failure_rate(self):
        store = ToolReliabilityStore()
        for _ in range(7):
            await store.record(tenant_id="t1", tool_name="mixed", success=True)
        for _ in range(3):
            await store.record(tenant_id="t1", tool_name="mixed", success=False)

        result = await store.get_reliability(tenant_id="t1", tool_name="mixed")
        assert result["success_rate"] == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_all_failures(self):
        store = ToolReliabilityStore()
        for _ in range(5):
            await store.record(tenant_id="t1", tool_name="broken", success=False)
        result = await store.get_reliability(tenant_id="t1", tool_name="broken")
        assert result["success_rate"] == pytest.approx(0.0)
        assert result["failure_count"] == 5

    @pytest.mark.asyncio
    async def test_last_used_at_none_in_memory(self):
        store = ToolReliabilityStore()
        await store.record(tenant_id="t1", tool_name="tool1", success=True)
        result = await store.get_reliability(tenant_id="t1", tool_name="tool1")
        # MEM-45: the in-memory path keeps the call time too (decay needs it)
        assert result["last_used_at"] is not None
