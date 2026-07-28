# AgentVerse: Memory Systems, Embedding Architecture, and Prompt Builder

> **Coverage:** `app/memory/`, `app/state_runtime/`, `app/embedding/`, `app/context/`, `app/rag/`
> **Audience:** Engineers building on or extending AgentVerse's cognitive layer.

---

## Table of Contents

- [Part A — Memory Systems](#part-a--memory-systems)
  - [1. Working Memory](#1-working-memory--agentstatecontent)
  - [2. Session Memory](#2-session-memory)
  - [3. Execution Memory](#3-execution-memory)
  - [4. Long-Term Memory](#4-long-term-memory-ltm)
  - [5. Episodic Memory](#5-episodic-memory)
  - [6. Procedural Memory](#6-procedural-memory)
  - [7. Reflexion Memory](#7-reflexion-memory-lesson-memory)
  - [8. Semantic Cache](#8-semantic-cache)
  - [9. Knowledge Graph Memory](#9-knowledge-graph-memory)
  - [10. Tool Reliability Memory](#10-tool-reliability-memory)
  - [Memory Scoping Matrix](#memory-scoping-matrix)
  - [Write and Recall Flows](#write-and-recall-flows)
  - [Memory Safety](#memory-safety)
- [Part B — Embedding Architecture](#part-b--embedding-architecture)
  - [Provider Abstraction](#1-provider-abstraction)
  - [Available Providers](#2-available-embedding-providers)
  - [EmbeddingRouter](#3-embeddingrouter)
  - [EmbeddingOrchestrator](#4-embeddingorchestrator)
  - [DimensionPolicy](#5-dimensionpolicy)
  - [DriftMonitor](#6-embeddingdriftmonitor)
  - [Dimension Discovery](#7-dimension-discovery-c6-fix)
- [Part C — Prompt Builder Architecture](#part-c--prompt-builder-architecture)
  - [ContextPipeline](#1-contextpipeline)
  - [PromptBuilder](#2-promptbuilder)
  - [System Prompts](#3-system-prompts)
  - [OutputContractBuilder](#4-outputcontractbuilder)
  - [ToolPromptBuilder](#5-toolpromptbuilder)
  - [SchemaAwarePromptInjector](#6-schemaawarepromptinjector)
  - [Prompt Safety](#7-prompt-safety)

---

## Part A — Memory Systems

AgentVerse maintains **ten distinct memory subsystems** that together give an agent the ability to reason from past experience, avoid repeating mistakes, and accumulate cross-session domain knowledge. Each subsystem occupies a different position on the time axis (ephemeral → session → persistent) and the scope axis (goal → tenant → agent).

---

### 1. Working Memory — `AgentState.context`

**File:** `app/agent/state.py`  
**Lifetime:** Single goal execution  
**Owner:** The LangGraph graph; cleared by `GoalService` when the goal reaches a terminal state

Working memory is the scratchpad for one goal run. It is the `context: dict[str, Any]` field of `AgentState`. Unlike every other memory type, it is **not a separate class** — it is the agent's live runtime state dictionary.

#### Keys written during execution

| Key | Written by | Type | Purpose |
|-----|-----------|------|---------|
| `_runtime_profile` | `_node_plan` | `GoalRuntimeProfile` | Complexity, risk, budget plan |
| `_active_rag_strategy` | `_node_plan` | `str` | Which RAG pattern is active (`fusion_rag`, `colbert`, etc.) |
| `_reflexion_lessons` | `ContextPipeline` | `list[str]` | Failure lessons injected into the plan |
| `total_cost_usd` | `_node_execute` | `float` | Accumulated LLM API cost for this goal |
| `_latency_ms` | Step executor | `float` | Per-step latency for cost-aware routing |
| `_pipeline_citations` | `ContextPipeline.run()` | `list[Citation]` | Citations threaded by `CitationThreader` |
| `_executor_context` | `_node_plan` (N9 fix) | `str` | Executor-specific RAG context, stored for reuse |
| `_verifier_context` | `_node_plan` (N9 fix) | `str` | Verifier-specific context from pipeline |

#### Data flow

```
goal submitted
       │
  _node_plan
       ├── RAG search + ContextPipeline.run() ──► context["_pipeline_citations"]
       ├── runtime_profile built ──────────────► context["_runtime_profile"]
       └── reflexion_lessons injected ─────────► context["_reflexion_lessons"]
       │
  _node_execute (per step)
       ├── tool call cost tracked ─────────────► context["total_cost_usd"] += Δ
       └── step latency measured ──────────────► context["_latency_ms"]
       │
  goal completes / fails
       └── AgentState discarded (context GC'd)
```

#### Failure modes

- **Key collision:** Two concurrent steps writing the same key — not possible in the current sequential LangGraph design, but would silently overwrite in multi-agent scenarios.
- **Cost drift:** If `total_cost_usd` is never initialized before increment, a `KeyError` will surface. The executor guards against this with `.get("total_cost_usd", 0.0)`.

---

### 2. Session Memory

**File:** `app/state_runtime/session_memory.py` — `SessionMemory`  
**Lifetime:** One goal's duration  
**Scope:** goal-scoped (keyed by `goal_id`)

`SessionMemory` is a simple key-value accumulator that stores named facts for the duration of a single goal execution. Unlike `AgentState.context`, it is not coupled to the LangGraph state object — it lives on `app.state.session_memory` (a singleton on the FastAPI application) and is accessed by goal ID.

```python
class SessionMemory:
    def add(self, *, goal_id: str, key: str, value: Any) -> None: ...
    def get(self, *, goal_id: str) -> list[dict[str, Any]]: ...
    def clear(self, goal_id: str) -> None: ...
```

The store is wired into `ContextPipeline.run()` via the `session_memory` parameter. The pipeline receives a snapshot of `session_memory.get(goal_id=...)` and injects it into the planner context under the "Session context" section.

**Cleared by:** `GoalService` calls `session_memory.clear(goal_id)` in the finally block of goal completion, ensuring no stale session data leaks between goal re-runs.

**Isolation:** Keys are partitioned by `goal_id`. There is no tenant-level separation inside this class because goals already belong to tenants — the goal_id namespace provides implicit isolation.

---

### 3. Execution Memory

**File:** `app/memory/execution.py` — `ExecutionMemory`  
**Lifetime:** Cross-session (DB-backed), in-process LRU cap of 100 per tenant  
**Scope:** Tenant-scoped

Execution memory records **which plans worked** (and which failed) across goal runs. At planning time, the planner LLM receives up to 3 similar past successful plans as examples.

#### Storage model

```
In-memory: _plans[tenant_id]  →  list of {goal, plan: list[str]}
           _failures[tenant_id] → list of {goal, failed_step, error}
DB:        execution_memory table (id, tenant_id, goal_text, plan::jsonb, success, created_at)
```

#### Key methods

| Method | Description |
|--------|-------------|
| `record(goal, plan, tenant_ctx)` | Synchronous in-memory write |
| `record_async(goal, plan, success, tenant_id, db)` | In-memory + DB write with ON CONFLICT DO NOTHING |
| `record_failure_async(goal, error, tenant_id, db)` | Records failed attempt (success=FALSE) |
| `recall(goal_hint, tenant_ctx)` | Synchronous keyword match in `_plans` |
| `recall_async(goal_hint, tenant_id, db)` | DB query (recent successful plans), keyword-filtered |
| `load_from_db(tenant_id, db, limit=100)` | Seeds `_plans` from DB on startup |

#### Recall algorithm

`recall_async()` queries the DB for the 3×`limit` most recent successful plans ordered by `created_at DESC`, then applies a keyword filter: the goal hint is tokenized (first 5 words) and any plan whose `goal_text` contains at least one matching word is included. This is intentionally simple — the goal is to bias the planner, not to do precision recall.

#### Failure modes

- DB write failures are silently swallowed (logged at WARNING). In-memory write always succeeds first, so the same-session recall still works.
- In-memory cap: `_memories[tid]` is trimmed to the last 100 entries. Older entries are only recoverable from DB.

---

### 4. Long-Term Memory (LTM)

**File:** `app/memory/long_term.py` — `LongTermMemoryStore`, `LongTermMemory`  
**Lifetime:** Permanent (DB-backed)  
**Scope:** Tenant-scoped

LTM stores **cross-session learnings** extracted from completed goals. The primary use is to bias future planning prompts with domain knowledge the agent has accumulated over time.

#### Data model

```python
@dataclass
class LongTermMemory:
    content: str
    source_goal_id: str
    memory_type: str   # "tool_preference" | "domain_fact" |
                       # "failure_pattern" | "success_pattern" | "rpa_extraction"
    confidence: float  # 0.0–1.0
    memory_id: str     # uuid4().hex
    created_at: str    # ISO-8601
    tags: list[str]
```

#### Storage

```sql
-- Table: long_term_memory
id            TEXT PRIMARY KEY
tenant_id     TEXT NOT NULL
content       TEXT
memory_type   TEXT
confidence    FLOAT
source_goal_id TEXT
tags          JSONB
embedding     VECTOR  -- pgvector column; nullable
created_at    TIMESTAMPTZ
```

The `embedding` column is populated by `store_async()` when an `embedder` is provided. When absent, the row is still inserted (without embedding) and falls back to keyword recall.

#### Key methods

| Method | Sync/Async | Storage | Description |
|--------|-----------|---------|-------------|
| `store(memory, tenant_ctx)` | Sync | In-memory | Appends to `_memories[tenant_id]` |
| `store_async(memory, tenant_ctx, db, embedder)` | Async | In-memory + DB | Embeds content, upserts row |
| `recall(query, tenant_ctx, top_k)` | Sync | In-memory | Keyword scoring (word overlap count) |
| `recall_async(query, tenant_ctx, top_k, db, embedder)` | Async | pgvector ANN | Cosine similarity via `embedding <=> CAST(:qvec AS vector)` |
| `extract_from_goal_async(goal, result, tenant_ctx, db, embedder)` | Async | Both | Auto-creates `success_pattern` memory |
| `store_rpa_extraction(url, text, goal_id, tenant_ctx, ...)` | Async | Both | Stores RPA-scraped page content in chunks |

#### Recall path

1. If `db` and `embedder` are both available: embed the query → pgvector `ORDER BY embedding <=>` (cosine distance, ascending) `LIMIT :k` → hydrate `_memories` cache.
2. If either is missing: fall back to `recall()` (in-memory keyword scoring).

#### RPA chunking

`store_rpa_extraction()` splits content over `chunk_size=500` chars with `overlap=50`. Short content under 50 chars is discarded as noise. This is the bridge between the RPA subsystem and LTM — scraped page facts become semantically searchable memories.

---

### 5. Episodic Memory

**File:** `app/memory/episodic.py` — `EpisodicMemoryStore`, `Episode`  
**Lifetime:** Permanent (DB-backed)  
**Scope:** Tenant-scoped  
**DB table:** `episodic_memories` (migration 0089)

Episodic memory records **what happened** during past goal executions — the narrative of the agent's experience.

#### Data model

```python
@dataclass
class Episode:
    episode_id: str
    tenant_id: str
    goal_id: str
    goal_text: str         # First 200 chars of the goal
    action_summary: str    # " → ".join of step descriptions (up to 5 steps)
    outcome: str           # "success" | "failed" | "partial"
    lessons: str           # From verification_feedback (up to 300 chars)
    quality_score: float   # 0.0–1.0 (passed from the verifier)
    steps_count: int
    tools_used: list[str]  # Deduped, first 10 tools
    embedding: list[float] | None  # Embedded goal text
```

#### Record flow

`EpisodicMemoryStore.record()` is called at goal completion (`_node_verify` success branch and the final completion handler in `GoalService`). It:

1. Determines `outcome` from `state.status` (`COMPLETE` → `"success"`, `WAITING_HUMAN` → `"partial"`, else → `"failed"`).
2. Builds `action_summary` from the first 5 step descriptions.
3. Extracts `tools_used` by iterating `step.tool_calls` across all steps.
4. Takes `lessons` from `state.verification_feedback`.
5. Embeds the `goal_text` if `_embedder` is available.
6. Writes to in-memory `_cache[tenant_id]` (capped at 100).
7. Inserts into `episodic_memories` via `embedding::jsonb` cast.

#### Recall algorithm

`recall()` tries DB first, falls back to in-memory keyword scoring. The DB query orders by `quality_score DESC, created_at DESC`, fetches 3× limit, then keyword-filters by token overlap. Final sort: `(-relevance, -quality_score)`.

#### Planner context injection

```python
def format_for_context(episodes: list[Episode]) -> str:
    # Produces:
    # [Episodic memory — similar past experiences:]
    # [Past episode — success]
    # Goal: <first 100 chars>
    # Actions: <first 150 chars>
    # Lesson: <first 200 chars>
```

---

### 6. Procedural Memory

**File:** `app/memory/procedural.py` — `ProceduralMemoryStore`, `Skill`  
**Lifetime:** Permanent (DB-backed)  
**Scope:** Tenant-scoped  
**DB table:** `procedural_memories` (migration 0089)

Procedural memory captures **how to do things** — specifically, which tool sequences reliably solve which goal patterns. It is the agent's skill library.

#### Data model

```python
@dataclass
class Skill:
    skill_id: str
    tenant_id: str
    goal_pattern: str    # Generalized, IDs stripped (e.g. "TICKET" for Jira IDs)
    domain: str          # "jira" | "git" | "database" | "communication" | "web" | "general"
    tool_sequence: list[str]  # Ordered, deduped, max 8 tools
    use_count: int
    success_rate: float  # Running weighted average
    avg_steps_saved: float
```

#### Goal pattern normalization

`_extract_goal_pattern()` strips specifics to produce reusable patterns:

```python
pattern = re.sub(r'\b[A-Z][A-Z0-9]+-\d+\b', 'TICKET', goal)  # Jira IDs → TICKET
pattern = re.sub(r'\b\d+\b', 'N', pattern)                    # Numbers → N
pattern = re.sub(r'"[^"]{1,50}"', 'VALUE', pattern)            # Quoted strings → VALUE
pattern = pattern[:150].strip()
```

This means `"Fix bug PROJ-1234 in version 3.2.1"` and `"Fix bug PROJ-5678 in version 4.0.0"` map to the same pattern `"Fix bug TICKET in version N.N.N"`.

#### Domain inference

`_extract_domain()` scans the tool names for keywords: `jira` → `"jira"`, `github`/`gitlab` → `"git"`, `postgres`/`sql` → `"database"`, `slack`/`email` → `"communication"`, `web`/`browser` → `"web"`, default → `"general"`.

#### Learn flow

`learn()` is called at goal completion alongside episodic `record()`. If a matching `goal_pattern` already exists in the cache, it updates the running success rate:

```
new_success_rate = (old_rate × old_count + (1.0 if success else 0.0)) / (old_count + 1)
```

New skills with zero tool calls are discarded (no value in recording a goal that used no tools).

#### Planner context injection

```python
def format_for_context(skills: list[Skill]) -> str:
    # [Procedural memory — relevant skills for this goal type:]
    #   • [Skill: Fix TICKET in version N] Tool sequence: jira_get → git_branch → jira_update (success rate: 87%, used 12x)
```

---

### 7. Reflexion Memory (Lesson Memory)

**File:** `app/state_runtime/reflexion_store.py` — `ReflexionStore`  
**Lifetime:** Permanent (DB-backed with in-memory hot path)  
**Scope:** Tenant-scoped  
**DB table:** `reflexion_lessons` (migration 0087)

Reflexion memory stores **failure lessons**: structured text that explains why a goal failed and what the agent should do differently next time.

#### Storage model

```
_lessons[tenant_id]  →  deque(maxlen=50)
                          ├── lesson: str
                          ├── source_goal_id: str
                          └── failure_class: str
                               "permission_denied" | "tool_not_found" | "ambiguous_goal" | etc.
```

#### Write path

`ReflexionWirer.maybe_store_async()` is called in the `_node_verify` failure branch with a structured lesson. It delegates to `record_async()` which:

1. Calls `record()` (in-memory deque, always succeeds).
2. If `db_factory` is provided, INSERTs into `reflexion_lessons` with `ON CONFLICT DO NOTHING`.

#### Lazy DB hydration

On the first `recall()` call for a tenant that has no in-memory lessons, the store schedules `load_from_db()` as an `asyncio.ensure_future()`. This is **best-effort, non-blocking** — the current recall returns an empty list, and subsequent recalls benefit from the hydrated data. In synchronous contexts (e.g. test setup), `loop.run_until_complete()` is used instead.

```python
if not lessons and self._db_factory and tenant_id not in self._hydrated_tenants:
    self._hydrated_tenants.add(tenant_id)  # Mark immediately to prevent re-entry
    asyncio.ensure_future(self.load_from_db(...))
```

#### Injection into ContextPipeline

`ContextPipeline.run()` accepts `reflexion_lessons: list[str]` directly. The caller (typically the goal service) is responsible for pre-fetching lessons via `reflexion_store.recall(tenant_id=..., limit=10)` and passing them in.

---

### 8. Semantic Cache

**File:** `app/rag/semantic_cache.py` — `SemanticCache`  
**Lifetime:** L1 (process lifetime), L2 (Redis TTL), L3 (never — it is execution, not storage)  
**Scope:** Tenant-scoped (all Redis keys namespaced by `tenant_id`)

The semantic cache is a **3-layer, similarity-based response cache** that deduplicates LLM calls by finding cached results for semantically equivalent queries rather than only exact string matches.

#### Layer architecture

```
L1: In-process LRU OrderedDict (configurable max size)
      └── Key: (tenant_id, text_hash)  Value: cached_response_str
      └── Hit latency: microseconds

L2: Redis (cross-replica)
      └── Key: agentverse:cache:{tenant_id}:{uuid}
      └── Value: zlib-compressed JSON {response, embedding_binary}
      └── TTL: configurable (default 3600s)
      └── Hit latency: milliseconds

L3: Cold execution (LLM + tool calls)
      └── Triggered when neither L1 nor L2 hits
```

#### True semantic matching

Unlike the v1 hash-based cache, the current implementation embeds each cache key and compares with cosine similarity at a configurable threshold (default ≈ 0.85). This means:

> `"Search GitHub for open issues"` ≈ `"Find open GitHub issues"` → **cache hit**

#### Per-tenant isolation

All Redis keys are prefixed with `agentverse:cache:{tenant_id}:`. A `tenant_index_key = agentverse:cache_index:{tenant_id}` set tracks all entries for O(1) tenant-scoped enumeration and bulk clear.

#### M2 fix — empty warmup entries

The `clear()` method flushes Redis alongside the in-process LRU, preventing stale entries from being warmed up. Empty entries from failed LLM calls are skipped during warmup iteration.

#### Usage in the agent loop

- `_execute_step_with_cache()`: Wraps each step execution with cache lookup/store.
- Batch prefetch: At plan start, all plan step descriptions are embedded together and checked against L2 Redis in one batch pass.

---

### 9. Knowledge Graph Memory

**File:** `app/state_runtime/kg_query_engine.py` — `KGQueryEngine`  
**Storage:** `app/knowledge_graph/store.py` — `KnowledgeGraphStore`  
**Scope:** Tenant-scoped (per `GraphNode.tenant_id`)

The knowledge graph memory provides **structured relational facts** that complement the fuzzy semantic recall of the other memory systems.

#### Graph data model

```
GraphNode:  node_id (str)  label (str)  tenant_id (str)  node_type  confidence
GraphEdge:  source_node_id  target_node_id  edge_type  tenant_id
```

#### Query strategies

`KGQueryEngine` routes each natural language query to one of four strategies based on keyword detection:

| Strategy | Trigger regex | Implementation |
|----------|--------------|----------------|
| `entity` | "related to", "associated with", "similar to" | `query_nodes()` by label search, returns entity facts |
| `path` | "depends on", "requires", "uses", "built on" | `query_nodes()` + `get_edges_for_node()` traversal |
| `impact` | "impact of", "effect of", "root cause" | `query_nodes()` + neighbourhood expansion |
| `community` | (grouped with impact) | Neighbourhood nodes |
| `none` | `^(list|get|fetch|show|count|find)` | Returns empty result, skips KG |

The default strategy when no keyword matches is `entity`.

#### Lazy per-tenant hydration

`KnowledgeGraphStore.query_nodes()` uses `asyncio.ensure_future(load_from_db(tenant_id))` to lazily hydrate the in-memory graph for a tenant on first access.

#### Injection

`KGQueryEngine.query()` returns a `KGQueryResult(facts: list[dict], entities_found: list[str], confidence: float)`. Facts are injected into the `PromptContextBundle.graph_facts` field, which `PromptBuilder.build_planner_context()` renders as the "Knowledge graph context" section.

---

### 10. Tool Reliability Memory

**File:** `app/memory/tool_reliability.py` — `ToolReliabilityStore`  
**Lifetime:** Permanent (DB-backed)  
**Scope:** Tenant+tool-scoped  
**DB table:** `tool_reliability_memory`

Tool reliability memory tracks **per-tool success rates and latencies** so that the `ToolRanker` can deprioritize unreliable tools during planning.

#### Storage model

```sql
-- Upsert on (tenant_id, tool_name)
INSERT INTO tool_reliability_memory
    (tenant_id, tool_name, success_count, failure_count, total_latency_ms, last_used_at)
ON CONFLICT (tenant_id, tool_name) DO UPDATE SET
    success_count  = tool_reliability_memory.success_count  + :sc,
    failure_count  = tool_reliability_memory.failure_count  + :fc,
    total_latency_ms = tool_reliability_memory.total_latency_ms + :lat,
    last_used_at   = NOW()
```

Every tool call result goes through `record(tenant_id, tool_name, success, latency_ms)`. This is a fire-and-forget async call — failures are logged at DEBUG and never propagated.

#### `get_unreliable_tools()`

Returns tools where `success_count + failure_count >= min_calls` AND `success_rate < max_success_rate` (defaults: 5 calls, 70% threshold), ordered by success rate ascending. Used by `ToolRanker` in `app/tool_runtime/tool_ranker.py` to exclude or deprioritize tools with a known failure history.

---

### Memory Scoping Matrix

| Memory System | Tenant | Agent | Goal | Collection | Source |
|---------------|:------:|:-----:|:----:|:----------:|:------:|
| Working Memory | — | — | ✓ | — | — |
| Session Memory | implicit¹ | — | ✓ | — | — |
| Execution Memory | ✓ | — | — | — | — |
| Long-Term Memory | ✓ | — | — | ✓ | ✓ |
| Episodic Memory | ✓ | — | ✓ | — | — |
| Procedural Memory | ✓ | — | — | — | — |
| Reflexion Memory | ✓ | — | — | — | — |
| Semantic Cache | ✓ | — | — | — | — |
| KG Memory | ✓ | — | — | — | — |
| Tool Reliability | ✓ | — | — | — | ✓ |

¹ Session memory is keyed by `goal_id`; goals belong to tenants, providing implicit tenant isolation.

---

### Write and Recall Flows

#### Write flow: goal completion

```
goal COMPLETE or FAILED
          │
          ├── EpisodicMemoryStore.record(state, tenant_ctx, quality_score)
          │         └── in-memory _cache + DB INSERT episodic_memories
          │
          ├── ProceduralMemoryStore.learn(state, tenant_ctx, success)
          │         └── _extract_goal_pattern() → upsert procedural_memories
          │
          ├── LongTermMemoryStore.extract_from_goal_async(goal, result, ...)
          │         └── auto-creates "success_pattern" LTM entry
          │         └── store_async() → embed → upsert long_term_memory
          │
          ├── ExecutionMemory.record_async(goal, plan, success, tenant_id, db)
          │         └── INSERT execution_memory
          │
          └── (on failure) ReflexionWirer.maybe_store_async(lesson, failure_class)
                    └── ReflexionStore.record_async() → INSERT reflexion_lessons
```

#### Recall flow: goal start (initial context assembly)

```
new goal submitted
          │
          ├── EpisodicMemoryStore.recall(goal, tenant_id, limit=3)
          │         └── DB: ORDER BY quality_score DESC → keyword filter
          │
          ├── ProceduralMemoryStore.recall(goal, tenant_id, min_success_rate=0.6)
          │         └── DB: ORDER BY success_rate DESC, use_count DESC
          │
          ├── LongTermMemoryStore.recall_async(goal, tenant_ctx, top_k=5)
          │         └── pgvector: ORDER BY embedding <=> query_vec LIMIT 5
          │
          ├── ExecutionMemory.recall_async(goal, tenant_id, limit=3)
          │         └── DB: recent successful plans → keyword filter
          │
          ├── ReflexionStore.recall(tenant_id, limit=10)
          │         └── in-memory deque (lazy-hydrated from DB)
          │
          └── KGQueryEngine.query(goal, tenant_id)
                    └── entity/path/impact strategy → GraphNode facts
          │
          All results → PromptContextBundle → ContextPipeline.run()
                    → planner_context → _node_plan LLM call
```

---

### Memory Safety

**Database-level isolation:** All memory tables include a `tenant_id` column. Row-Level Security (RLS) in `app/db/rls.py` sets `SET LOCAL app.tenant_id = :tenant_id` for every session, and Postgres RLS policies restrict all SELECT/INSERT/UPDATE/DELETE to matching rows. This means even a bug in the application layer that passes the wrong `tenant_id` will not leak cross-tenant data at the DB level.

**Failure class categorization:** Reflexion lessons are stored with a `failure_class` field (e.g., `"permission_denied"`, `"tool_not_found"`, `"ambiguous_goal"`). This allows pattern analysis: if a tenant consistently generates `"permission_denied"` lessons, the planner can be prompted to always check permissions first.

**In-memory caps:** All in-memory caches apply per-tenant size caps:
- Execution memory: last 100 entries per tenant (`_memories[tid] = _memories[tid][-100:]`)
- Reflexion store: `deque(maxlen=50)` per tenant
- Episodic cache: last 100 episodes per tenant

**Write-then-DB pattern:** Every async store method writes to the in-memory cache first, then persists to DB. DB failures are logged at WARNING and silently swallowed. This means the agent continues functioning during DB outages at the cost of learnings being lost for that session.

---

## Part B — Embedding Architecture

The embedding layer in AgentVerse is fully abstracted behind a `Protocol` interface. No component outside `app/providers/` or `app/embedding/` should care which embedding API is actually being called.

---

### 1. Provider Abstraction

**File:** `app/providers/base.py`

All LLM providers that support embeddings implement the `LLMProvider` Protocol, which includes the `embed` method:

```python
@runtime_checkable
class LLMProvider(Protocol):
    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...
    async def embed(self, request: EmbedRequest) -> EmbedResponse: ...
```

The embedding-specific types:

```python
@dataclass
class EmbedRequest:
    texts: list[str]                 # Batch of strings to embed
    model: str = ""                  # Optional model override
    metadata: dict[str, Any] = ...

@dataclass
class EmbedResponse:
    embeddings: list[list[float]]    # One vector per input text
    model: str = ""
    usage: TokenUsage | None = None
```

This structural protocol (no inheritance required) means the `FakeProvider`, real cloud providers, and any future local models all satisfy it without class hierarchy coupling.

---

### 2. Available Embedding Providers

| Provider | Model | Dimensions | Cost/1k tokens | Notes |
|----------|-------|:----------:|:---------------:|-------|
| OpenAI | `text-embedding-3-large` | 3072 | $0.00013 | Best quality; high tier only |
| OpenAI | `text-embedding-3-small` | 1536 | $0.00002 | Default for medium/starter plans |
| Voyage | `voyage-3-large` | 1024 | $0.00018 | Code and technical content |
| Voyage | `voyage-3-lite` | 512 | $0.000016 | Low tier fallback |
| Voyage | `voyage-code-3` | 1024 | — | Code-specific |
| Voyage | `voyage-multimodal-3` | 1024 | — | Multimodal content |
| Gemini | `text-embedding-004` | 768 | $0.000000 | Free; enterprise plan |
| sentence-transformers | `all-MiniLM-L6-v2` | 384 | Free (local) | ColBERT token embeddings |
| Fake | `fake-embedding` | 10 | Free | Deterministic test double |

**Plan gating** (`app/embedding/orchestrator.py`):

```python
_COST_BY_PLAN = {
    "free":         ["free", "low"],         # voyage-3-lite only
    "starter":      ["free", "low"],
    "professional": ["free", "low", "medium"],
    "enterprise":   ["free", "low", "medium", "high"],  # text-embedding-3-large available
}
```

---

### 3. EmbeddingRouter

**File:** `app/embedding/router.py` — `EmbeddingRouter`

The router is the low-level dispatch layer. It wraps a single `LLMProvider` instance and adds usage tracking, error counting, and a lexical fallback.

```python
class EmbeddingRouter:
    def set_provider(self, provider: Any) -> None: ...
    async def embed_texts(self, texts, provider, model, fallback_lexical=True) -> list[list[float]]: ...
    def _lexical_embed(self, text: str, dim: int = 384) -> list[float]: ...
    def get_drift_metrics(self) -> dict: ...
```

#### C7 fix — correct protocol method

`embed_texts()` calls `provider.embed(EmbedRequest(texts=texts))` rather than the optional `embed_batch()` shortcut, ensuring protocol compliance across all provider implementations.

#### Lexical fallback

When the provider fails (or is unavailable), `_lexical_embed()` produces a deterministic 384-dimensional vector using character-level hashing:

```python
for word in text.lower().split():
    idx = hash(word) % dim
    vec[idx] += 1.0
# L2-normalize
```

This is not semantically meaningful but is better than crashing — retrieval continues to work with degraded quality.

#### Usage tracking

The router accumulates `_usage[model_key]` (token count proxy: word count) and `_errors[model_key]` per model. These are exposed via `get_usage_stats()` and `get_drift_metrics()` for the observability layer.

---

### 4. EmbeddingOrchestrator

**File:** `app/embedding/orchestrator.py` — `EmbeddingOrchestrator`

The orchestrator is the policy layer above the router. It answers the question: *given this content type and this tenant's plan, which embedding model should I use?*

```python
def select(
    self,
    content_type: ContentType,
    tenant_ctx: Optional[TenantContext] = None,
    collection_size: int = 0,
) -> EmbeddingSelectionResult:
    ...
```

#### Content type → modality mapping

```python
_MODALITY_MAP = {
    ContentType.TEXT:     ["text"],
    ContentType.CODE:     ["code", "text"],         # code model first, fallback to text
    ContentType.IMAGE:    ["multimodal", "image", "text"],
    ContentType.AUDIO:    ["text"],                 # transcripts are text
    ContentType.VIDEO:    ["multimodal", "text"],
    ContentType.CSV:      ["text"],
    ContentType.JSON:     ["text"],
    # ... markdown, pdf, docx, html → ["text"]
}
```

#### Index strategy

The `collection_size` parameter is reserved for future index strategy selection: HNSW for collections under 100k documents, IVF for larger collections. This threshold aligns with pgvector's performance characteristics.

#### Selection algorithm

```
for modality in _MODALITY_MAP[content_type]:
    candidates = registry.list_by_modality(modality)
    affordable = [c for c in candidates if c.cost_class in allowed_costs]
    if affordable:
        best = max(affordable, key=lambda m: m.dimension)  # highest quality affordable
        return EmbeddingSelectionResult(model_id=best.model_id, dimension=..., ...)
```

The result includes the selected `model_id`, `dimension`, `cost_class`, `provider`, and a `selection_reason` string for observability.

---

### 5. DimensionPolicy

**File:** `app/embedding/dimension_policy.py` — `DimensionPolicy`

Maps model IDs to their standard vector dimensions. Used everywhere that needs to know the dimension of a collection's embedding without querying the model API.

```python
_DIMENSION_MAP = {
    "text-embedding-3-small":  1536,
    "text-embedding-3-large":  3072,
    "voyage-3-lite":           1024,
    "voyage-code-3":           1024,
    "voyage-multimodal-3":     1024,
    "fake-embedding":          10,
}

class DimensionPolicy:
    def select(self, model_id: str) -> int:
        return _DIMENSION_MAP.get(model_id, 1536)  # default: 1536
```

The `1536` default matches `text-embedding-3-small`, which is the most common model. This default is used when a model is not in the map — typically for newly added models or local sentence-transformer models.

The dimension drives dynamic table routing: knowledge chunks are stored in `knowledge_chunks_{dim}` tables (e.g., `knowledge_chunks_1536`, `knowledge_chunks_3072`) so that pgvector indices are always dimension-consistent.

---

### 6. EmbeddingDriftMonitor

**File:** `app/embedding/drift_monitor.py` — `EmbeddingDriftMonitor`

Detects when the embedding distribution has shifted — typically caused by switching embedding models mid-collection.

```python
class EmbeddingDriftMonitor:
    def measure(self, avg_similarity: float, sample_size: int = 100) -> DriftSeverity:
        # Thresholds:
        # ≥ 0.85 → STABLE
        # ≥ 0.70 → LOW
        # ≥ 0.55 → MEDIUM
        # ≥ 0.40 → HIGH
        # <  0.40 → CRITICAL

    def drift_score(self, avg_similarity: float) -> float:
        return max(0.0, min(1.0, 1.0 - avg_similarity))
```

A `DriftSeverity.CRITICAL` result triggers the re-embedding policy: all existing embeddings in the affected collection should be recomputed with the current model. This prevents silent degradation where new chunks are embedded with model B but old chunks use model A, causing ANN search to return inconsistent results.

#### When drift occurs

- **Model change:** `DIMENSION_MISMATCH` error — the new model produces 3072-dim vectors but the collection expects 1536-dim.
- **Provider switch:** Changing from OpenAI to Voyage changes the semantic space even at the same dimension.
- **Model version update:** Some providers silently update models; `drift_monitor.measure()` with a sampled `avg_similarity < 0.70` indicates drift.

---

### 7. Dimension Discovery (C6 fix)

A standing correctness fix: when `engine.hybrid_search()` is called without an explicit `embedding_dim` parameter, it queries `knowledge_collections.embedding_dim` from the DB rather than assuming a default. This prevents the silent empty results that occur when a collection uses 768-dim or 1024-dim vectors but the query vector is 1536-dim.

---

## Part C — Prompt Builder Architecture

The context assembly and prompt construction pipeline transforms raw retrieval results — chunks from multiple RAG strategies, five memory sources, web results — into precisely structured prompts for each of the three LLM roles: Planner, Executor, and Verifier.

---

### 1. ContextPipeline

**File:** `app/context/context_pipeline.py` — `ContextPipeline`, `PipelineResult`

The pipeline is a 7-step sequential processor that takes raw chunks and produces three role-specific prompt strings.

#### Constructor defaults

```python
ContextPipeline(
    max_tokens=6000,
    min_relevance_score=0.35,
    max_chunks=20,
    max_per_source=5,
    rerank_strategy=RerankStrategy.SCORE,
    citation_required=True,
    deduplication_enabled=True,
)
```

#### 7-step pipeline

```
Input: chunks[], query, goal_context, step_context,
       session_memory[], reflexion_lessons[], web_results[]
         │
  Step 1: Deduplicate
         │  RerankPolicy.rerank() with deduplicate=True
         │  Removes near-duplicate chunks (same content hash)
         │  → dedup_removed count tracked in PipelineResult
         │
  Step 2: Rerank
         │  RerankPolicy re-orders by strategy (SCORE, BM25, CROSS_ENCODER, etc.)
         │  Applies min_score filter (< 0.35 dropped)
         │  Applies max_per_source cap (max 5 chunks from same source)
         │  → filtered_removed count tracked
         │
  Step 3: Filter (min_relevance=0.35)
         │  Embedded in RerankPolicy — chunks below threshold are excluded
         │
  Step 4: Source Diversity (MMR)
         │  max_per_source=5 enforced via RerankPolicy
         │
  Step 5: Token Budget
         │  ContextBudget.apply(reranked) → BudgetResult
         │  max_tokens=6000, max_chunks=20
         │  Returns included_chunks, total_tokens
         │
  Step 6: Citation Threading
         │  CitationThreader.thread(included) → adds _citation_index to each chunk
         │  CitationManager.attach_citations(included) → (cited_chunks, citations[])
         │
  Step 7: PromptBuilder
         │  build_planner_context(bundle)   → planner_context  (all 9 sources)
         │  build_executor_context(bundle)  → executor_context (step-specific)
         │  build_verifier_context(bundle)  → verifier_context (citations + goal)
         │
Output: PipelineResult(planner_context, executor_context, verifier_context,
                       citations, total_tokens, dedup_removed, ...)
```

#### Input parameters

| Parameter | Type | Purpose |
|-----------|------|---------|
| `chunks` | `list[dict]` | RAG retrieval results (must have `content`, `score` keys) |
| `query` | `str` | The query used for relevance scoring |
| `goal_context` | `str` | The full goal text for context injection |
| `step_context` | `str` | Current step description for executor context |
| `session_memory` | `list[dict]` | From `SessionMemory.get(goal_id)` |
| `reflexion_lessons` | `list[str]` | From `ReflexionStore.recall()` |
| `web_results` | `list[dict]` | From FLARE/web retrieval patterns |

#### Production wiring note

Only `planner_context` is consumed directly in `_node_plan`'s LLM call. `executor_context` is stored in `agent_state.context["_executor_context"]` (N9 fix) rather than being discarded — it is retrieved by each step execution if a step needs fine-grained context. `verifier_context` is stored in `context["_verifier_context"]` for `_node_verify`.

---

### 2. PromptBuilder

**File:** `app/context/prompt_builder.py` — `PromptBuilder`, `PromptContextBundle`

`PromptBuilder` renders a `PromptContextBundle` into text strings suitable for direct LLM insertion.

#### PromptContextBundle fields

```python
@dataclass
class PromptContextBundle:
    goal_context: str
    knowledge_chunks: list[dict]          # From RAG + pipeline
    citations: list[Citation]             # From CitationManager
    session_memory: list[dict]
    reflexion_lessons: list[str]
    execution_memory: list[dict]          # Past plans (default=[])
    long_term_memory: list[dict]          # Cross-session facts (default=[])
    semantic_cache_hits: list[dict]       # Cache hits (default=[])
    graph_facts: list[dict]               # KG query results (default=[])
    web_results: list[dict]               # FLARE/web (default=[])
    degradation_notes: list[str]          # Service degradation messages
    source_inventory: dict                # For provenance tracking
```

#### `build_planner_context()` — 9 source sections

The planner context is the most comprehensive. It assembles up to 9 sections in priority order:

```
1. Goal: <goal_context>

2. Knowledge:
   <chunked RAG results with citation indices [1], [2], ...)>

3. Session context:
   <SessionMemory items, up to 3>

4. Prior successful approaches:
   <ExecutionMemory plans, up to 2>

5. Learned preferences:
   <LongTermMemory entries, up to 3>

6. [Cached context]
   <SemanticCache hits, up to 2>

7. Knowledge graph context:
   <KGQueryResult facts, up to 5>

8. Web context:
   <web_results snippets, up to 3 × 200 chars>

9. Past lessons:
   - <reflexion_lesson_1>
   - <reflexion_lesson_2>
   - <reflexion_lesson_3>

References:
[1] source_name — chunk_title (if citations enabled)
```

**Token budget enforcement:** The assembled string is truncated to `max_tokens × 4` chars (using `_CHARS_PER_TOKEN = 4`).

#### `build_executor_context()` — step-specific

Assembles: current step description → relevant chunks (token budget: `max_tokens // 2`) → citations → web results. Narrower focus than the planner — no episodic or procedural memory here.

#### `build_verifier_context()` — citation-focused

Goal text + citation block + degradation notes. The verifier only needs to check factual grounding; it does not need memory sources.

---

### 3. System Prompts

**File:** `app/agent/prompts.py`

System prompts define the persona and behavioral constraints of each LLM role. Five prompts are defined:

| Constant | Used by | Purpose |
|----------|---------|---------|
| `PLANNER_SYSTEM` | `_node_plan` default | Standard planning persona |
| `STRUCTURED_PLANNER_SYSTEM` | `_node_plan` (goal tree) | Structured plan with JSON output contract |
| `CHAIN_OF_THOUGHT_SYSTEM` | Complex reasoning steps | Forces CoT reasoning before answering |
| `REFLECTION_SYSTEM` | `_node_verify` failure branch | Failure diagnosis and lesson extraction |
| `SELF_REFINE_SYSTEM` | Output improvement pass | Iterative self-refinement of draft output |

The system prompt is selected before the LLM call based on the current node (`_node_plan`, `_node_execute`, `_node_verify`) and the active `PatternConfig`.

---

### 4. OutputContractBuilder

**File:** `app/context/output_contract_builder.py` — `OutputContractBuilder`, `OutputSchema`

The output contract builder auto-detects the desired response format from the goal text and generates format-specific instructions appended to the executor prompt.

#### Signal sets

```python
_JSON_SIGNALS = frozenset({
    "json", "table", "csv", "structured", "data", "dict", "array",
    "output as", "return as", "format as", "fields:", "columns:",
})

_MARKDOWN_SIGNALS = frozenset({
    "report", "document", "readme", "markdown", "formatted", "write a",
    "create a document", "draft", "article", "essay", "summary",
})
```

#### Format detection

```
output_format == "auto" and goal set:
    any(_JSON_SIGNALS in goal.lower()) → "json"
    any(_MARKDOWN_SIGNALS in goal.lower()) → "markdown"
    else → "text"
```

#### Generated instructions

| Format | Instruction injected |
|--------|---------------------|
| `json` (no fields) | `"Return ONLY valid JSON. No markdown code fences, no explanation text."` |
| `json` (with fields) | `"Return ONLY valid JSON with these exact fields: [field1, field2]. No markdown code fences…"` |
| `markdown` | `"Structure your response using proper Markdown: ## headings, **bold**…"` |
| `text` | `"Provide a clear, concise response. Be specific and actionable."` |

---

### 5. ToolPromptBuilder

**File:** `app/context/tool_prompt_builder.py` — `ToolPromptBuilder`

Formats the list of available MCP tools into the executor prompt. Called by `_execute_step()` when `_tool_defs` is non-empty.

```python
def build(self, tools: list[dict], step_context: str = "") -> str:
    # Output:
    # Step: <step_context>
    #
    # Available tools:
    #   - tool_name_1: description
    #   - tool_name_2: description
    #
    # Use only tools listed above. Return tool call as JSON.
```

When no tools are available, the prompt explicitly says: `"No tools available — use parametric knowledge."` This prevents the LLM from hallucinating tool calls.

---

### 6. SchemaAwarePromptInjector

**File:** `app/mcp/tool_intelligence.py`

When tools are available, the injector appends a tool-call format reminder to the executor system prompt. This prevents the common failure mode where the LLM produces valid-looking prose instead of the required JSON tool call format, especially during long multi-step executions where context is distant.

---

### 7. Prompt Safety

The prompt construction pipeline includes multiple safety gates:

#### GuardrailChecker

- `check_goal(goal)` — screens the incoming goal text before planning begins. Rejects goals containing forbidden patterns (PII extraction requests, direct injection attempts, etc.).
- `check_goal(step)` — called for each step description in `_execute_step()` to catch step-level injection.
- `check_tool_args(args)` — validates tool argument values before they are sent to the MCP server.

#### IndirectInjectionScanner

Tool results (MCP server responses) are scanned for injected LLM instructions before they are added to the agent's context. A web scraping tool that returns a page containing `"Ignore previous instructions and..."` is caught at this layer.

#### PromptVariantSelector

A/B variant selection via hash of `(tenant_id, goal_id)`. This enables controlled experiments where different prompt templates are tested across tenant populations without code changes.

---

## Part D — State Runtime Layer

The state runtime layer (`app/state_runtime/`) is the coordination layer that sits between the memory stores and the prompt builder. It contains the policy engines that decide *which* memory sources to activate, *whether* to cache a step result, and *how* to route knowledge graph queries — along with the aggregator that gathers results from all nine sources into a single `PromptContextBundle`.

---

### StateRuntimeContext and StateContextBuilder

**File:** `app/state_runtime/state_context.py`

`StateRuntimeContext` is a flat dataclass that holds up to nine source buckets, one per memory/knowledge type:

```python
@dataclass
class StateRuntimeContext:
    session_memory:      list[dict]  # From SessionMemory.get(goal_id)
    execution_memory:    list[dict]  # From ExecutionMemory.recall_async()
    long_term_memory:    list[dict]  # From LongTermMemoryStore.recall_async()
    semantic_cache_hits: list[dict]  # From SemanticCache.get_similar()
    knowledge_chunks:    list[dict]  # From KnowledgeStore.hybrid_search()
    graph_facts:         list[dict]  # From KGQueryEngine.query()
    web_results:         list[dict]  # From FLARE/web retrieval
    reflexion_lessons:   list[str]   # From ReflexionStore.recall()
    degradation_notes:   list[str]   # Service degradation messages

    def to_prompt_bundle(self, goal_context: str = "") -> PromptContextBundle:
        ...
```

`to_prompt_bundle()` converts the flat dataclass into the `PromptContextBundle` that `ContextPipeline.run()` and `PromptBuilder` consume. This is the single bridge between the runtime layer and the prompt construction layer — all nine sources pass through this method.

`StateContextBuilder` assembles the context asynchronously, firing parallel calls to each available store and populating the corresponding field. Stores that are `None` (e.g., KG store when no knowledge graph has been built for the tenant) simply leave their field as an empty list.

---

### MemoryPolicyEngine

**File:** `app/state_runtime/memory_policy.py` — `MemoryPolicyEngine`, `MemoryDecision`

`MemoryPolicyEngine` answers: *for this goal's runtime profile, which memory stores should be consulted?* It reads flags from `GoalRuntimeProfile.memory_cache`:

```python
@dataclass
class MemoryDecision:
    use_session_memory:    bool
    use_execution_memory:  bool
    use_long_term_memory:  bool
    use_knowledge_graph:   bool
    reflexion_enabled:     bool

class MemoryPolicyEngine:
    def decide(self, profile: GoalRuntimeProfile) -> MemoryDecision:
        mc = profile.memory_cache
        return MemoryDecision(
            use_session_memory    = mc.use_session_memory,
            use_execution_memory  = mc.use_execution_memory,
            use_long_term_memory  = mc.use_long_term_memory,
            use_knowledge_graph   = mc.use_knowledge_graph,
            reflexion_enabled     = mc.reflexion_enabled,
        )
```

This policy gate exists so that individual memory sources can be disabled per tenant plan, per goal, or for testing. A "fast mode" goal profile can disable LTM and KG lookups (both have DB round-trips) while keeping session memory and reflexion lessons (both in-memory and thus free).

---

### CachePolicyEngine

**File:** `app/state_runtime/cache_policy.py` — `CachePolicyEngine`, `CacheDecision`

Decides whether a step result is eligible for semantic cache storage. The policy enforces three invariants:

```python
class CachePolicyEngine:
    def decide(self, profile, step_text, step_output, is_error, is_nondeterministic) -> CacheDecision:
        if not profile.memory_cache.use_semantic_cache:
            return CacheDecision(False, "semantic_cache disabled by profile")
        if is_error:
            return CacheDecision(False, "error outputs must not be cached")
        if is_nondeterministic:
            return CacheDecision(False, "non-deterministic result must not override fresh data")
        return CacheDecision(True, "deterministic safe result — eligible for cache")
```

| Condition | Cache decision | Rationale |
|-----------|---------------|-----------|
| Profile disables cache | No | Tenant or goal explicitly opted out |
| `is_error=True` | No | Caching an error causes future cache hits to return that error |
| `is_nondeterministic=True` | No | Real-time data (stock prices, weather) must always be fetched fresh |
| All checks pass | Yes | Safe to cache |

---

### SemanticCacheBridge

**File:** `app/state_runtime/cache_bridge.py` — `SemanticCacheBridge`

The bridge is a write-through guard between the policy engine and the actual `SemanticCache`. It adds one additional safety check: empty outputs are never stored, even if the policy engine returns `should_cache=True`.

```python
async def maybe_store(self, step_text, step_output, tenant_id, is_error, is_nondeterministic) -> bool:
    if is_error: return False
    if is_nondeterministic: return False
    if not step_output or not step_output.strip(): return False   # Empty output guard
    await self._cache.store_async(query=step_text, response=step_output, tenant_id=tenant_id)
    return True

async def lookup(self, step_text, tenant_id, min_similarity=0.85) -> CacheBridgeResult | None:
    hits = await self._cache.get_similar(query=step_text, tenant_id=tenant_id, threshold=min_similarity)
    if hits:
        h = hits[0]
        return CacheBridgeResult(content=h["response"], tenant_id=tenant_id, similarity=h["similarity"])
    return None
```

The `min_similarity=0.85` default is the threshold below which a cache match is considered too dissimilar to return. Lowering this value increases cache hit rate but increases the risk of returning a semantically different cached result.

**Fallback mode:** When no `SemanticCache` is wired (e.g., no Redis in development), the bridge falls back to an in-process exact-match dict (`_fallback`). This means caching still works in development at the cost of no cross-replica sharing and no cosine similarity — only byte-for-byte identical step texts hit.

---

### KnowledgePolicyEngine

**File:** `app/state_runtime/knowledge_policy.py` — `KnowledgePolicyEngine`, `KnowledgeDecision`

Routes knowledge retrieval decisions based on the `KnowledgeRuntimeProfile` and the current query type:

```python
@dataclass
class KnowledgeDecision:
    use_kb:           bool   # Query the knowledge base (pgvector + BM25)
    use_graph:        bool   # Query the knowledge graph (KGQueryEngine)
    use_web_fallback: bool   # Fall back to web search if KB is empty or insufficient
    use_memory:       bool   # Always True — memory is always consulted

class KnowledgePolicyEngine:
    def decide(self, profile, query_type="factual") -> KnowledgeDecision:
        use_kb = profile.kb_state not in ("empty",)
        use_graph = (
            profile.graph_state not in ("empty",)
            and profile.graph_strategy != "none"
            and query_type in ("relationship", "impact", "dependency", "causal")
        )
        use_web = profile.web_fallback_required or profile.kb_state == "empty"
        return KnowledgeDecision(use_kb=use_kb, use_graph=use_graph,
                                  use_web_fallback=use_web, use_memory=True)
```

**Knowledge graph activation condition:** The KG is only queried for relational query types — `"relationship"`, `"impact"`, `"dependency"`, `"causal"`. For factual queries, entity lookup, or simple retrieval, the KG is skipped. This prevents unnecessary DB round-trips for goals that don't need graph traversal.

**Web fallback trigger:** Web search is triggered when `profile.kb_state == "empty"` (the tenant has no knowledge base) or when the profile explicitly flags `web_fallback_required` (e.g., a goal about current events that requires up-to-date information).

---

## Part E — Embedding Infrastructure: Index and Re-embedding Policies

---

### EmbeddingModelRegistry

**File:** `app/embedding/model_registry.py` — `EmbeddingModelRegistry`, `EmbeddingModelSpec`

The registry is the single source of truth for which embedding models exist, their dimensions, cost class, and modality. `EmbeddingOrchestrator` calls `registry.list_by_modality(modality)` to enumerate candidates.

```python
@dataclass
class EmbeddingModelSpec:
    model_id: str
    modality: str          # "text" | "code" | "multimodal" | "image"
    dimension: int
    cost_class: str        # "free" | "low" | "medium" | "high"
    provider: str
    max_input_tokens: int  # 8192 default
```

**Default registry** (from `EmbeddingModelRegistry.build_default()`):

| model_id | modality | dimension | cost_class | provider |
|----------|----------|:---------:|:----------:|---------|
| text-embedding-3-small | text | 1536 | low | openai |
| text-embedding-3-large | text | 3072 | medium | openai |
| voyage-3-lite | text | 512 | low | voyage |
| voyage-code-3 | code | 1024 | low | voyage |
| voyage-multimodal-3 | multimodal | 1024 | medium | voyage |
| fake-embedding | text | 10 | free | fake |

The `filter(cost_class=...)` method enables tenant-plan-based filtering without the policy having to know specific model names. `list_by_modality("code")` returns `[voyage-code-3]`; `list_by_modality("text")` returns all text-modality models ordered by insertion.

---

### VectorIndexPolicy

**File:** `app/embedding/vector_index_policy.py` — `VectorIndexPolicy`, `IndexStrategy`

Selects the pgvector index type based on collection size and dimension:

```python
_HNSW_THRESHOLD = 1_000       # < 1,000 docs: exact brute-force
_IVF_THRESHOLD  = 100_000     # 1,000–100,000: HNSW; ≥ 100,000: IVF

_SUPPORTED_DIMS = {768, 1024, 1536, 3072}   # pgvector index-compatible dimensions

class VectorIndexPolicy:
    def select(self, collection_size, dimension) -> IndexStrategy:
        if collection_size < _HNSW_THRESHOLD: return IndexStrategy.EXACT
        elif collection_size < _IVF_THRESHOLD: return IndexStrategy.HNSW
        else: return IndexStrategy.IVF
```

**Index behavior:**

| Strategy | Collection size | Performance | Quality |
|----------|:--------------:|:-----------:|:-------:|
| `EXACT` | < 1,000 | O(n) brute-force | 100% recall |
| `HNSW` | 1,000–100,000 | O(log n) approximate | ~99% recall |
| `IVF` | ≥ 100,000 | O(√n) partitioned ANN | ~95–98% recall |

**Supported dimensions:** Only `{768, 1024, 1536, 3072}` are index-compatible. Collections using dimensions outside this set (e.g., 512-dim `voyage-3-lite`) fall back to `EXACT` scan. The `is_dimension_compatible(old_dim, new_dim)` method is called before any embedding model migration to detect mismatch before it causes index corruption.

---

### ReembeddingPolicy

**File:** `app/embedding/reembedding_policy.py` — `ReembeddingPolicy`, `ReembeddingTrigger`

Determines whether an existing collection should be re-embedded. Four triggers:

```python
class ReembeddingTrigger(str, enum.Enum):
    NONE               = "none"
    MODEL_CHANGED      = "model_changed"
    DRIFT_DETECTED     = "drift_detected"
    STALE              = "stale"
    DIMENSION_MISMATCH = "dimension_mismatch"

_DRIFT_THRESHOLD = 0.25   # drift_score > 0.25 → re-embed

class ReembeddingPolicy:
    def should_reembed(
        self,
        current_model, new_model, collection_size,
        drift_score=0.0, age_days=0,
        staleness_threshold_days=90,
        old_dim=None, new_dim=None,
    ) -> ReembeddingTrigger:
        if current_model != new_model: return MODEL_CHANGED
        if old_dim != new_dim: return DIMENSION_MISMATCH
        if drift_score > _DRIFT_THRESHOLD: return DRIFT_DETECTED
        if age_days > 90 and collection_size > 100: return STALE
        return NONE
```

**Trigger priority (checked in order):**

1. **MODEL_CHANGED** — Any model change requires re-embedding. Even same-dimension models from different providers produce incompatible vector spaces.
2. **DIMENSION_MISMATCH** — Same trigger as model change but detected through dimension alone (before a full model comparison). Catches silent model version upgrades.
3. **DRIFT_DETECTED** — `drift_score > 0.25` (i.e., `avg_similarity < 0.75`). Measured by `EmbeddingDriftMonitor.drift_score()`. Indicates the current embeddings are no longer representative of the collection's true semantic space.
4. **STALE** — Collection not re-embedded in 90+ days AND has more than 100 documents. Periodic re-embedding ensures quality as model APIs are updated by providers.

When `should_reembed()` returns anything other than `NONE`, the collection management subsystem schedules a background re-embedding job via Celery.

---

*End of `04-memory-embeddings-prompt-builder.md`*
