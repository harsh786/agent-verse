"""ClickHouseConnector — ClickHouse analytics database ingestion.

Uses clickhouse-connect (HTTP interface) for both full and incremental queries.
Cursor: last row's ORDER BY column value (typically a timestamp).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING, Any

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    row_identity,
    stable_doc_id,
)
from app.ingestion.connector_egress import pin_source_hosts, run_driver_call
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)

# clickhouse-connect's own defaults, stated explicitly: the client is blocking and
# runs on a worker thread that cannot be interrupted, so these bound the call.
_CONNECT_TIMEOUT_S = 10
_SEND_RECEIVE_TIMEOUT_S = 300


def _client_kwargs(cc: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "host": cc.get("host", ""),
        "port": int(cc.get("port", 8123)),
        "username": cc.get("username", "default"),
        "password": cc.get("password", ""),
        "database": cc.get("database", "default"),
        "connect_timeout": _CONNECT_TIMEOUT_S,
        "send_receive_timeout": _SEND_RECEIVE_TIMEOUT_S,
    }


@register("clickhouse", feature_flag="ingestion_connector_clickhouse_enabled")
class ClickHouseConnector(BaseConnector):
    """ClickHouse analytics connector — query-based ingestion."""

    source_type = "clickhouse"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            cc = config.connection_config
            # The tenant-chosen host must resolve public (SSRF guard); inside the
            # block the HTTP client's lookups answer with the checked addresses.
            async with pin_source_hosts(
                [(cc.get("host", ""), cc.get("port", 8123))], context="clickhouse"
            ):
                import clickhouse_connect  # type: ignore[import-not-found]

                def _version() -> Any:
                    client = clickhouse_connect.get_client(**_client_kwargs(cc))
                    return client.query("SELECT version()").first_row[0]

                # Blocking HTTP client: off the event loop, every lookup checked.
                version = await run_driver_call(_version, context="clickhouse")
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

        cc = config.connection_config
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

        async with pin_source_hosts(
            [(cc.get("host", ""), cc.get("port", 8123))], context="clickhouse"
        ):
            try:
                import clickhouse_connect  # type: ignore[import-not-found]
            except ImportError:
                _log.error("clickhouse-connect not installed")
                return

            def _query() -> Any:
                return clickhouse_connect.get_client(**_client_kwargs(cc)).query(query)

            result = await run_driver_call(_query, context="clickhouse")
        col_names = result.column_names
        new_cursor = cursor or ""

        for row in result.result_rows:
            row_dict = dict(zip(col_names, row, strict=False))
            new_cursor = str(row_dict.get(cursor_col, new_cursor))
            text = "\n".join(f"{k}: {v}" for k, v in row_dict.items() if v is not None)
            row_key = row_identity(row_dict, cc.get("id_column"))
            doc = RawDocument(
                doc_id=stable_doc_id(config, row_key),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=f"clickhouse://{cc.get('host')}/{cc.get('database')}/row/{row_key}",
                content=text.encode(),
                content_type="text/plain",
                metadata=row_dict,
            )
            yield doc, new_cursor
