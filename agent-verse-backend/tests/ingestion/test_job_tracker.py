"""Comprehensive unit coverage for app/ingestion/job_tracker.IngestionJobTracker.

Covers the in-memory and DB-backed paths for: distributed locking, job
lifecycle (create/update-cursor/increment-counters/complete), the scheduler-
support methods (load_config, get_due_sources, failure counters, DLQ helpers),
and their exception-swallowing behaviour when the DB is present but the query
fails. ``add_to_dlq`` serialization is already covered by
``test_dlq_payload_serialization.py`` — not duplicated here.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.ingestion.job_tracker import IngestionJobTracker
from app.ingestion.source_config import SourceConfig, SourceFamily


def _config(**overrides: Any) -> SourceConfig:
    base = dict(
        source_id="src-1",
        tenant_id="t1",
        name="Test Source",
        family=SourceFamily.WEB,
        source_type="http",
        enabled=True,
        sync_mode="incremental",
        connection_config={},
        cursor_value="",
    )
    base.update(overrides)
    return SourceConfig(**base)


class _Mappings(list):
    """List that also supports SQLAlchemy's ``.first()`` mapping accessor."""

    def first(self) -> Any:
        return self[0] if self else None

    def all(self) -> list:
        return list(self)


class _Result:
    def __init__(self, *, mappings_first: Any = None, mappings_all: list | None = None, rows: list | None = None):
        self._mappings_first = mappings_first
        self._mappings_all = mappings_all or []
        self._rows = rows or []

    def mappings(self) -> Any:
        if self._mappings_first is not None:
            return _Mappings([self._mappings_first])
        return _Mappings(self._mappings_all)

    def __iter__(self):
        return iter(self._rows)


def _db_ok(execute_result: Any = None, execute_side_effect: list | None = None) -> Any:
    """A working session factory: async context manager yielding a session."""
    session = AsyncMock()
    if execute_side_effect is not None:
        session.execute = AsyncMock(side_effect=execute_side_effect)
    else:
        session.execute = AsyncMock(return_value=execute_result or _Result())

    begin = AsyncMock()
    begin.__aenter__ = AsyncMock(return_value=None)
    begin.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin)

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)

    def _factory() -> Any:
        return cm

    return _factory


def _db_raises() -> Any:
    """A session factory whose __call__ raises immediately (simulates any DB failure)."""

    def _factory() -> Any:
        raise RuntimeError("db unavailable")

    return _factory


# ── Distributed locking ───────────────────────────────────────────────────────


async def test_acquire_lock_in_memory_when_no_redis() -> None:
    tracker = IngestionJobTracker()
    job_id = await tracker.acquire_lock("src-1", "t1")
    assert job_id is not None


async def test_acquire_lock_in_memory_blocks_second_holder() -> None:
    tracker = IngestionJobTracker()
    first = await tracker.acquire_lock("src-1", "t1")
    assert first is not None
    second = await tracker.acquire_lock("src-1", "t1")
    assert second is None


async def test_release_lock_in_memory_allows_reacquire() -> None:
    tracker = IngestionJobTracker()
    job_id = await tracker.acquire_lock("src-1", "t1")
    await tracker.release_lock("src-1", "t1", job_id)
    again = await tracker.acquire_lock("src-1", "t1")
    assert again is not None


async def test_release_lock_in_memory_wrong_job_id_noop() -> None:
    tracker = IngestionJobTracker()
    job_id = await tracker.acquire_lock("src-1", "t1")
    await tracker.release_lock("src-1", "t1", "not-the-holder")
    # Lock still held by original job_id — reacquire fails.
    assert await tracker.acquire_lock("src-1", "t1") is None


async def test_release_lock_in_memory_without_job_id_forces_release() -> None:
    tracker = IngestionJobTracker()
    await tracker.acquire_lock("src-1", "t1")
    await tracker.release_lock("src-1", "t1")
    assert await tracker.acquire_lock("src-1", "t1") is not None


