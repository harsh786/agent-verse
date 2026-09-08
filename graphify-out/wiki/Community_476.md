# Community 476

> 12 nodes · cohesion 0.20

## Key Concepts

- **KnowledgeGraphStore (tenant in-memory KG + best-effort DB persist)** (8 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **EntityExtractor (deterministic + LLM entity/relation extraction)** (6 connections) — `agent-verse-backend/app/knowledge_graph/extractor.py`
- **CommunityDetector (Union-Find connected components + density)** (3 connections) — `agent-verse-backend/app/knowledge_graph/community_detection.py`
- **GraphEdge (directed edge with evidence + provenance)** (3 connections) — `agent-verse-backend/app/knowledge_graph/models.py`
- **GraphNode (tenant-scoped node with embedding + confidence)** (3 connections) — `agent-verse-backend/app/knowledge_graph/models.py`
- **validate_export_request (embeddings + max_nodes gated by role)** (1 connections) — `agent-verse-backend/app/knowledge_graph/access_control.py`
- **GraphAccessControl (role→operation matrix, tenant overrides)** (1 connections) — `agent-verse-backend/app/knowledge_graph/access_control.py`
- **_ENTITY_PATTERNS regex extraction (person/acronym/code/quoted/url)** (1 connections) — `agent-verse-backend/app/knowledge_graph/extractor.py`
- **LLM JSON-array entity/relationship prompt (text truncated 1000/800 chars)** (1 connections) — `agent-verse-backend/app/knowledge_graph/extractor.py`
- **EdgeType enum (mentions/supports/contradicts/caused_by/depends_on/...)** (1 connections) — `agent-verse-backend/app/knowledge_graph/models.py`
- **GraphCommunity (named cluster of node_ids)** (1 connections) — `agent-verse-backend/app/knowledge_graph/models.py`
- **get_graph_stats (node type counts + avg confidence)** (1 connections) — `agent-verse-backend/app/knowledge_graph/store.py`

## Relationships

- [Community 525](Community_525.md) (2 shared connections)
- [Community 505](Community_505.md) (2 shared connections)

## Source Files

- `agent-verse-backend/app/knowledge_graph/access_control.py`
- `agent-verse-backend/app/knowledge_graph/community_detection.py`
- `agent-verse-backend/app/knowledge_graph/extractor.py`
- `agent-verse-backend/app/knowledge_graph/models.py`
- `agent-verse-backend/app/knowledge_graph/store.py`

## Audit Trail

- EXTRACTED: 11 (65%)
- INFERRED: 3 (18%)
- AMBIGUOUS: 3 (18%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*