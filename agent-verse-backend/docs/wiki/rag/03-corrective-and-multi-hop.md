# Corrective RAG, Multi-Hop RAG & Graph RAG

Three patterns for when simple retrieval isn't enough: when retrieved documents might be wrong, when answers require reasoning across multiple documents, and when relationships between entities matter as much as content.

---

## 1. Corrective RAG

**Enum:** `RAGStrategy.CORRECTIVE` | **File:** `app/rag/agentic/patterns/corrective.py` | **Latency:** 500 ms–4 s

### What it is

Corrective RAG adds a **grading step** between retrieval and generation. An LLM evaluates each retrieved document for relevance quality before allowing it into the context window. Documents graded as poor triggers query reformulation and re-retrieval.

```
Query → Retrieve top-K chunks
              │
              ▼
        GRADER LLM
        "Grade each retrieved document:
         CORRECT = directly relevant and accurate
         AMBIGUOUS = partially relevant
         INCORRECT = off-topic or factually wrong"
              │
    ┌─────────┼──────────┐
    │         │          │
CORRECT   AMBIGUOUS  INCORRECT
    │         │          │
    │    Keep, but    Discard +
    │    lower weight  reformulate
    │                   query
    │                     │
    │              Web search or
    │              different retrieval
    │                     │
    └─────────┬───────────┘
              │
        Final context:
        CORRECT chunks + reformulation chunks
              │
              ▼
        LLM generates grounded answer
```

### Grading criteria in AgentVerse

```python
# From app/rag/agentic/patterns/corrective.py
_GRADE_SYSTEM = """Grade the retrieved document for the given query.
Return JSON: {
  "grade": "CORRECT" | "AMBIGUOUS" | "INCORRECT",
  "reason": "<brief explanation>",
  "confidence": <0.0-1.0>
}

CORRECT: Document directly addresses the query with accurate information
AMBIGUOUS: Document is tangentially related but may not fully answer the query  
INCORRECT: Document is off-topic, outdated, or factually wrong for this query"""
```

### When to use Corrective RAG

| Scenario | Problem it solves |
|---|---|
| Medical Q&A | Wrong dosage information would be catastrophic |
| Financial compliance | Outdated regulatory documents must not be cited |
| Legal research | Overruled case law must be excluded |
| Technical support | Version-specific documentation (v1.2 docs retrieved for v2.0 query) |
| Competitive intelligence | Analysts from Company A retrieve documents about Company B accidentally |

### Real-world example

**Scenario:** Pharmaceutical regulatory submission assistant, 500K FDA documents.

```
Query: "What is the required stability testing duration for 
        biological drug products in Zone IVb (hot/very humid) climates?"

Retrieval returns:
  Chunk 1: "ICH Q1A: stability testing for Zone IVb requires 12 months at 
             30°C/75% RH..." ← CORRECT
  Chunk 2: "Stability requirements for small molecule APIs..." ← AMBIGUOUS  
             (query is about biologics, not small molecules)
  Chunk 3: "ICH Q1A (1993 original): 6-month testing period..." ← INCORRECT
             (outdated, superseded by ICH Q1A(R2) in 2003)

Corrective RAG grading:
  Chunk 1: CORRECT (0.95 confidence) → included
  Chunk 2: AMBIGUOUS (0.62 confidence) → downweighted
  Chunk 3: INCORRECT (0.89 confidence: outdated document)
           → Discard + reformulate: "ICH Q1A R2 latest biological stability"
           → Re-retrieval finds ICH Q1A(R2) 2003 update

Final answer: Based on ICH Q1A(R2) (2003): biologics require 12 months 
               at 30°C/75%RH for Zone IVb, with 24-month long-term studies.
               [Source: ICH Q1A(R2), not the superseded 1993 version]
```

**Result:** A compliance error that could have cost millions in regulatory rejection is avoided by grading out the outdated document.

### Corrective RAG at scale

The grading step adds 1 LLM call per retrieved chunk:

