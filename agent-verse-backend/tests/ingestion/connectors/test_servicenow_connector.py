"""Tests for ServiceNowConnector — validate_connection, get_delta pagination,
multi-table iteration, and error handling. httpx is mocked throughout."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.ingestion.connectors.servicenow_connector import ServiceNowConnector
from app.ingestion.source_config import SourceConfig


def _make_config(conn_config: dict | None = None) -> SourceConfig:
    return SourceConfig(
        source_id="src-sn",
        tenant_id="t1",
        name="Test SNow",
        family="support",
        source_type="servicenow",
        enabled=True,
        connection_config=conn_config or {"instance": "acme", "username": "u", "password": "p"},
    )


def _mock_async_client(get_impl):
    mock_client = AsyncMock()
    mock_client.get = get_impl
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


class TestValidateConnection:
    async def test_success(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()

        async def get(*a, **kw):
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            connector = ServiceNowConnector()
            health = await connector.validate_connection(_make_config())
        assert health.ok is True
        assert health.metadata["instance"] == "acme"

    async def test_failure_returns_error(self):
        async def get(*a, **kw):
            raise ConnectionError("timed out")

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            connector = ServiceNowConnector()
            health = await connector.validate_connection(_make_config())
        assert health.ok is False
        assert "timed out" in health.error


class TestGetDelta:
    async def test_single_table_single_page(self):
        record = {
            "sys_id": "abc123",
            "number": "INC0001",
            "short_description": "Server down",
            "description": "The server is down",
            "sys_updated_on": "2026-01-01 10:00:00",
        }
        resp = MagicMock()
        resp.is_success = True
        resp.json = MagicMock(return_value={"result": [record]})

        async def get(*a, **kw):
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            connector = ServiceNowConnector()
            config = _make_config({"instance": "acme", "tables": ["incident"], "batch_size": 100})
            results = [d async for d in connector.get_delta(config, None)]

        assert len(results) == 1
        doc, cursor = results[0]
        assert "INC0001" in doc.content.decode()
        assert doc.metadata["table"] == "incident"
        assert cursor == "2026-01-01 10:00:00"

    async def test_pagination_across_pages(self):
        page1_records = [
            {
                "sys_id": f"id{i}",
                "number": f"INC000{i}",
                "short_description": "d",
                "sys_updated_on": f"2026-01-0{i} 00:00:00",
            }
            for i in range(1, 3)  # exactly batch_size=2 -> triggers next page
        ]
        page2_records: list = []
        call_count = 0

        async def get(url, params=None, auth=None, headers=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.is_success = True
            resp.json = MagicMock(
                return_value={"result": page1_records if call_count == 1 else page2_records}
            )
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            connector = ServiceNowConnector()
            config = _make_config({"instance": "acme", "tables": ["incident"], "batch_size": 2})
            results = [d async for d in connector.get_delta(config, None)]

        assert len(results) == 2
        assert call_count == 2  # second page fetched because len(records) == batch_size

    async def test_multiple_tables_iterated(self):
        async def get(url, params=None, auth=None, headers=None):
            resp = MagicMock()
            resp.is_success = True
            table_name = url.rsplit("/", 1)[-1]
            resp.json = MagicMock(
                return_value={
                    "result": [
                        {
                            "sys_id": "x1",
                            "number": "N1",
                            "short_description": table_name,
                            "sys_updated_on": "2026-02-01 00:00:00",
                        }
                    ]
                }
            )
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            connector = ServiceNowConnector()
            config = _make_config(
                {"instance": "acme", "tables": ["incident", "problem"], "batch_size": 100}
            )
            results = [d async for d in connector.get_delta(config, None)]

        tables_seen = {doc.metadata["table"] for doc, _ in results}
        assert tables_seen == {"incident", "problem"}

    async def test_failed_response_breaks_loop(self):
        async def get(*a, **kw):
            resp = MagicMock()
            resp.is_success = False
            resp.status_code = 500
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            connector = ServiceNowConnector()
            config = _make_config({"instance": "acme", "tables": ["incident"]})
            results = [d async for d in connector.get_delta(config, None)]
        assert results == []

    async def test_empty_records_breaks_loop(self):
        async def get(*a, **kw):
            resp = MagicMock()
            resp.is_success = True
            resp.json = MagicMock(return_value={"result": []})
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            connector = ServiceNowConnector()
            config = _make_config({"instance": "acme", "tables": ["incident"]})
            results = [d async for d in connector.get_delta(config, "2026-01-01 00:00:00")]
        assert results == []

    async def test_full_page_without_updated_timestamp_stops_pagination(self):
        """A full page (== batch_size) whose last record lacks sys_updated_on
        cannot compute the next page's filter, so pagination must stop."""
        full_page = [
            {"sys_id": "id1", "number": "N1", "short_description": "a", "sys_updated_on": "2026-01-01"},
            {"sys_id": "id2", "number": "N2", "short_description": "b", "sys_updated_on": ""},
        ]
        call_count = 0

        async def get(url, params=None, auth=None, headers=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.is_success = True
            resp.json = MagicMock(return_value={"result": full_page})
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            connector = ServiceNowConnector()
            config = _make_config({"instance": "acme", "tables": ["incident"], "batch_size": 2})
            results = [d async for d in connector.get_delta(config, None)]

        assert len(results) == 2
        assert call_count == 1  # no second page fetched — last_ts was empty

    async def test_cursor_passed_as_query_filter(self):
        seen_params = {}

        async def get(url, params=None, auth=None, headers=None):
            seen_params.update(params or {})
            resp = MagicMock()
            resp.is_success = True
            resp.json = MagicMock(return_value={"result": []})
            return resp

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = _mock_async_client(get)
            connector = ServiceNowConnector()
            config = _make_config({"instance": "acme", "tables": ["incident"]})
            _ = [d async for d in connector.get_delta(config, "2026-01-01 00:00:00")]

        assert seen_params.get("sysparm_query") == "sys_updated_on>2026-01-01 00:00:00"


def test_source_type_and_registration():
    from app.ingestion.connector_registry import get_connector

    assert ServiceNowConnector().source_type == "servicenow"
    assert get_connector("servicenow") is ServiceNowConnector
