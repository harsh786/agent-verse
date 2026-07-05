"""In-memory knowledge graph store with optional DB persistence."""
from __future__ import annotations
import logging
from typing import Any
from app.knowledge_graph.models import GraphNode, GraphEdge, NodeType

_log = logging.getLogger(__name__)


class KnowledgeGraphStore:
    """Tenant-scoped in-memory knowledge graph with DB persistence."""

    def __init__(self) -> None:
        self._nodes: dict[str, GraphNode] = {}  # node_id → node
        self._edges: dict[str, GraphEdge] = {}  # edge_id → edge
        self._tenant_nodes: dict[str, set[str]] = {}  # tenant_id → {node_ids}
        self._tenant_edges: dict[str, set[str]] = {}  # tenant_id → {edge_ids}

    def add_node(self, node: GraphNode) -> None:
        """Add or update a node (upsert by node_id)."""
        self._nodes[node.node_id] = node
        self._tenant_nodes.setdefault(node.tenant_id, set()).add(node.node_id)

    def add_edge(self, edge: GraphEdge) -> None:
        """Add an edge."""
        self._edges[edge.edge_id] = edge
        self._tenant_edges.setdefault(edge.tenant_id, set()).add(edge.edge_id)

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
            self._edges[eid] for eid in edge_ids
            if eid in self._edges and (
                self._edges[eid].source_node_id == node_id or
                self._edges[eid].target_node_id == node_id
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
        paths = []

        while queue and len(paths) < 5:
            path = queue.popleft()
            if len(path) > max_hops + 1:
                break
            current = path[-1]
            for neighbor in adj.get(current, []):
                if neighbor == target_id:
                    paths.append(path + [neighbor])
                elif neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(path + [neighbor])

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


# Module-level singleton
kg_store = KnowledgeGraphStore()
