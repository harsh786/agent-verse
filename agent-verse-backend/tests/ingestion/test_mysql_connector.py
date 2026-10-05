"""Tests for MySQLConnector — MySQL/MariaDB ingestion via pymysql/MySQLdb.

Neither pymysql nor MySQLdb is installed in the test environment, so both
driver modules are faked via sys.modules injection to exercise the primary
and fallback connection paths.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.ingestion.connectors.mysql_connector import MySQLConnector
from app.ingestion.source_config import SourceConfig, SourceFamily


def _config(**cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id="src-mysql",
        tenant_id="t1",
        name="mysql-src",
        family=SourceFamily.OLTP_DATABASE,
        source_type="mysql",
        connection_config={"host": "db.test", **cc},
    )


def _fake_pymysql(conn: MagicMock) -> MagicMock:
    mod = MagicMock()
    mod.connect = MagicMock(return_value=conn)
    mod.cursors.DictCursor = MagicMock()
    return mod


@pytest.mark.asyncio
async def test_validate_connection_ok() -> None:
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone = MagicMock(return_value=("8.0.30",))
    conn.cursor = MagicMock(return_value=cur)

    with patch.object(MySQLConnector, "_connect", return_value=conn):
        health = await MySQLConnector().validate_connection(_config())

    assert health.ok is True
    assert health.metadata == {"version": "8.0.30"}
    cur.execute.assert_called_once_with("SELECT VERSION()")
    conn.close.assert_called_once()


@pytest.mark.asyncio
async def test_validate_connection_import_error() -> None:
    with patch.object(MySQLConnector, "_connect", side_effect=ImportError("no driver")):
        health = await MySQLConnector().validate_connection(_config())
    assert health.ok is False
    assert "MySQL driver not installed" in health.error


@pytest.mark.asyncio
async def test_validate_connection_error() -> None:
    with patch.object(MySQLConnector, "_connect", side_effect=RuntimeError("conn refused")):
        health = await MySQLConnector().validate_connection(_config())
    assert health.ok is False
    assert "conn refused" in health.error


def test_connect_uses_pymysql_when_available() -> None:
    conn = MagicMock()
    fake_pymysql = _fake_pymysql(conn)

    with patch.dict("sys.modules", {"pymysql": fake_pymysql}):
        result = MySQLConnector._connect(
            {"host": "db", "port": 3306, "username": "u", "password": "p", "database": "d"}
        )

    assert result is conn
    fake_pymysql.connect.assert_called_once_with(
        host="db", port=3306, user="u", password="p", database="d",
        connect_timeout=10, read_timeout=300, charset="utf8mb4",
        cursorclass=fake_pymysql.cursors.DictCursor,
    )


def test_connect_falls_back_to_mysqldb_when_pymysql_missing() -> None:
    conn = MagicMock()
    fake_mysqldb = MagicMock()
    fake_mysqldb.connect = MagicMock(return_value=conn)
    fake_mysqldb.cursors.DictCursor = MagicMock()

    with patch.dict("sys.modules", {"pymysql": None, "MySQLdb": fake_mysqldb}):
        result = MySQLConnector._connect(
            {"host": "db", "port": 3306, "username": "u", "password": "p", "database": "d"}
        )

    assert result is conn
    fake_mysqldb.connect.assert_called_once_with(
        host="db", port=3306, user="u", passwd="p", db="d",
        cursorclass=fake_mysqldb.cursors.DictCursor, connect_timeout=10, charset="utf8mb4",
    )


@pytest.mark.asyncio
async def test_get_delta_table_mode_builds_query_and_transforms_rows() -> None:
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall = MagicMock(
        return_value=[
            {"id": 1, "updated_at": "2026-01-01", "memo": None},
            {"id": 2, "updated_at": "2026-01-02", "memo": None},
        ]
    )
    conn.cursor = MagicMock(return_value=cur)

    cfg = _config(table="events", cursor_column="updated_at", batch_size=100,
                  primary_keys={"events": ["id"]})
    with patch.object(MySQLConnector, "_connect", return_value=conn):
        docs = [d async for d in MySQLConnector().get_delta(cfg, None)]

    assert len(docs) == 2
    doc0, cursor0 = docs[0]
    assert b"id: 1" in doc0.content
    assert b"None" not in doc0.content  # NULLs are left out
    # P1b-7: a per-table keyset position (cursor value + primary key).
    assert json.loads(cursor0)["tables"]["events"]["k"] == [{"t": "int", "v": "1"}]
    _doc1, cursor1 = docs[1]
    assert json.loads(cursor1)["tables"]["events"]["c"] == {"t": "str", "v": "2026-01-02"}
    conn.close.assert_called_once()
    cur.execute.assert_any_call(
        "SELECT * FROM `events` WHERE `updated_at` IS NOT NULL ORDER BY `updated_at`, `id` "
        "LIMIT 100",
        (),
    )


@pytest.mark.asyncio
async def test_get_delta_table_mode_with_a_legacy_cursor() -> None:
    import datetime

    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall = MagicMock(return_value=[])
    conn.cursor = MagicMock(return_value=cur)

    cfg = _config(table="events", cursor_column="updated_at", batch_size=50,
                  primary_keys={"events": ["id"]})
    with patch.object(MySQLConnector, "_connect", return_value=conn):
        docs = [d async for d in MySQLConnector().get_delta(cfg, "2026-01-01")]

    assert docs == []
    cur.execute.assert_any_call(
        "SELECT * FROM `events` WHERE `updated_at` > %s ORDER BY `updated_at`, `id` LIMIT 50",
        (datetime.datetime(2026, 1, 1),),
    )


@pytest.mark.asyncio
async def test_get_delta_custom_query_without_placeholder() -> None:
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall = MagicMock(return_value=[{"x": 1}])
    conn.cursor = MagicMock(return_value=cur)

    cfg = _config(query="SELECT * FROM t")
    with patch.object(MySQLConnector, "_connect", return_value=conn):
        docs = [d async for d in MySQLConnector().get_delta(cfg, "10")]

    assert len(docs) == 1
    cur.execute.assert_any_call("SELECT * FROM t", ())
