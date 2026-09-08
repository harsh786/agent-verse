# Community 1030

> 3 nodes · cohesion 0.67

## Key Concepts

- **get_delta cursor contract (resumable, idempotent, modified-at ascending)** (2 connections) — `agent-verse-backend/app/ingestion/base_connector.py`
- **update_cursor (LAW-03 resumability: source_configs.cursor_value + ingestion_jobs.cursor_after)** (2 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **cursor commit every 100 documents (LAW-14 atomicity)** (1 connections) — `agent-verse-backend/app/ingestion/scheduler.py`

## Relationships

- [Community 666](Community_666.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/ingestion/base_connector.py`
- `agent-verse-backend/app/ingestion/job_tracker.py`
- `agent-verse-backend/app/ingestion/scheduler.py`

## Audit Trail

- EXTRACTED: 2 (67%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 1 (33%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*