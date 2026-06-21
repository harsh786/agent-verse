"""Tests for TenantScopedStore — Redis key prefixing ensures tenant isolation."""


from app.tenancy.store import TenantScopedStore


class FakeRedis:
    """Minimal in-memory Redis fake for unit tests."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._sets: dict[str, dict[str, float]] = {}
        self._ttls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._data[key] = value
        if ex is not None:
            self._ttls[key] = ex

    async def delete(self, *keys: str) -> int:
        removed = 0
        for k in keys:
            if k in self._data:
                del self._data[k]
                removed += 1
        return removed

    async def exists(self, *keys: str) -> int:
        return sum(1 for k in keys if k in self._data)

    async def incr(self, key: str) -> int:
        val = int(self._data.get(key, "0")) + 1
        self._data[key] = str(val)
        return val

    async def expire(self, key: str, seconds: int) -> bool:
        self._ttls[key] = seconds
        return key in self._data or key in self._sets

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        if key not in self._sets:
            self._sets[key] = {}
        added = sum(1 for k in mapping if k not in self._sets[key])
        self._sets[key].update(mapping)
        return added

    async def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> int:
        if key not in self._sets:
            return 0
        before = len(self._sets[key])
        self._sets[key] = {
            member: score
            for member, score in self._sets[key].items()
            if not (min_score <= score <= max_score)
        }
        return before - len(self._sets[key])

    async def zcard(self, key: str) -> int:
        return len(self._sets.get(key, {}))


async def test_get_returns_none_for_missing_key() -> None:
    store = TenantScopedStore(FakeRedis(), "t1")  # type: ignore[arg-type]
    assert await store.get("missing") is None


async def test_set_and_get_round_trip() -> None:
    store = TenantScopedStore(FakeRedis(), "t1")  # type: ignore[arg-type]
    await store.set("foo", "bar")
    assert await store.get("foo") == "bar"


async def test_keys_are_tenant_prefixed() -> None:
    redis = FakeRedis()
    store = TenantScopedStore(redis, "t1")  # type: ignore[arg-type]
    await store.set("mykey", "myval")
    assert "tenant:t1:mykey" in redis._data
    assert "mykey" not in redis._data


async def test_two_tenants_are_isolated() -> None:
    redis = FakeRedis()
    s1 = TenantScopedStore(redis, "t1")  # type: ignore[arg-type]
    s2 = TenantScopedStore(redis, "t2")  # type: ignore[arg-type]
    await s1.set("x", "tenant-1-value")
    await s2.set("x", "tenant-2-value")
    assert await s1.get("x") == "tenant-1-value"
    assert await s2.get("x") == "tenant-2-value"


async def test_delete_only_removes_own_keys() -> None:
    redis = FakeRedis()
    s1 = TenantScopedStore(redis, "t1")  # type: ignore[arg-type]
    s2 = TenantScopedStore(redis, "t2")  # type: ignore[arg-type]
    await s1.set("k", "v1")
    await s2.set("k", "v2")
    await s1.delete("k")
    assert await s1.get("k") is None
    assert await s2.get("k") == "v2"  # t2's key untouched


async def test_incr_starts_at_one() -> None:
    store = TenantScopedStore(FakeRedis(), "t1")  # type: ignore[arg-type]
    val = await store.incr("counter")
    assert val == 1


async def test_exists_returns_correct_count() -> None:
    store = TenantScopedStore(FakeRedis(), "t1")  # type: ignore[arg-type]
    await store.set("a", "1")
    assert await store.exists("a") == 1
    assert await store.exists("b") == 0


async def test_zadd_and_zcard() -> None:
    store = TenantScopedStore(FakeRedis(), "t1")  # type: ignore[arg-type]
    await store.zadd("myset", {"m1": 1.0, "m2": 2.0})
    assert await store.zcard("myset") == 2


async def test_zremrangebyscore_prunes_entries() -> None:
    store = TenantScopedStore(FakeRedis(), "t1")  # type: ignore[arg-type]
    await store.zadd("myset", {"old": 100.0, "new": 999.0})
    removed = await store.zremrangebyscore("myset", 0, 500.0)
    assert removed == 1
    assert await store.zcard("myset") == 1
