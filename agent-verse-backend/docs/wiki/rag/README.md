---
title: RAG Patterns — Index
description: Index of all 18 Retrieval-Augmented Generation strategy documents in the AgentVerse wiki.
outline: deep
---

# RAG Patterns

AgentVerse implements **18 distinct RAG strategies** across four maturity tiers. Every strategy is implemented under `app/rag/agentic/patterns/` and exposed via the `RAGEngine` (`app/rag/engine.py`) and `KnowledgeStore` (`app/knowledge/store.py`).

This section documents each strategy with architecture, code paths, selection criteria, performance benchmarks, and real-world deployment examples.

---

## Pages in This Section

| File | Strategies Covered |
|---|---|
| [00 — Overview & Selection Guide](./00-rag-patterns-overview.md) | All 18 patterns, selection decision tree, performance matrix |
| [01 — Naive, Hybrid & HyDE RAG](./01-naive-and-hybrid-rag.md) | `NAIVE`, `HYBRID`, `HYDE` |
| [02 — Multi-Hop & Graph RAG](./02-multi-hop-and-graph-rag.md) | `MULTI_HOP`, `GRAPH` |
| [03 — Corrective RAG](./03-corrective-and-multi-hop.md) | `CORRECTIVE` |
| [04 — FLARE, RAPTOR & Self-RAG](./04-flare-and-raptor.md) | `FLARE`, `RAPTOR`, `SELF_RAG` |
| [06 — Agentic & Web-Augmented Patterns](./06-agentic-patterns.md) | `FUSION`, `SPECULATIVE`, `AGENTIC`, `AGENTIC_CHUNKING`, `WEB_AUGMENTED` |
| [07 — Advanced Patterns](./07-advanced-patterns.md) | `ADAPTIVE`, `MODULAR`, `COLBERT`, `RAFT` |
| [08 — RAG at Scale](./08-at-scale.md) | Infrastructure, HNSW, caching, multi-tenant isolation |
| [09 — Integration Guide](./09-integration-guide.md) | All 7 chunkers, `DataClassification`, `CitationManager`, end-to-end example |

---

## Strategy Quick-Reference

| Strategy | Enum | Best For | Latency |
|---|---|---|---|
| Naive | `NAIVE` | Simple Q&A, < 50K chunks | < 50 ms |
| Hybrid | `HYBRID` | Mixed keyword + semantic | 60–120 ms |
| HyDE | `HYDE` | Sparse knowledge, academic queries | 80–150 ms |
| Multi-Hop | `MULTI_HOP` | Multi-entity questions, PE due diligence | 200–500 ms |
| Graph | `GRAPH` | Relationship queries, drug interactions | 300–800 ms |
| Corrective | `CORRECTIVE` | High-stakes domains, pharma regulatory | 150–300 ms |
| FLARE | `FLARE` | Long-form generation, medical AI | 200–400 ms |
| RAPTOR | `RAPTOR` | Hierarchical summarisation, investment research | 400–800 ms |
| Self-RAG | `SELF_RAG` | Legal, autonomous quality verification | 300–600 ms |
| Fusion | `FUSION` | Patent search, multi-perspective retrieval | 250–500 ms |
| Speculative | `SPECULATIVE` | High-throughput read-heavy (500 M req/day) | 30–60 ms |
| Agentic | `AGENTIC` | Complex multi-step research | 1–5 s |
| Agentic Chunking | `AGENTIC_CHUNKING` | Wikipedia-scale, heterogeneous corpora | N/A (offline) |
| Web-Augmented | `WEB_AUGMENTED` | Real-time data, hedge fund news | 500 ms–2 s |
| Adaptive | `ADAPTIVE` | Multi-tenant platforms, Big 4 consulting | Varies |
| Modular | `MODULAR` | Global bank, composable pipelines | 100–300 ms |
| ColBERT | `COLBERT` | Code search, PubMed, token-level matching | 80–200 ms |
| RAFT | `RAFT` | Fine-tuned domain accuracy, insurance/EHR | < 50 ms |

---

## How Strategies Are Selected

`RAGEngine.retrieve()` delegates to the `PatternFactory`, which maps a `RAGStrategy` enum
value to a concrete implementation class. The strategy can be:

1. **Explicitly specified** in the request: `rag_strategy=RAGStrategy.HYBRID`
2. **Adaptively chosen** by `AdaptiveStrategySelector` based on query complexity, tenant
   knowledge density, and latency SLO
3. **Defaulted** to `NAIVE` when no strategy is set

```python
# app/rag/engine.py
async def retrieve(
    self,
    query: str,
    *,
    strategy: RAGStrategy = RAGStrategy.NAIVE,
    tenant_ctx: TenantContext,
    collection_id: str | None = None,
    top_k: int = 10,
) -> list[dict]:
    impl = self._factory.get(strategy)
    return await impl.retrieve(query, tenant_ctx=tenant_ctx, top_k=top_k)
```

---

## Related Sections

- [Retrieval Strategies](../retrieval-strategies/README.md) — low-level retrieval mechanics (BM25, pgvector, RRF, reranking)
- [Chunking Strategies](../chunking-strategies/README.md) — how documents are split before indexing
- [Embeddings](../embeddings/README.md) — embedding models, dimensions, and pgvector HNSW configuration
- [Knowledge & Knowledge Graph](../knowledge-and-kg/README.md) — `KnowledgeStore`, collections, and graph queries
