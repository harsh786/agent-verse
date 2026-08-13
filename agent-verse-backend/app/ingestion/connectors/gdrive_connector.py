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
from typing import Any


class GDriveConnector:
    """Thin wrapper around the Google Drive REST API v3."""

    _DRIVE_API = "https://www.googleapis.com/drive/v3"
    _EXPORT_MIME: dict[str, str] = {
        "application/vnd.google-apps.document": "text/plain",
        "application/vnd.google-apps.spreadsheet": "text/csv",
        "application/vnd.google-apps.presentation": "text/plain",
    }
    # MIME types we can download directly
    _DIRECT_DOWNLOAD_MIMES: frozenset[str] = frozenset({
        "text/plain",
        "text/markdown",
        "text/csv",
        "text/html",
        "application/json",
        "application/pdf",
    })

    def __init__(
        self,
        credentials: Any | None = None,
        key_path: str | None = None,
    ) -> None:
        self._creds = credentials
        self._key_path = key_path
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
    ) -> list[dict[str, Any]]:
        """Return file metadata for all files in a Google Drive folder."""
        service = self._build_service()
        files: list[dict[str, Any]] = []
        page_token: str | None = None
        query = f"'{folder_id}' in parents and trashed = false"
        while True:
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
        return files

    def download_file(self, file_id: str, mime_type: str) -> str:
        """Download or export a Drive file and return its content as a string."""
        service = self._build_service()

        export_mime = self._EXPORT_MIME.get(mime_type)
        if export_mime:
            # Google Workspace file — export as plain text
            request = service.files().export_media(fileId=file_id, mimeType=export_mime)
        elif mime_type in self._DIRECT_DOWNLOAD_MIMES:
            request = service.files().get_media(fileId=file_id)
        else:
            # Unsupported type — return empty string
            return ""

        from googleapiclient.http import MediaIoBaseDownload  # type: ignore[import-untyped]

        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue().decode("utf-8", errors="replace")

    def get_file_metadata(self, file_id: str) -> dict[str, Any]:
        """Return metadata for a single Drive file."""
        service = self._build_service()
        return (
            service.files()
            .get(fileId=file_id, fields="id,name,mimeType,modifiedTime,size,webViewLink")
            .execute()
        )
