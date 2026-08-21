"""MySQLConnector — MySQL / MariaDB database ingestion.

Uses mysqlclient or PyMySQL.
Cursor: last row's ORDER BY column value (typically a timestamp or auto-increment ID).
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


@register("mysql", feature_flag="ingestion_connector_mysql_enabled")
@register("mariadb")
class MySQLConnector(BaseConnector):
    """MySQL / MariaDB connector — query-based incremental ingestion."""

    source_type = "mysql"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        cc = config.connection_config
        try:
            conn = self._connect(cc)
            cur = conn.cursor()
            cur.execute("SELECT VERSION()")
            version = cur.fetchone()[0]
            conn.close()
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(ok=True, latency_ms=latency, metadata={"version": version})
        except ImportError as exc:
            return ConnectionHealth(ok=False, error=f"MySQL driver not installed: {exc}")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    @staticmethod
    def _connect(cc: dict):
        try:
            import pymysql  # type: ignore[import-not-found]

            return pymysql.connect(
                host=cc.get("host", "localhost"),
                port=int(cc.get("port", 3306)),
                user=cc.get("username", ""),
                password=cc.get("password", ""),
                database=cc.get("database", ""),
                connect_timeout=10,
                cursorclass=pymysql.cursors.DictCursor,
            )
        except ImportError:
            import MySQLdb  # type: ignore[import-not-found]

            return MySQLdb.connect(
                host=cc.get("host", "localhost"),
                port=int(cc.get("port", 3306)),
                user=cc.get("username", ""),
                passwd=cc.get("password", ""),
                db=cc.get("database", ""),
                cursorclass=MySQLdb.cursors.DictCursor,
                connect_timeout=10,
            )

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        import asyncio

        from app.ingestion.source_config import RawDocument

        cc = config.connection_config
        query = cc.get("query", "")
        cursor_col = cc.get("cursor_column", "updated_at")
        batch_size = int(cc.get("batch_size", 1000))
        table = cc.get("table", "")

        if not query and table:
            query = f"SELECT * FROM `{table}`"
            if cursor:
                query += f" WHERE {cursor_col} > %s ORDER BY {cursor_col} LIMIT {batch_size}"
            else:
                query += f" ORDER BY {cursor_col} LIMIT {batch_size}"

        def _fetch():
            conn = self._connect(cc)
            try:
                cur = conn.cursor()
                cur.execute(query, (cursor,) if cursor and "%s" in query else ())
                return cur.fetchall()
            finally:
                conn.close()

        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(None, _fetch)

        new_cursor = cursor or ""
        for row in rows:
            row_dict = dict(row)
            new_cursor = str(row_dict.get(cursor_col, new_cursor))
            text = "\n".join(f"{k}: {v}" for k, v in row_dict.items() if v is not None)
            doc = RawDocument(
                doc_id=str(uuid.uuid4()),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=f"mysql://{cc.get('host')}/{cc.get('database')}/row/{uuid.uuid4()}",
                content=text.encode(),
                content_type="text/plain",
                metadata=row_dict,
            )
            yield doc, new_cursor
