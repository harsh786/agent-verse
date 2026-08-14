# Fusion RAG, Speculative RAG & Agentic Patterns

Three patterns that use **parallel execution and multi-perspective synthesis** for higher recall and reliability.

---

## 1. Fusion RAG — Multi-Query Parallel Retrieval

**Enum:** `RAGStrategy.FUSION` | **File:** `app/rag/agentic/patterns/fusion.py` | **Latency:** 600 ms–5 s

### What it is

RAG-Fusion generates **multiple reformulations** of the original query in parallel, retrieves for each, then merges all results with RRF. The core insight: a single query formulation will miss documents that a differently-worded reformulation would find.

```
Original query: "Why is Kubernetes harder to use than Docker Compose?"

Step 1: Query expansion (parallel LLM calls)
  Reformulation 1: "Kubernetes complexity vs Docker Compose simplicity"
  Reformulation 2: "challenges learning Kubernetes for beginners"
  Reformulation 3: "Docker Compose advantages over Kubernetes for small teams"
  Reformulation 4: "when to avoid Kubernetes and use simpler orchestration"
  [original query also included]

Step 2: Retrieve for each (5 parallel hybrid searches)
  Query 1 results: [k8s architecture docs, cert-manager complexity guide, ...]
  Query 2 results: [k8s learning curve articles, CNCF survey data, ...]
  Query 3 results: [docker compose guides, "right tool for the job" posts, ...]
  Query 4 results: [k8s anti-patterns, "don't use k8s for small apps", ...]
  [original: same as query 1 roughly]

Step 3: RRF merge across all 5 result sets
  Documents appearing in multiple result sets are ranked higher
  "Kubernetes Complexity: What Nobody Tells You" → in 4/5 sets → rank 1

Step 4: LLM synthesizes from merged context
  Answer covers: architecture complexity, YAML verbosity, networking model,
  learning curve, when each tool fits
```

### When to use Fusion RAG

| Scenario | Why multi-query helps |
|---|---|
| Ambiguous/multi-faceted queries | Different phrasings capture different aspects |
| Technical comparison questions | Captures both sides' documentation |
| Research queries with vocabulary gaps | Domain-specific vs. general vocabulary |
| High-stakes precision/recall | Medical, legal: missing one source is unacceptable |
| Questions where query = title of an answer | Reformulations find the actual document |

### Real-world example

**Scenario:** Patent search database, 10M patents, law firm uses it.

```
Query: "prior art for haptic feedback in smartwatch displays"

Single-query RAG misses:
  - "tactile response touchscreen wearable" (different vocabulary)
  - "piezoelectric actuator wrist device" (technical terminology)
  - "vibrotactile display wearable computing" (academic terminology)

Fusion RAG reformulations:
  1. "haptic feedback smartwatch touchscreen patent"
  2. "tactile response wearable display vibration"
  3. "piezoelectric actuator wristband device"
  4. "vibrotactile feedback wearable 2015-2023"
  5. [original]

Results: 5× more prior art discovered
False negative rate: 2% vs 15% for single-query
```

---

## 2. Speculative RAG — Parallel Generate-Then-Verify

**Enum:** `RAGStrategy.SPECULATIVE` | **File:** `app/rag/agentic/patterns/speculative.py` | **Latency:** 800 ms–4 s

### What it is

Speculative RAG generates **multiple candidate answers in parallel** using small/fast models, then verifies only the best candidate against retrieved evidence using a larger model.

```
Query: "What is the velocity of light in glass with refractive index 1.5?"

Step 1: Generate N candidates in parallel (fast model)
  Candidate A: "c/n = 3×10⁸/1.5 = 2×10⁸ m/s"    (likely correct)
  Candidate B: "Approximately 200,000 km/s"          (correct but vague)
  Candidate C: "1.5c... 4.5×10⁸ m/s"               (wrong: n multiplied)
  
Step 2: Rank candidates by embedding similarity to retrieved context
  Retrieved: "Speed of light in medium = c/n where n = refractive index"
  Candidate A similarity: 0.92 (correct formula)
  Candidate B similarity: 0.78 (correct but less specific)
  Candidate C similarity: 0.35 (wrong formula)
  
  Top candidate: A → "c/n = 3×10⁸/1.5 = 2×10⁸ m/s"

Step 3: Verify top candidate with larger model
  "Is 'c/n = 3×10⁸/1.5 = 2×10⁸ m/s' supported by: 
   'Speed of light in medium = c/n'?"
  → VERIFIED (0.97 confidence)

Step 4: Return verified answer with source citation
```

### When to use Speculative RAG

| Scenario | Why speculative helps |
|---|---|
| Factual Q&A with clear answers | Fast generation of likely correct answer, cheap verification |
| High-volume low-latency RAG | Small model for candidates, large model only for verification |
| Math/formula questions | Multiple approaches, verify the correct one |
| Ambiguous queries with one right answer | Generate the answer space, find the best |

