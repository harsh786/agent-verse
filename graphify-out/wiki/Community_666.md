# Community 666

> 7 nodes · cohesion 0.29

## Key Concepts

- **ingestion.sync_source Celery task (lock→load→delta loop→cursor→complete)** (6 connections) — `agent-verse-backend/app/ingestion/scheduler.py`
- **ingestion_dlq table (add/retry/resolve/permanent_failure, retry_count<5)** (2 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **ingestion.retry_dlq_entries task (reprocessing failed documents)** (2 connections) — `agent-verse-backend/app/ingestion/scheduler.py`
- **on_webhook push-event path (default NotImplementedError)** (1 connections) — `agent-verse-backend/app/ingestion/base_connector.py`
- **LAW-20 feature-flag gate on get_connector** (1 connections) — `agent-verse-backend/app/ingestion/connector_registry.py`
- **LAW-14 Redis SETNX ingestion lock with in-memory fallback** (1 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **BEAT_SCHEDULE (dispatch_due_sources 60s, retry_dlq 300s, queue=ingestion)** (1 connections) — `agent-verse-backend/app/ingestion/scheduler.py`

## Relationships

- [Community 1030](Community_1030.md) (1 shared connections)
- [Community 634](Community_634.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/ingestion/base_connector.py`
- `agent-verse-backend/app/ingestion/connector_registry.py`
- `agent-verse-backend/app/ingestion/job_tracker.py`
- `agent-verse-backend/app/ingestion/scheduler.py`

## Audit Trail

- EXTRACTED: 5 (62%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 3 (38%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*