# Community 300

> 22 nodes · cohesion 0.17

## Key Concepts

- **RerankPolicy** (15 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **Any** (8 connections)
- **._cross_encoder_rerank()** (7 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **._diversity_rerank()** (7 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **.rerank()** (7 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **.rerank_async()** (7 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **._llm_rerank_sync()** (5 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **._mmr_with_embeddings()** (5 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **._mmr_with_tokens()** (5 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **._tfidf_rerank()** (5 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **._cosine()** (3 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **._jaccard()** (3 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **True Maximal Marginal Relevance reranking. Selects documents that are both…** (1 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **Compute cosine similarity between two vectors.** (1 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **MMR using real embedding cosine similarity.** (1 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **Token-level Jaccard similarity as fallback.** (1 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **MMR using token Jaccard as fallback when no embeddings.** (1 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **Real cross-encoder reranking via sentence-transformers. Falls back to TF-IDF…** (1 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **Async reranking — cross-encoder via thread pool for blocking inference.** (1 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **TF-IDF weighted token overlap reranking (improved fallback).** (1 connections) — `agent-verse-backend/app/context/rerank_policy.py`
- **Cross-encoder reranking (uses sentence-transformers when available, else TF-…** (1 connections) — `agent-verse-backend/app/context/rerank_policy.py`

## Relationships

- [Community 138](Community_138.md) (6 shared connections)
- [ColBERT Reranking](ColBERT_Reranking.md) (1 shared connections)
- [Community 217](Community_217.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/context/rerank_policy.py`

## Audit Trail

- EXTRACTED: 47 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*