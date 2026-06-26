"""Tests for SemanticCache TTL behavior."""
from __future__ import annotations

import time

import pytest

from app.rag.semantic_cache import SemanticCache
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="cache-ttl-t1", plan=PlanTier.FREE, api_key_id="cttl1")


def test_cache_entry_expires_after_ttl():
    cache = SemanticCache(threshold=0.9, ttl_seconds=0.05)  # 50 ms TTL
    embedding = [1.0, 0.0, 0.0]
    cache.store(query_embedding=embedding, response="cached", tenant_ctx=T)
    # Immediately: should hit
    result = cache.lookup(query_embedding=embedding, tenant_ctx=T)
    assert result == "cached"
    # After TTL: should miss
    time.sleep(0.1)
    result2 = cache.lookup(query_embedding=embedding, tenant_ctx=T)
    assert result2 is None


def test_cache_stats_track_hits_and_misses():
    cache = SemanticCache(threshold=0.99)
    embedding = [1.0, 0.0, 0.0]
    cache.store(query_embedding=embedding, response="cached", tenant_ctx=T)
    cache.lookup(query_embedding=embedding, tenant_ctx=T)          # hit
    cache.lookup(query_embedding=[0.0, 1.0, 0.0], tenant_ctx=T)   # miss
    stats = cache.stats(tenant_ctx=T)
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["cached_entries"] == 1


def test_cache_clear_removes_entries():
    cache = SemanticCache()
    embedding = [1.0, 0.0, 0.0]
    cache.store(query_embedding=embedding, response="data", tenant_ctx=T)
    cache.clear(tenant_ctx=T)
    result = cache.lookup(query_embedding=embedding, tenant_ctx=T)
    assert result is None


def test_cache_clear_resets_stats():
    cache = SemanticCache(threshold=0.99)
    embedding = [1.0, 0.0, 0.0]
    cache.store(query_embedding=embedding, response="v", tenant_ctx=T)
    cache.lookup(query_embedding=embedding, tenant_ctx=T)  # hit
    cache.clear(tenant_ctx=T)
    stats = cache.stats(tenant_ctx=T)
    assert stats["hits"] == 0
    assert stats["misses"] == 0
    assert stats["cached_entries"] == 0


def test_cache_prunes_expired_on_store():
    """Expired entries are pruned when storing a new entry."""
    cache = SemanticCache(threshold=0.9, ttl_seconds=0.05)
    embedding = [1.0, 0.0, 0.0]
    cache.store(query_embedding=embedding, response="old", tenant_ctx=T)
    time.sleep(0.1)
    cache.store(query_embedding=[0.0, 1.0, 0.0], response="new", tenant_ctx=T)
    # Only the new entry should remain
    stats = cache.stats(tenant_ctx=T)
    assert stats["cached_entries"] == 1
