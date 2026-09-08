# Community 225

> 27 nodes · cohesion 0.12

## Key Concepts

- **IngestionPipeline** (27 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **.ingest()** (15 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **._embed()** (7 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **._index()** (7 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **SourceConfig** (7 connections)
- **._emit()** (5 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **._emit_metrics()** (5 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **._enrich()** (5 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **._check_existing_hash()** (4 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **._chunk()** (4 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **._quality_score()** (4 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **._dedup_chunks()** (3 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **._run_pii()** (3 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **Any** (2 connections)
- **PipelineResult** (2 connections)
- **Return True if this content hash is already indexed for this tenant.** (1 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **Detect and handle PII. Returns (text_after_action, pii_was_detected). Returns…** (1 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **Compute a quality score 0.0–1.0 for the text.** (1 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **Dispatch to appropriate chunking strategy.** (1 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **Add metadata to each chunk before embedding.** (1 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **Embed all chunks, returning chunks with 'embedding' field added.** (1 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **Remove exact-duplicate chunks within this document.** (1 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **Write chunks to KnowledgeStore (pgvector + BM25).** (1 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **The single, canonical path: RawDocument → indexed chunks. Wired in app/main.py…** (1 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **Emit knowledge.updated Redis event and update stats (Stage 13).** (1 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- *... and 2 more nodes in this community*

## Relationships

- [Ingestion Connectors](Ingestion_Connectors.md) (8 shared connections)
- [Community 58](Community_58.md) (6 shared connections)
- [Ingestion API](Ingestion_API.md) (3 shared connections)
- [Community 119](Community_119.md) (3 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Community 62](Community_62.md) (1 shared connections)
- [Content Classification & Embedding Policy](Content_Classification_&_Embedding_Policy.md) (1 shared connections)
- [BM25 Retrieval](BM25_Retrieval.md) (1 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/ingestion/pipeline.py`

## Audit Trail

- EXTRACTED: 59 (86%)
- INFERRED: 10 (14%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*