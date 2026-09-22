"""Tests for SharePoint connector."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.connectors.sharepoint_connector import (
    SharePointConnector,
    SharePointSourceConnector,
)
from app.ingestion.source_config import SourceConfig, SourceFamily


def _config(conn_config: dict | None = None) -> SourceConfig:
    return SourceConfig(
        source_id="src-1",
        tenant_id="tenant-1",
        name="Test SharePoint Source",
        family=SourceFamily.OBJECT_STORAGE,
        source_type="sharepoint",
        connection_config=conn_config or {},
    )


def _http_client_mock(get_response=None, post_response=None):
    """Build a mock httpx.AsyncClient context manager returning canned responses."""
    mock_client_class = MagicMock()
    mock_ctx = AsyncMock()
    if get_response is not None:
        mock_ctx.get = AsyncMock(return_value=get_response)
    if post_response is not None:
        mock_ctx.post = AsyncMock(return_value=post_response)
    mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
    mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_client_class, mock_ctx


class TestSharePointConnector:
    def setup_method(self) -> None:
        self.connector = SharePointConnector(
            tenant_id="test-tenant",
            client_id="test-client",
            client_secret="test-secret",
        )

    def test_graph_url_structure(self) -> None:
        """Verify the base URL is correct."""
        from app.ingestion.connectors.sharepoint_connector import _GRAPH_BASE
        assert "graph.microsoft.com" in _GRAPH_BASE

    def test_token_url_structure(self) -> None:
        from app.ingestion.connectors.sharepoint_connector import _TOKEN_URL
        url = _TOKEN_URL.format(tenant_id="my-tenant")
        assert "login.microsoftonline.com" in url
        assert "my-tenant" in url
        assert "oauth2/v2.0/token" in url

    def test_connector_stores_credentials(self) -> None:
        assert self.connector._tenant_id == "test-tenant"
        assert self.connector._client_id == "test-client"
        assert self.connector._client_secret == "test-secret"

    def test_connector_initial_token_is_none(self) -> None:
        assert self.connector._access_token is None

    @pytest.mark.asyncio
    async def test_get_access_token_raises_on_bad_creds(self) -> None:
        """Test that bad credentials cause an httpx error (not silent failure)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import httpx

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized", request=MagicMock(), response=MagicMock()
        )

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_ctx = AsyncMock()
            mock_ctx.post = AsyncMock(return_value=mock_resp)
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(httpx.HTTPStatusError):
                await self.connector.get_access_token()

    def test_exported_from_connectors_init(self) -> None:
        from app.ingestion.connectors import SharePointConnector as SC
        assert SC is SharePointConnector

    @pytest.mark.asyncio
    async def test_get_access_token_success_stores_token(self) -> None:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"access_token": "tok-123"}
        mock_client_class, mock_ctx = _http_client_mock(post_response=resp)
        with patch("httpx.AsyncClient", mock_client_class):
            token = await self.connector.get_access_token()
        assert token == "tok-123"
        assert self.connector._access_token == "tok-123"

    @pytest.mark.asyncio
    async def test_headers_fetches_token_when_missing(self) -> None:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"access_token": "fresh-tok"}
        mock_client_class, _ = _http_client_mock(post_response=resp)
        with patch("httpx.AsyncClient", mock_client_class):
            headers = await self.connector._headers()
        assert headers["Authorization"] == "Bearer fresh-tok"
        assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_headers_reuses_existing_token(self) -> None:
        self.connector._access_token = "cached-tok"
        with patch("httpx.AsyncClient") as mock_cls:
            headers = await self.connector._headers()
        mock_cls.assert_not_called()
        assert headers["Authorization"] == "Bearer cached-tok"

    @pytest.mark.asyncio
    async def test_get_retries_once_on_401(self) -> None:
        self.connector._access_token = "stale-tok"
        unauthorized = MagicMock(status_code=401)
        unauthorized.raise_for_status = MagicMock()
        ok = MagicMock(status_code=200)
        ok.raise_for_status = MagicMock()
        ok.json.return_value = {"value": []}
        token_resp = MagicMock()
        token_resp.raise_for_status = MagicMock()
        token_resp.json.return_value = {"access_token": "new-tok"}

        mock_client_class = MagicMock()
        mock_ctx = AsyncMock()
        mock_ctx.get = AsyncMock(side_effect=[unauthorized, ok])
        mock_ctx.post = AsyncMock(return_value=token_resp)
        mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", mock_client_class):
            result = await self.connector._get("sites")
        assert result == {"value": []}
        assert self.connector._access_token == "new-tok"
        assert mock_ctx.get.await_count == 2

    @pytest.mark.asyncio
    async def test_get_raises_on_non_401_error(self) -> None:
        import httpx

        self.connector._access_token = "tok"
        resp = MagicMock(status_code=500)
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        )
        mock_client_class, _ = _http_client_mock(get_response=resp)
        with patch("httpx.AsyncClient", mock_client_class):
            with pytest.raises(httpx.HTTPStatusError):
                await self.connector._get("sites")

    @pytest.mark.asyncio
    async def test_download_returns_bytes(self) -> None:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.content = b"file-bytes"
        mock_client_class, _ = _http_client_mock(get_response=resp)
        with patch("httpx.AsyncClient", mock_client_class):
            content = await self.connector._download("https://example.com/file")
        assert content == b"file-bytes"

    @pytest.mark.asyncio
    async def test_list_sites_returns_value_list(self) -> None:
        self.connector._access_token = "tok"
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"value": [{"id": "site-1"}]}
        mock_client_class, _ = _http_client_mock(get_response=resp)
        with patch("httpx.AsyncClient", mock_client_class):
            sites = await self.connector.list_sites()
        assert sites == [{"id": "site-1"}]

    @pytest.mark.asyncio
    async def test_get_site_returns_metadata(self) -> None:
        self.connector._access_token = "tok"
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"id": "site-1", "name": "Team Site"}
        mock_client_class, _ = _http_client_mock(get_response=resp)
        with patch("httpx.AsyncClient", mock_client_class):
            site = await self.connector.get_site("site-1")
        assert site["name"] == "Team Site"

    @pytest.mark.asyncio
    async def test_list_files_filters_to_files_only(self) -> None:
        self.connector._access_token = "tok"
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "value": [
                {"id": "1", "name": "doc.txt", "file": {}},
                {"id": "2", "name": "folder", "folder": {}},
            ]
        }
        mock_client_class, mock_ctx = _http_client_mock(get_response=resp)
        with patch("httpx.AsyncClient", mock_client_class):
            files = await self.connector.list_files("site-1")
        assert len(files) == 1
        assert files[0]["name"] == "doc.txt"

    @pytest.mark.asyncio
    async def test_list_files_with_drive_id_uses_drives_path(self) -> None:
        self.connector._access_token = "tok"
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"value": []}
        mock_client_class, mock_ctx = _http_client_mock(get_response=resp)
        with patch("httpx.AsyncClient", mock_client_class):
            await self.connector.list_files("site-1", drive_id="drive-9", folder_path="root")
        url = mock_ctx.get.call_args.args[0]
        assert "drives/drive-9" in url

    @pytest.mark.asyncio
    async def test_list_all_files_delegates_to_list_files(self) -> None:
        with patch.object(
            SharePointConnector, "list_files", AsyncMock(return_value=[{"id": "1"}])
        ) as mock_list:
            files = await self.connector.list_all_files("site-1")
        assert files == [{"id": "1"}]
        mock_list.assert_awaited_once_with("site-1", None, "root")

    @pytest.mark.asyncio
    async def test_download_file_returns_decoded_content(self) -> None:
        self.connector._access_token = "tok"
        meta_resp = MagicMock()
        meta_resp.raise_for_status = MagicMock()
        meta_resp.json.return_value = {
            "@microsoft.graph.downloadUrl": "https://cdn.example.com/f"
        }
        dl_resp = MagicMock()
        dl_resp.raise_for_status = MagicMock()
        dl_resp.content = b"hello world"

        mock_client_class = MagicMock()
        mock_ctx = AsyncMock()
        mock_ctx.get = AsyncMock(side_effect=[meta_resp, dl_resp])
        mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", mock_client_class):
            content = await self.connector.download_file("site-1", "item-1")
        assert content == "hello world"

    @pytest.mark.asyncio
    async def test_download_file_returns_empty_string_without_download_url(self) -> None:
        self.connector._access_token = "tok"
        meta_resp = MagicMock()
        meta_resp.raise_for_status = MagicMock()
        meta_resp.json.return_value = {}
        mock_client_class, _ = _http_client_mock(get_response=meta_resp)
        with patch("httpx.AsyncClient", mock_client_class):
            content = await self.connector.download_file("site-1", "item-1")
        assert content == ""

    @pytest.mark.asyncio
    async def test_list_drives_returns_value(self) -> None:
        self.connector._access_token = "tok"
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"value": [{"id": "drive-1"}]}
        mock_client_class, _ = _http_client_mock(get_response=resp)
        with patch("httpx.AsyncClient", mock_client_class):
            drives = await self.connector.list_drives("site-1")
        assert drives == [{"id": "drive-1"}]

    @pytest.mark.asyncio
    async def test_get_file_metadata_returns_raw_dict(self) -> None:
        self.connector._access_token = "tok"
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"id": "item-1", "size": 42}
        mock_client_class, _ = _http_client_mock(get_response=resp)
        with patch("httpx.AsyncClient", mock_client_class):
            meta = await self.connector.get_file_metadata("site-1", "item-1")
        assert meta["size"] == 42


