"""P1b-1: the source sync lock and cancel flag live in the SHARED Redis.

Live finding (2026-10-05): the API's tracker and the worker's tracker had no Redis,
so the lock was per-process memory. The API took it on ``POST /sources/{id}/sync``
and the worker could never release it — every later manual sync of that source was
answered ``already_running`` until the API restarted — while a scheduled sync in the
worker ran concurrently with the manual one (two jobs, duplicate DLQ rows), and a
cancel request never reached the worker.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest

from app.ingestion.job_tracker import IngestionJobTracker, attach_shared_redis
from app.ingestion.scheduler import _sync_source_async
from app.ingestion.source_config import PipelineResult, RawDocument, SourceConfig, SourceFamily


def _config() -> SourceConfig:
    return SourceConfig(
        source_id="src-1", tenant_id="t1", name="s", family=SourceFamily.OBJECT_STORAGE,
        source_type="s3", collection_id="col-1",
    )


@pytest.mark.parametrize("decode", [True, False])
def test_release_lock_releases_with_either_response_type(decode: bool) -> None:
    async def run() -> None:
        redis = fakeredis.aioredis.FakeRedis(decode_responses=decode)
        tracker = IngestionJobTracker(redis=redis)
        token = await tracker.acquire_lock("src-1", "t1")
        assert token is not None
        await tracker.release_lock("src-1", "t1", token)
        assert await redis.get("ingestion_lock:t1:src-1") is None
        assert await tracker.acquire_lock("src-1", "t1") is not None

    asyncio.run(run())


def test_release_lock_keeps_a_lock_held_by_another_job() -> None:
    async def run() -> None:
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        tracker = IngestionJobTracker(redis=redis)
        token = await tracker.acquire_lock("src-1", "t1")
        await tracker.release_lock("src-1", "t1", "someone-else")
        assert await redis.get("ingestion_lock:t1:src-1") == token

    asyncio.run(run())


def test_attach_shared_redis_only_fills_a_missing_client() -> None:
    tracker = IngestionJobTracker()
    shared = object()
    assert attach_shared_redis(tracker, shared) is True
    assert tracker._redis is shared
    assert attach_shared_redis(tracker, object()) is False
    assert tracker._redis is shared
    assert attach_shared_redis(None, shared) is False


class _OneDocConnector:
    def __init__(self) -> None:
        self.source_type = "s3"

    async def get_delta(self, config, cursor):  # type: ignore[no-untyped-def]
        yield RawDocument(doc_id="d1", source_id="src-1", tenant_id="t1", content=b"hello",
                          content_type="text/plain"), "c1"


def _pipeline() -> AsyncMock:
    p = AsyncMock()
    p.ingest.return_value = PipelineResult(
        doc_id="d1", source_id="src-1", tenant_id="t1", status="indexed")
    return p


def _store() -> AsyncMock:
    store = AsyncMock()
    store.get.return_value = _config()
    return store


def _run_sync(redis: object, *, job_id: str | None) -> dict:
    tracker = IngestionJobTracker()  # the worker's own, Redis-less tracker

    async def run() -> dict:
        with (
            patch("app.ingestion.scheduler._build_worker_ingestion",
                  return_value=(tracker, _pipeline(), _store())),
            patch("app.ingestion.scheduler._shared_redis", return_value=redis),
            patch("app.ingestion.connector_registry.get_connector",
                  return_value=_OneDocConnector),
            patch("app.ingestion.scheduler._schedule_reconcile_if_due",
                  AsyncMock(return_value=False)),
        ):
            return await _sync_source_async(task=AsyncMock(), source_id="src-1",
                                            tenant_id="t1", triggered_by="manual",
                                            job_id=job_id)

    return asyncio.run(run())


def test_worker_releases_the_lock_the_api_took() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    api_tracker = IngestionJobTracker(redis=redis)

    async def take() -> str | None:
        return await api_tracker.acquire_lock("src-1", "t1")

    token = asyncio.run(take())
    assert token
    result = _run_sync(redis, job_id=token)
    assert result.get("docs_indexed") == 1

    async def check() -> tuple[object, str | None]:
        return await redis.get("ingestion_lock:t1:src-1"), await api_tracker.acquire_lock(
            "src-1", "t1")

    held, again = asyncio.run(check())
    assert held is None, "the worker left the API's lock behind"
    assert again, "a second manual sync must be accepted once the first finished"


def test_scheduled_sync_waits_for_a_running_manual_sync() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    api_tracker = IngestionJobTracker(redis=redis)

    async def take() -> str | None:
        return await api_tracker.acquire_lock("src-1", "t1")

    assert asyncio.run(take())
    result = _run_sync(redis, job_id=None)  # the scheduler's run, no job id
    assert result == {"skipped": True, "reason": "already_running"}


def test_cancel_requested_through_the_api_reaches_the_worker() -> None:
    async def run() -> bool:
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        api = IngestionJobTracker(redis=redis)
        worker = IngestionJobTracker()
        attach_shared_redis(worker, redis)
        token = await api.acquire_lock("src-1", "t1")
        assert await api.request_cancel("src-1", "t1") == token
        return await worker.is_cancel_requested("t1", str(token))

    assert asyncio.run(run()) is True
