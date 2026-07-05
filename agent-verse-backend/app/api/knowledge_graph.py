"""Tenant Knowledge Graph API."""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/knowledge-graph", tags=["knowledge-graph"])


def _require_tenant(request: Request):
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


class ExtractRequest(BaseModel):
    text: str = Field(..., max_length=50_000)
    source_id: str | None = None
    use_llm: bool = True  # False = deterministic only


class AddNodeRequest(BaseModel):
    node_type: str
    label: str
    content: str = ""
    confidence: float = 1.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class AddEdgeRequest(BaseModel):
    source_node_id: str
    target_node_id: str
    edge_type: str
    label: str = ""
    confidence: float = 1.0
    evidence: str = ""


@router.post("/extract")
async def extract_from_text(request: Request, body: ExtractRequest) -> dict[str, Any]:
    """Extract entities and relationships from text into the tenant knowledge graph."""
    tenant = _require_tenant(request)
    from app.knowledge_graph.extractor import entity_extractor
    from app.knowledge_graph.store import kg_store

    provider = getattr(request.app.state, "_app_provider", None)
    if provider:
        entity_extractor.set_provider(provider)

    # Extract entities
    if body.use_llm and provider:
        entities = await entity_extractor.extract_entities_llm(
            body.text, tenant.tenant_id, body.source_id
        )
    else:
        entities = entity_extractor.extract_entities_deterministic(
            body.text, tenant.tenant_id, body.source_id
        )

    # Extract relationships
    edges = await entity_extractor.extract_relationships_llm(
        body.text, entities, tenant.tenant_id, body.source_id
    )

    # Store everything
    for node in entities:
        kg_store.add_node(node)
    for edge in edges:
        kg_store.add_edge(edge)

    return {
        "entities_extracted": len(entities),
        "relationships_extracted": len(edges),
        "nodes": [
            {
                "node_id": n.node_id, "label": n.label,
                "type": n.node_type.value, "confidence": n.confidence,
            }
            for n in entities
        ],
        "edges": [
            {
                "edge_id": e.edge_id, "type": e.edge_type.value,
                "source": e.source_node_id, "target": e.target_node_id,
            }
            for e in edges
        ],
    }


@router.get("/nodes")
async def query_nodes(
    request: Request,
    node_type: str | None = Query(default=None),
    search: str | None = Query(default=None),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    limit: int = Query(default=50, le=200),
) -> dict[str, Any]:
    """Query nodes in the tenant knowledge graph."""
    tenant = _require_tenant(request)
    from app.knowledge_graph.store import kg_store
    from app.knowledge_graph.models import NodeType

    nt = None
    if node_type:
        try:
            nt = NodeType(node_type)
        except ValueError:
            raise HTTPException(400, f"Invalid node_type: {node_type}")

    nodes = kg_store.query_nodes(
        tenant.tenant_id, node_type=nt, search=search,
        min_confidence=min_confidence, limit=limit,
    )

    return {
        "nodes": [
            {
                "node_id": n.node_id,
                "node_type": n.node_type.value,
                "label": n.label,
                "content": n.content[:200],
                "confidence": n.confidence,
                "source_id": n.source_id,
                "metadata": n.metadata,
                "created_at": n.created_at,
            }
            for n in nodes
        ],
        "total": len(nodes),
    }


@router.get("/nodes/{node_id}")
async def get_node(request: Request, node_id: str) -> dict[str, Any]:
    """Get a specific node and its edges."""
    tenant = _require_tenant(request)
    from app.knowledge_graph.store import kg_store

    node = kg_store.get_node(node_id, tenant.tenant_id)
    if not node:
        raise HTTPException(404, "Node not found")

    edges = kg_store.get_edges_for_node(node_id, tenant.tenant_id)

    return {
        "node": {
            "node_id": node.node_id,
            "node_type": node.node_type.value,
            "label": node.label,
            "content": node.content,
            "confidence": node.confidence,
            "source_id": node.source_id,
            "metadata": node.metadata,
        },
        "edges": [
            {
                "edge_id": e.edge_id,
                "edge_type": e.edge_type.value,
                "source_node_id": e.source_node_id,
                "target_node_id": e.target_node_id,
                "label": e.label,
                "confidence": e.confidence,
                "evidence": e.evidence,
            }
            for e in edges
        ],
    }


@router.post("/nodes")
async def add_node(request: Request, body: AddNodeRequest) -> dict[str, Any]:
    """Manually add a node to the knowledge graph."""
    tenant = _require_tenant(request)
    from app.knowledge_graph.store import kg_store
    from app.knowledge_graph.models import GraphNode, NodeType
    import uuid
    import datetime

    try:
        nt = NodeType(body.node_type)
    except ValueError:
        raise HTTPException(400, f"Invalid node_type: {body.node_type}")

    node = GraphNode(
        node_id=str(uuid.uuid4()),
        tenant_id=tenant.tenant_id,
        node_type=nt,
        label=body.label,
        content=body.content,
        confidence=body.confidence,
        metadata=body.metadata,
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )
    kg_store.add_node(node)
    return {"node_id": node.node_id, "status": "added"}


@router.post("/edges")
async def add_edge(request: Request, body: AddEdgeRequest) -> dict[str, Any]:
    """Manually add an edge to the knowledge graph."""
    tenant = _require_tenant(request)
    from app.knowledge_graph.store import kg_store
    from app.knowledge_graph.models import GraphEdge, EdgeType
    import uuid
    import datetime

    try:
        et = EdgeType(body.edge_type)
    except ValueError:
        raise HTTPException(400, f"Invalid edge_type: {body.edge_type}")

    edge = GraphEdge(
        edge_id=str(uuid.uuid4()),
        tenant_id=tenant.tenant_id,
        source_node_id=body.source_node_id,
        target_node_id=body.target_node_id,
        edge_type=et,
        label=body.label,
        confidence=body.confidence,
        evidence=body.evidence,
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )
    kg_store.add_edge(edge)
    return {"edge_id": edge.edge_id, "status": "added"}


@router.get("/path")
async def find_path(
    request: Request,
    source_id: str = Query(...),
    target_id: str = Query(...),
    max_hops: int = Query(default=3, ge=1, le=6),
) -> dict[str, Any]:
    """Find paths between two nodes in the knowledge graph."""
    tenant = _require_tenant(request)
    from app.knowledge_graph.store import kg_store

    paths = kg_store.find_path(source_id, target_id, tenant.tenant_id, max_hops)
    return {
        "source_id": source_id,
        "target_id": target_id,
        "paths": paths,
        "path_count": len(paths),
    }


@router.get("/stats")
async def get_graph_stats(request: Request) -> dict[str, Any]:
    """Get knowledge graph statistics for the tenant."""
    tenant = _require_tenant(request)
    from app.knowledge_graph.store import kg_store
    return kg_store.get_graph_stats(tenant.tenant_id)


@router.delete("/rebuild")
async def rebuild_graph(request: Request) -> dict[str, Any]:
    """Delete and rebuild the tenant's knowledge graph (idempotent)."""
    tenant = _require_tenant(request)
    from app.knowledge_graph.store import kg_store
    kg_store.delete_tenant_graph(tenant.tenant_id)
    return {"status": "cleared", "tenant_id": tenant.tenant_id}
