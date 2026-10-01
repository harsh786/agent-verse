"""KB-15 worker side: cancel, reindex and operator DLQ retry in the scheduler."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.ingestion.job_tracker import IngestionJobTracker
from app.ingestion.scheduler import _retry_dlq_entry_async, _sync_source_async
from app.ingestion.source_config import PipelineResult, RawDocument, SourceConfig, SourceFamily


def _config(**overrides: Any) -> SourceConfig:
    values: dict[str, Any] = {
        "source_id": "src-1",
        "tenant_id": "t1",
        "name": "s",
        "family": SourceFamily.WEB,
        "source_type": "http",
        "collection_id": "col-1",
        "cursor_value": "old-cursor",
    }
    values.update(overrides)
    return SourceConfig(**values)


def _doc(i: int) -> RawDocument:
    return RawDocument(
        doc_id=f"d{i}", source_id="src-1", tenant_id="t1", content=b"x", content_type="text/plain"
    )


class _Connector:
    seen_cursor: Any = "unset"
    on_doc: Any = None

    async def get_delta(self, config: Any, cursor: Any) -> Any:
        type(self).seen_cursor = cursor
        for i in range(4):
            if type(self).on_doc is not None:
                await type(self).on_doc(i)
            yield _doc(i), f"c{i}"


def _pipeline(ingested: list[str]) -> Any:
    pipeline = MagicMock()

    async def _ingest(raw_doc: RawDocument, config: Any) -> PipelineResult:
        ingested.append(raw_doc.doc_id)
        return PipelineResult(
            doc_id=raw_doc.doc_id, source_id="src-1", tenant_id="t1", status="indexed"
        )

    pipeline.ingest = _ingest
    return pipeline


async def _run(tracker: Any, pipeline: Any, store: Any, **kwargs: Any) -> dict:
    with (
        patch(
            "app.ingestion.scheduler._build_worker_ingestion",
            return_value=(tracker, pipeline, store),
        ),
        patch("app.ingestion.connector_registry.load_all_connectors"),
        patch("app.ingestion.connector_registry.get_connector", return_value=_Connector),
    ):
        return await _sync_source_async(
            task=MagicMock(), source_id="src-1", tenant_id="t1", **kwargs
        )


async def test_cancel_request_stops_the_worker_sync_and_keeps_progress() -> None:
    tracker = IngestionJobTracker()
    job_id = await tracker.acquire_lock("src-1", "t1")
    store = AsyncMock()
    store.get.return_value = _config()
    ingested: list[str] = []

    async def _cancel_at(i: int) -> None:
        if i == 2:
            await tracker.request_cancel("src-1", "t1")

    _Connector.on_doc = _cancel_at
    try:
        result = await _run(
            tracker, _pipeline(ingested), store, triggered_by="manual", job_id=job_id
        )
    finally:
        _Connector.on_doc = None
    assert ingested == ["d0", "d1"]
    assert result["cancelled"] is True
    job = tracker.get_job(str(job_id))
    assert job is not None and job.status == "cancelled"
    store.mark_synced.assert_awaited()
    assert await tracker.running_job_id("src-1", "t1") is None  # lock released


async def test_reindex_deletes_the_sources_documents_and_syncs_from_scratch() -> None:
    tracker = IngestionJobTracker()
    job_id = await tracker.acquire_lock("src-1", "t1")
    store = AsyncMock()
    store.get.return_value = _config()
    ingested: list[str] = []
    pipeline = _pipeline(ingested)
    kb = MagicMock()
    pages = [[{"id": "doc-a"}, {"id": "doc-b"}], []]
    kb.list_source_documents_async = AsyncMock(side_effect=lambda **_: pages.pop(0))
    kb.delete_document_async = AsyncMock(return_value=1)
    pipeline._kb = kb

    await _run(tracker, pipeline, store, triggered_by="reindex", job_id=job_id, reindex=True)

    deleted = [c.args[0] for c in kb.delete_document_async.await_args_list]
    assert deleted == ["doc-a", "doc-b"]
    for call in kb.delete_document_async.await_args_list:
        assert call.kwargs["collection_id"] == "col-1"
        assert call.kwargs["tenant_ctx"].tenant_id == "t1"
    assert kb.list_source_documents_async.await_args_list[0].kwargs["source_id"] == "src-1"
    assert _Connector.seen_cursor is None  # full re-sync, not from the old cursor
    store.update.assert_any_await("src-1", "t1", cursor_value="")
    assert ingested == ["d0", "d1", "d2", "d3"]


async def test_operator_retry_replays_an_entry_past_the_retry_cap() -> None:
    tracker = MagicMock()
    tracker.get_dlq_entry = AsyncMock(
        return_value={
            "dlq_id": "q1",
            "tenant_id": "t1",
            "source_id": "src-1",
            "doc_id": "d9",
            "retry_count": 99,
            "resolved_at": None,
            "raw_doc_json": '{"doc_id": "d9", "content": "hello", "content_type": "text/plain"}',
        }
    )
    tracker.resolve_dlq_entry = AsyncMock()
    tracker.mark_dlq_permanent_failure = AsyncMock()
    pipeline = MagicMock()
    pipeline.run = AsyncMock(return_value=MagicMock(success=True, skipped=False))
    store = AsyncMock()
    store.get.return_value = _config()
    with patch(
        "app.ingestion.scheduler._build_worker_ingestion", return_value=(tracker, pipeline, store)
    ):
        result = await _retry_dlq_entry_async(dlq_id="q1", tenant_id="t1")
    assert result == {"dlq_id": "q1", "outcome": "succeeded"}
    tracker.resolve_dlq_entry.assert_awaited_once_with("q1", "t1")
    tracker.mark_dlq_permanent_failure.assert_not_called()
    tracker.get_dlq_entry.assert_awaited_once_with("q1", "t1")


async def test_operator_retry_of_a_resolved_entry_does_nothing() -> None:
    tracker = MagicMock()
    tracker.get_dlq_entry = AsyncMock(return_value={"dlq_id": "q1", "resolved_at": "x"})
    pipeline = MagicMock()
    pipeline.run = AsyncMock()
    with patch(
        "app.ingestion.scheduler._build_worker_ingestion",
        return_value=(tracker, pipeline, AsyncMock()),
    ):
        result = await _retry_dlq_entry_async(dlq_id="q1", tenant_id="t1")
    assert result["outcome"] == "already_resolved"
    pipeline.run.assert_not_called()
