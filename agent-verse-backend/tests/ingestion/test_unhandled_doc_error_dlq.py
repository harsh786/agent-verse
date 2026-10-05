"""USR-4: a document whose processing raises unexpectedly is queued for retry.

The sync loop counted such a document as failed and moved on — it never reached
the DLQ, so it was lost until the next manual sync.
"""

from __future__ import annotations

import contextlib
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.ingestion.job_tracker import IngestionJobTracker
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily

pytestmark = pytest.mark.asyncio


class _ExplodingPipeline:
    async def ingest(self, raw_doc: Any, cfg: Any) -> Any:
        raise RuntimeError("embedder exploded")


async def test_unhandled_pipeline_error_goes_to_the_dlq() -> None:
    from app.ingestion.scheduler import _sync_source_async

    config = SourceConfig(
        source_id="src-x", tenant_id="t-x", name="n", family=SourceFamily.WEB,
        source_type="http", connection_config={"url": "https://93.184.216.34/x"},
        collection_id="col",
    )
    doc = RawDocument(doc_id="d-1", source_id="src-x", tenant_id="t-x", content=b"x",
                      content_type="text/plain")

    class _Connector:
        async def get_delta(self, cfg: Any, cursor: Any) -> Any:
            yield doc, "c1"

    tracker = IngestionJobTracker()
    tracker.add_to_dlq = AsyncMock()  # type: ignore[method-assign]
    store = AsyncMock()
    store.get = AsyncMock(return_value=config)
    with (
        patch("app.ingestion.scheduler._build_worker_ingestion",
              return_value=(tracker, _ExplodingPipeline(), store)),
        patch("app.ingestion.connector_registry.get_connector", return_value=_Connector),
        contextlib.suppress(Exception),
    ):
        result = await _sync_source_async(
            task=None, source_id="src-x", tenant_id="t-x", triggered_by="manual"
        )
    assert result["docs_failed"] == 1
    tracker.add_to_dlq.assert_awaited_once()
    kwargs = tracker.add_to_dlq.await_args.kwargs
    assert kwargs["doc_id"] == "d-1" and kwargs["raw_doc"] is doc
    assert "embedder exploded" in kwargs["error"]
    (job,) = tracker._jobs.values()
    assert job.status == "failed" and job.docs_failed == 1
