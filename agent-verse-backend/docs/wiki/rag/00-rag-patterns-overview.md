# RAG Patterns — Complete Reference

AgentVerse implements **18 Retrieval-Augmented Generation (RAG) strategies**, ranging from millisecond-latency simple lookups to multi-minute deep research pipelines. Every strategy is a first-class module in `app/rag/agentic/patterns/`, backed by a unified `KnowledgeStore` (PostgreSQL + pgvector + BM25 + pg_trgm) and selected by the `PatternAssembler` or the `RetrievalPlanner` at runtime.

---

## Strategy Inventory

| # | Strategy | Enum | Latency | Scale | Primary Use Case |
|---|---|---|---|---|---|
| 1 | **Naive RAG** | `NAIVE` | 20–80 ms | ✅ Billions of queries | Simple Q&A, FAQ, lookup |
| 2 | **Hybrid RAG** | `HYBRID` | 30–120 ms | ✅ Billions | General-purpose, best baseline |
| 3 | **HyDE** | `HYDE` | 200–600 ms | ✅ Billions | Abstract queries, concept search |
| 4 | **Multi-Hop RAG** | `MULTI_HOP` | 400 ms–3 s | ✅ Millions | Comparative, cross-document analysis |
| 5 | **Graph RAG** | `GRAPH` | 100–500 ms | ⚠️ Tens of millions | Entity-relationship, knowledge graph |
| 6 | **Corrective RAG** | `CORRECTIVE` | 500 ms–4 s | ✅ Hundreds of millions | Hallucination prevention, medical/legal |
| 7 | **FLARE** | `FLARE` | 1–8 s | ✅ Millions | Long-form generation, uncertainty-driven |
| 8 | **RAPTOR** | `RAPTOR` | 2–20 s | ⚠️ Tens of millions | Long documents, hierarchical summarization |
| 9 | **Fusion RAG** | `FUSION` | 600 ms–5 s | ✅ Hundreds of millions | High-recall, multi-perspective |
| 10 | **Self-RAG** | `SELF_RAG` | 1–6 s | ✅ Millions | On-demand retrieval, critique-aware |
| 11 | **Agentic RAG** | `AGENTIC` | 2–30 s | ✅ Millions | Complex research, multi-tool |
| 12 | **Agentic Chunking** | `AGENTIC_CHUNKING` | 3–15 s (index time) | ✅ Millions | Dense proposition extraction |
| 13 | **Web-Augmented RAG** | `WEB_AUGMENTED` | 1–5 s | ✅ Millions | Real-time knowledge, current events |
| 14 | **Adaptive RAG** | `ADAPTIVE` | 50 ms–5 s | ✅ Billions | Auto-select best strategy per query |
| 15 | **Speculative RAG** | `SPECULATIVE` | 800 ms–4 s | ✅ Hundreds of millions | Parallel candidate + verification |
| 16 | **Modular RAG** | `MODULAR` | 200 ms–10 s | ✅ Millions | Composable pipeline with custom modules |
| 17 | **ColBERT** | `COLBERT` | 50–300 ms | ⚠️ Tens of millions | Token-level late interaction, domain search |
| 18 | **RAFT** | `RAFT` | 20–100 ms | ✅ Billions | Fine-tuned for domain (post-training) |

---

## Architecture: How All 18 Patterns Share the Same Foundation

Every RAG pattern in AgentVerse operates on the same infrastructure stack — only the **retrieval algorithm** and **generation loop** differ.

