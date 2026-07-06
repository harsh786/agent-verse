# AgentVerse — World-Class Agentic RAG Design

**Author:** Platform Architecture  
**Date:** 2026-07-07  
**Status:** APPROVED FOR IMPLEMENTATION  
**Replaces:** Ad-hoc RAG wiring in `app/agent/graph.py`

---

## 1. Executive Summary

AgentVerse currently has a **pre-baked RAG pipeline** — retrieval fires once before planning and once per step, but the agent cannot choose what to search, when to search, or how many times to search. This is standard RAG wrapped around an agent, not truly agentic RAG.

This document defines the architecture to make AgentVerse a **fully agentic RAG system** where:
- The agent drives retrieval as an explicit tool call
- Retrieval is iterative, strategy-adaptive, and query-reformulating
- Every degradation path (no knowledge base, no embedder, no provider) is handled gracefully with transparent fallbacks
- Multiple RAG strategies (vector, graph, web, memory, hybrid) are unified under a single `RetrieverTool` the agent controls

---

## 2. Current State Analysis

### 2.1 What exists today

```
graph.py (LangGraph nodes)
├── _node_rag_retrieval       ← Layer 1: pre-planning, fires once
│   ├── ExecMemory recall
│   ├── LongTermMemory pgvector recall
│   ├── KnowledgeStore hybrid search (vector + FTS + trigram, RRF)
│   └── RRF engine (pgvector + BM25 + trigram)
├── _node_execute
│   └── smart_context_fetch  ← Layer 2: per-step, fires per step
└── _node_verify              ← No RAG — verifier is blind to sources
```

```
app/rag_platform/ (standalone API)
├── RAGRetriever              ← Layer 3: used only by /rag-platform/query API
│   ├── DIRECT (vector)
│   ├── MULTI_HOP
│   ├── HYDE
│   ├── GRAPH (KG expansion)
│   └── MULTIMODAL
└── Reranker + CitationVerifier
```

```
app/tools/web_search.py       ← Exists but NOT wired to agent graph
app/knowledge_graph/          ← Exists but only used in Layer 1 GRAPH strategy
```

### 2.2 Critical gaps

| Gap | Consequence |
|---|---|
| RAG is pre-baked, not a tool | Agent cannot decide to search; cannot search mid-step |
| No re-retrieval on verification failure | Agent replans blindly without new context |
| Web search not wired to agent | If KB is empty, agent uses only parametric LLM knowledge |
| No iterative/multi-turn retrieval | Complex multi-hop questions get a single retrieval attempt |
| No query reformulation on miss | Empty result set stays empty; no retry with different query |
| smart_context_fetch returns "" without embedder | Per-step RAG is silently skipped with no degradation signal |
| No retrieval confidence signal to planner | Planner doesn't know if it has good context or none |
| No explicit "context exhausted" routing | Agent keeps replanning even when more context would help |

---

## 3. Target Architecture: Fully Agentic RAG

### 3.1 Core Principle

> **The agent owns retrieval.** It decides what to search, when to search, which strategy to use, and whether retrieved context is sufficient before proceeding. The system provides a rich `RetrieverTool` the agent calls explicitly, not a hidden pre-step.

### 3.2 Architecture Diagram

