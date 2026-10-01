"""SnowflakeConnector — Snowflake data warehouse ingestion.

Modes:
  - query: execute a SELECT query, each row → RawDocument
  - stream: Snowflake Streams for CDC (INSERT/UPDATE/DELETE tracking)

Cursor: last row's ORDER BY column value (typically a timestamp).

snowflake-connector-python is blocking: connect, execute, fetch and close run on
the SDK pool (:mod:`app.ingestion.sdk_executor`), never on the event loop.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    ConnectorUnavailableError,
    row_identity,
    stable_doc_id,
)
from app.ingestion.connector_registry import register
from app.ingestion.sdk_executor import iterate_blocking, run_blocking
from app.ingestion.sql_safety import CURSOR_PARAM, bind_cursor_placeholder, checked_identifier

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)

# Rows fetched per round-trip while streaming a result set.
_FETCH_CHUNK = 500


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

            def _version() -> Any:
                conn = snowflake.connector.connect(
                    user=cc.get("user"),
                    password=cc.get("password"),
                    account=cc.get("account"),
                    warehouse=cc.get("warehouse"),
                    database=cc.get("database"),
                    schema=cc.get("schema", "PUBLIC"),
                    login_timeout=10,
                )
                try:
                    cur = conn.cursor()
                    cur.execute("SELECT CURRENT_VERSION()")
                    return cur.fetchone()[0]
                finally:
                    conn.close()

            version = await run_blocking(_version)
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
        except ImportError as exc:
            # Returning nothing here reported a successful, empty sync.
            raise ConnectorUnavailableError(
                "snowflake-connector-python is not installed on this server; "
                "the connector cannot run"
            ) from exc

        cc = config.connection_config
        mode = cc.get("mode", "query")
        query = cc.get("query", "")
        cursor_col = cc.get("cursor_column", "UPDATED_AT")
        batch_size = int(cc.get("batch_size", 1000))

        # The cursor (a value read back from the source's rows) is bound with the
        # connector's pyformat parameters, never pasted into the SQL. Identifiers
        # must be plain names; they stay unquoted so Snowflake's case folding of
        # unquoted names keeps working.
        params: dict[str, Any] | None = None
        placeholder = f"%({CURSOR_PARAM})s"

        def _column() -> str:
            return ".".join(checked_identifier(cursor_col, what="cursor_column", max_parts=1))

        if mode == "stream":
            stream = ".".join(checked_identifier(cc.get("stream_name", ""), what="stream_name"))
            sql = f"SELECT * FROM {stream}"
            if cursor:
                sql += f" WHERE {_column()} > {placeholder}"
                params = {CURSOR_PARAM: cursor}
            sql += f" LIMIT {batch_size}"
        else:
            sql = query
            if cursor:
                # With parameters, a literal % in the tenant's query must be %%.
                escaped, bound = bind_cursor_placeholder(sql.replace("%", "%%"), placeholder)
                if bound:
                    sql = escaped
                else:
                    sql = f"{escaped} WHERE {_column()} > {placeholder} LIMIT {batch_size}"
                params = {CURSOR_PARAM: cursor}

        conn = await run_blocking(
            snowflake.connector.connect,
            user=cc.get("user"),
            password=cc.get("password"),
            account=cc.get("account"),
            warehouse=cc.get("warehouse"),
            database=cc.get("database"),
            schema=cc.get("schema", "PUBLIC"),
        )
        try:
            cur = conn.cursor(snowflake.connector.DictCursor)
            if params is None:
                await run_blocking(cur.execute, sql)
            else:
                await run_blocking(cur.execute, sql, params)
            new_cursor = cursor or ""
            # The cursor fetches result chunks lazily over the network: page it in
            # on the pool so the result still streams.
            async for row in iterate_blocking(cur, chunk_size=_FETCH_CHUNK):
                row_dict = dict(row)
                new_cursor = str(row_dict.get(cursor_col, new_cursor))
                text = _row_to_text(row_dict)
                row_key = row_identity(row_dict, cc.get("id_column"))
                doc = RawDocument(
                    doc_id=stable_doc_id(config, row_key),
                    source_id=config.source_id,
                    tenant_id=config.tenant_id,
                    source_url=f"snowflake://{cc.get('account')}/{cc.get('database')}/row/{row_key}",
                    content=text.encode(),
                    content_type="text/plain",
                    metadata=row_dict,
                )
                yield doc, new_cursor
        finally:
            await run_blocking(conn.close)
