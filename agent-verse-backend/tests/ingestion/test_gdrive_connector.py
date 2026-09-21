"""GDriveConnector — real functional scenarios against a mocked Drive v3 API.

No real network calls: the googleapiclient/google-auth libraries are faked via
``sys.modules`` injection so ``_build_service`` exercises its real branching,
and the Drive ``service`` object itself is a MagicMock whose chained calls
return canned API responses.
"""
from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.ingestion.connector_registry import get_connector, load_all_connectors
from app.ingestion.connectors.gdrive_connector import (
    GDriveConnector,
    GDriveSourceConnector,
)
from app.ingestion.source_config import SourceConfig, SourceFamily


def _config(**cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id="src-gdrive",
        tenant_id="t1",
        name="gdrive-src",
        family=SourceFamily.DOCUMENT_STORE,
        source_type="gdrive",
        connection_config=cc,
    )


def _fake_google_modules() -> dict[str, MagicMock]:
    """Build fake google.oauth2.service_account + googleapiclient.discovery modules."""
    google_oauth2 = MagicMock()
    service_account_mod = MagicMock()
    fake_creds = MagicMock(name="ServiceAccountCredentials")
    service_account_mod.Credentials.from_service_account_file = MagicMock(
        return_value=fake_creds
    )
    google_oauth2.service_account = service_account_mod

    googleapiclient = MagicMock()
    discovery_mod = MagicMock()
    built_service = MagicMock(name="DriveService")
    discovery_mod.build = MagicMock(return_value=built_service)
    googleapiclient.discovery = discovery_mod

    return {
        "google.oauth2": google_oauth2,
        "google.oauth2.service_account": service_account_mod,
        "googleapiclient": googleapiclient,
        "googleapiclient.discovery": discovery_mod,
    }


class _FakeMediaIoBaseDownload:
    """Minimal stand-in for googleapiclient.http.MediaIoBaseDownload."""

    _CONTENT: bytes = b"downloaded file body"

    def __init__(self, buf: Any, request: Any) -> None:
        self._buf = buf
        self._chunks_left = 2

    def next_chunk(self) -> tuple[Any, bool]:
        self._chunks_left -= 1
        if self._chunks_left == 0:
            self._buf.write(self._CONTENT)
            return None, True
        return None, False