```
Goal submitted
      │
      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    AGENTVERSE AGENTIC RAG LOOP                       │
│                                                                       │
│  ┌─────────────┐                                                      │
│  │  INITIALIZE │  Build RAG context capsule with source inventory     │
│  └──────┬──────┘  (lists available: KB collections, web, memory, KG) │
│         │                                                             │
│         ▼                                                             │
│  ┌─────────────┐                                                      │
│  │ RAG_PRIME   │  Mandatory first retrieval across ALL source types   │
│  │  (new node) │  → memory, KB, web (if KB empty), KG                │
│  │             │  → sets rag_capsule in state                        │
│  └──────┬──────┘                                                      │
│         │                                                             │
│         ▼                                                             │
│  ┌─────────────┐                                                      │
│  │    PLAN     │  Planner sees: goal + rag_capsule + source_inventory │
│  │             │  → generates steps WITH optional search directives   │
│  └──────┬──────┘  → e.g. "Step 1: [SEARCH: 'X'] then analyse"       │
│         │                                                             │
│         ▼                                                             │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                         EXECUTE LOOP                             │ │
│  │  For each step:                                                  │ │
│  │   1. Parse step for [SEARCH:...] directives                     │ │
│  │   2. If directive found → call RetrieverTool(query, strategy)   │ │
│  │   3. If no directive → smart_context_fetch with query reform.   │ │
│  │   4. If retrieval score < threshold → try web_search fallback   │ │
│  │   5. Inject context into executor prompt                        │ │
│  │   6. Execute with gpt-5.2                                       │ │
│  │   7. Track: retrieval_hits, source, confidence per step         │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│         │                                                             │
│         ▼                                                             │
│  ┌─────────────┐                                                      │
│  │    VERIFY   │  Verifier sees: steps + outputs + citations         │
│  │             │  → if fail reason = "missing context":             │
│  │             │     route to RAG_REMEDIATE (not replan)             │
│  └──────┬──────┘                                                      │
│         │                                                             │
│    ┌────┴─────────────────────────┐                                  │
│    │                              │                                   │
│    ▼                              ▼                                   │
│  complete                   RAG_REMEDIATE (new node)                  │
│                              │  → formulate remediation queries       │
│                              │  → search with broader strategy        │
│                              │  → inject into next plan iteration     │
│                              └──────────────→ plan                   │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.3 New Graph Nodes

```
initialize → rag_prime → [think?] → plan → execute → verify
                                              ↑           │
                                              │      rag_remediate
                                              └───────────┘
```

| Node | Purpose | New? |
|---|---|---|
| `rag_prime` | Comprehensive first retrieval across all sources | **NEW** |
| `rag_remediate` | Targeted re-retrieval when verification fails due to context gap | **NEW** |
| `execute` | Now calls `RetrieverTool` explicitly per step | **UPGRADED** |
| `verify` | Now receives citations + source manifest | **UPGRADED** |

---

## 4. Core Component: RetrieverTool

### 4.1 Interface

```python
class RetrieverTool:
    """The single unified retrieval interface for the agent.
    
    The agent calls this as an explicit tool. It selects the best 
    strategy, executes retrieval, handles degradation, and returns
    structured results the agent can reason about.
    """
    
    async def retrieve(
        self,
        query: str,
        *,
        strategy: str = "auto",          # auto | vector | graph | hyde | web | memory | hybrid
        collection_id: str | None = None, # specific collection or all
        top_k: int = 5,
        min_confidence: float = 0.3,      # below this → trigger fallback
        tenant_ctx: TenantContext,
        step_context: str = "",           # current step for query refinement
        goal_context: str = "",           # original goal for context
        allow_web_fallback: bool = True,  # allow web search if KB empty/low confidence
        allow_reformulation: bool = True, # auto-reformulate on empty results
        max_reformulation_attempts: int = 2,
    ) -> RetrievalResult:
        ...
```

### 4.2 Strategy Selection Logic

```
AUTO strategy resolution:
  ┌─ KB has documents? ─────────────────────────────────────────────┐
  │   YES                                 NO                         │
  │   ├─ multi-hop keywords?  →  HYBRID   └─ web available? ──┐     │
  │   ├─ graph keywords?      →  GRAPH+                        │     │
  │   ├─ code/technical?      →  VECTOR+HYDE                   YES   │
  │   ├─ recent events?       →  WEB                           │     │
  │   └─ default              →  HYBRID   ┌──────────────────┘     │
  │                                       └─ WEB (web_search)        │
  └──────────────────────────────────────────────────────────────────┘

Fallback chain (in order):
  HYBRID → GRAPH expansion → HYDE → WEB → LTM → parametric LLM knowledge
```

### 4.3 Degradation Prevention — ALL Cases Handled

| Situation | Detection | Behaviour |
|---|---|---|
| No knowledge base | `collection_count == 0` | Skip vector search, go directly to web_search or LTM |
| No embedder | `embedder is None` | Use lexical-only (FTS + trigram), emit `retrieval_mode: lexical_only` |
| No web search | `SEARXNG_URL` not set | Use DuckDuckGo Instant Answer API (already in web_search.py) |
| Empty search results | `len(results) == 0` | Query reformulation → try 2 alternate phrasings |
| Low confidence results | `avg_score < min_confidence` | Widen search, then web fallback |
| All sources exhausted | All fallbacks empty | Return `RetrievalResult(confidence=0.0, source="parametric")` — agent knows to rely on own knowledge |
| Network error | `httpx.TimeoutError` | Return cached results if any, else parametric |
| RLS/permission error | DB error | Graceful empty result with warning |

**Key principle:** The agent is ALWAYS told what happened — never a silent empty string. Every `RetrievalResult` includes `source`, `confidence`, `strategy_used`, and `fallback_used`.

---

## 5. RAG Sources Inventory

AgentVerse should use ALL available sources, not just the knowledge base:

```
Source 1: KnowledgeStore (tenant documents)
  ├── Vector: pgvector ANN (HNSW) — cosine similarity
  ├── Lexical: PostgreSQL FTS (tsvector + ts_rank_cd)
  ├── Fuzzy: pg_trgm trigram similarity
  └── Fusion: RRF (Reciprocal Rank Fusion) across all 3
  
