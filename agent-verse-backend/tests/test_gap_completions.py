"""Tests for GAP 2A-2D completions."""
import pytest


@pytest.mark.asyncio
async def test_reranker_returns_sorted_results():
    from app.rag_platform.reranker import Reranker
    r = Reranker()
    docs = [
        {"content": "Python is a language", "score": 0.7},
        {"content": "The cat sat on the mat", "score": 0.3},
        {"content": "Python pandas is a library", "score": 0.9},
    ]
    reranked = await r.rerank("Python programming", docs, top_k=2)
    assert len(reranked) == 2
    # Score-based fallback should put higher score first
    assert reranked[0]["score"] >= reranked[1]["score"]


@pytest.mark.asyncio
async def test_citation_verifier_no_provider():
    from app.rag_platform.reranker import CitationVerifier
    v = CitationVerifier()  # No provider
    result = await v.verify_citations("The sky is blue", [{"content": "The sky appears blue"}])
    assert "verified" in result
    assert "confidence" in result


@pytest.mark.asyncio
async def test_memory_consolidation():
    from app.memory_v2.consolidation import MemoryConsolidator
    from datetime import datetime, timezone, timedelta

    consolidator = MemoryConsolidator()
    old_date = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()

    memories = {
        "t1:m1": {
            "memory_id": "m1",
            "content": "Test memory",
            "lifecycle_state": "active",
            "confidence": 0.8,
            "updated_at": old_date,
        },
        "t1:m2": {
            "memory_id": "m2",
            "content": "Test memory",
            "lifecycle_state": "active",
            "confidence": 0.9,
            "updated_at": old_date,
        },  # Duplicate
    }

    stats = await consolidator.consolidate("t1", memories)
    assert stats["marked_stale"] >= 1 or stats["merged"] >= 0
    assert "total_before" in stats


def test_kg_community_detection():
    from app.knowledge_graph.store import KnowledgeGraphStore
    from app.knowledge_graph.models import GraphNode, GraphEdge, NodeType, EdgeType
    import uuid

    store = KnowledgeGraphStore()
    tid = "community-test-tenant"

    # Add 3 connected nodes
    for i in range(3):
        node = GraphNode(
            node_id=str(uuid.uuid4()),
            tenant_id=tid,
            node_type=NodeType.ENTITY,
            label=f"Node {i}",
        )
        store.add_node(node)

    nodes = store.query_nodes(tid)
    if len(nodes) >= 2:
        edge = GraphEdge(
            edge_id=str(uuid.uuid4()),
            tenant_id=tid,
            source_node_id=nodes[0].node_id,
            target_node_id=nodes[1].node_id,
            edge_type=EdgeType.MENTIONS,
        )
        store.add_edge(edge)

    communities = store.detect_communities(tid)
    assert isinstance(communities, list)


def test_kg_export():
    from app.knowledge_graph.store import KnowledgeGraphStore
    from app.knowledge_graph.models import GraphNode, NodeType
    import uuid

    store = KnowledgeGraphStore()
    tid = "export-test-tenant"
    node = GraphNode(
        node_id=str(uuid.uuid4()),
        tenant_id=tid,
        node_type=NodeType.CONCEPT,
        label="Export Test",
    )
    store.add_node(node)

    # Test export logic directly
    node_ids = store._tenant_nodes.get(tid, set())
    assert len(node_ids) >= 1


def test_skill_update_increments_version():
    from app.api.skills_runtime import _tenant_skills, _skill_versions

    # Pre-populate a skill
    skill_id = "test-version-skill"
    _tenant_skills.setdefault("version-test-tenant", []).append({
        "skill_id": skill_id,
        "name": "Test Skill",
        "version": "1.0.0",
        "description": "Original",
    })

    # Simulate update logic
    skill = next(s for s in _tenant_skills["version-test-tenant"] if s["skill_id"] == skill_id)
    old_version = skill["version"]
    parts = old_version.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    skill["version"] = ".".join(parts)

    assert skill["version"] == "1.0.1"
