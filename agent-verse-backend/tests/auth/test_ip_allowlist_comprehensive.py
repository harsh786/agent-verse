"""Comprehensive tests for app/auth/ip_allowlist.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.auth.ip_allowlist import IPAllowlistCache, is_ip_allowed


# ---------------------------------------------------------------------------
# is_ip_allowed — pure function
# ---------------------------------------------------------------------------


def test_is_ip_allowed_empty_cidrs_allows_all():
    assert is_ip_allowed("1.2.3.4", []) is True
    assert is_ip_allowed("10.0.0.1", []) is True
    assert is_ip_allowed("192.168.1.1", []) is True


def test_is_ip_allowed_loopback_always_permitted():
    cidrs = ["192.168.0.0/24"]  # loopback is NOT in this range
    assert is_ip_allowed("127.0.0.1", cidrs) is True
    assert is_ip_allowed("::1", cidrs) is True


def test_is_ip_allowed_ipv4_in_cidr():
    assert is_ip_allowed("10.0.0.5", ["10.0.0.0/8"]) is True


def test_is_ip_allowed_ipv4_not_in_cidr():
    assert is_ip_allowed("172.16.0.1", ["10.0.0.0/8"]) is False


def test_is_ip_allowed_exact_host_match():
    assert is_ip_allowed("203.0.113.45", ["203.0.113.45/32"]) is True


def test_is_ip_allowed_outside_range():
    assert is_ip_allowed("8.8.8.8", ["192.168.1.0/24"]) is False


def test_is_ip_allowed_multiple_cidrs_first_match():
    cidrs = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
    assert is_ip_allowed("172.16.5.5", cidrs) is True
    assert is_ip_allowed("1.2.3.4", cidrs) is False


def test_is_ip_allowed_malformed_ip_denied():
    assert is_ip_allowed("not-an-ip", ["10.0.0.0/8"]) is False
    assert is_ip_allowed("999.999.999.999", ["10.0.0.0/8"]) is False


def test_is_ip_allowed_malformed_cidr_skipped():
    # Malformed CIDR should be skipped; others still evaluated
    cidrs = ["not-a-cidr", "10.0.0.0/8"]
    assert is_ip_allowed("10.5.5.5", cidrs) is True


def test_is_ip_allowed_all_cidrs_malformed_denies():
    cidrs = ["bad-cidr-1", "bad-cidr-2"]
    assert is_ip_allowed("10.5.5.5", cidrs) is False


def test_is_ip_allowed_ipv6_in_cidr():
    assert is_ip_allowed("2001:db8::1", ["2001:db8::/32"]) is True


def test_is_ip_allowed_ipv6_loopback():
    assert is_ip_allowed("::1", ["10.0.0.0/8"]) is True


def test_is_ip_allowed_strict_false_on_host_cidr():
    # strict=False allows "10.0.0.1/8" (host bits set)
    assert is_ip_allowed("10.5.5.5", ["10.0.0.1/8"]) is True


# ---------------------------------------------------------------------------
# IPAllowlistCache._key
# ---------------------------------------------------------------------------


def test_ip_allowlist_cache_key_format():
    cache = IPAllowlistCache(redis=MagicMock())
    key = cache._key("tenant-123")
    assert key == "ip_wl:tenant-123"


def test_ip_allowlist_cache_prefix():
    assert IPAllowlistCache.PREFIX == "ip_wl:"


def test_ip_allowlist_cache_ttl():
    assert IPAllowlistCache.TTL == 60


# ---------------------------------------------------------------------------
# IPAllowlistCache.get_cidrs — cache hit
# ---------------------------------------------------------------------------


async def test_get_cidrs_cache_hit_returns_cidrs():
    import json
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=json.dumps(["10.0.0.0/8", "192.168.0.0/16"]))
    cache = IPAllowlistCache(redis=redis_mock)

    cidrs = await cache.get_cidrs("t1")
    assert cidrs == ["10.0.0.0/8", "192.168.0.0/16"]
    # DB factory should not be called
    redis_mock.set.assert_not_called()


async def test_get_cidrs_cache_hit_empty_list():
    import json
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=json.dumps([]))
    cache = IPAllowlistCache(redis=redis_mock)

    cidrs = await cache.get_cidrs("t1")
    assert cidrs == []


# ---------------------------------------------------------------------------
# IPAllowlistCache.get_cidrs — cache miss, DB available
# ---------------------------------------------------------------------------


async def test_get_cidrs_cache_miss_queries_db():
    import json
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)  # Cache miss
    redis_mock.setex = AsyncMock()

    result_mock = MagicMock()
    result_mock.fetchall.return_value = [("10.0.0.0/8",), ("192.168.0.0/16",)]

    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    session_mock.execute = AsyncMock(return_value=result_mock)
    db_factory = MagicMock(return_value=session_mock)

    cache = IPAllowlistCache(redis=redis_mock)
    # Patch sqlalchemy.select so the ORM query building doesn't crash,
    # and patch the model import inside the function at its source module.
    mock_model = MagicMock()
    mock_col = MagicMock()
    mock_model.cidr = mock_col
    mock_model.tenant_id = MagicMock()
    mock_model.is_active = MagicMock()

    with (
        patch("sqlalchemy.select", return_value=MagicMock()),
        patch("app.db.models.auth.IPAllowlistEntry", mock_model),
    ):
        cidrs = await cache.get_cidrs("t1", db_factory=db_factory)

    assert cidrs == ["10.0.0.0/8", "192.168.0.0/16"]
    # Should have populated the cache
    redis_mock.setex.assert_awaited_once()
    call_args = redis_mock.setex.call_args[0]
    assert call_args[1] == 60  # TTL


async def test_get_cidrs_cache_miss_no_db_returns_empty():
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)
    cache = IPAllowlistCache(redis=redis_mock)

    cidrs = await cache.get_cidrs("t1", db_factory=None)
    assert cidrs == []


async def test_get_cidrs_db_error_returns_empty():
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)

    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    session_mock.execute = AsyncMock(side_effect=Exception("DB error"))
    db_factory = MagicMock(return_value=session_mock)

    cache = IPAllowlistCache(redis=redis_mock)
    # Fail-open: return empty list on DB error
    cidrs = await cache.get_cidrs("t1", db_factory=db_factory)
    assert cidrs == []


# ---------------------------------------------------------------------------
# IPAllowlistCache.invalidate
# ---------------------------------------------------------------------------


async def test_invalidate_deletes_cache_key():
    redis_mock = AsyncMock()
    redis_mock.delete = AsyncMock()
    cache = IPAllowlistCache(redis=redis_mock)

    await cache.invalidate("tenant-xyz")

    redis_mock.delete.assert_awaited_once_with("ip_wl:tenant-xyz")


async def test_invalidate_different_tenants_use_different_keys():
    redis_mock = AsyncMock()
    redis_mock.delete = AsyncMock()
    cache = IPAllowlistCache(redis=redis_mock)

    await cache.invalidate("tenant-A")
    await cache.invalidate("tenant-B")

    calls = [c[0][0] for c in redis_mock.delete.call_args_list]
    assert "ip_wl:tenant-A" in calls
    assert "ip_wl:tenant-B" in calls