async def test_acquire_lock_redis_success() -> None:
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    tracker = IngestionJobTracker(redis=redis)
    job_id = await tracker.acquire_lock("src-1", "t1")
    assert job_id is not None
    redis.set.assert_awaited_once()


async def test_acquire_lock_redis_already_held() -> None:
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=False)
    redis.get = AsyncMock(return_value=b"other-job")
    tracker = IngestionJobTracker(redis=redis)
    job_id = await tracker.acquire_lock("src-1", "t1")
    assert job_id is None


async def test_acquire_lock_redis_error_falls_back_to_in_memory() -> None:
    redis = AsyncMock()
    redis.set = AsyncMock(side_effect=RuntimeError("redis down"))
    tracker = IngestionJobTracker(redis=redis)
    job_id = await tracker.acquire_lock("src-1", "t1")
    assert job_id is not None


async def test_release_lock_redis_matching_job_id_deletes() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=b"job-123")
    redis.delete = AsyncMock()
    tracker = IngestionJobTracker(redis=redis)
    await tracker.release_lock("src-1", "t1", "job-123")
    redis.delete.assert_awaited_once()


async def test_release_lock_redis_non_matching_job_id_skips_delete() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=b"someone-else")
    redis.delete = AsyncMock()
    tracker = IngestionJobTracker(redis=redis)
    await tracker.release_lock("src-1", "t1", "job-123")
    redis.delete.assert_not_awaited()


async def test_release_lock_redis_without_job_id_deletes_unconditionally() -> None:
    redis = AsyncMock()
    redis.delete = AsyncMock()
    tracker = IngestionJobTracker(redis=redis)
    await tracker.release_lock("src-1", "t1")
    redis.delete.assert_awaited_once()


async def test_release_lock_redis_error_is_swallowed() -> None:
    redis = AsyncMock()
    redis.delete = AsyncMock(side_effect=RuntimeError("boom"))
    tracker = IngestionJobTracker(redis=redis)
    await tracker.release_lock("src-1", "t1")  # must not raise


# ── Job lifecycle ─────────────────────────────────────────────────────────────


async def test_create_job_in_memory_only() -> None:
    tracker = IngestionJobTracker()
    cfg = _config(cursor_value="cursor-0")
    job = await tracker.create_job(cfg, job_id="job-1")
    assert job.job_id == "job-1"
    assert job.status == "running"
    assert job.cursor_before == "cursor-0"
    assert job.triggered_by == "scheduler"
    assert tracker.get_job("job-1") is job


async def test_create_job_persists_when_db_present() -> None:
    factory = _db_ok()
    tracker = IngestionJobTracker(db=factory)
    cfg = _config()
    job = await tracker.create_job(cfg, job_id="job-2", triggered_by="webhook")
    assert job.triggered_by == "webhook"


async def test_create_job_db_persist_error_is_swallowed() -> None:
    tracker = IngestionJobTracker(db=_db_raises())
    cfg = _config()
    job = await tracker.create_job(cfg, job_id="job-3")
    assert job.status == "running"  # still created in-memory


async def test_update_cursor_updates_job_and_config() -> None:
    tracker = IngestionJobTracker()
    cfg = _config()
    job = await tracker.create_job(cfg, job_id="job-1")
    await tracker.update_cursor(job, "cursor-1", cfg)
    assert job.cursor_after == "cursor-1"
    assert cfg.cursor_value == "cursor-1"


async def test_update_cursor_persists_when_db_present() -> None:
    factory = _db_ok()
    tracker = IngestionJobTracker(db=factory)
    cfg = _config()
    job = await tracker.create_job(cfg, job_id="job-1")
    await tracker.update_cursor(job, "cursor-9", cfg)
    assert job.cursor_after == "cursor-9"


async def test_update_cursor_db_error_is_swallowed() -> None:
    tracker = IngestionJobTracker(db=_db_raises())
    cfg = _config()
    job = await tracker.create_job(cfg, job_id="job-1")
    await tracker.update_cursor(job, "cursor-9", cfg)  # must not raise


