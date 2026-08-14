# RAG at Scale: 1M+ Requests/Second, Terabytes of Documents

This page covers the engineering reality of running RAG in production at enterprise scale: latency budgets, infrastructure sizing, caching strategies, cost optimization, and how the AgentVerse system behaves under extreme load.

---

## Scale Dimensions

RAG systems face two independent scaling challenges:

| Dimension | Challenge | AgentVerse solution |
|---|---|---|
| **Query throughput** | 1M+ requests/second during peak | Horizontal sharding + L1/L2 cache |
| **Corpus size** | 1TB+ documents, hundreds of millions of chunks | HNSW index + partitioned pgvector |
| **Concurrent LLM calls** | Thousands of parallel generation requests | Per-tenant bulkhead + rate limiting |
| **Embedding generation** | Real-time embedding for every query | Embedding cache + batch embedding |
| **Multi-tenant isolation** | Different orgs on same infra | Row-Level Security + connection pools |

---

## Infrastructure Architecture at 1M Requests/Day

```
                    ┌──────────────────────────────┐
    Clients         │    API Gateway               │
    (web/SDK/MCP)   │    Rate limiter              │
    ─────────────▶  │    Concurrent goal counter   │
                    │    Goal deduplication         │
                    └─────────────┬────────────────┘
                                  │ shard by tenant_id hash
                          ┌───────┼───────┐
                          │       │       │
                    ┌─────▼──┐ ┌──▼───┐ ┌▼──────┐
                    │Celery  │ │Celery│ │Celery │
                    │Worker 1│ │Work 2│ │Work 3 │  ← per-tenant queue routing
                    └─────┬──┘ └──┬───┘ └┬──────┘
                          │       │      │
              ┌───────────▼───────▼──────▼──────────┐
              │         RAG Gateway (app/rag/gateway.py)│
              │  RAGCostController: budget per query   │
              │  CollectionAuthorizer: tenant scope    │
              │  _BudgetedEmbedder: cost-aware embed   │
              └─────────────┬──────────────────────────┘
                            │
              ┌─────────────▼──────────────────────┐
              │         Semantic Cache              │
              │  L1: Redis in-memory LRU           │
              │      256 entries/tenant, TTL 300s  │
              │      cosine threshold: 0.92        │
              │  L2: pgvector persistent            │
              │      brotli-compressed responses   │
              │  Hit rate: L1 ~40%, L2 cumulative ~65% │
              └─────────────┬──────────────────────┘
                            │ (35% of queries reach DB)
              ┌─────────────▼──────────────────────┐
              │    PostgreSQL + pgvector Cluster    │
              │                                    │
              │  Primary (writes + HNSW index)     │
              │  Replica 1 (vector reads)          │
              │  Replica 2 (BM25 + FTS reads)      │
              │  PgBouncer (connection pooling)    │
              │  max_connections: 200/instance     │
              └─────────────┬──────────────────────┘
                            │
              ┌─────────────▼──────────────────────┐
              │         LLM Providers               │
              │  AI Router selects:                 │
              │    OpenAI / Anthropic / Voyage       │
              │  Per-tenant routing policies        │
              │  Circuit breaker per provider       │
              └────────────────────────────────────┘
```

---

## Corpus Size vs. Query Latency

### HNSW index behavior at scale

pgvector uses HNSW (Hierarchical Navigable Small World) indexing:

```
Documents       Chunks      Vector storage   HNSW query p50
─────────────────────────────────────────────────────────
100K docs       5M chunks   38 GB           3–8 ms
1M docs         50M chunks  380 GB          5–15 ms
10M docs        500M chunks 3.8 TB          8–25 ms
100M docs       5B chunks   38 TB           15–50 ms
```

**Key insight:** HNSW is sub-linear — going from 5M to 5B chunks (1000×) only increases latency by ~5× due to the graph-navigated approximate nearest neighbor search.

**HNSW parameters in AgentVerse:**
```sql
CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 200);
-- At query time: SET hnsw.ef_search = 100;
-- Higher ef_search = better recall, higher latency
-- ef_search=100: 95% recall at 15ms
-- ef_search=200: 98% recall at 30ms
```

### Corpus sharding strategy

For corpora > 500M chunks:

```
Shard by: tenant_id % num_shards (tenant isolation)
           OR
           content_hash % num_shards (even distribution)

At 5B chunks across 10 pgvector shards:
- Per shard: 500M chunks = 3.8 TB
- Query: fan-out to all shards in parallel, merge results
- Latency: max(shard_latency) + merge ~= 20–45 ms
- With tenant sharding: tenant always hits 1 shard = 15–30 ms
```

---

## The Caching Layer: Your Most Important Scaling Tool

### Layer 1: Redis in-memory cache (SemanticCache)

```python
# app/rag/semantic_cache.py
class SemanticCache:
    # L1: in-memory LRU per tenant
    max_size: int = 256  # entries per tenant
    ttl: float = 300.0   # 5 minutes
    threshold: float = 0.92  # cosine similarity to count as "same query"
```

**What gets cached:** Full RAG responses (retrieved chunks + generated answer).

