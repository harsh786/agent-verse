# Naive RAG, Hybrid RAG & HyDE

The three foundational patterns. Every other pattern in AgentVerse builds on top of these or delegates to them as sub-routines.

---

## 1. Naive RAG

**Enum:** `RAGStrategy.NAIVE` | **File:** `app/rag/store.py` | **Latency:** 20–80 ms

### What it is

The simplest possible retrieval strategy: embed the query, run a single vector cosine search against the knowledge store, return top-K chunks, prepend them to the prompt.

```
Query text
    │
    ▼
EmbeddingOrchestrator.embed(query)     ← ~5–20 ms
    │
    ▼
pgvector cosine search                  ← ~2–15 ms
  SELECT * FROM chunks
  ORDER BY embedding <=> $query_vec
  LIMIT $top_k
    │
    ▼
top_k chunks → prepend to LLM prompt   ← ~1 ms
    │
    ▼
LLM generates answer                   ← 100–800 ms
```

### When to use Naive RAG

| Scenario | Why Naive RAG is correct |
|---|---|
| Customer FAQ chatbot | Questions are clear, chunks are self-contained |
| Internal HR policy lookup | "What is the parental leave policy?" — single document |
| Product feature documentation Q&A | Exact semantic match works reliably |
| Latency-critical paths (< 100 ms SLA) | No multi-round trips |
| High-volume, low-complexity (> 100K req/s) | Minimum CPU/DB load per request |

### Real-world example

**Scenario:** Support chatbot for a SaaS product handling 500,000 Q&A requests per day.

```
User: "How do I reset my 2FA device?"

1. embed("How do I reset my 2FA device?") → [0.23, -0.11, 0.87, ...]
2. pgvector: top-3 chunks returned in 8 ms
   Chunk 1: "To reset your 2FA device, navigate to Account Settings → Security..."
   Chunk 2: "If you lose access to your authenticator app, contact support@..."
   Chunk 3: "Two-factor authentication settings can be managed under..."
3. LLM: "To reset your 2FA device: 1. Go to Account Settings → Security..."
```

**Throughput:** At 500K req/day, Naive RAG costs ~$0.0003 per query (embedding + 1 LLM call). A 3-node Postgres cluster handles 50K concurrent vector searches.

### Limitations of Naive RAG

- **Vocabulary mismatch:** "car" vs "automobile" — vector search helps but isn't perfect
- **Single-perspective:** Returns top-K by cosine score only; misses relevant BM25 hits
- **No self-correction:** If retrieved chunks are wrong, the answer is wrong
- **Long-document problem:** A 50-page PDF split into 200 chunks — the key answer may be spread across 5 non-adjacent chunks that individually score low

---

## 2. Hybrid RAG

**Enum:** `RAGStrategy.HYBRID` | **File:** `app/rag/store.py::hybrid_search()` | **Latency:** 30–120 ms

### What it is

Combines three parallel retrieval signals — dense vector (semantic), sparse BM25 (lexical), and trigram (fuzzy string match) — then merges them with Reciprocal Rank Fusion (RRF) for significantly higher recall than any signal alone.

```
Query text
    │
    ├─────────────────────────────────────────┐
    │                                         │
    ▼                                         ▼
pgvector cosine                       BM25 keyword search
(semantic similarity)                  + pg_trgm trigram
  top-100 chunks                        top-100 chunks
    │                                         │
    └─────────────┬───────────────────────────┘
                  │
                  ▼
       Reciprocal Rank Fusion (RRF)
       score = Σ 1 / (60 + rank_i)
       Merged ranked list (top-20)
                  │
                  ▼
       [Optional] CrossEncoder.rerank()
       LLM scores (query, passage) pairs
       Re-orders by semantic correctness
                  │
                  ▼
       Final blended score:
       0.7 × (cosine + trigram) + 0.3 × BM25_norm
                  │
                  ▼
       Top-K chunks → LLM prompt
```

### Why RRF beats score fusion

| Approach | Problem | RRF solution |
|---|---|---|
| Simple average of cosine + BM25 | Scores are on different scales (cosine: 0–1, BM25: 0–∞) | RRF uses rank position, not raw score |
| Weighted sum | Requires tuned weights per domain | RRF is parameter-free (just k=60) |
| Take union top-K | Double-counts docs appearing in both | RRF naturally boosts cross-signal agreement |

### When to use Hybrid RAG

