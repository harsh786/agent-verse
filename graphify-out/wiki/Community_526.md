# Community 526

> 11 nodes · cohesion 0.22

## Key Concepts

- **ContentType enum (pdf/docx/html/md/code/image/audio/video/csv/json/excel/yaml/parquet/avro/latex/notebook)** (6 connections) — `agent-verse-backend/app/ingestion/content_classifier.py`
- **ParserRegistry (ContentType → parser instance map)** (4 connections) — `agent-verse-backend/app/ingestion/parser_registry.py`
- **select_and_chunk (Stage 8 dispatch with per-strategy fallback to _fixed_chunk)** (3 connections) — `agent-verse-backend/app/ingestion/chunking_strategy_selector.py`
- **_STRATEGY_MAP (content type → semantic/heading/layout/paragraph/dom/ast/region/timestamp/scene/row_group/record)** (3 connections) — `agent-verse-backend/app/ingestion/chunking_strategy_selector.py`
- **EmbeddingPolicySelector (modality→model/dim/index/cost, hnsw over 1000 docs)** (3 connections) — `agent-verse-backend/app/ingestion/embedding_policy_selector.py`
- **ModalityPipeline.select_pipeline (transcription/vision flags per content type)** (3 connections) — `agent-verse-backend/app/ingestion/modality_pipeline.py`
- **_EXT_MAP filename-extension → ContentType table** (2 connections) — `agent-verse-backend/app/ingestion/content_classifier.py`
- **ContentClassifier.classify (regex sniffing: html→json→code→markdown→text)** (2 connections) — `agent-verse-backend/app/ingestion/content_classifier.py`
- **CodeParser (split on def/class/function/const/let boundaries)** (1 connections) — `agent-verse-backend/app/ingestion/parser_registry.py`
- **HTMLParser (regex tag strip, also serves WEB_PAGE)** (1 connections) — `agent-verse-backend/app/ingestion/parser_registry.py`
- **PDFTextParser (form-feed page split, else paragraph split)** (1 connections) — `agent-verse-backend/app/ingestion/parser_registry.py`

## Relationships

- [Community 861](Community_861.md) (2 shared connections)
- [Community 751](Community_751.md) (1 shared connections)
- [Community 634](Community_634.md) (1 shared connections)
- [Community 525](Community_525.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/ingestion/chunking_strategy_selector.py`
- `agent-verse-backend/app/ingestion/content_classifier.py`
- `agent-verse-backend/app/ingestion/embedding_policy_selector.py`
- `agent-verse-backend/app/ingestion/modality_pipeline.py`
- `agent-verse-backend/app/ingestion/parser_registry.py`

## Audit Trail

- EXTRACTED: 11 (65%)
- INFERRED: 4 (24%)
- AMBIGUOUS: 2 (12%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*