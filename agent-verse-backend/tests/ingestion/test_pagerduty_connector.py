"""Tests for PagerDutyConnector — incident ingestion via PagerDuty REST API."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.connectors.pagerduty_connector import PagerDutyConnector
from app.ingestion.source_config import SourceConfig, SourceFamily


def _config(**cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id="src-pd",
        tenant_id="t1",
        name="pagerduty-src",
        family=SourceFamily.OBSERVABILITY,
        source_type="pagerduty",
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
    resp.json = MagicMock(return_value={"user": {"name": "Bob", "email": "bob@example.com"}})
    client = _mock_async_client(get_side_effect=[resp])

    with patch("httpx.AsyncClient", return_value=client):
        health = await PagerDutyConnector().validate_connection(_config(api_token="tok"))

    assert health.ok is True
    assert health.metadata == {"user": "Bob", "email": "bob@example.com"}
    _, kwargs = client.get.call_args
    assert kwargs["headers"]["Authorization"] == "Token token=tok"


@pytest.mark.asyncio
async def test_validate_connection_error() -> None:
    resp = MagicMock()
    resp.raise_for_status = MagicMock(side_effect=RuntimeError("401"))
    client = _mock_async_client(get_side_effect=[resp])

    with patch("httpx.AsyncClient", return_value=client):
        health = await PagerDutyConnector().validate_connection(_config())

    assert health.ok is False
    assert "401" in health.error


@pytest.mark.asyncio
async def test_get_delta_paginates_until_more_is_false() -> None:
    page1 = MagicMock()
    page1.is_success = True
    page1.json = MagicMock(
        return_value={
            "incidents": [
                {
                    "id": "i1",
                    "created_at": "2026-01-01T00:00:00Z",
                    "title": "Disk full",
                    "status": "triggered",
                    "urgency": "high",
                    "service": {"summary": "web"},
                    "html_url": "https://pd/i1",
                },
                {
                    "id": "i2",
                    "created_at": "2026-01-02T00:00:00Z",
                    "title": "CPU high",
                    "status": "acknowledged",
                    "urgency": "low",
                    "service": {"summary": "api"},
                    "html_url": "https://pd/i2",
                },
            ],
            "more": False,
        }
    )
    client = _mock_async_client(get_side_effect=[page1])

    cfg = _config(api_token="tok", batch_size=50)
    with patch("httpx.AsyncClient", return_value=client):
        docs = [d async for d in PagerDutyConnector().get_delta(cfg, None)]

    assert len(docs) == 2
    doc0, cursor0 = docs[0]
    assert b"Disk full" in doc0.content
    assert doc0.source_url == "https://pd/i1"
    doc1, cursor1 = docs[1]
    assert cursor1 == "2026-01-02T00:00:00Z"
    client.get.assert_called_once()
    _, kwargs = client.get.call_args
    assert kwargs["params"]["limit"] == 50


@pytest.mark.asyncio
async def test_get_delta_stops_on_empty_page() -> None:
    page1 = MagicMock()
    page1.is_success = True
    page1.json = MagicMock(
        return_value={
            "incidents": [
                {
                    "id": "i1",
                    "created_at": "2026-01-01T00:00:00Z",
                    "title": "One",
                    "status": "triggered",
                    "urgency": "high",
                    "service": {},
                }
            ],
            "more": True,
        }
    )
    page2 = MagicMock()
    page2.is_success = True
    page2.json = MagicMock(return_value={"incidents": [], "more": True})

    client = _mock_async_client(get_side_effect=[page1, page2])
    cfg = _config(api_token="tok")
    with patch("httpx.AsyncClient", return_value=client):
        docs = [d async for d in PagerDutyConnector().get_delta(cfg, "2025-12-01T00:00:00Z")]

    assert len(docs) == 1
    assert client.get.call_count == 2


@pytest.mark.asyncio
async def test_get_delta_breaks_on_failed_response() -> None:
    fail_resp = MagicMock()
    fail_resp.is_success = False
    client = _mock_async_client(get_side_effect=[fail_resp])

    cfg = _config(api_token="tok")
    with patch("httpx.AsyncClient", return_value=client):
        docs = [d async for d in PagerDutyConnector().get_delta(cfg, None)]

    assert docs == []


@pytest.mark.asyncio
async def test_get_delta_skips_non_incident_ingest_types() -> None:
    cfg = _config(api_token="tok", ingest_types=["alerts"])
    client = _mock_async_client(get_side_effect=[])
    with patch("httpx.AsyncClient", return_value=client):
        docs = [d async for d in PagerDutyConnector().get_delta(cfg, None)]

    assert docs == []
    client.get.assert_not_called()
