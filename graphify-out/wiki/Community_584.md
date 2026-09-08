# Community 584

> 9 nodes · cohesion 0.28

## Key Concepts

- **.get_batch()** (6 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **.get_similar()** (6 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **._redis_lookup()** (6 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **_find_best_match()** (5 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **_CacheHit** (4 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **True semantic similarity lookup. Returns a _CacheHit(response, similarity,…** (1 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **Look up multiple embeddings in a single batched Redis pipeline. Dramatically…** (1 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **Load all tenant entries from Redis and find the most similar one. O(n) scan in…** (1 connections) — `agent-verse-backend/app/rag/semantic_cache.py`
- **Find the highest-similarity entry above threshold. Returns (response,…** (1 connections) — `agent-verse-backend/app/rag/semantic_cache.py`

## Relationships

- [Community 498](Community_498.md) (4 shared connections)
- [Community 435](Community_435.md) (3 shared connections)
- [Community 515](Community_515.md) (2 shared connections)
- [Community 499](Community_499.md) (1 shared connections)
- [Community 900](Community_900.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/rag/semantic_cache.py`

## Audit Trail

- EXTRACTED: 21 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*