"""P1b-3: the sync commits a connector's ``completed_cursor`` only for a finished run."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.job_tracker import IngestionJobTracker
from app.ingestion.scheduler import _sync_source_async
from app.ingestion.source_config import PipelineResult, RawDocument, SourceConfig, SourceFamily


class _Listing:
    cancel_after: int | None = None
    tracker: Any = None

    def __init__(self) -> None:
        self.completed_cursor: str | None = None

    async def get_delta(self, config: SourceConfig, cursor: str | None) -> Any:
        for i in range(3):
            if self.cancel_after is not None and i == self.cancel_after:
                await self.tracker.request_cancel("src-1", "t1")
            yield RawDocument(doc_id=f"d{i}", source_id="src-1", tenant_id="t1", content=b"x",
                              content_type="text/plain"), f"after:d{i}"
        self.completed_cursor = "watermark"


async def _sync(cancel_after: int | None) -> str:
    tracker = IngestionJobTracker()
    job_id = await tracker.acquire_lock("src-1", "t1")
    store = AsyncMock()
    store.get.return_value = SourceConfig(source_id="src-1", tenant_id="t1", name="s",
                                          family=SourceFamily.OBJECT_STORAGE, source_type="s3",
                                          collection_id="c")
    pipeline = AsyncMock()
    pipeline.ingest.return_value = PipelineResult(doc_id="d", source_id="src-1", tenant_id="t1",
                                                  status="indexed")
    _Listing.cancel_after, _Listing.tracker = cancel_after, tracker
    with (
        patch("app.ingestion.scheduler._build_worker_ingestion",
              return_value=(tracker, pipeline, store)),
        patch("app.ingestion.scheduler._shared_redis", return_value=None),
        patch("app.ingestion.connector_registry.load_all_connectors"),
        patch("app.ingestion.connector_registry.get_connector", return_value=_Listing),
        patch("app.ingestion.scheduler._schedule_reconcile_if_due", AsyncMock()),
    ):
        await _sync_source_async(task=MagicMock(), source_id="src-1", tenant_id="t1",
                                 triggered_by="manual", job_id=job_id)
    job = tracker.get_job(str(job_id))
    assert job is not None, "no job recorded"
    return job.cursor_after


@pytest.mark.asyncio
async def test_a_finished_run_commits_the_watermark() -> None:
    assert await _sync(None) == "watermark"


@pytest.mark.asyncio
async def test_a_cancelled_run_keeps_its_resume_position() -> None:
    assert await _sync(2) == "after:d1"


@pytest.mark.asyncio
async def test_an_operator_sync_of_a_failing_source_is_not_backed_off() -> None:
    """P1b-5: the API answered "queued" with a job id; the worker skipped it for
    backoff without any job record, so the caller waited on a job that never was."""
    import datetime

    tracker = IngestionJobTracker()
    store = AsyncMock()
    failing = SourceConfig(source_id="src-1", tenant_id="t1", name="s",
                           family=SourceFamily.OBJECT_STORAGE, source_type="s3",
                           collection_id="c", consecutive_failures=2,
                           last_synced_at=datetime.datetime.now(datetime.UTC).isoformat())
    store.get.return_value = failing
    pipeline = AsyncMock()
    pipeline.ingest.return_value = PipelineResult(doc_id="d", source_id="src-1", tenant_id="t1",
                                                  status="indexed")
    _Listing.cancel_after = None
    results = {}
    for trigger in ("manual", "scheduler"):
        job_id = await tracker.acquire_lock("src-1", "t1") if trigger == "manual" else None
        with (
            patch("app.ingestion.scheduler._build_worker_ingestion",
                  return_value=(tracker, pipeline, store)),
            patch("app.ingestion.scheduler._shared_redis", return_value=None),
            patch("app.ingestion.connector_registry.load_all_connectors"),
            patch("app.ingestion.connector_registry.get_connector", return_value=_Listing),
            patch("app.ingestion.scheduler._schedule_reconcile_if_due", AsyncMock()),
        ):
            results[trigger] = await _sync_source_async(
                task=MagicMock(), source_id="src-1", tenant_id="t1", triggered_by=trigger,
                job_id=job_id)
    assert results["manual"].get("docs_indexed") == 3
    assert results["scheduler"].get("reason") == "backoff"