```
                    QUERY / GOAL TEXT
                           │
                           ▼
           ┌───────────────────────────────┐
           │  PatternAssembler (Phase 1)    │
           │  19 rules → rag_patterns list  │
           │  e.g. ["hybrid_rag", "flare"]  │
           └───────────────────────────────┘
                           │
           ┌───────────────────────────────┐
           │  RetrievalPlanner.select()     │
           │  heuristic: lexical / multi-   │
           │  hop / hyde / direct           │
           └───────────────────────────────┘
                           │
                    STRATEGY DISPATCH
              ┌────────────┼──────────────┐
              │            │              │
         CORE ENGINE   PATTERN ADAPTER  WEB/GRAPH
         (engine.py)   (patterns/*.py)  EXTENSION
              │
    ┌─────────────────────────────────────┐
    │         KNOWLEDGE STORE             │
    │  PostgreSQL + pgvector (HNSW)       │
    │  pg_trgm (trigram full-text)        │
    │  BM25 (keyword ranking)             │
    │  Reciprocal Rank Fusion             │
    │  CrossEncoder re-ranking            │
    └─────────────────────────────────────┘
              │
    ┌─────────────────────────────────────┐
    │    EMBEDDING ORCHESTRATOR           │
    │  EmbeddingPolicySelector:           │
    │    free / standard / premium tier   │
    │  Providers: OpenAI, Voyage, Gemini  │
    │  embed_with_fallback()              │
    └─────────────────────────────────────┘
              │
    ┌─────────────────────────────────────┐
    │    CROSS-CUTTING SYSTEMS            │
    │  SemanticCache L1+L2 (skip LLM)     │
    │  GuardrailsV2 (content safety)      │
    │  CitationManager ([1][2][3])        │
    │  ProvenanceLedger (audit lineage)   │
    │  CostController (budget guard)      │
    │  DataClassification (PII handling)  │
    └─────────────────────────────────────┘
```

---

## Hybrid Search: The Foundation of Every Pattern

Before any RAG pattern executes, queries pass through the **hybrid search pipeline** implemented in `app/rag/store.py`:

```
query_text
    │
    ├─ EmbeddingOrchestrator.embed(query) → query_vector [float32 × 1536]
    │
    ├─ pgvector HNSW cosine search
    │    SELECT * FROM chunks ORDER BY embedding <=> $query_vector LIMIT 100
    │    Latency: 2–15 ms at 100M chunks (HNSW ef=200)
    │
    ├─ BM25 keyword ranking (app/rag/bm25.py)
    │    TF-IDF weighted exact + stem match
    │    Latency: 1–5 ms
    │
    ├─ pg_trgm trigram similarity (PostgreSQL built-in)
    │    Good for partial matches, typos, code identifiers
    │    Latency: 3–20 ms
    │
    ├─ Reciprocal Rank Fusion (RRF)
    │    score = Σ 1/(k + rank_i)   k=60 (standard)
    │    Merges pgvector + BM25 + trigram ranked lists
    │
    └─ CrossEncoder re-ranking (app/rag/cross_encoder.py)
         LLM scores top-20 (query, passage) pairs for relevance
         Promotes semantically correct results over keyword matches
         Latency: 50–200 ms (optional, skip for latency-sensitive paths)

Final blend: score = 0.7 × (cosine + trigram) + 0.3 × normalized_BM25
```

**At 1 TB of documents (≈500M chunks at 2KB avg):**
- pgvector HNSW: 8–25 ms (sub-linear with HNSW index)
- BM25: 5–15 ms (inverted index)
- RRF merge: < 1 ms (in-memory)
- CrossEncoder: 50–200 ms (optional)
- **Total hybrid search: 15–250 ms at petabyte scale**

---

## Pattern Selection Decision Tree

```
New query arrives
        │
        ├─ Lexical ID? (e.g. JIRA-123, PR-456)
        │    └─ lexical strategy (BM25-only, no vector)
        │
        ├─ Comparative keywords? (compare, analyze, contrast)
        │    └─ MULTI_HOP
        │
        ├─ Abstract concept? (what is, explain, how does) + len < 80
        │    └─ HYDE
        │
        ├─ PatternAssembler override from GoalProperties?
        │    ├─ complexity=HARD + domain=RESEARCH → RAPTOR or AGENTIC
        │    ├─ risk=HIGH (medical/legal) → CORRECTIVE
        │    ├─ realtime data needed → WEB_AUGMENTED
        │    ├─ knowledge graph wired → GRAPH
        │    └─ long-form generation → FLARE
        │
        └─ Default: HYBRID (best generalist)
```

---

## Pages in This Section

