"""Tests for the untested endpoints /knowledge-graph/communities and
/knowledge-graph/export in app/api/knowledge_graph.py (lines 283-304).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.knowledge_graph import router as kg_router
from app.knowledge_graph.store import KnowledgeGraphStore, kg_store
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(
    tenant_id="tid-kg-gap", plan=PlanTier.ENTERPRISE, api_key_id="kid-kg"
)
_KEY = "ak_kg_gap_test_key"
_HEADERS = {"X-API-Key": _KEY}


def _make_app() -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(kg_router)
    return app


# ── GET /knowledge-graph/communities ─────────────────────────────────────────


def test_get_communities_requires_auth() -> None:
    """GET /knowledge-graph/communities without auth returns 401."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/knowledge-graph/communities")
    assert resp.status_code == 401


def test_get_communities_returns_empty_for_unpopulated_tenant() -> None:
    """GET /knowledge-graph/communities returns empty list for a fresh tenant."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/knowledge-graph/communities", headers=_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert "communities" in body
    assert "total" in body
    assert body["total"] == len(body["communities"])


def test_get_communities_returns_connected_components() -> None:
    """GET /knowledge-graph/communities returns communities after nodes are added."""
    import uuid

    from app.knowledge_graph.models import EdgeType, GraphEdge, GraphNode, NodeType

    # Pre-populate the kg_store with a few nodes for this tenant
    n1_id = uuid.uuid4().hex
    n2_id = uuid.uuid4().hex
    n1 = GraphNode(
        node_id=n1_id, tenant_id=_CTX.tenant_id, node_type=NodeType.CONCEPT, label="Node 1"
    )
    n2 = GraphNode(
        node_id=n2_id, tenant_id=_CTX.tenant_id, node_type=NodeType.CONCEPT, label="Node 2"
    )
    kg_store.add_node(n1)
    kg_store.add_node(n2)
    # Connect the two nodes so they form one community
    edge = GraphEdge(
        edge_id=uuid.uuid4().hex,
        tenant_id=_CTX.tenant_id,
        source_node_id=n1_id,
        target_node_id=n2_id,
        edge_type=EdgeType.REFERENCES,
    )
    kg_store.add_edge(edge)

    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/knowledge-graph/communities", headers=_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1

    # Cleanup to avoid leaking state into other tests
    kg_store._tenant_nodes.get(_CTX.tenant_id, set()).discard(n1_id)
    kg_store._tenant_nodes.get(_CTX.tenant_id, set()).discard(n2_id)
    if n1_id in kg_store._nodes:
        del kg_store._nodes[n1_id]
    if n2_id in kg_store._nodes:
        del kg_store._nodes[n2_id]
    if edge.edge_id in kg_store._edges:
        del kg_store._edges[edge.edge_id]
    kg_store._tenant_edges.get(_CTX.tenant_id, set()).discard(edge.edge_id)


# ── GET /knowledge-graph/export ──────────────────────────────────────────────


def test_export_graph_requires_auth() -> None:
    """GET /knowledge-graph/export without auth returns 401."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/knowledge-graph/export")
    assert resp.status_code == 401


def test_export_graph_returns_empty_for_unpopulated_tenant() -> None:
    """GET /knowledge-graph/export returns an empty graph structure for a fresh tenant."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/knowledge-graph/export", headers=_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == _CTX.tenant_id
    assert "exported_at" in body
    assert "nodes" in body
    assert "edges" in body
    assert isinstance(body["nodes"], list)
    assert isinstance(body["edges"], list)


def test_export_graph_returns_nodes_and_edges_after_population() -> None:
    """GET /knowledge-graph/export returns serialized nodes and edges."""
    import uuid

    from app.knowledge_graph.models import EdgeType, GraphEdge, GraphNode, NodeType

    # Add nodes for this tenant
    n1_id = uuid.uuid4().hex
    n2_id = uuid.uuid4().hex
    n1 = GraphNode(
        node_id=n1_id,
        tenant_id=_CTX.tenant_id,
        node_type=NodeType.CONCEPT,
        label="Exportable Node 1",
        confidence=0.9,
    )
    n2 = GraphNode(
        node_id=n2_id,
        tenant_id=_CTX.tenant_id,
        node_type=NodeType.CONCEPT,
        label="Exportable Node 2",
        confidence=0.8,
    )
    kg_store.add_node(n1)
    kg_store.add_node(n2)
    edge = GraphEdge(
        edge_id=uuid.uuid4().hex,
        tenant_id=_CTX.tenant_id,
        source_node_id=n1_id,
        target_node_id=n2_id,
        edge_type=EdgeType.REFERENCES,
        confidence=0.5,
    )
    kg_store.add_edge(edge)

    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/knowledge-graph/export", headers=_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == _CTX.tenant_id
    assert len(body["nodes"]) >= 2
    assert len(body["edges"]) >= 1
    # Each node should have node_id, node_type, label
    assert "node_id" in body["nodes"][0]
    assert "node_type" in body["nodes"][0]
    assert "label" in body["nodes"][0]

    # Cleanup
    kg_store._tenant_nodes.get(_CTX.tenant_id, set()).discard(n1_id)
    kg_store._tenant_nodes.get(_CTX.tenant_id, set()).discard(n2_id)
    if n1_id in kg_store._nodes:
        del kg_store._nodes[n1_id]
    if n2_id in kg_store._nodes:
        del kg_store._nodes[n2_id]
    if edge.edge_id in kg_store._edges:
        del kg_store._edges[edge.edge_id]
    kg_store._tenant_edges.get(_CTX.tenant_id, set()).discard(edge.edge_id)


def test_export_graph_excludes_other_tenants_nodes() -> None:
    """export_graph only returns nodes for the requesting tenant (tenant isolation)."""
    import uuid

    from app.knowledge_graph.models import GraphNode, NodeType

    # Add a node for a DIFFERENT tenant
    other_tenant = "other-tenant-xyz"
    other_n_id = uuid.uuid4().hex
    other_node = GraphNode(
        node_id=other_n_id,
        tenant_id=other_tenant,
        node_type=NodeType.CONCEPT,
        label="Other tenant node",
    )
    kg_store.add_node(other_node)

    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/knowledge-graph/export", headers=_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    # Our tenant should not see other-tenant's node
    labels = [n.get("label") for n in body["nodes"]]
    assert "Other tenant node" not in labels

    # Cleanup the other-tenant node to avoid leaking state
    kg_store._tenant_nodes.get(other_tenant, set()).discard(other_n_id)
    if other_n_id in kg_store._nodes:
        del kg_store._nodes[other_n_id]