Source 2: KnowledgeGraph (entity/relationship network)
  ├── Entity nodes from ingested documents
  ├── BFS path expansion from seed entities
  └── Community detection for topic clustering

Source 3: ExecutionMemory (past winning plans)
  ├── Embedding-based similarity to current goal
  └── BM25 keyword matching fallback

Source 4: LongTermMemory (cross-session learnings)
  ├── pgvector semantic search
  └── Structured facts and skills

Source 5: WebSearch (SearxNG / DuckDuckGo)
  ├── Current information not in KB
  ├── Documentation lookups
  └── Fact verification

Source 6: Parametric LLM knowledge (zero-retrieval baseline)
  ├── Used when ALL other sources return empty/low confidence
  ├── Agent explicitly told: "No external context available. Using model knowledge."
  └── Confidence marked as 0.0 — verifier knows to be conservative
  
Source 7: RAG Platform API (existing standalone)
  ├── DIRECT, MULTI_HOP, HYDE, GRAPH, MULTIMODAL strategies
  └── Now called BY RetrieverTool, not separately
```

---

## 6. Agentic Retrieval Behaviours

### 6.1 Iterative Retrieval

The agent can call the RetrieverTool **multiple times per step**:

```
Step: "Research the impact of quantum computing on cryptography"
  
  Iteration 1: retrieve("quantum computing cryptography", strategy=hybrid)
    → 3 chunks found, confidence=0.72
    
  Iteration 2: retrieve("post-quantum encryption standards NIST", strategy=web)
    → 5 results from web, confidence=0.88
    
  Iteration 3: retrieve("RSA quantum vulnerability timeline", strategy=hyde)
    → 4 chunks found, confidence=0.65
    
  Final context: 12 chunks fused, reranked, top 5 injected into executor
```

### 6.2 Query Reformulation on Miss

```python
# First attempt
results = retrieve("XYZ concept")  # → empty

# Auto-reformulate: extract key terms, rephrase
alternate_queries = reformulate(original="XYZ concept", attempts=2)
# → ["definition of XYZ in technical context"]
# → ["XYZ overview and applications"]

results_2 = retrieve(alternate_queries[0])  # → hits
```

### 6.3 Re-retrieval on Verification Failure

```
verify() → {success: false, reason: "insufficient context about X"}
           ↓
     _route() detects reason contains "context" or "information" or "unclear"
           ↓
     rag_remediate node fires
           ↓
     extract missing_topic from verification_feedback
           ↓
     retrieve(missing_topic, strategy=auto, allow_web_fallback=True)
           ↓
     inject into next plan iteration as [Remediation context]
           ↓
     plan node replans WITH new context
```

### 6.4 RAG-Driven Planning

The planner is told WHAT sources are available, so it can generate steps that explicitly use them:

```
Planner system prompt (injected):
  Available retrieval sources:
  - knowledge_base: 3 collections (247 documents, topics: DevOps, Architecture, APIs)
  - knowledge_graph: 1,234 entities
  - web_search: available (SearxNG)
  - execution_memory: 12 similar past goals

  You may include retrieval directives in steps:
    [SEARCH:kb:"your query"] - search knowledge base
    [SEARCH:web:"your query"] - search the web  
    [SEARCH:graph:"entity name"] - expand knowledge graph
    [SEARCH:memory:"goal description"] - recall past plans
```

### 6.5 Citation Threading

Every step output is tagged with its sources:

```python
@dataclass
class StepResult:
    description: str
    output: str
    # --- NEW: RAG provenance ---
    citations: list[Citation] = field(default_factory=list)
    retrieval_confidence: float = 0.0
    sources_used: list[str] = field(default_factory=list)  # kb, web, memory, parametric
    retrieval_strategy: str = ""