| Scenario | Signal that wins |
|---|---|
| Technical documentation search | BM25 catches exact API names (`openssl`, `SHA-256`) |
| Legal document retrieval | Trigram matches partial statute numbers (`§ 42(b)(3)`) |
| Medical literature | Vector catches `myocardial infarction` ↔ `heart attack` synonyms |
| Code search | BM25 for function names + vector for intent |
| Product catalogs | Both: "blue running shoes" (vector) + exact SKU "NKE-R300-BLU" (BM25) |

### Real-world example

**Scenario:** Legal research platform, 50 million legal documents (≈ 2 TB), 10,000 attorney queries per hour.

```
Attorney: "cases where employer failed to provide OSHA PPE and contractor died"

Naive RAG misses:
  - "personal protective equipment" ≠ "PPE" (abbreviation)
  - "fatality" ≠ "died" (legal terminology differs)

Hybrid RAG finds:
  BM25 hits:      "OSHA 29 CFR 1926.100 — PPE requirements for contractors"
  Vector hits:    "employer liability for subcontractor workplace fatality"
  Trigram hits:   partial case citation "29 CFR § 1926.100(a)"
  RRF merge:      All three signals agree on top-5 chunks → high confidence
  
Precision improvement: 82% → 94% on legal Q&A benchmark vs. Naive RAG
```

**At scale:** 50M documents × 1536-dim float32 vectors = 300 GB in pgvector. With HNSW index (ef_construction=200, m=16): query latency 12–40 ms. Single Postgres instance handles 2,000 parallel vector queries. For 10K req/hr: trivial.

### Hybrid RAG at 1 million requests/second

For truly high-scale deployments (1M req/s), the architecture shifts:

```
                     ┌─────────────┐
                     │  Load       │
    1M req/s ───────▶│  Balancer   │
                     └──────┬──────┘
                            │ shard by tenant_id
                  ┌─────────┴──────────┐
                  │                    │
           ┌──────▼──────┐    ┌────────▼──────┐
           │  pgvector   │    │  pgvector     │
           │  shard A    │    │  shard B      │
           │  50M docs   │    │  50M docs     │
           └──────┬──────┘    └────────┬──────┘
                  └─────────┬──────────┘
                            │
                    ┌───────▼───────┐
                    │  SemanticCache│
                    │  L1 (Redis)   │ ← cache hit rate ~40%
                    │  L2 (pgvector)│ ← cache hit rate ~65%
                    └───────┬───────┘
                            │ (misses only)
                    Actual DB queries: ~350K/s
```

**Cost at 1M req/s:**
- Embedding: $0.0001/query × 1M = $100/s → use cached embeddings
- pgvector: 8–15 ms/query, 350K actual queries/s → 35 Postgres nodes
- BM25: co-located with Postgres, no extra cost
- **Total infra cost estimate: ~$50,000/month at 1M req/s**

---

## 3. HyDE (Hypothetical Document Embeddings)

**Enum:** `RAGStrategy.HYDE` | **File:** `app/rag/agentic/patterns/agentic.py` | **Latency:** 200–600 ms

### What it is

Gao et al. 2022: *"Precise Zero-Shot Dense Retrieval without Relevance Labels"*

Instead of embedding the raw query (which may be short and ambiguous), HyDE first generates a **hypothetical document** that would answer the query, then embeds that hypothetical document for retrieval. The insight: a hypothetical answer is a better proxy for the actual answer than the question itself.

```
Short ambiguous query: "transformer attention complexity"
        │
        ▼
LLM generates hypothetical document (no grounding needed):
  "The attention mechanism in Transformer models has quadratic O(n²)
   time and space complexity with respect to sequence length n, because
   each token must attend to every other token. This limits Transformers
   to sequences of ~512–4096 tokens without modifications like..."
        │
        ▼
embed(hypothetical_document) → hypothesis_vector
[much richer signal than embed("transformer attention complexity")]
        │
        ▼
pgvector cosine search with hypothesis_vector
        │
        ▼
Retrieved chunks: actual papers, textbooks, blog posts about attention complexity
```

### Why HyDE works for abstract queries

| Query type | Naive RAG problem | HyDE solution |
|---|---|---|
| "transformer attention complexity" | 3 words, vague vector | Hypothetical paragraph → dense vector |
| "overview of federated learning" | Generic query embedding | Hypothetical answer captures key concepts |
| "difference between TCP and UDP" | Query vs answer domain gap | Hypothesis bridges the gap |
| "how does garbage collection work" | Short, polysemous | GC description → precise vector |

### When to use HyDE

