"""/knowledge/ingest/gdrive-folder must not report success when files failed.

Regression: the endpoint always answered 200 ``{"status": "ingested"}`` even
when every file in the folder failed to download or ingest -- the failures
were only visible as free-text strings in ``errors``. Contract now:

* every file succeeded (or was skipped as empty) -> 200, ``status: "ingested"``
* some files failed                               -> 207, ``status: "partial"``
* every attempted file failed                     -> 502, ``detail.status: "failed"``

Each failure is reported per file in ``failed`` as
``{"file_id", "filename", "error", "correlation_id"}`` — ``error`` is a
client-safe reason, never the raw exception text (a04-F067-06).
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
    assert detail["failed"][0]["error"] == "the file could not be ingested"
    assert "download failed" not in resp.text


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
    [failure] = body["failed"]
    assert failure["file_id"] == "f2" and failure["filename"] == "two.txt"
    assert failure["error"] == "the file could not be ingested"
    assert len(failure["correlation_id"]) == 32
    assert "quota exceeded" not in resp.text


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
    assert "embedder down" not in resp.text


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


# ── a04-F067-06: per-file errors never echo raw exception text ────────────────


def test_per_file_errors_are_sanitized_and_logged_with_a_correlation_id() -> None:
    secret_detail = "psycopg OperationalError: connection to 10.0.4.17:5432 refused for user app"
    logger = MagicMock()
    with patch("app.observability.logging.get_logger", return_value=logger):
        resp = _post(
            _FILES,
            download=["content one", "content two"],
            ingest=AsyncMock(
                side_effect=[SimpleNamespace(chunks_created=1), RuntimeError(secret_detail)]
            ),
        )
    assert resp.status_code == 207
    body = resp.json()
    assert "10.0.4.17" not in resp.text and "psycopg" not in resp.text
    [failure] = body["failed"]
    assert body["errors"] == ["two.txt: the file could not be ingested"]
    logged = [
        c.kwargs
        for c in logger.warning.call_args_list
        if c.args and c.args[0] == "gdrive_file_ingest_failed"
    ]
    assert logged and logged[0]["correlation_id"] == failure["correlation_id"]
    assert secret_detail in logged[0]["error"]  # the cause stays in the server log


def test_known_failure_kinds_get_specific_public_reasons() -> None:
    from app.api.knowledge import _public_file_error
    from app.ingestion.document_text import DocumentParseError
    from app.ingestion.pipeline import IngestionPolicyRejectedError

    http_error = RuntimeError("<HttpError 403 ... internal quota project 1234>")
    http_error.resp = SimpleNamespace(status=403)  # type: ignore[attr-defined]
    assert _public_file_error(http_error) == "the Drive API refused the request (HTTP 403)"
    assert _public_file_error(IngestionPolicyRejectedError("pii: SSN 123-45-6789")) == (
        "rejected by the ingestion policy"
    )
    assert _public_file_error(DocumentParseError("x.pdf: broken xref at /tmp/a")) == (
        "the file could not be parsed"
    )


# ── a04-F067-04: an oversized file is refused before / while downloading ─────


def test_listed_size_over_the_budget_truncates_without_downloading() -> None:
    from app.ingestion.connectors.gdrive_connector import GDriveConnector

    connector = GDriveConnector(credentials=object())
    connector._service = MagicMock()
    files = [
        {"id": "big", "name": "big.pdf", "mimeType": "application/pdf", "size": str(10**12)},
    ]
    client = _client()
    fake = MagicMock()
    fake.list_files.return_value = files
    fake.download_file.side_effect = connector.download_file
    orch = MagicMock()
    orch.ingest = AsyncMock(return_value=SimpleNamespace(chunks_created=1))
    with (
        patch(
            "app.ingestion.connectors.gdrive_connector.GDriveConnector", return_value=fake
        ),
        patch("app.ingestion.orchestrator.IngestionOrchestrator", return_value=orch),
    ):
        resp = client.post("/knowledge/ingest/gdrive-folder", json=_BODY, headers=_auth())
    assert resp.status_code == 200, resp.text
    assert resp.json()["truncated"] is True
    assert resp.json()["files_ingested"] == 0
    connector._service.files.return_value.get_media.assert_not_called()
    kwargs = fake.download_file.call_args.kwargs
    assert kwargs["size"] == str(10**12)