| Page | Content |
|---|---|
| [01-naive-and-hybrid-rag.md](./01-naive-and-hybrid-rag.md) | Naive RAG, Hybrid RAG, HyDE — the three foundational patterns with RWEs: SaaS support chatbot (500K/day), legal research platform (50M docs), academic paper search (200M papers) |
| [02-multi-hop-and-graph-rag.md](./02-multi-hop-and-graph-rag.md) | Multi-Hop RAG, Graph RAG — cross-document reasoning with RWEs: PE due diligence, healthcare network analysis, supply chain risk, pharma drug interaction graph, financial fraud detection |
| [03-corrective-and-multi-hop.md](./03-corrective-and-multi-hop.md) | Corrective RAG — hallucination prevention with grading; RWE: pharma regulatory (500K FDA docs), multi-jurisdiction compliance |
| [04-flare-and-raptor.md](./04-flare-and-raptor.md) | FLARE, RAPTOR, Self-RAG — active retrieval, hierarchical summarization, critique tokens; RWEs: medical AI discharge summaries (500K/day), investment research (50TB), legal AI assistant |
| [06-agentic-patterns.md](./06-agentic-patterns.md) | Fusion, Speculative, Agentic, Agentic Chunking, Web-Augmented; RWEs: patent search (10M patents), e-commerce at 500M queries/day, Wikipedia-scale proposition indexing, financial news intelligence |
| [07-advanced-patterns.md](./07-advanced-patterns.md) | Adaptive, Modular, ColBERT, RAFT; RWEs: Big 4 consulting ($3.5M/year savings), regulatory compliance pipeline, GitHub code search, biomedical search, insurance claims (96% cost reduction) |
| [08-at-scale.md](./08-at-scale.md) | Scalability at 1M req/s, 1TB+ documents, latency budgets, caching ($234K/month savings) |
| [09-integration-guide.md](./09-integration-guide.md) | Embeddings, chunking (7 strategies), guardrails, governance working together; Goldman Sachs 10-K compliance example |

---

## Related

- [Ingestion Pipeline](../ingestion-pipeline.md) — how documents become chunks become vectors
- [Embedding System](../embedding-system.md) — model selection, cost tiers, fallback
- [Agent Patterns](../agent-patterns/00-pattern-selection-and-dispatch.md) — how RAG patterns are selected per goal

---

## Real-World Examples

**Real-World Example 1 — E-Commerce Product Assistant (Pattern Selection by Query Type)**

> A fashion e-commerce platform's product assistant routes query types to different RAG patterns automatically via the `PatternAssembler`. Simple catalog lookups ("Is the Adidas Ultraboost available in size 10, black?") hit **Naive RAG** at 35ms against 4 million product SKUs. Complex comparison queries ("Compare the 5 best running shoes under $150 for overpronation") trigger **Fusion RAG**: 5 parallel retrieval runs generate diverse candidate sets, RRF merges them, and the LLM produces a cited comparison in 2.1 seconds. Archival policy questions ("What was the return policy for holiday 2021 purchases?") route to **Self-RAG with CRAG fallback**: the agent detects that the initially retrieved 2021 policy chunk has a low grading score (the policy was superseded), triggers a corrective hop to archived policy documents, and returns the historically correct answer with a "historical policy" disclaimer — preventing a customer service escalation.

**Real-World Example 2 — Hospital Clinical Decision Support (Query Type → RAG Pattern Mapping)**

> A hospital system's clinical decision support tool routes queries based on `PatternAssembler` signals. Objective reference queries ("What is the normal range for serum creatinine in adults?") use **Hybrid RAG** at 80ms against 50,000 clinical reference articles — fast, grounded, no LLM-invented values for objective thresholds. Multi-step drug interaction queries ("Can a warfarin patient take ibuprofen alongside metformin?") trigger **Multi-hop RAG**: three sequential retrieval hops — (1) warfarin + NSAID interactions, (2) ibuprofen + anticoagulation risk, (3) NSAID + metformin renal contraindication — synthesised into one answer with 7 source citations in 3.4 seconds. Rare disease queries with sparse internal documentation ("Management options for paraneoplastic cerebellar degeneration?") activate **Web-Augmented RAG**, pulling current guidelines from PubMed and labelling external provenance in the audit log so clinicians know which content came from outside the hospital's vetted corpus.
