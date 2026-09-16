"""Tests for SnowflakeConnector — query/stream mode ingestion.

``snowflake-connector-python`` is not installed in this environment, so we
inject fake ``snowflake`` / ``snowflake.connector`` modules into
``sys.modules`` to exercise the success paths, and rely on the real
ImportError for the "not installed" branch.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from app.ingestion.connectors.snowflake_connector import SnowflakeConnector, _row_to_text
from app.ingestion.source_config import SourceConfig


def _make_config(conn_config: dict | None = None) -> SourceConfig:
    return SourceConfig(
        source_id="src-sf",
        tenant_id="t1",
        name="Test Snowflake",
        family="olap_database",
        source_type="snowflake",
        connection_config=conn_config or {},
    )


async def _collect(agen) -> list:
    out = []
    async for item in agen:
        out.append(item)
    return out


class _FakeCursor:
    def __init__(self, rows=None, fetchone_val=None):
        self.rows = rows or []
        self._fetchone_val = fetchone_val
        self.executed_sql: list[str] = []

    def execute(self, sql):
        self.executed_sql.append(sql)

    def fetchone(self):
        return self._fetchone_val

    def __iter__(self):
        return iter(self.rows)


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self, *_args, **_kwargs):
        return self._cursor

    def close(self):
        self.closed = True


def _install_fake_snowflake(connect_return=None, connect_side_effect=None):
    fake_connector_mod = MagicMock()
    fake_connector_mod.DictCursor = object()
    if connect_side_effect is not None:
        fake_connector_mod.connect = MagicMock(side_effect=connect_side_effect)
    else:
        fake_connector_mod.connect = MagicMock(return_value=connect_return)
    fake_snowflake_pkg = MagicMock()
    fake_snowflake_pkg.connector = fake_connector_mod
    return patch.dict(
        sys.modules,
        {"snowflake": fake_snowflake_pkg, "snowflake.connector": fake_connector_mod},
    )


class TestRowToText:
    def test_skips_none_values(self):
        text = _row_to_text({"A": 1, "B": None, "C": "x"})
        assert "A: 1" in text and "C: x" in text and "B" not in text


class TestValidateConnection:
    async def test_success(self):
        cursor = _FakeCursor(fetchone_val=("8.1.0",))
        conn = _FakeConn(cursor)
        with _install_fake_snowflake(connect_return=conn):
            result = await SnowflakeConnector().validate_connection(
                _make_config({"user": "u", "account": "acme"})
            )
        assert result.ok is True
        assert result.metadata["version"] == "8.1.0"
        assert conn.closed is True

    async def test_import_error(self):
        with patch.dict(sys.modules, {"snowflake": None, "snowflake.connector": None}):
            result = await SnowflakeConnector().validate_connection(_make_config())
        assert result.ok is False
        assert "not installed" in result.error

    async def test_connect_exception(self):
        with _install_fake_snowflake(connect_side_effect=Exception("auth failed")):
            result = await SnowflakeConnector().validate_connection(_make_config())
        assert result.ok is False
        assert "auth failed" in result.error


class TestGetDelta:
    async def test_no_library_yields_nothing(self):
        with patch.dict(sys.modules, {"snowflake": None, "snowflake.connector": None}):
            docs = await _collect(SnowflakeConnector().get_delta(_make_config(), None))
        assert docs == []

    async def test_query_mode_yields_rows(self):
        rows = [
            {"ID": 1, "NAME": "Alice", "UPDATED_AT": "2026-01-01T00:00:00"},
            {"ID": 2, "NAME": "Bob", "UPDATED_AT": "2026-01-02T00:00:00"},
        ]
        cursor = _FakeCursor(rows=rows)
        conn = _FakeConn(cursor)
        config = _make_config(
            {"mode": "query", "query": "SELECT * FROM USERS", "cursor_column": "UPDATED_AT"}
        )
        with _install_fake_snowflake(connect_return=conn):
            docs = await _collect(SnowflakeConnector().get_delta(config, None))

        assert len(docs) == 2
        doc0, cursor0 = docs[0]
        assert "Alice" in doc0.content.decode()
        assert cursor0 == "2026-01-01T00:00:00"
        _doc1, cursor1 = docs[1]
        assert cursor1 == "2026-01-02T00:00:00"
        assert conn.closed is True

    async def test_query_mode_with_cursor_placeholder(self):
        cursor = _FakeCursor(rows=[])
        conn = _FakeConn(cursor)
        config = _make_config(
            {"mode": "query", "query": "SELECT * FROM T WHERE ts > '{cursor}'"}
        )
        with _install_fake_snowflake(connect_return=conn):
            await _collect(SnowflakeConnector().get_delta(config, "2026-01-01"))
        assert cursor.executed_sql == ["SELECT * FROM T WHERE ts > '2026-01-01'"]

    async def test_query_mode_appends_where_when_no_placeholder(self):
        cursor = _FakeCursor(rows=[])
        conn = _FakeConn(cursor)
        config = _make_config(
            {"mode": "query", "query": "SELECT * FROM T", "cursor_column": "TS", "batch_size": 50}
        )
        with _install_fake_snowflake(connect_return=conn):
            await _collect(SnowflakeConnector().get_delta(config, "2026-01-01"))
        assert cursor.executed_sql == ["SELECT * FROM T WHERE TS > '2026-01-01' LIMIT 50"]

    async def test_stream_mode_builds_stream_query(self):
        cursor = _FakeCursor(rows=[])
        conn = _FakeConn(cursor)
        config = _make_config({"mode": "stream", "stream_name": "MY_STREAM", "batch_size": 10})
        with _install_fake_snowflake(connect_return=conn):
            await _collect(SnowflakeConnector().get_delta(config, None))
        assert cursor.executed_sql == ["SELECT * FROM MY_STREAM LIMIT 10"]

    async def test_stream_mode_with_cursor_filters_on_cursor_column(self):
        cursor = _FakeCursor(rows=[])
        conn = _FakeConn(cursor)
        config = _make_config(
            {
                "mode": "stream",
                "stream_name": "MY_STREAM",
                "cursor_column": "TS",
                "batch_size": 5,
            }
        )
        with _install_fake_snowflake(connect_return=conn):
            await _collect(SnowflakeConnector().get_delta(config, "2026-01-01"))
        assert cursor.executed_sql == [
            "SELECT * FROM MY_STREAM WHERE TS > '2026-01-01' LIMIT 5"
        ]

    async def test_doc_metadata_matches_row(self):
        rows = [{"ID": 42, "UPDATED_AT": "2026-03-01T00:00:00"}]
        cursor = _FakeCursor(rows=rows)
        conn = _FakeConn(cursor)
        config = _make_config(
            {"mode": "query", "query": "SELECT *", "account": "acme", "database": "DB"}
        )
        with _install_fake_snowflake(connect_return=conn):
            docs = await _collect(SnowflakeConnector().get_delta(config, None))
        doc, _cursor = docs[0]
        assert doc.metadata == rows[0]
        assert doc.source_url.startswith("snowflake://acme/DB/row/")
