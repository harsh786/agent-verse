"""KB-12: legacy Notion / Drive / email ingest never leak raw exception text, and an
unsupported Drive file is reported as unsupported, not "empty".
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tests.api.test_knowledge_coverage_boost import _auth, _client

_SECRET = "postgresql://svc:hunter2@10.0.0.7/internal"


def test_an_upstream_exception_is_a_generic_502_with_a_correlation_id() -> None:
    connector = MagicMock()
    connector.fetch_page_content = AsyncMock(side_effect=RuntimeError(_SECRET))
    with patch("app.ingestion.connectors.notion_connector.NotionConnector", return_value=connector):
        resp = _client().post(
            "/knowledge/ingest/notion",
            json={"api_key": "k", "page_id": "p1", "collection_id": "col-1"},
            headers=_auth(),
        )
    assert resp.status_code == 502
    assert "hunter2" not in resp.text and "10.0.0.7" not in resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "upstream_error"
    assert detail["correlation_id"]


def test_drive_route_catch_all_does_not_leak() -> None:
    with patch(
        "app.ingestion.connectors.gdrive_connector.GDriveConnector",
        side_effect=RuntimeError(_SECRET),
    ):
        resp = _client().post(
            "/knowledge/ingest/gdrive-folder",
            json={
                "folder_id": "f",
                "collection_id": "col-1",
                "service_account_key_json": '{"type": "service_account"}',
            },
            headers=_auth(),
        )
    assert resp.status_code == 502
    assert "hunter2" not in resp.text


def test_unsupported_drive_mime_is_reported_as_unsupported() -> None:
    connector = MagicMock()
    connector.list_files.return_value = [
        {"id": "f1", "name": "movie.mp4", "mimeType": "video/mp4"},
        {"id": "f2", "name": "blank.txt", "mimeType": "text/plain"},
    ]
    connector.download_file.side_effect = lambda fid, mime, **kw: None if fid == "f1" else "  "
    orch = MagicMock()
    orch.ingest = AsyncMock(return_value=SimpleNamespace(chunks_created=1))
    with (
        patch("app.ingestion.connectors.gdrive_connector.GDriveConnector", return_value=connector),
        patch("app.ingestion.orchestrator.IngestionOrchestrator", return_value=orch),
    ):
        resp = _client().post(
            "/knowledge/ingest/gdrive-folder",
            json={
                "folder_id": "f",
                "collection_id": "col-1",
                "service_account_key_json": '{"type": "service_account"}',
            },
            headers=_auth(),
        )
    assert resp.status_code == 200, resp.text
    reasons = {s["file_id"]: s["reason"] for s in resp.json()["skipped"]}
    assert reasons == {"f1": "unsupported", "f2": "empty"}


def test_gdrive_download_file_returns_none_for_unsupported_types() -> None:
    from app.ingestion.connectors.gdrive_connector import GDriveConnector

    connector = GDriveConnector(credentials=object())
    connector._service = MagicMock()
    assert connector.download_file("f1", "video/mp4") is None
