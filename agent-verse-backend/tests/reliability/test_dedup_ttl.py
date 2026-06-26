"""Tests for DeduplicationCache TTL."""
from __future__ import annotations

import time

import pytest

from app.reliability.dedup import DeduplicationCache
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="dedup-ttl-t1", plan=PlanTier.FREE, api_key_id="dttl1")


def test_dedup_hash_expires_after_ttl():
    cache = DeduplicationCache(ttl_seconds=0.05)
    cache.mark_seen(content_hash="hash1", tenant_ctx=T)
    assert cache.is_duplicate(content_hash="hash1", tenant_ctx=T)
    time.sleep(0.1)
    assert not cache.is_duplicate(content_hash="hash1", tenant_ctx=T)


def test_dedup_clear_removes_tenant_hashes():
    cache = DeduplicationCache()
    cache.mark_seen(content_hash="h1", tenant_ctx=T)
    cache.clear(tenant_ctx=T)
    assert not cache.is_duplicate(content_hash="h1", tenant_ctx=T)


def test_dedup_non_expired_hash_is_still_duplicate():
    cache = DeduplicationCache(ttl_seconds=60.0)
    cache.mark_seen(content_hash="stable", tenant_ctx=T)
    assert cache.is_duplicate(content_hash="stable", tenant_ctx=T)


def test_dedup_different_tenants_isolated():
    """Hashes from one tenant do not affect another."""
    T2 = TenantContext(tenant_id="dedup-ttl-t2", plan=PlanTier.FREE, api_key_id="dttl2")
    cache = DeduplicationCache()
    cache.mark_seen(content_hash="shared_hash", tenant_ctx=T)
    assert not cache.is_duplicate(content_hash="shared_hash", tenant_ctx=T2)
