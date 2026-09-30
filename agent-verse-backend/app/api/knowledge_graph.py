"""Tenant Knowledge Graph API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.tenancy.rbac import require_role

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
    from app.knowledge_graph.extractor import EntityExtractor
    from app.knowledge_graph.store import kg_store

    # A per-request extractor: set_provider on the module-global one raced
    # between concurrent requests. Without use_llm it has no provider at all,
    # so nothing below can spend an LLM call (relationships used to be
    # extracted with the LLM even for use_llm=false).
    extractor = EntityExtractor()
    provider = getattr(request.app.state, "_app_provider", None)
    use_llm = bool(body.use_llm and provider)
    if use_llm:
        extractor.set_provider(provider)

    if use_llm:
        entities = await extractor.extract_entities_llm(
            body.text, tenant.tenant_id, body.source_id
        )
        edges = await extractor.extract_relationships_llm(
            body.text, entities, tenant.tenant_id, body.source_id
        )
    else:
        # Deterministic extraction finds entities only; it emits no edges.
        entities = extractor.extract_entities_deterministic(
            body.text, tenant.tenant_id, body.source_id
        )
        edges = []

    # One awaited, batched upsert: the response reports these as extracted, so
    # they must be durable when it returns (the old per-element fire-and-forget
    # writes could still be in flight — or have silently failed).
    await kg_store.aupsert(entities, edges)

    return {
        "entities_extracted": len(entities),
        "relationships_extracted": len(edges),
        "nodes": [
            {
                "node_id": n.node_id,
                "label": n.label,
                "type": n.node_type.value,
                "confidence": n.confidence,
            }
            for n in entities
        ],
        "edges": [
            {
                "edge_id": e.edge_id,
                "type": e.edge_type.value,
                "source": e.source_node_id,
                "target": e.target_node_id,
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
    from app.knowledge_graph.models import NodeType
    from app.knowledge_graph.store import kg_store

    nt = None
    if node_type:
        try:
            nt = NodeType(node_type)
        except ValueError as _b904_exc:
            raise HTTPException(400, f"Invalid node_type: {node_type}") from _b904_exc

    nodes = await kg_store.aquery_nodes(
        tenant.tenant_id,
        node_type=nt,
        search=search,
        min_confidence=min_confidence,
        limit=limit,
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

    node = await kg_store.aget_node(node_id, tenant.tenant_id)
    if not node:
        raise HTTPException(404, "Node not found")

    edges = await kg_store.aget_edges_for_node(node_id, tenant.tenant_id)

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
    import datetime
    import uuid

    from app.knowledge_graph.models import GraphNode, NodeType
    from app.knowledge_graph.store import kg_store

    try:
        nt = NodeType(body.node_type)
    except ValueError as _b904_exc:
        raise HTTPException(400, f"Invalid node_type: {body.node_type}") from _b904_exc

    node = GraphNode(
        node_id=str(uuid.uuid4()),
        tenant_id=tenant.tenant_id,
        node_type=nt,
        label=body.label,
        content=body.content,
        confidence=body.confidence,
        metadata=body.metadata,
        created_at=datetime.datetime.now(datetime.UTC).isoformat(),
    )
    await kg_store.aupsert([node], [])
    return {"node_id": node.node_id, "status": "added"}


@router.post("/edges")
async def add_edge(request: Request, body: AddEdgeRequest) -> dict[str, Any]:
    """Manually add an edge to the knowledge graph."""
    tenant = _require_tenant(request)
    import datetime
    import uuid

    from app.knowledge_graph.models import EdgeType, GraphEdge
    from app.knowledge_graph.store import kg_store

    try:
        et = EdgeType(body.edge_type)
    except ValueError as _b904_exc:
        raise HTTPException(400, f"Invalid edge_type: {body.edge_type}") from _b904_exc

    edge = GraphEdge(
        edge_id=str(uuid.uuid4()),
        tenant_id=tenant.tenant_id,
        source_node_id=body.source_node_id,
        target_node_id=body.target_node_id,
        edge_type=et,
        label=body.label,
        confidence=body.confidence,
        evidence=body.evidence,
        created_at=datetime.datetime.now(datetime.UTC).isoformat(),
    )
    await kg_store.aupsert([], [edge])
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

    paths = await kg_store.afind_path(source_id, target_id, tenant.tenant_id, max_hops)
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

    return await kg_store.aget_graph_stats(tenant.tenant_id)


@router.delete("/rebuild")
async def rebuild_graph(
    request: Request,
    # Admin-only: this irreversibly wipes every node and edge of the tenant, in
    # memory and in the DB. It used to check only _require_tenant, so any key of
    # the tenant — even a read-only viewer key — could delete the whole graph.
    _rbac: None = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Delete and rebuild the tenant's knowledge graph (idempotent)."""
    tenant = _require_tenant(request)
    from app.knowledge_graph.store import kg_store

    await kg_store.delete_tenant_graph(tenant.tenant_id)
    return {"status": "cleared", "tenant_id": tenant.tenant_id}


@router.get("/communities")
async def get_communities(request: Request) -> dict[str, Any]:
    """Detect and return knowledge graph communities (connected components)."""
    tenant = _require_tenant(request)
    from app.knowledge_graph.store import kg_store

    communities = await kg_store.adetect_communities(tenant.tenant_id)
    return {"communities": communities, "total": len(communities)}


@router.get("/export")
async def export_graph(
    request: Request,
    limit: int = Query(default=1000, ge=1, le=5000),
    node_cursor: str | None = Query(default=None),
    edge_cursor: str | None = Query(default=None),
) -> dict[str, Any]:
    """Export the tenant's knowledge graph as JSON, one keyset page at a time.

    Used to dump every node and edge in a single response built from the
    replica's in-memory copy of the graph. Pass ``next_node_cursor`` /
    ``next_edge_cursor`` back to fetch the next page; both are ``null`` on the
    last one.
    """
    import datetime

    tenant = _require_tenant(request)
    from app.knowledge_graph.store import kg_store

    page = await kg_store.aexport(
        tenant.tenant_id, limit=limit, node_cursor=node_cursor, edge_cursor=edge_cursor
    )
    nodes, edges = page["nodes"], page["edges"]
    return {
        "tenant_id": tenant.tenant_id,
        "exported_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "nodes": [
            {
                "node_id": n.node_id,
                "node_type": n.node_type.value,
                "label": n.label,
                "confidence": n.confidence,
                "source_id": n.source_id,
            }
            for n in nodes
        ],
        "edges": [
            {
                "edge_id": e.edge_id,
                "edge_type": e.edge_type.value,
                "source": e.source_node_id,
                "target": e.target_node_id,
                "confidence": e.confidence,
            }
            for e in edges
        ],
        "stats": {"nodes": len(nodes), "edges": len(edges)},
        "next_node_cursor": page["next_node_cursor"],
        "next_edge_cursor": page["next_edge_cursor"],
        "format": "agentverse_kg_v1",
    }
