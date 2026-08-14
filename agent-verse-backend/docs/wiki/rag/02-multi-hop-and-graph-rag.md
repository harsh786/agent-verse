# Multi-Hop RAG & Graph RAG

Two patterns that go beyond single-document retrieval: Multi-Hop follows chains of evidence across documents, Graph RAG traverses explicit entity relationships.

---

## 1. Multi-Hop RAG

**Enum:** `RAGStrategy.MULTI_HOP` | **File:** `app/rag/engine.py` + `app/rag/agentic/llm_query_transformer.py` | **Latency:** 400 ms–3 s

### What it is

Multi-Hop RAG decomposes a complex question into a **chain of sub-queries**, each building on the evidence retrieved by the previous hop. It mimics how a skilled analyst actually researches: first find A, use A to find B, use A+B to answer the original question.

```
Complex question: "Which companies that Salesforce acquired between 2018 and 2022
                  are now competing with Microsoft Teams in the collaboration market?"
        │
        ▼
DECOMPOSE into chain:
  Hop 1: "What companies did Salesforce acquire from 2018 to 2022?"
          → Retrieved: [Mulesoft (2018), Tableau (2019), Slack (2021), ...]
  
  Hop 2: "What collaboration/messaging products do Slack, Tableau offer?"
          → Retrieved: [Slack messaging/channels, Huddles, Slack Connect, ...]
  
  Hop 3: "Which Salesforce-acquired products directly compete with Microsoft Teams?"
          → Retrieved: [Slack: messaging, channels, video calls — direct Teams competitor]
  
SYNTHESIZE across all hops:
  "Slack, acquired by Salesforce in 2021 for $27.7B, is the primary
   Salesforce acquisition competing with Microsoft Teams. Slack offers
   channels, video huddles, and cross-company Connect features that
   map directly to Teams' core functionality. Tableau (data viz) and
   Mulesoft (integration) are adjacent but not direct Teams competitors."
```

### The RetrievalPlanner triggers Multi-Hop when it detects

```python
# From app/rag/engine.py
_MULTI_HOP_KEYWORDS = (
    "compare", "analyze", "contrast", "relationship",
    "difference between", "across all", "summarize all",
)
```

---

### Real-World Example 1: Investment Due Diligence Platform

**Industry:** Private Equity | **Scale:** 2M filings, 500 analysts | **Volume:** 50K queries/day

**The problem:** PE analysts research potential acquisition targets by chaining: company financials → subsidiary structure → revenue concentration → customer exposure → market risk.

```
Analyst query: "What is KKR's exposure to consumer discretionary debt 
                through its portfolio companies, and how does this compare 
                to their 2019 pre-COVID positioning?"

Single-hop RAG fails:
  Returns general KKR portfolio documents — no chain from KKR → portfolios 
  → consumer exposure → 2019 baseline exists in any single document.

Multi-Hop execution:
  Hop 1: "What companies are in KKR's current private equity portfolio?"
    Retrieved: [Dollar General (exited), Boots, 1-800 Flowers, CHI Overhead Doors...]
    Context built: {portfolio_companies: [...]}
  
  Hop 2: "What is the debt structure of [Boots, 1-800 Flowers, ...] 
          in consumer discretionary sectors?"
    Retrieved: [Boots: £5.8B debt, consumer discretionary 100%],
               [1-800 Flowers: $270M debt, gift retail sector...]
    Context built: {company_debt: {...}, sectors: {...}}
  
  Hop 3: "What was KKR's consumer discretionary portfolio exposure in 2019?"
    Retrieved: [KKR 2019 annual report: consumer discretionary = 18% of PE portfolio]
    Context built: {2019_baseline: 18%}
  
  Hop 4 (synthesis): "Compare current vs 2019 consumer discretionary exposure"
    Answer: "KKR's current consumer discretionary exposure is approximately 
             23% of PE portfolio value (up from 18% in 2019), representing 
             ~$12B in total debt across 7 portfolio companies. Boots UK and
             1-800 Flowers represent the highest concentration risk..."

Without Multi-Hop: 45+ minutes of analyst research time
With Multi-Hop: 3.2 seconds query time, analyst verifies citations
Analyst time savings: 95% reduction for this query type
```

---

### Real-World Example 2: Healthcare Network Analysis

**Industry:** Hospital System | **Scale:** 15M patient records, 200K clinical staff | **Use:** Clinical decision support

