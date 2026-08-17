"""SnowflakeConnector — Snowflake data warehouse ingestion.

Modes:
  - query: execute a SELECT query, each row → RawDocument
  - stream: Snowflake Streams for CDC (INSERT/UPDATE/DELETE tracking)

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


def _row_to_text(row: dict) -> str:
    return "\n".join(f"{k}: {v}" for k, v in row.items() if v is not None)


@register("snowflake", feature_flag="ingestion_connector_snowflake_enabled")
class SnowflakeConnector(BaseConnector):
    """Snowflake data warehouse connector — query and stream modes."""

    source_type = "snowflake"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time
        t0 = time.perf_counter()
        try:
            import snowflake.connector  # type: ignore[import-not-found]
            cc = config.connection_config
            conn = snowflake.connector.connect(
                user=cc.get("user"),
                password=cc.get("password"),
                account=cc.get("account"),
                warehouse=cc.get("warehouse"),
                database=cc.get("database"),
                schema=cc.get("schema", "PUBLIC"),
                login_timeout=10,
            )
            cur = conn.cursor()
            cur.execute("SELECT CURRENT_VERSION()")
            version = cur.fetchone()[0]
            conn.close()
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(ok=True, latency_ms=latency, metadata={"version": version})
        except ImportError:
            return ConnectionHealth(ok=False, error="snowflake-connector-python not installed")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument
        try:
            import snowflake.connector  # type: ignore[import-not-found]
        except ImportError:
            _log.error("snowflake-connector-python not installed"); return

        cc = config.connection_config
        mode = cc.get("mode", "query")
        query = cc.get("query", "")
        cursor_col = cc.get("cursor_column", "UPDATED_AT")
        batch_size = int(cc.get("batch_size", 1000))

        conn = snowflake.connector.connect(
            user=cc.get("user"), password=cc.get("password"),
            account=cc.get("account"), warehouse=cc.get("warehouse"),
            database=cc.get("database"), schema=cc.get("schema", "PUBLIC"),
        )
        try:
            cur = conn.cursor(snowflake.connector.DictCursor)
            if mode == "stream":
                stream_name = cc.get("stream_name", "")
                sql = f"SELECT * FROM {stream_name}"
                if cursor:
                    sql += f" WHERE {cursor_col} > '{cursor}'"
                sql += f" LIMIT {batch_size}"
            else:
                sql = query
                if cursor and "{cursor}" in sql:
                    sql = sql.replace("{cursor}", cursor)
                elif cursor:
                    sql += f" WHERE {cursor_col} > '{cursor}' LIMIT {batch_size}"

            cur.execute(sql)
            new_cursor = cursor or ""
            for row in cur:
                row_dict = dict(row)
                new_cursor = str(row_dict.get(cursor_col, new_cursor))
                text = _row_to_text(row_dict)
                doc = RawDocument(
                    doc_id=str(uuid.uuid4()),
                    source_id=config.source_id,
                    tenant_id=config.tenant_id,
                    source_url=f"snowflake://{cc.get('account')}/{cc.get('database')}/row/{uuid.uuid4()}",
                    content=text.encode(),
                    content_type="text/plain",
                    metadata=row_dict,
                )
                yield doc, new_cursor
        finally:
            conn.close()