```

The verifier sees citations alongside outputs — it can reject steps where the output contradicts its cited sources.

---

## 7. Graceful Degradation — Zero-Knowledge Operation

When no knowledge base is configured (new tenant, agent with no collections), the system must NOT silently return empty strings. Instead:

### 7.1 Source Inventory at Startup

```python
# In _node_rag_prime:
source_inventory = {
    "kb_collections": len(collections),        # 0 if none
    "kb_total_chunks": total_chunks,           # 0 if none  
    "kg_nodes": kg_node_count,                 # 0 if no KG
    "ltm_entries": ltm_count,                  # 0 if no LTM
    "exec_memory_plans": exec_memory_count,    # 0 if none
    "web_available": bool(SEARXNG_URL or DDG), # True/False
    "embedder_available": bool(embedder),      # True/False
}
agent_state.context["source_inventory"] = source_inventory
```

### 7.2 Degradation Transparency

Injected into planner prompt when KB is empty:

```
[Context Availability Notice]
Knowledge base: EMPTY (0 documents)
Web search: AVAILABLE via SearxNG
Execution memory: 3 similar plans recalled
Long-term memory: EMPTY

IMPORTANT: No tenant documents are available. For factual questions, 
use [SEARCH:web:"..."] directives. For internal knowledge, the model's 
parametric knowledge will be used. Mark confidence as LOW in your plan.
```

### 7.3 Web Search Auto-Activation

When KB is empty AND web search is available:

```python
if source_inventory["kb_total_chunks"] == 0 and source_inventory["web_available"]:
    # Auto-fire web search with the goal as query
    web_results = await web_search_tool.search(goal, num_results=5)
    context_parts.append(f"[Web search — used because knowledge base is empty]\n{web_results}")
    agent_state.context["web_search_active"] = True
    agent_state.context["web_search_auto"] = True
```

### 7.4 Parametric Baseline (Last Resort)

When ALL sources return empty:

```python
parametric_context = (
    "No external context available from any retrieval source.\n"
    "The model is operating on parametric (training) knowledge only.\n"
    f"Retrieval confidence: 0.0\n"
    f"Sources attempted: {', '.join(attempted_sources)}\n"
    "Mark outputs with [PARAMETRIC] and low confidence. "
    "The verifier will flag unsupported claims."
)
agent_state.context["retrieval_mode"] = "parametric_only"
```

---

## 8. Multi-Strategy RAG Integration Map

All existing RAG implementations, unified:

```
RetrieverTool (new unified interface)
         │
    ┌────┴────────────────────────────────────────────────────────┐
    │    strategy router                                           │
    └────────────────────────────────────────────────────────────┘
         │               │                │              │
         ▼               ▼                ▼              ▼
   KnowledgeStore    RAGPlatform      WebSearch      Memory
   hybrid_search_db  (existing)      web_search     (LTM+Exec)
   (vector+FTS+tgm)  retriever.py    web_search.py  memory/
         │               │
         ├─ DIRECT        ├─ DIRECT
         ├─ LEXICAL       ├─ MULTI_HOP
         ├─ VECTOR        ├─ HYDE
         └─ HYBRID        ├─ GRAPH
                          └─ MULTIMODAL
         │
         ▼
   KnowledgeGraph          ← used by GRAPH strategy in both
   kg_store.query_nodes()
   community_detection()
         │
         ▼
   Reranker                ← post-retrieval reranking
   reranker.rerank()
         │
         ▼
   CitationVerifier        ← post-synthesis verification
   citation_verifier.verify()
```

---

## 9. New LangGraph Node Specifications

### 9.1 `_node_rag_prime` (replaces `_node_rag_retrieval`)

```python
async def _node_rag_prime(self, state: GraphState) -> dict:
    """
    Comprehensive first retrieval before planning.
    
    Fires across ALL available sources in parallel (asyncio.gather).
    Returns rag_capsule: rich context object with source manifest,
    confidence scores, and citations — NOT a plain string.
    
    Unlike the current _node_rag_retrieval:
    - Fires sources in PARALLEL (not sequential)
    - Uses RetrieverTool (unified interface)
    - Builds source_inventory for planner awareness
    - Activates web_search automatically if KB is empty
    - Emits retrieval_started / retrieval_complete SSE events
    """