**Cache key:** Embedding of the query. If a new query has cosine similarity ≥ 0.92 with a cached query, return the cached response.

**Hit scenarios:**
- "What is the refund policy?" and "Explain your refund policy" → cache HIT (cosine ~0.94)
- "JIRA-1234 status" and "What is the status of JIRA-1234?" → cache HIT

**Miss scenarios:**
- "What is the refund policy for international orders?" vs "domestic refunds" → MISS (cosine ~0.78)

### Layer 2: pgvector persistent cache

```sql
-- Stored in cache_entries table (RLS-scoped by tenant_id)
-- Entries: embedding, compressed_response, created_at, hit_count
-- Same 0.92 threshold but persistent across Redis restarts
-- brotli compression: ~85% size reduction on text responses
```

### Impact of caching on infrastructure costs

```
Without cache:
  1M queries/day × $0.002 embedding + $0.01 generation = $12,000/day

With L1+L2 cache (65% hit rate):
  350K actual queries/day × $0.012 = $4,200/day
  Savings: $7,800/day = $234,000/month

Postgres load:
  Without: 11.6 queries/second (1M/day)
  With:    4.1 queries/second (350K/day)
  → 3-node cluster handles this comfortably
```

---

## Throughput Limits by RAG Pattern

**Key constraint:** Every LLM call is ~200–2000ms. The pattern determines how many LLM calls happen.

```
Pattern          LLM calls/query  Max throughput  Cost/1M queries
────────────────────────────────────────────────────────────────
Naive RAG              1           50M req/day      $12,000
Hybrid RAG             1           40M req/day      $12,000
HyDE                   2           20M req/day      $20,000
Corrective RAG       1–3           10M req/day      $30,000
Multi-Hop (2-hop)    2–3           8M req/day       $25,000
FLARE                2–4           5M req/day       $40,000
Self-RAG             3–5           3M req/day       $50,000
Agentic RAG          3–10          1M req/day      $100,000
RAPTOR (index)        N/A          (index cost)
RAPTOR (query)         1          40M req/day      $12,000*

* RAPTOR query is same as Hybrid RAG; index cost is one-time
```

---

## Multi-Tenant Isolation at Scale

AgentVerse enforces tenant isolation at four layers:

### Layer 1: Row-Level Security in PostgreSQL

```sql
-- Every vector search is tenant-scoped
CREATE POLICY tenant_isolation ON chunks
  USING (tenant_id = current_setting('app.tenant_id')::uuid);

-- Query automatically filtered:
SELECT * FROM chunks 
ORDER BY embedding <=> $query_vec 
LIMIT 20;
-- RLS ensures only tenant_id-matching rows are returned
```

### Layer 2: Per-Tenant Bulkhead

```python
# app/reliability/ — per-tenant concurrency limits
# Tenant on Free plan: max 5 concurrent RAG queries
# Tenant on Enterprise: max 500 concurrent RAG queries
```

### Layer 3: Per-Tenant Rate Limiting (Redis sliding window)

```python
# app/tenancy/ — sliding window rate limiter
# Free: 100 queries/minute
# Pro: 1,000 queries/minute  
# Enterprise: 10,000 queries/minute (custom)
```

### Layer 4: Embedding Namespace Isolation

```python
# app/rag/gateway.py — CollectionAuthorizer
# Collections are namespaced: "tenant_{uuid}_{collection_name}"
# Vector searches cannot cross collection boundaries
```

---

## Latency Budget: Where Time Goes

For a typical Hybrid RAG query:

```
Phase                              p50    p99    Notes
─────────────────────────────────────────────────────
Rate limiting check                 1ms    5ms   Redis O(1)
Goal deduplication check            1ms    5ms   Redis
Semantic cache L1 lookup           <1ms    2ms   In-memory hash
Semantic cache L2 lookup            5ms   20ms   pgvector cosine
Query embedding                    15ms   50ms   OpenAI text-embedding-3-small
pgvector HNSW search               10ms   35ms   50M chunks
BM25 retrieval                      5ms   15ms   In-memory BM25
RRF merge                           1ms    3ms   Pure Python
CrossEncoder rerank (optional)     80ms  300ms   GPT-4o-mini
LLM generation                    200ms  800ms   GPT-4o-mini (200 tokens)
CitationManager.register           <1ms    2ms   
AuditLog.record                     2ms   10ms   Async PostgreSQL write
──────────────────────────────────────────────────
TOTAL (no cache, no rerank)        ~250ms  ~950ms
TOTAL (no cache, with rerank)      ~330ms ~1250ms
TOTAL (L1 cache hit)               ~5ms   ~30ms
TOTAL (L2 cache hit)               ~25ms  ~80ms
```

---

## Cost Optimization Decision Tree

