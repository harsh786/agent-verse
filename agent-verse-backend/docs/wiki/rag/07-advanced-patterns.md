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

### Real-World Example: Enterprise Knowledge Management Platform

**Industry:** Professional Services Firm (Big 4 Consulting) | **Scale:** 5M internal documents, 80K consultants | **Volume:** 500K queries/day

```
The challenge: 80,000 consultants ask everything from "What's our PTO policy?" 
to "Synthesize all AI regulation frameworks across 12 jurisdictions."
A single RAG strategy forces a choice: optimize for simple (waste resources 
on complex queries) or complex (add 4s latency to every PTO lookup).

Adaptive RAG in action:

Query A: "What is the dress code policy?"
  → Classification: 3ms, heuristic → NAIVE
  → Result: 25ms total, $0.001/query
  → 70% of queries are this type (simple policy lookup)

Query B: "Compare GDPR vs CCPA data residency requirements for healthcare"
  → Classification: 200ms, LLM → MULTI_HOP + CORRECTIVE
  → Result: 2.1s total, $0.025/query
  → 8% of queries are this type (regulatory comparison)

Query C: "breaking news data privacy EU"
  → Classification: 5ms, keyword "breaking" → WEB_AUGMENTED
  → Result: 2.8s, fetches today's EDPB guidance
  → 3% of queries are this type

Query D: "List every project involving semiconductor clients in 2023"
  → Classification: 15ms, keyword "list every" → FUSION (multiple reformulations)
  → Result: 4.2s, high-recall multi-query retrieval
  → 5% of queries are this type

Cost impact of Adaptive RAG vs fixed MULTI_HOP for all:
  All MULTI_HOP: 500K × $0.025 = $12,500/day
  Adaptive RAG:  (350K × $0.001) + (40K × $0.025) + (15K × $0.035) + (25K × $0.015) + (70K × $0.010)
               = $350 + $1,000 + $525 + $375 + $700 = $2,950/day
  Daily savings: $9,550 = $3.5M/year

Quality maintained: each query gets the right strategy, not a one-size-fits-all approach
```

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

### Real-World Example: Global Bank Regulatory Compliance Pipeline

**Industry:** Investment Banking | **Regulatory scope:** 47 jurisdictions | **Volume:** 200K compliance queries/day

```
Challenge: The bank's compliance team needs answers that:
  1. Search from jurisdiction-specific regulatory databases
  2. Are graded for accuracy (can't trust all retrieved docs equally)
  3. Are formatted as structured memos with regulatory citations
  4. Respect confidentiality (internal vs. public document split)
  5. Can be A/B tested (different rerankers for different query types)

Modular pipeline for US Securities Compliance:
  Module 1 — RegulatoryQueryExpander:
    Input: "What are position limit rules for equity swaps?"
    Output: "equity swap position limits", "Dodd-Frank Section 737", 
            "CFTC Rule 150.5", "aggregate position accountability"
  
  Module 2 — ParallelRetriever:
    ├── SECRetriever → searches SEC.gov rule database
    ├── CFTCRetriever → searches CFTC guidance database  
    └── InternalPolicyRetriever → searches internal compliance memos
  
  Module 3 — JurisdictionReranker:
    Scores: US regulatory docs > foreign docs for this query
    Filters: Removes superseded rules (< effective_date cutoff)
  
  Module 4 — CorrectiveGrader:
    CORRECT: "CFTC Rule 150.5: Position limit accountability levels..."
    INCORRECT: "EU EMIR position limits" (wrong jurisdiction) → discard
  
  Module 5 — ComplianceMemoBuilder:
    Formats as: "REGULATORY GUIDANCE — [Date] — [Jurisdiction]
                 Applicable Rules: CFTC Rule 150.5, Dodd-Frank §737
                 Position: ...
                 Citations: [1][2][3]"
  
  Module 6 — ConfidentialityValidator:
    Checks: Is any retrieved content marked INTERNAL ONLY?
    If yes: Strip from output, log access attempt to audit trail
  
  Module 7 — ComplianceLLM:
    Domain-fine-tuned model (vs. GPT-4o-mini for general queries)
    Trained on 50K compliance memo examples

A/B test: Swap Module 3 between ColBERT reranker vs. cross-encoder
  ColBERT: 85ms, 88% precision
  Cross-encoder: 290ms, 94% precision
  Decision: Use ColBERT for latency-sensitive paths, cross-encoder for high-stakes

ROI: 340 compliance analysts × $150/hr × 60% time savings = $30M/year
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

### Real-World Example 1: GitHub Code Search at Enterprise Scale

**Industry:** Enterprise Software | **Scale:** 50M code files, 10K engineers | **Volume:** 500K searches/day

```
Problem with standard dense retrieval for code search:
  Query: "function that handles JWT token expiration gracefully"
  Standard dense: embeds the full query as one vector
    → Returns: documents mentioning "JWT" and "token" generically
    → Misses: functions named handleTokenExpiry(), onJWTExpired(), etc.
  
ColBERT token-level matching:
  Query tokens: ["function", "handles", "JWT", "token", "expiration", "gracefully"]
  Code tokens: ["def", "handle_jwt_expiry", "(token):", "if", "expired", ...]
  
  MaxSim scoring:
    "JWT" → matches "jwt" in function name (exact token match)
    "expiration" → matches "expiry" (near-synonym, high score)
    "handles" → matches "handle" in function name
    "gracefully" → matches exception handling comment
  
  ColBERT finds: handle_jwt_expiry(), onJWTTokenExpired(), refreshOnExpiry()
  Standard dense: finds: JWT documentation, generic token guide
  
