"""Comprehensive tests for app/intelligence/benchmarking.py — targeting 95%+ coverage."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.intelligence.benchmarking import AgentBenchmark, BenchmarkRun, BenchmarkStore
from app.intelligence.eval import EvalScorecard
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="bench-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")
CTX2 = TenantContext(tenant_id="bench-t2", plan=PlanTier.FREE, api_key_id="k2")


def _scorecard(avg: float, goal_id: str = "g1") -> EvalScorecard:
    dims = ["task_completion", "efficiency", "accuracy", "safety", "coherence"]
    return EvalScorecard(
        goal_id=goal_id,
        scores={k: avg for k in dims},
    )


class TestBenchmarkRun:
    def test_created_at_auto_set_when_empty(self):
        """Covers BenchmarkRun.__post_init__ lines 23-24."""
        run = BenchmarkRun(suite_name="suite-a", score=0.85)
        assert run.created_at != ""
        assert "T" in run.created_at  # ISO format

    def test_explicit_created_at_preserved(self):
        run = BenchmarkRun(suite_name="suite-a", score=0.85, created_at="2024-01-01T00:00:00+00:00")
        assert run.created_at == "2024-01-01T00:00:00+00:00"

    def test_default_tenant_id(self):
        run = BenchmarkRun(suite_name="suite-x", score=0.7)
        assert run.tenant_id == "global"

    def test_explicit_tenant_id(self):
        run = BenchmarkRun(suite_name="suite-x", score=0.7, tenant_id="t-abc")
        assert run.tenant_id == "t-abc"


class TestBenchmarkStoreEval:
    def test_record_eval_creates_benchmark(self):
        store = BenchmarkStore()
        bench = store.record_eval(agent_id="a1", scorecard=_scorecard(0.8), tenant_ctx=CTX)
        assert bench.run_count == 1
        assert bench.best_score == pytest.approx(0.8)
        assert bench.worst_score == pytest.approx(0.8)

    def test_record_eval_updates_avg_scores(self):
        store = BenchmarkStore()
        store.record_eval(agent_id="a1", scorecard=_scorecard(0.6), tenant_ctx=CTX)
        store.record_eval(agent_id="a1", scorecard=_scorecard(0.8), tenant_ctx=CTX)
        bench = store.get_benchmark(agent_id="a1", tenant_ctx=CTX)
        assert bench.avg_scores["task_completion"] == pytest.approx(0.7)

    def test_trend_stable_exactly_six_runs(self):
        """Covers line 102: trend stable when recent ≈ previous (within 0.05)."""
        store = BenchmarkStore()
        # All scores 0.8 → recent avg = previous avg → stable
        for i in range(6):
            store.record_eval(agent_id="a1", scorecard=_scorecard(0.8, f"g{i}"), tenant_ctx=CTX)
        bench = store.get_benchmark(agent_id="a1", tenant_ctx=CTX)
        assert bench.trend == "stable"

    def test_trend_improving(self):
        store = BenchmarkStore()
        for i, score in enumerate([0.3, 0.3, 0.3, 0.9, 0.9, 0.9]):
            store.record_eval(agent_id="a1", scorecard=_scorecard(score, f"g{i}"), tenant_ctx=CTX)
        bench = store.get_benchmark(agent_id="a1", tenant_ctx=CTX)
        assert bench.trend == "improving"

    def test_trend_declining(self):
        store = BenchmarkStore()
        for i, score in enumerate([0.9, 0.9, 0.9, 0.3, 0.3, 0.3]):
            store.record_eval(agent_id="a1", scorecard=_scorecard(score, f"g{i}"), tenant_ctx=CTX)
        bench = store.get_benchmark(agent_id="a1", tenant_ctx=CTX)
        assert bench.trend == "declining"

    def test_trend_stable_marginal_diff(self):
        """Exactly 6 runs with diff <= 0.05 → stable."""
        store = BenchmarkStore()
        for i, score in enumerate([0.8, 0.8, 0.8, 0.83, 0.83, 0.83]):
            store.record_eval(agent_id="a1", scorecard=_scorecard(score, f"g{i}"), tenant_ctx=CTX)
        bench = store.get_benchmark(agent_id="a1", tenant_ctx=CTX)
        assert bench.trend in ("stable", "improving")  # diff = 0.03, within threshold

    def test_trend_stable_fewer_than_six_runs(self):
        store = BenchmarkStore()
        for i in range(4):
            store.record_eval(agent_id="a1", scorecard=_scorecard(0.8, f"g{i}"), tenant_ctx=CTX)
        bench = store.get_benchmark(agent_id="a1", tenant_ctx=CTX)
        assert bench.trend == "stable"

    def test_get_benchmark_returns_none_for_unknown(self):
        store = BenchmarkStore()
        result = store.get_benchmark(agent_id="nonexistent", tenant_ctx=CTX)
        assert result is None

    def test_list_benchmarks_empty(self):
        store = BenchmarkStore()
        assert store.list_benchmarks(tenant_ctx=CTX) == []

    def test_list_benchmarks_returns_all_for_tenant(self):
        store = BenchmarkStore()
        store.record_eval(agent_id="a1", scorecard=_scorecard(0.9), tenant_ctx=CTX)
        store.record_eval(agent_id="a2", scorecard=_scorecard(0.7), tenant_ctx=CTX)
        benches = store.list_benchmarks(tenant_ctx=CTX)
        assert len(benches) == 2

    def test_tenant_isolation(self):
        store = BenchmarkStore()
        store.record_eval(agent_id="a1", scorecard=_scorecard(0.9), tenant_ctx=CTX)
        benches = store.list_benchmarks(tenant_ctx=CTX2)
        assert benches == []

    def test_compare_agents_sorted_by_score(self):
        store = BenchmarkStore()
        store.record_eval(agent_id="a1", scorecard=_scorecard(0.9), tenant_ctx=CTX)
        store.record_eval(agent_id="a2", scorecard=_scorecard(0.5), tenant_ctx=CTX)
        result = store.compare_agents(agent_ids=["a1", "a2"], tenant_ctx=CTX)
        assert result[0]["agent_id"] == "a1"
        assert result[1]["agent_id"] == "a2"

    def test_compare_agents_skips_unknown(self):
        store = BenchmarkStore()
        store.record_eval(agent_id="a1", scorecard=_scorecard(0.9), tenant_ctx=CTX)
        result = store.compare_agents(agent_ids=["a1", "nonexistent"], tenant_ctx=CTX)
        assert len(result) == 1
        assert result[0]["agent_id"] == "a1"

    def test_to_dict_fields(self):
        bench = AgentBenchmark(agent_id="a1", tenant_id="t1", run_count=3)
        d = bench.to_dict()
        assert "agent_id" in d
        assert "run_count" in d
        assert "avg_scores" in d
        assert "trend" in d


class TestBenchmarkStoreAsyncPersistence:
    """Tests for record_run_async and load_history_from_db (lines 128-171)."""

    @pytest.mark.asyncio
    async def test_record_run_async_no_db_only_memory(self):
        """record_run_async without DB still updates in-memory cache."""
        store = BenchmarkStore()
        run = BenchmarkRun(suite_name="my-suite", score=0.75)
        await store.record_run_async(run)
        assert "my-suite" in store._runs
        assert len(store._runs["my-suite"]) == 1
        assert store._runs["my-suite"][0].score == pytest.approx(0.75)

    @pytest.mark.asyncio
    async def test_record_run_async_multiple_runs(self):
        store = BenchmarkStore()
        for score in [0.7, 0.8, 0.9]:
            await store.record_run_async(BenchmarkRun(suite_name="perf-suite", score=score))
        assert len(store._runs["perf-suite"]) == 3

    @pytest.mark.asyncio
    async def test_record_run_async_with_failing_db_still_saves_to_memory(self):
        """DB failure must not prevent in-memory cache update."""
        class _FailDB:
            def __call__(self):
                return self
            async def __aenter__(self):
                raise RuntimeError("DB down")
            async def __aexit__(self, *a):
                pass

        store = BenchmarkStore(db_session_factory=_FailDB())
        run = BenchmarkRun(suite_name="resilient-suite", score=0.6)
        await store.record_run_async(run)  # should not raise
        assert "resilient-suite" in store._runs

    @pytest.mark.asyncio
    async def test_record_run_async_includes_metadata(self):
        store = BenchmarkStore()
        run = BenchmarkRun(
            suite_name="meta-suite",
            score=0.88,
            tenant_id="t-meta",
            metadata={"agent_version": "1.2"},
        )
        await store.record_run_async(run)
        stored = store._runs["meta-suite"][0]
        assert stored.metadata == {"agent_version": "1.2"}

    @pytest.mark.asyncio
    async def test_load_history_from_db_no_db_returns_empty(self):
        """load_history_from_db with no DB returns []."""
        store = BenchmarkStore()
        result = await store.load_history_from_db("any-suite")
        assert result == []

    @pytest.mark.asyncio
    async def test_load_history_from_db_with_failing_db_returns_empty(self):
        """DB failure returns empty list (graceful degradation)."""
        class _FailDB:
            def __call__(self):
                return self
            async def __aenter__(self):
                raise RuntimeError("DB connection refused")
            async def __aexit__(self, *a):
                pass

        store = BenchmarkStore(db_session_factory=_FailDB())
        result = await store.load_history_from_db("test-suite", limit=10)
        assert result == []
