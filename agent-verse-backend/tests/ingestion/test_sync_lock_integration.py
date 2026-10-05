"""TG-12 against a real Redis and a migrated Postgres.

Every "process" (API replica, Celery worker) gets its own Redis client and its
own engine, exactly as separate processes would. Two simultaneous sync triggers
must produce ONE job and ingest each document once; a run that loses its lock
can commit nothing (fencing token); renewal keeps a long run's lock.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/ingestion/test_sync_lock_integration.py
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.ingestion.job_tracker import IngestionJobTracker, SyncLockLostError
from app.ingestion.source_config import PipelineResult, RawDocument, SourceConfig
from app.ingestion.source_store import SourceConfigStore

pytestmark = pytest.mark.integration

_DOCS = 40


class _Processes:
    """Factory of per-"process" (engine, redis client) pairs, all disposed at the end."""

    def __init__(self, pg_url: str, redis_url: str) -> None:
        self.pg_url = pg_url
        self.redis_url = redis_url
        self._engines: list[AsyncEngine] = []
        self._redis: list[Any] = []

    def tracker(self) -> IngestionJobTracker:
        engine = create_async_engine(self.pg_url, pool_size=2, max_overflow=0)
        self._engines.append(engine)
        client = aioredis.from_url(self.redis_url, decode_responses=True)
        self._redis.append(client)
        return IngestionJobTracker(
            db=async_sessionmaker(engine, expire_on_commit=False), redis=client
        )

    def store(self) -> SourceConfigStore:
        engine = create_async_engine(self.pg_url, pool_size=2, max_overflow=0)
        self._engines.append(engine)
        return SourceConfigStore(db=async_sessionmaker(engine, expire_on_commit=False))

    async def close(self) -> None:
        for client in self._redis:
            await client.aclose()
        for engine in self._engines:
            await engine.dispose()


@pytest_asyncio.fixture
async def procs(pg_url: str, redis_url: str) -> AsyncIterator[_Processes]:
    p = _Processes(pg_url, redis_url)
    yield p
    await p.close()


@pytest_asyncio.fixture
async def source(pg_url: str, procs: _Processes) -> SourceConfig:
    tenant = f"lock-{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(pg_url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES (:id, :id, :email, 'free', true)"
            ),
            {"id": tenant, "email": f"{tenant}@example.test"},
        )
    await engine.dispose()
    config = SourceConfig(
        source_id=f"src-{uuid.uuid4().hex[:8]}",
        tenant_id=tenant,
        name="locked source",
        family="nosql_database",  # type: ignore[arg-type]
        source_type="mongodb",
        enabled=True,
        collection_id="kb-lock",
    )
    await procs.store().create(config)
    return config


async def _job_rows(pg_url: str, source_id: str) -> list[dict[str, Any]]:
    engine = create_async_engine(pg_url)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT id, status FROM ingestion_jobs WHERE source_id = :s"),
                {"s": source_id},
            )
        ).mappings().all()
    await engine.dispose()
    return [dict(r) for r in rows]


async def test_two_simultaneous_triggers_produce_one_job_and_no_duplicates(
    pg_url: str, procs: _Processes, source: SourceConfig
) -> None:
    from app.api.ingestion import enqueue_source_sync
    from app.ingestion.scheduler import _sync_source_async

    sid, tid = source.source_id, source.tenant_id

    # Two API replicas receive "Sync now" at the same moment.
    with patch("app.ingestion.scheduler.sync_source_task") as task_mod:
        tokens = await asyncio.gather(
            enqueue_source_sync(procs.tracker(), sid, tid),
            enqueue_source_sync(procs.tracker(), sid, tid),
        )
        queued = [c.kwargs["kwargs"] for c in task_mod.apply_async.call_args_list]
    assert sum(t is not None for t in tokens) == 1, tokens
    assert len(queued) == 1

    ingested: list[str] = []

    class _Connector:
        async def get_delta(self, cfg: Any, cursor: Any) -> AsyncIterator[Any]:
            for i in range(_DOCS):
                await asyncio.sleep(0.01)
                yield (
                    RawDocument(
                        doc_id=f"doc-{i}",
                        source_id=sid,
                        tenant_id=tid,
                        content=b"x",
                        content_type="text/plain",
                    ),
                    f"cursor-{i}",
                )

    class _Pipeline:
        async def ingest(self, raw_doc: Any, cfg: Any) -> PipelineResult:
            ingested.append(raw_doc.doc_id)
            return PipelineResult(
                doc_id=raw_doc.doc_id, source_id=sid, tenant_id=tid, status="indexed"
            )

    def _worker_process() -> tuple[Any, Any, Any]:
        return procs.tracker(), _Pipeline(), procs.store()

    task = MagicMock()
    task.retry.side_effect = lambda exc, **_kw: exc
    # The queued manual sync and a beat-scheduled sync start together on two workers.
    with (
        patch("app.ingestion.scheduler._build_worker_ingestion", side_effect=_worker_process),
        patch("app.ingestion.connector_registry.get_connector", return_value=_Connector),
        patch("app.ingestion.scheduler._schedule_reconcile_if_due", return_value=False),
    ):
        results = await asyncio.gather(
            _sync_source_async(task=task, **queued[0]),
            _sync_source_async(
                task=task, source_id=sid, tenant_id=tid, triggered_by="scheduler"
            ),
        )

    ran = [r for r in results if "job_id" in r]
    skipped = [r for r in results if r.get("reason") == "already_running"]
    assert len(ran) == 1 and len(skipped) == 1, results
    assert ran[0]["job_id"] == queued[0]["job_id"]
    assert ran[0]["docs_indexed"] == _DOCS
    assert sorted(ingested) == sorted(f"doc-{i}" for i in range(_DOCS))  # each once
    rows = await _job_rows(pg_url, sid)
    assert [(r["id"], r["status"]) for r in rows] == [(queued[0]["job_id"], "completed")]
    # The lock was released by its holder, across processes.
    assert await procs.tracker().running_job_id(sid, tid) is None
    stored = await procs.store().get(sid, tid)
    assert stored is not None and stored.cursor_value == f"cursor-{_DOCS - 1}"


async def test_a_run_that_lost_its_lock_commits_no_cursor(
    pg_url: str, procs: _Processes, source: SourceConfig
) -> None:
    sid, tid = source.source_id, source.tenant_id
    first, second = procs.tracker(), procs.tracker()

    token1 = await first.acquire_lock(sid, tid, ttl_seconds=60)
    assert token1
    lease1 = await first.hold(sid, tid, token1, ttl_seconds=1)
    assert lease1 is not None
    job1 = await first.create_job(source, job_id=token1)

    # The first worker stalls; its lock expires and another run takes over.
    await first._redis.delete(f"ingestion_lock:{tid}:{sid}")
    token2 = await second.acquire_lock(sid, tid, ttl_seconds=60)
    assert token2
    lease2 = await second.hold(sid, tid, token2, ttl_seconds=60)
    assert lease2 is not None and lease2.fence > lease1.fence
    job2 = await second.create_job(source, job_id=token2)
    await second.update_cursor(job2, "from-the-new-run", source, fence=lease2.fence)

    # The stale run wakes up: its cursor commit matches nothing, and its
    # renewal finds the lock gone.
    with pytest.raises(SyncLockLostError):
        await first.update_cursor(job1, "stale", source, fence=lease1.fence)
    for _ in range(40):
        if lease1.lost:
            break
        await asyncio.sleep(0.05)
    assert lease1.lost
    stored = await procs.store().get(sid, tid)
    assert stored is not None and stored.cursor_value == "from-the-new-run"

    # Releasing the stale lease does not free the new run's lock.
    await lease1.release()
    assert await second.running_job_id(sid, tid) == token2
    await lease2.release()
    assert await second.running_job_id(sid, tid) is None


async def test_renewal_keeps_a_long_run_locked_past_its_ttl(
    procs: _Processes, source: SourceConfig
) -> None:
    sid, tid = source.source_id, source.tenant_id
    worker, other = procs.tracker(), procs.tracker()
    token = await worker.acquire_lock(sid, tid, ttl_seconds=60)
    assert token
    lease = await worker.hold(sid, tid, token, ttl_seconds=1)
    assert lease is not None
    await asyncio.sleep(2.5)  # 2.5 TTLs
    assert not lease.lost
    assert await other.acquire_lock(sid, tid) is None
    assert await other.running_job_id(sid, tid) == token
    # A wrong token can neither renew nor release it.
    assert await other.renew_lock(sid, tid, "not-the-token", 60) is False
    await other.release_lock(sid, tid, "not-the-token")
    assert await other.running_job_id(sid, tid) == token
    await lease.release()
    assert await other.acquire_lock(sid, tid)


async def test_api_worker_api_a_second_sync_starts_once_the_first_finishes(
    pg_url: str, procs: _Processes, source: SourceConfig
) -> None:
    """NF-5: after one manual sync every later "Sync now" answered "already
    running" until the API restarted (the API's process-local lock was
    "released" by the worker in another process)."""
    from app.api.ingestion import enqueue_source_sync
    from app.ingestion.scheduler import _sync_source_async

    sid, tid = source.source_id, source.tenant_id
    api = procs.tracker()  # one long-lived API process

    class _Connector:
        async def get_delta(self, cfg: Any, cursor: Any) -> AsyncIterator[Any]:
            yield (
                RawDocument(doc_id="d", source_id=sid, tenant_id=tid, content=b"x",
                            content_type="text/plain"),
                "c",
            )

    class _Pipeline:
        async def ingest(self, raw_doc: Any, cfg: Any) -> PipelineResult:
            return PipelineResult(doc_id="d", source_id=sid, tenant_id=tid, status="indexed")

    task = MagicMock()
    task.retry.side_effect = lambda exc, **_kw: exc
    job_ids: list[str] = []
    for _round in range(2):
        with patch("app.ingestion.scheduler.sync_source_task") as task_mod:
            job_id = await enqueue_source_sync(api, sid, tid)
            assert job_id is not None, "a finished sync still holds the lock"
            # While it is queued / running, another trigger is refused.
            assert await enqueue_source_sync(api, sid, tid) is None
            queued = task_mod.apply_async.call_args.kwargs["kwargs"]
        with (
            patch(
                "app.ingestion.scheduler._build_worker_ingestion",
                side_effect=lambda: (procs.tracker(), _Pipeline(), procs.store()),
            ),
            patch("app.ingestion.connector_registry.get_connector", return_value=_Connector),
            patch("app.ingestion.scheduler._schedule_reconcile_if_due", return_value=False),
        ):
            result = await _sync_source_async(task=task, **queued)  # another process
        assert result["job_id"] == job_id
        assert await api.running_job_id(sid, tid) is None
        job_ids.append(job_id)

    rows = await _job_rows(pg_url, sid)
    assert sorted(r["id"] for r in rows) == sorted(job_ids)
    assert all(r["status"] == "completed" for r in rows)
