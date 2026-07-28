# Canonical Persisted RAG Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:subagent-driven-development` to execute this plan one task at a time, with
> specification review followed by code-quality review for every task.

**Goal:** Make every one of the 18 canonical RAG strategies production-wired, tenant-scoped,
persisted, explicitly traceable, and reachable through the same runtime from the API,
AgentGraph, knowledge chat, and federated search.

**Architecture:** `app/rag/engine.py` remains the retrieval algorithm core, but all callers
enter it through a new tenant-aware `RetrievalGateway`. The gateway resolves canonical IDs,
authorizes collections, owns RLS-aware session lifecycles, creates independent sessions for
concurrent legs, and returns one typed result with citations and strategy evidence.
`KnowledgeStore` owns collection and ingestion persistence. `rag_platform` becomes a thin
planning/synthesis layer, never an alternative retriever.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, asyncpg, PostgreSQL/pgvector,
OpenAI-compatible LLM and embedding providers, Pydantic, pytest, Ruff, mypy.

---

## Execution rules

- Work in
  `/Users/harsh.kumar01/.config/superpowers/worktrees/Agent-Verse/agentverse-complete`.
- Run backend commands from `agent-verse-backend/` with `uv run`.
- Add the failing regression test first and observe the intended failure before production
  edits.
- Never make a strategy pass by silently invoking a different strategy.
- Fake providers are permitted only in focused development tests. The program is not accepted
  until the real-provider live journeys pass against PostgreSQL, Redis, and the running API.
- Keep commits task-scoped. Do not reformat unrelated files.
- If a public contract changes, update OpenAPI-facing models and both SDKs in Program 4 rather
  than adding compatibility ambiguity here.

## Task 1: Establish one canonical RAG strategy contract

**Files:**

- Create: `agent-verse-backend/app/rag/contracts.py`
- Modify: `agent-verse-backend/app/rag/__init__.py`
- Modify: `agent-verse-backend/app/rag_platform/query_planner.py`
- Modify: `agent-verse-backend/app/orchestration/strategy_registry.py`
- Create: `agent-verse-backend/tests/rag/test_canonical_contracts.py`

**Step 1: Write the failing contract tests**

Test that:

- `RAGStrategy` contains exactly the registry's 18 public RAG IDs;
- the six historical IDs resolve to their canonical IDs;
- an unknown ID raises `UnknownRAGStrategyError`;
- `RAGExecutionResult` serializes requested ID, resolved ID, citations, retrieval legs,
  strategy trace, answer, and grounded state;
- every registry RAG entry is `IMPLEMENTED` only when a concrete runtime capability is
  registered.

**Step 2: Run the red test**

```bash
uv run pytest tests/rag/test_canonical_contracts.py -q --no-cov
```

Expected: collection or import fails because `app.rag.contracts` does not exist.

**Step 3: Implement the minimal canonical contract**

Define:

```python
class RAGStrategy(StrEnum):
    NAIVE = "naive"
    HYBRID = "hybrid"
    HYDE = "hyde"
    MULTI_HOP = "multi_hop"
    GRAPH = "graph"
    CORRECTIVE = "corrective"
    ADAPTIVE = "adaptive"
    MODULAR = "modular"
    SPECULATIVE = "speculative"
    AGENTIC = "agentic"
    WEB_AUGMENTED = "web_augmented"
    FUSION = "fusion"
    SELF_RAG = "self_rag"
    FLARE = "flare"
    RAPTOR = "raptor"
    AGENTIC_CHUNKING = "agentic_chunking"
    COLBERT = "colbert"
    RAFT = "raft"
```

Add `resolve_rag_strategy()`, the compatibility alias map, typed citation/retrieval-leg/trace
records, `RAGExecutionResult`, and explicit unknown/unavailable exceptions. Re-export the
contract. Replace the duplicate planner enum with the canonical enum. Make the registry's
RAG status derive from concrete capability registration; do not mark unimplemented runtime
adapters complete yet.

**Step 4: Run focused verification**

```bash
uv run pytest tests/rag/test_canonical_contracts.py tests/rag/test_strategy_dispatch.py -q --no-cov
uv run ruff check app/rag/contracts.py app/rag/__init__.py app/rag_platform/query_planner.py tests/rag/test_canonical_contracts.py
uv run mypy app/rag/contracts.py app/rag_platform/query_planner.py
```

**Step 5: Commit**

