# Community 176

> 32 nodes · cohesion 0.09

## Key Concepts

- **KnowledgeGraphStore** (19 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **.load_from_db()** (8 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **._persist_edge_to_db()** (5 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **._persist_node_to_db()** (5 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **.query_nodes()** (5 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **GraphNode** (5 connections)
- **.add_node()** (4 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **.detect_communities()** (4 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **GraphEdge** (4 connections)
- **.add_edge()** (3 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **.get_edges_for_node()** (3 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **.get_graph_stats()** (3 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **.set_db()** (3 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **Any** (3 connections)
- **.delete_tenant_graph()** (2 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **.find_path()** (2 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **.get_node()** (2 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/state_runtime/kg_query_engine.py`
- **NodeType** (2 connections)
- **.__init__()** (1 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **Get all edges connected to a node.** (1 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **BFS to find paths between two nodes (max_hops limit).** (1 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **Return graph statistics for a tenant.** (1 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **Delete all graph data for a tenant.** (1 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **Tenant-scoped in-memory knowledge graph with DB persistence.** (1 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- *... and 7 more nodes in this community*

## Relationships

- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (3 shared connections)
- [Community 326](Community_326.md) (2 shared connections)
- [Community 511](Community_511.md) (2 shared connections)
- [Community 554](Community_554.md) (2 shared connections)

## Source Files

- `agent-verse-backend/app/knowledge_graph/store.py`
- `agent-verse-backend/app/state_runtime/kg_query_engine.py`

## Audit Trail

- EXTRACTED: 52 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*