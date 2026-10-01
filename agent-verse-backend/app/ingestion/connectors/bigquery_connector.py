"""BigQueryConnector — Google BigQuery analytics ingestion.

Modes:
  - query: execute a SQL query, each row → RawDocument
  - table: full or incremental scan of a table using a timestamp partition column

Cursor: last row's partition column value.

google-cloud-bigquery is blocking (client construction may itself fetch
credentials): every call runs on the SDK pool, and result rows are paged in off
the event loop.
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


def _quoted(parts: list[str]) -> str:
    return ".".join(f"`{part}`" for part in parts)


def _make_client(bigquery: Any, creds_json: Any, project: str) -> Any:
    import json
    import os
    import tempfile

    if isinstance(creds_json, dict):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            json.dump(creds_json, tmp)
        try:
            return bigquery.Client.from_service_account_json(tmp.name, project=project)
        finally:
            os.unlink(tmp.name)
    return bigquery.Client(project=project)


@register("bigquery", feature_flag="ingestion_connector_bigquery_enabled")
class BigQueryConnector(BaseConnector):
    """Google BigQuery connector — query and table modes."""

    source_type = "bigquery"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            from google.cloud import bigquery  # type: ignore[import-not-found]

            creds_json = config.connection_config.get("service_account_json")
            project = config.connection_config.get("project", "")

            def _probe() -> None:
                client = _make_client(bigquery, creds_json, project)
                # Cheap query to verify access
                list(client.list_datasets(max_results=1))

            await run_blocking(_probe)
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(ok=True, latency_ms=latency, metadata={"project": project})
        except ImportError:
            return ConnectionHealth(ok=False, error="google-cloud-bigquery not installed")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument

        try:
            from google.cloud import bigquery  # type: ignore[import-not-found]
        except ImportError as exc:
            # Returning nothing here reported a successful, empty sync.
            raise ConnectorUnavailableError(
                "google-cloud-bigquery is not installed on this server; the connector cannot run"
            ) from exc

        cc = config.connection_config
        creds_json = cc.get("service_account_json")
        project = cc.get("project", "")
        mode = cc.get("mode", "query")
        cursor_col = cc.get("cursor_column", "updated_at")
        batch_size = int(cc.get("batch_size", 1000))

        # The cursor (a value read back from the source's rows) is a STRING query
        # parameter — BigQuery coerces STRING parameters to DATE/DATETIME/TIMESTAMP
        # exactly as it did the old literal — never pasted into the SQL. Identifiers
        # must be plain names (a hyphenated project id is allowed) and are
        # backtick-quoted part by part.
        bind_cursor = False
        if mode == "table":
            table = _quoted(
                checked_identifier(cc.get("table", ""), what="table", bigquery_project=True)
            )
            column = _quoted(checked_identifier(cursor_col, what="cursor_column", max_parts=1))
            sql = f"SELECT * FROM {table}"
            if cursor:
                sql += f" WHERE {column} > @{CURSOR_PARAM}"
                bind_cursor = True
            sql += f" ORDER BY {column} LIMIT {batch_size}"
        else:
            sql = cc.get("query", "SELECT 1")
            if cursor:
                sql, bind_cursor = bind_cursor_placeholder(sql, f"@{CURSOR_PARAM}")

        def _run_query() -> Any:
            client = _make_client(bigquery, creds_json, project)
            if not bind_cursor:
                return client.query(sql).result()
            job_config = bigquery.QueryJobConfig(
                query_parameters=[bigquery.ScalarQueryParameter(CURSOR_PARAM, "STRING", cursor)]
            )
            return client.query(sql, job_config=job_config).result()

        rows = await run_blocking(_run_query)
        new_cursor = cursor or ""
        async for row in iterate_blocking(rows, chunk_size=500):
            row_dict = dict(row)
            new_cursor = str(row_dict.get(cursor_col, new_cursor))
            text = "\n".join(f"{k}: {v}" for k, v in row_dict.items() if v is not None)
            row_key = row_identity(row_dict, cc.get("id_column"))
            doc = RawDocument(
                doc_id=stable_doc_id(config, row_key),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=f"bigquery://{project}/row/{row_key}",
                content=text.encode(),
                content_type="text/plain",
                metadata=row_dict,
            )
            yield doc, new_cursor
