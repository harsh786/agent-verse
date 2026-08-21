"""BigQueryConnector — Google BigQuery analytics ingestion.

Modes:
  - query: execute a SQL query, each row → RawDocument
  - table: full or incremental scan of a table using a timestamp partition column

Cursor: last row's partition column value.
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


@register("bigquery", feature_flag="ingestion_connector_bigquery_enabled")
class BigQueryConnector(BaseConnector):
    """Google BigQuery connector — query and table modes."""

    source_type = "bigquery"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            import json
            import os
            import tempfile

            from google.cloud import bigquery  # type: ignore[import-not-found]

            creds_json = config.connection_config.get("service_account_json")
            project = config.connection_config.get("project", "")

            if isinstance(creds_json, dict):
                tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
                json.dump(creds_json, tmp)
                tmp.close()
                client = bigquery.Client.from_service_account_json(tmp.name, project=project)
                os.unlink(tmp.name)
            else:
                client = bigquery.Client(project=project)

            # Cheap query to verify access
            list(client.list_datasets(max_results=1))
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
        except ImportError:
            _log.error("google-cloud-bigquery not installed")
            return

        import json
        import os
        import tempfile

        cc = config.connection_config
        creds_json = cc.get("service_account_json")
        project = cc.get("project", "")
        mode = cc.get("mode", "query")
        cursor_col = cc.get("cursor_column", "updated_at")
        batch_size = int(cc.get("batch_size", 1000))

        if isinstance(creds_json, dict):
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
            json.dump(creds_json, tmp)
            tmp.close()
            client = bigquery.Client.from_service_account_json(tmp.name, project=project)
            os.unlink(tmp.name)
        else:
            client = bigquery.Client(project=project)

        if mode == "table":
            table_ref = cc.get("table", "")
            sql = f"SELECT * FROM `{table_ref}`"
            if cursor:
                sql += f" WHERE {cursor_col} > '{cursor}'"
            sql += f" ORDER BY {cursor_col} LIMIT {batch_size}"
        else:
            sql = cc.get("query", "SELECT 1")
            if cursor and "{cursor}" in sql:
                sql = sql.replace("{cursor}", cursor)

        new_cursor = cursor or ""
        for row in client.query(sql).result():
            row_dict = dict(row)
            new_cursor = str(row_dict.get(cursor_col, new_cursor))
            text = "\n".join(f"{k}: {v}" for k, v in row_dict.items() if v is not None)
            doc = RawDocument(
                doc_id=str(uuid.uuid4()),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=f"bigquery://{project}/row/{uuid.uuid4()}",
                content=text.encode(),
                content_type="text/plain",
                metadata=row_dict,
            )
            yield doc, new_cursor
