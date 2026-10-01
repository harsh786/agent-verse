"""DuckDBConnector — DuckDB local analytics database ingestion.

DuckDB can query Parquet, CSV, JSON, Arrow, and Delta Lake files directly.
Modes:
  - query: arbitrary SQL SELECT
  - file: DuckDB auto-scans a file or glob pattern (read_parquet, read_csv, etc.)

Cursor: last row's ORDER BY column value.

Security: DuckDB runs *in-process* on the API/worker host, so an unconfined
connection would let tenant SQL (``read_csv('/etc/passwd')``, ``ATTACH``,
``INSTALL httpfs`` ...) read host files or reach internal networks. Every
connection is therefore opened with ``enable_external_access = false`` and
``allowed_directories`` pinned to the tenant's own data directory
(``<duckdb_data_root>/<tenant_id>/``), with the configuration locked so SQL
cannot re-enable access. ``database`` and ``file`` paths are additionally
resolved in Python and rejected when they escape that directory.
"""

from __future__ import annotations

import logging
import pathlib
import re
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_registry import register
from app.ingestion.sdk_executor import run_blocking

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class DuckDBPathError(PermissionError):
    """Raised when a DuckDB database/file path escapes the tenant directory."""


def _tenant_root(tenant_id: str) -> pathlib.Path:
    from app.core.config import get_settings

    if not tenant_id or "/" in tenant_id or "\\" in tenant_id or tenant_id in {".", ".."}:
        raise DuckDBPathError(f"invalid tenant id for DuckDB data root: {tenant_id!r}")
    return (pathlib.Path(get_settings().duckdb_data_root) / tenant_id).resolve()


def _confine(path: str, root: pathlib.Path, *, what: str) -> str:
    """Resolve *path* relative to *root*; raise if it escapes (symlinks included)."""
    if not path or "\x00" in path:
        raise DuckDBPathError(f"DuckDB {what} path is empty or invalid")
    if "://" in path or path.startswith(("md:", "motherduck:")):
        raise DuckDBPathError(f"DuckDB {what} must be a local tenant path, got {path!r}")
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise DuckDBPathError(
            f"DuckDB {what} path {path!r} resolves outside the tenant data directory"
        ) from exc
    return str(resolved)


def _open_confined(duckdb: Any, db_setting: str, tenant_id: str) -> tuple[Any, str]:
    """Open a DuckDB connection whose external access is confined to the tenant root."""
    root = _tenant_root(tenant_id)
    if db_setting in ("", ":memory:"):
        db_path = ":memory:"
        read_only = False  # DuckDB refuses read-only in-memory databases
    else:
        db_path = _confine(db_setting, root, what="database")
        read_only = True
    config = {
        "enable_external_access": False,
        "allowed_directories": [str(root) + "/"],
        "autoinstall_known_extensions": False,
        "autoload_known_extensions": False,
        "lock_configuration": True,
    }
    con = duckdb.connect(db_path, read_only=read_only, config=config)
    return con, db_path


@register("duckdb", feature_flag="ingestion_connector_duckdb_enabled")
class DuckDBConnector(BaseConnector):
    """DuckDB connector — in-process analytics with multi-format file support."""

    source_type = "duckdb"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            import duckdb  # type: ignore[import-not-found]

            def _probe() -> str:
                con, db_path = _open_confined(
                    duckdb,
                    str(config.connection_config.get("database", ":memory:")),
                    config.tenant_id,
                )
                try:
                    con.execute("SELECT 1")
                finally:
                    con.close()
                return db_path

            # In-process engine: file I/O and query work happen on the SDK pool.
            db_path = await run_blocking(_probe)
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(ok=True, latency_ms=latency, metadata={"database": db_path})
        except ImportError:
            return ConnectionHealth(ok=False, error="duckdb not installed")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument

        try:
            import duckdb  # type: ignore[import-not-found]
        except ImportError:
            _log.error("duckdb not installed")
            return

        cc = config.connection_config
        mode = cc.get("mode", "query")
        cursor_col = str(cc.get("cursor_column", "updated_at"))
        batch_size = int(cc.get("batch_size", 1000))
        if not _IDENT_RE.match(cursor_col):
            raise ValueError(f"invalid DuckDB cursor_column {cursor_col!r}")

        file_path = ""
        if mode == "file":
            file_path = _confine(
                str(cc.get("file", "")), _tenant_root(config.tenant_id), what="file"
            )

        # In-process engine: opening, querying and fetching are blocking file/CPU
        # work, so they run on the SDK pool rather than the event loop.
        con, db_path = await run_blocking(
            _open_confined, duckdb, str(cc.get("database", ":memory:")), config.tenant_id
        )
        try:
            if mode == "file":
                safe_file = file_path.replace("'", "''")
                query = f"SELECT * FROM '{safe_file}'"
                if cursor:
                    safe_cursor = cursor.replace("'", "''")
                    query += f" WHERE \"{cursor_col}\" > '{safe_cursor}'"
                query += f" LIMIT {batch_size}"
            else:
                query = cc.get("query", "SELECT 1")
                if cursor and "{cursor}" in query:
                    query = query.replace("{cursor}", cursor.replace("'", "''"))

            def _run(sql: str) -> tuple[list[str], list[Any]]:
                result = con.execute(sql)
                return [d[0] for d in result.description], result.fetchall()

            col_names, rows = await run_blocking(_run, query)
            new_cursor = cursor or ""

            for row in rows:
                row_dict = dict(zip(col_names, row, strict=False))
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
            await run_blocking(con.close)
