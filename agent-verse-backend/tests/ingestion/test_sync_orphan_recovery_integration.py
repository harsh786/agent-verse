"""SYNC-ORPHAN on a real Postgres + Redis: a sync whose worker died is resumed.

A knowledge-Source sync whose worker died stayed ``running`` until the
age-based reaper failed it two hours later, and was never run again. Here the
real sync task runs against a migrated Postgres (as a NOBYPASSRLS app role) and
a real Redis, with the real ingestion pipeline and knowledge store:

* a worker dies mid-sync (its coroutine freezes and its lock renewal stops --
  what SIGKILL leaves behind). Orphan recovery finds it once its heartbeat is
  stale and its lock expired, requeues the same job, and a new worker resumes
  it from the checkpointed cursor: every document indexed exactly once, no
  duplicate chunks, attempt 2 with its requeue reason on the job. The zombie,
  woken up later, can commit nothing.
* a slow worker that is alive (still renewing its lock) is never requeued.
* a job whose attempts are spent is failed with the reason, and its Source is
  backed off.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/ingestion/test_sync_orphan_recovery_integration.py -m integration
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.ingestion.job_tracker import IngestionJobTracker, SyncLease
from app.ingestion.orphan_recovery import RecoverySettings, recover_orphaned_syncs
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily
from app.ingestion.source_store import SourceConfigStore
from app.rag.models import KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from tests.rag._pg import admin_exec, app_engine, seed_tenant

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_DIM = 768
_DOCS = 250  # checkpoints at 100 and 200
_DIE_AFTER = 160  # the first worker dies with docs 100..159 indexed past its checkpoint
_LOCK_TTL = 5  # seconds (the floor of _sync_lock_ttl); renewed every ~1.7 s
_FILL = " This knowledge base article keeps enough words to pass every quality gate." * 3


class _Embedder:
    async def embed(self, request: Any) -> Any:
        from app.providers.base import EmbedResponse

        return EmbedResponse(embeddings=[[0.1] * _DIM for _ in request.texts], model="fake")


@dataclass
class _Upstream:
    """A cursor-based source of ``_DOCS`` documents (cursor = last index handed over).

    ``freeze_after`` makes the delta block forever after that many documents
    of a run (the worker "dies" there) until ``thaw`` is set.
    """

    source_id: str
    tenant_id: str
    freeze_after: int | None = None
    thaw: asyncio.Event = field(default_factory=asyncio.Event)
    frozen: asyncio.Event = field(default_factory=asyncio.Event)
    cursors_seen: list[str | None] = field(default_factory=list)

    def connector(self) -> type:
        upstream = self

        class _Connector:
            async def get_delta(self, cfg: Any, cursor: Any) -> AsyncIterator[Any]:
                upstream.cursors_seen.append(cursor)
                start = int(cursor) + 1 if cursor else 0
                for handed, i in enumerate(range(start, _DOCS)):
                    if upstream.freeze_after is not None and handed == upstream.freeze_after:
                        upstream.frozen.set()
                        await upstream.thaw.wait()
                    await asyncio.sleep(0)
                    yield (
                        RawDocument(
                            doc_id=f"doc-{i:04d}",
                            source_id=upstream.source_id,
                            tenant_id=upstream.tenant_id,
                            content=f"Article {i}: how to reset gadget model {i}.{_FILL}".encode(),
                            content_type="text/plain",
                            title=f"Article {i}",
                            source_url=f"https://kb.example.test/articles/{i}",
                        ),
                        str(i),
                    )

        return _Connector


@dataclass
class _World:
    pg_url: str
    redis_url: str
    app: AsyncEngine
    admin: AsyncEngine
    store: KnowledgeStore
    config: SourceConfig
    clients: list[Any] = field(default_factory=list)

    def sessions(self) -> Any:
        return async_sessionmaker(self.app, expire_on_commit=False)

    def tracker(self) -> IngestionJobTracker:
        """A tracker as one worker / beat process would build it: its own Redis client."""
        client = aioredis.from_url(self.redis_url, decode_responses=True)
        self.clients.append(client)
        return IngestionJobTracker(
            db=self.sessions(),
            system_db=async_sessionmaker(self.admin, expire_on_commit=False),
            redis=client,
        )

    def source_store(self) -> SourceConfigStore:
        return SourceConfigStore(db=self.sessions())

    def pipeline(self) -> IngestionPipeline:
        return IngestionPipeline(knowledge_store=self.store, embedder=_Embedder())


@pytest.fixture(autouse=True)
def _short_lock_ttl(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_SYNC_LOCK_TTL_SECONDS", str(_LOCK_TTL))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def world(pg_url: str, redis_url: str) -> AsyncIterator[_World]:
    tid = str(uuid.uuid4())
    await seed_tenant(pg_url, tid)
    app = await app_engine(pg_url)
    admin = create_async_engine(pg_url, pool_size=2)
    db = async_sessionmaker(app, expire_on_commit=False)
    store = KnowledgeStore(db, embedding_dim=_DIM)
    ctx = TenantContext(tid, PlanTier.FREE, "k")
    cid = await store.create_collection_async(KnowledgeCollection(name="kb"), tenant_ctx=ctx)
    config = SourceConfig(
        source_id=uuid.uuid4().hex,
        tenant_id=tid,
        name="help centre",
        family=SourceFamily.WEB,
        source_type="http",
        collection_id=cid,
        min_quality_score=0.0,
    )
    await SourceConfigStore(db=db).create(config)
    w = _World(pg_url, redis_url, app, admin, store, config)
    try:
        yield w
    finally:
        for client in w.clients:
            with contextlib.suppress(Exception):
                await client.aclose()
        await app.dispose()
        await admin.dispose()


def _settings(**overrides: Any) -> RecoverySettings:
    base: dict[str, Any] = {
        "stale_seconds": 2.0, "max_attempts": 3, "backoff_seconds": 30, "backoff_max_seconds": 600
    }
    base.update(overrides)
    return RecoverySettings(**base)


class _Worker:
    """One worker process running ``_sync_source_async`` (its own tracker + pipeline)."""

    def __init__(self, world: _World, upstream: _Upstream) -> None:
        self.world = world
        self.upstream = upstream
        self.leases: list[SyncLease] = []

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        from app.ingestion.scheduler import _sync_source_async

        tracker = self.world.tracker()
        hold = tracker.hold

        async def _capturing_hold(*args: Any, **kw: Any) -> SyncLease | None:
            lease = await hold(*args, **kw)
            if lease is not None:
                self.leases.append(lease)
            return lease

        tracker.hold = _capturing_hold  # type: ignore[method-assign]
        task = MagicMock()
        task.retry.side_effect = lambda exc=None, **_kw: exc or RuntimeError("retry")
        with (
            patch(
                "app.ingestion.scheduler._build_worker_ingestion",
                return_value=(tracker, self.world.pipeline(), self.world.source_store()),
            ),
            patch(
                "app.ingestion.connector_registry.get_connector",
                return_value=self.upstream.connector(),
            ),
            patch("app.ingestion.scheduler._schedule_reconcile_if_due", return_value=False),
        ):
            return dict(await _sync_source_async(task=task, **kwargs))

    def kill(self) -> None:
        """What SIGKILL leaves: the run is frozen and nothing renews its lock."""
        for lease in self.leases:
            if lease._task is not None:
                lease._task.cancel()


async def _job(world: _World, job_id: str) -> dict[str, Any]:
    rows = await admin_exec(
        world.pg_url,
        "SELECT status, attempts, requeue_reason, error_message, docs_discovered, docs_indexed, "
        "docs_skipped, docs_failed, lease_token, cursor_after FROM ingestion_jobs WHERE id = :id",
        {"id": job_id},
    )
    keys = (
        "status", "attempts", "requeue_reason", "error_message", "docs_discovered",
        "docs_indexed", "docs_skipped", "docs_failed", "lease_token", "cursor_after",
    )
    return dict(zip(keys, rows[0], strict=True))


async def _chunk_census(world: _World) -> tuple[int, int, int]:
    """(chunks, distinct documents, distinct (document, chunk_index)) of the Source."""
    rows = await admin_exec(
        world.pg_url,
        f"SELECT count(*), count(DISTINCT document_id), "
        f"count(DISTINCT (document_id, chunk_index)) FROM knowledge_chunks_{_DIM} "
        "WHERE tenant_id = :t AND collection_id = :c",
        {"t": world.config.tenant_id, "c": world.config.collection_id},
    )
    return int(rows[0][0]), int(rows[0][1]), int(rows[0][2])


async def _wait(event: asyncio.Event, what: str, timeout: float = 60.0) -> None:
    try:
        await asyncio.wait_for(event.wait(), timeout)
    except TimeoutError:  # pragma: no cover - diagnostic
        pytest.fail(f"timed out waiting for {what}")


async def _wait_until_lock_expires(tracker: IngestionJobTracker, config: SourceConfig) -> None:
    for _ in range(int(_LOCK_TTL * 10) + 40):
        if await tracker.lock_holder(config.source_id, config.tenant_id) is None:
            return
        await asyncio.sleep(0.1)
    pytest.fail("the dead worker's lock never expired")


async def test_a_dead_workers_sync_is_requeued_and_resumes_exactly(world: _World) -> None:
    cfg = world.config
    upstream = _Upstream(cfg.source_id, cfg.tenant_id, freeze_after=_DIE_AFTER)
    first = _Worker(world, upstream)
    zombie = asyncio.ensure_future(
        first.run(source_id=cfg.source_id, tenant_id=cfg.tenant_id, triggered_by="scheduler")
    )
    try:
        await _wait(upstream.frozen, "the first worker to reach its death point")
        # Let the documents in flight finish (indexed past the checkpoint).
        for _ in range(100):
            chunks, _docs, _keys = await _chunk_census(world)
            if chunks >= _DIE_AFTER:
                break
            await asyncio.sleep(0.1)
        lease = first.leases[0]
        job_id = lease.job_id
        assert lease.token.startswith(f"{job_id}#")

        beat = world.tracker()
        store = world.source_store()
        queued: list[tuple[dict[str, Any], int]] = []

        def _enqueue(kwargs: dict[str, Any], countdown: int) -> None:
            queued.append((kwargs, countdown))

        # Alive: recovery leaves it alone (fresh heartbeat, lock renewed).
        report = await recover_orphaned_syncs(
            beat, source_store=store, settings=_settings(), enqueue=_enqueue
        )
        assert not report.requeued and not report.gave_up
        before = await _job(world, job_id)
        assert before["status"] == "running" and before["attempts"] == 1
        assert before["cursor_after"] == "99"  # checkpoint at 100 documents
        assert before["docs_indexed"] == 100

        # The worker dies: nothing renews its lock or beats its job any more.
        first.kill()
        await _wait_until_lock_expires(beat, cfg)
        await asyncio.sleep(2.2)  # heartbeat older than the 2 s stale window

        report = await recover_orphaned_syncs(
            beat, source_store=store, settings=_settings(), enqueue=_enqueue
        )
        assert [r["job_id"] for r in report.requeued] == [job_id], report.as_dict()
        (kwargs, countdown), = queued
        assert countdown == 30  # first requeue: the base backoff
        assert kwargs == {
            "source_id": cfg.source_id,
            "tenant_id": cfg.tenant_id,
            "triggered_by": "scheduler",
            "job_id": job_id,
            "reindex": False,
            "resume": True,
        }
        pending = await _job(world, job_id)
        assert pending["status"] == "pending" and pending["attempts"] == 2
        assert "worker lost" in pending["requeue_reason"]
        assert pending["lease_token"] == job_id  # the queued lock's token
        assert await beat.running_job_id(cfg.source_id, cfg.tenant_id) == job_id

        # A second recovery pass (or a second beat replica) does nothing more.
        again = await recover_orphaned_syncs(
            beat, source_store=store, settings=_settings(), enqueue=_enqueue
        )
        assert not again.requeued and len(queued) == 1

        # A new worker runs the requeued job: it resumes from the checkpoint.
        upstream.freeze_after = None
        second = _Worker(world, upstream)
        result = await second.run(**kwargs)
        assert result["job_id"] == job_id, result
        assert result["docs_failed"] == 0
        assert upstream.cursors_seen == [None, "99"]

        done = await _job(world, job_id)
        assert done["status"] == "completed", done
        assert done["attempts"] == 2
        assert "worker lost" in done["requeue_reason"]
        # Every document counted once across both attempts: 100 checkpointed by
        # the dead run, 150 read by the resumed one (60 of them already indexed
        # by the dead run past its checkpoint -> unchanged, skipped by dedup).
        assert done["docs_discovered"] == _DOCS
        assert done["docs_indexed"] + done["docs_skipped"] == _DOCS
        assert done["docs_skipped"] == _DIE_AFTER - 100
        chunks, docs, keys = await _chunk_census(world)
        assert docs == _DOCS
        assert chunks == keys == _DOCS  # no duplicate chunk anywhere
        stored = await world.source_store().get(cfg.source_id, cfg.tenant_id)
        assert stored is not None and stored.cursor_value == str(_DOCS - 1)
        assert stored.consecutive_failures == 0
        assert await beat.running_job_id(cfg.source_id, cfg.tenant_id) is None

        # The zombie wakes up: it can commit nothing and cannot touch the job.
        upstream.thaw.set()
        zombie_result = await asyncio.wait_for(zombie, 60)
        assert zombie_result.get("error") == "lock_lost", zombie_result
        after = await _job(world, job_id)
        assert after == done
        stored = await world.source_store().get(cfg.source_id, cfg.tenant_id)
        assert stored is not None and stored.cursor_value == str(_DOCS - 1)
        chunks, docs, keys = await _chunk_census(world)
        assert (chunks, docs, keys) == (_DOCS, _DOCS, _DOCS)
    finally:
        upstream.thaw.set()
        if not zombie.done():
            zombie.cancel()
        with contextlib.suppress(BaseException):
            await zombie


async def test_a_slow_but_alive_worker_is_never_requeued(world: _World) -> None:
    cfg = world.config
    upstream = _Upstream(cfg.source_id, cfg.tenant_id, freeze_after=30)
    worker = _Worker(world, upstream)
    run = asyncio.ensure_future(
        worker.run(source_id=cfg.source_id, tenant_id=cfg.tenant_id, triggered_by="scheduler")
    )
    try:
        await _wait(upstream.frozen, "the slow worker to stall")
        job_id = worker.leases[0].job_id
        beat = world.tracker()
        queued: list[Any] = []
        alive = 0
        # Two lock TTLs of a stalled-but-alive run, its heartbeat often older
        # than the (tiny) stale window: the renewed lock proves it alive.
        for _ in range(7):
            await asyncio.sleep(1.5)
            report = await recover_orphaned_syncs(
                beat,
                source_store=world.source_store(),
                settings=_settings(stale_seconds=0.5),
                enqueue=lambda kwargs, countdown: queued.append(kwargs),
            )
            alive += report.alive
            assert not report.requeued and not report.gave_up, report.as_dict()
        assert alive >= 1  # it was a candidate and judged alive
        assert not queued
        row = await _job(world, job_id)
        assert row["status"] == "running" and row["attempts"] == 1
        assert not worker.leases[0].lost

        upstream.thaw.set()
        result = await asyncio.wait_for(run, 60)
        assert result["job_id"] == job_id and result["docs_indexed"] == _DOCS, result
        done = await _job(world, job_id)
        assert done["status"] == "completed" and done["attempts"] == 1
        assert done["requeue_reason"] == ""
        chunks, docs, keys = await _chunk_census(world)
        assert (chunks, docs, keys) == (_DOCS, _DOCS, _DOCS)
    finally:
        upstream.thaw.set()
        if not run.done():
            run.cancel()
        with contextlib.suppress(BaseException):
            await run


async def test_spent_attempts_fail_the_job_with_the_reason_and_back_off_the_source(
    world: _World,
) -> None:
    cfg = world.config
    upstream = _Upstream(cfg.source_id, cfg.tenant_id, freeze_after=20)
    worker = _Worker(world, upstream)
    zombie = asyncio.ensure_future(
        worker.run(source_id=cfg.source_id, tenant_id=cfg.tenant_id, triggered_by="manual")
    )
    try:
        await _wait(upstream.frozen, "the worker to reach its death point")
        job_id = worker.leases[0].job_id
        worker.kill()
        beat = world.tracker()
        await _wait_until_lock_expires(beat, cfg)
        await asyncio.sleep(2.2)
        queued: list[Any] = []
        report = await recover_orphaned_syncs(
            beat,
            source_store=world.source_store(),
            settings=_settings(max_attempts=1),
            enqueue=lambda kwargs, countdown: queued.append(kwargs),
        )
        assert report.gave_up == [job_id] and not queued, report.as_dict()
        row = await _job(world, job_id)
        assert row["status"] == "failed" and row["attempts"] == 1
        assert "gave up after 1 attempt(s)" in row["error_message"]
        assert "worker lost" in row["error_message"]
        # Served by GET /sources/{id}/sync/history (never the lease token).
        history = await world.tracker().list_jobs(cfg.source_id, cfg.tenant_id)
        assert history[0]["job_id"] == job_id and history[0]["attempts"] == 1
        assert "lease_token" not in history[0]
        assert history[0]["heartbeat_at"]
        stored = await world.source_store().get(cfg.source_id, cfg.tenant_id)
        assert stored is not None and stored.consecutive_failures == 1
        # Nothing was requeued, so nothing holds the Source.
        assert await beat.lock_holder(cfg.source_id, cfg.tenant_id) is None
    finally:
        upstream.thaw.set()
        if not zombie.done():
            zombie.cancel()
        with contextlib.suppress(BaseException):
            await zombie


async def test_a_scheduled_rerun_supersedes_the_dead_job_and_resumes_it(world: _World) -> None:
    """Celery redelivered the dead scheduled sync (or the due-scan re-dispatched
    it) before orphan recovery ran: the new run supersedes the dead job,
    continues its attempt count and resumes from its checkpoint."""
    cfg = world.config
    upstream = _Upstream(cfg.source_id, cfg.tenant_id, freeze_after=_DIE_AFTER)
    first = _Worker(world, upstream)
    zombie = asyncio.ensure_future(
        first.run(source_id=cfg.source_id, tenant_id=cfg.tenant_id, triggered_by="scheduler")
    )
    try:
        await _wait(upstream.frozen, "the first worker to reach its death point")
        dead_job = first.leases[0].job_id
        first.kill()
        await _wait_until_lock_expires(world.tracker(), cfg)

        upstream.freeze_after = None
        result = await _Worker(world, upstream).run(
            source_id=cfg.source_id, tenant_id=cfg.tenant_id, triggered_by="scheduler"
        )
        new_job = result["job_id"]
        assert new_job != dead_job and result["docs_failed"] == 0, result
        assert upstream.cursors_seen == [None, "99"]
        old = await _job(world, dead_job)
        assert old["status"] == "failed" and new_job in old["error_message"]
        new = await _job(world, new_job)
        assert new["status"] == "completed" and new["attempts"] == 2
        assert dead_job in new["requeue_reason"]
        chunks, docs, keys = await _chunk_census(world)
        assert (chunks, docs, keys) == (_DOCS, _DOCS, _DOCS)
        # Nothing is left for orphan recovery.
        report = await recover_orphaned_syncs(
            world.tracker(), source_store=world.source_store(), settings=_settings(),
            enqueue=lambda kwargs, countdown: pytest.fail("nothing to requeue"),
        )
        assert report.scanned == 0
    finally:
        upstream.thaw.set()
        if not zombie.done():
            zombie.cancel()
        with contextlib.suppress(BaseException):
            await zombie
