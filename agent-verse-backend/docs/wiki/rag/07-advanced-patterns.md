# Adaptive RAG, Modular RAG, ColBERT & RAFT

Four specialised patterns for different optimization goals: automatic strategy selection, composable pipelines, token-level precision retrieval, and domain-adapted fine-tuning.

---

## 1. Adaptive RAG — Automatic Strategy Selection

**Enum:** `RAGStrategy.ADAPTIVE` | **File:** `app/rag/agentic/patterns/adaptive.py` | **Latency:** 50 ms–5 s (depends on selected strategy)

### What it is

Adaptive RAG examines the query and automatically selects the optimal RAG strategy. It's a **meta-pattern** that wraps other patterns — you configure the available strategies and it picks the best one.

```python
# From app/rag/agentic/patterns/adaptive.py
class AdaptiveDecision:
    selected_strategy: RAGStrategy
    reason: str
    confidence: float
    fallback: RAGStrategy  # if selected strategy unavailable

class AdaptiveRAGPattern(RAGPattern):
    """Make exactly one non-recursive decision from certified capabilities."""
```

### Decision logic

```
Query analysis:
  ├─ Contains "compare" / "vs" / "difference" → FUSION (multi-perspective)
  ├─ Contains timestamp / "latest" / "today" → WEB_AUGMENTED
  ├─ Complex technical deep-dive → AGENTIC
  ├─ Short factual lookup (< 30 chars) → NAIVE
  ├─ Medium complexity, no special signals → HYBRID (default)
  ├─ Medical/legal/financial + risk=HIGH → CORRECTIVE
  └─ Long-form generation with uncertainty → FLARE

"Certified capabilities" check:
  Is GRAPH available for this tenant? (knowledge graph built?)
  Is WEB allowed? (policy check)
  Is RAFT trained for this domain? (model availability check)
  → Only select strategies that are actually available and configured
```

### When to use Adaptive RAG

| Scenario | Benefit |
|---|---|
| Multi-tenant SaaS (different query types) | Each tenant's queries get optimal strategy automatically |
| Developer API exposure | Users don't need to pick a strategy |
| A/B testing RAG strategies | Adaptive routes to different strategies based on query type |
| Default configuration for new deployments | Safe starting point before fine-tuning |

### Adaptive RAG at scale

Adaptive RAG adds one classification step:
- Heuristic classification (regex + keyword): < 1ms, ~80% accuracy
- LLM classification (for ambiguous queries): 100–300ms, ~95% accuracy
- Cache strategy selections: same query type → same strategy (Redis, TTL 1hr)

---

## 2. Modular RAG — Composable Pipeline Architecture

**Enum:** `RAGStrategy.MODULAR` | **File:** `app/rag/agentic/patterns/modular.py` + `app/rag/modular.py` | **Latency:** 200 ms–10 s

### What it is

Modular RAG treats the retrieval pipeline as a **directed graph of modules**, each with a defined interface. You compose custom pipelines by connecting modules: query transformers → retrievers → rerankers → context builders → generators.

```
Custom pipeline: Enterprise Legal Research

Query Transformer Module:
  LegalQueryExpander → adds statute numbers, legal synonyms
  
Retriever Modules (parallel):
  CaseCorpusRetriever → searches case law database
  StatuteRetriever    → searches statutory text
  RegulationRetriever → searches agency regulations

Merger Module:
  JurisdictionAwareRRF → ranks by jurisdiction relevance

Reranker Module:
  ColBERTReranker → token-level precision reranking

Context Builder Module:
  LegalCitationFormatter → formats Bluebook citations

Generator Module:
  LegalWritingLLM → trained for legal memo format
```

### Module types in AgentVerse

```python
# From app/rag/modular.py
class ModuleType(str, Enum):
    QUERY_TRANSFORMER = "query_transformer"    # rewrites query
    RETRIEVER = "retriever"                    # fetches documents
    RERANKER = "reranker"                      # reorders results
    CONTEXT_BUILDER = "context_builder"        # formats context
    GENERATOR = "generator"                    # LLM generation
    VALIDATOR = "validator"                    # quality check
    CACHE = "cache"                            # caching layer

class ModularRAGRuntimeAdapter(ModularRAGRuntimeContract):
    """Execute a safe default or validated per-agent Modular RAG pipeline."""
```

