"""/knowledge/ingest/gdrive-folder must not report success when files failed.

Regression: the endpoint always answered 200 ``{"status": "ingested"}`` even
when every file in the folder failed to download or ingest -- the failures
were only visible as free-text strings in ``errors``. Contract now:

* every file succeeded (or was skipped as empty) -> 200, ``status: "ingested"``
* some files failed                               -> 207, ``status: "partial"``
* every attempted file failed                     -> 502, ``detail.status: "failed"``

Each failure is reported per file in ``failed`` as
``{"file_id", "filename", "error"}``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from tests.api.test_knowledge_coverage_boost import _auth, _client

_BODY = {
    "folder_id": "folder-1",
    "collection_id": "col-1",
    "service_account_key_json": '{"type": "service_account"}',
}


def _post(files: list[dict[str, str]], download: Any, ingest: Any) -> Any:
    client = _client()
    connector = MagicMock()
    connector.list_files.return_value = files
    connector.download_file.side_effect = download
    orch = MagicMock()
    orch.ingest = ingest
    with (
        patch(
            "app.ingestion.connectors.gdrive_connector.GDriveConnector",
            return_value=connector,
        ),
        patch("app.ingestion.orchestrator.IngestionOrchestrator", return_value=orch),
    ):
        return client.post("/knowledge/ingest/gdrive-folder", json=_BODY, headers=_auth())


_FILES = [
    {"id": "f1", "name": "one.txt", "mimeType": "text/plain"},
    {"id": "f2", "name": "two.txt", "mimeType": "text/plain"},
]


def test_all_files_failing_is_502_with_per_file_failures() -> None:
    resp = _post(
        _FILES,
        download=RuntimeError("download failed"),
        ingest=AsyncMock(return_value=SimpleNamespace(chunks_created=3)),
    )
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["status"] == "failed"
    assert detail["files_ingested"] == 0
    assert [f["file_id"] for f in detail["failed"]] == ["f1", "f2"]
    assert detail["failed"][0]["filename"] == "one.txt"
    assert "download failed" in detail["failed"][0]["error"]


def test_some_files_failing_is_207_partial() -> None:
    resp = _post(
        _FILES,
        download=["content one", RuntimeError("quota exceeded")],
        ingest=AsyncMock(return_value=SimpleNamespace(chunks_created=3)),
    )
    assert resp.status_code == 207
    body = resp.json()
    assert body["status"] == "partial"
    assert body["files_ingested"] == 1
    assert body["chunks_created"] == 3
    assert body["failed"] == [
        {"file_id": "f2", "filename": "two.txt", "error": "quota exceeded"}
    ]


def test_ingest_failure_after_download_counts_as_failed_file() -> None:
    resp = _post(
        _FILES,
        download=["content one", "content two"],
        ingest=AsyncMock(
            side_effect=[SimpleNamespace(chunks_created=2), ValueError("embedder down")]
        ),
    )
    assert resp.status_code == 207
    body = resp.json()
    assert body["status"] == "partial"
    assert body["failed"][0]["file_id"] == "f2"
    assert "embedder down" in body["failed"][0]["error"]


def test_all_files_succeeding_is_200_ingested_with_empty_failed_list() -> None:
    resp = _post(
        _FILES,
        download=["content one", "   "],
        ingest=AsyncMock(return_value=SimpleNamespace(chunks_created=4)),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ingested"
    assert body["files_ingested"] == 1
    assert body["skipped"] == [{"file_id": "f2", "filename": "two.txt", "reason": "empty"}]
    assert body["failed"] == []
