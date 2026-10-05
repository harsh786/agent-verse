"""MySQLConnector — MySQL / MariaDB database ingestion.

Uses PyMySQL (or mysqlclient). Two modes:

* **tables** (``tables: [...]``, or the legacy single ``table``): every table is
  read in keyset batches ``ORDER BY <cursor>, <primary key>`` and resumes from
  its own position (P1b-7, see :mod:`app.ingestion.connectors.sql_rows`);
  deleted rows are found by listing primary keys (upstream reconciliation).
* **query** (``query``): a tenant-written SELECT, optionally with ``%s`` (or
  ``{cursor}``) bound to the last cursor value.

Sessions are read-only with a statement time limit.
"""

from __future__ import annotations

import asyncio
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
    row_identity,
    stable_doc_id,
)
from app.ingestion.connector_egress import pin_source_hosts
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 1000
_MAX_BATCH_SIZE = 10_000
_LIVE_PAGE = 5_000


def _q(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _ident(name: object, what: str) -> str:
    from app.ingestion.sql_safety import checked_identifier

    return checked_identifier(name, what=what, max_parts=1)[0]


def _json_safe(value: Any) -> Any:
    from app.ingestion.connectors.sql_rows import render_value

    if value is None or isinstance(value, bool | int | float | str):
        return value
    return render_value(value)


@register("mysql", feature_flag="ingestion_connector_mysql_enabled")
@register("mariadb")
class MySQLConnector(BaseConnector):
    """MySQL / MariaDB connector — per-table incremental ingestion."""

    source_type = "mysql"
    supports_deletion_tracking = True

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        cc = config.connection_config
        try:
            # The tenant-chosen host must resolve public (SSRF guard), and the
            # driver dials the checked address, never a fresh DNS answer.
            async with pin_source_hosts(
                [(cc.get("host", ""), cc.get("port", 3306))], context="mysql"
            ) as pins:
                host = pins.ip(cc.get("host", ""))

                def _probe() -> tuple[str, list[str]]:
                    conn = self._connect(cc, host=host)
                    try:
                        cur = conn.cursor()
                        cur.execute("SELECT VERSION()")
                        row = cur.fetchone()
                        version = row[0] if isinstance(row, tuple | list) else (
                            next(iter(row.values())) if isinstance(row, dict) else row
                        )
                        unreadable: list[str] = []
                        for table in self._tables(cc):
                            try:
                                cur.execute(f"SELECT 1 FROM {_q(table)} LIMIT 0")
                            except Exception as exc:
                                unreadable.append(f"{table} ({describe_fetch_error(exc)})")
                        return str(version), unreadable
                    finally:
                        conn.close()

                version, unreadable = await asyncio.to_thread(_probe)
            latency = (time.perf_counter() - t0) * 1000
            if unreadable:
                return ConnectionHealth(
                    ok=False,
                    latency_ms=latency,
                    error=f"connected, but these tables are not readable: {'; '.join(unreadable)}",
                    metadata={"version": version},
                )
            return ConnectionHealth(ok=True, latency_ms=latency, metadata={"version": version})
        except ImportError as exc:
            return ConnectionHealth(ok=False, error=f"MySQL driver not installed: {exc}")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=describe_fetch_error(exc))

    @staticmethod
    def _tables(cc: dict) -> list[str]:
        tables = cc.get("tables")
        if tables:
            return [_ident(t, "table") for t in tables]
        if cc.get("table") and not cc.get("query"):
            return [_ident(cc["table"], "table")]
        return []

    @staticmethod
    def _connect(cc: dict, *, host: str | None = None):  # type: ignore[no-untyped-def]
        # ``host`` is the pinned, egress-checked address (no TLS is configured
        # here, so there is no certificate hostname to preserve).
        host = host or cc.get("host", "")
        try:
            import pymysql  # type: ignore[import-not-found]

            conn = pymysql.connect(
                host=host,
                port=int(cc.get("port", 3306)),
                user=cc.get("username", ""),
                password=cc.get("password", ""),
                database=cc.get("database", ""),
                connect_timeout=10,
                read_timeout=300,
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
            )
        except ImportError:
            import MySQLdb  # type: ignore[import-not-found]

            conn = MySQLdb.connect(
                host=host,
                port=int(cc.get("port", 3306)),
                user=cc.get("username", ""),
                passwd=cc.get("password", ""),
                db=cc.get("database", ""),
                cursorclass=MySQLdb.cursors.DictCursor,
                connect_timeout=10,
                charset="utf8mb4",
            )
        return conn

    @staticmethod
    def _harden(conn: Any) -> None:
        """Read-only session with a statement time limit (best effort on MariaDB)."""
        cur = conn.cursor()
        for statement in ("SET SESSION TRANSACTION READ ONLY",
                          "SET SESSION MAX_EXECUTION_TIME = 300000"):
            try:
                cur.execute(statement)
            except Exception as exc:  # MariaDB names the limit differently
                _log.debug("mysql_session_setting_skipped %s: %s", statement, exc)

    async def _open(self, cc: dict, host: str) -> Any:
        try:
            return await asyncio.to_thread(self._connect, cc, host=host)
        except ImportError as exc:
            # Returning nothing here reported a successful, empty sync.
            raise ConnectorUnavailableError(
                f"MySQL driver not installed (pymysql or mysqlclient): {exc}"
            ) from exc
        except Exception as exc:
            # USR-1: a connection / auth failure fails the sync with its reason.
            reason = describe_fetch_error(exc)
            raise ConnectorFetchError(f"mysql: cannot connect: {reason}") from exc

    def _primary_key(self, conn: Any, cc: dict, table: str) -> list[str]:
        configured = (cc.get("primary_keys") or {}).get(table) or (
            None if cc.get("tables") else cc.get("id_column")  # legacy single table
        )
        if configured:
            cols = [configured] if isinstance(configured, str) else list(configured)
            return [_ident(c, "primary key") for c in cols]
        cur = conn.cursor()
        cur.execute(
            "SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "AND CONSTRAINT_NAME = 'PRIMARY' ORDER BY ORDINAL_POSITION",
            (table,),
        )
        cols = [str(_first(r)) for r in cur.fetchall()]
        if cols:
            return cols
        cur.execute(
            "SELECT COUNT(*) AS n, SUM(COLUMN_NAME = 'id') AS has_id "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (table,),
        )
        found = cur.fetchone() or {}
        n, has_id = (found.get("n"), found.get("has_id")) if isinstance(found, dict) else found
        if not int(n or 0):
            # information_schema shows only what the user may read.
            raise ConnectorFetchError(
                f"table {table} does not exist or this user has no SELECT privilege on it"
            )
        if int(has_id or 0):
            return ["id"]
        raise ConnectorFetchError(f"{table} has no primary key; set connection_config.primary_keys")

    def _row_key(self, config: SourceConfig, table: str, pk_cols: list[str], row: dict) -> str:
        cc = config.connection_config
        if not cc.get("tables") and cc.get("table"):
            # Legacy single-table sources keep their document ids.
            return row_identity(row, cc.get("id_column") or (pk_cols[0] if pk_cols else None))
        return f"{table}/" + "_".join(str(row.get(c)) for c in pk_cols)

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        cc = config.connection_config
        if cc.get("query"):
            async for item in self._query_mode(config, cursor):
                yield item
            return
        async for item in self._tables_mode(config, cursor):
            yield item

    async def _tables_mode(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.connectors.sql_rows import TableCursors, key_desc, row_text
        from app.ingestion.source_config import RawDocument

        cc = config.connection_config
        cursor_col = _ident(cc.get("cursor_field") or cc.get("cursor_column") or "updated_at",
                            "cursor column")
        batch = max(1, min(int(cc.get("batch_size", _DEFAULT_BATCH_SIZE)), _MAX_BATCH_SIZE))
        positions = TableCursors.parse(cursor)
        failures = UnitFailures("mysql")
        async with pin_source_hosts(
            [(cc.get("host", ""), cc.get("port", 3306))], context="mysql"
        ) as pins:
            host = pins.ip(cc.get("host", ""))
            tables = self._tables(cc)
            if not tables:
                raise ConnectorFetchError("mysql: no tables configured (connection_config.tables)")
            conn = await self._open(cc, host)
            try:
                await asyncio.to_thread(self._harden, conn)
                for table in tables:
                    try:
                        pk_cols = await asyncio.to_thread(self._primary_key, conn, cc, table)
                    except Exception as exc:
                        failures.add(f"table {table}", exc)
                        continue
                    cols = [cursor_col, *pk_cols]
                    order = ", ".join(_q(c) for c in cols)
                    pos = positions.get(table)
                    while True:
                        if pos.started and pos.key:
                            marks = ", ".join(["%s"] * len(cols))
                            where, params = f"WHERE ({order}) > ({marks})", [pos.cursor, *pos.key]
                        elif pos.started:
                            where, params = f"WHERE {_q(cursor_col)} > %s", [pos.cursor]
                        else:
                            where, params = f"WHERE {_q(cursor_col)} IS NOT NULL", []
                        sql = f"SELECT * FROM {_q(table)} {where} ORDER BY {order} LIMIT {batch}"
                        try:
                            rows = await asyncio.to_thread(_fetch, conn, sql, params)
                        except Exception as exc:
                            # Counted (partial); the table's position is untouched.
                            failures.add(f"table {table}", exc)
                            break
                        for row in rows:
                            key = [row.get(c) for c in pk_cols]
                            position = positions.advance(table, row.get(cursor_col), key)
                            pos = positions.get(table)
                            desc, ident = key_desc(pk_cols, row)
                            row_key = self._row_key(config, table, pk_cols, row)
                            yield RawDocument(
                                doc_id=stable_doc_id(config, row_key),
                                source_id=config.source_id,
                                tenant_id=config.tenant_id,
                                source_url=f"mysql://{cc.get('host')}/{cc.get('database')}/"
                                f"{table}/{ident}",
                                title=f"{table} {ident}",
                                content=row_text(table, desc, row).encode("utf-8"),
                                content_type="text/plain",
                                modified_at=str(row.get(cursor_col) or ""),
                                metadata={"table": table, "pk": ident},
                            ), position
                        if len(rows) < batch:
                            break
            finally:
                await asyncio.to_thread(conn.close)
        failures.raise_if_any()

    async def _query_mode(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.connectors.sql_rows import row_text
        from app.ingestion.source_config import RawDocument
        from app.ingestion.sql_safety import bind_cursor_placeholder

        cc = config.connection_config
        query, has_braces = bind_cursor_placeholder(str(cc.get("query", "")), "%s")
        cursor_col = cc.get("cursor_field") or cc.get("cursor_column") or "updated_at"
        params = (cursor,) if cursor and (has_braces or "%s" in query) else ()

        def _run(pinned_host: str) -> list[dict]:
            conn = self._connect(cc, host=pinned_host)
            try:
                self._harden(conn)
                return _fetch(conn, query, list(params))
            finally:
                conn.close()

        async with pin_source_hosts(
            [(cc.get("host", ""), cc.get("port", 3306))], context="mysql"
        ) as pins:
            try:
                rows = await asyncio.to_thread(_run, pins.ip(cc.get("host", "")))
            except ImportError as exc:
                raise ConnectorUnavailableError(
                    f"MySQL driver not installed (pymysql or mysqlclient): {exc}"
                ) from exc
            except Exception as exc:
                raise ConnectorFetchError(f"mysql: {describe_fetch_error(exc)}") from exc

        new_cursor = cursor or ""
        for row in rows:
            row_dict = dict(row)
            value = row_dict.get(cursor_col)
            if value is not None:
                new_cursor = str(value)
            row_key = row_identity(row_dict, cc.get("id_column"))
            yield RawDocument(
                doc_id=stable_doc_id(config, row_key),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=f"mysql://{cc.get('host')}/{cc.get('database')}/row/{row_key}",
                content=row_text("row", row_key, row_dict).encode("utf-8"),
                content_type="text/plain",
                metadata={k: _json_safe(v) for k, v in list(row_dict.items())[:50]},
            ), new_cursor

    async def iter_live_doc_ids(self, config: SourceConfig) -> AsyncIterator[str]:
        """Primary keys of every configured table, keyset-paged (KB-44 reconcile).

        A tenant-written query cannot be listed: nothing is ever deleted then.
        Any error propagates — a partial listing must never look complete.
        """
        from app.ingestion.base_connector import LiveListingUnavailableError

        cc = config.connection_config
        if cc.get("query"):
            raise LiveListingUnavailableError("mysql query mode")
        async with pin_source_hosts(
            [(cc.get("host", ""), cc.get("port", 3306))], context="mysql"
        ) as pins:
            conn = await self._open(cc, pins.ip(cc.get("host", "")))
            try:
                for table in self._tables(cc):
                    pk_cols = await asyncio.to_thread(self._primary_key, conn, cc, table)
                    order = ", ".join(_q(c) for c in pk_cols)
                    after: list[Any] | None = None
                    while True:
                        if after is None:
                            sql, params = (f"SELECT {order} FROM {_q(table)} ORDER BY {order} "
                                           f"LIMIT {_LIVE_PAGE}", [])
                        else:
                            marks = ", ".join(["%s"] * len(pk_cols))
                            sql, params = (f"SELECT {order} FROM {_q(table)} WHERE ({order}) > "
                                           f"({marks}) ORDER BY {order} LIMIT {_LIVE_PAGE}", after)
                        rows = await asyncio.to_thread(_fetch, conn, sql, params)
                        for row in rows:
                            yield stable_doc_id(config, self._row_key(config, table, pk_cols, row))
                        if len(rows) < _LIVE_PAGE:
                            break
                        after = [rows[-1].get(c) for c in pk_cols]
            finally:
                await asyncio.to_thread(conn.close)


def _first(row: Any) -> Any:
    if isinstance(row, dict):
        return next(iter(row.values()), None)
    if isinstance(row, tuple | list):
        return row[0] if row else None
    return row


def _fetch(conn: Any, sql: str, params: list[Any]) -> list[dict]:
    cur = conn.cursor()
    cur.execute(sql, tuple(params) if params else ())
    return [dict(r) for r in cur.fetchall()]