```
Optimization at high volume:

1. Grade in parallel:
   asyncio.gather(*[grade(chunk, query) for chunk in retrieved_chunks])
   5 chunks graded simultaneously = single LLM latency (200–400 ms)

2. Batch grading:
   Grade all chunks in one LLM call:
   "Grade these 5 chunks for relevance to: '{query}'
    [1] {chunk1[:200]}
    [2] {chunk2[:200]}
    ...
    Return: [{"id": 1, "grade": "..."}, ...]"
   
   Single call: 50% cost reduction vs. per-chunk grading

3. Cache grades:
   (chunk_hash, query_hash) → grade
   SemanticCache stores grades for similar (chunk, query) pairs
   ~40% hit rate → significant cost savings

4. Tiered grading:
   Fast path: embedding cosine < 0.4 → auto-INCORRECT, no LLM needed
   Medium path: cosine 0.4–0.65 → LLM grade
   Fast path: cosine > 0.85 → auto-CORRECT for simple queries
```

---

## 2. Multi-Hop RAG

**Enum:** `RAGStrategy.MULTI_HOP` | **File:** `app/rag/engine.py` + `app/rag/agentic/llm_query_transformer.py` | **Latency:** 400 ms–3 s

### What it is

Multi-Hop RAG decomposes complex questions into a chain of simpler sub-queries, retrieving evidence for each hop before answering the next. This mirrors human research: "First find X, then use X to find Y, then answer Z."

```
Complex query: "Which companies acquired by Microsoft in the last 5 years 
                have products that compete with Salesforce CRM?"

Single-hop RAG fails: no single document contains this answer

Multi-hop decomposition:
  Hop 1: "What companies has Microsoft acquired 2019-2024?"
          → Retrieved: [LinkedIn, Nuance, Activision, Semantic Machines, ...]
  
  Hop 2: "What products do [Nuance, Semantic Machines, ...] offer?"
          → Retrieved: [Nuance Dragon NaturallySpeaking, DAX clinical AI,
                       Semantic Machines conversational AI...]
  
  Hop 3: "Which of [DAX, conversational AI, ...] compete with Salesforce CRM?"
          → Retrieved: [DAX Copilot targets Salesforce Health Cloud market,
                       conversational AI overlaps with Salesforce Einstein...]
  
  Synthesis: "Post-acquisition, Nuance's DAX Copilot and Semantic Machines' 
              conversational AI platform compete in market segments overlapping
              with Salesforce Health Cloud and Einstein AI."
```

### Multi-hop trigger keywords (AgentVerse)

```python
# From app/rag/engine.py RetrievalPlanner
_MULTI_HOP_KEYWORDS = (
    "compare", "analyze", "contrast", "relationship",
    "difference between", "across all", "summarize all",
)
```

### When to use Multi-Hop RAG

| Query type | Example |
|---|---|
| Cross-entity comparison | "Compare GPT-4 and Claude on coding benchmarks" |
| Chain-of-evidence | "What caused the 2023 Silicon Valley Bank collapse?" |
| Aggregation across sources | "What are all the regulatory requirements for medical devices in the EU?" |
| Temporal chains | "How did Apple's strategy evolve from 2010 to 2020?" |
| Causal chains | "What supply chain issues caused the 2021 semiconductor shortage?" |

### Multi-Hop at scale

**Problem:** Each hop is a separate retrieval + optional LLM call.

```
3-hop query = 3 retrieval calls + 2 intermediate LLM calls + 1 final LLM call
At 50K req/hour: 50K × 5 operations = 250K operations/hour

Optimization:

1. Parallel hops where possible:
   Independent sub-queries: execute simultaneously
   "Compare X and Y": hop to X docs and Y docs in parallel
   
2. Evidence caching:
   If Hop 1 "Microsoft acquisitions" was answered recently,
   cache the entity list for 1 hour
   
3. Hop limit: max_hops = 4 (configurable)
   Prevents infinite chains from complex queries
   
4. Short-circuit: if first-hop retrieval is highly confident,
   skip intermediate LLM synthesis
```