```

### 9.2 `_node_rag_remediate` (new)

```python
async def _node_rag_remediate(self, state: GraphState) -> dict:
    """
    Targeted re-retrieval triggered when verification fails due to 
    context gap (not logic error).
    
    Triggered by _route() when verification_feedback contains signals:
      "insufficient", "unclear", "no information", "cannot determine",
      "lack of context", "not mentioned", "unknown"
      
    Strategy:
    1. Extract missing topics from verification_feedback using LLM
    2. Formulate targeted queries for each missing topic
    3. Search with BROADER strategy (web if KB was empty before)
    4. Inject as [Remediation context: iteration N] into next plan
    5. Increment remediation_count (max 2 to prevent loops)
    
    If remediation_count >= 2: route to replan (not remediate again).
    """
```

### 9.3 Upgraded `_route`

```python
def _route(self, state: GraphState) -> str:
    ...
    # NEW: context-gap detection before replanning
    feedback = agent_state.verification_feedback or ""
    _context_gap_signals = {
        "insufficient", "unclear", "no information", "cannot determine",
        "lack of context", "not mentioned", "unknown", "not found",
        "need more", "more context", "cannot verify"
    }
    _is_context_gap = any(s in feedback.lower() for s in _context_gap_signals)
    _remediation_count = agent_state.context.get("remediation_count", 0)
    
    if _is_context_gap and _remediation_count < 2:
        agent_state.context["remediation_count"] = _remediation_count + 1
        return "rag_remediate"  # NEW routing path
    
    # Original replan path
    return "replan"
```

---

## 10. World-Class Practices Checklist

### Retrieval Quality
- [x] Multi-leg retrieval with RRF fusion (already implemented)
- [ ] **Parallel source retrieval** (asyncio.gather across KB, KG, memory)
- [ ] **Query reformulation** (2 automatic reformulations on empty result)
- [ ] **Re-retrieval on verification failure** (rag_remediate node)
- [ ] **Confidence-gated fallback** (web search if score < threshold)
- [x] Cross-encoder reranking (already in reranker.py)
- [x] Citation verification (already in reranker.py)

### Degradation Prevention  
- [ ] **Web search auto-activation** when KB is empty
- [ ] **Source inventory manifest** in planner prompt
- [ ] **Parametric baseline** as transparent last resort
- [ ] **No silent empty strings** — every retrieval result is informative
- [ ] **Lexical fallback** when no embedder (FTS + trigram only)
- [ ] **DuckDuckGo fallback** when SearxNG unavailable

### Agent Control
- [ ] **RetrieverTool as explicit tool** agent can call N times
- [ ] **[SEARCH:...] directives** in planner-generated steps
- [ ] **Iterative multi-turn retrieval** per step
- [ ] **Source selection** by agent (which source to use per step)

### Observability
- [ ] **RAG trace** in AgentRunTrace (which sources, confidence, latency)
- [ ] **Per-step retrieval events** in SSE stream
- [ ] **retrieval_dashboard** in observability UI
- [ ] **Cache hit rate** tracking (already started in LLMResponseCache)

### Context Management
- [ ] **Relevance filtering** — only inject chunks with score > 0.4
- [ ] **Token budget** — cap injected context at 2000 tokens
- [ ] **Context deduplication** — don't inject same chunk twice per goal
- [ ] **Chunk ranking** per step (not just per goal)

---

## 11. Implementation Phases

### Phase A: Foundation (Week 1) — Highest Impact
1. Create `app/rag/retriever_tool.py` — `RetrieverTool` unified interface
2. Wire web_search into RetrieverTool fallback chain
3. Add source_inventory to planner prompt
4. Add auto web_search activation when KB is empty
5. Replace `_node_rag_retrieval` with `_node_rag_prime` (parallel sources)

### Phase B: Agentic Control (Week 2)
6. Add `rag_remediate` node to LangGraph
7. Upgrade `_route` to detect context gaps → route to `rag_remediate`
8. Add `[SEARCH:...]` directive parsing in execute node
9. Add query reformulation to RetrieverTool (2 auto-attempts)
10. Add iterative retrieval support (multiple calls per step)

### Phase C: Quality (Week 3)
11. Add confidence-gated fallback chain
12. Thread citations through StepResult
13. Upgrade verifier to receive + reason about citations
14. Add retrieval metrics to AgentRunTrace
15. Build retrieval dashboard in observability UI

### Phase D: Advanced (Week 4)
16. LLM-based missing-topic extraction in rag_remediate
17. Parallel source retrieval with asyncio.gather
18. Context token budget management
19. Chunk deduplication across retrieval iterations
20. A/B testing RAG strategies per goal type

---

## 12. Files to Create / Modify

### New files
```
app/rag/retriever_tool.py          ← RetrieverTool unified interface
app/rag/source_inventory.py        ← SourceInventory, build_source_manifest()
app/rag/query_reformulator.py      ← QueryReformulator, reformulate()
app/rag/context_manager.py         ← ContextBudgetManager, dedup, token cap
```

### Modified files
```
app/agent/graph.py
  - _node_rag_retrieval → _node_rag_prime (parallel, RetrieverTool)
  - _node_execute → parse [SEARCH:...] directives, call RetrieverTool
  - _node_verify → pass citations to verifier
  - _route → detect context gaps, route to rag_remediate
  - + _node_rag_remediate (new node)
  
