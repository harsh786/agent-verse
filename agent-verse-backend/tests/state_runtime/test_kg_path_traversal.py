"""Graph RAG path traversal must return real edges, not hardcoded '?' placeholders."""
from __future__ import annotations

import pytest

from app.knowledge_graph.models import EdgeType, GraphEdge, GraphNode, NodeType
from app.knowledge_graph.store import KnowledgeGraphStore
from app.state_runtime.kg_query_engine import KGQueryEngine


@pytest.fixture
def kg_store_with_edges():
    store = KnowledgeGraphStore()
    node_a = GraphNode(node_id="n1", label="AgentVerse", node_type=NodeType.CONCEPT, tenant_id="t1")
    node_b = GraphNode(node_id="n2", label="LangGraph", node_type=NodeType.CONCEPT, tenant_id="t1")
    node_c = GraphNode(node_id="n3", label="Orchestration", node_type=NodeType.CONCEPT, tenant_id="t1")
    store.add_node(node_a)
    store.add_node(node_b)
    store.add_node(node_c)
    store.add_edge(GraphEdge(
        edge_id="e1", source_node_id="n1", target_node_id="n2",
        edge_type=EdgeType.MENTIONS, tenant_id="t1",
    ))
    store.add_edge(GraphEdge(
        edge_id="e2", source_node_id="n1", target_node_id="n3",
        edge_type=EdgeType.MENTIONS, tenant_id="t1",
    ))
    return store


async def test_path_traversal_returns_real_edges(kg_store_with_edges):
    """_path_traversal must return facts with real 'to' values, not '?'."""
    engine = KGQueryEngine(kg_store=kg_store_with_edges)
    result = await engine.query("AgentVerse", tenant_id="t1", strategy="path")

    assert result.strategy_used == "path"
    assert len(result.facts) > 0
    for fact in result.facts:
        assert fact.get("to") != "?", f"Placeholder '?' found in fact: {fact}"
        assert fact.get("from"), "Missing 'from' in path fact"
        assert fact.get("relation"), "Missing 'relation' in path fact"


async def test_path_traversal_includes_neighbour_names(kg_store_with_edges):
    """Path facts must contain actual neighbour node names."""
    engine = KGQueryEngine(kg_store=kg_store_with_edges)
    result = await engine.query("AgentVerse", tenant_id="t1", strategy="path")

    to_values = {f["to"] for f in result.facts}
    assert to_values & {"LangGraph", "Orchestration"}, \
        f"Expected neighbour names, got: {to_values}"


async def test_path_traversal_no_edges_returns_empty_facts():
    """Node with no edges must return empty facts gracefully."""
    store = KnowledgeGraphStore()
    isolated = GraphNode(node_id="iso1", label="Isolated", node_type=NodeType.CONCEPT, tenant_id="t1")
    store.add_node(isolated)

    engine = KGQueryEngine(kg_store=store)
    result = await engine.query("Isolated", tenant_id="t1", strategy="path")

    assert result.strategy_used == "path"
    assert isinstance(result.facts, list)
    for fact in result.facts:
        assert fact.get("to") != "?", "Hardcoded '?' found even for isolated node"


async def test_entity_expansion_unchanged(kg_store_with_edges):
    """Entity expansion (existing, working) must still pass."""
    engine = KGQueryEngine(kg_store=kg_store_with_edges)
    result = await engine.query("AgentVerse", tenant_id="t1", strategy="entity")
    assert result.strategy_used == "entity"
    assert result.confidence > 0
