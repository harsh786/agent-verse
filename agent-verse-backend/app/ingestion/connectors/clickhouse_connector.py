"""ClickHouseConnector — ClickHouse analytics database ingestion.

Uses clickhouse-connect (HTTP interface) for both full and incremental queries.
Cursor: last row's ORDER BY column value (typically a timestamp).
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


@register("clickhouse", feature_flag="ingestion_connector_clickhouse_enabled")
class ClickHouseConnector(BaseConnector):
    """ClickHouse analytics connector — query-based ingestion."""

    source_type = "clickhouse"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time
        t0 = time.perf_counter()
        try:
            import clickhouse_connect  # type: ignore[import-not-found]
            cc = config.connection_config
            client = clickhouse_connect.get_client(
                host=cc.get("host", "localhost"),
                port=int(cc.get("port", 8123)),
                username=cc.get("username", "default"),
                password=cc.get("password", ""),
                database=cc.get("database", "default"),
                connect_timeout=10,
            )
            result = client.query("SELECT version()")
            version = result.first_row[0]
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(ok=True, latency_ms=latency, metadata={"version": version})
        except ImportError:
            return ConnectionHealth(ok=False, error="clickhouse-connect not installed")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument
        try:
            import clickhouse_connect  # type: ignore[import-not-found]
        except ImportError:
            _log.error("clickhouse-connect not installed"); return

        cc = config.connection_config
        client = clickhouse_connect.get_client(
            host=cc.get("host", "localhost"),
            port=int(cc.get("port", 8123)),
            username=cc.get("username", "default"),
            password=cc.get("password", ""),
            database=cc.get("database", "default"),
        )

        query = cc.get("query", "")
        cursor_col = cc.get("cursor_column", "updated_at")
        batch_size = int(cc.get("batch_size", 1000))

        if not query:
            table = cc.get("table", "")
            query = f"SELECT * FROM {table}"
            if cursor:
                query += f" WHERE {cursor_col} > '{cursor}'"
            query += f" ORDER BY {cursor_col} LIMIT {batch_size}"
        elif cursor and "{cursor}" in query:
            query = query.replace("{cursor}", cursor)

        result = client.query(query)
        col_names = result.column_names
        new_cursor = cursor or ""

        for row in result.result_rows:
            row_dict = dict(zip(col_names, row))
            new_cursor = str(row_dict.get(cursor_col, new_cursor))
            text = "\n".join(f"{k}: {v}" for k, v in row_dict.items() if v is not None)
            doc = RawDocument(
                doc_id=str(uuid.uuid4()),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=f"clickhouse://{cc.get('host')}/{cc.get('database')}/row/{uuid.uuid4()}",
                content=text.encode(),
                content_type="text/plain",
                metadata=row_dict,
            )
            yield doc, new_cursor
