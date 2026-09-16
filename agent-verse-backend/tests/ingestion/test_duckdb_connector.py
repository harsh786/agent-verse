"""Tests for DuckDBConnector — local analytics DB ingestion via duckdb.

duckdb is not installed in the test environment, so the module is faked via
sys.modules injection to exercise both the "installed" and "not installed"
branches.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.ingestion.connectors.duckdb_connector import DuckDBConnector
from app.ingestion.source_config import SourceConfig, SourceFamily


def _config(**cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id="src-duckdb",
        tenant_id="t1",
        name="duckdb-src",
        family=SourceFamily.OLAP_DATABASE,
        source_type="duckdb",
        connection_config=cc,
    )


def _fake_duckdb_module(con: MagicMock) -> MagicMock:
    mod = MagicMock()
    mod.connect = MagicMock(return_value=con)
    return mod


@pytest.mark.asyncio
async def test_validate_connection_ok() -> None:
    con = MagicMock()
    con.execute = MagicMock()
    con.close = MagicMock()
    fake_mod = _fake_duckdb_module(con)

    with patch.dict("sys.modules", {"duckdb": fake_mod}):
        health = await DuckDBConnector().validate_connection(_config(database=":memory:"))

    assert health.ok is True
    assert health.metadata == {"database": ":memory:"}
    con.execute.assert_called_once_with("SELECT 1")
    con.close.assert_called_once()


@pytest.mark.asyncio
async def test_validate_connection_not_installed() -> None:
    with patch.dict("sys.modules", {"duckdb": None}):
        health = await DuckDBConnector().validate_connection(_config())
    assert health.ok is False
    assert "not installed" in health.error


@pytest.mark.asyncio
async def test_validate_connection_error() -> None:
    fake_mod = MagicMock()
    fake_mod.connect = MagicMock(side_effect=RuntimeError("boom"))
    with patch.dict("sys.modules", {"duckdb": fake_mod}):
        health = await DuckDBConnector().validate_connection(_config())
    assert health.ok is False
    assert "boom" in health.error


@pytest.mark.asyncio
async def test_get_delta_not_installed_yields_nothing() -> None:
    with patch.dict("sys.modules", {"duckdb": None}):
        docs = [d async for d in DuckDBConnector().get_delta(_config(), None)]
    assert docs == []


@pytest.mark.asyncio
async def test_get_delta_file_mode_builds_query_and_yields_docs() -> None:
    con = MagicMock()
    result = MagicMock()
    result.description = [("id",), ("name",)]
    result.fetchall = MagicMock(return_value=[(6, "foo"), (7, "bar")])
    con.execute = MagicMock(return_value=result)
    fake_mod = _fake_duckdb_module(con)

    cfg = _config(
        database=":memory:",
        mode="file",
        file="data.parquet",
        cursor_column="id",
        batch_size=500,
    )

    with patch.dict("sys.modules", {"duckdb": fake_mod}):
        docs = [d async for d in DuckDBConnector().get_delta(cfg, "5")]

    con.execute.assert_called_once_with(
        "SELECT * FROM 'data.parquet' WHERE id > '5' LIMIT 500"
    )
    assert len(docs) == 2
    doc0, cursor0 = docs[0]
    assert b"id: 6" in doc0.content
    assert b"name: foo" in doc0.content
    assert doc0.content_type == "text/plain"
    assert cursor0 == "6"
    doc1, cursor1 = docs[1]
    assert cursor1 == "7"
    con.close.assert_called_once()


@pytest.mark.asyncio
async def test_get_delta_query_mode_replaces_cursor_placeholder() -> None:
    con = MagicMock()
    result = MagicMock()
    result.description = [("x",)]
    result.fetchall = MagicMock(return_value=[(1,)])
    con.execute = MagicMock(return_value=result)
    fake_mod = _fake_duckdb_module(con)

    cfg = _config(query="SELECT * FROM t WHERE x > {cursor}")

    with patch.dict("sys.modules", {"duckdb": fake_mod}):
        docs = [d async for d in DuckDBConnector().get_delta(cfg, "10")]

    con.execute.assert_called_once_with("SELECT * FROM t WHERE x > 10")
    assert len(docs) == 1


@pytest.mark.asyncio
async def test_get_delta_query_mode_default_query() -> None:
    con = MagicMock()
    result = MagicMock()
    result.description = [("1",)]
    result.fetchall = MagicMock(return_value=[])
    con.execute = MagicMock(return_value=result)
    fake_mod = _fake_duckdb_module(con)

    with patch.dict("sys.modules", {"duckdb": fake_mod}):
        docs = [d async for d in DuckDBConnector().get_delta(_config(), None)]

    con.execute.assert_called_once_with("SELECT 1")
    assert docs == []