async def test_increment_counters_accumulates() -> None:
    tracker = IngestionJobTracker()
    cfg = _config()
    job = await tracker.create_job(cfg, job_id="job-1")
    await tracker.increment_counters(job, indexed=3, skipped=1, failed=2, chunks=5, bytes_=100, tokens=50)
    await tracker.increment_counters(job, indexed=1)
    assert job.docs_indexed == 4
    assert job.docs_skipped == 1
    assert job.docs_failed == 2
    assert job.chunks_created == 5
    assert job.bytes_processed == 100
    assert job.tokens_consumed == 50
    # docs_discovered accumulates indexed+skipped+failed for each call.
    assert job.docs_discovered == 6 + 1


async def test_complete_job_success_sets_completed_status() -> None:
    tracker = IngestionJobTracker()
    cfg = _config()
    job = await tracker.create_job(cfg, job_id="job-1")
    await tracker.complete_job(job)
    assert job.status == "completed"
    assert job.error_message == ""
    assert job.completed_at


async def test_complete_job_with_error_sets_failed_status_and_truncates_message() -> None:
    tracker = IngestionJobTracker()
    cfg = _config()
    job = await tracker.create_job(cfg, job_id="job-1")
    long_error = "x" * 3000
    await tracker.complete_job(job, error=long_error)
    assert job.status == "failed"
    assert len(job.error_message) == 2048


async def test_complete_job_persists_when_db_present() -> None:
    factory = _db_ok()
    tracker = IngestionJobTracker(db=factory)
    cfg = _config()
    job = await tracker.create_job(cfg, job_id="job-1")
    await tracker.complete_job(job)


async def test_complete_job_db_error_is_swallowed() -> None:
    tracker = IngestionJobTracker(db=_db_raises())
    cfg = _config()
    job = await tracker.create_job(cfg, job_id="job-1")
    await tracker.complete_job(job)  # must not raise


def test_get_job_returns_none_when_missing() -> None:
    tracker = IngestionJobTracker()
    assert tracker.get_job("missing") is None


async def test_list_jobs_for_source_filters_by_source_id() -> None:
    tracker = IngestionJobTracker()
    cfg_a = _config(source_id="a")
    cfg_b = _config(source_id="b")
    job_a1 = await tracker.create_job(cfg_a, job_id="a1")
    job_a2 = await tracker.create_job(cfg_a, job_id="a2")
    await tracker.create_job(cfg_b, job_id="b1")
    jobs = tracker.list_jobs_for_source("a")
    assert {j.job_id for j in jobs} == {job_a1.job_id, job_a2.job_id}


# ── load_config ───────────────────────────────────────────────────────────────


async def test_load_config_uses_in_memory_job_when_present() -> None:
    tracker = IngestionJobTracker()
    cfg = _config(source_id="src-1")
    await tracker.create_job(cfg, job_id="src-1")
    tracker._source_cursors["src-1"] = "cur-x"
    loaded = await tracker.load_config("src-1", "t1")
    assert loaded is not None
    assert loaded.cursor_value == "cur-x"


async def test_load_config_falls_back_to_db_when_not_in_memory() -> None:
    row = {
        "source_id": "src-2",
        "tenant_id": "t1",
        "name": "DB Source",
        "family": SourceFamily.WEB,
        "source_type": "http",
        "enabled": True,
        "sync_mode": "incremental",
        "connection_config": {},
    }
    factory = _db_ok(execute_result=_Result(mappings_first=row))
    tracker = IngestionJobTracker(db=factory)
    loaded = await tracker.load_config("src-2", "t1")
    assert loaded is not None
    assert loaded.source_id == "src-2"


async def test_load_config_returns_none_when_db_empty() -> None:
    factory = _db_ok(execute_result=_Result(mappings_first=None))
    tracker = IngestionJobTracker(db=factory)
    loaded = await tracker.load_config("src-3", "t1")
    assert loaded is None


