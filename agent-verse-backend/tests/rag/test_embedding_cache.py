"""T5.1 — embedding cache: L1 round-trip, keying, batch, optional Redis."""
from __future__ import annotations

from typing import Any

from app.rag.embedding_cache import EmbeddingCache


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: Any) -> None:
        self.store[key] = value


async def test_set_get_roundtrip_and_miss() -> None:
    c = EmbeddingCache()
    assert await c.get("m1", "hello world") is None
    await c.set("m1", "hello world", [0.1, 0.2, 0.3])
    assert await c.get("m1", "hello world") == [0.1, 0.2, 0.3]


async def test_whitespace_normalized_but_case_sensitive() -> None:
    c = EmbeddingCache()
    await c.set("m1", "hello   world", [1.0, 2.0])
    assert await c.get("m1", "hello world") == [1.0, 2.0]  # whitespace-insensitive hit
    assert await c.get("m1", "Hello world") is None  # case-sensitive miss


async def test_model_discriminates_key() -> None:
    c = EmbeddingCache()
    await c.set("m1", "x", [1.0])
    assert await c.get("m2", "x") is None


async def test_lru_eviction() -> None:
    c = EmbeddingCache(max_size=2)
    await c.set("m", "a", [1.0])
    await c.set("m", "b", [2.0])
    await c.get("m", "a")  # touch a → b now LRU
    await c.set("m", "c", [3.0])  # evicts b
    assert await c.get("m", "b") is None
    assert await c.get("m", "a") == [1.0]
    assert await c.get("m", "c") == [3.0]


async def test_get_batch_reports_hits_and_misses() -> None:
    c = EmbeddingCache()
    await c.set("m", "cached", [9.0])
    hits, misses = await c.get_batch("m", ["cached", "new1", "new2"])
    assert hits == {0: [9.0]}
    assert misses == [1, 2]


async def test_redis_l2_roundtrip_survives_l1_clear() -> None:
    redis = _FakeRedis()
    c1 = EmbeddingCache(redis=redis)
    await c1.set("m", "shared", [0.5, 0.25])
    # A fresh replica (empty L1) still hits via Redis L2.
    c2 = EmbeddingCache(redis=redis)
    assert await c2.get("m", "shared") == [0.5, 0.25]


async def test_redis_error_degrades_to_miss() -> None:
    class _BadRedis:
        async def get(self, key: str) -> bytes:
            raise RuntimeError("redis down")

        async def set(self, key: str, value: Any) -> None:
            raise RuntimeError("redis down")

    c = EmbeddingCache(redis=_BadRedis())
    await c.set("m", "x", [1.0])  # set must not raise
    # L1 still serves; redis error on a fresh instance → miss, not crash
    c2 = EmbeddingCache(redis=_BadRedis())
    assert await c2.get("m", "x") is None
