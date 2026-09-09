"""D-16: KnowledgeGraphStore.get_neighbors adjacency behaviour.

Seeds a small graph in-memory and proves that:
  * ``KnowledgeGraphStore.get_neighbors`` returns the correct adjacency,
  * it is tenant-scoped.
"""

from __future__ import annotations

import uuid

from app.knowledge_graph.models import EdgeType, GraphEdge, GraphNode, NodeType
from app.knowledge_graph.store import KnowledgeGraphStore


def _node(node_id: str, tenant_id: str = "t-1") -> GraphNode:
    return GraphNode(
        node_id=node_id,
        tenant_id=tenant_id,
        node_type=NodeType.ENTITY,
        label=node_id,
    )


def _edge(src: str, tgt: str, tenant_id: str = "t-1") -> GraphEdge:
    return GraphEdge(
        edge_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        source_node_id=src,
        target_node_id=tgt,
        edge_type=EdgeType.MENTIONS,
        label="mentions",
    )


def _seeded_store() -> KnowledgeGraphStore:
    """A -> B -> C -> D chain plus a B -> D shortcut, all for tenant t-1."""
    store = KnowledgeGraphStore()
    for n in ("A", "B", "C", "D"):
        store.add_node(_node(n))
    store.add_edge(_edge("A", "B"))
    store.add_edge(_edge("B", "C"))
    store.add_edge(_edge("C", "D"))
    store.add_edge(_edge("B", "D"))
    return store


# ---------------------------------------------------------------------------
# get_neighbors
# ---------------------------------------------------------------------------


def test_get_neighbors_returns_adjacent_nodes() -> None:
    store = _seeded_store()
    neighbors = store.get_neighbors("B", "t-1")
    targets = {n["target"] for n in neighbors}
    # B is adjacent to A (incoming), C and D (outgoing) — adjacency is undirected.
    assert targets == {"A", "C", "D"}
    assert all("relation" in n for n in neighbors)


def test_get_neighbors_is_tenant_scoped() -> None:
    store = _seeded_store()
    # A different tenant sees no edges for the same node id.
    assert store.get_neighbors("B", "other-tenant") == []


def test_get_neighbors_unknown_node() -> None:
    store = _seeded_store()
    assert store.get_neighbors("ZZZ", "t-1") == []