```
Query arrives
      │
      ├─ L1 cache hit? → return immediately (< 5ms, $0)
      │
      ├─ L2 cache hit? → return (< 30ms, embedding cost only ~$0.0001)
      │
      ├─ Is this a simple factual lookup?
      │    └─ Use Naive RAG (1 embed + 1 LLM call) = $0.012
      │
      ├─ Is this from a free-tier tenant?
      │    └─ Use Hybrid RAG, no CrossEncoder, GPT-4o-mini = $0.008
      │
      ├─ Is this latency-critical (< 200ms SLA)?
      │    └─ Use Hybrid RAG, skip CrossEncoder rerank = $0.01
      │
      ├─ Is this a complex research query?
      │    ├─ PatternAssembler: complexity=HARD → RAPTOR or AGENTIC
      │    └─ Budget from enterprise plan allows higher cost per query
      │
      └─ Default: Hybrid RAG with CrossEncoder = $0.015
```

---

## Embedding Cost Optimization

Embedding generation is ~15% of total RAG cost. AgentVerse optimizes via:

### 1. EmbeddingPolicySelector cost tiers

```python
# app/embedding/orchestrator.py
# free tier: sentence-transformers (local, $0)
# standard: text-embedding-3-small ($0.02/1M tokens)
# premium: text-embedding-3-large ($0.13/1M tokens)

# Match model to query complexity:
# Simple FAQ → free tier ($0)
# Standard search → standard ($0.02/1M)
# Expert domain search → premium ($0.13/1M)
```

### 2. Query embedding cache

```python
# Before embedding, check if this exact query was embedded recently
cache_key = hashlib.sha256(query.encode()).hexdigest()
cached_embedding = redis.get(f"embed:{cache_key}")
if cached_embedding:
    return unpack_embedding(cached_embedding)  # skip API call
```

### 3. Batch embedding at index time

```python
# app/embedding/orchestrator.py
async def embed_batch(texts: list[str]) -> list[list[float]]:
    # OpenAI supports 2048 texts per API call
    # Reduces API call overhead by 2000×
    # At index time: 100K chunks → 50 API calls vs 100K
```

---

## Failure Modes and Resilience

### Circuit breaker per LLM provider

```
Provider OpenAI state: CLOSED (healthy)
                        │
         5 failures in 60s │
                        ▼
                    OPEN (reject all calls)
                        │
         30s timeout      │
                        ▼
                    HALF-OPEN (allow 1 probe)
                        │
         probe succeeds   │
                        ▼
                    CLOSED (healthy again)
```

When OpenAI circuit opens:
1. Try Anthropic (fallback provider)
2. Try cached responses (SemanticCache L2)
3. Return degraded response with `confidence: low` flag
4. Never return 500 — always return a response

### Corpus search failure resilience

```
pgvector unavailable:
  1. Try replica (Replica 1 → Replica 2)
  2. Fall back to BM25-only (keyword search)
  3. Last resort: return empty context + LLM uses internal knowledge
     (flagged: "answer may be incomplete, retrieval unavailable")
```

---

## Monitoring Targets

| Metric | Alert threshold | Critical threshold |
|---|---|---|
| RAG query p99 latency | > 2s | > 5s |
| Cache hit rate | < 35% | < 20% |
| pgvector query p99 | > 100ms | > 300ms |
| Embedding API error rate | > 1% | > 5% |
| LLM provider error rate | > 0.5% | > 2% |
| Cost per query (moving avg) | > $0.05 | > $0.20 |
| Cross-tenant data leak alerts | any | any (instant page) |

All metrics emitted via OTel to `app/observability/rag_trace.py`.

---

## Real-World Scale Reference Points

| Company type | Daily queries | Corpus size | Recommended config |
|---|---|---|---|
| SaaS startup | 10K/day | 10K docs (50M chunks) | Single pgvector, Hybrid RAG, GPT-4o-mini |
| Mid-market enterprise | 500K/day | 500K docs (2.5B chunks) | 3-node PG, L1+L2 cache, adaptive strategy |
| Large enterprise | 5M/day | 5M docs (25B chunks) | 10-node PG cluster, corpus sharding, dedicated embed servers |
| Platform provider | 50M/day | Multi-tenant, 50B+ chunks | Multi-region PG, shard-by-tenant, dedicated Redis |
| Hyperscale | 1B/day | 100B+ chunks | Custom pgvector deployment, embedding CDN, aggressive caching |

**Real-World Example 3 — Financial News Terminal (2,000 Queries/Minute with Semantic Cache)**

> A financial data provider runs a news intelligence terminal used by 500 power-user analysts submitting approximately 2,000 queries per minute during peak market hours (09:30–11:00 EST). The system indexes 200 million news chunks from 3,000 wire services updated in real time. Semantic cache L1 (Redis LRU, 256 entries/tenant) achieves a 42% hit rate at peak — analysts frequently ask semantically equivalent questions like "What is the latest Fed statement on rates?" versus "Fed interest rate decision today?", returning cached results in under 5ms at zero retrieval cost. The remaining 58% (approximately 1,160 queries/minute) reach the HNSW index: with `ef_search=100` on the 200M-chunk corpus, pgvector delivers P99 latency of 28ms across a 3-node read-replica cluster. Total infrastructure cost at peak runs $0.0031/query ($372/hour), down from $0.0082/query ($984/hour) before semantic caching was enabled — a 62% cost reduction that recovered the Redis infrastructure cost in under 3 days.
