# FLARE, RAPTOR & Self-RAG

Three patterns that make retrieval **adaptive at generation time**: FLARE retrieves only when uncertain, RAPTOR builds a recursive hierarchy of your entire corpus, and Self-RAG critiques every retrieval step before including it.

---

## 1. FLARE — Forward-Looking Active Retrieval

**Enum:** `RAGStrategy.FLARE` | **File:** `app/rag/agentic/patterns/flare.py` | **Latency:** 1–8 s | **Paper:** Jiang et al. 2023

### What it is

FLARE continuously monitors **uncertainty signals** in the LLM's own generated text. When the model hedges ("I think", "might be", "I'm not sure"), FLARE pauses, extracts the uncertain claim, retrieves context specifically for that claim, and regenerates.

```
Long query: "Explain how mRNA vaccines work, their approval timeline, 
             and long-term immune response data"
              │
              ▼
ITERATION 1 — Initial generation (no retrieval yet)
  LLM: "mRNA vaccines work by delivering genetic instructions...
        The first COVID-19 vaccines were approved in [UNCERTAIN: late 2020
        or early 2021?]... Long-term immunity data shows..."
              │
              ▼
DETECT uncertainty: "[UNCERTAIN: late 2020 or early 2021?]"
              │
              ▼
EXTRACT claim for retrieval:
  "When were the first COVID-19 mRNA vaccines approved?"
              │
              ▼
RETRIEVE context for specific claim:
  Chunk 1: "Pfizer-BioNTech received EUA on December 11, 2020"
  Chunk 2: "Moderna received EUA on December 18, 2020"
              │
              ▼
ITERATION 2 — Regenerate uncertain section with context:
  LLM: "...The first COVID-19 vaccines received Emergency Use Authorization
        in December 2020 (Pfizer Dec 11, Moderna Dec 18)..."
              │
              ▼
DETECT — no more uncertainty signals → DONE
```

### Uncertainty signals AgentVerse detects

```python
_UNCERTAINTY_SIGNALS = frozenset({
    "i think", "i'm not sure", "might be", "could be",
    "possibly", "i believe", "unclear", "uncertain",
    "not certain", "may be", "perhaps", "i don't know",
    "[uncertain]", "i'm unsure", "hard to say", "not clear",
})
```

### When to use FLARE

| Scenario | Why FLARE excels |
|---|---|
| Long-form report generation | Uncertainty emerges mid-generation, not at query time |
| Medical/clinical summarization | Model must be confident on every factual claim |
| Legal document drafting | Uncertain statute citations need targeted retrieval |
| Research synthesis across many papers | Complex interwoven facts from multiple sources |
| Educational content generation | Accuracy required for every stated fact |

### When NOT to use FLARE

- **Simple Q&A**: "What is the capital of France?" — no uncertainty possible
- **Real-time systems < 500ms SLA**: FLARE has 1–3 retrieval rounds
- **Known-domain chatbots**: If the model is confident, FLARE does nothing (falls back to Naive)

### Real-world example

**Scenario:** Medical AI assistant generating patient discharge summaries, 500K patients/day.

```
Doctor query: "Summarize treatment effectiveness for patient #4521 
               and compare with standard protocols for sepsis management"

FLARE execution:
  Round 1: Generate initial summary
    "Patient received vancomycin 15mg/kg IV... Blood cultures showed 
     MRSA... Lactate cleared to [UNCERTAIN: unclear value] by hour 6..."
  
  Uncertainty detected: lactate clearance value
  
  Round 2: Retrieve from EHR chunks
    Chunk: "06:00 - Lactate 1.2 mmol/L (cleared, baseline 4.8)"
    Regenerate: "Lactate cleared from 4.8 to 1.2 mmol/L by hour 6 ✓"
  
  Round 3: Another uncertain claim:
    "Standard protocol recommends [UNCERTAIN: 3-hour or 6-hour bundle?]"
    Retrieved: "Surviving Sepsis Campaign: 3-hour bundle recommended"
    Regenerate: "Standard 3-hour bundle per SSC guidelines ✓"
  
  Final summary: Zero uncertainty markers, all facts verified
```

### FLARE at scale

**Challenge:** FLARE is iterative — each iteration is a full LLM call + retrieval.

**Mitigation strategies at 1M requests/day:**

```
Max iterations: 3 (configurable in app/rag/agentic/patterns/flare.py)
                │
                ├─ SemanticCache: cache (query + context) pairs
                │    ~55% hit rate on similar uncertainty spans
                │
                ├─ Early stop: if no uncertainty in first response, return immediately
                │    ~70% of queries need 0 iterations (model is confident)
                │
                └─ Parallel retrieval: if multiple uncertain spans, retrieve concurrently
                     asyncio.gather(*[retrieve(span) for span in uncertain_spans])
```

