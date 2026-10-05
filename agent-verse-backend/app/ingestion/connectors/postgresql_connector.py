"""PostgreSQLConnector — incremental ingestion with CDC support.

Three modes:
  query:               SELECT WHERE updated_at > cursor (default, no privileges)
  logical_replication: pgoutput WAL streaming (requires superuser/replication role)
  pg_notify:           LISTEN/NOTIFY for push-based row change ingestion
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    ConnectorFetchError,
    ConnectorUnavailableError,
    UnitFailures,
    describe_fetch_error,
    stable_doc_id,
)
from app.ingestion.connector_egress import (
    EgressPins,
    dsn_with_pinned_hosts,
    pin_source_dsn,
    pinned_hostname_ssl,
)
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)

_DEFAULT_CURSOR_FIELD = "updated_at"
_DEFAULT_BATCH_SIZE = 500
_MAX_BATCH_SIZE = 10_000
_LIVE_PAGE = 5_000
# Sessions are read-only and bounded: a sync never writes to the tenant's
# database, and one slow statement cannot hold the worker forever.
_SERVER_SETTINGS = {
    "application_name": "agentverse-ingestion",
    "default_transaction_read_only": "on",
    "statement_timeout": "300000",
}


def _quote(part: str) -> str:
    return '"' + part.replace('"', '""') + '"'


def _table_ref(table_def: str) -> tuple[str, str, str]:
    """(schema, table, quoted reference) of ``schema.table`` / ``table`` (validated)."""
    from app.ingestion.sql_safety import checked_identifier

    parts = checked_identifier(table_def, what="table", max_parts=2)
    schema, table = (parts[0], parts[1]) if len(parts) == 2 else ("public", parts[0])
    return schema, table, f"{_quote(schema)}.{_quote(table)}"


def _row_to_text(table: str, pk_cols: list[str], row: dict) -> str:
    """Convert a DB row to human-readable text for embedding."""
    from app.ingestion.connectors.sql_rows import key_desc, row_text

    return row_text(table, key_desc(pk_cols or ["id"], row)[0], row)


@register("postgresql", feature_flag="ingestion_connector_postgresql_enabled")
class PostgreSQLConnector(BaseConnector):
    """PostgreSQL incremental ingestion: per-table keyset batches (P1b-7)."""

    source_type = "postgresql"
    supports_deletion_tracking = True

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            dsn = config.connection_config.get("dsn") or _build_dsn(config.connection_config)
            # Every host the DSN would dial must resolve public (SSRF guard), and
            # asyncpg dials the checked address, never a fresh DNS answer.
            async with pin_source_dsn(dsn, context="postgresql") as pins:
                import asyncpg  # type: ignore[import-not-found]

                conn = await asyncpg.connect(**_pinned_connect_kwargs(dsn, pins), timeout=10)
            try:
                version = await conn.fetchval("SELECT version()")
                unreadable = []
                for table_def in config.connection_config.get("tables", []):
                    schema, table, _ref = _table_ref(table_def)
                    ok = await conn.fetchval(
                        "SELECT has_table_privilege(to_regclass($1), 'SELECT')",
                        f"{_quote(schema)}.{_quote(table)}",
                    )
                    if not ok:
                        unreadable.append(f"{schema}.{table}")
            finally:
                await conn.close()
            latency = (time.perf_counter() - t0) * 1000
            tables = config.connection_config.get("tables", [])
            if unreadable:
                return ConnectionHealth(
                    ok=False,
                    latency_ms=latency,
                    error=f"connected, but these tables are missing or not readable: "
                    f"{', '.join(unreadable)}",
                    metadata={"version": str(version)[:50], "tables": tables},
                )
            return ConnectionHealth(
                ok=True,
                latency_ms=latency,
                metadata={"version": str(version)[:50], "tables": tables},
            )
        except ImportError:
            return ConnectionHealth(ok=False, error="asyncpg not installed — pip install asyncpg")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=describe_fetch_error(exc))

    async def _connect(self, config: SourceConfig) -> Any:
        try:
            import asyncpg
        except ImportError as exc:
            # Returning nothing here reported a successful, empty sync.
            raise ConnectorUnavailableError(
                "asyncpg is not installed on this server; the connector cannot run"
            ) from exc
        dsn = config.connection_config.get("dsn") or _build_dsn(config.connection_config)
        async with pin_source_dsn(dsn, context="postgresql") as pins:
            try:
                return await asyncpg.connect(
                    **_pinned_connect_kwargs(dsn, pins),
                    timeout=15,
                    server_settings=_SERVER_SETTINGS,
                )
            except Exception as exc:
                # USR-1: a connection / auth failure fails the sync.
                _log.error("postgresql_connect_error: %s", exc)
                raise ConnectorFetchError(
                    f"postgresql: cannot connect: {describe_fetch_error(exc)}"
                ) from exc

    async def _primary_key(self, conn: Any, config: SourceConfig, schema: str, table: str
                           ) -> list[str]:
        """Configured ``primary_keys[table]``, else the table's PRIMARY KEY, else ``id``."""
        from app.ingestion.sql_safety import checked_identifier

        configured = (config.connection_config.get("primary_keys") or {}).get(table)
        if configured:
            cols = [configured] if isinstance(configured, str) else list(configured)
            return [checked_identifier(c, what="primary key", max_parts=1)[0] for c in cols]
        regclass = f"{_quote(schema)}.{_quote(table)}"
        if await conn.fetchval("SELECT to_regclass($1)", regclass) is None:
            raise ConnectorFetchError(f"table {schema}.{table} does not exist")
        rows = await conn.fetch(
            "SELECT a.attname FROM pg_index i JOIN pg_attribute a "
            "ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = to_regclass($1) AND i.indisprimary "
            "ORDER BY array_position(i.indkey, a.attnum)",
            f"{_quote(schema)}.{_quote(table)}",
        )
        if rows:
            return [str(r["attname"]) for r in rows]
        has_id = await conn.fetchval(
            "SELECT count(*) FROM pg_attribute WHERE attrelid = to_regclass($1) "
            "AND attname = 'id' AND NOT attisdropped",
            f"{_quote(schema)}.{_quote(table)}",
        )
        if has_id:
            return ["id"]
        raise ConnectorFetchError(
            f"{schema}.{table} has no primary key; set connection_config.primary_keys"
        )

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        """Rows changed since each table's own position, in keyset batches."""
        from app.ingestion.connectors.sql_rows import TableCursors
        from app.ingestion.source_config import RawDocument
        from app.ingestion.sql_safety import checked_identifier

        cc = config.connection_config
        if cc.get("cdc_mode", "query") != "query":
            _log.warning(
                "postgresql_cdc_mode=%s not yet implemented, falling back to query",
                cc.get("cdc_mode"),
            )
        cursor_field = checked_identifier(
            cc.get("cursor_field", _DEFAULT_CURSOR_FIELD), what="cursor field", max_parts=1
        )[0]
        batch_size = max(1, min(int(cc.get("batch_size", _DEFAULT_BATCH_SIZE)), _MAX_BATCH_SIZE))
        positions = TableCursors.parse(cursor)
        conn = await self._connect(config)
        failures = UnitFailures("postgresql")
        try:
            for table_def in cc.get("tables", []):
                try:
                    schema, table, ref = _table_ref(table_def)
                    pk_cols = await self._primary_key(conn, config, schema, table)
                except Exception as exc:
                    failures.add(f"table {table_def}", exc)
                    continue
                unit = f"{schema}.{table}"
                cols = [cursor_field, *pk_cols]
                order = ", ".join(_quote(c) for c in cols)
                tuple_sql = "(" + order + ")"
                pos = positions.get(unit)
                while True:
                    if pos.started and pos.key:
                        marks = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
                        where = f"WHERE {tuple_sql} > ({marks})"
                        params: list[Any] = [pos.cursor, *pos.key]
                    elif pos.started:
                        where = f"WHERE {_quote(cursor_field)} > $1"
                        params = [pos.cursor]
                    else:
                        where = f"WHERE {_quote(cursor_field)} IS NOT NULL"
                        params = []
                    sql = (
                        f"SELECT * FROM {ref} {where} ORDER BY {order} "
                        f"LIMIT {batch_size}"
                    )
                    try:
                        rows = await conn.fetch(sql, *params)
                    except Exception as exc:
                        # USR-1: an unreadable table is a counted failure (partial);
                        # its position is untouched, so the next sync retries it.
                        failures.add(f"table {unit}", exc)
                        break
                    for row in rows:
                        row_dict = dict(row)
                        key = [row_dict.get(c) for c in pk_cols]
                        position = positions.advance(unit, row_dict.get(cursor_field), key)
                        pos = positions.get(unit)
                        text = _row_to_text(table, pk_cols, row_dict)
                        pk_val = "_".join(str(k) for k in key)
                        row_url = f"pg://{schema}.{table}/{pk_val}"
                        yield RawDocument(
                            doc_id=stable_doc_id(config, row_url),
                            source_id=config.source_id,
                            tenant_id=config.tenant_id,
                            content=text.encode("utf-8"),
                            content_type="text/plain",
                            source_url=row_url,
                            title=f"{table} {pk_val}",
                            modified_at=str(row_dict.get(cursor_field) or ""),
                            metadata={"table": table, "schema": schema, "pk": pk_val},
                        ), position
                    if len(rows) < batch_size:
                        break
        finally:
            await conn.close()
        failures.raise_if_any()

    async def iter_live_doc_ids(self, config: SourceConfig) -> AsyncIterator[str]:
        """Every configured table's primary keys, keyset-paged (KB-44 reconcile).

        Any error propagates: a partial listing must never look complete.
        """
        conn = await self._connect(config)
        try:
            for table_def in config.connection_config.get("tables", []):
                schema, table, ref = _table_ref(table_def)
                pk_cols = await self._primary_key(conn, config, schema, table)
                order = ", ".join(_quote(c) for c in pk_cols)
                after: list[Any] | None = None
                while True:
                    if after is None:
                        rows = await conn.fetch(
                            f"SELECT {order} FROM {ref} ORDER BY {order} LIMIT {_LIVE_PAGE}"
                        )
                    else:
                        marks = ", ".join(f"${i}" for i in range(1, len(pk_cols) + 1))
                        rows = await conn.fetch(
                            f"SELECT {order} FROM {ref} WHERE ({order}) > ({marks}) "
                            f"ORDER BY {order} LIMIT {_LIVE_PAGE}",
                            *after,
                        )
                    for row in rows:
                        pk_val = "_".join(str(row[c]) for c in pk_cols)
                        yield stable_doc_id(config, f"pg://{schema}.{table}/{pk_val}")
                    if len(rows) < _LIVE_PAGE:
                        break
                    after = [rows[-1][c] for c in pk_cols]
        finally:
            await conn.close()


