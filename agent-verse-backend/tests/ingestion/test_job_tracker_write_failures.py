"""NF-12: a failed job/cursor write is never reported as success.

``IngestionJobTracker._persist_job_created`` / ``_persist_cursor_update`` /
``_persist_job_completed`` logged DB errors at WARNING and returned, so a job row
that was never written, or a cursor that was never committed, looked recorded —
and ``update_cursor`` advanced the in-memory cursor regardless.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.job_tracker import IngestionJobTracker, IngestionPersistenceError
from app.ingestion.source_config import IngestionJob, RawDocument, SourceConfig, SourceFamily
from tests.ingestion._lease import install_lease


class _DownSession:
    async def __aenter__(self) -> Any:
        raise ConnectionRefusedError("postgres down")

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _down_db() -> Any:
    return MagicMock(side_effect=lambda: _DownSession())


def _source(**kw: Any) -> SourceConfig:
    return SourceConfig(
        source_id=uuid.uuid4().hex,
        tenant_id="t-nf12",
        name="src",
        family=SourceFamily.WEB,
        source_type="http",
        collection_id="col",
        cursor_value="cur-0",
        **kw,
    )


def _job(source: SourceConfig) -> IngestionJob:
    return IngestionJob(
        job_id="job-1",
        source_id=source.source_id,
        tenant_id=source.tenant_id,
        status="running",
        sync_mode="incremental",
    )


async def test_create_job_raises_when_the_row_cannot_be_written() -> None:
    tracker = IngestionJobTracker(db=_down_db())
    source = _source()

    with pytest.raises(IngestionPersistenceError, match="could not be recorded"):
        await tracker.create_job(source, job_id="job-x")

    assert tracker.get_job("job-x") is None


async def test_cursor_never_advances_when_its_write_fails() -> None:
    tracker = IngestionJobTracker(db=_down_db())
    source = _source()
    job = _job(source)
    job.cursor_after = "cur-0"

    with pytest.raises(IngestionPersistenceError, match="cursor"):
        await tracker.update_cursor(job, "cur-1", source, fence=3)

    assert source.cursor_value == "cur-0"
    assert job.cursor_after == "cur-0"


async def test_complete_job_raises_and_marks_the_job_failed() -> None:
    tracker = IngestionJobTracker(db=_down_db())
    source = _source()
    job = _job(source)
    job.docs_indexed = 5

    with pytest.raises(IngestionPersistenceError, match="could not be recorded"):
        await tracker.complete_job(job)

    assert job.status == "failed"
    assert "could not be recorded" in job.error_message


async def test_without_db_the_in_memory_path_is_unchanged() -> None:
    tracker = IngestionJobTracker()
    source = _source()
    job = await tracker.create_job(source, job_id="job-mem")
    await tracker.update_cursor(job, "cur-9", source)
    await tracker.complete_job(job)
    assert source.cursor_value == "cur-9"
    assert job.status == "completed"


# ── the worker sync loop ──────────────────────────────────────────────────────


class _HundredDocs:
    async def get_delta(self, config: Any, cursor: Any):  # type: ignore[no-untyped-def]
        for i in range(100):
            yield RawDocument(
                doc_id=f"d{i}", source_id=config.source_id, tenant_id=config.tenant_id,
                content=b"x", content_type="text/plain",
            ), f"c{i}"


async def test_worker_does_not_swallow_a_failed_cursor_commit_as_a_doc_failure() -> None:
    from app.ingestion.scheduler import _sync_source_async

    source = _source()
    tracker = install_lease(AsyncMock())
    tracker.create_job = AsyncMock(return_value=_job(source))
    tracker.is_cancel_requested = AsyncMock(return_value=False)
    tracker.update_cursor = AsyncMock(
        side_effect=IngestionPersistenceError("cursor of source s could not be saved")
    )
    pipeline = AsyncMock()
    pipeline.ingest = AsyncMock(
        return_value=MagicMock(status="indexed", tokens_consumed=0, chunks_created=1)
    )
    source_store = AsyncMock()
    source_store.get = AsyncMock(return_value=source)
    task = MagicMock()
    task.retry = MagicMock(return_value=RuntimeError("RETRY"))

    with (
        patch(
            "app.ingestion.scheduler._build_worker_ingestion",
            return_value=(tracker, pipeline, source_store),
        ),
        patch(
            "app.ingestion.connector_registry.get_connector", return_value=_HundredDocs
        ),
        pytest.raises(RuntimeError, match="RETRY"),
    ):
        await _sync_source_async(
            task=task,
            source_id=source.source_id,
            tenant_id=source.tenant_id,
            triggered_by="manual",
            job_id="job-1",
        )

    # The failed commit stopped the run at doc 100: it is not a document failure.
    tracker.add_to_dlq.assert_not_awaited()
    tracker.update_cursor.assert_awaited_once()
    error = tracker.complete_job.await_args.kwargs["error"]
    assert "could not be saved" in error
    # The durable source cursor was never advanced past the failed write.
    for call in source_store.update.await_args_list:
        assert "cursor_value" not in call.kwargs
    tracker.release_lock.assert_awaited_once()