**Effective throughput:** ~30K req/hr for FLARE with 3-node infrastructure (vs. 2M/hr for Hybrid RAG).

**Latency profile:**
- 0 iterations (confident): 200–400 ms
- 1 iteration: 1–2 s
- 2 iterations: 2–4 s
- 3 iterations (max): 3–8 s

---

## 2. RAPTOR — Recursive Abstractive Processing Tree Of Results

**Enum:** `RAGStrategy.RAPTOR` | **File:** `app/rag/agentic/patterns/raptor.py` | **Latency:** 2–20 s (query); 5–60 min (index build) | **Paper:** Sarthi et al. 2024

### What it is

RAPTOR builds a **hierarchical tree of summaries** over your entire document corpus at index time. At query time, it searches across all levels of the tree simultaneously, giving access to both fine-grained details and high-level summaries.

```
INDEX TIME (runs once, or when corpus changes significantly):

Level 0 (leaves): Original chunks
  [Chunk 1: "Tesla Q3 revenue $23.35B..."]
  [Chunk 2: "Tesla Q3 vehicle deliveries 435,059..."]
  [Chunk 3: "Tesla Q3 energy storage 4.0 GWh..."]
  [Chunk 4: "Tesla Q3 gross margin 17.9%..."]
       │
       ▼ cluster_size = 4, summarize with LLM
Level 1 (summaries):
  [Summary A: "Tesla Q3 2023: Revenue $23.35B, deliveries 435K, 
               energy 4GWh, gross margin 17.9%..."]
       │
       ▼ cluster_size = 4, summarize again
Level 2 (higher summaries):
  [Summary AA: "Tesla 2023 performance: strong revenue growth, 
                margin compression from price cuts, energy storage 
                segment accelerating..."]
       │
       ▼ single root node
Level 3 (root):
  [Root: "Tesla overall strategic position: leading EV maker with 
          diversifying revenue streams..."]

All nodes at all levels are embedded and stored in pgvector.
```

```
QUERY TIME:

"What is Tesla's financial health?"
       │
       ▼
embed(query) → search ALL levels simultaneously
       │
       ├─ Level 0: individual Q3 metric chunks
       ├─ Level 1: quarterly summaries
       ├─ Level 2: annual trend summaries
       └─ Level 3: strategic overview
       │
       ▼
RRF merge across levels: answer uses both specific numbers AND context
       │
       ▼
LLM: "Tesla Q3 2023 revenue was $23.35B (+9% YoY). Gross margin 
      compressed to 17.9% due to aggressive pricing. Energy storage 
      segment growing strongly at 4 GWh, suggesting revenue 
      diversification. Overall: solid growth with near-term margin 
      pressure." [Sources: Q3 10-Q, Annual Strategic Summary]
```

### Why RAPTOR beats standard chunking for long documents

| Limitation | Standard chunking | RAPTOR |
|---|---|---|
| "What are the main themes?" | No chunk captures all themes | Level-2 summary has them |
| "Summarize this 200-page report" | 200 chunks, no coherence | Root node + level-1 summaries |
| "How did performance change over 5 years?" | Scattered in time-ordered chunks | Level-2 temporal summary |
| Needle-in-haystack | Works well | Works well (leaf level) |

### When to use RAPTOR

| Scenario | Document scale |
|---|---|
| Annual reports, 10-K filings | 50–200 pages, quarterly updates |
| Medical literature synthesis | Hundreds of related papers |
| Legal case analysis | Case files + precedent corpus |
| Technical specification review | Large RFPs, standards documents |
| Research paper summaries | arxiv corpus, 500K+ papers |

### When NOT to use RAPTOR

- **Small corpora (< 100 chunks)**: Hierarchy adds no value
- **Real-time indexed documents**: Index build takes time
- **Single-fact lookups**: Leaf-level search works; hierarchy is overhead
- **Latency < 1s SLA**: Use Hybrid RAG instead

### Real-world example

**Scenario:** Investment research platform, 50,000 company annual reports (10 years each), 500,000 PDFs total, ~50 TB.

```
Analyst query: "How has Apple's R&D spending trend compared to revenue 
                growth from 2018 to 2023, and what does this suggest 
                about innovation investment?"

Without RAPTOR:
  Query retrieves individual quarterly chunks:
  - "Q2 2019: R&D expense $3.9B"
  - "Q3 2021: Revenue $81.4B"
  - (500 disconnected data points)
  LLM: cannot synthesize trend from individual chunks

With RAPTOR:
  Level 0: individual R&D and revenue quarterly chunks
  Level 1: annual summaries per year
    "2020 Apple: Revenue $274.5B (+5.5%), R&D $18.75B (+16%)"
    "2021 Apple: Revenue $365.8B (+33%), R&D $21.91B (+17%)"
    "2022 Apple: Revenue $394.3B (+8%), R&D $26.25B (+20%)"
  Level 2: 5-year trend summary
    "Apple R&D CAGR 2018-2023: 18%. Revenue CAGR: 14%. R&D/Revenue 
     ratio expanding from 5.4% to 7.2%, indicating increased 
     innovation investment relative to scale."
  
  Query hits Level 2 directly → LLM synthesizes trend from summary
  Time to answer: 2.5 s (vs. 45 s without RAPTOR due to iterative synthesis)
```

