"""Tests for agent performance benchmarking."""
from __future__ import annotations
import pytest
from app.intelligence.benchmarking import BenchmarkStore, AgentBenchmark
from app.intelligence.eval import EvalScorecard
from app.tenancy.context import TenantContext, PlanTier

CTX = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")


def _scorecard(avg: float, goal_id: str = "g1") -> EvalScorecard:
    return EvalScorecard(
        goal_id=goal_id,
        scores={k: avg for k in ["task_completion", "efficiency", "accuracy", "safety", "coherence"]},
    )


def test_record_eval_creates_benchmark():
    store = BenchmarkStore()
    bench = store.record_eval(agent_id="a1", scorecard=_scorecard(0.8), tenant_ctx=CTX)
    assert bench.run_count == 1
    assert bench.avg_scores["task_completion"] == pytest.approx(0.8)


def test_record_eval_accumulates():
    store = BenchmarkStore()
    store.record_eval(agent_id="a1", scorecard=_scorecard(0.6, "g1"), tenant_ctx=CTX)
    store.record_eval(agent_id="a1", scorecard=_scorecard(0.8, "g2"), tenant_ctx=CTX)
    bench = store.get_benchmark(agent_id="a1", tenant_ctx=CTX)
    assert bench.run_count == 2
    assert bench.avg_scores["task_completion"] == pytest.approx(0.7)


def test_trend_improving():
    store = BenchmarkStore()
    for i, score in enumerate([0.4, 0.4, 0.4, 0.9, 0.9, 0.9]):
        store.record_eval(agent_id="a1", scorecard=_scorecard(score, f"g{i}"), tenant_ctx=CTX)
    bench = store.get_benchmark(agent_id="a1", tenant_ctx=CTX)
    assert bench.trend == "improving"


def test_trend_declining():
    store = BenchmarkStore()
    for i, score in enumerate([0.9, 0.9, 0.9, 0.4, 0.4, 0.4]):
        store.record_eval(agent_id="a1", scorecard=_scorecard(score, f"g{i}"), tenant_ctx=CTX)
    bench = store.get_benchmark(agent_id="a1", tenant_ctx=CTX)
    assert bench.trend == "declining"


def test_compare_agents():
    store = BenchmarkStore()
    store.record_eval(agent_id="a1", scorecard=_scorecard(0.9), tenant_ctx=CTX)
    store.record_eval(agent_id="a2", scorecard=_scorecard(0.5), tenant_ctx=CTX)
    result = store.compare_agents(agent_ids=["a1", "a2"], tenant_ctx=CTX)
    assert result[0]["agent_id"] == "a1"  # higher score first
    assert result[1]["agent_id"] == "a2"


def test_tenant_isolation():
    store = BenchmarkStore()
    ctx2 = TenantContext(tenant_id="t2", plan=PlanTier.FREE, api_key_id="k2")
    store.record_eval(agent_id="a1", scorecard=_scorecard(0.8), tenant_ctx=CTX)
    benches = store.list_benchmarks(tenant_ctx=ctx2)
    assert benches == []


# ---------------------------------------------------------------------------
# Phase 25: DB-backed BenchmarkStore tests
# ---------------------------------------------------------------------------

def test_benchmark_store_has_db_persistence():
    from app.intelligence.benchmarking import BenchmarkStore
    store = BenchmarkStore()
    import asyncio
    assert hasattr(store, "record_run_async")
    assert asyncio.iscoroutinefunction(store.record_run_async)


def test_cli_module_exists():
    import os
    cli_path = "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-sdk-python/agentverse/cli.py"
    assert os.path.exists(cli_path), "CLI module must exist"


def test_stuck_goal_task_exists():
    import inspect
    from app.scaling import tasks
    src = inspect.getsource(tasks)
    assert "detect_stuck_goals" in src or "stuck_goal" in src, \
        "Stuck goal detector task must exist in tasks.py"


def test_migration_0032_exists():
    import os
    files = os.listdir("/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-backend/app/db/migrations/versions")
    assert any("0032" in f for f in files), "Migration 0032 (benchmark_runs) must exist"
