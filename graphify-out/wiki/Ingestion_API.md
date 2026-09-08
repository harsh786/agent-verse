# Ingestion API

> 76 nodes · cohesion 0.05

## Key Concepts

- **api/ingestion.py** (29 connections) — `agent-verse-backend/app/api/ingestion.py`
- **Request** (18 connections)
- **_require_tenant()** (17 connections) — `agent-verse-backend/app/api/ingestion.py`
- **ingestion/scheduler.py** (17 connections) — `agent-verse-backend/app/ingestion/scheduler.py`
- **get** (10 connections)
- **get_connector()** (10 connections) — `agent-verse-backend/app/ingestion/connector_registry.py`
- **_sync_source_async()** (10 connections) — `agent-verse-backend/app/ingestion/scheduler.py`
- **create_source()** (9 connections) — `agent-verse-backend/app/api/ingestion.py`
- **trigger_sync()** (9 connections) — `agent-verse-backend/app/api/ingestion.py`
- **SourceFamily** (8 connections) — `agent-verse-backend/app/ingestion/source_config.py`
- **preview_source()** (7 connections) — `agent-verse-backend/app/api/ingestion.py`
- **_run_sync()** (7 connections) — `agent-verse-backend/app/api/ingestion.py`
- **_serialize_source()** (7 connections) — `agent-verse-backend/app/api/ingestion.py`
- **get_connector_metadata()** (7 connections) — `agent-verse-backend/app/ingestion/connector_registry.py`
- **job_tracker.py** (7 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **health_check()** (6 connections) — `agent-verse-backend/app/api/ingestion.py`
- **update_source()** (6 connections) — `agent-verse-backend/app/api/ingestion.py`
- **_retry_dlq_async()** (6 connections) — `agent-verse-backend/app/ingestion/scheduler.py`
- **get_catalogue()** (5 connections) — `agent-verse-backend/app/api/ingestion.py`
- **_get_pipeline()** (5 connections) — `agent-verse-backend/app/api/ingestion.py`
- **get_source()** (5 connections) — `agent-verse-backend/app/api/ingestion.py`
- **_get_tracker()** (5 connections) — `agent-verse-backend/app/api/ingestion.py`
- **list_documents()** (5 connections) — `agent-verse-backend/app/api/ingestion.py`
- **list_sources()** (5 connections) — `agent-verse-backend/app/api/ingestion.py`
- **sync_status()** (5 connections) — `agent-verse-backend/app/api/ingestion.py`
- *... and 51 more nodes in this community*

## Relationships

- [Ingestion Connectors](Ingestion_Connectors.md) (18 shared connections)
- [Community 120](Community_120.md) (7 shared connections)
- [Community 82](Community_82.md) (3 shared connections)
- [Community 225](Community_225.md) (3 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (3 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (2 shared connections)
- [Community 136](Community_136.md) (2 shared connections)
- [Community 320](Community_320.md) (1 shared connections)
- [Community 57](Community_57.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/api/ingestion.py`
- `agent-verse-backend/app/ingestion/base_connector.py`
- `agent-verse-backend/app/ingestion/connector_registry.py`
- `agent-verse-backend/app/ingestion/job_tracker.py`
- `agent-verse-backend/app/ingestion/scheduler.py`
- `agent-verse-backend/app/ingestion/source_config.py`

## Audit Trail

- EXTRACTED: 173 (93%)
- INFERRED: 13 (7%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*