### When to use Modular RAG

- **Domain-specific pipelines** that need custom modules (legal, medical, code)
- **A/B testing pipeline components** (swap out reranker A for reranker B)
- **Compliance requirements** that mandate specific processing steps
- **Multi-source retrieval** with domain-specific merging logic
- **Enterprise deployments** where different business units need different pipelines

### Modular RAG example: Code Documentation Assistant

```
Module graph:
  [CodeQueryParser] → parse function names, class names, imports
         │
  ┌──────┴──────────┐
  │                 │
[CodeSearch]  [DocSearch]  ← parallel
  │                 │
  └──────┬──────────┘
         │
  [ContextWindowOptimizer] → prioritize relevant file sections
         │
  [SignatureExtractor] → extract function signatures
         │
  [CodeAwareLLM] → generates documentation

Result: Function documentation with accurate types, parameters, 
        examples from actual usage in the codebase
```

---

## 3. ColBERT — Token-Level Late Interaction Retrieval

**Enum:** `RAGStrategy.COLBERT` | **File:** `app/rag/agentic/patterns/colbert.py` | **Latency:** 50–300 ms

### What it is

ColBERT (Contextualized Late Interaction BERT) uses a fundamentally different retrieval architecture from standard dense retrieval. Instead of a single query vector vs. a single document vector, ColBERT computes **token-level embeddings** and uses late interaction to capture fine-grained matches.

```
Standard Dense Retrieval:
  "climate change policy"  →  [0.23, -0.11, 0.87, ...]  (single 1536-dim vector)
  Document →  [0.19, -0.08, 0.92, ...]  (single vector)
  Score = cosine(query_vec, doc_vec) = 0.94

ColBERT:
  "climate change policy"  →  
    "climate"  →  [v1]
    "change"   →  [v2]  (5 token vectors, each 128-dim)
    "policy"   →  [v3]
    [CLS]      →  [v4]
    [SEP]      →  [v5]
    
  Document →
    "global"   →  [d1]
    "warming"  →  [d2]  (N token vectors per document)
    "legislation" → [d3]
    ...
    
  Score = Σ_i max_j(cosine(query_token_i, doc_token_j))
  "climate" matches "warming" (d2)   → high score
  "policy" matches "legislation" (d3) → high score
  
  Total score accounts for partial matches
```

### Why ColBERT outperforms standard dense retrieval

| Scenario | Standard dense | ColBERT |
|---|---|---|
| "API rate limit exceeded error" | Single vector averages all concepts | "rate limit" token matches "429 Too Many Requests" token |
| "CEO of Apple Tim Cook" | "Tim Cook" dominates vector | Each name token matched independently |
| Long technical queries | Short queries lose specificity in averaging | Each technical term gets its own token match |
| Domain-specific terminology | OOV tokens averaged into noise | Domain tokens matched exactly |

### When to use ColBERT

| Scenario | Why ColBERT wins |
|---|---|
| Enterprise code search | Exact function name token matching |
| Medical terminology | Multi-word medical terms matched token-by-token |
| Legal exact citation matching | "42 U.S.C. § 1983" matched as tokens |
| Multilingual retrieval | Token-level matching works across languages |
| Long-tail query vocabulary | Domain-specific terms not well-captured by averaging |

### ColBERT scaling considerations

ColBERT stores N token vectors per document (vs. 1 for dense retrieval):
- 100 tokens/doc × 128-dim float32 × 50M docs = 2.56 TB
- vs. 1536-dim float32 × 50M docs = 307 GB for dense
- **Storage cost: ~8× higher than standard dense retrieval**
- **Retrieval latency: 2–5× higher than standard dense**
- **Quality gain: 8–15% precision improvement on technical corpora**

Use ColBERT when precision is more valuable than storage cost.

---

## 4. RAFT — Retrieval-Augmented Fine-Tuning

**Enum:** `RAGStrategy.RAFT` | **File:** `app/rag/agentic/patterns/raft.py` + `app/rag/raft.py` | **Latency:** 20–100 ms (query time)

### What it is

