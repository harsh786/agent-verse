"""Neo4jConnector — Neo4j graph database ingestion.

Cursor: last node's lastModified property or internal ID.
Exports nodes and relationships as structured text for embedding.
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


@register("neo4j", feature_flag="ingestion_connector_neo4j_enabled")
class Neo4jConnector(BaseConnector):
    """Neo4j graph database connector — nodes, relationships, and Cypher queries."""

    source_type = "neo4j"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            from neo4j import GraphDatabase  # type: ignore[import-not-found]

            cc = config.connection_config
            with GraphDatabase.driver(
                cc.get("uri", "bolt://localhost:7687"),
                auth=(cc.get("username", "neo4j"), cc.get("password", "")),
            ) as driver:
                driver.verify_connectivity()
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(ok=True, latency_ms=latency, metadata={"uri": cc.get("uri")})
        except ImportError:
            return ConnectionHealth(ok=False, error="neo4j not installed — pip install neo4j")
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument

        try:
            from neo4j import GraphDatabase  # type: ignore[import-not-found]
        except ImportError:
            _log.error("neo4j not installed")
            return

        import asyncio

        cc = config.connection_config
        neo4j_uri = cc.get("uri", "bolt://localhost:7687")
        auth = (cc.get("username", "neo4j"), cc.get("password", ""))
        cypher = cc.get("cypher", "")
        node_labels = cc.get("node_labels") or []
        batch_size = int(cc.get("batch_size", 500))
        cursor_prop = cc.get("cursor_property", "id")

        if not cypher:
            if node_labels:
                label_filter = ":".join(node_labels)
                cypher = f"MATCH (n:{label_filter}) RETURN n LIMIT {batch_size}"
            else:
                cypher = f"MATCH (n) RETURN n LIMIT {batch_size}"

        def _run_query():
            with GraphDatabase.driver(neo4j_uri, auth=auth) as driver:
                with driver.session() as session:
                    result = session.run(cypher)
                    return [dict(record) for record in result]

        loop = asyncio.get_event_loop()
        records = await loop.run_in_executor(None, _run_query)

        new_cursor = cursor or ""
        for record in records:
            # Extract the node (or first value in record)
            node = None
            for v in record.values():
                if hasattr(v, "_properties"):  # neo4j Node
                    node = dict(v._properties)
                    node["_labels"] = list(v.labels)
                    node["_id"] = v.id
                    break
            if node is None:
                node = record

            cursor_val = str(node.get(cursor_prop, node.get("_id", "")))
            new_cursor = max(new_cursor, cursor_val)

            labels_str = ", ".join(node.get("_labels") or [])
            props_text = "\n".join(f"  {k}: {v}" for k, v in node.items() if not k.startswith("_"))
            text = f"Node [{labels_str}]\n{props_text}"

            doc = RawDocument(
                doc_id=str(uuid.uuid4()),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=f"neo4j://{neo4j_uri}/node/{node.get('_id', uuid.uuid4())}",
                content=text.encode(),
                content_type="text/plain",
                metadata={"labels": labels_str, "id": node.get("_id")},
            )
            yield doc, new_cursor
