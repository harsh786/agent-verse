"""T5.2 — tool-result cache: read-only gate, uncacheable guard, TTL, invalidation."""
from __future__ import annotations

from app.rag.tool_result_cache import ToolResultCache, is_uncacheable_result


async def test_read_only_result_roundtrips() -> None:
    c = ToolResultCache()
    stored = await c.set("t1", "jira.search", {"jql": "open"}, {"total": 3}, read_only=True)
    assert stored is True
    assert await c.get("t1", "jira.search", {"jql": "open"}) == {"total": 3}
    # arg order doesn't matter (sorted keys)
    assert await c.get("t1", "jira.search", {"jql": "open"}) == {"total": 3}


async def test_write_tools_are_not_cached() -> None:
    c = ToolResultCache()
    stored = await c.set("t1", "jira.create", {"x": 1}, {"id": "J-1"}, read_only=False)
    assert stored is False
    assert await c.get("t1", "jira.create", {"x": 1}) is None


async def test_uncacheable_results_are_refused() -> None:
    c = ToolResultCache()
    assert is_uncacheable_result({"error": "boom"})
    assert is_uncacheable_result({"total": 0})
    assert is_uncacheable_result([])
    assert is_uncacheable_result("INSUFFICIENT DATA: missing id")
    assert not is_uncacheable_result({"total": 5, "content": ["x"]})
    assert await c.set("t1", "r.read", {}, {"total": 0}, read_only=True) is False
    assert await c.set("t1", "r.read", {}, "Error: failed", read_only=True) is False


async def test_ttl_expiry() -> None:
    c = ToolResultCache(ttl_seconds=0.05)
    await c.set("t1", "r.read", {}, {"total": 1}, read_only=True)
    assert await c.get("t1", "r.read", {}) == {"total": 1}
    import asyncio

    await asyncio.sleep(0.07)
    assert await c.get("t1", "r.read", {}) is None


async def test_invalidate_drops_tool_entries() -> None:
    c = ToolResultCache()
    await c.set("t1", "jira.search", {"a": 1}, {"total": 1}, read_only=True)
    await c.set("t1", "jira.search", {"a": 2}, {"total": 2}, read_only=True)
    await c.set("t1", "gh.search", {"a": 1}, {"total": 9}, read_only=True)
    dropped = c.invalidate("t1", "jira.search")
    assert dropped == 2
    assert await c.get("t1", "jira.search", {"a": 1}) is None
    assert await c.get("t1", "gh.search", {"a": 1}) == {"total": 9}  # untouched


async def test_tenant_isolation() -> None:
    c = ToolResultCache()
    await c.set("t1", "r.read", {}, {"total": 1}, read_only=True)
    assert await c.get("t2", "r.read", {}) is None