```bash
git add agent-verse-backend/app/rag/contracts.py \
  agent-verse-backend/app/rag/__init__.py \
  agent-verse-backend/app/rag_platform/query_planner.py \
  agent-verse-backend/app/orchestration/strategy_registry.py \
  agent-verse-backend/tests/rag/test_canonical_contracts.py
git commit -m "feat(rag): define canonical strategy contracts"
```

## Task 2: Add the tenant-aware retrieval gateway

**Files:**

- Create: `agent-verse-backend/app/rag/gateway.py`
- Modify: `agent-verse-backend/app/rag/engine.py`
- Modify: `agent-verse-backend/app/main.py`
- Create: `agent-verse-backend/tests/rag/test_retrieval_gateway.py`

**Step 1: Write failing gateway tests**

Cover:

- required `TenantContext`;
- collection ownership validation before retrieval;
- every DB operation occurs inside tenant RLS context;
- gateway creates a new session for each concurrent retrieval leg;
- provider/embedder/capability absence raises an explicit unavailable error;
- aliases retain requested and resolved IDs in the returned trace;
- algorithm failures propagate as failed results and never become empty-success fallback.

Use recording session factories and deterministic providers to prove lifecycle ownership.

**Step 2: Run the red test**

```bash
uv run pytest tests/rag/test_retrieval_gateway.py -q --no-cov
```

Expected: import fails because the gateway is absent.

**Step 3: Implement the gateway**

Create `RetrievalDependencies` and `RetrievalGateway`. Accept an async session factory,
embedder, LLM provider/model resolver, graph/search capabilities, and policy services.
Authorize the tenant/collection, resolve the strategy, open sessions through
`rls_context()`, invoke the strategy adapter, and normalize the result into
`RAGExecutionResult`.

Refactor engine functions to consume a session supplied for one retrieval operation instead
of keeping or sharing a mutable session. Wire the gateway onto `app.state` in both in-memory
and lifespan-backed application assembly.

**Step 4: Verify**

```bash
uv run pytest tests/rag/test_retrieval_gateway.py tests/rag/test_retrieval_engine.py -q --no-cov
uv run ruff check app/rag/gateway.py app/rag/engine.py tests/rag/test_retrieval_gateway.py
uv run mypy app/rag/gateway.py app/rag/engine.py
```

**Step 5: Commit**

```bash
git add agent-verse-backend/app/rag/gateway.py \
  agent-verse-backend/app/rag/engine.py \
  agent-verse-backend/app/main.py \
  agent-verse-backend/tests/rag/test_retrieval_gateway.py
git commit -m "feat(rag): add tenant-aware retrieval gateway"
```

## Task 3: Unify persisted collection and chunk retrieval

**Files:**

- Modify: `agent-verse-backend/app/rag/store.py`
- Modify: `agent-verse-backend/app/db/models/knowledge.py`
- Add: `agent-verse-backend/app/db/migrations/versions/0091_rag_ingestion_structures.py`
- Create: `agent-verse-backend/tests/rag/test_persisted_rag_store.py`
- Modify: `agent-verse-backend/tests/rag/test_rag_db.py`

**Step 1: Write failing persistence tests**

Against PostgreSQL/pgvector, prove that:

- ingest and search use the same collection/chunk model after process restart;
- the collection belongs to the active tenant;
- vector, FTS, trigram, BM25 inputs, metadata, document ID, parent ID, window ID, hierarchy
  level, proposition flag, and strategy metadata persist;
- embeddings of every configured supported dimension are stored in the correct table;
- no production read path hydrates legacy in-memory `Document` state.

**Step 2: Run the red integration test**

```bash
DOCKER_HOST=unix:///Users/harsh.kumar01/.colima/default/docker.sock \
TESTCONTAINERS_RYUK_DISABLED=true \
uv run pytest tests/rag/test_persisted_rag_store.py -m integration -q --no-cov
```

Expected: restart/search or hierarchy metadata assertion fails.

**Step 3: Implement one persistence model**

Extend the chunk schema and dynamic-dimension table creation with the missing hierarchy and
strategy fields. Make `KnowledgeStore.ingest_document()` and `KnowledgeStore.search()` use
the same SQL-backed contract. Restrict `sync_from_db()` to compatibility metadata and remove
production search dependence on its document cache. Add forward migration and downgrade for
new columns/indexes.

**Step 4: Verify**

