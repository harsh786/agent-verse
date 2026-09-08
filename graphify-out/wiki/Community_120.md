# Community 120

> 41 nodes · cohesion 0.06

## Key Concepts

- **IngestionJobTracker** (32 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **IngestionJob** (8 connections)
- **.create_job()** (5 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **.update_cursor()** (5 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **.complete_job()** (4 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **.increment_counters()** (3 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **.load_config()** (3 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **._persist_job_completed()** (3 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **._persist_job_created()** (3 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **SourceConfig** (3 connections)
- **.acquire_lock()** (2 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **.add_to_dlq()** (2 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **.get_due_sources()** (2 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **.get_job()** (2 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **.get_retryable_dlq_entries()** (2 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **.increment_dlq_retry()** (2 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **.increment_failure_counter()** (2 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **.list_jobs_for_source()** (2 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **.mark_dlq_permanent_failure()** (2 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **._persist_cursor_update()** (2 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **.release_lock()** (2 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **.reset_failure_counter()** (2 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **.resolve_dlq_entry()** (2 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **Any** (1 connections)
- *... and 16 more nodes in this community*

## Relationships

- [Ingestion API](Ingestion_API.md) (7 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Ingestion Connectors](Ingestion_Connectors.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/ingestion/job_tracker.py`

## Audit Trail

- EXTRACTED: 59 (95%)
- INFERRED: 3 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*