RAFT (Zhang et al. 2024) fine-tunes an LLM on domain-specific Q&A pairs that include **both relevant ("oracle") documents and distractor ("noise") documents**. The trained model learns to:
1. Extract answers from the oracle document
2. Ignore irrelevant distractors
3. Generate chain-of-thought reasoning citing the right sources

```
Standard RAG (without RAFT):
  LLM sees: [Relevant doc] + [3 distractor docs]
  LLM confuses distractors for relevant info
  Hallucination rate: 12%

RAFT fine-tuned LLM:
  Training saw thousands of examples like:
  "Given docs [D1_relevant, D2_distractor, D3_distractor, D4_distractor],
   answer: Q. CoT: 'D1 states X, therefore answer is Y. D2-D4 are about 
   different topics and not relevant.'"
  
  At query time: LLM automatically identifies and ignores distractors
  Hallucination rate: 3%
  Accuracy improvement: +12% on domain-specific Q&A benchmarks
```

### RAFT training data format (AgentVerse)

```python
# From app/rag/raft.py
class RAFTTrainingExample:
    question: str
    oracle_document: str      # the document that contains the answer
    distractor_documents: list[str]  # random docs that don't help
    answer: str
    chain_of_thought: str     # "According to [D1]: X, therefore Y..."
    source_citations: list[str]
```

### When to use RAFT

| Scenario | RAFT benefit |
|---|---|
| Narrow domain Q&A (medical, legal, financial) | Domain-specific fine-tuning captures specialized reasoning |
| Internal enterprise knowledge base | Fine-tuned on your specific docs + formats |
| High-volume, low-latency requirements | RAFT model doesn't need multi-round retrieval |
| Consistent answer quality required | Reduced hallucination through domain training |

### RAFT vs. standard RAG tradeoffs

| Aspect | Standard RAG | RAFT |
|---|---|---|
| Setup cost | None | Training time + compute |
| Query latency | 200–800ms (LLM call) | 20–100ms (smaller fine-tuned model) |
| Hallucination rate | 5–15% | 1–5% |
| Corpus update handling | Instant (just re-index) | Requires re-training |
| Out-of-domain performance | Good | Poor (optimized for training domain) |
| Best for | General purpose | Single-domain high-volume |

### RAFT in AgentVerse implementation

```python
# From app/rag/agentic/patterns/raft.py
class RAFTRAGRuntimeAdapter(RAFTRAGRuntimeContract):
    """Retrieve only after a compatible completed RAFT model is durable."""
    
    # Checks:
    # 1. Is a RAFT model trained for this tenant/domain?
    # 2. Is the RAFT model current (corpus hasn't changed significantly)?
    # 3. Is the query within the RAFT model's training distribution?
    # If any check fails: fall back to standard Hybrid RAG
```

---

## Strategy Selection Summary: When to Use What

```
Query arrives
      │
      ├─ Simple factual lookup, < 100ms budget
      │    └─ NAIVE or HYBRID
      │
      ├─ Short abstract concept query
      │    └─ HYDE
      │
      ├─ Comparative / multi-entity query
      │    └─ FUSION (multiple reformulations)
      │
      ├─ Cross-document reasoning chain
      │    └─ MULTI_HOP
      │
      ├─ Entity-relationship traversal
      │    └─ GRAPH (requires knowledge graph built)
      │
      ├─ Documents may contain wrong/outdated info
      │    └─ CORRECTIVE (grades each doc)
      │
      ├─ Long-form generation with uncertainty
      │    └─ FLARE (retrieve when uncertain)
      │
      ├─ Large corpus, need hierarchical context
      │    └─ RAPTOR (requires tree index built)
      │
      ├─ Self-verification required (medical/legal)
      │    └─ SELF_RAG (critique tokens)
      │
      ├─ Don't know what to retrieve until you start
      │    └─ AGENTIC (LLM-directed)
      │
      ├─ Real-time knowledge needed
      │    └─ WEB_AUGMENTED
      │
      ├─ Token-level precision on technical content
      │    └─ COLBERT
      │
      ├─ High volume, same narrow domain
      │    └─ RAFT (requires model training)
      │
      ├─ Custom processing pipeline needed
      │    └─ MODULAR
      │
      └─ Let the system decide
           └─ ADAPTIVE
```
