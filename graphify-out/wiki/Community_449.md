# Community 449

> 14 nodes · cohesion 0.18

## Key Concepts

- **ContentDeduplicator** (6 connections) — `agent-verse-backend/app/ingestion/quality_checks.py`
- **quality_checks.py** (5 connections) — `agent-verse-backend/app/ingestion/quality_checks.py`
- **.deduplicate()** (4 connections) — `agent-verse-backend/app/ingestion/quality_checks.py`
- **.hash_chunk()** (4 connections) — `agent-verse-backend/app/ingestion/quality_checks.py`
- **.is_duplicate()** (3 connections) — `agent-verse-backend/app/ingestion/quality_checks.py`
- **DeduplicationResult** (2 connections) — `agent-verse-backend/app/ingestion/quality_checks.py`
- **.check()** (2 connections) — `agent-verse-backend/app/ingestion/quality_checks.py`
- **QualityCheckResult** (2 connections) — `agent-verse-backend/app/ingestion/quality_checks.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/ingestion/quality_checks.py`
- **QualityChecker — validates chunks before ingestion.** (1 connections) — `agent-verse-backend/app/ingestion/quality_checks.py`
- **Session-scoped chunk deduplicator using SHA-256 content hashes. Eliminates…** (1 connections) — `agent-verse-backend/app/ingestion/quality_checks.py`
- **Return the SHA-256 hex digest of the normalised chunk content.** (1 connections) — `agent-verse-backend/app/ingestion/quality_checks.py`
- **Filter *chunks* to only those whose hash has not been seen before.** (1 connections) — `agent-verse-backend/app/ingestion/quality_checks.py`
- **Return True if this exact content has already been processed.** (1 connections) — `agent-verse-backend/app/ingestion/quality_checks.py`

## Relationships

- [Community 58](Community_58.md) (2 shared connections)

## Source Files

- `agent-verse-backend/app/ingestion/quality_checks.py`

## Audit Trail

- EXTRACTED: 18 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*