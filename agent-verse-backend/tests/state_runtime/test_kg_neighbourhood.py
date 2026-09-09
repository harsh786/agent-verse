"""KGQueryEngine _neighbourhood must use n.label, not n.name."""
from __future__ import annotations

from app.knowledge_graph.store import GraphNode, KnowledgeGraphStore, NodeType
from app.state_runtime.kg_query_engine import KGQueryEngine


async def test_neighbourhood_query_returns_facts_not_empty():
    """community/impact strategies must return facts, not empty list."""
    store = KnowledgeGraphStore()
    node = GraphNode(node_id="n1", label="AgentVerse",  # use label, not name
                     node_type=NodeType.CONCEPT, tenant_id="t1")
    store.add_node(node)

    engine = KGQueryEngine(kg_store=store)
    result = await engine.query("AgentVerse", tenant_id="t1", strategy="community")

    assert result.strategy_used == "community"
    # Must not be empty — at least the node itself must be returned
    assert isinstance(result.facts, list)
    # Must not raise AttributeError (the bug being fixed)


async def test_impact_strategy_works():
    store = KnowledgeGraphStore()
    store.add_node(GraphNode(node_id="n1", label="System",
                             node_type=NodeType.CONCEPT, tenant_id="t1"))
    engine = KGQueryEngine(kg_store=store)
    result = await engine.query("System", tenant_id="t1", strategy="impact")
    assert result.strategy_used == "impact"
    assert isinstance(result.facts, list)