**Latency breakdown:**
- 2-hop: 400–800 ms
- 3-hop: 800 ms–2 s
- 4-hop (max): 1.5–4 s

---

## 3. Graph RAG

**Enum:** `RAGStrategy.GRAPH` | **File:** `app/rag/agentic/patterns/graph.py` | **Latency:** 100–500 ms

### What it is

Graph RAG stores your knowledge as a **graph of entities and relationships** alongside vector embeddings. When entities are mentioned in queries, it traverses the graph to find connected evidence — capturing relationships that pure vector search misses.

```
Knowledge Graph (stored in PostgreSQL + graph tables):
  
  Apple ─────── has_product ────────── iPhone
    │                                     │
    │                                     │
  competes_with                      manufactured_by
    │                                     │
    │                                     ▼
  Samsung ──── has_product ────── Galaxy S24
    │
    │
  acquired ──────────────────── Galaxy (tablet brand)

Query: "How does Apple's supply chain compare to Samsung's for flagship phones?"
        │
        ▼
Entity extraction: ["Apple", "Samsung", "flagship phones"]
        │
        ▼
Graph traversal:
  Apple → manufactured_by → TSMC, Foxconn
  Samsung → manufactured_by → Samsung Semiconductor (in-house)
  Apple → supplier → TSMC (chip), LG (OLED), Sony (camera sensors)
  Samsung → supplier → internal (vertical integration)
        │
        ▼
Graph evidence:
  {
    "paths": [
      "Apple → uses_chip → TSMC M3",
      "Samsung → makes_chip → Exynos 2400 (in-house)"
    ],
    "entities": {
      "Apple": {vertical_integration: low},
      "Samsung": {vertical_integration: high}
    }
  }
        │
        ▼
Hybrid: graph evidence + vector chunk retrieval
        │
        ▼
LLM: "Apple relies on TSMC for chips (external), Samsung manufactures 
      Exynos in-house (vertical integration). Apple has supply chain risk 
      concentration at TSMC; Samsung has manufacturing flexibility but 
      faces Exynos competitive disadvantage vs. Qualcomm Snapdragon..."
```

### Graph evidence types in AgentVerse

```python
# From app/rag/agentic/patterns/graph.py
class GraphEvidenceQuery:
    entity_names: list[str]     # entities to look up
    relationship_types: list[str] # filter by edge type
    max_hops: int = 2           # traversal depth
    include_communities: bool = True  # include community summaries
```

**Three types of graph evidence:**
1. **Entity facts**: attributes of nodes (Apple: founded 1976, HQ Cupertino)
2. **Path evidence**: chains of relationships (Apple → acquired → Siri → developed_by → SRI)
3. **Community summaries**: pre-computed summaries of graph clusters (Apple ecosystem: devices + services + retail)

### When to use Graph RAG

| Scenario | Why graph adds value |
|---|---|
| Enterprise knowledge graphs | Org charts, project dependencies, team relationships |
| Biomedical research | Gene → protein → disease → drug interactions |
| Financial networks | Company → subsidiary → parent → investor relationships |
| Supply chain analysis | Supplier → manufacturer → distributor → retailer chains |
| Cybersecurity threat intelligence | Actor → tool → target → vulnerability relationships |
| Software dependency analysis | Package → version → dependency → vulnerability |

### When NOT to use Graph RAG

- **No pre-built graph**: Graph RAG requires a knowledge graph that was built at index time
- **Simple text Q&A**: Pure vector search is faster and sufficient
- **Unstructured text**: Graph extraction from free text adds significant indexing cost
- **Latency < 200ms SLA**: Graph traversal adds 50–150ms

### Real-world example

**Scenario:** Cybersecurity threat intelligence platform, 10M IoCs (Indicators of Compromise).

