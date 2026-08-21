"""In-memory knowledge graph store with optional DB persistence."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.db.rls import sqlalchemy_rls_context
from app.knowledge_graph.models import EdgeType, GraphEdge, GraphNode, NodeType

_log = logging.getLogger(__name__)


class KnowledgeGraphStore:
    """Tenant-scoped in-memory knowledge graph with DB persistence."""

    def __init__(self) -> None:
        self._nodes: dict[str, GraphNode] = {}  # node_id → node
        self._edges: dict[str, GraphEdge] = {}  # edge_id → edge
        self._tenant_nodes: dict[str, set[str]] = {}  # tenant_id → {node_ids}
        self._tenant_edges: dict[str, set[str]] = {}  # tenant_id → {edge_ids}
        self._db: Any = None  # set via set_db() in lifespan
        self._hydrated_tenants: set[str] = set()

    # ------------------------------------------------------------------
    # DB wiring
    # ------------------------------------------------------------------

    def set_db(self, db_factory: Any) -> None:
        """Wire an async SQLAlchemy session factory for persistent storage."""
        self._db = db_factory

    # ------------------------------------------------------------------
    # In-memory operations (unchanged public interface)
    # ------------------------------------------------------------------

    def add_node(self, node: GraphNode) -> None:
        """Add or update a node (upsert by node_id)."""
        self._nodes[node.node_id] = node
        self._tenant_nodes.setdefault(node.tenant_id, set()).add(node.node_id)
        # Fire-and-forget async DB persist
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                _task = asyncio.create_task(self._persist_node_to_db(node))
                _task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        except Exception:
            pass

    def add_edge(self, edge: GraphEdge) -> None:
        """Add an edge."""
        self._edges[edge.edge_id] = edge
        self._tenant_edges.setdefault(edge.tenant_id, set()).add(edge.edge_id)
        # Fire-and-forget async DB persist
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                _task = asyncio.create_task(self._persist_edge_to_db(edge))
                _task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        except Exception:
            pass

    def get_node(self, node_id: str, tenant_id: str) -> GraphNode | None:
        node = self._nodes.get(node_id)
        return node if node and node.tenant_id == tenant_id else None

    def query_nodes(
        self,
        tenant_id: str,
        node_type: NodeType | None = None,
        search: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[GraphNode]:
        """Query nodes by type, search text, and confidence."""
        # Lazy per-tenant DB hydration (runs once per tenant per process lifetime)
        if self._db is not None and tenant_id not in self._hydrated_tenants:
            self._hydrated_tenants.add(tenant_id)  # mark before load to prevent recursion
            try:
                import asyncio

                task = asyncio.ensure_future(self.load_from_db(tenant_id))
                task.add_done_callback(
                    lambda completed: completed.exception() if not completed.cancelled() else None
                )
            except RuntimeError:
                pass  # no event loop running (e.g. tests)
        node_ids = self._tenant_nodes.get(tenant_id, set())
        nodes = [self._nodes[nid] for nid in node_ids if nid in self._nodes]

        if node_type:
            nodes = [n for n in nodes if n.node_type == node_type]
        if search:
            sq = search.lower()
            nodes = [n for n in nodes if sq in n.label.lower() or sq in n.content.lower()]
        if min_confidence > 0:
            nodes = [n for n in nodes if n.confidence >= min_confidence]

        nodes.sort(key=lambda n: (-n.confidence, n.label))
        return nodes[:limit]

    def get_edges_for_node(self, node_id: str, tenant_id: str) -> list[GraphEdge]:
        """Get all edges connected to a node."""
        edge_ids = self._tenant_edges.get(tenant_id, set())
        return [
            self._edges[eid]
            for eid in edge_ids
            if eid in self._edges
            and (
                self._edges[eid].source_node_id == node_id
                or self._edges[eid].target_node_id == node_id
            )
        ]

    def find_path(
        self, source_id: str, target_id: str, tenant_id: str, max_hops: int = 3
    ) -> list[list[str]]:
        """BFS to find paths between two nodes (max_hops limit)."""
        if source_id == target_id:
            return [[source_id]]

        edge_ids = self._tenant_edges.get(tenant_id, set())
        # Build adjacency map
        adj: dict[str, list[str]] = {}
        for eid in edge_ids:
            e = self._edges.get(eid)
            if e:
                adj.setdefault(e.source_node_id, []).append(e.target_node_id)

        # BFS
        from collections import deque

        queue: deque[list[str]] = deque([[source_id]])
        visited = {source_id}
        paths: list[list[str]] = []

        while queue and len(paths) < 5:
            path = queue.popleft()
            if len(path) > max_hops + 1:
                break
            current = path[-1]
            for neighbor in adj.get(current, []):
                if neighbor == target_id:
                    paths.append([*path, neighbor])
                elif neighbor not in visited:
                    visited.add(neighbor)
                    queue.append([*path, neighbor])

        return paths

    def get_graph_stats(self, tenant_id: str) -> dict[str, Any]:
        """Return graph statistics for a tenant."""
        node_ids = self._tenant_nodes.get(tenant_id, set())
        edge_ids = self._tenant_edges.get(tenant_id, set())

        nodes = [self._nodes[nid] for nid in node_ids if nid in self._nodes]
        type_counts: dict[str, int] = {}
        for n in nodes:
            type_counts[n.node_type.value] = type_counts.get(n.node_type.value, 0) + 1

        return {
            "total_nodes": len(nodes),
            "total_edges": len(edge_ids),
            "node_types": type_counts,
            "avg_confidence": sum(n.confidence for n in nodes) / max(len(nodes), 1),
        }

    def delete_tenant_graph(self, tenant_id: str) -> None:
        """Delete all graph data for a tenant."""
        for nid in list(self._tenant_nodes.get(tenant_id, set())):
            self._nodes.pop(nid, None)
        for eid in list(self._tenant_edges.get(tenant_id, set())):
            self._edges.pop(eid, None)
        self._tenant_nodes.pop(tenant_id, None)
        self._tenant_edges.pop(tenant_id, None)

    def detect_communities(self, tenant_id: str) -> list[dict[str, Any]]:
        """Community detection using Union-Find connected components via CommunityDetector."""
        from app.knowledge_graph.community_detection import CommunityDetector

        node_ids = self._tenant_nodes.get(tenant_id, set())
        edge_ids = self._tenant_edges.get(tenant_id, set())

        nodes = [self._nodes[nid] for nid in node_ids if nid in self._nodes]
        edges = [self._edges[eid] for eid in edge_ids if eid in self._edges]

        detector = CommunityDetector()
        raw_communities = detector.detect_communities(nodes, edges)

        # Enrich with tenant context and human-readable fields
        communities = []
        for c in raw_communities:
            central = c.get("central_node", "")
            label = self._nodes[central].label if central in self._nodes else "Community"
            communities.append(
                {
                    **c,
                    "tenant_id": tenant_id,
                    "name": f"Cluster: {label}",
                    "summary": f"Connected component with {c['size']} nodes",
                }
            )

        return communities

    # ------------------------------------------------------------------
    # DB persistence helpers
    # ------------------------------------------------------------------

    async def _persist_node_to_db(self, node: GraphNode) -> None:
        """Persist a node to DB (best-effort, upsert)."""
        if not self._db:
            return
        try:
            from sqlalchemy import text as _t

            now = datetime.now(UTC)
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, node.tenant_id),
            ):
                await session.execute(
                    _t(
                        """
                        INSERT INTO knowledge_nodes
                            (id, tenant_id, node_type, label, content, source_id,
                             confidence, extra_metadata, created_at, updated_at)
                        VALUES
                            (:id, :tenant_id, :node_type, :label, :content, :source_id,
                             :confidence, CAST(:metadata AS jsonb), :now, :now)
                        ON CONFLICT (id) DO UPDATE SET
                            label        = EXCLUDED.label,
                            content      = EXCLUDED.content,
                            confidence   = EXCLUDED.confidence,
                            extra_metadata = EXCLUDED.extra_metadata,
                            updated_at   = EXCLUDED.updated_at
                        """
                    ),
                    {
                        "id": node.node_id,
                        "tenant_id": node.tenant_id,
                        "node_type": node.node_type.value,
                        "label": node.label,
                        "content": node.content,
                        "source_id": node.source_id,
                        "confidence": node.confidence,
                        "metadata": json.dumps(node.metadata),
                        "now": now,
                    },
                )
        except Exception as exc:
            _log.debug("Failed to persist KG node to DB: %s", exc)

    async def _persist_edge_to_db(self, edge: GraphEdge) -> None:
        """Persist an edge to DB (best-effort, insert-ignore)."""
        if not self._db:
            return
        try:
            from sqlalchemy import text as _t

            now = datetime.now(UTC)
            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, edge.tenant_id),
            ):
                await session.execute(
                    _t(
                        """
                        INSERT INTO knowledge_edges
                            (id, tenant_id, source_node_id, target_node_id,
                             edge_type, label, confidence, evidence, provenance, created_at)
                        VALUES
                            (:id, :tenant_id, :src, :tgt,
                             :edge_type, :label, :confidence, :evidence, :provenance, :now)
                        ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    {
                        "id": edge.edge_id,
                        "tenant_id": edge.tenant_id,
                        "src": edge.source_node_id,
                        "tgt": edge.target_node_id,
                        "edge_type": edge.edge_type.value,
                        "label": edge.label,
                        "confidence": edge.confidence,
                        "evidence": edge.evidence,
                        "provenance": edge.provenance,
                        "now": now,
                    },
                )
        except Exception as exc:
            _log.debug("Failed to persist KG edge to DB: %s", exc)

    async def load_from_db(self, tenant_id: str) -> int:
        """Load a tenant's graph from DB into the in-memory cache.

        Returns the number of nodes loaded.
        """
        if not self._db:
            return 0
        loaded = 0
        try:
            from sqlalchemy import text as _t

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                # Load nodes
                node_rows = (
                    await session.execute(
                        _t(
                            "SELECT id, tenant_id, node_type, label, content, "
                            "source_id, confidence, extra_metadata, created_at "
                            "FROM knowledge_nodes WHERE tenant_id = :tid"
                        ),
                        {"tid": tenant_id},
                    )
                ).fetchall()

                for r in node_rows:
                    try:
                        node = GraphNode(
                            node_id=r[0],
                            tenant_id=r[1],
                            node_type=NodeType(r[2]),
                            label=r[3],
                            content=r[4] or "",
                            source_id=r[5],
                            confidence=float(r[6] or 1.0),
                            metadata=r[7] or {},
                            created_at=r[8].isoformat() if r[8] else None,
                        )
                        self._nodes[node.node_id] = node
                        self._tenant_nodes.setdefault(tenant_id, set()).add(node.node_id)
                        loaded += 1
                    except Exception as exc:
                        _log.debug("Skipping malformed KG node row: %s", exc)

                # Load edges
                edge_rows = (
                    await session.execute(
                        _t(
                            "SELECT id, tenant_id, source_node_id, target_node_id, "
                            "edge_type, label, confidence, evidence, provenance, created_at "
                            "FROM knowledge_edges WHERE tenant_id = :tid"
                        ),
                        {"tid": tenant_id},
                    )
                ).fetchall()

                for r in edge_rows:
                    try:
                        edge = GraphEdge(
                            edge_id=r[0],
                            tenant_id=r[1],
                            source_node_id=r[2],
                            target_node_id=r[3],
                            edge_type=EdgeType(r[4]),
                            label=r[5] or "",
                            confidence=float(r[6] or 1.0),
                            evidence=r[7] or "",
                            provenance=r[8] or "",
                            created_at=r[9].isoformat() if r[9] else None,
                        )
                        self._edges[edge.edge_id] = edge
                        self._tenant_edges.setdefault(tenant_id, set()).add(edge.edge_id)
                    except Exception as exc:
                        _log.debug("Skipping malformed KG edge row: %s", exc)

        except Exception as exc:
            _log.debug("Failed to load KG from DB: %s", exc)
        return loaded


# Module-level singleton
kg_store = KnowledgeGraphStore()
