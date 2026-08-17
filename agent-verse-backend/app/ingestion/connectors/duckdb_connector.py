"""DuckDBConnector — DuckDB local analytics database ingestion.

DuckDB can query Parquet, CSV, JSON, Arrow, and Delta Lake files directly.
Modes:
  - query: arbitrary SQL SELECT
  - file: DuckDB auto-scans a file or glob pattern (read_parquet, read_csv, etc.)

Cursor: last row's ORDER BY column value.
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, AsyncIterator

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


@register("duckdb", feature_flag="ingestion_connector_duckdb_enabled")
class DuckDBConnector(BaseConnector):
    """DuckDB connector — in-process analytics with multi-format file support."""

    source_type = "duckdb"

    async def validate_connection(self, config: "SourceConfig") -> ConnectionHealth:
        import time
        t0 = time.perf_counter()
        try:
            import duckdb  # type: ignore[import-not-found]
            db_path = config.connection_config.get("database", ":memory:")
            con = duckdb.connect(db_path, read_only=True)
            con.execute("SELECT 1")
            con.close()
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(ok=True, latency_ms=latency, metadata={"database": db_path})
        except ImportError:
            return ConnectionHealth(ok=False, error="duckdb not installed")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: "SourceConfig", cursor: str | None
    ) -> AsyncIterator[tuple["RawDocument", str]]:
        from app.ingestion.source_config import RawDocument
        try:
            import duckdb  # type: ignore[import-not-found]
        except ImportError:
            _log.error("duckdb not installed"); return

        cc = config.connection_config
        db_path = cc.get("database", ":memory:")
        mode = cc.get("mode", "query")
        cursor_col = cc.get("cursor_column", "updated_at")
        batch_size = int(cc.get("batch_size", 1000))

        con = duckdb.connect(db_path, read_only=True)
        try:
            if mode == "file":
                file_path = cc.get("file", "")
                query = f"SELECT * FROM '{file_path}'"
                if cursor:
                    query += f" WHERE {cursor_col} > '{cursor}'"
                query += f" LIMIT {batch_size}"
            else:
                query = cc.get("query", "SELECT 1")
                if cursor and "{cursor}" in query:
                    query = query.replace("{cursor}", cursor)

            result = con.execute(query)
            col_names = [d[0] for d in result.description]
            new_cursor = cursor or ""

            for row in result.fetchall():
                row_dict = dict(zip(col_names, row))
                new_cursor = str(row_dict.get(cursor_col, new_cursor))
                text = "\n".join(f"{k}: {v}" for k, v in row_dict.items() if v is not None)
                doc = RawDocument(
                    doc_id=str(uuid.uuid4()),
                    source_id=config.source_id,
                    tenant_id=config.tenant_id,
                    source_url=f"duckdb://{db_path}/row/{uuid.uuid4()}",
                    content=text.encode(),
                    content_type="text/plain",
                    metadata=row_dict,
                )
                yield doc, new_cursor
        finally:
            con.close()
