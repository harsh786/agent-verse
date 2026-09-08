# Community 505

> 12 nodes · cohesion 0.17

## Key Concepts

- **federated_search (parallel multi-collection retrieval)** (6 connections) — `agent-verse-backend/app/knowledge/federated_search.py`
- **_content_key (content_hash-or-SHA256 dedup key)** (3 connections) — `agent-verse-backend/app/knowledge/federated_search.py`
- **MultiHopReasoner (BFS path finding + ego-network subgraph)** (3 connections) — `agent-verse-backend/app/knowledge_graph/multi_hop.py`
- **Stage 3 CONTENT_HASH dedup via KnowledgeStore.exists_by_hash** (3 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **ContentDeduplicator (session-scoped SHA-256 chunk dedup)** (3 connections) — `agent-verse-backend/app/ingestion/quality_checks.py`
- **paths_to_context (graph paths → RAG context text, capped at 5)** (2 connections) — `agent-verse-backend/app/knowledge_graph/multi_hop.py`
- **collection-namespaced citation_id (cid:citation_id)** (1 connections) — `agent-verse-backend/app/knowledge/federated_search.py`
- **_normalize_scores (min-max per-collection score normalisation)** (1 connections) — `agent-verse-backend/app/knowledge/federated_search.py`
- **HopPath (fact chain 'A --[rel]--> B' rendering)** (1 connections) — `agent-verse-backend/app/knowledge_graph/multi_hop.py`
- **LAW-22 dry_run exit after ENRICH (parse+chunk, skip embed/index)** (1 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **RawDocument.compute_hash (SHA-256 of content bytes, LAW-02)** (1 connections) — `agent-verse-backend/app/ingestion/source_config.py`
- **KnowledgeGraphStore.find_path (BFS adjacency, max 5 paths)** (1 connections) — `agent-verse-backend/app/knowledge_graph/store.py`

## Relationships

- [Community 476](Community_476.md) (2 shared connections)
- [Community 525](Community_525.md) (1 shared connections)
- [Community 634](Community_634.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/ingestion/pipeline.py`
- `agent-verse-backend/app/ingestion/quality_checks.py`
- `agent-verse-backend/app/ingestion/source_config.py`
- `agent-verse-backend/app/knowledge/federated_search.py`
- `agent-verse-backend/app/knowledge_graph/multi_hop.py`
- `agent-verse-backend/app/knowledge_graph/store.py`

## Audit Trail

- EXTRACTED: 6 (40%)
- INFERRED: 8 (53%)
- AMBIGUOUS: 1 (7%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*