### RAPTOR at scale: 50 TB corpus

**Index build:** 
- 500K PDFs × avg 100 chunks × 3 LLM summarization calls per cluster = 150M LLM calls at index time
- Uses batch embedding + async LLM calls: ~2,000 documents/hour
- Full reindex: ~250 hours → use incremental reindex (only new/changed docs)
- Storage: 500K × (100 leaves + 25 L1 + 6 L2 + 1 root) × 1536-dim = ~50 GB additional vectors

**Query time:**
- Multi-level search: 4 parallel pgvector queries (one per level)
- Latency: 20–80 ms additional vs. standard search
- Benefit: avoids 5–10 LLM synthesis calls that would otherwise be needed

---

## 3. Self-RAG — Retrieve and Critique

**Enum:** `RAGStrategy.SELF_RAG` | **File:** `app/rag/agentic/patterns/self_rag.py` | **Latency:** 1–6 s | **Paper:** Asai et al. 2023

### What it is

Self-RAG uses **critique tokens** (simulated via LLM prompts) to evaluate every step of the RAG pipeline. Before using a retrieved document, it asks: "Is this relevant?" Before returning an answer, it asks: "Is this supported?" and "Is this useful?"

```
Query: "What are the side effects of combining metformin and ibuprofen?"
        │
        ▼
STEP 1: ShouldRetrieve?
  LLM: {"should_retrieve": true, "reason": "specific medical drug interaction query"}
        │
        ▼ (Yes, retrieve)
STEP 2: Retrieve top-K chunks
  Chunk 1: "Metformin and NSAIDs: renal impairment risk..."
  Chunk 2: "Ibuprofen contraindications in Type 2 diabetes..."
  Chunk 3: "General NSAID drug interactions..."
        │
        ▼
STEP 3: ISREL — is each chunk relevant?
  LLM evaluates: {"is_relevant": true} for Chunks 1 and 2
                 {"is_relevant": false} for Chunk 3 (too general)
        │
        ▼ (only use Chunks 1 and 2)
STEP 4: Generate answer with relevant chunks
  "Combining metformin with ibuprofen (an NSAID) can increase the risk 
   of lactic acidosis and renal impairment, especially in patients with..."
        │
        ▼
STEP 5: ISSUP — is response supported by context?
  LLM: {"is_supported": true, "confidence": 0.92}
        │
        ▼
STEP 6: ISUSE — is response useful?
  LLM: {"is_useful": true, "confidence": 0.88}
        │
        ▼
Return answer with critique metadata:
  {
    "answer": "Combining metformin with ibuprofen...",
    "critique": {
      "retrieved": true,
      "relevance_filter": [true, true, false],
      "is_supported": true,
      "is_useful": true,
      "confidence": 0.90
    }
  }
```

### Self-RAG critique token meanings

| Token | Question | When False |
|---|---|---|
| `[Retrieve]` | Does this query need external knowledge? | No retrieval; use internal knowledge only |
| `[ISREL]` | Is this chunk relevant to the query? | Drop chunk; don't include in context |
| `[ISSUP]` | Is my answer supported by the context? | Flag as potentially hallucinated |
| `[ISUSE]` | Is this answer useful for the user? | Regenerate with different approach |

### When to use Self-RAG

| Scenario | Critique that matters most |
|---|---|
| Medical question answering | ISSUP: is the drug information from the chunk or hallucinated? |
| Financial advice bots | ISREL: are retrieved documents actually about the right stock? |
| Compliance checking | ISSUP: is the cited regulation actually in the retrieved text? |
| Exam answer generation | ISUSE: is the answer complete and accurate? |
| Customer support for technical products | ISREL: is the KB article about the right product version? |

### Self-RAG vs. Corrective RAG

| Aspect | Self-RAG | Corrective RAG |
|---|---|---|
| When does critique happen? | During generation (iterative) | After retrieval, before generation |
| Critique method | LLM prompts (simulated critique tokens) | Separate grading LLM pass |
| Overhead | 3–5 additional LLM calls | 1–2 additional LLM calls |
| Granularity | Per-chunk relevance + per-answer quality | Per-retrieved-doc grade |
| Best for | Complex multi-doc answers | Factual Q&A with potential wrong docs |

