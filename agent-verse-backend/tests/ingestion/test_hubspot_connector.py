"""Tests for HubSpotConnector — CRM object ingestion via HubSpot REST API v3."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.connectors.hubspot_connector import HubSpotConnector
from app.ingestion.source_config import SourceConfig, SourceFamily


def _config(**cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id="src-hubspot",
        tenant_id="t1",
        name="hubspot-src",
        family=SourceFamily.CRM_ERP,
        source_type="hubspot",
        connection_config=cc,
    )


def _mock_async_client(get_side_effect=None) -> AsyncMock:
    client = AsyncMock()
    client.get = AsyncMock(side_effect=get_side_effect)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


@pytest.mark.asyncio
async def test_validate_connection_ok() -> None:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    client = _mock_async_client(get_side_effect=[resp])

    with patch("httpx.AsyncClient", return_value=client):
        health = await HubSpotConnector().validate_connection(_config(access_token="tok"))

    assert health.ok is True
    assert health.metadata == {"api": "hubspot"}
    _, kwargs = client.get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_validate_connection_error() -> None:
    resp = MagicMock()
    resp.raise_for_status = MagicMock(side_effect=RuntimeError("bad token"))
    client = _mock_async_client(get_side_effect=[resp])

    with patch("httpx.AsyncClient", return_value=client):
        health = await HubSpotConnector().validate_connection(_config())

    assert health.ok is False
    assert "bad token" in health.error


@pytest.mark.asyncio
async def test_get_delta_paginates_single_object_type() -> None:
    page1 = MagicMock()
    page1.is_success = True
    page1.json = MagicMock(
        return_value={
            "results": [
                {
                    "id": "1",
                    "properties": {
                        "hs_lastmodifieddate": "2026-01-01T00:00:00Z",
                        "email": "a@example.com",
                    },
                }
            ],
            "paging": {"next": {"after": "cursor123"}},
        }
    )
    page2 = MagicMock()
    page2.is_success = True
    page2.json = MagicMock(
        return_value={
            "results": [
                {
                    "id": "2",
                    "properties": {"hs_lastmodifieddate": "2026-01-02T00:00:00Z"},
                }
            ],
            "paging": {},
        }
    )
    client = _mock_async_client(get_side_effect=[page1, page2])

    cfg = _config(access_token="tok", object_types=["contacts"], batch_size=2)
    with patch("httpx.AsyncClient", return_value=client):
        docs = [d async for d in HubSpotConnector().get_delta(cfg, None)]

    assert len(docs) == 2
    doc0, cursor0 = docs[0]
    assert b"HubSpot Contact: 1" in doc0.content
    assert cursor0 == "2026-01-01T00:00:00Z"
    doc1, cursor1 = docs[1]
    assert cursor1 == "2026-01-02T00:00:00Z"
    assert client.get.call_count == 2
    # second call should carry the paging cursor.
    _, kwargs = client.get.call_args_list[1]
    assert kwargs["params"]["after"] == "cursor123"


@pytest.mark.asyncio
async def test_get_delta_iterates_multiple_object_types() -> None:
    contacts_page = MagicMock()
    contacts_page.is_success = True
    contacts_page.json = MagicMock(
        return_value={
            "results": [{"id": "c1", "properties": {"hs_lastmodifieddate": "2026-01-01"}}],
            "paging": {},
        }
    )
    deals_page = MagicMock()
    deals_page.is_success = True
    deals_page.json = MagicMock(
        return_value={
            "results": [{"id": "d1", "properties": {"hs_lastmodifieddate": "2026-01-05"}}],
            "paging": {},
        }
    )
    client = _mock_async_client(get_side_effect=[contacts_page, deals_page])

    cfg = _config(access_token="tok", object_types=["contacts", "deals"])
    with patch("httpx.AsyncClient", return_value=client):
        docs = [d async for d in HubSpotConnector().get_delta(cfg, None)]

    assert len(docs) == 2
    assert "HubSpot Contact" in docs[0][0].content.decode()
    assert "HubSpot Deal" in docs[1][0].content.decode()


@pytest.mark.asyncio
async def test_get_delta_initial_cursor_sets_after_param() -> None:
    page = MagicMock()
    page.is_success = True
    page.json = MagicMock(
        return_value={
            "results": [{"id": "1", "properties": {"hs_lastmodifieddate": "2026-01-03"}}],
            "paging": {},
        }
    )
    client = _mock_async_client(get_side_effect=[page])

    cfg = _config(access_token="tok", object_types=["contacts"])
    with patch("httpx.AsyncClient", return_value=client):
        docs = [
            d async for d in HubSpotConnector().get_delta(cfg, "existing-paging-token")
        ]

    assert len(docs) == 1
    _, kwargs = client.get.call_args_list[0]
    assert kwargs["params"]["after"] == "existing-paging-token"


@pytest.mark.asyncio
async def test_get_delta_breaks_on_failed_response() -> None:
    fail_resp = MagicMock()
    fail_resp.is_success = False
    client = _mock_async_client(get_side_effect=[fail_resp])

    cfg = _config(access_token="tok", object_types=["contacts"])
    with patch("httpx.AsyncClient", return_value=client):
        docs = [d async for d in HubSpotConnector().get_delta(cfg, None)]

    assert docs == []