_VERIFYING_SSLMODES = frozenset({"verify-full"})


def _pinned_connect_kwargs(dsn: str, pins: EgressPins) -> dict[str, Any]:
    """asyncpg connect kwargs that dial only the egress-checked addresses.

    asyncpg resolves hosts on the event loop (libuv under uvloop), outside the
    pinned ``socket.getaddrinfo``, so the DSN's hosts are replaced by the checked
    IPs. ``sslmode=verify-full`` would then check the certificate against the IP;
    it gets a context that verifies (and sends SNI for) the configured hostname.
    """
    from urllib.parse import parse_qsl, urlsplit

    if "://" not in dsn:
        # asyncpg only understands URI DSNs; don't hand it a keyword string.
        raise ValueError("postgresql: dsn must be a postgresql:// URI")
    query = {k.lower(): v for k, v in parse_qsl(urlsplit(dsn).query, keep_blank_values=True)}
    kwargs: dict[str, Any] = {"dsn": dsn_with_pinned_hosts(dsn, pins)}
    if query.get("sslmode", "").lower() in _VERIFYING_SSLMODES:
        kwargs["ssl"] = pinned_hostname_ssl(pins, cafile=query.get("sslrootcert") or None)
    return kwargs


def _build_dsn(cfg: dict) -> str:
    from urllib.parse import quote

    # No "localhost" default: that is the platform's own database, not the
    # tenant's. An empty host is refused by the egress guard.
    host = str(cfg.get("host", "") or "")
    port = cfg.get("port", 5432)
    db = quote(str(cfg.get("database", "postgres")), safe="")
    user = quote(str(cfg.get("username", "postgres")), safe="")
    pwd = quote(str(cfg.get("password", "")), safe="")
    netloc_host = f"[{host}]" if ":" in host else host
    return f"postgresql://{user}:{pwd}@{netloc_host}:{port}/{db}"
