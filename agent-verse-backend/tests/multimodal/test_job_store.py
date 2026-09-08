"""Tests for AssetJobStore (D-23: persist asset ingestion jobs).

Before this store existed, MultimodalPipeline kept jobs in a private
``dict`` that vanished on every process restart. These tests pin down:
  * the in-memory-only default behavior (what create_app()'s phase-1
    wiring and unit tests get), and
  * the Redis-backed behavior once a client is wired in (what the FastAPI
    lifespan upgrades to when a real Redis connection is available) --
    exercised here against a small fake async Redis client so it runs
    without any real infra.
"""

from __future__ import annotations

import json

from app.multimodal.job_store import AssetJobStore, _job_key
from app.multimodal.models import AssetIngestionJob, ExtractedSpan, Modality


class _FakeAsyncRedis:
    """Minimal in-process stand-in for the redis-py async client."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.set_calls: list[tuple[str, int | None]] = []

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.store[key] = value
        self.set_calls.append((key, ex))
        return True

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0


class _AlwaysFailingRedis:
    async def get(self, key: str) -> str | None:
        raise ConnectionError("redis down")

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        raise ConnectionError("redis down")

    async def delete(self, key: str) -> int:
        raise ConnectionError("redis down")


def _make_job(tenant_id: str = "t1", job_id: str = "job-1") -> AssetIngestionJob:
    return AssetIngestionJob(
        job_id=job_id,
        tenant_id=tenant_id,
        asset_type=Modality.TEXT,
        status="completed",
        spans=[ExtractedSpan(content="hello", modality=Modality.TEXT, confidence=1.0)],
    )


# ── in-memory-only (no Redis wired) ────────────────────────────────────────


async def test_in_memory_store_round_trips_a_job() -> None:
    store = AssetJobStore()
    assert store.is_persistent() is False
    job = _make_job()
    await store.save(job)
    fetched = await store.get(job.job_id, job.tenant_id)
    assert fetched is not None
    assert fetched.job_id == job.job_id
    assert fetched.spans[0].content == "hello"


async def test_in_memory_store_enforces_tenant_isolation() -> None:
    store = AssetJobStore()
    job = _make_job(tenant_id="tenant-a")
    await store.save(job)
    assert await store.get(job.job_id, "tenant-b") is None


async def test_in_memory_store_missing_job_returns_none() -> None:
    store = AssetJobStore()
    assert await store.get("does-not-exist", "t1") is None


# ── Redis-backed (D-23: survives restart / cross-replica) ─────────────────


async def test_redis_backed_store_is_persistent() -> None:
    store = AssetJobStore(redis=_FakeAsyncRedis())
    assert store.is_persistent() is True


async def test_redis_backed_store_writes_through_on_save() -> None:
    redis = _FakeAsyncRedis()
    store = AssetJobStore(redis=redis)
    job = _make_job(tenant_id="t1", job_id="job-42")
    await store.save(job)
    raw = redis.store[_job_key("t1", "job-42")]
    payload = json.loads(raw)
    assert payload["job_id"] == "job-42"
    assert payload["status"] == "completed"
    # TTL was passed so completed jobs eventually expire from Redis.
    assert redis.set_calls[-1][1] is not None


async def test_redis_backed_store_never_persists_raw_asset_bytes() -> None:
    """source_base64 can be large; it must not be written to Redis."""
    redis = _FakeAsyncRedis()
    store = AssetJobStore(redis=redis)
    job = _make_job()
    job.source_base64 = "a" * 10_000
    await store.save(job)
    raw = redis.store[_job_key(job.tenant_id, job.job_id)]
    assert "source_base64" not in json.loads(raw)


async def test_redis_backed_store_survives_process_restart_simulation() -> None:
    """A fresh store instance sharing the same Redis backend must still see
    a job saved by an earlier (now-discarded) store instance -- this is the
    concrete behavior D-23 requires: jobs outlive the process."""
    redis = _FakeAsyncRedis()
    store_before_restart = AssetJobStore(redis=redis)
    job = _make_job(tenant_id="t1", job_id="job-restart")
    await store_before_restart.save(job)

    # Simulate a restart: brand new store, empty local memory cache, same Redis.
    store_after_restart = AssetJobStore(redis=redis)
    fetched = await store_after_restart.get("job-restart", "t1")
    assert fetched is not None
    assert fetched.job_id == "job-restart"
    assert fetched.spans[0].content == "hello"


async def test_redis_get_failure_falls_back_to_none_without_raising() -> None:
    store = AssetJobStore(redis=_AlwaysFailingRedis())
    result = await store.get("job-1", "t1")
    assert result is None


async def test_redis_save_failure_does_not_raise_and_keeps_memory_copy() -> None:
    """A Redis outage must not fail the ingestion request -- best effort."""
    store = AssetJobStore(redis=_AlwaysFailingRedis())
    job = _make_job()
    await store.save(job)  # must not raise
    # Same-process reads still work off the in-memory cache.
    assert await store.get(job.job_id, job.tenant_id) is not None


async def test_delete_removes_from_memory_and_redis() -> None:
    redis = _FakeAsyncRedis()
    store = AssetJobStore(redis=redis)
    job = _make_job()
    await store.save(job)
    await store.delete(job.job_id, job.tenant_id)
    assert await store.get(job.job_id, job.tenant_id) is None
    assert _job_key(job.tenant_id, job.job_id) not in redis.store
