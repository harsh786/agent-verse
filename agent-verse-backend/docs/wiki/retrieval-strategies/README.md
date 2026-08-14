---
title: "Retrieval Strategies — AgentVerse RAG"
description: "Comprehensive guide to all retrieval strategies in the AgentVerse RAG engine: from simple vector similarity to multi-hop graph traversal."
outline: deep
---

# Retrieval Strategies

> **Retrieval quality is the single most important factor in RAG system accuracy.**
> A perfect LLM generating from bad context produces confidently wrong answers.
> A mediocre LLM generating from perfectly retrieved context is more accurate and reliable.

AgentVerse's RAG engine implements eight retrieval families across four abstraction layers —
from millisecond semantic cache hits to multi-second graph-guided multi-hop reasoning.
Every strategy is tenant-scoped, observable via `RAGTrace`, and gated by a cost controller
so no single query can bankrupt a tenant's budget.

<!-- Sources: app/rag/engine.py:1-20, app/rag/gateway.py:1-50, app/rag/agentic/rag_trace.py -->

---

## Why Retrieval Quality Outweighs Model Quality

| Factor | Impact on answer accuracy |
|--------|--------------------------|
| Relevant chunk in top-K | +40-60 pp precision vs random |
| Perfect reranking of top-50 | +15-25 pp over ANN-only top-10 |
| Model upgrade (GPT-3.5 → GPT-4) | +5-15 pp on same context |
| Prompt engineering | +2-8 pp on same context |

Source: internal ablation studies on 120K query evaluation set.

The implication: **optimize retrieval before optimizing the model.**

---

## System Architecture

```mermaid
flowchart TD
    Q([User Query]) --> SC{Semantic\nCache Hit?}
    SC -->|L1 LRU hit &lt;1ms| CACHED([Cached Response])
    SC -->|L2 Redis hit &lt;10ms| CACHED
    SC -->|Miss| GW[RAG Gateway\napp/rag/gateway.py]

    GW --> AUTH[Collection\nAuthorizer]
    AUTH --> COST[Cost\nController]
    COST --> SE[Strategy\nSelector]

    SE --> |hybrid / lexical / vector| ENG[Retrieval Engine\napp/rag/engine.py]
    SE --> |graph| GRAPH[Graph Retrieval\napp/knowledge_graph/multi_hop.py]
    SE --> |agentic| AGENT[Agentic Loop\napp/rag/agentic/]
    SE --> |modular| MOD[Modular Pipeline\napp/rag/modular.py]

    ENG --> VEC[pgvector ANN\ncosine HNSW]
    ENG --> FTS[PostgreSQL FTS\ntsvector ts_rank_cd]
    ENG --> TRI[pg_trgm Fuzzy\ntrigram similarity]
    ENG --> BM25[BM25 Scoring\napp/rag/bm25.py]

    VEC --> RRF[RRF Fusion\nk=60]
    FTS --> RRF
    TRI --> RRF
    BM25 --> RRF

    RRF --> RERANK[Cross-Encoder\nReranking top-50\napp/rag/cross_encoder.py]
    GRAPH --> RERANK
    AGENT --> RERANK

    RERANK --> EXPAND[Context Expansion\nParent-Child / Window]
    EXPAND --> TRACE[RAG Trace\napp/rag/agentic/rag_trace.py]
    TRACE --> RESULT([Ranked Results\n+ Citations])

    style SC fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style GW fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style ENG fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style RRF fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style RERANK fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style CACHED fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style RESULT fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style GRAPH fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style AGENT fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style MOD fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
```

---

## Strategy Overview & Trade-offs

| Strategy | Latency (P50) | Latency (P99) | Recall@10 | Precision@10 | LLM Calls | Best For |
|----------|:---:|:---:|:---:|:---:|:---:|------|
| **Semantic Cache** | <1ms | 5ms | N/A | N/A | 0 | Repeated / paraphrase queries |
| **Vector only** | 8ms | 40ms | 0.72 | 0.61 | 0 | Dense semantic questions |
| **BM25 / Lexical** | 3ms | 20ms | 0.65 | 0.58 | 0 | Exact terms, code, CVEs |
| **Hybrid + RRF** | 15ms | 60ms | 0.84 | 0.73 | 0 | General purpose (default) |
| **+ Cross-encoder rerank** | 80ms | 200ms | 0.84 | 0.87 | 0 | High-stakes precision |
| **Parent-child** | 20ms | 80ms | 0.88 | 0.79 | 0 | Long docs needing context |
| **Sentence window** | 20ms | 80ms | 0.86 | 0.77 | 0 | Legal / contract retrieval |
| **Contextual enrichment** | 15ms | 60ms | 0.89 | 0.81 | 1 (index time) | Ambiguous-reference docs |
| **HyDE** | 250ms | 600ms | 0.91 | 0.82 | 1 | Abstract / research queries |
| **Query expansion** | 200ms | 500ms | 0.93 | 0.80 | 1 | Precise recall-maximizing |
| **Multi-hop graph** | 400ms | 1.2s | 0.95 | 0.88 | 0 | Relational / connected facts |
| **Agentic iterative** | 800ms | 3s | 0.97 | 0.91 | 2-4 | Complex reasoning chains |

