# Community 634

> 8 nodes · cohesion 0.29

## Key Concepts

- **IngestionPipeline (13-stage canonical RawDocument → indexed chunks path)** (12 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **BaseConnector ABC (LAW-01 single ingestion interface)** (2 connections) — `agent-verse-backend/app/ingestion/base_connector.py`
- **ParserRegistry.parse(bytes) — Stage 5 entry with UTF-8 replace fallback** (2 connections) — `agent-verse-backend/app/ingestion/parser_registry.py`
- **RawDocument (bytes + immutable provenance + acl + correlation_id)** (2 connections) — `agent-verse-backend/app/ingestion/source_config.py`
- **bytes↔str bridge adapters (_ExcelBridge/_ParquetBridge/_AvroBridge/_YAMLBridge/_LaTeXBridge)** (1 connections) — `agent-verse-backend/app/ingestion/parser_registry.py`
- **_emit_metrics — LAW-12 Prometheus counters unimplemented (imports TRIGGER_FIRED_TOTAL, unused)** (1 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **Stage 13 EMIT — Redis publish is a stub (json.dumps discarded, log only)** (1 connections) — `agent-verse-backend/app/ingestion/pipeline.py`
- **PipelineResult (status + skip_reason taxonomy)** (1 connections) — `agent-verse-backend/app/ingestion/source_config.py`

## Relationships

- [Community 525](Community_525.md) (2 shared connections)
- [Community 526](Community_526.md) (1 shared connections)
- [Community 751](Community_751.md) (1 shared connections)
- [Community 505](Community_505.md) (1 shared connections)
- [Community 666](Community_666.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/ingestion/base_connector.py`
- `agent-verse-backend/app/ingestion/parser_registry.py`
- `agent-verse-backend/app/ingestion/pipeline.py`
- `agent-verse-backend/app/ingestion/source_config.py`

## Audit Trail

- EXTRACTED: 6 (43%)
- INFERRED: 4 (29%)
- AMBIGUOUS: 4 (29%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*