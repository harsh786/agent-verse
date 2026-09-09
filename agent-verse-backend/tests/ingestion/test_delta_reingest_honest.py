"""WS-12 [FAKE success] — delta_reingest_files routes the real pipeline, no faking.

The old stub reported ``status=ok`` with a fabricated ``chunks_ingested`` count
derived from ``len(pages)`` WITHOUT ever running the ingestion pipeline. This
pins the honest behaviour: it routes through the registered connector + real
pipeline and reports real tallies, or an explicit ``unsupported`` for an
unknown source — never a fabricated success.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.scaling.tasks import delta_reingest_files


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
    with (
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