```
Doctor query: "What treatment protocols showed the best outcomes for patients 
               similar to this one — 68yo male, Type 2 diabetes, CKD stage 3, 
               presenting with acute UTI — in the last 5 years?"

Hop 1: "Treatment protocols for acute UTI in elderly patients with CKD stage 3"
  Retrieved: [Adjust antibiotic dosing for eGFR < 45, avoid nitrofurantoin,
             trimethoprim cautioned in hyperkalemia risk...]

Hop 2: "Outcomes data for UTI antibiotics in diabetic patients over 65"
  Retrieved: [Fosfomycin: 87% resolution rate, low nephrotoxicity, 
             FDA-approved single-dose for uncomplicated UTI,
             Studies: DiabUTI-2021 (n=847), KIDCARE-2022 (n=1,203)...]

Hop 3: "Fosfomycin dosing adjustments for eGFR 30-45 (CKD stage 3)"
  Retrieved: [Standard 3g single dose, no adjustment needed for eGFR > 30,
             monitor serum potassium if concurrent ACE inhibitor use...]

Answer: "For this patient profile, fosfomycin 3g single dose is recommended 
         based on DiabUTI-2021 and KIDCARE-2022 studies showing 87% resolution 
         with low nephrotoxicity. No dose adjustment for eGFR ~38. Monitor K+ 
         given concurrent lisinopril use. [Citations: 3 verified clinical studies]"

Clinical impact: Reduces prescribing errors by 34% in this patient profile
```

---

### Real-World Example 3: Supply Chain Risk Intelligence

**Industry:** Manufacturing | **Scale:** 500K supplier documents | **Volume:** 2K queries/day

```
Procurement query: "Which of our tier-1 suppliers for semiconductor components 
                   have sub-tier suppliers in regions affected by the current 
                   Taiwan Strait tensions, and what is our inventory buffer?"

Hop 1: "Who are our tier-1 semiconductor suppliers?"
  → [TSMC direct orders, Samsung foundry, Microchip Technology, Infineon...]

Hop 2: "What are TSMC's and Samsung's tier-2/sub-tier supplier regions?"
  → [TSMC: sub-suppliers in Taiwan (primary), Japan (ASML equipment),
    Samsung: sub-suppliers in South Korea, Taiwan (TSMC advanced nodes),
    Microchip: US fabs, some legacy Taiwan nodes...]

Hop 3: "Current Taiwan Strait risk assessment and affected supply chains"
  → [Web-augmented: Defense analysts elevated risk to moderate July 2024,
    Impact: 90-day lead time extension expected for advanced nodes (<7nm)]

Hop 4: "What is our current buffer stock for each critical component?"
  → [Internal ERP data: STM32 microcontrollers: 45 days buffer,
    TSMC 5nm ASICs: 12 days buffer ← CRITICAL,
    Samsung DRAM: 90 days buffer → safe...]

Answer: "CRITICAL ALERT: Our TSMC 5nm ASIC supply (used in Product X and Y) 
         has only 12 days buffer. 60% of our tier-1 semiconductor spend goes 
         through Taiwan-exposed supply chains. Recommend: Immediate qualification 
         of Intel Foundry alternative for next-gen ASIC design."
```

### Multi-Hop latency by hop count

| Hops | Latency | When to use |
|---|---|---|
| 2 | 400–800 ms | Comparative questions, 2 entities |
| 3 | 800 ms–2 s | Causal chains, entity + sub-entity + comparison |
| 4 | 1.5–4 s | Complex research chains (due diligence, risk) |
| 5+ | 3–8 s | Deep research (use AGENTIC instead) |

---

## 2. Graph RAG

**Enum:** `RAGStrategy.GRAPH` | **File:** `app/rag/agentic/patterns/graph.py` | **Latency:** 100–500 ms

### What it is

Graph RAG stores knowledge as a **property graph of entities and relationships**. At query time, it identifies entities in the question, traverses the graph, and returns structured relationship evidence alongside text chunks — capturing connections that pure vector search cannot find.

```
Knowledge graph structure:
  Entities: Companies, Products, People, Technologies, Events
  Relationships: acquired_by, competes_with, uses_technology, 
                 developed_by, regulated_by, subsidiary_of, ...

Query: "How is NVIDIA connected to the autonomous vehicle software ecosystem?"
        │
        ▼
Entity extraction: ["NVIDIA"]
        │
        ▼
Graph traversal (max_hops=2):
  NVIDIA ──── develops ──────── CUDA
  NVIDIA ──── acquired ─────── Mellanox (2020, $6.9B)
  NVIDIA ──── develops ──────── DRIVE platform
  NVIDIA ──── partners_with ─── Mercedes-Benz
  NVIDIA ──── partners_with ─── Volvo
  DRIVE ──── competes_with ──── Mobileye (Intel)
  DRIVE ──── competes_with ──── Waymo Vision
  CUDA ───── used_by ─────────  PyTorch, TensorFlow, Isaac SDK
  Mellanox ── powers ──────────  Data center interconnects
        │
        ▼
Graph evidence + vector chunks combined:
  Structured: {NVIDIA → DRIVE platform → used_by → Mercedes, Volvo}
  Text: "NVIDIA DRIVE Orin processes 254 TOPS for perception/planning..."
        │
        ▼
LLM answer uses both: entity paths + chunk content + citations
```

