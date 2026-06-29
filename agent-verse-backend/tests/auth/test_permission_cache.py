"""Comprehensive tests for app/auth/permission_cache.py."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.auth.permission_cache import PermissionCache


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_permission_cache_ttl():
    assert PermissionCache.TTL == 300


def test_permission_cache_prefix():
    assert PermissionCache.PREFIX == "perm:"


# ---------------------------------------------------------------------------
# _key
# ---------------------------------------------------------------------------


def test_key_format():
    cache = PermissionCache(redis=MagicMock())
    key = cache._key("tenant-1", "key-abc")
    assert key == "perm:tenant-1:key-abc"


def test_key_different_tenants():
    cache = PermissionCache(redis=MagicMock())
    k1 = cache._key("t1", "key-1")
    k2 = cache._key("t2", "key-1")
    assert k1 != k2


def test_key_different_keys():
    cache = PermissionCache(redis=MagicMock())
    k1 = cache._key("t1", "key-1")
    k2 = cache._key("t1", "key-2")
    assert k1 != k2


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


async def test_get_returns_scope_set_on_cache_hit():
    scopes = ["goals:read", "agents:read", "knowledge:read"]
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=json.dumps(scopes))
    cache = PermissionCache(redis=redis_mock)

    result = await cache.get("t1", "key-1")

    assert isinstance(result, set)
    assert result == set(scopes)
    redis_mock.get.assert_awaited_once_with("perm:t1:key-1")


async def test_get_returns_none_on_cache_miss():
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)
    cache = PermissionCache(redis=redis_mock)

    result = await cache.get("t1", "key-1")
    assert result is None


async def test_get_returns_empty_set_when_empty_list_cached():
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=json.dumps([]))
    cache = PermissionCache(redis=redis_mock)

    result = await cache.get("t1", "key-1")
    assert result == set()


async def test_get_handles_full_scope_set():
    all_scopes = [
        "goals:read", "goals:write", "goals:delete", "goals:execute",
        "agents:read", "agents:write",
        "knowledge:read", "knowledge:write", "knowledge:delete",
        "governance:read", "governance:write", "governance:approve",
    ]
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=json.dumps(all_scopes))
    cache = PermissionCache(redis=redis_mock)

    result = await cache.get("t1", "admin-key")
    assert "governance:approve" in result
    assert len(result) == len(all_scopes)


# ---------------------------------------------------------------------------
# set
# ---------------------------------------------------------------------------


async def test_set_stores_sorted_scopes_with_ttl():
    redis_mock = AsyncMock()
    redis_mock.setex = AsyncMock()
    cache = PermissionCache(redis=redis_mock)

    scopes = {"goals:write", "goals:read", "agents:read"}
    await cache.set("t1", "key-1", scopes)

    redis_mock.setex.assert_awaited_once()
    call_args = redis_mock.setex.call_args[0]
    key, ttl, value = call_args
    assert key == "perm:t1:key-1"
    assert ttl == 300
    stored = json.loads(value)
    assert sorted(stored) == sorted(list(scopes))


async def test_set_stores_sorted_list():
    redis_mock = AsyncMock()
    redis_mock.setex = AsyncMock()
    cache = PermissionCache(redis=redis_mock)

    await cache.set("t1", "key-1", {"z:read", "a:write"})

    call_args = redis_mock.setex.call_args[0]
    stored = json.loads(call_args[2])
    assert stored == sorted(["z:read", "a:write"])


async def test_set_stores_empty_scope_set():
    redis_mock = AsyncMock()
    redis_mock.setex = AsyncMock()
    cache = PermissionCache(redis=redis_mock)

    await cache.set("t1", "key-1", set())
    call_args = redis_mock.setex.call_args[0]
    stored = json.loads(call_args[2])
    assert stored == []


# ---------------------------------------------------------------------------
# invalidate
# ---------------------------------------------------------------------------


async def test_invalidate_deletes_key():
    redis_mock = AsyncMock()
    redis_mock.delete = AsyncMock()
    cache = PermissionCache(redis=redis_mock)

    await cache.invalidate("t1", "key-1")
    redis_mock.delete.assert_awaited_once_with("perm:t1:key-1")


async def test_invalidate_different_key_from_get():
    redis_mock = AsyncMock()
    redis_mock.delete = AsyncMock()
    cache = PermissionCache(redis=redis_mock)

    await cache.invalidate("t1", "key-X")
    deleted_key = redis_mock.delete.call_args[0][0]
    assert deleted_key == "perm:t1:key-X"


# ---------------------------------------------------------------------------
# invalidate_tenant
# ---------------------------------------------------------------------------


async def test_invalidate_tenant_scans_and_deletes_all_keys():
    keys_found = [b"perm:t1:key-1", b"perm:t1:key-2", b"perm:t1:key-3"]

    redis_mock = AsyncMock()
    # First scan returns cursor=0 (done) with keys
    redis_mock.scan = AsyncMock(return_value=(0, keys_found))
    redis_mock.delete = AsyncMock()

    cache = PermissionCache(redis=redis_mock)
    await cache.invalidate_tenant("t1")

    redis_mock.scan.assert_awaited_once_with(0, match="perm:t1:*", count=200)
    redis_mock.delete.assert_awaited_once_with(*keys_found)


async def test_invalidate_tenant_handles_multiple_scan_pages():
    # First scan: cursor=5 (continue), second scan: cursor=0 (done)
    redis_mock = AsyncMock()
    redis_mock.scan = AsyncMock(side_effect=[
        (5, [b"perm:t1:key-1"]),
        (0, [b"perm:t1:key-2"]),
    ])
    redis_mock.delete = AsyncMock()

    cache = PermissionCache(redis=redis_mock)
    await cache.invalidate_tenant("t1")

    assert redis_mock.scan.call_count == 2
    assert redis_mock.delete.call_count == 2


async def test_invalidate_tenant_no_keys_found():
    redis_mock = AsyncMock()
    redis_mock.scan = AsyncMock(return_value=(0, []))
    redis_mock.delete = AsyncMock()

    cache = PermissionCache(redis=redis_mock)
    await cache.invalidate_tenant("t1")

    redis_mock.scan.assert_awaited_once()
    redis_mock.delete.assert_not_called()


async def test_invalidate_tenant_uses_correct_pattern():
    redis_mock = AsyncMock()
    redis_mock.scan = AsyncMock(return_value=(0, []))
    redis_mock.delete = AsyncMock()

    cache = PermissionCache(redis=redis_mock)
    await cache.invalidate_tenant("my-tenant-id")

    scan_kwargs = redis_mock.scan.call_args[1]
    assert scan_kwargs["match"] == "perm:my-tenant-id:*"


# ---------------------------------------------------------------------------
# Round-trip: set → get
# ---------------------------------------------------------------------------


async def test_round_trip_set_get():
    """Simulate a full set-then-get cycle using a real dict as Redis stub."""
    store: dict = {}

    redis_mock = AsyncMock()

    async def fake_setex(key, ttl, value):
        store[key] = value

    async def fake_get(key):
        return store.get(key)

    redis_mock.setex = fake_setex
    redis_mock.get = fake_get

    cache = PermissionCache(redis=redis_mock)
    scopes = {"goals:read", "goals:write", "agents:read"}
    await cache.set("t1", "my-key", scopes)

    result = await cache.get("t1", "my-key")
    assert result == scopes
