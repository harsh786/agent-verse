# BM25 Retrieval

> 92 nodes · cohesion 0.04

## Key Concepts

- **KnowledgeStore** (55 connections) — `agent-verse-backend/app/rag/store.py`
- **rag/store.py** (32 connections) — `agent-verse-backend/app/rag/store.py`
- **Any** (14 connections)
- **Chunk** (11 connections) — `agent-verse-backend/app/rag/models.py`
- **RuntimeError** (11 connections)
- **.hybrid_search_db()** (10 connections) — `agent-verse-backend/app/rag/store.py`
- **._search_precomputed_memory()** (10 connections) — `agent-verse-backend/app/rag/store.py`
- **BM25Retriever** (9 connections) — `agent-verse-backend/app/rag/bm25.py`
- **expand_agentic_parent_results()** (9 connections) — `agent-verse-backend/app/rag/engine.py`
- **._expand_agentic_parent_citations()** (9 connections) — `agent-verse-backend/app/rag/store.py`
- **.hybrid_search()** (9 connections) — `agent-verse-backend/app/rag/store.py`
- **load_agentic_parent_citations()** (8 connections) — `agent-verse-backend/app/rag/engine.py`
- **.ingest_document()** (8 connections) — `agent-verse-backend/app/rag/store.py`
- **._persist_chunks()** (8 connections) — `agent-verse-backend/app/rag/store.py`
- **.search_precomputed_index()** (8 connections) — `agent-verse-backend/app/rag/store.py`
- **rag/models.py** (7 connections) — `agent-verse-backend/app/rag/models.py`
- **.create_collection()** (7 connections) — `agent-verse-backend/app/rag/store.py`
- **.create_collection_async()** (7 connections) — `agent-verse-backend/app/rag/store.py`
- **.delete_document_async()** (7 connections) — `agent-verse-backend/app/rag/store.py`
- **.get_collection_async()** (7 connections) — `agent-verse-backend/app/rag/store.py`
- **.get_ingestion_job_async()** (7 connections) — `agent-verse-backend/app/rag/store.py`
- **.ingest_chunks_async()** (7 connections) — `agent-verse-backend/app/rag/store.py`
- **.ingest_repository_chunks_async()** (7 connections) — `agent-verse-backend/app/rag/store.py`
- **.search()** (7 connections) — `agent-verse-backend/app/rag/store.py`
- **.update_ingestion_job_async()** (7 connections) — `agent-verse-backend/app/rag/store.py`
- *... and 67 more nodes in this community*

## Relationships

- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (29 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (17 shared connections)
- [Community 55](Community_55.md) (14 shared connections)
- [Knowledge Ingestion API](Knowledge_Ingestion_API.md) (11 shared connections)
- [Community 409](Community_409.md) (5 shared connections)
- [Community 159](Community_159.md) (5 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (4 shared connections)
- [Federated RAG Search](Federated_RAG_Search.md) (3 shared connections)
- [Community 58](Community_58.md) (3 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (3 shared connections)
- [RAG Capability Catalogue](RAG_Capability_Catalogue.md) (3 shared connections)
- [Community 119](Community_119.md) (2 shared connections)

## Source Files

- `agent-verse-backend/app/rag/bm25.py`
- `agent-verse-backend/app/rag/engine.py`
- `agent-verse-backend/app/rag/models.py`
- `agent-verse-backend/app/rag/store.py`

## Audit Trail

- EXTRACTED: 277 (99%)
- INFERRED: 3 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*