async def test_load_config_returns_none_when_no_db_and_not_in_memory() -> None:
    tracker = IngestionJobTracker()
    loaded = await tracker.load_config("src-nonexistent", "t1")
    assert loaded is None


async def test_load_config_db_error_returns_none() -> None:
    tracker = IngestionJobTracker(db=_db_raises())
    loaded = await tracker.load_config("src-4", "t1")
    assert loaded is None


# ── get_due_sources ────────────────────────────────────────────────────────────


async def test_get_due_sources_returns_tuples() -> None:
    class _Row:
        def __init__(self, source_id, tenant_id):
            self.source_id = source_id
            self.tenant_id = tenant_id

    rows = [_Row("s1", "t1"), _Row("s2", "t1")]
    factory = _db_ok(execute_result=_Result(rows=rows))
    tracker = IngestionJobTracker(db=factory)
    due = await tracker.get_due_sources()
    assert due == [("s1", "t1"), ("s2", "t1")]


async def test_get_due_sources_db_error_returns_empty_list() -> None:
    tracker = IngestionJobTracker(db=_db_raises())
    due = await tracker.get_due_sources()
    assert due == []


# ── failure counters ──────────────────────────────────────────────────────────


async def test_increment_failure_counter_succeeds() -> None:
    factory = _db_ok()
    tracker = IngestionJobTracker(db=factory)
    await tracker.increment_failure_counter("src-1", "t1")  # must not raise


async def test_increment_failure_counter_db_error_is_swallowed() -> None:
    tracker = IngestionJobTracker(db=_db_raises())
    await tracker.increment_failure_counter("src-1", "t1")  # must not raise


async def test_reset_failure_counter_succeeds() -> None:
    factory = _db_ok()
    tracker = IngestionJobTracker(db=factory)
    await tracker.reset_failure_counter("src-1", "t1")


async def test_reset_failure_counter_db_error_is_swallowed() -> None:
    tracker = IngestionJobTracker(db=_db_raises())
    await tracker.reset_failure_counter("src-1", "t1")  # must not raise


# ── DLQ retry helpers ──────────────────────────────────────────────────────────


async def test_get_retryable_dlq_entries_returns_mappings() -> None:
    entries = [{"dlq_id": "d1"}, {"dlq_id": "d2"}]
    factory = _db_ok(execute_result=_Result(mappings_all=entries))
    tracker = IngestionJobTracker(db=factory)
    result = await tracker.get_retryable_dlq_entries(max_entries=10)
    assert result == entries


async def test_get_retryable_dlq_entries_db_error_returns_empty_list() -> None:
    tracker = IngestionJobTracker(db=_db_raises())
    result = await tracker.get_retryable_dlq_entries()
    assert result == []


async def test_resolve_dlq_entry_succeeds() -> None:
    factory = _db_ok()
    tracker = IngestionJobTracker(db=factory)
    await tracker.resolve_dlq_entry("d1")


async def test_resolve_dlq_entry_db_error_is_swallowed() -> None:
    tracker = IngestionJobTracker(db=_db_raises())
    await tracker.resolve_dlq_entry("d1")  # must not raise


async def test_increment_dlq_retry_succeeds() -> None:
    factory = _db_ok()
    tracker = IngestionJobTracker(db=factory)
    await tracker.increment_dlq_retry("d1", error="retry failed")


async def test_increment_dlq_retry_db_error_is_swallowed() -> None:
    tracker = IngestionJobTracker(db=_db_raises())
    await tracker.increment_dlq_retry("d1")  # must not raise


async def test_mark_dlq_permanent_failure_succeeds() -> None:
    factory = _db_ok()
    tracker = IngestionJobTracker(db=factory)
    await tracker.mark_dlq_permanent_failure("d1")


async def test_mark_dlq_permanent_failure_db_error_is_swallowed() -> None:
    tracker = IngestionJobTracker(db=_db_raises())
    await tracker.mark_dlq_permanent_failure("d1")  # must not raise
