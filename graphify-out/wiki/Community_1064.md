# Community 1064

> 2 nodes · cohesion 1.00

## Key Concepts

- **get_due_sources SQL (enabled, non-streaming, last_synced_at + interval <= NOW)** (1 connections) — `agent-verse-backend/app/ingestion/job_tracker.py`
- **_jitter deterministic per-source md5 jitter (thundering-herd guard)** (1 connections) — `agent-verse-backend/app/ingestion/scheduler.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `agent-verse-backend/app/ingestion/job_tracker.py`
- `agent-verse-backend/app/ingestion/scheduler.py`

## Audit Trail

- EXTRACTED: 1 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*