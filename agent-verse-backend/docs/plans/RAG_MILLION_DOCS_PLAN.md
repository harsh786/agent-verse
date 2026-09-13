# RAG at Scale — Million(+)-Document Retrieval Plan

Status: PLAN · Branch: `feat/agent-memory-governance` · TDD, no stubs.

## Goal
Make retrieval correct, fast (p95 < ~300 ms for top-k), and cost-bounded at
**10M+ chunks per tenant** across many tenants, while keeping grounding intact
(retrieval quality is necessary but hallucination is caught by the grounding stack,
not by RAG alone).

## Current state (verified)
- Per-dimension chunk tables `knowledge_chunks_{768,1024,1536,2048,3072}` with HNSW
  (halfvec for 2048/3072), FTS (`to_tsvector`), trigram (`gin_trgm_ops`), jsonb
  metadata GIN. RLS-per-tenant on every query.
- Hybrid 4-leg (vector + FTS + trigram + BM25) fused by RRF; two-stage **binary
  prefilter** (`binary_quantize()::bit` Hamming shortlist → exact cosine rerank),
  opt-in via `rag_binary_prefilter_enabled` (migration 0120 index).
- **Gap:** chunk tables are NOT partitioned; HNSW `ef_search`/build params are static;
  binary prefilter is opt-in not default; no ingestion-throughput or ANN-recall harness.

## Design
1. **Partitioning** — `PARTITION BY LIST (tenant_id)` (or hash) on each
   `knowledge_chunks_*` table, with per-collection sub-partitioning for the largest
   tenants. Keeps HNSW graphs bounded per partition (smaller graphs = faster, higher
   recall) and lets big tenants be moved/rebalanced. Migration adds partitioned
   parents + attaches existing rows; RLS predicate unchanged.
2. **ANN tuning + defaults** — expose `hnsw.ef_search` per query (raise for
   high-precision goals), tune `m`/`ef_construction` per dimension; make the **binary
   two-stage prefilter the default** for collections above a size threshold
   (shortlist 200 → exact rerank top-k), falling back to direct HNSW for small ones.
3. **Recall/latency harness** — an offline eval (`tests/rag/test_ann_recall.py`,
   slow) that builds N synthetic chunks, measures recall@k vs exact brute force and
   p95 latency, and asserts recall ≥ target (e.g. 0.95) at the configured params —
   so tuning is regression-guarded.
4. **Ingestion throughput** — batched COPY-based chunk insert + deferred index
   maintenance (`CREATE INDEX CONCURRENTLY` already used); background
   embed→upsert pipeline with backpressure; per-tenant ingestion lease (exists in
   `app/rag/*ingestion*`) extended for parallel workers.
5. **Tiered recall for huge collections** — optional coarse cluster/centroid
   pre-routing (IVF-style) so a query only scans relevant partitions/clusters; keep
   HNSW within cluster. Off by default; enabled above a size threshold.
6. **Cost/latency guards** — cap candidate pool, short-circuit legs when the
   vector leg is already high-confidence, and cache embeddings (see Memory plan's
   embedding cache) to avoid re-embedding identical queries.

## TDD task list
- T1 `tests/rag/test_chunk_partitioning.py` (integration) — migration creates
  partitioned tables; inserts route to the right partition; RLS still isolates;
  cross-tenant invisible. → new migration + `store.py` table resolver.
- T2 `tests/rag/test_ann_recall.py` (slow) — recall@10 ≥ 0.95 vs brute force at
  default params; p95 latency budget asserted. → tuning + harness.
- T3 `tests/rag/test_binary_prefilter_default.py` (integration) — prefilter auto-on
  above threshold; identical top-k to direct HNSW within tolerance. → `store.py` default flip.
- T4 `tests/rag/test_ingestion_throughput.py` (slow) — N-chunk batched ingest
  under a time budget; index built; searchable. → COPY path + deferred index.
- T5 `tests/rag/test_ef_search_per_query.py` — high-precision goal raises ef_search;
  low-latency goal lowers it. → query param plumbing from runtime profile.

## Acceptance
- 10M-chunk synthetic tenant: recall@10 ≥ 0.95, p95 < target, no cross-tenant leakage.
- Partition pruning verified in `EXPLAIN` (only relevant partition scanned).
- Ingestion meets throughput budget; existing hybrid/grounding tests unaffected.

## Honest note
"Not hallucinating" is delivered by the grounding stack (see the hallucination
plan), not by retrieval. This plan makes retrieval *scale and stay accurate*;
the two compose — better recall gives grounding more true evidence to match against.
