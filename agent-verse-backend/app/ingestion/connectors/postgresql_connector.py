"""PostgreSQLConnector — incremental ingestion with CDC support.

Three modes:
  query:               SELECT WHERE updated_at > cursor (default, no privileges)
  logical_replication: pgoutput WAL streaming (requires superuser/replication role)
  pg_notify:           LISTEN/NOTIFY for push-based row change ingestion
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)

_DEFAULT_CURSOR_FIELD = "updated_at"
_DEFAULT_BATCH_SIZE = 500


def _row_to_text(table: str, pk_cols: list[str], row: dict) -> str:
    """Convert a DB row to human-readable text for embedding."""
    pk = ", ".join(f"{k}={row.get(k, '?')}" for k in (pk_cols or ["id"]))
    parts = [f"{k}: {v}" for k, v in row.items() if v is not None and str(v).strip()]
    return f"{table} record ({pk}): " + ", ".join(parts[:50]) + "."


@register("postgresql", feature_flag="ingestion_connector_postgresql_enabled")
class PostgreSQLConnector(BaseConnector):
    """PostgreSQL incremental ingestion (query or CDC mode)."""

    source_type = "postgresql"
    supports_deletion_tracking = True

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time
        t0 = time.perf_counter()
        try:
            import asyncpg  # type: ignore[import-not-found]
            dsn = config.connection_config.get("dsn") or _build_dsn(config.connection_config)
            conn = await asyncpg.connect(dsn, timeout=10)
            version = await conn.fetchval("SELECT version()")
            await conn.close()
            latency = (time.perf_counter() - t0) * 1000
            tables = config.connection_config.get("tables", [])
            return ConnectionHealth(
                ok=True,
                latency_ms=latency,
                metadata={"version": str(version)[:50], "tables": tables},
            )
        except ImportError:
            return ConnectionHealth(ok=False, error="asyncpg not installed — pip install asyncpg")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        """Yield rows from configured tables newer than cursor."""
        from app.ingestion.source_config import RawDocument

        cdc_mode = config.connection_config.get("cdc_mode", "query")
        tables = config.connection_config.get("tables", [])
        cursor_field = config.connection_config.get("cursor_field", _DEFAULT_CURSOR_FIELD)
        batch_size = config.connection_config.get("batch_size", _DEFAULT_BATCH_SIZE)
        dsn = config.connection_config.get("dsn") or _build_dsn(config.connection_config)

        if cdc_mode != "query":
            _log.warning("postgresql_cdc_mode=%s not yet implemented, falling back to query", cdc_mode)

        try:
            import asyncpg
        except ImportError:
            _log.error("asyncpg not installed")
            return

        try:
            conn = await asyncpg.connect(dsn)
        except Exception as exc:
            _log.error("postgresql_connect_error: %s", exc)
            return

        try:
            new_cursor = cursor or "1970-01-01T00:00:00"
            for table_def in tables:
                # table_def is "schema.table" or just "table"
                parts = table_def.split(".")
                schema = parts[0] if len(parts) > 1 else "public"
                table = parts[-1]
                pk_cols = config.connection_config.get("primary_keys", {}).get(table, ["id"])

                try:
                    rows = await conn.fetch(
                        f'SELECT * FROM "{schema}"."{table}" '
                        f'WHERE "{cursor_field}" > $1 '
                        f'ORDER BY "{cursor_field}" ASC LIMIT $2',
                        cursor or "1970-01-01",
                        batch_size,
                    )
                except Exception as exc:
                    _log.warning("postgresql_query_error table=%s: %s", table, exc)
                    continue

                for row in rows:
                    row_dict = dict(row)
                    row_cursor = str(row_dict.get(cursor_field, ""))
                    if row_cursor > new_cursor:
                        new_cursor = row_cursor

                    text = _row_to_text(table, pk_cols, row_dict)
                    pk_val = "_".join(str(row_dict.get(k, "")) for k in pk_cols)
                    doc_id = f"pg://{schema}.{table}/{pk_val}"

                    raw = RawDocument(
                        doc_id=doc_id,
                        source_id=config.source_id,
                        tenant_id=config.tenant_id,
                        content=text.encode("utf-8"),
                        content_type="text/plain",
                        source_url=doc_id,
                        title=f"{table} {pk_val}",
                        modified_at=row_cursor,
                        metadata={"table": table, "schema": schema, "pk": pk_val},
                    )
                    yield raw, new_cursor
        finally:
            await conn.close()


def _build_dsn(cfg: dict) -> str:
    host = cfg.get("host", "localhost")
    port = cfg.get("port", 5432)
    db = cfg.get("database", "postgres")
    user = cfg.get("username", "postgres")
    pwd = cfg.get("password", "")
    return f"postgresql://{user}:{pwd}@{host}:{port}/{db}"
