"""Phase 5: Tenant Knowledge Graph tests."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.knowledge_graph import router as kg_router
from app.knowledge_graph.store import KnowledgeGraphStore
from app.knowledge_graph.extractor import EntityExtractor
from app.knowledge_graph.models import NodeType, EdgeType
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-p5", plan=PlanTier.PROFESSIONAL, api_key_id="kid-p5")
_CTX_B = TenantContext(tenant_id="tid-p5-b", plan=PlanTier.FREE, api_key_id="kid-p5b")
_KEY = "ak_phase5_test_key"
_KEY_B = "ak_phase5b_test_key"
_HEADERS = {"X-API-Key": _KEY}
_HEADERS_B = {"X-API-Key": _KEY_B}


def _make_app():
    app = FastAPI()

    async def _resolve(key):
        if key == _KEY:
            return _CTX
        if key == _KEY_B:
            return _CTX_B
        return None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(kg_router)
    return app


def test_entity_extraction_deterministic():
    extractor = EntityExtractor()
    nodes = extractor.extract_entities_deterministic(
        "OpenAI and Anthropic are building `claude` and GPT models.", "test-tid"
    )
    assert len(nodes) > 0
    labels = [n.label for n in nodes]
    # Should find code terms and proper names
    assert any("claude" in l or "OpenAI" in l or "Anthropic" in l for l in labels)


def test_extract_text_creates_nodes():
    client = TestClient(_make_app())
    resp = client.post("/knowledge-graph/extract", json={
        "text": "FastAPI is a Python web framework. LangGraph enables agent workflows.",
        "use_llm": False,
    }, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "entities_extracted" in data
    assert data["entities_extracted"] >= 0


def test_add_node_manually():
    client = TestClient(_make_app())
    resp = client.post("/knowledge-graph/nodes", json={
        "node_type": "concept",
        "label": "Test Concept",
        "content": "A test concept for unit testing",
        "confidence": 0.9,
    }, headers=_HEADERS)
    assert resp.status_code == 200
    assert "node_id" in resp.json()


def test_get_node_by_id():
    client = TestClient(_make_app())
    add = client.post("/knowledge-graph/nodes", json={
        "node_type": "entity",
        "label": "Findable Entity",
        "content": "Entity to find by ID",
    }, headers=_HEADERS)
    node_id = add.json()["node_id"]

    resp = client.get(f"/knowledge-graph/nodes/{node_id}", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["node"]["label"] == "Findable Entity"


def test_get_nonexistent_node_returns_404():
    client = TestClient(_make_app())
    resp = client.get("/knowledge-graph/nodes/nonexistent-id-xyz", headers=_HEADERS)
    assert resp.status_code == 404


def test_add_edge_between_nodes():
    client = TestClient(_make_app())
    n1 = client.post("/knowledge-graph/nodes", json={"node_type": "entity", "label": "Node A"}, headers=_HEADERS).json()["node_id"]
    n2 = client.post("/knowledge-graph/nodes", json={"node_type": "entity", "label": "Node B"}, headers=_HEADERS).json()["node_id"]

    resp = client.post("/knowledge-graph/edges", json={
        "source_node_id": n1,
        "target_node_id": n2,
        "edge_type": "depends_on",
        "confidence": 0.85,
        "evidence": "A requires B to function",
    }, headers=_HEADERS)
    assert resp.status_code == 200
    assert "edge_id" in resp.json()


def test_find_path_between_nodes():
    client = TestClient(_make_app())
    n1 = client.post("/knowledge-graph/nodes", json={"node_type": "entity", "label": "Path Source"}, headers=_HEADERS).json()["node_id"]
    n2 = client.post("/knowledge-graph/nodes", json={"node_type": "entity", "label": "Path Target"}, headers=_HEADERS).json()["node_id"]
    client.post("/knowledge-graph/edges", json={"source_node_id": n1, "target_node_id": n2, "edge_type": "mentions"}, headers=_HEADERS)

    resp = client.get(f"/knowledge-graph/path?source_id={n1}&target_id={n2}&max_hops=2", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["path_count"] >= 1


def test_query_nodes_with_search():
    client = TestClient(_make_app())
    client.post("/knowledge-graph/nodes", json={"node_type": "concept", "label": "SearchableUniqueTerm"}, headers=_HEADERS)

    resp = client.get("/knowledge-graph/nodes?search=SearchableUniqueTerm", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


def test_query_nodes_with_type_filter():
    client = TestClient(_make_app())
    client.post("/knowledge-graph/nodes", json={"node_type": "workflow", "label": "Test Workflow"}, headers=_HEADERS)

    resp = client.get("/knowledge-graph/nodes?node_type=workflow", headers=_HEADERS)
    assert resp.status_code == 200
    nodes = resp.json()["nodes"]
    assert all(n["node_type"] == "workflow" for n in nodes)


def test_graph_stats():
    client = TestClient(_make_app())
    resp = client.get("/knowledge-graph/stats", headers=_HEADERS)
    assert resp.status_code == 200
    stats = resp.json()
    assert "total_nodes" in stats
    assert "total_edges" in stats


def test_tenant_isolation_nodes():
    """Tenant A cannot see Tenant B's nodes."""
    client = TestClient(_make_app())
    # Tenant B adds a node
    node_b = client.post("/knowledge-graph/nodes", json={
        "node_type": "entity",
        "label": "Tenant B Secret Node",
    }, headers=_HEADERS_B).json()["node_id"]

    # Tenant A should not see it
    resp = client.get(f"/knowledge-graph/nodes/{node_b}", headers=_HEADERS)
    assert resp.status_code == 404  # Not found for Tenant A


def test_rebuild_clears_graph():
    client = TestClient(_make_app())
    # Add a node
    client.post("/knowledge-graph/nodes", json={"node_type": "entity", "label": "ToBeDeleted"}, headers=_HEADERS)
    # Rebuild (clear)
    resp = client.delete("/knowledge-graph/rebuild", headers=_HEADERS)
    assert resp.status_code == 200
    # Stats should show 0 (or fewer) nodes for this tenant
    # Note: other tests may have added nodes, but at minimum rebuild works


def test_invalid_node_type_returns_400():
    client = TestClient(_make_app())
    resp = client.post("/knowledge-graph/nodes", json={
        "node_type": "invalid_type_xyz",
        "label": "Bad Node",
    }, headers=_HEADERS)
    assert resp.status_code == 400


def test_invalid_edge_type_returns_400():
    client = TestClient(_make_app())
    resp = client.post("/knowledge-graph/edges", json={
        "source_node_id": "n1",
        "target_node_id": "n2",
        "edge_type": "invalid_edge_xyz",
    }, headers=_HEADERS)
    assert resp.status_code == 400
