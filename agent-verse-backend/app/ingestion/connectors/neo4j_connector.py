"""Neo4jConnector — Neo4j graph database ingestion.

Cursor: last node's lastModified property or internal ID.
Exports nodes and relationships as structured text for embedding.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    ConnectorUnavailableError,
    stable_doc_id,
)
from app.ingestion.connector_egress import pin_source_dsn, run_driver_call
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)


def _node_identity(node: dict[str, object]) -> str:
    """The node's element id; a row without one (a projection) by its values."""
    from app.ingestion.base_connector import row_identity

    node_id = node.get("_id")
    return str(node_id) if node_id not in (None, "") else row_identity(node, "__no_key__")


@register("neo4j", feature_flag="ingestion_connector_neo4j_enabled")
class Neo4jConnector(BaseConnector):
    """Neo4j graph database connector — nodes, relationships, and Cypher queries."""

    source_type = "neo4j"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            cc = config.connection_config
            def _verify() -> None:
                from neo4j import GraphDatabase  # type: ignore[import-not-found]

                with GraphDatabase.driver(
                    cc.get("uri", ""),
                    auth=(cc.get("username", "neo4j"), cc.get("password", "")),
                ) as driver:
                    driver.verify_connectivity()

            # No bolt://localhost default, the host must resolve public, and the
            # driver's own lookups answer with the checked addresses only —
            # including the routers/readers/writers a neo4j:// routing table names.
            async with pin_source_dsn(cc.get("uri", ""), context="neo4j"):
                await run_driver_call(_verify, context="neo4j")
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

        cc = config.connection_config
        neo4j_uri = cc.get("uri", "")
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
            from neo4j import GraphDatabase

            with GraphDatabase.driver(neo4j_uri, auth=auth) as driver, driver.session() as session:
                result = session.run(cypher)
                return [dict(record) for record in result]

        async with pin_source_dsn(neo4j_uri, context="neo4j"):
            try:
                import neo4j  # type: ignore[import-not-found]  # noqa: F401
            except ImportError as exc:
                # Returning nothing here reported a successful, empty sync.
                raise ConnectorUnavailableError(
                    "neo4j is not installed on this server; the connector cannot run"
                ) from exc
            # A neo4j:// routing table makes the driver dial hosts the *server*
            # names; run_driver_call egress-checks every one of those lookups.
            records = await run_driver_call(_run_query, context="neo4j")

        new_cursor = cursor or ""
        for record in records:
            # Extract the node (or first value in record)
            node = None
            for v in record.values():
                if hasattr(v, "_properties"):  # neo4j Node
                    node = dict(v._properties)
                    node["_labels"] = list(v.labels)
                    node["_id"] = getattr(v, "element_id", None) or v.id
                    break
            if node is None:
                node = record

            cursor_val = str(node.get(cursor_prop, node.get("_id", "")))
            new_cursor = max(new_cursor, cursor_val)

            labels_str = ", ".join(node.get("_labels") or [])
            props_text = "\n".join(f"  {k}: {v}" for k, v in node.items() if not k.startswith("_"))
            text = f"Node [{labels_str}]\n{props_text}"

            doc = RawDocument(
                doc_id=stable_doc_id(config, _node_identity(node)),
                source_id=config.source_id,
                tenant_id=config.tenant_id,
                source_url=f"neo4j://{neo4j_uri}/node/{_node_identity(node)}",
                content=text.encode(),
                content_type="text/plain",
                metadata={"labels": labels_str, "id": node.get("_id")},
            )
            yield doc, new_cursor
