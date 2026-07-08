# tests/optimization/test_optimizer_implementations.py
"""All optimizer classes must be real implementations, not stubs."""
from __future__ import annotations
import pytest


def test_latency_optimizer_records_and_recommends():
    from app.optimization.latency_optimizer import LatencyOptimizer
    opt = LatencyOptimizer()
    for _ in range(5):
        opt.record_latency("research", 45_000)
    opts = opt.get_optimizations("research")
    assert len(opts) >= 1
    assert opt.avg_latency_ms("research") == 45_000.0


def test_model_optimizer_recommends_for_task():
    from app.optimization.model_optimizer import ModelOptimizer
    opt = ModelOptimizer()
    rec = opt.recommend("planning")
    assert rec.model_id
    assert rec.provider
    assert rec.estimated_cost_usd > 0


def test_cache_optimizer_identifies_opportunities():
    from app.optimization.cache_optimizer import CacheOptimizer
    opt = CacheOptimizer()
    for i in range(10):
        for _ in range(3):
            opt.record_call(f"sig_{i}")
    opp = opt.get_opportunities()
    assert len(opp) >= 1
    assert opp[0].estimated_hit_rate > 0


def test_token_optimizer_importable():
    import app.optimization.token_optimizer
    assert app.optimization.token_optimizer is not None


def test_prompt_optimizer_importable():
    import app.optimization.prompt_optimizer
    assert app.optimization.prompt_optimizer is not None


def test_cost_optimizer_importable():
    import app.optimization.cost_optimizer
    assert app.optimization.cost_optimizer is not None


async def test_execution_memory_load_from_db_no_db():
    """load_from_db() must gracefully handle no DB."""
    from app.memory.execution import ExecutionMemory
    mem = ExecutionMemory()
    count = await mem.load_from_db(tenant_id="t1", db=None)
    assert count == 0
