# Community 435

> 15 nodes · cohesion 0.17

## Key Concepts

- **SemanticCache (3-layer L1/L2/L3)** (33 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **.store_async()** (6 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **._redis_store()** (5 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **.warm()** (5 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **.set_async()** (4 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **.clear()** (3 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **._l1_text_set()** (2 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **.size()** (2 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **World-class semantic cache with true cosine-similarity matching. Layer 1 (L1):…** (1 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **Store a step execution result in L1 + L2. Silently ignores all storage errors —…** (1 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **Backward-compatible wrapper for old hash-based API.** (1 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **Backward-compatible sync clear. Clears L1 and resets stats (no Redis flush).** (1 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **Pre-populate the cache with known step→response patterns. Two calling…** (1 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **Return number of entries stored in Redis for this tenant.** (1 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **Store one entry in Redis. Data structure: HASH scv2:entry:{tenant}:{entry_id}…** (1 connections) — `agent-verse-backend/app/rag/semantic_cache.py`

## Relationships

- [Community 515](Community_515.md) (7 shared connections)
- [Community 498](Community_498.md) (4 shared connections)
- [Community 499](Community_499.md) (3 shared connections)
- [Community 584](Community_584.md) (3 shared connections)
- [Knowledge Ingestion API](Knowledge_Ingestion_API.md) (2 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Community 901](Community_901.md) (2 shared connections)
- [Community 900](Community_900.md) (2 shared connections)
- [Community 330](Community_330.md) (1 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (1 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (1 shared connections)
- [Community 102](Community_102.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/rag/semantic_cache.py`

## Audit Trail

- EXTRACTED: 46 (94%)
- INFERRED: 3 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*