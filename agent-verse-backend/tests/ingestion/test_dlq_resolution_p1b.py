"""P1b-4: a failed object is recoverable once its cause is fixed.

Live (MinIO, 2026-10-05): an object the connector user could not read
(AccessDenied) and a corrupt PDF both went to the DLQ. After access was
restored, ``POST /ingestion/dlq/{id}/retry`` marked the entry permanent without
fetching the object again; after the PDF was replaced upstream the next sync
indexed it but its DLQ entry stayed open (and would have been replayed with the
stale bytes).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from botocore.exceptions import ClientError

from app.ingestion.job_tracker import IngestionJobTracker
from app.ingestion.scheduler import _retry_one_dlq_entry, _sync_source_async
from app.ingestion.source_config import (
    PipelineResult,
    RawDocument,
    SourceConfig,
    SourceFamily,
    configuration_problem,
)
from tests.ingestion.test_s3_dlq_replay import (
    _config,
    _failed_webhook_doc,
    _ok,
    _Pipeline,
    _store,
    _tracker,
    dlq_entry_for,
)


def _denied() -> ClientError:
    return ClientError(
        {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 403}},
        "GetObject",
    )


async def test_operator_retry_refetches_a_permanently_failed_object() -> None:
    doc = await _failed_webhook_doc(_denied())
    pipeline, tracker = _Pipeline(), _tracker()
    s3 = MagicMock()
    s3.get_object.return_value = _ok(b"readable now")
    with patch("boto3.Session", return_value=MagicMock(client=MagicMock(return_value=s3))):
        outcome = await _retry_one_dlq_entry(
            dlq_entry_for(doc), tracker, pipeline, _store(_config()), force=True
        )
    assert outcome == "succeeded"
    assert [d.content for d in pipeline.docs] == [b"readable now"]
    tracker.resolve_dlq_entry.assert_awaited_once()
    tracker.mark_dlq_permanent_failure.assert_not_called()


async def test_the_automatic_retry_still_gives_up_on_a_permanent_failure() -> None:
    doc = await _failed_webhook_doc(_denied())
    pipeline, tracker = _Pipeline(), _tracker()
    with patch("boto3.Session") as client:
        outcome = await _retry_one_dlq_entry(dlq_entry_for(doc), tracker, pipeline,
                                             _store(_config()))
    assert outcome == "permanent"
    client.assert_not_called()


class _Docs:
    def __init__(self) -> None:
        self.completed_cursor = None

    async def get_delta(self, config: SourceConfig, cursor: str | None) -> Any:
        for name in ("fixed.pdf", "bad.pdf"):
            yield RawDocument(doc_id=f"s3://b/{name}", source_id="src-1", tenant_id="t1",
                              content=b"x", content_type="text/plain"), name


async def test_a_sync_resolves_open_dlq_entries_of_documents_it_indexed() -> None:
    tracker = IngestionJobTracker()
    tracker.resolve_dlq_for_documents = AsyncMock()  # type: ignore[method-assign]
    tracker.add_to_dlq = AsyncMock()  # type: ignore[method-assign]
    job_id = await tracker.acquire_lock("src-1", "t1")
    store = AsyncMock()
    store.get.return_value = SourceConfig(source_id="src-1", tenant_id="t1", name="s",
                                          family=SourceFamily.OBJECT_STORAGE, source_type="s3",
                                          collection_id="c")
    pipeline = AsyncMock()

    async def _ingest(raw: RawDocument, config: Any) -> PipelineResult:
        ok = raw.doc_id.endswith("fixed.pdf")
        return PipelineResult(doc_id=raw.doc_id, source_id="src-1", tenant_id="t1",
                              status="indexed" if ok else "failed", error="" if ok else "bad")

    pipeline.ingest = _ingest
    with (
        patch("app.ingestion.scheduler._build_worker_ingestion",
              return_value=(tracker, pipeline, store)),
        patch("app.ingestion.connector_registry.load_all_connectors"),
        patch("app.ingestion.connector_registry.get_connector", return_value=_Docs),
        patch("app.ingestion.scheduler._schedule_reconcile_if_due", AsyncMock()),
    ):
        await _sync_source_async(task=MagicMock(), source_id="src-1", tenant_id="t1",
                                 triggered_by="manual", job_id=job_id)
    tracker.resolve_dlq_for_documents.assert_awaited_once_with("src-1", "t1", ["s3://b/fixed.pdf"])
    job = tracker.get_job(str(job_id))
    assert job is not None and job.docs_discovered == 2


def test_a_minio_source_without_an_endpoint_needs_configuration() -> None:
    cfg = SourceConfig(source_id="s", tenant_id="t", name="n", family=SourceFamily.OBJECT_STORAGE,
                       source_type="minio", collection_id="c", connection_config={"bucket": "b"})
    assert "endpoint" in str(configuration_problem(cfg))
    cfg.connection_config["endpoint_url"] = "http://minio:9000"
    assert configuration_problem(cfg) is None