```
Security analyst: "What attack techniques are associated with APT29 
                  (Cozy Bear) and which of our systems are at risk?"

Graph structure:
  APT29 ──── uses_tool ──── Cobalt Strike
  APT29 ──── uses_technique ──── Spearphishing (T1566)
  APT29 ──── targets_sector ──── Government, Energy
  
  Cobalt Strike ──── exploits_vuln ──── CVE-2023-4966
  CVE-2023-4966 ──── affects_product ──── Citrix NetScaler

  Our_Infrastructure ──── runs ──── Citrix NetScaler (v14.1)

Graph traversal from "APT29":
  APT29 → uses_tool → Cobalt Strike → exploits → CVE-2023-4966 
        → affects → Citrix NetScaler → matches → Our Citrix (v14.1)!

Alert: "APT29's known Cobalt Strike deployment exploits CVE-2023-4966 
        which affects your Citrix NetScaler instances at versions < 14.1.1.
        IMMEDIATE PATCHING REQUIRED."

Without graph: Vector search would find general APT29 information
               but would not traverse to YOUR specific vulnerable systems.
```

**Scale:** 10M IoC nodes, 50M relationships.
- Graph query (2-hop): 50–150 ms
- Combined with vector search: 150–300 ms total

---

## How Corrective, Multi-Hop, and Graph RAG Compose

```
Enterprise research query:
"What are the antitrust risks for Microsoft's Activision acquisition 
 given precedents from their previous gaming acquisitions?"

PatternAssembler selects: [MULTI_HOP, GRAPH, CORRECTIVE]

Execution:
  1. MULTI_HOP:
     Hop 1: "Microsoft gaming acquisitions history" → [Minecraft, ZeniMax, 
              Bethesda, Activision Blizzard]
     Hop 2: "Antitrust outcomes for each acquisition" → regulatory filings
     Hop 3: "Gaming market concentration analysis" → analyst reports
  
  2. GRAPH:
     Microsoft → acquired → ZeniMax (2020, $7.5B, approved by FTC)
     Microsoft → acquired → Activision (2023, $68.7B, CMA blocked then approved)
     Activision → market_share → 20% Call of Duty, 15% mobile gaming
     Microsoft → competes_with → Sony (PlayStation), Nintendo
  
  3. CORRECTIVE:
     Grade: FTC 2023 ruling about Activision (CORRECT: current)
     Grade: FTC 2000 ruling about Sun acquisition (INCORRECT: unrelated)
     → Discard irrelevant precedent
  
  Final answer: Cites accurate current precedents, graph-verified relationships,
                filtered outdated documents. 15 seconds total.
```

---

## Latency and Scale Comparison

| Pattern | p50 latency | p99 latency | Max throughput (3-node) | Cost/query |
|---|---|---|---|---|
| Corrective RAG | 600 ms | 2 s | 200K req/hr | $0.004 |
| Multi-Hop (2-hop) | 500 ms | 1.5 s | 150K req/hr | $0.006 |
| Multi-Hop (4-hop) | 1.5 s | 4 s | 50K req/hr | $0.015 |
| Graph RAG | 200 ms | 500 ms | 500K req/hr | $0.002 |
| Corrective + Multi-Hop | 1.5 s | 5 s | 50K req/hr | $0.018 |

---

## Code Locations

| Component | File | Key element |
|---|---|---|
| Corrective RAG | `app/rag/agentic/patterns/corrective.py` | `CorrectiveRAGPattern`, `_GRADE_SYSTEM` |
| Multi-Hop | `app/rag/engine.py` + `app/rag/agentic/llm_query_transformer.py` | `RetrievalPlanner`, query decomposition |
| Graph RAG | `app/rag/agentic/patterns/graph.py` | `GraphEvidenceQuery`, `GraphEvidence` |
| Knowledge graph | `app/knowledge_graph/` | `KGIngestionHook`, `ingestion_hook.py` |
| Graph RLS scope | `app/rag/agentic/patterns/graph.py` | tenant-scoped graph capability |
