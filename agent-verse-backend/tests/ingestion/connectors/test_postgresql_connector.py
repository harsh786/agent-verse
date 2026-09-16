"""Tests for PostgreSQLConnector — query-mode incremental ingestion.

asyncpg is a real installed dependency here, so we patch ``asyncpg.connect``
(and simulate the "not installed" case via ``sys.modules``) rather than
faking the whole module.
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, patch

from app.ingestion.connectors.postgresql_connector import (
    PostgreSQLConnector,
    _build_dsn,
    _row_to_text,
)
from app.ingestion.source_config import SourceConfig


def _make_config(conn_config: dict | None = None) -> SourceConfig:
    return SourceConfig(
        source_id="src-pg",
        tenant_id="t1",
        name="Test PG",
        family="oltp_database",
        source_type="postgresql",
        connection_config=conn_config or {},
    )


async def _collect(agen) -> list:
    out = []
    async for item in agen:
        out.append(item)
    return out


class TestHelpers:
    def test_row_to_text_includes_pk_and_fields(self):
        text = _row_to_text("orders", ["id"], {"id": 1, "amount": 42, "note": None})
        assert "orders record (id=1)" in text
        assert "amount: 42" in text
        assert "note" not in text  # None values skipped

    def test_row_to_text_default_pk(self):
        text = _row_to_text("orders", [], {"id": 7, "x": "y"})
        assert "id=7" in text

    def test_build_dsn_defaults(self):
        dsn = _build_dsn({})
        assert dsn == "postgresql://postgres:@localhost:5432/postgres"

    def test_build_dsn_custom(self):
        dsn = _build_dsn(
            {"host": "db", "port": 6543, "database": "app", "username": "u", "password": "p"}
        )
        assert dsn == "postgresql://u:p@db:6543/app"


class TestValidateConnection:
    async def test_success(self):
        fake_conn = AsyncMock()
        fake_conn.fetchval = AsyncMock(return_value="PostgreSQL 16.0")
        fake_conn.close = AsyncMock()
        config = _make_config({"dsn": "postgresql://u:p@h/db", "tables": ["public.orders"]})

        with patch("asyncpg.connect", AsyncMock(return_value=fake_conn)):
            result = await PostgreSQLConnector().validate_connection(config)

        assert result.ok is True
        assert result.metadata["tables"] == ["public.orders"]
        assert "16.0" in result.metadata["version"]
        fake_conn.close.assert_awaited_once()

    async def test_import_error(self):
        config = _make_config()
        with patch.dict(sys.modules, {"asyncpg": None}):
            result = await PostgreSQLConnector().validate_connection(config)
        assert result.ok is False
        assert "asyncpg" in result.error

    async def test_connection_exception(self):
        config = _make_config({"dsn": "postgresql://bad"})
        with patch("asyncpg.connect", AsyncMock(side_effect=OSError("refused"))):
            result = await PostgreSQLConnector().validate_connection(config)
        assert result.ok is False
        assert "refused" in result.error


class TestGetDelta:
    async def test_no_asyncpg_installed_yields_nothing(self):
        config = _make_config({"tables": ["orders"]})
        with patch.dict(sys.modules, {"asyncpg": None}):
            docs = await _collect(PostgreSQLConnector().get_delta(config, None))
        assert docs == []

    async def test_connect_failure_yields_nothing(self):
        config = _make_config({"tables": ["orders"]})
        with patch("asyncpg.connect", AsyncMock(side_effect=OSError("no route"))):
            docs = await _collect(PostgreSQLConnector().get_delta(config, None))
        assert docs == []

    async def test_yields_rows_and_advances_cursor(self):
        rows = [
            {"id": 1, "name": "Alice", "updated_at": "2026-01-02T00:00:00"},
            {"id": 2, "name": "Bob", "updated_at": "2026-01-03T00:00:00"},
        ]
        fake_conn = AsyncMock()
        fake_conn.fetch = AsyncMock(return_value=rows)
        fake_conn.close = AsyncMock()
        config = _make_config(
            {
                "dsn": "postgresql://u:p@h/db",
                "tables": ["public.customers"],
                "primary_keys": {"customers": ["id"]},
            }
        )

        with patch("asyncpg.connect", AsyncMock(return_value=fake_conn)):
            docs = await _collect(PostgreSQLConnector().get_delta(config, None))

        assert len(docs) == 2
        doc0, _cursor0 = docs[0]
        assert doc0.doc_id == "pg://public.customers/1"
        assert doc0.source_id == "src-pg"
        assert doc0.tenant_id == "t1"
        assert "Alice" in doc0.content.decode()
        _doc1, cursor1 = docs[1]
        assert cursor1 == "2026-01-03T00:00:00"
        fake_conn.close.assert_awaited_once()

    async def test_table_without_schema_defaults_to_public(self):
        rows = [{"id": 5, "updated_at": "2026-02-01T00:00:00"}]
        fake_conn = AsyncMock()
        fake_conn.fetch = AsyncMock(return_value=rows)
        fake_conn.close = AsyncMock()
        config = _make_config({"tables": ["widgets"]})

        with patch("asyncpg.connect", AsyncMock(return_value=fake_conn)):
            docs = await _collect(PostgreSQLConnector().get_delta(config, None))

        assert docs[0][0].doc_id == "pg://public.widgets/5"

    async def test_query_error_on_one_table_continues_to_next(self):
        fake_conn = AsyncMock()
        fake_conn.fetch = AsyncMock(
            side_effect=[Exception("bad table"), [{"id": 9, "updated_at": "2026-01-01"}]]
        )
        fake_conn.close = AsyncMock()
        config = _make_config({"tables": ["broken", "public.good"]})

        with patch("asyncpg.connect", AsyncMock(return_value=fake_conn)):
            docs = await _collect(PostgreSQLConnector().get_delta(config, None))

        assert len(docs) == 1
        assert docs[0][0].doc_id == "pg://public.good/9"

    async def test_no_tables_configured_yields_nothing(self):
        fake_conn = AsyncMock()
        fake_conn.close = AsyncMock()
        config = _make_config({"tables": []})

        with patch("asyncpg.connect", AsyncMock(return_value=fake_conn)):
            docs = await _collect(PostgreSQLConnector().get_delta(config, None))

        assert docs == []
        fake_conn.close.assert_awaited_once()

    async def test_cdc_mode_falls_back_to_query(self):
        fake_conn = AsyncMock()
        fake_conn.fetch = AsyncMock(return_value=[{"id": 1, "updated_at": "2026-01-01"}])
        fake_conn.close = AsyncMock()
        config = _make_config({"tables": ["t"], "cdc_mode": "logical_replication"})

        with patch("asyncpg.connect", AsyncMock(return_value=fake_conn)):
            docs = await _collect(PostgreSQLConnector().get_delta(config, None))

        assert len(docs) == 1
