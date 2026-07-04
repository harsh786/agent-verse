"""Tests for LLMResponseCache (app/rag/llm_response_cache.py)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.rag.llm_response_cache import LLMResponseCache


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_cache(redis=None) -> LLMResponseCache:
    return LLMResponseCache(redis=redis, planning_ttl=60, verify_ttl=120)


# ── Miss / Hit basic flow ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_miss_returns_none():
    cache = _make_cache()
    result = await cache.get(
        system="sys", user="user", model="gpt-4", tenant_id="t1", task_type="planning"
    )
    assert result is None


@pytest.mark.asyncio
async def test_set_then_get_returns_cached():
    cache = _make_cache()
    await cache.set(
        system="sys", user="goal: find tickets",
        model="gpt-4", response='{"steps":["search jira"]}',
        tenant_id="t1", task_type="planning",
    )
    result = await cache.get(
        system="sys", user="goal: find tickets",
        model="gpt-4", tenant_id="t1", task_type="planning",
    )
    assert result == '{"steps":["search jira"]}'


@pytest.mark.asyncio
async def test_different_model_is_cache_miss():
    cache = _make_cache()
    await cache.set(
        system="s", user="u", model="gpt-4",
        response="response-a", tenant_id="t1", task_type="planning",
    )
    result = await cache.get(
        system="s", user="u", model="gpt-3.5-turbo", tenant_id="t1", task_type="planning",
    )
    assert result is None


@pytest.mark.asyncio
async def test_different_tenant_is_cache_miss():
    cache = _make_cache()
    await cache.set(
        system="s", user="u", model="gpt-4",
        response="resp", tenant_id="tenant-A", task_type="planning",
    )
    result = await cache.get(
        system="s", user="u", model="gpt-4", tenant_id="tenant-B", task_type="planning",
    )
    assert result is None


@pytest.mark.asyncio
async def test_different_system_prompt_is_cache_miss():
    cache = _make_cache()
    await cache.set(
        system="plan carefully", user="u", model="m",
        response="resp", tenant_id="t", task_type="planning",
    )
    result = await cache.get(
        system="plan quickly", user="u", model="m", tenant_id="t", task_type="planning",
    )
    assert result is None


# ── Task type filtering ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execution_task_type_bypasses_cache():
    cache = _make_cache()
    await cache.set(
        system="s", user="u", model="m",
        response="resp", tenant_id="t", task_type="execution",
    )
    result = await cache.get(
        system="s", user="u", model="m", tenant_id="t", task_type="execution",
    )
    assert result is None  # execution is not a cacheable task type


@pytest.mark.asyncio
async def test_verification_task_type_is_cached():
    cache = _make_cache()
    await cache.set(
        system="s", user="u", model="m",
        response='{"success":true}', tenant_id="t", task_type="verification",
    )
    result = await cache.get(
        system="s", user="u", model="m", tenant_id="t", task_type="verification",
    )
    assert result == '{"success":true}'


# ── should_skip_cache ─────────────────────────────────────────────────────────

def test_should_skip_cache_replanning():
    assert LLMResponseCache.should_skip_cache("[Previous attempt feedback] step 1 failed") is True


def test_should_skip_cache_tool_failed():
    assert LLMResponseCache.should_skip_cache("Step result: [TOOL FAILED]") is True


def test_should_skip_cache_normal_goal():
    assert LLMResponseCache.should_skip_cache("Goal: find all open JIRA tickets") is False


# ── Stats ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stats_hit_rate():
    cache = _make_cache()
    await cache.set(system="s", user="u", model="m", response="r", tenant_id="t", task_type="planning")
    await cache.get(system="s", user="u", model="m", tenant_id="t", task_type="planning")  # hit
    await cache.get(system="s", user="MISS", model="m", tenant_id="t", task_type="planning")  # miss
    s = cache.stats("t")
    assert s["hits"] == 1
    assert s["misses"] == 1
    assert s["hit_rate"] == 0.5


@pytest.mark.asyncio
async def test_stats_l1_hits_tracked():
    cache = _make_cache()
    await cache.set(system="s", user="u", model="m", response="r", tenant_id="t", task_type="planning")
    await cache.get(system="s", user="u", model="m", tenant_id="t", task_type="planning")
    s = cache.stats("t")
    assert s["l1_hits"] >= 1


# ── Redis integration ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_redis_miss_falls_through_to_l1():
    redis = AsyncMock()
    redis.get.return_value = None  # Redis miss
    cache = _make_cache(redis=redis)

    # Warm L1
    await cache.set(system="s", user="u", model="m", response="r", tenant_id="t", task_type="planning")
    result = await cache.get(system="s", user="u", model="m", tenant_id="t", task_type="planning")
    assert result == "r"


@pytest.mark.asyncio
async def test_redis_hit_promotes_to_l1():
    import json, zlib
    redis = AsyncMock()
    cached = zlib.compress(json.dumps({"content": "cached-plan", "model": "m", "ts": 1000}).encode())
    redis.get.return_value = cached
    cache = _make_cache(redis=redis)

    result = await cache.get(system="s", user="u", model="m", tenant_id="t", task_type="planning")
    assert result == "cached-plan"
    # Now L1 should have it too
    assert "t" in cache._local


@pytest.mark.asyncio
async def test_redis_error_does_not_raise():
    redis = AsyncMock()
    redis.get.side_effect = ConnectionError("redis down")
    cache = _make_cache(redis=redis)
    result = await cache.get(system="s", user="u", model="m", tenant_id="t", task_type="planning")
    assert result is None  # graceful degradation


# ── Clear ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clear_removes_l1_entries():
    cache = _make_cache()
    await cache.set(system="s", user="u", model="m", response="r", tenant_id="t", task_type="planning")
    await cache.clear("t")
    result = await cache.get(system="s", user="u", model="m", tenant_id="t", task_type="planning")
    assert result is None


# ── L1 eviction ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_l1_evicts_when_full():
    cache = LLMResponseCache(max_local=3)
    for i in range(4):
        await cache.set(
            system="s", user=f"u{i}", model="m",
            response=f"r{i}", tenant_id="t", task_type="planning",
        )
    # Should still work; just oldest was evicted
    assert len(cache._local.get("t", {})) <= 3
