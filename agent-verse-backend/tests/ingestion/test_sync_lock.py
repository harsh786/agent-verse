"""TG-12: the per-Source sync lock is shared across processes (unit level).

The real Redis + Postgres behaviour (two triggers -> one job, fencing) is in
tests/ingestion/test_sync_lock_integration.py.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest

from app.ingestion.job_tracker import (
    IngestionJobTracker,
    SyncLockLostError,
    SyncLockUnavailableError,
)
from app.ingestion.source_config import PipelineResult, RawDocument, SourceConfig
from app.ingestion.source_store import SourceConfigStore


class _BrokenRedis:
    async def set(self, *a: Any, **k: Any) -> Any:
        raise ConnectionError("redis down")

    async def get(self, *a: Any, **k: Any) -> Any:
        raise ConnectionError("redis down")

    async def eval(self, *a: Any, **k: Any) -> Any:
        raise ConnectionError("redis down")


async def test_a_redis_failure_refuses_the_lock_instead_of_a_process_local_one() -> None:
    tracker = IngestionJobTracker(redis=_BrokenRedis())
    with pytest.raises(SyncLockUnavailableError):
        await tracker.acquire_lock("s1", "t1")
    # Nothing was taken in this process's memory, which no other replica sees.
    assert tracker._locks == {}


async def test_the_api_answers_503_when_the_lock_is_unavailable() -> None:
    from app.api.ingestion import SyncEnqueueError, enqueue_source_sync

    with pytest.raises(SyncEnqueueError):
        await enqueue_source_sync(IngestionJobTracker(redis=_BrokenRedis()), "s1", "t1")


async def test_two_trackers_on_one_redis_exclude_each_other() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    api_a, api_b = IngestionJobTracker(redis=redis), IngestionJobTracker(redis=redis)
    token = await api_a.acquire_lock("s1", "t1")
    assert token
    assert await api_b.acquire_lock("s1", "t1") is None
    assert await api_b.running_job_id("s1", "t1") == token
    # An unowned release (no token, or another token) frees nothing.
    await api_b.release_lock("s1", "t1")
    await api_b.release_lock("s1", "t1", "someone-else")
    assert await api_a.running_job_id("s1", "t1") == token


def test_the_worker_tracker_gets_the_shared_redis() -> None:
    from app.ingestion import scheduler

    sentinel = object()
    with (
        patch("app.db.session.get_session_factory", return_value=MagicMock()),
        patch("app.db.session.get_system_session_factory", return_value=MagicMock()),
        patch(
            "app.ingestion.worker_services.build_worker_knowledge_services",
            return_value=(MagicMock(), MagicMock()),
        ),
        patch("app.ingestion.scheduler._build_worker_kg_hook", return_value=None),
        patch.object(scheduler, "_reconcile_redis", return_value=sentinel),
    ):
        tracker, _pipeline, _store = scheduler._build_worker_ingestion()
    assert isinstance(tracker, IngestionJobTracker)
    assert tracker._redis is sentinel


async def test_the_api_tracker_gets_the_shared_redis_in_the_lifespan() -> None:
    from app.core.config import Settings
    from app.main import create_app

    class _Pools:
        def __init__(self) -> None:
            self.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

        async def startup(self) -> None:
            return None

        async def shutdown(self) -> None:
            await self.redis.aclose()

        def health_checks(self) -> list[Any]:
            return []

    pools = _Pools()
    app = create_app(settings=Settings(voice_enabled=False), manage_pools=True, pools=pools)
    async with app.router.lifespan_context(app):
        assert app.state.ingestion_job_tracker._redis is pools.redis


# ── The lease: renewal, loss, fencing ────────────────────────────────────────


async def test_a_lease_that_loses_its_lock_stops_the_run() -> None:
    tracker = IngestionJobTracker()
    token = await tracker.acquire_lock("s1", "t1")
    assert token
    lease = await tracker.hold("s1", "t1", token, ttl_seconds=1)
    assert lease is not None
    lease.check()
    # The lock is taken over (expired + re-acquired elsewhere).
    tracker._locks["s1"] = "another-run"
    for _ in range(40):
        if lease.lost:
            break
        await asyncio.sleep(0.05)
    assert lease.lost
    with pytest.raises(SyncLockLostError):
        lease.check()
    await lease.release()
    assert tracker._locks["s1"] == "another-run"  # not ours to free


async def test_a_stale_fence_commits_no_cursor() -> None:
    tracker = IngestionJobTracker()
    config = SourceConfig(
        source_id="s1", tenant_id="t1", name="n", family="nosql_database",  # type: ignore[arg-type]
        source_type="mongodb",
    )
    job = await tracker.create_job(config, job_id="j1")
    old = await tracker.take_fence("s1", "t1")
    new = await tracker.take_fence("s1", "t1")
    assert new > old
    await tracker.update_cursor(job, "c-new", config, fence=new)
    with pytest.raises(SyncLockLostError):
        await tracker.update_cursor(job, "c-stale", config, fence=old)
    assert config.cursor_value == "c-new"


async def test_a_worker_that_loses_its_lock_mid_sync_fails_the_job_and_stops() -> None:
    from app.ingestion.scheduler import _sync_source_async

    store = SourceConfigStore()
    tracker = IngestionJobTracker()
    config = SourceConfig(
        source_id="s1", tenant_id="t1", name="n", family="nosql_database",  # type: ignore[arg-type]
        source_type="mongodb", collection_id="kb", enabled=True,
    )
    await store.create(config)
    seen: list[str] = []

    class _Connector:
        async def get_delta(self, cfg: Any, cursor: Any) -> Any:
            for i in range(5):
                if i == 2:
                    tracker._locks["s1"] = "another-run"  # taken over
                    await asyncio.sleep(0.6)  # > ttl/3: the renewal notices
                yield RawDocument(
                    doc_id=f"d{i}", source_id="s1", tenant_id="t1", content=b"x",
                    content_type="text/plain",
                ), f"c{i}"

    class _Pipeline:
        async def ingest(self, raw_doc: Any, cfg: Any) -> PipelineResult:
            seen.append(raw_doc.doc_id)
            return PipelineResult(doc_id=raw_doc.doc_id, source_id="s1", tenant_id="t1",
                                  status="indexed")

    task = MagicMock()
    task.retry = MagicMock(side_effect=AssertionError("a lost lock is not retried"))
    with (
        patch(
            "app.ingestion.scheduler._build_worker_ingestion",
            return_value=(tracker, _Pipeline(), store),
        ),
        patch("app.ingestion.connector_registry.get_connector", return_value=_Connector),
        patch("app.ingestion.scheduler._sync_lock_ttl", return_value=1),
    ):
        result = await _sync_source_async(task=task, source_id="s1", tenant_id="t1",
                                          triggered_by="scheduler")
    assert result["error"] == "lock_lost"
    assert seen == ["d0", "d1"]  # nothing after the loss
    (job,) = tracker.list_jobs_for_source("s1")
    assert job.status == "failed"
    assert "lock" in job.error_message
    stored = await store.get("s1", "t1")
    assert stored is not None and not stored.cursor_value  # never committed by the stale run
    assert tracker._locks["s1"] == "another-run"


async def test_an_adopted_lock_held_by_another_run_skips() -> None:
    from app.ingestion.scheduler import _sync_source_async

    tracker = AsyncMock()
    tracker.hold = AsyncMock(return_value=None)
    with patch(
        "app.ingestion.scheduler._build_worker_ingestion",
        return_value=(tracker, MagicMock(), MagicMock()),
    ):
        result = await _sync_source_async(
            task=MagicMock(), source_id="s1", tenant_id="t1", triggered_by="manual",
            job_id="queued-token",
        )
    assert result == {"skipped": True, "reason": "already_running"}
    tracker.create_job.assert_not_called()
