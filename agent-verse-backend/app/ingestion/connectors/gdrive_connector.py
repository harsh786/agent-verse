"""GDriveConnector — lists and downloads files from Google Drive for ingestion.

Supports:
- Listing files in a specific folder
- Downloading plain text for text-based files
- Exporting Google Docs/Sheets/Slides to plain text or CSV

Authentication: expects a pre-built ``google.oauth2.credentials.Credentials``
object or a service-account JSON key path via ``key_path``.
"""

from __future__ import annotations

import io
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, ClassVar

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    fetch_failure_document,
    stable_doc_id,
)
from app.ingestion.connector_registry import register
from app.ingestion.sdk_executor import run_blocking

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig


# Bytes requested per Drive download request. googleapiclient's default chunk
# is 100 MiB, so a byte cap checked between chunks fired only after up to
# 100 MiB of one file was already buffered (a04-F067-04).
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024


class DriveFileTooLargeError(ValueError):
    """A Drive file is larger than the download cap (listed size or bytes read)."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        super().__init__(f"file exceeds the {max_bytes}-byte download cap")


def _listed_size(size: object) -> int | None:
    """Drive's listed ``size`` (a decimal string; absent for Google Docs)."""
    try:
        value = int(str(size))
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


class GDriveConnector:
    """Thin wrapper around the Google Drive REST API v3."""

    _DRIVE_API = "https://www.googleapis.com/drive/v3"
    _EXPORT_MIME: ClassVar[dict[str, str]] = {
        "application/vnd.google-apps.document": "text/plain",
        "application/vnd.google-apps.spreadsheet": "text/csv",
        "application/vnd.google-apps.presentation": "text/plain",
    }
    # MIME types we can download directly
    _DIRECT_DOWNLOAD_MIMES: frozenset[str] = frozenset(
        {
            "text/plain",
            "text/markdown",
            "text/csv",
            "text/html",
            "application/json",
            "application/pdf",
        }
    )

    def __init__(
        self,
        credentials: Any | None = None,
        key_path: str | None = None,
        service_account_info: dict[str, Any] | None = None,
    ) -> None:
        self._creds = credentials
        self._key_path = key_path
        # The parsed service-account key, kept in memory (never written to disk).
        self._service_account_info = service_account_info
        self._service: Any = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_service(self) -> Any:
        """Lazy-build the googleapiclient Drive service."""
        if self._service is not None:
            return self._service
        try:
            from google.oauth2 import service_account  # type: ignore[import-untyped]
            from googleapiclient.discovery import build  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "google-api-python-client and google-auth are required for GDriveConnector. "
                "Install with: pip install google-api-python-client google-auth"
            ) from exc

        if self._creds is not None:
            self._service = build("drive", "v3", credentials=self._creds)
        elif self._service_account_info is not None:
            creds = service_account.Credentials.from_service_account_info(
                self._service_account_info,
                scopes=["https://www.googleapis.com/auth/drive.readonly"],
            )
            self._service = build("drive", "v3", credentials=creds)
        elif self._key_path is not None:
            creds = service_account.Credentials.from_service_account_file(
                self._key_path,
                scopes=["https://www.googleapis.com/auth/drive.readonly"],
            )
            self._service = build("drive", "v3", credentials=creds)
        else:
            raise ValueError("Either credentials or key_path must be provided")
        return self._service

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_files(
        self,
        folder_id: str,
        page_size: int = 100,
        max_files: int | None = None,
    ) -> list[dict[str, Any]]:
        """File metadata of a Drive folder; at most ``max_files`` (None = all)."""
        service = self._build_service()
        files: list[dict[str, Any]] = []
        page_token: str | None = None
        query = f"'{folder_id}' in parents and trashed = false"
        while max_files is None or len(files) < max_files:
            params: dict[str, Any] = {
                "q": query,
                "fields": "nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                "pageSize": page_size,
            }
            if page_token:
                params["pageToken"] = page_token
            result = service.files().list(**params).execute()
            files.extend(result.get("files", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return files if max_files is None else files[:max_files]

    def download_file(
        self,
        file_id: str,
        mime_type: str,
        max_bytes: int | None = None,
        size: object = None,
    ) -> str | None:
        """Download or export a Drive file and return its content as a string.

        Returns ``None`` for a type this connector cannot read (it used to return
        ``""``, so an unsupported file was reported as an empty one).

        Raises :class:`DriveFileTooLargeError` (a ``ValueError``) without
        downloading anything when the listed ``size`` already exceeds
        ``max_bytes``, and otherwise as soon as more than ``max_bytes`` have been
        read — bytes are requested in 1 MiB ranges (at most ``max_bytes + 1``),
        so at most one small range past the cap is ever buffered.
        """
        listed = _listed_size(size)
        if max_bytes is not None and listed is not None and listed > max_bytes:
            raise DriveFileTooLargeError(max_bytes)
        service = self._build_service()

        export_mime = self._EXPORT_MIME.get(mime_type)
        if export_mime:
            # Google Workspace file — export as plain text
            request = service.files().export_media(fileId=file_id, mimeType=export_mime)
        elif mime_type in self._DIRECT_DOWNLOAD_MIMES:
            request = service.files().get_media(fileId=file_id)
        else:
            return None  # unsupported type

        from googleapiclient.http import MediaIoBaseDownload  # type: ignore[import-untyped]

        chunksize = _DOWNLOAD_CHUNK_BYTES
        if max_bytes is not None:
            chunksize = max(1, min(_DOWNLOAD_CHUNK_BYTES, max_bytes + 1))
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request, chunksize=chunksize)
        done = False
        while not done:
            _, done = downloader.next_chunk()
            if max_bytes is not None and buf.tell() > max_bytes:
                raise DriveFileTooLargeError(max_bytes)
        return buf.getvalue().decode("utf-8", errors="replace")

    def get_file_metadata(self, file_id: str) -> dict[str, Any]:
        """Return metadata for a single Drive file."""
        service = self._build_service()
        return (
            service.files()
            .get(fileId=file_id, fields="id,name,mimeType,modifiedTime,size,webViewLink")
            .execute()
        )


@register("gdrive", feature_flag="ingestion_connector_gdrive_enabled")
class GDriveSourceConnector(BaseConnector):
    """BaseConnector adapter routing Google Drive files through the real pipeline.

    Reads ``config.connection_config`` (``folder_id`` plus ``key_path`` or a
    prebuilt ``credentials`` object) and yields one ``RawDocument`` per file.
    Cursor is the max ``modifiedTime`` seen (LAW-03).
    """

    source_type = "gdrive"

    def _client(self, config: SourceConfig) -> GDriveConnector:
        cc = config.connection_config
        return GDriveConnector(
            credentials=cc.get("credentials"), key_path=cc.get("key_path")
        )

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        t0 = time.perf_counter()
        try:
            client = self._client(config)
            # googleapiclient is blocking: run it on the SDK pool, not the loop.
            files = await run_blocking(
                client.list_files, config.connection_config.get("folder_id", "")
            )
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(ok=True, latency_ms=latency,
                                    metadata={"files_visible": len(files)})
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument

        client = self._client(config)
        folder_id = config.connection_config.get("folder_id", "")
        files = await run_blocking(client.list_files, folder_id)
        new_cursor = cursor or ""
        for meta in files:
            file_id = meta.get("id", "")
            mime = meta.get("mimeType", "")
            if not file_id:
                continue
            modified = meta.get("modifiedTime", "")
            if cursor and modified and modified <= cursor:
                continue
            if modified:
                new_cursor = max(new_cursor, modified)
            cap = int(config.max_doc_size_bytes)
            try:
                # The Source's document size cap bounds the download itself (it
                # was unbounded: the whole file was buffered, then refused by
                # the pipeline's size stage).
                content = await run_blocking(
                    client.download_file, file_id, mime, cap, meta.get("size")
                )
            except DriveFileTooLargeError as exc:
                yield fetch_failure_document(
                    config,
                    doc_id=stable_doc_id(config, file_id),
                    reason=str(exc),
                    retryable=False,
                    title=meta.get("name", ""),
                    metadata={"gdrive_file_id": file_id, "source_type": "gdrive"},
                ), new_cursor
                continue
            if content is None or not content.strip():
                continue  # unsupported type, or no text
            doc = RawDocument(
                doc_id=stable_doc_id(config, file_id),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                title=meta.get("name", ""),
                content=content.encode(),
                content_type="text/plain",
                modified_at=modified,
                metadata={"gdrive_file_id": file_id, "source_type": "gdrive"},
            )
            yield doc, new_cursor