```bash
DOCKER_HOST=unix:///Users/harsh.kumar01/.colima/default/docker.sock \
TESTCONTAINERS_RYUK_DISABLED=true \
uv run pytest tests/rag/test_persisted_rag_store.py tests/rag/test_rag_db.py \
  -m integration -q --no-cov
uv run alembic upgrade head
uv run ruff check app/rag/store.py app/db/models/knowledge.py \
  app/db/migrations/versions/0091_rag_ingestion_structures.py
uv run mypy app/rag/store.py app/db/models/knowledge.py
```

**Step 5: Commit**

```bash
git add agent-verse-backend/app/rag/store.py \
  agent-verse-backend/app/db/models/knowledge.py \
  agent-verse-backend/app/db/migrations/versions/0091_rag_ingestion_structures.py \
  agent-verse-backend/tests/rag/test_persisted_rag_store.py \
  agent-verse-backend/tests/rag/test_rag_db.py
git commit -m "feat(rag): unify persisted collection retrieval"
```

## Task 4: Route all product retrieval entry points through the gateway

**Files:**

- Modify: `agent-verse-backend/app/api/rag_platform.py`
- Modify: `agent-verse-backend/app/api/knowledge.py`
- Modify: `agent-verse-backend/app/knowledge/federated_search.py`
- Modify: `agent-verse-backend/app/rag_platform/retriever.py`
- Modify: `agent-verse-backend/app/agent/graph.py`
- Create: `agent-verse-backend/tests/rag/test_gateway_entrypoints.py`

**Step 1: Write failing entry-point tests**

Assert that `/rag/query`, knowledge search/chat, federated search, `RAGRetriever`, and
AgentGraph each invoke the same gateway with tenant, collection, strategy, `top_k`, and
filters. Verify:

- `/rag/strategies` exposes exactly 18 canonical entries plus availability metadata;
- `limit` is rejected or translated only at the documented boundary;
- a failed required retrieval leg yields a non-2xx response or failed goal trace;
- knowledge chat uses the active provider/model and never returns an HTTP-200 error string.

**Step 2: Run the red tests**

```bash
uv run pytest tests/rag/test_gateway_entrypoints.py -q --no-cov
```

Expected: multiple entry points use separate stores/retrievers and the six-item strategy list.

**Step 3: Replace alternate runtime paths**

Inject `request.app.state.retrieval_gateway`. Make `RAGRetriever` only synthesize/verify the
gateway result. Replace tenantless federated search. Normalize errors with structured API
responses. Pass the configured model in every `CompletionRequest`. Emit citations and the
strategy trace through AgentGraph events and goal state.

**Step 4: Verify**

```bash
uv run pytest tests/rag/test_gateway_entrypoints.py tests/api/test_knowledge_api.py \
  tests/rag/test_rag_e2e.py -q --no-cov
uv run ruff check app/api/rag_platform.py app/api/knowledge.py app/knowledge/federated_search.py \
  app/rag_platform/retriever.py app/agent/graph.py tests/rag/test_gateway_entrypoints.py
uv run mypy app/api/rag_platform.py app/api/knowledge.py app/knowledge/federated_search.py \
  app/rag_platform/retriever.py
```

**Step 5: Commit**

```bash
git add agent-verse-backend/app/api/rag_platform.py \
  agent-verse-backend/app/api/knowledge.py \
  agent-verse-backend/app/knowledge/federated_search.py \
  agent-verse-backend/app/rag_platform/retriever.py \
  agent-verse-backend/app/agent/graph.py \
  agent-verse-backend/tests/rag/test_gateway_entrypoints.py
git commit -m "refactor(rag): unify product retrieval entry points"
```

## Task 5: Correct Naive, Hybrid, HyDE, Multi-hop, and Fusion

**Files:**

- Modify: `agent-verse-backend/app/rag/engine.py`
- Modify: `agent-verse-backend/app/rag/bm25.py`
- Modify: `agent-verse-backend/app/rag/agentic/patterns/fusion.py`
- Modify: `agent-verse-backend/tests/rag/test_retrieval_strategies.py`
- Modify: `agent-verse-backend/tests/rag/test_fusion_embedder.py`
- Create: `agent-verse-backend/tests/rag/test_core_strategy_evidence.py`

**Step 1: Add failing behavioral tests**

Prove strategy-specific behavior:

- naive executes vector-only persisted retrieval;
- hybrid records vector, FTS, trigram, and BM25 legs and RRF scores;
- HyDE passes a configured model, embeds the hypothetical document, and never embeds only
  the original query;