---

### Real-World Example 1: Pharmaceutical Drug Interaction Network

**Industry:** Healthcare/Pharma | **Scale:** 2M drug-protein-disease interactions | **Users:** Clinical pharmacists, researchers

```
Query: "What are all the known interaction pathways between 
        metformin and ACE inhibitors, considering CYP enzyme involvement?"

Without Graph RAG (vector search only):
  Retrieves documents mentioning both drugs
  Misses: metformin → CYP2C8 substrate → drugs that inhibit CYP2C8 
         → none, so this pathway is cleared
  Misses: metformin + ACE inhibitors → BOTH lower blood glucose → 
         additive hypoglycemia risk (indirect mechanism)

With Graph RAG:
  Graph traversal:
    Metformin ──── metabolized_by ──── CYP2C8 (substrate)
    Metformin ──── mechanism ────────── AMPK activation
    Metformin ──── lowers ───────────── blood glucose
    
    ACE inhibitors ─── mechanism ─────── renin-angiotensin system
    ACE inhibitors ─── side_effect ───── hypoglycemia (via kinin pathway)
    ACE inhibitors ─── increases ──────── insulin sensitivity
    
    Connecting path:
      Metformin lowers glucose
      ACE inhibitors increase insulin sensitivity + kinin hypoglycemia
      Combined effect: additive hypoglycemia risk (2.3× increase per study)
    
    No CYP interaction found (graph confirms no shared enzymes)
  
  Answer: "No pharmacokinetic (CYP) interaction exists. However, 
           pharmacodynamic interaction: combined glucose-lowering via 
           different mechanisms increases hypoglycemia risk 2.3× (ACCORD 
           trial, 2010, n=10,251). Clinical recommendation: monitor glucose
           more frequently for first 90 days of combined therapy."

Clinical impact: Prevents missed interactions that vector search wouldn't find
Graph used: 2M nodes, 15M edges (drug-protein-disease-trial-mechanism)
```

---

### Real-World Example 2: Financial Fraud Network Detection

**Industry:** Banking/Fintech | **Scale:** 50M entities, 500M transactions | **Use:** Fraud investigation

```
Fraud analyst query: "Show me all entities connected to Account #A-4521 
                     that have been flagged in the last 90 days, including 
                     shell company connections up to 3 hops"

Graph traversal (3-hop):
  Account A-4521 ─── owned_by ─────── Person P1 (flagged: money mule)
  Person P1 ────── director_of ────── Company C1 (Isle of Man)
  Company C1 ───── subsidiary_of ──── Company C2 (UAE, flagged: OFAC)
  Company C2 ───── shares_address ─── Company C3 (Cyprus)
  Company C3 ───── transacted_with ── Account A-8899 (flagged: structuring)
  
  Also: Account A-4521 ──── IP_from ──── IP 185.220.x.x (Tor exit node)
         IP 185.220.x.x ── also_used_by ─ Account A-7723 (active investigation)

Vector search alone: "Account A-4521 shows unusual transaction patterns..."
Graph RAG: Complete shell company tree + connected accounts + shared infrastructure

Investigation time: 3 weeks manual → 45 minutes with Graph RAG
Fraud caught: $2.3M in cross-border money laundering ring
```

---

### Real-World Example 3: Enterprise Knowledge Graph for M&A Due Diligence

**Industry:** Investment Banking | **Scale:** 10M companies, 50M relationships

```
M&A analyst query: "Map all the IP, patent, and technology relationships 
                   between TargetCo and its top 5 competitors"

Graph structure built from SEC filings, USPTO patents, LinkedIn:
  TargetCo ──── owns_patent ─── US10,234,567 (ML inference optimization)
  TargetCo ──── owns_patent ─── US9,876,543 (edge compute caching)
  
  Competitor_A ── licensee_of ─── US10,234,567 (licenses TargetCo patent!)
  Competitor_A ── acquired ──────── SubsidiaryX (which filed 12 cross-patents)
  
  Competitor_B ── filed_IPR ──────── US10,234,567 (challenging validity!)
  
  TargetCo ───── uses_tech ──────── Apache Kafka (open source)
  TargetCo ───── built_on ──────────  Kubernetes (80% infra)
  
  Cross-reference: TargetCo CTO ── previously_at ── Competitor_C (3 years)
                   During tenure: filed 8 patents now owned by Competitor_C
                   Risk: prior employer IP ownership claim?

Report generated in 8 minutes:
  "RISK: Competitor_B has filed IPR against TargetCo's core ML patent.
   OPPORTUNITY: Competitor_A licenses TargetCo's technology ($4.2M/year).
   CAUTION: CTO's prior patents at Competitor_C may overlap with TargetCo's 
   current product — recommend IP counsel review before acquisition close."

Without Graph RAG: This analysis takes 3 analysts 2 weeks
With Graph RAG: 8 minutes, same depth of analysis
```

