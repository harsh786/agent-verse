"""Tests for ToolResultCache (app/mcp/tool_cache.py)."""
from __future__ import annotations

import json
import zlib
import pytest
from unittest.mock import AsyncMock

from app.mcp.tool_cache import ToolResultCache, classify_tool, ttl_for_tool


# ── Tool classification ───────────────────────────────────────────────────────

def test_classify_read_tools():
    assert classify_tool("search_issues") == "read"
    assert classify_tool("get_ticket") == "read"
    assert classify_tool("list_projects") == "static"
    assert classify_tool("find_users") == "read"


def test_classify_write_tools():
    assert classify_tool("create_issue") == "write"
    assert classify_tool("delete_page") == "write"
    assert classify_tool("update_ticket") == "write"
    assert classify_tool("send_message") == "write"


def test_classify_static_tools():
    assert classify_tool("list_projects") == "static"
    assert classify_tool("get_schema") == "static"
    assert classify_tool("list_spaces") == "static"


def test_classify_unknown():
    # Tools that don't match read/write/static patterns → unknown (not cached)
    assert classify_tool("analyze_data") == "unknown"
    assert classify_tool("process_event") == "unknown"


def test_execute_and_run_are_write():
    # execute_* and run_* intentionally classify as write (side-effectful)
    assert classify_tool("execute_code") == "write"
    assert classify_tool("run_query") == "write"


def test_ttl_write_returns_none():
    assert ttl_for_tool("create_issue") is None
    assert ttl_for_tool("delete_page") is None


def test_ttl_read_tools():
    ttl = ttl_for_tool("search_issues")
    assert ttl == 300  # 5 minutes


def test_ttl_static_tools():
    ttl = ttl_for_tool("list_projects")
    assert ttl == 3600  # 1 hour


def test_ttl_slow_read_gets_longer_ttl():
    ttl = ttl_for_tool("search_issues", duration_ms=3000)
    assert ttl == 1800  # 30 minutes


def test_ttl_unknown_returns_none():
    assert ttl_for_tool("run_script") is None


# ── Basic cache operations ────────────────────────────────────────────────────

@pytest.fixture
def cache():
    return ToolResultCache()


@pytest.mark.asyncio
async def test_miss_returns_none(cache):
    result = await cache.get(
        server_id="srv", tool_name="search_issues",
        arguments={"jql": "project=FOO"}, tenant_id="t1",
    )
    assert result is None


@pytest.mark.asyncio
async def test_set_then_get(cache):
    args = {"jql": "project=FOO"}
    payload = [{"id": "1", "title": "Bug"}]
    await cache.set(
        server_id="srv", tool_name="search_issues",
        arguments=args, result=payload, tenant_id="t1",
    )
    result = await cache.get(
        server_id="srv", tool_name="search_issues",
        arguments=args, tenant_id="t1",
    )
    assert result == payload


@pytest.mark.asyncio
async def test_write_tool_not_cached(cache):
    """Write tools must never be cached."""
    await cache.set(
        server_id="srv", tool_name="create_issue",
        arguments={"title": "Bug"}, result={"id": "new-1"}, tenant_id="t1",
    )
    result = await cache.get(
        server_id="srv", tool_name="create_issue",
        arguments={"title": "Bug"}, tenant_id="t1",
    )
    assert result is None


@pytest.mark.asyncio
async def test_different_arguments_miss(cache):
    await cache.set(
        server_id="srv", tool_name="search_issues",
        arguments={"jql": "project=A"}, result=["a"], tenant_id="t1",
    )
    result = await cache.get(
        server_id="srv", tool_name="search_issues",
        arguments={"jql": "project=B"}, tenant_id="t1",
    )
    assert result is None


@pytest.mark.asyncio
async def test_different_tenant_miss(cache):
    await cache.set(
        server_id="srv", tool_name="list_projects",
        arguments={}, result=["proj1"], tenant_id="tenant-A",
    )
    result = await cache.get(
        server_id="srv", tool_name="list_projects",
        arguments={}, tenant_id="tenant-B",
    )
    assert result is None


# ── Stats ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stats_hit_miss(cache):
    await cache.set(
        server_id="s", tool_name="get_ticket", arguments={"id": "1"},
        result={"title": "T"}, tenant_id="t",
    )
    await cache.get(server_id="s", tool_name="get_ticket", arguments={"id": "1"}, tenant_id="t")  # hit
    await cache.get(server_id="s", tool_name="get_ticket", arguments={"id": "2"}, tenant_id="t")  # miss
    s = cache.stats("t")
    assert s["hits"] >= 1
    assert s["misses"] >= 1
    assert s["hit_rate"] > 0


# ── Redis integration ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_redis_hit_returned():
    redis = AsyncMock()
    payload = [{"id": "X"}]
    compressed = zlib.compress(json.dumps({"result": payload, "tool": "search_issues", "ts": 100}).encode())
    redis.get.return_value = compressed
    cache = ToolResultCache(redis=redis)

    result = await cache.get(
        server_id="s", tool_name="search_issues",
        arguments={"q": "foo"}, tenant_id="t",
    )
    assert result == payload


@pytest.mark.asyncio
async def test_redis_error_graceful():
    redis = AsyncMock()
    redis.get.side_effect = ConnectionError("down")
    cache = ToolResultCache(redis=redis)

    result = await cache.get(
        server_id="s", tool_name="search_issues",
        arguments={}, tenant_id="t",
    )
    assert result is None  # no exception raised


# ── Stale fallback ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stale_key_returned_within_max_age():
    import time
    redis = AsyncMock()
    payload = {"old": "data"}
    compressed = zlib.compress(json.dumps({"result": payload, "ts": int(time.time()) - 100}).encode())
    redis.get.return_value = compressed
    cache = ToolResultCache(redis=redis)

    result = await cache.get_stale(
        server_id="s", tool_name="search_issues",
        arguments={}, tenant_id="t", max_age_seconds=3600,
    )
    assert result == payload


@pytest.mark.asyncio
async def test_stale_key_rejected_when_too_old():
    import time
    redis = AsyncMock()
    payload = {"old": "data"}
    # 2 hours old
    compressed = zlib.compress(json.dumps({"result": payload, "ts": int(time.time()) - 7200}).encode())
    redis.get.return_value = compressed
    cache = ToolResultCache(redis=redis)

    result = await cache.get_stale(
        server_id="s", tool_name="search_issues",
        arguments={}, tenant_id="t", max_age_seconds=3600,
    )
    assert result is None


# ── Invalidation ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invalidate_writes_clears_l1():
    cache = ToolResultCache()
    await cache.set(
        server_id="s", tool_name="search_issues",
        arguments={}, result=["x"], tenant_id="t",
    )
    await cache.invalidate_writes("create_issue", tenant_id="t", server_id="s")
    # L1 cleared
    assert "t" not in cache._local or len(cache._local.get("t", {})) == 0
