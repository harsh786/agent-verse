# Community 515

> 11 nodes · cohesion 0.18

## Key Concepts

- **._get_stats()** (7 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **.lookup_sync()** (5 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **.stats()** (5 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **.store_sync()** (5 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **.lookup()** (4 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **.store()** (4 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **Legacy sync store. Stores in L1 only (no Redis without async).** (1 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **Legacy sync lookup. Checks L1 only.** (1 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **Backward-compatible sync store alias → calls store_sync.** (1 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **Backward-compatible sync lookup alias → calls lookup_sync.** (1 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **Return rich stats including hit rate, bytes saved, and L1/L2 breakdown. Tracks…** (1 connections) — `agent-verse-backend/app/rag/semantic_cache.py`

## Relationships

- [Community 435](Community_435.md) (7 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (5 shared connections)
- [Community 584](Community_584.md) (2 shared connections)
- [Community 499](Community_499.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/rag/semantic_cache.py`

## Audit Trail

- EXTRACTED: 25 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*