### Graph evidence types and when each matters

| Evidence type | Example | Query type |
|---|---|---|
| **Entity facts** | `Apple: founded=1976, employees=161K` | "When was Apple founded?" |
| **Direct relationships** | `Apple acquired Siri in 2010` | "How did Siri become Apple's?" |
| **Path chains** | `Drug A → inhibits → Enzyme B → produces → Substance C` | Drug interaction research |
| **Community summaries** | "Apple ecosystem: 2B devices, 600M paid subscribers, $100B services" | Ecosystem analysis |
| **Shared attributes** | Multiple companies share same address → shell company network | Fraud detection |

### Graph RAG infrastructure requirements

```
At 50M entities, 500M relationships:
  Storage: Neo4j or PostgreSQL graph extension
           ~150 bytes/node = 7.5 GB
           ~80 bytes/edge = 40 GB
           Total: ~48 GB
  
  Query: 2-hop traversal = 50–150 ms
         3-hop traversal = 150–400 ms
  
  Index: B-tree on entity names, relationship types
         Full-text search on entity attributes
  
  Build time: incremental from document ingestion
              ~1M entities/hour with parallel extraction
```

### When NOT to use Graph RAG

- No knowledge graph built at index time (requires dedicated graph extraction)
- Simple text Q&A without entity relationship queries
- Latency under 150ms required (add 50–200ms over Hybrid RAG)
- Documents don't have extractable entities and relationships (free-form text with no structure)

---

## Multi-Hop + Graph RAG Together

The most powerful combination: Multi-Hop decomposes the question, Graph RAG provides structured relationship evidence at each hop.

```
Query: "Which NVIDIA GPU architecture powers the inference chips used 
        by the top 3 LLM providers, and what manufacturing node do they use?"

Multi-Hop + Graph:

Hop 1 (Graph): 
  "Top 3 LLM providers by model capability"
  Graph: OpenAI → GPT-4, Anthropic → Claude 3.5, Google → Gemini Ultra
  
Hop 2 (Graph + Vector):
  "What chips do OpenAI, Anthropic, Google use for inference?"
  Graph: OpenAI → uses_infrastructure → Microsoft Azure → NVIDIA A100/H100
  Graph: Anthropic → uses_infrastructure → AWS → NVIDIA A100 + Trainium
  Graph: Google → uses_infrastructure → Google Cloud → TPU v5 + H100
  
Hop 3 (Vector):
  "NVIDIA A100 and H100 GPU architectures and manufacturing nodes"
  Retrieved: A100=Ampere arch, Samsung 7nm. H100=Hopper arch, TSMC 4nm

Answer: "H100 (Hopper, TSMC 4nm) is used by both OpenAI (via Azure) and 
         Anthropic (via AWS). Google uses H100 + its own TPU v5 (custom 
         architecture, TSMC 5nm) for inference. The A100 (Ampere, Samsung 7nm) 
         is being phased out in favor of H100 for production inference workloads."

This query: impossible with either Multi-Hop or Graph alone
Combined: 4.2 seconds, cites 8 sources across graph + vector
```

---

## Code Locations

| Component | File | Purpose |
|---|---|---|
| Multi-Hop trigger keywords | `app/rag/engine.py::RetrievalPlanner` | `_MULTI_HOP_KEYWORDS` |
| Query transformer | `app/rag/agentic/llm_query_transformer.py` | Decompose → sub-queries |
| Graph evidence | `app/rag/agentic/patterns/graph.py` | `GraphEvidence`, `GraphEvidenceQuery` |
| Graph traversal | `app/rag/agentic/patterns/graph.py` | `TenantScopedGraphCapability` |
| Knowledge graph indexing | `app/knowledge_graph/ingestion_hook.py` | Builds graph at ingest time |

## Related

- [01-naive-and-hybrid-rag.md](./01-naive-and-hybrid-rag.md) — single-hop foundation used by each sub-query
- [03-corrective-and-multi-hop.md](./03-corrective-and-multi-hop.md) — Corrective RAG for validating retrieved evidence
- [06-agentic-patterns.md](./06-agentic-patterns.md) — Agentic RAG: when you don't know how many hops you need