- multi-hop embeds and retrieves every decomposed hop and merges citations;
- fusion embeds all expanded queries and runs them with different session identities.

Tests must fail if outputs are merely relabeled naive results.

**Step 2: Run red tests**

```bash
uv run pytest tests/rag/test_core_strategy_evidence.py \
  tests/rag/test_fusion_embedder.py -q --no-cov
```

**Step 3: Implement the five strategies**

Use the model resolver for every completion request. Embed generated text. Add real BM25
corpus scoring before RRF. Make fusion request sessions from the gateway per query rather
than concurrently using one `AsyncSession`. Preserve per-leg provenance in the canonical
trace.

**Step 4: Verify**

```bash
uv run pytest tests/rag/test_core_strategy_evidence.py \
  tests/rag/test_retrieval_strategies.py tests/rag/test_fusion_embedder.py -q --no-cov
uv run ruff check app/rag/engine.py app/rag/bm25.py \
  app/rag/agentic/patterns/fusion.py tests/rag/test_core_strategy_evidence.py
uv run mypy app/rag/engine.py app/rag/bm25.py app/rag/agentic/patterns/fusion.py
```

**Step 5: Commit**

```bash
git add agent-verse-backend/app/rag/engine.py \
  agent-verse-backend/app/rag/bm25.py \
  agent-verse-backend/app/rag/agentic/patterns/fusion.py \
  agent-verse-backend/tests/rag/test_retrieval_strategies.py \
  agent-verse-backend/tests/rag/test_fusion_embedder.py \
  agent-verse-backend/tests/rag/test_core_strategy_evidence.py
git commit -m "fix(rag): implement core retrieval strategies"
```

## Task 6: Complete Graph, Corrective, Adaptive, and Web-augmented RAG

**Files:**

- Modify: `agent-verse-backend/app/rag/engine.py`
- Modify: `agent-verse-backend/app/rag/agentic/patterns/corrective.py`
- Modify: `agent-verse-backend/app/rag/agentic/patterns/adaptive.py`
- Create: `agent-verse-backend/app/rag/agentic/patterns/graph.py`
- Create: `agent-verse-backend/app/rag/agentic/patterns/web_augmented.py`
- Modify: `agent-verse-backend/app/rag/agentic/patterns/__init__.py`
- Create: `agent-verse-backend/tests/rag/test_grounding_strategies.py`

**Step 1: Add failing behavioral tests**

Test:

- graph retrieval merges vector seeds with entity, path, and community evidence;
- graph strategy is explicitly unavailable when no graph capability exists;
- corrective retrieval grades, filters, reformulates, retries, then applies policy-authorized
  web fallback;
- adaptive selection only chooses available capabilities and records its reason;
- web-augmented retrieval enforces policy, records URL/freshness, and distinguishes web from
  persisted citations.

**Step 2: Run red tests**

```bash
uv run pytest tests/rag/test_grounding_strategies.py -q --no-cov
```

**Step 3: Implement the strategy adapters**

Use typed capability protocols for graph and web search. Add bounded retries and explicit
stop reasons. Do not substitute hybrid when graph or web capability is missing. Register the
adapters in the canonical capability map.

**Step 4: Verify**

```bash
uv run pytest tests/rag/test_grounding_strategies.py tests/rag/test_corrective_rag.py \
  tests/rag/test_adaptive_rag.py -q --no-cov
uv run ruff check app/rag/engine.py app/rag/agentic/patterns tests/rag/test_grounding_strategies.py
uv run mypy app/rag/engine.py app/rag/agentic/patterns
```

**Step 5: Commit**

```bash
git add agent-verse-backend/app/rag/engine.py \
  agent-verse-backend/app/rag/agentic/patterns \
  agent-verse-backend/tests/rag/test_grounding_strategies.py
git commit -m "feat(rag): complete grounding-aware strategies"
```

## Task 7: Complete Speculative, Agentic, Self-RAG, and FLARE

**Files:**

- Modify: `agent-verse-backend/app/rag/agentic/patterns/speculative.py`
- Create: `agent-verse-backend/app/rag/agentic/patterns/agentic.py`
- Modify: `agent-verse-backend/app/rag/agentic/patterns/self_rag.py`
- Modify: `agent-verse-backend/app/rag/agentic/patterns/flare.py`
- Modify: `agent-verse-backend/app/rag/agentic/query_reformulator.py`
- Modify: `agent-verse-backend/app/rag/agentic/fallback_chain.py`
- Create: `agent-verse-backend/tests/rag/test_reasoning_retrieval_strategies.py`

