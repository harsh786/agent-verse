# Community 552

> 10 nodes · cohesion 0.20

## Key Concepts

- **RedisDeduplicationCache** (9 connections) — `agent-verse-backend/app/reliability/dedup.py`
- **.get_existing()** (2 connections) — `agent-verse-backend/app/reliability/dedup.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/reliability/dedup.py`
- **.register()** (2 connections) — `agent-verse-backend/app/reliability/dedup.py`
- **.unregister()** (2 connections) — `agent-verse-backend/app/reliability/dedup.py`
- **Any** (1 connections)
- **Redis-backed cross-replica deduplication cache. Prevents duplicate in-flight…** (1 connections) — `agent-verse-backend/app/reliability/dedup.py`
- **Check if identical goal is already in-flight. Returns goal_id or None.** (1 connections) — `agent-verse-backend/app/reliability/dedup.py`
- **Register a goal to prevent duplicates during its execution window.** (1 connections) — `agent-verse-backend/app/reliability/dedup.py`
- **Remove dedup entry after goal completes.** (1 connections) — `agent-verse-backend/app/reliability/dedup.py`

## Relationships

- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Reliability & Audit](Reliability_&_Audit.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/reliability/dedup.py`

## Audit Trail

- EXTRACTED: 12 (92%)
- INFERRED: 1 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*