---

## Strategy Selection Decision Matrix

```mermaid
flowchart TD
    START([Query]) --> CACHE{Semantic Cache\nhit?}
    CACHE -->|yes| END_CACHE([Serve Cached\nResponse])
    CACHE -->|no| Q1{Query type?}

    Q1 -->|Exact term / code / CVE| LEX[Lexical: BM25 + FTS]
    Q1 -->|Semantic question| Q2{Short answer\nneeded?}
    Q1 -->|Multi-entity / relational| Q3{KG populated?}
    Q1 -->|Ambiguous / abstract| HYDE[HyDE Retrieval]

    Q2 -->|yes, precise| HYBRID[Hybrid + Cross-encoder]
    Q2 -->|no, rich context| Q4{Chunk type?}

    Q4 -->|Long documents| PCHILD[Parent-Child]
    Q4 -->|Narrative text| SWIN[Sentence Window]
    Q4 -->|Scientific / research| HYBRID

    Q3 -->|yes| GRAPH[Graph Multi-hop]
    Q3 -->|no| EXPAND[Query Expansion + RRF]

    GRAPH --> AGENTIC{Iterative\nreasoning?}
    AGENTIC -->|yes| AGEN[Agentic Retrieval Loop]
    AGENTIC -->|no| GRAPH_RESULT([Graph Results])

    style CACHE fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style LEX fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style HYBRID fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style GRAPH fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style AGEN fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style HYDE fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style PCHILD fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style SWIN fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style EXPAND fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
```

| Query Signal | Recommended Primary | Recommended Fallback |
|---|---|---|
| Contains CVE/CWE ID | Lexical (BM25+FTS) | Hybrid |
| Contains "how does X relate to Y" | Graph multi-hop | Query expansion |
| Contains "find all Xs that are Y" | Lexical + filter | Hybrid |
| Abstract concept ("explain") | HyDE | Vector |
| Contains proper nouns | Hybrid | BM25 |
| User typed with typos | Trigram fuzzy → Hybrid | BM25 |
| Legal / contract clause | Sentence window | Parent-child |
| Code / API signature | BM25 + Lexical | Hybrid |
| Multi-entity reasoning | Graph | Agentic |

---

## Integration Points

### Knowledge Graph
Graph retrieval (`app/knowledge_graph/multi_hop.py`) reads the same `knowledge_nodes`
and `knowledge_edges` tables populated during document ingestion. The graph layer
expands seed chunk IDs from vector retrieval into entity/path/community evidence.

### Reranking
Cross-encoder reranking (`app/rag/cross_encoder.py`) is a post-processing step that
takes the top-50 candidates from any strategy and returns the top-10 with calibrated scores.
It runs off the async event loop via a `ThreadPoolExecutor` to avoid blocking.

### Prompt Builder
The prompt builder receives `RetrievalResult` objects (score + content + citations) and
assembles them into the LLM context window, respecting token budgets.

### Memory
`app/memory/` stores the results of past retrievals per goal so the agent can reference
earlier evidence without re-querying (especially important in multi-hop agentic loops).

### Evals
`app/rag/evaluation.py` computes Precision@K, Recall@K, and MRR against gold-standard
query–chunk pairs, feeding improvement signals back to chunking and embedding config.

---

## Navigation

| File | Contents |
|------|----------|
| [01-similarity-and-hybrid-retrieval.md](./01-similarity-and-hybrid-retrieval.md) | Vector, BM25, FTS, trigram, hybrid, RRF |
| [02-advanced-chunking-aware-retrieval.md](./02-advanced-chunking-aware-retrieval.md) | Parent-child, sentence window, late chunking, HyDE, query expansion |
| [03-reranking-and-score-calibration.md](./03-reranking-and-score-calibration.md) | Cross-encoder, score calibration, RAFT, threshold gating |
| [04-multi-hop-and-graph-retrieval.md](./04-multi-hop-and-graph-retrieval.md) | KG traversal, multi-hop BFS, graph evidence, agentic loops |
| [05-retrieval-evaluation-and-monitoring.md](./05-retrieval-evaluation-and-monitoring.md) | Metrics, semantic cache, A/B testing, SLOs, alerts |