class TestSharePointSourceConnectorValidateConnection:
    @pytest.mark.asyncio
    async def test_ok_on_successful_token_fetch(self) -> None:
        config = _config({"tenant_id": "t", "client_id": "c", "client_secret": "s"})
        with patch.object(
            SharePointConnector, "get_access_token", AsyncMock(return_value="tok")
        ):
            health = await SharePointSourceConnector().validate_connection(config)
        assert health.ok is True
        assert health.latency_ms is not None

    @pytest.mark.asyncio
    async def test_not_ok_on_auth_failure(self) -> None:
        config = _config({"tenant_id": "t", "client_id": "c", "client_secret": "bad"})
        with patch.object(
            SharePointConnector,
            "get_access_token",
            AsyncMock(side_effect=RuntimeError("invalid client secret")),
        ):
            health = await SharePointSourceConnector().validate_connection(config)
        assert health.ok is False
        assert "invalid client secret" in health.error


class TestSharePointSourceConnectorGetDelta:
    async def _collect(self, agen):
        return [item async for item in agen]

    @pytest.mark.asyncio
    async def test_yields_documents_and_advances_cursor(self) -> None:
        config = _config({"site_id": "site-1", "tenant_id": "t", "client_id": "c", "client_secret": "s"})
        files = [
            {
                "id": "item-1",
                "name": "notes.txt",
                "webUrl": "https://sp/notes.txt",
                "lastModifiedDateTime": "2026-01-02T00:00:00Z",
            }
        ]
        with (
            patch.object(SharePointConnector, "list_all_files", AsyncMock(return_value=files)),
            patch.object(
                SharePointConnector, "download_file", AsyncMock(return_value="file content")
            ),
        ):
            docs = await self._collect(
                SharePointSourceConnector().get_delta(config, None)
            )
        assert len(docs) == 1
        doc, cursor = docs[0]
        assert doc.title == "notes.txt"
        assert doc.content == b"file content"
        assert doc.metadata["sharepoint_item_id"] == "item-1"
        assert cursor == "2026-01-02T00:00:00Z"

    @pytest.mark.asyncio
    async def test_skips_files_without_item_id(self) -> None:
        config = _config({"site_id": "site-1"})
        files = [{"name": "no-id.txt"}]
        with patch.object(SharePointConnector, "list_all_files", AsyncMock(return_value=files)):
            docs = await self._collect(SharePointSourceConnector().get_delta(config, None))
        assert docs == []

    @pytest.mark.asyncio
    async def test_skips_files_older_than_cursor(self) -> None:
        config = _config({"site_id": "site-1"})
        files = [
            {
                "id": "item-1",
                "name": "old.txt",
                "lastModifiedDateTime": "2020-01-01T00:00:00Z",
            }
        ]
        with (
            patch.object(SharePointConnector, "list_all_files", AsyncMock(return_value=files)),
            patch.object(SharePointConnector, "download_file", AsyncMock(return_value="x")),
        ):
            docs = await self._collect(
                SharePointSourceConnector().get_delta(config, "2025-01-01T00:00:00Z")
            )
        assert docs == []

    @pytest.mark.asyncio
    async def test_skips_files_with_empty_content(self) -> None:
        config = _config({"site_id": "site-1"})
        files = [
            {
                "id": "item-1",
                "name": "empty.txt",
                "lastModifiedDateTime": "2026-01-01T00:00:00Z",
            }
        ]
        with (
            patch.object(SharePointConnector, "list_all_files", AsyncMock(return_value=files)),
            patch.object(SharePointConnector, "download_file", AsyncMock(return_value="   ")),
        ):
            docs = await self._collect(SharePointSourceConnector().get_delta(config, None))
        assert docs == []

    @pytest.mark.asyncio
    async def test_cursor_defaults_to_empty_when_none_and_no_modified_dates(self) -> None:
        config = _config({"site_id": "site-1"})
        files = [{"id": "item-1", "name": "x.txt"}]
        with (
            patch.object(SharePointConnector, "list_all_files", AsyncMock(return_value=files)),
            patch.object(SharePointConnector, "download_file", AsyncMock(return_value="content")),
        ):
            docs = await self._collect(SharePointSourceConnector().get_delta(config, None))
        assert len(docs) == 1
        _, cursor = docs[0]
        assert cursor == ""

    def test_client_builds_from_connection_config(self) -> None:
        config = _config({"tenant_id": "t1", "client_id": "c1", "client_secret": "s1"})
        client = SharePointSourceConnector()._client(config)
        assert client._tenant_id == "t1"
        assert client._client_id == "c1"
        assert client._client_secret == "s1"
