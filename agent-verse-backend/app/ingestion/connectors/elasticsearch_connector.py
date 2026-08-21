"""ElasticsearchConnector — Elasticsearch / OpenSearch document ingestion.

Cursor: last document's sort value (typically a timestamp or _id).
Uses search_after for deep pagination (no 10k limit).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.ingestion.base_connector import BaseConnector, ConnectionHealth
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


@register("elasticsearch", feature_flag="ingestion_connector_elasticsearch_enabled")
@register("opensearch")
class ElasticsearchConnector(BaseConnector):
    """Elasticsearch / OpenSearch connector — index-based ingestion."""

    source_type = "elasticsearch"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        import httpx

        t0 = time.perf_counter()
        try:
            cc = config.connection_config
            url = cc.get("url", "http://localhost:9200")
            auth = (cc.get("username", ""), cc.get("password", "")) if cc.get("username") else None
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url, auth=auth)
                r.raise_for_status()
                info = r.json()
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(
                ok=True,
                latency_ms=latency,
                metadata={
                    "version": info.get("version", {}).get("number"),
                    "cluster": info.get("cluster_name"),
                },
            )
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        import httpx

        from app.ingestion.source_config import RawDocument

        cc = config.connection_config
        base_url = cc.get("url", "http://localhost:9200").rstrip("/")
        index = cc.get("index", "_all")
        auth = (cc.get("username", ""), cc.get("password", "")) if cc.get("username") else None
        batch_size = int(cc.get("batch_size", 500))
        sort_field = cc.get("sort_field", "@timestamp")
        cursor_parsed: list | None = json.loads(cursor) if cursor else None

        new_cursor: list = cursor_parsed or []

        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                body: dict = {
                    "size": batch_size,
                    "sort": [{sort_field: "asc"}, {"_id": "asc"}],
                    "query": {"match_all": {}},
                }
                if cursor_parsed:
                    body["search_after"] = cursor_parsed

                r = await client.post(
                    f"{base_url}/{index}/_search",
                    json=body,
                    auth=auth,
                )
                if not r.is_success:
                    _log.warning("elasticsearch: %d %s", r.status_code, r.text[:200])
                    break

                hits = r.json().get("hits", {}).get("hits", [])
                if not hits:
                    break

                for hit in hits:
                    sort_vals = hit.get("sort", [])
                    new_cursor = sort_vals if sort_vals else new_cursor
                    source = hit.get("_source", {})
                    text = json.dumps(source, ensure_ascii=False, indent=2)
                    doc = RawDocument(
                        doc_id=str(uuid.uuid4()),
                        source_id=config.source_id,
                        tenant_id=config.tenant_id,
                        source_url=f"{base_url}/{index}/_doc/{hit.get('_id')}",
                        content=text.encode(),
                        content_type="application/json",
                        metadata={"index": index, "_id": hit.get("_id")},
                    )
                    yield doc, json.dumps(new_cursor)

                if len(hits) < batch_size:
                    break
                cursor_parsed = new_cursor
