# Community 525

> 11 nodes · cohesion 0.20

## Key Concepts

- **KGIngestionHook (post-ingest KG entity/relation extraction)** (7 connections) — `agent-verse-backend/app/knowledge_graph/ingestion_hook.py`
- **IngestionOrchestrator (str-content ingest: classify→parse→quality→chunk→embed→store)** (6 connections) — `agent-verse-backend/app/ingestion/orchestrator.py`
- **Stage 12 INDEX (build rag.models.Chunk, ingest_chunks_async into pgvector+BM25)** (4 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **Stage 9 ENRICH (chunk metadata: doc title/author/acl/content_hash/correlation_id)** (4 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **ProvenanceBuilder / IngestionProvenance (source_url, chunk_index, page_number)** (2 connections) — `agent-verse-backend/app/ingestion/provenance_builder.py`
- **QualityChecker (min length, noise-only regex, word-char ratio score)** (2 connections) — `agent-verse-backend/app/ingestion/quality_checks.py`
- **get_acl source-permission propagation (LAW-07)** (1 connections) — `agent-verse-backend/app/ingestion/base_connector.py`
- **first-10-chunks combined_text cost cap for KG extraction** (1 connections) — `agent-verse-backend/app/knowledge_graph/ingestion_hook.py`
- **swallow-all-exceptions policy: KG extraction never blocks ingestion** (1 connections) — `agent-verse-backend/app/knowledge_graph/ingestion_hook.py`
- **NodeType enum (document/chunk/entity/concept/goal/tool/memory/artifact/agent/workflow)** (1 connections) — `agent-verse-backend/app/knowledge_graph/models.py`
- **in_memory_only guard (in-memory KnowledgeStore must be opted into)** (1 connections) — `agent-verse-backend/app/ingestion/orchestrator.py`

## Relationships

- [Community 476](Community_476.md) (2 shared connections)
- [Community 634](Community_634.md) (2 shared connections)
- [Community 526](Community_526.md) (1 shared connections)
- [Community 596](Community_596.md) (1 shared connections)
- [Community 751](Community_751.md) (1 shared connections)
- [Community 505](Community_505.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/ingestion/base_connector.py`
- `agent-verse-backend/app/ingestion/orchestrator.py`
- `agent-verse-backend/app/ingestion/pipeline.py`
- `agent-verse-backend/app/ingestion/provenance_builder.py`
- `agent-verse-backend/app/ingestion/quality_checks.py`
- `agent-verse-backend/app/knowledge_graph/ingestion_hook.py`
- `agent-verse-backend/app/knowledge_graph/models.py`

## Audit Trail

- EXTRACTED: 12 (63%)
- INFERRED: 4 (21%)
- AMBIGUOUS: 3 (16%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*