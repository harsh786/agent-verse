# Content Classification & Embedding Policy

> 82 nodes · cohesion 0.03

## Key Concepts

- **ContentType** (32 connections) — `agent-verse-backend/app/ingestion/content_classifier.py`
- **parser_registry.py** (25 connections) — `agent-verse-backend/app/ingestion/parser_registry.py`
- **.__init__()** (21 connections) — `agent-verse-backend/app/ingestion/parser_registry.py`
- **content_classifier.py** (12 connections) — `agent-verse-backend/app/ingestion/content_classifier.py`
- **AvroParser** (6 connections) — `agent-verse-backend/app/ingestion/parsers/avro_parser.py`
- **ExcelParser** (6 connections) — `agent-verse-backend/app/ingestion/parsers/excel_parser.py`
- **LaTeXParser** (6 connections) — `agent-verse-backend/app/ingestion/parsers/latex_parser.py`
- **ParquetParser** (6 connections) — `agent-verse-backend/app/ingestion/parsers/parquet_parser.py`
- **embedding_policy_selector.py** (5 connections) — `agent-verse-backend/app/ingestion/embedding_policy_selector.py`
- **modality_pipeline.py** (5 connections) — `agent-verse-backend/app/ingestion/modality_pipeline.py`
- **provenance_builder.py** (5 connections) — `agent-verse-backend/app/ingestion/provenance_builder.py`
- **_AvroBridge** (4 connections) — `agent-verse-backend/app/ingestion/parser_registry.py`
- **_ExcelBridge** (4 connections) — `agent-verse-backend/app/ingestion/parser_registry.py`
- **_LaTeXBridge** (4 connections) — `agent-verse-backend/app/ingestion/parser_registry.py`
- **_ParquetBridge** (4 connections) — `agent-verse-backend/app/ingestion/parser_registry.py`
- **VisionParser** (4 connections) — `agent-verse-backend/app/ingestion/parser_registry.py`
- **_YAMLBridge** (4 connections) — `agent-verse-backend/app/ingestion/parser_registry.py`
- **latex_parser.py** (4 connections) — `agent-verse-backend/app/ingestion/parsers/latex_parser.py`
- **.build()** (4 connections) — `agent-verse-backend/app/ingestion/provenance_builder.py`
- **EmbeddingPolicySelector** (3 connections) — `agent-verse-backend/app/ingestion/embedding_policy_selector.py`
- **.select()** (3 connections) — `agent-verse-backend/app/ingestion/embedding_policy_selector.py`
- **ModalityPipeline** (3 connections) — `agent-verse-backend/app/ingestion/modality_pipeline.py`
- **.select_pipeline()** (3 connections) — `agent-verse-backend/app/ingestion/modality_pipeline.py`
- **ModalityPipelineResult** (3 connections) — `agent-verse-backend/app/ingestion/modality_pipeline.py`
- **AudioTranscriptParser** (3 connections) — `agent-verse-backend/app/ingestion/parser_registry.py`
- *... and 57 more nodes in this community*

## Relationships

- [Community 58](Community_58.md) (19 shared connections)
- [Community 62](Community_62.md) (5 shared connections)
- [Community 119](Community_119.md) (4 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (4 shared connections)
- [Community 277](Community_277.md) (2 shared connections)
- [Community 393](Community_393.md) (2 shared connections)
- [Community 540](Community_540.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Ingestion Connectors](Ingestion_Connectors.md) (1 shared connections)
- [Community 225](Community_225.md) (1 shared connections)
- [Community 141](Community_141.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/ingestion/content_classifier.py`
- `agent-verse-backend/app/ingestion/embedding_policy_selector.py`
- `agent-verse-backend/app/ingestion/modality_pipeline.py`
- `agent-verse-backend/app/ingestion/parser_registry.py`
- `agent-verse-backend/app/ingestion/parsers/avro_parser.py`
- `agent-verse-backend/app/ingestion/parsers/excel_parser.py`
- `agent-verse-backend/app/ingestion/parsers/latex_parser.py`
- `agent-verse-backend/app/ingestion/parsers/parquet_parser.py`
- `agent-verse-backend/app/ingestion/provenance_builder.py`

## Audit Trail

- EXTRACTED: 139 (90%)
- INFERRED: 16 (10%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*