Precision@10: 62% (standard dense) → 89% (ColBERT)
Search time: 8ms additional latency for ColBERT vs. standard
Storage overhead: 8× more vectors, stored in separate ColBERT index

Business impact: Engineers find the right function 3× faster
                 43% reduction in "code duplication" (engineers now find existing functions)
```

### Real-World Example 2: Medical Literature Search for Clinical Trials

**Industry:** Biotech/Pharma | **Scale:** 35M PubMed abstracts, 2M full papers | **Users:** 5K researchers

```
Query: "PD-L1 expression level correlation pembrolizumab response rate NSCLC"

Standard dense embedding:
  Averages: PD-L1 + pembrolizumab + NSCLC + response + correlation
  → High scores for general oncology/immunotherapy papers
  → Low discrimination between PD-L1 ≥ 50% vs PD-L1 1% vs PD-L1 negative

ColBERT token matching:
  Token "PD-L1" → exact match in paper titles/abstracts
  Token "pembrolizumab" → exact match (vs. "Keytruda" needs alias expansion)
  Token "NSCLC" → exact 5-char token match vs. "non-small cell lung cancer"
  Token "50%" → threshold specification matched in results tables
  
  Returns:
    KEYNOTE-024 (PD-L1 ≥ 50%, pembrolizumab vs. chemo, NSCLC: 44.8% ORR)
    KEYNOTE-042 (PD-L1 ≥ 1% vs ≥ 50% subgroup analysis)
    KEYNOTE-789 (PD-L1 negative, pembrolizumab + chemo, NSCLC)
  
  Critically: ColBERT separates these three DIFFERENT PD-L1 threshold studies
  Standard dense: lumps them together as "pembrolizumab NSCLC" papers

Impact: Researchers make precision queries that find the right subgroup data
        Incorrect subgroup data cited in IND applications reduced by 67%
```

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

### Real-World Example 1: Insurance Claims Processing

**Industry:** Insurance | **Scale:** 2M policy documents, 500K claims/year | **Volume:** 50K claims queries/day

```
Problem: Claims adjusters ask the same types of questions millions of times:
  - "Does this policy cover flood damage to the foundation?"
  - "What is the deductible for hail damage on a commercial roof?"
  - "Is this medical procedure covered under the rider in section 4.2?"

Standard RAG:
  - 5 documents retrieved (policy + similar policies + exclusions + state law...)
  - LLM sees 4 distractors + 1 relevant section
  - Hallucination rate: 11% (confuses exclusion clauses with coverage clauses)
  - Latency: 800ms (GPT-4o generation)
  - Cost: $0.018/query × 50K/day = $900/day

RAFT fine-tuning process:
  Step 1: Generate 100K training examples from policy Q&A:
    "Given: [Policy Section 3.2 (flood exclusion)], 
            [Section 4.1 (water damage coverage)],  ← oracle
            [Competitor policy excerpt] (distractor),
            [General insurance law text] (distractor)
     Q: Does this policy cover flood damage to the foundation?
     CoT: Section 4.1 covers 'sudden water damage from plumbing failures.'
          Section 3.2 explicitly excludes 'flood, surface water, storm surge.'
          Foundation damage from flood = excluded under Section 3.2.
     A: No. Foundation flood damage is excluded under Section 3.2."
  
  Step 2: Fine-tune GPT-3.5 on 100K examples (2 days, $8,000 training cost)
  
  Results:
    Hallucination rate: 11% → 2.1% (distractors no longer cause confusion)
    Latency: 800ms → 45ms (smaller fine-tuned model, 5× faster)
    Cost: $0.018 → $0.0008/query = $40/day (96% cost reduction)
    Accuracy: 82% → 94% on held-out test set
  
  ROI:
    Training cost: $8,000 one-time
    Daily savings: $860/day = $314,000/year
    Accuracy gain: Wrongful claim denials down 67%
    Payback period: 10 days
```

### Real-World Example 2: Electronic Health Records Q&A

**Industry:** Healthcare System | **Scale:** 50M patient records | **Volume:** 2M clinical queries/day

```
Context: Nurses and doctors query EHR Q&A 2 million times daily.
Same question types repeated constantly:
  "What was the patient's last HbA1c value?"
  "Any documented penicillin allergy?"
  "When was the last flu vaccine administered?"

Standard RAG on EHR:
  Retrieves 5 documents → LLM must extract from notes, labs, prescriptions
  LLM confuses: "allergy to penicillin" vs. "family history of penicillin allergy"
  Hallucination on family history vs. patient allergy: 8% error rate
  → At 2M queries/day: 160,000 potential medication errors/day!

RAFT for EHR Q&A:
  Training data: 500K EHR Q&A pairs with deliberate distractor sections
  Fine-tuned model learns:
    "Patient allergy" = entries in ALLERGY section
    "Family history" = entries in FAMILY HX section → NOT the patient's allergy
    Lab values: always from RESULTS section, not from ASSESSMENT notes
  
  Post-RAFT:
    Family history/patient allergy confusion: 8% → 0.3% error rate
    Latency: 600ms → 35ms (critical for clinical workflows)
    Cost: 2M × $0.015 = $30,000/day → 2M × $0.0005 = $1,000/day
    Daily savings: $29,000 = $10.6M/year
    Clinical safety: 99.7% accuracy on allergy/medication queries
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