**Step 1: Add failing behavioral tests**

Prove:

- speculative draft generation overlaps retrieval in time and verifies draft claims;
- agentic RAG chooses among retrieve, reformulate, fallback, and stop with a bounded loop;
- self-RAG stores relevance/support/usefulness critique in result strategy metadata and
  retries unsupported evidence;
- FLARE detects uncertain spans, creates new follow-up text, embeds that text, retrieves it,
  and continues generation.

**Step 2: Run red tests**

```bash
uv run pytest tests/rag/test_reasoning_retrieval_strategies.py -q --no-cov
```

**Step 3: Implement the bounded reasoning adapters**

Use task groups for speculative concurrency, typed decisions for agentic actions, explicit
critique records for Self-RAG, and per-follow-up embeddings for FLARE. Persist the strategy
metadata with the execution trace.

**Step 4: Verify**

```bash
uv run pytest tests/rag/test_reasoning_retrieval_strategies.py \
  tests/rag/test_agentic tests/rag/test_rag_patterns_functional.py -q --no-cov
uv run ruff check app/rag/agentic tests/rag/test_reasoning_retrieval_strategies.py
uv run mypy app/rag/agentic
```

**Step 5: Commit**

```bash
git add agent-verse-backend/app/rag/agentic \
  agent-verse-backend/tests/rag/test_reasoning_retrieval_strategies.py
git commit -m "feat(rag): complete reasoning retrieval strategies"
```

## Task 8: Move RAPTOR and agentic chunking to ingestion

**Files:**

- Create: `agent-verse-backend/app/rag/indexing.py`
- Modify: `agent-verse-backend/app/rag/store.py`
- Modify: `agent-verse-backend/app/rag/agentic/patterns/raptor.py`
- Modify: `agent-verse-backend/app/rag/agentic/patterns/agentic_chunking.py`
- Modify: `agent-verse-backend/app/knowledge/chunker_v2.py`
- Create: `agent-verse-backend/tests/rag/test_ingestion_time_strategies.py`

**Step 1: Add failing persisted-index tests**

Against PostgreSQL, ingest a document and prove:

- RAPTOR leaf and recursively summarized nodes exist before a query;
- agentic proposition chunks and parent links exist before a query;
- restart does not rebuild or lose either index;
- RAPTOR queries search leaf and summary levels;
- agentic-chunking queries search propositions and return parent-window citations.

**Step 2: Run red integration tests**

```bash
DOCKER_HOST=unix:///Users/harsh.kumar01/.colima/default/docker.sock \
TESTCONTAINERS_RYUK_DISABLED=true \
uv run pytest tests/rag/test_ingestion_time_strategies.py -m integration -q --no-cov
```

**Step 3: Implement indexing pipelines**

Add a `RAGIndexingPipeline` selected by ingestion configuration. Batch LLM and embedding
calls, store hierarchy/proposition metadata, and make the query adapters read precomputed
structures. Keep parent-child and sentence-window support dimension-independent.

**Step 4: Verify**

```bash
DOCKER_HOST=unix:///Users/harsh.kumar01/.colima/default/docker.sock \
TESTCONTAINERS_RYUK_DISABLED=true \
uv run pytest tests/rag/test_ingestion_time_strategies.py \
  tests/rag/test_parent_child_sentence_window.py -q --no-cov
uv run ruff check app/rag/indexing.py app/rag/store.py \
  app/rag/agentic/patterns/raptor.py app/rag/agentic/patterns/agentic_chunking.py
uv run mypy app/rag/indexing.py app/rag/store.py
```

**Step 5: Commit**

```bash
git add agent-verse-backend/app/rag/indexing.py \
  agent-verse-backend/app/rag/store.py \
  agent-verse-backend/app/rag/agentic/patterns/raptor.py \
  agent-verse-backend/app/rag/agentic/patterns/agentic_chunking.py \
  agent-verse-backend/app/knowledge/chunker_v2.py \
  agent-verse-backend/tests/rag/test_ingestion_time_strategies.py
git commit -m "feat(rag): persist ingestion-time strategy indexes"
```

## Task 9: Implement event-loop-safe ColBERT reranking

**Files:**