### Real-world example

**Scenario:** Legal AI assistant for law firms, 10,000 attorney queries/day.

```
Attorney: "Is there precedent for piercing the corporate veil in Delaware 
           when the sole shareholder commingles personal and business funds?"

Without Self-RAG:
  Retrieves 5 chunks. One is about California law, not Delaware.
  LLM includes California precedent in the answer (wrong jurisdiction).
  
With Self-RAG:
  ISREL check on chunk "Riddle v. Leuschner (Cal. 1959)":
    {"is_relevant": false, "reason": "California jurisdiction, not Delaware"}
  → Chunk dropped
  
  ISREL check on chunk "Wallace v. Wood (Del. Ch. 1999)":
    {"is_relevant": true, "reason": "Delaware Court of Chancery, exactly relevant"}
  → Chunk kept
  
  Answer generated from Delaware-only precedent.
  
  ISSUP check: "Is my answer about Delaware corporate veil doctrine 
                supported by Wallace v. Wood?"
    {"is_supported": true, "confidence": 0.94}
  
  Final answer: cites only valid Delaware precedent ✓
```

### Self-RAG at scale: avoiding critique overhead

At high volume, the 3–5 extra LLM calls per Self-RAG query are expensive:

```
10,000 attorney queries/day × 4 critique calls × $0.01/call = $400/day in LLM costs

Optimization strategies:

1. Selective Self-RAG:
   - High-stakes queries (medical, legal, financial): always Self-RAG
   - General queries: Hybrid RAG (10× cheaper)
   
2. Cached critiques:
   - SemanticCache stores (query, chunk) relevance judgments
   - If chunk X was relevant to query type Y before, reuse judgment
   
3. Parallel critique:
   asyncio.gather(*[judge_relevance(chunk) for chunk in retrieved_chunks])
   - 5 chunks critiqued in parallel = same latency as 1 critique
   
4. Lightweight critic model:
   - Use GPT-4o-mini for critique calls ($0.15/1M tokens vs. $5/1M for GPT-4o)
   - Critique accuracy: 94% vs. 98% for the expensive model
```

---

## How FLARE, RAPTOR, and Self-RAG Compose

In AgentVerse, these patterns can be composed for complex analytical queries:

```
Goal: "Analyze 10 years of SEC filings for Apple, identify the top 3 
       risk factors that have grown most significantly, and cite sources"

PatternAssembler selects: [RAPTOR, FLARE, SELF_RAG]

Execution:
  1. RAPTOR: Search hierarchical index of SEC filings
     → Level-2 summaries identify risk factor categories
     → Level-1 summaries provide year-by-year details
     → Leaf chunks contain specific cited text
  
  2. FLARE: During report generation, uncertain claims trigger retrieval
     "Revenue concentration risk increased significantly in [UNCERTAIN: 
      which year?]" → retrieves specific filing chunk
  
  3. SELF_RAG: Before finalizing each risk analysis section:
     ISREL: Are these 2023 10-K chunks about this specific risk? ✓
     ISSUP: Is the risk magnitude claim supported by cited text? ✓
     ISUSE: Is the analysis actionable for investors? ✓
  
Total latency: 15–45 seconds (appropriate for deep research task)
Quality: citation-verified, uncertainty-resolved, relevance-filtered
```

---

## Code Locations

| Component | File | Key class/function |
|---|---|---|
| FLARE | `app/rag/agentic/patterns/flare.py` | `FLARERAGPattern.execute()` |
| FLARE uncertainty signals | `app/rag/agentic/patterns/flare.py` | `_UNCERTAINTY_SIGNALS` |
| RAPTOR | `app/rag/agentic/patterns/raptor.py` | `RAPTORPattern`, `TreeNode` |
| RAPTOR summarize | `app/rag/agentic/patterns/raptor.py` | `_SUMMARIZE_SYSTEM` |
| Self-RAG | `app/rag/agentic/patterns/self_rag.py` | `SelfRAGPattern.execute()` |
| Self-RAG criteria | `app/rag/agentic/patterns/self_rag.py` | `_SHOULD_RETRIEVE_SYSTEM`, `_CRITIQUE_SYSTEM` |
| Strategy dispatch | `app/rag/engine.py` | `retrieve_with_strategy()` |

## Related

- [01-naive-and-hybrid-rag.md](./01-naive-and-hybrid-rag.md) — foundational retrieval used by all three patterns
- [03-corrective-and-multi-hop.md](./03-corrective-and-multi-hop.md) — Corrective RAG: a lighter-weight critique alternative
- [08-at-scale.md](./08-at-scale.md) — latency budgets and cost optimization at 1M req/s