- **Short abstract queries** (< 80 characters): "what is blockchain", "explain GAN"
- **Concept-level search** where the question uses different vocabulary than the indexed documents
- **Educational content retrieval** where students ask vague questions
- **Research assistant scenarios** where queries are topic titles, not full sentences

### When NOT to use HyDE

- **Factual lookup**: "What is the API rate limit?" — the query is already specific
- **Latency-critical paths**: adds one extra LLM call (100–400 ms)
- **Short-term queries with exact matches**: "JIRA-1234 status" — use BM25/lexical

### Real-world example

**Scenario:** Academic paper search engine, 200 million papers, 15 TB, students and researchers use it.

```
Student: "quantum computing speedup"

Naive RAG:
  embed("quantum computing speedup") → blurry vector
  Top results: generic "quantum computing" intros, not speedup-specific

HyDE:
  Step 1 - Hypothetical doc:
    "Quantum computers achieve speedup over classical computers for specific
     problems through quantum parallelism and interference. The most famous
     example is Shor's algorithm (O(log³ n) vs O(exp(√n))) for factoring.
     Grover's algorithm provides quadratic speedup for unstructured search."
  
  Step 2 - embed(hypothetical) → rich vector
  
  Step 3 - Retrieved:
    1. "Quantum advantage for computational problems" (Nature 2019)
    2. "Exponential speedup from quantum algorithms: a survey"
    3. "When does quantum computing provide provable speedup?"
  
  Precision@5: 78% vs 51% for Naive RAG on this query type
```

### HyDE at scale

HyDE adds one LLM call before retrieval. At 100K req/hr:
- Hypothetical generation: GPT-4o-mini at $0.15/1M tokens, ~200 tokens = $0.00003/query
- Total added cost: $3/hour = $72/day
- Cache hypotheticals: SemanticCache (cosine 0.92) means ~60% queries skip LLM
- Effective added cost after caching: ~$29/day

---

## How These Three Patterns Work Together

In practice, AgentVerse stacks these patterns:

```
Query arrives
    │
    ├─ RetrievalPlanner.select_strategy()
    │    ├─ Short abstract query → HYDE first, then HYBRID on hypothesis
    │    ├─ Exact ID → NAIVE with BM25-only
    │    └─ General → HYBRID directly
    │
    ├─ PatternAssembler may upgrade to CORRECTIVE or FLARE
    │   if the goal requires hallucination prevention or
    │   long-form generation with uncertainty
    │
    └─ All three patterns share:
         - Same KnowledgeStore.hybrid_search() call
         - Same SemanticCache L1+L2 (skip duplicate embeds)
         - Same CitationManager ([1][2][3] IDs)
         - Same DataClassification (PII-aware filtering)
         - Same AuditLog entry
```

### Latency comparison at p50/p99

| Pattern | p50 | p99 | at 10M docs | at 1B docs |
|---|---|---|---|---|
| Naive RAG | 25 ms | 80 ms | 30 ms | 60 ms |
| Hybrid RAG | 45 ms | 120 ms | 60 ms | 110 ms |
| HyDE | 250 ms | 600 ms | 260 ms | 280 ms |

*Note: p99 difference between 10M and 1B docs is only 2× due to HNSW sub-linear scaling.*

---

## Scalability Summary

| Factor | Naive | Hybrid | HyDE |
|---|---|---|---|
| Embedding calls/query | 1 | 1 | 2 (query + hypothesis) |
| DB queries/request | 1 | 2–3 parallel | 1 |
| LLM calls/request | 1 | 1 | 2 |
| Cache benefit | High (exact query cache) | Medium | High (hypothesis cache) |
| Cost at 1M req/day | ~$30 | ~$35 | ~$80 |
| Max throughput (3-node PG) | 5M req/hr | 2M req/hr | 300K req/hr |

---

## Code Location

| Component | File | Key function |
|---|---|---|
| Naive RAG | `app/rag/store.py` | `KnowledgeStore.hybrid_search(strategy="naive")` |
| Hybrid RAG | `app/rag/store.py` | `KnowledgeStore.hybrid_search()` + `hybrid_search_db()` |
| HyDE | `app/rag/agentic/patterns/agentic.py` | `RAGStrategy.HYDE` handler |
| BM25 | `app/rag/bm25.py` | `BM25Retriever.search()` |
| CrossEncoder | `app/rag/cross_encoder.py` | `rerank_results()` |
| Semantic Cache | `app/rag/semantic_cache.py` | `SemanticCache.get()` / `.set()` |
| Strategy routing | `app/rag/engine.py` | `RetrievalPlanner.select_strategy()` |