- Modify: `agent-verse-backend/app/rag/cross_encoder.py`
- Modify: `agent-verse-backend/app/rag/agentic/patterns/colbert.py`
- Modify: `agent-verse-backend/app/rag_platform/reranker.py`
- Create: `agent-verse-backend/tests/rag/test_colbert_runtime.py`

**Step 1: Add failing runtime tests**

Assert that ColBERT:

- obtains a wider persisted candidate set;
- performs token-level late-interaction scoring;
- runs blocking model inference in a worker thread;
- preserves original citations while recording rerank scores;
- fails explicitly when the configured reranker cannot load.

**Step 2: Run red tests**

```bash
uv run pytest tests/rag/test_colbert_runtime.py -q --no-cov
```

**Step 3: Implement the reranker**

Wrap model loading and inference behind a typed reranker protocol. Use `asyncio.to_thread()`
for blocking inference. Register the real ColBERT adapter separately from generic
cross-encoder reranking.

**Step 4: Verify**

```bash
uv run pytest tests/rag/test_colbert_runtime.py tests/rag/test_real_reranking.py -q --no-cov
uv run ruff check app/rag/cross_encoder.py app/rag/agentic/patterns/colbert.py \
  app/rag_platform/reranker.py tests/rag/test_colbert_runtime.py
uv run mypy app/rag/cross_encoder.py app/rag/agentic/patterns/colbert.py \
  app/rag_platform/reranker.py
```

**Step 5: Commit**

```bash
git add agent-verse-backend/app/rag/cross_encoder.py \
  agent-verse-backend/app/rag/agentic/patterns/colbert.py \
  agent-verse-backend/app/rag_platform/reranker.py \
  agent-verse-backend/tests/rag/test_colbert_runtime.py
git commit -m "feat(rag): add event-loop-safe ColBERT reranking"
```

## Task 10: Implement validated Modular RAG

**Files:**

- Create: `agent-verse-backend/app/rag/modular.py`
- Create: `agent-verse-backend/app/rag/agentic/patterns/modular.py`
- Modify: `agent-verse-backend/app/rag/agentic/patterns/__init__.py`
- Create: `agent-verse-backend/tests/rag/test_modular_rag.py`

**Step 1: Add failing module-graph tests**

Test valid and invalid pipelines composed of query expander, retriever, reranker, grader,
fallback, and synthesizer modules. Prove typed input/output compatibility, bounded branching,
ordered trace evidence, and explicit capability failures.

**Step 2: Run red tests**

```bash
uv run pytest tests/rag/test_modular_rag.py -q --no-cov
```

**Step 3: Implement Modular RAG**

Create immutable module specs, a validator, capability registry, and executor. Supply a safe
default pipeline but allow validated per-agent configuration. The executor must invoke the
canonical gateway primitives, not recursive public gateway calls.

**Step 4: Verify**

```bash
uv run pytest tests/rag/test_modular_rag.py -q --no-cov
uv run ruff check app/rag/modular.py app/rag/agentic/patterns/modular.py \
  tests/rag/test_modular_rag.py
uv run mypy app/rag/modular.py app/rag/agentic/patterns/modular.py
```

**Step 5: Commit**

```bash
git add agent-verse-backend/app/rag/modular.py \
  agent-verse-backend/app/rag/agentic/patterns/modular.py \
  agent-verse-backend/app/rag/agentic/patterns/__init__.py \
  agent-verse-backend/tests/rag/test_modular_rag.py
git commit -m "feat(rag): implement validated modular pipelines"
```

## Task 11: Implement the provider-neutral RAFT lifecycle

**Files:**

- Create: `agent-verse-backend/app/rag/raft.py`
- Create: `agent-verse-backend/app/rag/agentic/patterns/raft.py`
- Modify: `agent-verse-backend/app/rag/agentic/patterns/__init__.py`
- Modify: `agent-verse-backend/app/api/rag_platform.py`
- Create: `agent-verse-backend/tests/rag/test_raft_lifecycle.py`

**Step 1: Add failing lifecycle tests**

Cover dataset construction from persisted chunks, oracle/distractor assignment, train/test
separation, example validation, JSONL export, provider adapter submission, job status,
evaluation, and cost-confirmation enforcement. Test that retrieval with RAFT is unavailable
until a completed compatible model exists.

**Step 2: Run red tests**

```bash
uv run pytest tests/rag/test_raft_lifecycle.py -q --no-cov
```

**Step 3: Implement RAFT without submitting paid work**