class TestGDriveConnectorBuildService:
    def test_import_error_without_libraries(self) -> None:
        # googleapiclient is genuinely not installed in this environment.
        connector = GDriveConnector(credentials=object())
        with pytest.raises(ImportError, match="google-api-python-client"):
            connector._build_service()

    def test_cached_service_is_reused(self) -> None:
        connector = GDriveConnector(credentials=object())
        sentinel = MagicMock(name="cached-service")
        connector._service = sentinel
        assert connector._build_service() is sentinel

    def test_no_credentials_or_key_path_raises_value_error(self) -> None:
        connector = GDriveConnector()
        fakes = _fake_google_modules()
        with patch.dict(sys.modules, fakes):
            with pytest.raises(ValueError, match="credentials or key_path"):
                connector._build_service()

    def test_build_with_credentials(self) -> None:
        creds = MagicMock(name="OAuthCredentials")
        connector = GDriveConnector(credentials=creds)
        fakes = _fake_google_modules()
        with patch.dict(sys.modules, fakes):
            service = connector._build_service()
        fakes["googleapiclient.discovery"].build.assert_called_once_with(
            "drive", "v3", credentials=creds
        )
        assert service is fakes["googleapiclient.discovery"].build.return_value

    def test_build_with_key_path(self) -> None:
        connector = GDriveConnector(key_path="/tmp/key.json")
        fakes = _fake_google_modules()
        with patch.dict(sys.modules, fakes):
            connector._build_service()
        fakes["google.oauth2.service_account"].Credentials.from_service_account_file.assert_called_once_with(
            "/tmp/key.json", scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
        fakes["googleapiclient.discovery"].build.assert_called_once()


class TestGDriveConnectorListFiles:
    def test_single_page(self) -> None:
        connector = GDriveConnector(credentials=object())
        service = MagicMock()
        service.files.return_value.list.return_value.execute.return_value = {
            "files": [{"id": "f1", "name": "a.txt"}],
        }
        with patch.object(GDriveConnector, "_build_service", return_value=service):
            files = connector.list_files("folder-1")
        assert files == [{"id": "f1", "name": "a.txt"}]
        call_kwargs = service.files.return_value.list.call_args.kwargs
        assert "folder-1" in call_kwargs["q"]

    def test_pagination_follows_next_page_token(self) -> None:
        connector = GDriveConnector(credentials=object())
        service = MagicMock()
        page1 = {"files": [{"id": "f1"}], "nextPageToken": "tok2"}
        page2 = {"files": [{"id": "f2"}]}
        service.files.return_value.list.return_value.execute.side_effect = [page1, page2]
        with patch.object(GDriveConnector, "_build_service", return_value=service):
            files = connector.list_files("folder-1", page_size=1)
        assert [f["id"] for f in files] == ["f1", "f2"]
        assert service.files.return_value.list.call_count == 2
        second_call_kwargs = service.files.return_value.list.call_args_list[1].kwargs
        assert second_call_kwargs["pageToken"] == "tok2"


class TestGDriveConnectorDownloadFile:
    def test_export_google_doc(self) -> None:
        connector = GDriveConnector(credentials=object())
        service = MagicMock()
        with (
            patch.object(GDriveConnector, "_build_service", return_value=service),
            patch.dict(
                sys.modules,
                {
                    "googleapiclient.http": MagicMock(
                        MediaIoBaseDownload=_FakeMediaIoBaseDownload
                    )
                },
            ),
        ):
            content = connector.download_file(
                "doc1", "application/vnd.google-apps.document"
            )
        service.files.return_value.export_media.assert_called_once_with(
            fileId="doc1", mimeType="text/plain"
        )
        assert content == "downloaded file body"

    def test_direct_download_supported_mime(self) -> None:
        connector = GDriveConnector(credentials=object())
        service = MagicMock()
        with (
            patch.object(GDriveConnector, "_build_service", return_value=service),
            patch.dict(
                sys.modules,
                {
                    "googleapiclient.http": MagicMock(
                        MediaIoBaseDownload=_FakeMediaIoBaseDownload
                    )
                },
            ),
        ):
            content = connector.download_file("f1", "text/plain")
        service.files.return_value.get_media.assert_called_once_with(fileId="f1")
        assert content == "downloaded file body"

    def test_unsupported_mime_returns_empty_string(self) -> None:
        connector = GDriveConnector(credentials=object())
        service = MagicMock()
        with patch.object(GDriveConnector, "_build_service", return_value=service):
            content = connector.download_file("f1", "application/octet-stream")
        assert content == ""
        service.files.return_value.get_media.assert_not_called()
        service.files.return_value.export_media.assert_not_called()


class TestGDriveConnectorMetadata:
    def test_get_file_metadata(self) -> None:
        connector = GDriveConnector(credentials=object())
        service = MagicMock()
        service.files.return_value.get.return_value.execute.return_value = {
            "id": "f1",
            "name": "doc.txt",
        }
        with patch.object(GDriveConnector, "_build_service", return_value=service):
            meta = connector.get_file_metadata("f1")
        assert meta == {"id": "f1", "name": "doc.txt"}
        service.files.return_value.get.assert_called_once_with(
            fileId="f1", fields="id,name,mimeType,modifiedTime,size,webViewLink"
        )


class TestGDriveSourceConnectorRegistration:
    def test_registered(self) -> None:
        load_all_connectors()
        assert "gdrive" in {"gdrive"} & {"gdrive"}
        cls = get_connector("gdrive")
        assert cls is GDriveSourceConnector

    def test_client_reads_connection_config(self) -> None:
        conn = GDriveSourceConnector()
        cfg = _config(folder_id="F", key_path="/tmp/k.json")
        client = conn._client(cfg)
        assert client._key_path == "/tmp/k.json"


class TestGDriveSourceConnectorValidateConnection:
    async def test_success(self) -> None:
        conn = GDriveSourceConnector()
        cfg = _config(folder_id="F")
        with patch.object(
            GDriveConnector, "list_files", return_value=[{"id": "f1"}, {"id": "f2"}]
        ):
            health = await conn.validate_connection(cfg)
        assert health.ok is True
        assert health.metadata["files_visible"] == 2

    async def test_failure_returns_unhealthy(self) -> None:
        conn = GDriveSourceConnector()
        cfg = _config(folder_id="F")
        with patch.object(
            GDriveConnector, "list_files", side_effect=RuntimeError("quota exceeded")
        ):
            health = await conn.validate_connection(cfg)
        assert health.ok is False
        assert "quota exceeded" in health.error


class TestGDriveSourceConnectorGetDelta:
    async def test_yields_documents_with_metadata(self) -> None:
        conn = GDriveSourceConnector()
        cfg = _config(folder_id="F")
        files = [
            {
                "id": "f1",
                "name": "report.txt",
                "mimeType": "text/plain",
                "modifiedTime": "2026-01-02T00:00:00Z",
            }
        ]
        with (
            patch.object(GDriveConnector, "list_files", return_value=files),
            patch.object(GDriveConnector, "download_file", return_value="hello world"),
        ):
            docs = [d async for d in conn.get_delta(cfg, None)]
        assert len(docs) == 1
        raw, cursor = docs[0]
        assert raw.content == b"hello world"
        assert raw.title == "report.txt"
        assert raw.metadata == {"gdrive_file_id": "f1", "source_type": "gdrive"}
        assert cursor == "2026-01-02T00:00:00Z"

    async def test_skips_files_without_id(self) -> None:
        conn = GDriveSourceConnector()
        cfg = _config(folder_id="F")
        files = [{"name": "no-id.txt", "mimeType": "text/plain"}]
        with (
            patch.object(GDriveConnector, "list_files", return_value=files),
            patch.object(GDriveConnector, "download_file", return_value="text"),
        ):
            docs = [d async for d in conn.get_delta(cfg, None)]
        assert docs == []

    async def test_skips_empty_content(self) -> None:
        conn = GDriveSourceConnector()
        cfg = _config(folder_id="F")
        files = [
            {"id": "f1", "name": "empty.txt", "mimeType": "text/plain", "modifiedTime": "t1"}
        ]
        with (
            patch.object(GDriveConnector, "list_files", return_value=files),
            patch.object(GDriveConnector, "download_file", return_value="   "),
        ):
            docs = [d async for d in conn.get_delta(cfg, None)]
        assert docs == []

    async def test_skips_files_not_newer_than_cursor(self) -> None:
        conn = GDriveSourceConnector()
        cfg = _config(folder_id="F")
        files = [
            {
                "id": "old",
                "name": "old.txt",
                "mimeType": "text/plain",
                "modifiedTime": "2026-01-01T00:00:00Z",
            },
            {
                "id": "new",
                "name": "new.txt",
                "mimeType": "text/plain",
                "modifiedTime": "2026-01-05T00:00:00Z",
            },
        ]
        with (
            patch.object(GDriveConnector, "list_files", return_value=files),
            patch.object(GDriveConnector, "download_file", return_value="content"),
        ):
            docs = [
                d
                async for d in conn.get_delta(cfg, "2026-01-02T00:00:00Z")
            ]
        assert len(docs) == 1
        raw, cursor = docs[0]
        assert raw.metadata["gdrive_file_id"] == "new"
        assert cursor == "2026-01-05T00:00:00Z"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