### Latency optimization with Speculative RAG

```
Without Speculative:
  Large model generates answer: 800ms–2s

With Speculative (3 candidates):
  3 small model calls (parallel): 200ms
  + Retrieval: 100ms
  + Ranking (embedding): 20ms
  + Large model verify top-1: 300ms
  Total: ~620ms

Savings: 30–50% latency reduction for fact-based queries
Cost: 3 small model calls + 1 large = similar cost to 1 large alone
```

---

## 3. Agentic RAG — LLM-Directed Multi-Step Retrieval

**Enum:** `RAGStrategy.AGENTIC` | **File:** `app/rag/agentic/patterns/agentic.py` | **Latency:** 2–30 s

### What it is

Agentic RAG lets an LLM **decide what to retrieve next** based on what it has already found. The LLM orchestrates a multi-step retrieval loop, choosing actions from a defined set.

```
Actions available:
  RETRIEVE → standard hybrid search
  EXPAND → retrieve neighboring chunks for context
  WEB → search the web for real-time data
  DONE → sufficient context, generate answer
  CITE → extract citation from existing context (no more retrieval)

Query: "What were the key decisions at the AWS re:Invent 2023 keynote 
        that affect serverless computing?"

Turn 1:
  LLM decides: RETRIEVE("AWS re:Invent 2023 keynote announcements")
  Retrieved: [AWS Graviton4 launch, EKS anywhere updates, ...]
  
  LLM: "I have general announcements but not serverless-specific"

Turn 2:
  LLM decides: RETRIEVE("AWS Lambda 2023 updates serverless features")
  Retrieved: [Lambda SnapStart GA, Lambda 10GB memory, Functions URLs...]
  
  LLM: "Good serverless data but missing context on why these matter"

Turn 3:
  LLM decides: RETRIEVE("AWS Lambda performance improvements architecture")
  Retrieved: [SnapStart explanation, cold start reduction blog post...]

Turn 4:
  LLM decides: DONE (sufficient context)
  
  Generates: "AWS re:Invent 2023 serverless highlights:
              1. Lambda SnapStart → 90% cold start reduction [1][2]
              2. Memory up to 10GB for compute-intensive functions [1]
              3. Lambda Functions URLs for direct HTTPS invocation [2]..."
```

### Agentic action decision system

```python
# From app/rag/agentic/patterns/agentic.py
class AgenticAction(StrEnum):
    RETRIEVE = "retrieve"     # standard retrieval
    EXPAND = "expand"         # get surrounding context
    WEB = "web_search"        # real-time web retrieval
    CITE = "cite"             # extract citation from context
    DONE = "done"             # sufficient evidence, generate

_DECISION_SYSTEM = """Choose exactly one next Agentic RAG action.
verbatim claims that occur in both the answer and evidence. 
Never invent another action."""
```

### When to use Agentic RAG

| Scenario | Why agentic is needed |
|---|---|
| Open-ended research questions | Don't know what to retrieve until you've seen the first results |
| Complex multi-document synthesis | Each document suggests what to look for next |
| Current events + historical context | Web for today, corpus for background |
| Investigative analysis | "Follow the trail" of evidence |
| Code debugging assistance | Read error → retrieve related code → retrieve docs |

---

## 4. Agentic Chunking — LLM-Driven Proposition Extraction

**Enum:** `RAGStrategy.AGENTIC_CHUNKING` | **File:** `app/rag/agentic/patterns/agentic_chunking.py` | **Latency:** 3–15 s (index time)

### What it is

Instead of mechanical chunking (split by 512 tokens, by heading, by sentence), Agentic Chunking uses an LLM to extract **self-contained atomic propositions** from documents.

```
Input paragraph:
  "The transformer architecture was introduced by Vaswani et al. in 2017 
   in the paper 'Attention Is All You Need'. It uses self-attention 
   mechanisms instead of recurrent neural networks. The original model 
   had 65M parameters and achieved state-of-the-art performance on 
   machine translation tasks. BERT, released by Google in 2018, built 
   on this architecture for bidirectional encoding."

Standard semantic chunker:
  1 chunk = the entire paragraph above (500 tokens)

Agentic Chunking:
  Proposition 1: "The transformer architecture was introduced by Vaswani 
                  et al. in 2017."
  Proposition 2: "The paper 'Attention Is All You Need' introduced the 
                  transformer architecture."
  Proposition 3: "Transformers use self-attention instead of RNNs."
  Proposition 4: "The original transformer had 65M parameters."
  Proposition 5: "The original transformer achieved SOTA on machine translation."
  Proposition 6: "BERT was released by Google in 2018."
  Proposition 7: "BERT is based on the transformer architecture."
  Proposition 8: "BERT uses bidirectional encoding."
```

### Why atomic propositions improve retrieval