Create provider-neutral `FineTuneProvider` and job records. Add dataset generation and
validation services, lifecycle endpoints, cost preview, confirmation token enforcement, and
evaluation hooks. The query adapter selects only completed compatible RAFT models and records
model/job IDs in the trace.

Do not submit a real paid fine-tune job during this task. That external action is deferred to
live certification and requires action-time user confirmation.

**Step 4: Verify**

```bash
uv run pytest tests/rag/test_raft_lifecycle.py -q --no-cov
uv run ruff check app/rag/raft.py app/rag/agentic/patterns/raft.py \
  app/api/rag_platform.py tests/rag/test_raft_lifecycle.py
uv run mypy app/rag/raft.py app/rag/agentic/patterns/raft.py app/api/rag_platform.py
```

**Step 5: Commit**

```bash
git add agent-verse-backend/app/rag/raft.py \
  agent-verse-backend/app/rag/agentic/patterns/raft.py \
  agent-verse-backend/app/rag/agentic/patterns/__init__.py \
  agent-verse-backend/app/api/rag_platform.py \
  agent-verse-backend/tests/rag/test_raft_lifecycle.py
git commit -m "feat(rag): implement provider-neutral RAFT lifecycle"
```

## Task 12: Promote all 18 RAG strategies and verify Program 1

**Files:**

- Modify: `agent-verse-backend/app/orchestration/strategy_registry.py`
- Modify: `agent-verse-backend/app/rag/agentic/patterns/__init__.py`
- Modify: `agent-verse-backend/tests/rag/test_strategy_dispatch.py`
- Create: `agent-verse-backend/tests/rag/test_registry_complete_rag.py`
- Modify: `docs/testing/AVCERT-20260729-001-full-product-certification-report.md`

**Step 1: Add the failing registry-completeness test**

For every registry RAG entry, resolve a concrete adapter and execute a strategy-specific
probe. Assert `IMPLEMENTED`, exact ID parity, no duplicate adapter, and non-empty distinct
trace evidence. Also assert the public strategy endpoint reports the same 18 entries.

**Step 2: Run red test**

```bash
uv run pytest tests/rag/test_registry_complete_rag.py -q --no-cov
```

Expected: registry states and adapter coverage do not yet agree.

**Step 3: Promote verified capabilities only**

Register all adapters, remove obsolete duplicate dispatch maps, set all 18 verified entries
to `IMPLEMENTED`, and update the certification report's RAG findings with implementation
evidence and remaining live-certification status.

**Step 4: Run Program 1 verification**

```bash
uv run pytest tests/rag tests/knowledge -q --no-cov
uv run ruff check app/rag app/rag_platform app/knowledge app/api/rag_platform.py \
  tests/rag tests/knowledge
uv run mypy app/rag app/rag_platform app/knowledge
DOCKER_HOST=unix:///Users/harsh.kumar01/.colima/default/docker.sock \
TESTCONTAINERS_RYUK_DISABLED=true \
uv run pytest -m integration tests/rag tests/knowledge -q --no-cov
```

Then start the real stack and, using the real configured provider, perform an API smoke
journey: create tenant and collection, ingest unique content, restart backend, query each
non-paid strategy, inspect citations and trace, and record failures. This is a development
checkpoint; the final acceptance remains Program 6 of the approved design.

**Step 5: Commit**

```bash
git add agent-verse-backend/app/orchestration/strategy_registry.py \
  agent-verse-backend/app/rag/agentic/patterns/__init__.py \
  agent-verse-backend/tests/rag/test_strategy_dispatch.py \
  agent-verse-backend/tests/rag/test_registry_complete_rag.py \
  docs/testing/AVCERT-20260729-001-full-product-certification-report.md
git commit -m "feat(rag): complete canonical strategy registry"
```

## Program 1 completion gate

Program 1 is complete only when:

- all 18 canonical entries resolve to distinct real adapters;
- the registry and `/rag/strategies` both report all 18 as implemented and capability-aware;
- all product entry points use `RetrievalGateway`;
- persistence survives restart and is tenant-isolated;
- query-time and ingestion-time strategies emit their claimed evidence;
- no required retrieval failure is converted to a successful-looking empty response;
- focused unit, RAG/knowledge, integration, Ruff, and mypy commands above pass for all touched
  files;
- a real-provider API smoke journey succeeds for every strategy not requiring a paid external
  job;
- RAFT's paid submission remains blocked until the user confirms the displayed cost at
  action time.