app/agent/state.py
  - AgentState: add rag_capsule, source_inventory, remediation_count
  - StepResult: add citations, retrieval_confidence, sources_used

app/agent/prompts.py
  - PLANNER_SYSTEM: add source inventory section, [SEARCH:...] instructions
  - VERIFIER_SYSTEM: add citation reasoning instructions
  
app/tools/web_search.py
  - Ensure SearxNG + DuckDuckGo both tested and working
  
app/pipeline/steps.py
  - smart_context_fetch: use RetrieverTool, no silent empty returns
```

### Unchanged (already good)
```
app/rag/engine.py                  ← RRF engine, keep as is
app/rag/semantic_cache.py          ← semantic cache, keep as is  
app/rag_platform/reranker.py       ← reranker, keep as is
app/knowledge_graph/               ← KG, keep as is
app/rag/llm_response_cache.py      ← LLM cache, keep as is
```

---

## 13. Metrics for Success

| Metric | Current | Target |
|---|---|---|
| RAG retrieval rate (% goals that retrieve anything) | ~60% | >95% |
| Web search fallback activation (when KB empty) | 0% | 100% |
| Context gap re-retrieval (% failures that try rag_remediate) | 0% | >80% |
| Retrieval confidence (avg score of injected chunks) | Unknown | >0.6 |
| Goal success rate with empty KB | ~40% | >75% (web fallback) |
| Silent empty context returns | ~40% of steps | 0% |
| Citation grounding rate | ~50% | >85% |
| Retrieval-to-plan latency | ~500ms | <300ms (parallel) |

---

## 14. Anti-patterns to Avoid

| Anti-pattern | Why Bad | Correct Pattern |
|---|---|---|
| `if knowledge_store is None: return ""` | Silent degradation | Return `RetrievalResult(confidence=0, source="none_available")` |
| Fire retrieval after planner already ran | Context too late | Fire BEFORE planner so context shapes the plan |
| One retrieval strategy per run | Misses documents | Try multiple strategies, fuse with RRF |
| Inject all retrieved chunks | Token overflow | Cap at 2000 tokens, rank by step relevance |
| Retry replan on context gap | Ignores root cause | Route to rag_remediate first |
| Use raw goal as retrieval query | Semantic drift per step | Reformulate query per step description |
| Cache error responses | Poisons future runs | Guard: skip cache if response is error |

---

## 15. Appendix: Existing RAG Code Map

| File | Purpose | Status |
|---|---|---|
| `app/rag/engine.py` | RRF hybrid search (pgvector + FTS + trigram) | Good, keep |
| `app/rag/store.py` | KnowledgeStore CRUD + search | Good, keep |
| `app/rag/semantic_cache.py` | Step-level semantic cache (cosine sim) | Good, keep |
| `app/rag/llm_response_cache.py` | LLM response cache (planning/verification) | Good, keep, error guard added |
| `app/rag/chunker.py` | Document chunking (sliding window) | Good, keep |
| `app/rag_platform/retriever.py` | Multi-strategy retriever (standalone) | Integrate via RetrieverTool |
| `app/rag_platform/query_planner.py` | Strategy auto-selection | Use inside RetrieverTool |
| `app/rag_platform/reranker.py` | Cross-encoder reranking + citation verifier | Use inside RetrieverTool |
| `app/tools/web_search.py` | SearxNG + DuckDuckGo | Wire into RetrieverTool |
| `app/knowledge_graph/store.py` | Entity/relationship graph | Use in GRAPH strategy |
| `app/pipeline/steps.py` | `smart_context_fetch` per step | Replace with RetrieverTool |
| `app/agent/graph.py` | `_node_rag_retrieval` | Replace with `_node_rag_prime` |
