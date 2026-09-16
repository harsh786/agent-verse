"""Tests for ClickHouseConnector — analytics DB ingestion via clickhouse-connect.

clickhouse-connect is not installed in the test environment, so the module is
faked via sys.modules injection.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.ingestion.connectors.clickhouse_connector import ClickHouseConnector
from app.ingestion.source_config import SourceConfig, SourceFamily


def _config(**cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id="src-ch",
        tenant_id="t1",
        name="clickhouse-src",
        family=SourceFamily.OLAP_DATABASE,
        source_type="clickhouse",
        connection_config=cc,
    )


def _fake_clickhouse_module(client: MagicMock) -> MagicMock:
    mod = MagicMock()
    mod.get_client = MagicMock(return_value=client)
    return mod


@pytest.mark.asyncio
async def test_validate_connection_ok() -> None:
    client = MagicMock()
    result = MagicMock()
    result.first_row = ["23.8.1"]
    client.query = MagicMock(return_value=result)
    fake_mod = _fake_clickhouse_module(client)

    with patch.dict("sys.modules", {"clickhouse_connect": fake_mod}):
        health = await ClickHouseConnector().validate_connection(
            _config(host="ch.local", port=8123)
        )

    assert health.ok is True
    assert health.metadata == {"version": "23.8.1"}
    client.query.assert_called_once_with("SELECT version()")


@pytest.mark.asyncio
async def test_validate_connection_not_installed() -> None:
    with patch.dict("sys.modules", {"clickhouse_connect": None}):
        health = await ClickHouseConnector().validate_connection(_config())
    assert health.ok is False
    assert "not installed" in health.error


@pytest.mark.asyncio
async def test_validate_connection_error() -> None:
    fake_mod = MagicMock()
    fake_mod.get_client = MagicMock(side_effect=RuntimeError("conn refused"))
    with patch.dict("sys.modules", {"clickhouse_connect": fake_mod}):
        health = await ClickHouseConnector().validate_connection(_config())
    assert health.ok is False
    assert "conn refused" in health.error


@pytest.mark.asyncio
async def test_get_delta_not_installed_yields_nothing() -> None:
    with patch.dict("sys.modules", {"clickhouse_connect": None}):
        docs = [d async for d in ClickHouseConnector().get_delta(_config(), None)]
    assert docs == []


@pytest.mark.asyncio
async def test_get_delta_table_mode_builds_query_and_transforms_rows() -> None:
    client = MagicMock()
    result = MagicMock()
    result.column_names = ["ts", "val"]
    result.result_rows = [("2026-01-01", 5), ("2026-01-02", 6)]
    client.query = MagicMock(return_value=result)
    fake_mod = _fake_clickhouse_module(client)

    cfg = _config(table="events", cursor_column="ts", batch_size=10)
    with patch.dict("sys.modules", {"clickhouse_connect": fake_mod}):
        docs = [d async for d in ClickHouseConnector().get_delta(cfg, None)]

    client.query.assert_called_once_with("SELECT * FROM events ORDER BY ts LIMIT 10")
    assert len(docs) == 2
    doc0, cursor0 = docs[0]
    assert b"ts: 2026-01-01" in doc0.content
    assert cursor0 == "2026-01-01"
    doc1, cursor1 = docs[1]
    assert cursor1 == "2026-01-02"


@pytest.mark.asyncio
async def test_get_delta_table_mode_with_cursor_filters() -> None:
    client = MagicMock()
    result = MagicMock()
    result.column_names = ["ts"]
    result.result_rows = [("2026-02-01",)]
    client.query = MagicMock(return_value=result)
    fake_mod = _fake_clickhouse_module(client)

    cfg = _config(table="events", cursor_column="ts", batch_size=5)
    with patch.dict("sys.modules", {"clickhouse_connect": fake_mod}):
        docs = [d async for d in ClickHouseConnector().get_delta(cfg, "2026-01-15")]

    client.query.assert_called_once_with(
        "SELECT * FROM events WHERE ts > '2026-01-15' ORDER BY ts LIMIT 5"
    )
    assert len(docs) == 1


@pytest.mark.asyncio
async def test_get_delta_custom_query_replaces_cursor_placeholder() -> None:
    client = MagicMock()
    result = MagicMock()
    result.column_names = ["x"]
    result.result_rows = [(1,)]
    client.query = MagicMock(return_value=result)
    fake_mod = _fake_clickhouse_module(client)

    cfg = _config(query="SELECT * FROM t WHERE x > {cursor}")
    with patch.dict("sys.modules", {"clickhouse_connect": fake_mod}):
        docs = [d async for d in ClickHouseConnector().get_delta(cfg, "42")]

    client.query.assert_called_once_with("SELECT * FROM t WHERE x > 42")
    assert len(docs) == 1
