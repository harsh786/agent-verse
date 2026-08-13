"""Tests for SharePoint connector."""
from __future__ import annotations

import pytest
from app.ingestion.connectors.sharepoint_connector import SharePointConnector


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
        import httpx
        from unittest.mock import AsyncMock, patch, MagicMock

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