```
Query: "What year did BERT come out?"

Standard chunking → retrieves full paragraph → LLM finds year within text
                    (5× more tokens in context, higher cost)

Agentic Chunking → retrieves Proposition 6: "BERT was released by Google in 2018"
                    (exact match, minimal tokens, $0 extra cost for answer)
```

### When to use Agentic Chunking

- **Dense informational documents** with many facts per paragraph
- **FAQ databases** where each chunk should answer exactly one question
- **Scientific papers** where each sentence may be independently queryable
- **Product documentation** with many specifications

### Cost consideration

Agentic Chunking runs at **index time** — every chunk goes through an LLM extraction:
- 1M chunks × $0.005/extraction = $5,000 one-time indexing cost
- Query time cost: same as Naive RAG (no extra calls)
- **Use when retrieval quality improvements justify indexing cost**

---

## 5. Web-Augmented RAG — Real-Time Knowledge

**Enum:** `RAGStrategy.WEB_AUGMENTED` | **File:** `app/rag/agentic/patterns/web_augmented.py` | **Latency:** 1–5 s

### What it is

Supplements corpus retrieval with **real-time web search results** when knowledge is time-sensitive or not in the indexed corpus.

```
Query: "What is the current Fed funds rate and how does it compare 
        to last year?"

Corpus: Last indexed 3 months ago → doesn't have today's rate
        
Web-Augmented RAG:
  1. Corpus search: "Federal Reserve monetary policy history" → context
  2. Web search (SearXNG): "Federal Reserve funds rate today 2024"
     → Returns: "Fed holds rates at 5.25-5.5% for 5th consecutive meeting"
  
  Merged context: historical rate trajectory from corpus + today's rate from web
  
  Answer: "Current Fed funds rate: 5.25-5.5% (held steady at today's FOMC 
           meeting). This is up from 0.25% in early 2022 [corpus: 1], 
           representing the fastest rate hiking cycle since 1980 [corpus: 2]. 
           The current hold suggests the Fed believes inflation is under control 
           [web: live data]."
```

### Web search integration in AgentVerse

```python
# app/rag/agentic/patterns/web_augmented.py
class WebSearchRequest:
    query: str
    max_results: int = 5
    require_https: bool = True      # security
    allowed_domains: list[str] = []  # tenant policy whitelist
    blocked_domains: list[str] = []  # tenant policy blacklist

class WebRejection:
    reason: str  # "domain_not_allowed" | "content_policy" | "ssl_required"
```

**Security controls:**
- Only HTTPS sources (no HTTP)
- Per-tenant domain whitelist/blacklist via PolicyEngine
- Content safety scan on web results before inclusion
- No credentials/auth tokens passed to web searches
- Rate limited via CostController

### When to use Web-Augmented RAG

| Scenario | What web adds |
|---|---|
| Current stock prices / financial data | Real-time market data |
| Breaking news analysis | Today's events vs. historical context |
| Software version compatibility | Latest SDK versions, deprecation notices |
| Regulatory updates | New rulings, amendments not in corpus |
| Sports/entertainment results | Live scores, box office, chart positions |
| Scientific preprints | arXiv papers published after last corpus index |

### When NOT to use Web-Augmented RAG

- **Air-gapped environments**: no external network access allowed
- **Sensitive internal knowledge**: never send proprietary queries to web search
- **High compliance requirements**: web sources can't be audited like internal documents
- **Static, well-indexed corpora**: internal KB is always up-to-date

---

## Pattern Composition: Research Assistant Example

**Scenario:** Market research analyst, "Comprehensive competitive analysis of AI chip market Q4 2024"

```
PatternAssembler selects:
  [AGENTIC, FUSION, WEB_AUGMENTED, CORRECTIVE]

Execution:
  Phase 1 — AGENTIC RAG builds retrieval plan:
    Turn 1: RETRIEVE "NVIDIA AI chip market share 2024"
    Turn 2: RETRIEVE "AMD GPU AI training competitive position"  
    Turn 3: RETRIEVE "Intel Gaudi AI accelerator performance"
    Turn 4: WEB "TSMC AI chip production capacity Q4 2024"
    Turn 5: RETRIEVE "AI chip market forecast 2024-2025"
    Turn 6: DONE

  Phase 2 — FUSION on each major sub-query:
    "NVIDIA AI chip market" + 4 reformulations → 5× more relevant docs

  Phase 3 — CORRECTIVE grades each retrieved document:
    INCORRECT: 2022 market data (outdated) → discarded
    INCORRECT: consumer GPU article (wrong segment) → discarded
    CORRECT: "AI chip market: NVIDIA 80% data center share" → kept

  Phase 4 — WEB fills gaps:
    "NVIDIA Q3 2024 earnings AI revenue" → real-time earnings data
    Merged with corpus for historical context

  Final report: 2,500 words, 18 citations, all verified
  Total latency: 35 seconds (acceptable for deep research task)
```
