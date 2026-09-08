# Community 596

> 9 nodes · cohesion 0.28

## Key Concepts

- **knowledge_nodes table (upsert on conflict, extra_metadata jsonb)** (4 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **RAGIndexingPipeline branch (multi-strategy indexed ingestion)** (3 connections) — `agent-verse-backend/app/ingestion/orchestrator.py`
- **add_node/add_edge fire-and-forget asyncio DB persist** (3 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **uuid5(NAMESPACE_DNS, tenant_id:label) stable entity node id** (2 connections) — `agent-verse-backend/app/knowledge_graph/extractor.py`
- **SHA-256(tenant\x1fcollection\x1fsource_identity) deterministic document_id for indexed re-ingest** (2 connections) — `agent-verse-backend/app/ingestion/orchestrator.py`
- **knowledge_edges table (insert-ignore on conflict)** (2 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **lazy per-tenant DB hydration on query_nodes (once per process)** (2 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **sqlalchemy_rls_context wrapping every KG DB read/write** (2 connections) — `agent-verse-backend/app/knowledge_graph/store.py`
- **EmptyIndexedContentError (refuse to replace a source with zero indexable chunks)** (1 connections) — `agent-verse-backend/app/ingestion/orchestrator.py`

## Relationships

- [Community 525](Community_525.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/ingestion/orchestrator.py`
- `agent-verse-backend/app/knowledge_graph/extractor.py`
- `agent-verse-backend/app/knowledge_graph/store.py`

## Audit Trail

- EXTRACTED: 8 (73%)
- INFERRED: 2 (18%)
- AMBIGUOUS: 1 (9%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*