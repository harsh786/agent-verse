"""WS-12 [FAKE success] — delta_reingest_files routes the real pipeline, no faking.

The old stub reported ``status=ok`` with a fabricated ``chunks_ingested`` count
derived from ``len(pages)`` WITHOUT ever running the ingestion pipeline. This
pins the honest behaviour: it routes through the registered connector + real
pipeline and reports real tallies, or an explicit ``unsupported`` for an
unknown source — never a fabricated success.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from app.ingestion.source_config import PipelineResult
from app.scaling.tasks import delta_reingest_files


class _RecordingPipeline:
    """Stands in for the worker's fully-wired pipeline (no DB in unit tests)."""

    def __init__(self) -> None:
        self.docs: list[Any] = []

    async def ingest(self, raw_doc: Any, config: Any) -> PipelineResult:
        self.docs.append((raw_doc, config))
        return PipelineResult(
            doc_id=raw_doc.doc_id,
            source_id=config.source_id,
            tenant_id=config.tenant_id,
            status="indexed",
            chunks_created=1,
        )


def _worker(pipeline: Any) -> Any:
    return patch(
        "app.ingestion.scheduler._build_worker_ingestion",
        return_value=(SimpleNamespace(), pipeline, SimpleNamespace()),
    )


def test_unknown_source_type_is_explicitly_unsupported() -> None:
    result = delta_reingest_files.run(
        tenant_id="t1",
        collection_id="c1",
        source_type="totally-unknown-source",
        source_config={},
    )
    assert result["status"] == "unsupported"
    # Never a fabricated chunk count.
    assert "chunks_ingested" not in result


def test_notion_delta_runs_real_pipeline_and_reports_real_tallies() -> None:
    pages = [{"id": "p1", "url": "https://notion.so/p1",
              "last_edited_time": "2026-01-02T00:00:00Z"}]
    pipeline = _RecordingPipeline()
    with (
        _worker(pipeline),
        patch(
            "app.ingestion.connectors.notion_connector.NotionConnector"
            ".list_all_pages_in_workspace",
            new=AsyncMock(return_value=pages),
        ),
        patch(
            "app.ingestion.connectors.notion_connector.NotionConnector.fetch_page_content",
            new=AsyncMock(return_value="Notion page body " * 40),
        ),
    ):
        result = delta_reingest_files.run(
            tenant_id="t1",
            collection_id="c1",
            source_type="notion",
            source_config={"api_key": "secret"},
        )
    # Honest structured tallies from a real pipeline run (no fabricated count).
    assert result["status"] == "ok"
    assert "chunks_ingested" not in result
    assert {"docs_indexed", "docs_skipped", "docs_failed"} <= result.keys()
    assert result["docs_indexed"] + result["docs_skipped"] + result["docs_failed"] == 1


def test_delta_reingest_uses_the_fully_wired_worker_pipeline() -> None:
    """Regression: a bare ``IngestionPipeline()`` (no store/embedder/PII/quota)
    skipped every document as ``no_embedder`` while reporting ``status: ok``.
    The task must use the worker's wired pipeline and count what it indexed."""
    pages = [{"id": "p1", "url": "https://notion.so/p1",
              "last_edited_time": "2026-01-02T00:00:00Z"}]
    pipeline = _RecordingPipeline()
    with (
        _worker(pipeline),
        patch(
            "app.ingestion.connectors.notion_connector.NotionConnector"
            ".list_all_pages_in_workspace",
            new=AsyncMock(return_value=pages),
        ),
        patch(
            "app.ingestion.connectors.notion_connector.NotionConnector.fetch_page_content",
            new=AsyncMock(return_value="Notion page body " * 40),
        ),
    ):
        result = delta_reingest_files.run(
            tenant_id="t1",
            collection_id="c1",
            source_type="notion",
            source_config={"api_key": "secret"},
        )
    assert len(pipeline.docs) == 1
    assert pipeline.docs[0][1].collection_id == "c1"
    assert result["docs_indexed"] == 1
