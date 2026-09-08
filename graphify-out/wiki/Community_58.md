# Community 58

> 65 nodes · cohesion 0.05

## Key Concepts

- **ingestion/orchestrator.py** (31 connections) — `agent-verse-backend/app/ingestion/orchestrator.py`
- **IngestionOrchestrator** (22 connections) — `agent-verse-backend/app/ingestion/orchestrator.py`
- **ParserRegistry** (16 connections) — `agent-verse-backend/app/ingestion/parser_registry.py`
- **.ingest()** (12 connections) — `agent-verse-backend/app/ingestion/orchestrator.py`
- **QualityChecker** (10 connections) — `agent-verse-backend/app/ingestion/quality_checks.py`
- **IndexingDependency** (10 connections) — `agent-verse-backend/app/rag/indexing.py`
- **ParentChildChunker** (10 connections) — `agent-verse-backend/app/rag/parent_child_chunker.py`
- **ContentClassifier** (9 connections) — `agent-verse-backend/app/ingestion/content_classifier.py`
- **RAGIndexingConfig** (9 connections) — `agent-verse-backend/app/rag/indexing.py`
- **SentenceWindowChunker** (9 connections) — `agent-verse-backend/app/rag/sentence_window.py`
- **._chunk()** (8 connections) — `agent-verse-backend/app/ingestion/orchestrator.py`
- **.__init__()** (8 connections) — `agent-verse-backend/app/ingestion/orchestrator.py`
- **EmptyIndexedContentError** (5 connections) — `agent-verse-backend/app/ingestion/orchestrator.py`
- **._chunk_with_quality_check()** (5 connections) — `agent-verse-backend/app/ingestion/orchestrator.py`
- **TextParser** (5 connections) — `agent-verse-backend/app/ingestion/parser_registry.py`
- **.__init__()** (5 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **.chunk()** (5 connections) — `agent-verse-backend/app/rag/parent_child_chunker.py`
- **sentence_window.py** (5 connections) — `agent-verse-backend/app/rag/sentence_window.py`
- **._filter_quality()** (4 connections) — `agent-verse-backend/app/ingestion/orchestrator.py`
- **Any** (4 connections)
- **parent_child_chunker.py** (4 connections) — `agent-verse-backend/app/rag/parent_child_chunker.py`
- **.chunk()** (4 connections) — `agent-verse-backend/app/rag/sentence_window.py`
- **SentenceWindowRetriever** (4 connections) — `agent-verse-backend/app/rag/sentence_window.py`
- **IngestionResult** (3 connections) — `agent-verse-backend/app/ingestion/orchestrator.py`
- **.get_parser()** (3 connections) — `agent-verse-backend/app/ingestion/parser_registry.py`
- *... and 40 more nodes in this community*

## Relationships

- [Content Classification & Embedding Policy](Content_Classification_&_Embedding_Policy.md) (19 shared connections)
- [Knowledge Ingestion API](Knowledge_Ingestion_API.md) (12 shared connections)
- [Community 159](Community_159.md) (9 shared connections)
- [Community 62](Community_62.md) (7 shared connections)
- [Community 225](Community_225.md) (6 shared connections)
- [Community 119](Community_119.md) (4 shared connections)
- [Ingestion Connectors](Ingestion_Connectors.md) (3 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (3 shared connections)
- [BM25 Retrieval](BM25_Retrieval.md) (3 shared connections)
- [Community 434](Community_434.md) (2 shared connections)
- [RAG Capability Catalogue](RAG_Capability_Catalogue.md) (2 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (2 shared connections)

## Source Files

- `agent-verse-backend/app/ingestion/content_classifier.py`
- `agent-verse-backend/app/ingestion/orchestrator.py`
- `agent-verse-backend/app/ingestion/parser_registry.py`
- `agent-verse-backend/app/ingestion/pipeline.py`
- `agent-verse-backend/app/ingestion/quality_checks.py`
- `agent-verse-backend/app/rag/indexing.py`
- `agent-verse-backend/app/rag/parent_child_chunker.py`
- `agent-verse-backend/app/rag/sentence_window.py`

## Audit Trail

- EXTRACTED: 147 (85%)
- INFERRED: 25 (15%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*