"""MongoDBConnector — MongoDB document database ingestion.

Cursor: last document's _id (ObjectId as string) for incremental fetch.
Supports: MongoDB Atlas, self-hosted, and DocumentDB-compatible.
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


def _flatten_doc(doc: dict, max_depth: int = 5) -> str:
    """Flatten a MongoDB document to key: value pairs."""
    parts: list[str] = []

    def _recurse(obj: object, prefix: str = "", depth: int = 0):
        if depth > max_depth:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                _recurse(v, f"{prefix}.{k}" if prefix else k, depth + 1)
        elif isinstance(obj, (list, tuple)):
            for i, v in enumerate(obj[:20]):
                _recurse(v, f"{prefix}[{i}]", depth + 1)
        else:
            parts.append(f"{prefix}: {obj}")

    _recurse(doc)
    return "\n".join(parts)


@register("mongodb", feature_flag="ingestion_connector_mongodb_enabled")
class MongoDBConnector(BaseConnector):
    """MongoDB connector — collection-based incremental ingestion."""

    source_type = "mongodb"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            from pymongo import MongoClient  # type: ignore[import-not-found]

            cc = config.connection_config
            client = MongoClient(
                cc.get("uri")
                or f"mongodb://{cc.get('username', '')}:{cc.get('password', '')}@{cc.get('host', 'localhost')}:{cc.get('port', 27017)}/",
                serverSelectionTimeoutMS=5000,
            )
            client.admin.command("ping")
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(ok=True, latency_ms=latency, metadata={"host": cc.get("host")})
        except ImportError:
            return ConnectionHealth(ok=False, error="pymongo not installed")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument

        try:
            from bson import ObjectId  # type: ignore[import-not-found]
            from pymongo import MongoClient  # type: ignore[import-not-found]
        except ImportError:
            _log.error("pymongo not installed")
            return

        import asyncio

        cc = config.connection_config
        uri = (
            cc.get("uri")
            or f"mongodb://{cc.get('username', '')}:{cc.get('password', '')}@{cc.get('host', 'localhost')}:{cc.get('port', 27017)}/"
        )
        db_name = cc.get("database", "")
        collection_name = cc.get("collection", "")
        batch_size = int(cc.get("batch_size", 500))
        cursor_field = cc.get("cursor_field", "_id")

        def _fetch():
            client = MongoClient(uri)
            db = client[db_name]
            col = db[collection_name]
            query: dict = {}
            if cursor and cursor_field == "_id":
                try:
                    query["_id"] = {"$gt": ObjectId(cursor)}
                except Exception:
                    pass
            elif cursor and cursor_field != "_id":
                query[cursor_field] = {"$gt": cursor}
            return list(col.find(query).sort(cursor_field, 1).limit(batch_size))

        loop = asyncio.get_event_loop()
        docs = await loop.run_in_executor(None, _fetch)

        new_cursor = cursor or ""
        for doc in docs:
            doc_id_val = doc.get("_id")
            new_cursor = str(doc_id_val) if doc_id_val else new_cursor
            # Remove ObjectId for serialization
            doc["_id"] = str(doc.get("_id", ""))
            text = _flatten_doc(doc)
            raw_doc = RawDocument(
                doc_id=str(uuid.uuid4()),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=f"mongodb://{cc.get('host')}/{db_name}/{collection_name}/{doc.get('_id')}",
                content=text.encode(),
                content_type="text/plain",
                metadata={"collection": collection_name, "_id": doc.get("_id")},
            )
            yield raw_doc, new_cursor
