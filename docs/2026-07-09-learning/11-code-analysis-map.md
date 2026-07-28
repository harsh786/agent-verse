# AgentVerse — Comprehensive Code Analysis Map

> **Purpose:** Reference for every key module in the backend. Each entry documents: primary file, line count, responsibility, key classes/functions, upstream/downstream dependencies, DB tables, tests, migrations, feature flags, and known limitations. Use this as a map when adding a feature, fixing a bug, or reviewing a PR.

---

## Table of Contents

1. [Core — App Factory & Services](#core)
2. [RAG Stack](#rag-stack)
3. [Memory Layer](#memory-layer)
4. [Ingestion Pipeline](#ingestion-pipeline)
5. [Orchestration & Routing](#orchestration--routing)
6. [AI / Model Layer](#ai--model-layer)
7. [MCP Tool Layer](#mcp-tool-layer)
8. [Evals & Self-Improvement](#evals--self-improvement)
9. [Observability](#observability)
10. [Guardrails & Governance](#guardrails--governance)
11. [Tenancy & Auth](#tenancy--auth)
12. [Reliability](#reliability)
13. [Scaling — Celery](#scaling--celery)
14. [Key Test Files](#key-test-files)
15. [Cross-Module Dependency Graph](#cross-module-dependency-graph)
16. [Migration Timeline](#migration-timeline)
17. [Environment Variables Reference](#environment-variables-reference)

---

## Core

---

### 1. `app/main.py` — Application Factory

**Primary file:** `app/main.py`  
**Lines:** 1697  
**Responsibility:** FastAPI application factory (`create_app`), two-phase service wiring, lifespan pool management, middleware registration, ~25 router inclusions, error handlers.

**Key classes/functions:**

| Symbol | Description |
|--------|-------------|
| `create_app(settings, health_checks, pools, manage_pools, ...)` | Factory function. Phase 1: in-memory service construction. Phase 2 (lifespan): DB/Redis upgrade. |
| `lifespan(app)` | asynccontextmanager. Starts `ConnectionPools`, upgrades every `app.state` service to DB-backed. |
| `_resolve_provider_for_app(settings)` | Resolves real LLM provider from env (Anthropic/OpenAI/Gemini/Groq/Ollama) or `FakeProvider`. Hard fails in production with FakeProvider. |
| `_build_verifier_provider(settings)` | Builds a separate verifier LLM for cross-model verification (breaks self-confirmation bias). |
| `_FakeRedis` | Thread/async-safe in-memory dict that mirrors the Redis async API. Used in Phase 1 and all unit tests. |
| `_register_error_handlers(app)` | Registers `PlatformError` handler (structured JSON) and generic exception handler (detail hidden). |

**Dependencies:** All 30+ service modules imported at module level. Key: `app.core.pools`, `app.providers.registry`, `app.services.goal_service`, `app.mcp.registry`, `app.tenancy.middleware`.

**Depended on by:** `uvicorn` entry point, all tests that call `create_app()`.

**DB tables:** None directly; wires all services that touch tables.

**Tests:** `tests/test_main.py`, `tests/test_lifespan.py`

**Flags/env vars:** `MANAGE_POOLS`, `ENVIRONMENT`, all provider keys, `CORS_ORIGINS`, `DATABASE_URL`, `REDIS_URL`.

**Known limitations:** God-module pattern acknowledged in comments — decomposition planned into `app/main_services.py`.

---

### 2. `app/agent/graph.py` — AgentGraph

**Primary file:** `app/agent/graph.py`  
**Lines:** 4534  
**Responsibility:** LangGraph-based autonomous agent state machine. All node implementations, edge construction, routing logic, feature-flag-driven topology.

**Key classes/functions:**

| Symbol | Description |
|--------|-------------|
| `AgentGraph.__init__(planner, executor, verifier, ...)` | Constructor. Accepts 30+ configuration kwargs. Calls `_build()` which compiles the LangGraph. |
| `_build()` | Constructs `StateGraph[GraphState]`. Conditionally adds nodes (`think`, `tree_of_thoughts`, `refine`, `self_consistency`, `peer_review`, `reflect`) based on feature flags. |
| `_node_initialize` | Phase 1 node: creates `AgentState`, fires guardrail check, SelfOptimizerV2 arm selection, RuntimeProfileBuilder, identity/governance profile. |
| `_node_rag_retrieval` | Phase 2 node: execution memory recall, LTM recall, KnowledgeStore hybrid search, advanced RAG dispatch (FLARE/HyDE/fusion/etc.), web fallback. |
| `_node_plan` | Phase 3 node: ContextPipeline 7-step processing, episodic/procedural/reflexion recall, LLM call with STRUCTURED_PLANNER_SYSTEM. |
| `_node_execute` | Phase 4 node: wave-based structured plan execution, per-step guardrails, policy check, HITL gate, cost gate, MCPClient.call_tool, rollback registration. |
| `_node_verify` | Phase 5 node: builds verifier summary, LLM call with VERIFIER_SYSTEM, EvalRunner, RuntimeScorecard, SelfImprovementEngine. |
| `_route(state)` | Conditional edge function. Returns: `complete`, `replan`, `max_iter`, `waiting_human`, `rag_remediate`, `reflect`. |
| `_node_think` | Chain-of-thought node using `CHAIN_OF_THOUGHT_SYSTEM`. Uses `planning_model` tier. |
| `_node_reflect` | Reflection node using `REFLECTION_SYSTEM`. Diagnoses failures, populates `verification_feedback`. |
| `_node_refine` | Self-Refine node. Improves last step output (max 2 iterations). |
| `_node_self_consistency` | Samples 3 LLM responses → majority vote consensus. |
| `_node_tree_of_thoughts` | `TreeOfThoughtsPattern(n_thoughts=3, max_depth=2)`. |
| `_node_peer_review` | `PeerReviewPattern(quality_threshold=0.7)` using verifier LLM. |
| `_node_rag_remediate` | Targeted re-retrieval when verification detects context gap. |
| `_build_verifier_summary(steps)` | Builds verifier input: all FAILED/UNGROUNDED steps + last 5 steps. |
| `GraphState` | `TypedDict` with `goal`, `tenant_ctx`, `agent_state`, `rag_context`, `plan`, `iteration`, `terminal_reason`, `cot_reasoning`. |

**Dependencies:** `langgraph`, `app.agent.state`, `app.agent.prompts`, `app.providers.base`, `app.governance.*`, `app.reliability.*`, `app.memory.*`, `app.rag.*`, `app.mcp.client`, `app.intelligence.guardrails`.

**Depended on by:** `app.services.goal_service._make_agent_loop_for_tenant()`, `app.scaling.tasks.run_goal`.

**DB tables:** Reads/writes via injected service objects (no direct DB access).

**Tests:** `tests/agent/test_loop.py`, `tests/agent/test_graph.py`

**Flags/env vars:** `DYNAMIC_ORCHESTRATION`, `ENABLE_RAG_STRATEGY_ROUTING`, `ENABLE_PATTERN_SSE_EVENTS`. Feature flags passed as constructor kwargs: `enable_cot`, `enable_reflection`, `enable_self_refine`, `enable_self_consistency`, `enable_tree_of_thoughts`, `enable_peer_review`.

**Known limitations:** Single file at 4534 lines — extraction of individual node classes planned. `_node_supervisor_check` and `_node_debate` are stubs (log warnings only).

---

### 3. `app/agent/state.py` — AgentState

**Primary file:** `app/agent/state.py`  
**Lines:** 98  
**Responsibility:** Typed runtime state for one agent execution. Designed to be serializable for LangGraph checkpointing.

**Key classes/functions:**

| Symbol | Description |
|--------|-------------|
| `GoalStatus` | StrEnum: `PLANNING`, `EXECUTING`, `VERIFYING`, `WAITING_HUMAN`, `COMPLETE`, `FAILED`, `CANCELLED` |
| `StepStatus` | StrEnum: `PENDING`, `RUNNING`, `COMPLETE`, `FAILED`, `SKIPPED`, `UNGROUNDED` |
| `StepResult` | Dataclass: `step_id`, `description`, `status`, `output`, `tool_calls: list[dict]`, `error` |
| `SubGoal` | Dataclass for goal-tree decomposition: `sub_goal_id`, `description`, `parent_goal_id`, `depends_on`, `status`, `result` |
| `AgentState` | Main state dataclass: `goal`, `tenant_ctx`, `goal_id`, `status`, `iterations`, `steps`, `plan`, `context`, `verification_feedback`, `verification_success`, `error_message`, `sub_goals`, `events`, `ungrounded_claims`, `cited_answer`, `provenance`, `run_trace` |

**Dependencies:** `app.tenancy.context.TenantContext`

**Depended on by:** `app.agent.graph` (all nodes), `app.services.goal_service`

**Notes:** `events` field is explicitly noted as "not checkpointed — ephemeral". `run_trace` and `events` are populated but not persisted to the LangGraph checkpoint.

---

### 4. `app/agent/prompts.py` — System Prompts

**Primary file:** `app/agent/prompts.py`  
**Lines:** 198  
**Responsibility:** All LLM system prompts. Kept as separate module-level constants so changing one role's prompt cannot affect others.

**Key constants:**

| Constant | Role | Critical rules |
|----------|------|----------------|
| `PLANNER_SYSTEM` | Planner | JSON-only output `{"steps": [...]}`. No markdown. |
| `STRUCTURED_PLANNER_SYSTEM` | Planner (structured) | JSON with `{id, description, tool, arguments, depends_on, risk, expected_output}`. |
| `EXECUTOR_SYSTEM` | Executor | CRITICAL GROUNDING RULES: never fabricate IDs/counts/dates. `{"tool": null, "result": "INSUFFICIENT DATA"}` when uncertain. |
| `VERIFIER_SYSTEM` | Verifier | `{"success": bool, "reason": str, "retry": bool}`. `[TOOL FAILED]` → always false. |
| `CHAIN_OF_THOUGHT_SYSTEM` | Think node | INTENT / RELEVANT TOOLS / RISKS / APPROACH template. |
| `REFLECTION_SYSTEM` | Reflect node | FAILED_STEP / ROOT_CAUSE / FIX. Returns minimal repair (1-2 steps). |
| `GROUNDING_SYSTEM` | Grounding verifier | Check if claims appear in tool output evidence. Literal presence check. |
| `GOAL_TREE_SYSTEM` | Goal decomposer | `{"decompose": bool, "sub_goals": [...]}`. Only decomposes when >= 4 steps. |

**Dependencies:** None (pure constants).

**Depended on by:** `app.agent.graph` (all nodes import these), `app.agent.goal_tree`, `app.agent.workflow_planner`.

---

### 5. `app/agent/model_router.py` — ModelRouter

**Primary file:** `app/agent/model_router.py`  
**Lines:** 192  
**Responsibility:** Routes task types (planning/execution/verification/embedding/reflection) to optimal models per provider. Supports complexity-based downgrading to save cost.

**Key classes/functions:**

| Symbol | Description |
|--------|-------------|
| `ModelRouterConfig` | Dataclass: `planning_model`, `execution_model`, `verification_model`, `embedding_model`, `fallback_model` |
| `_PROVIDER_DEFAULTS` | Built-in per-provider configs: Anthropic (opus-4-8/sonnet-4-5/haiku-3-5), OpenAI (gpt-5.2/4o-mini/4o-mini), Groq (llama-3.1-70b/8b), Ollama (llama3.2) |
| `ModelRouter.model_for(task_type)` | Maps task → model. Keys: `planning`, `execution`, `verification`, `embedding`, `classification`, `reflection`, `think`, `thinking` |
| `ModelRouter.complexity_tier(goal)` | Regex classification: `simple`/`medium`/`complex` based on keyword patterns |
| `ModelRouter.model_for_goal(task_type, goal)` | Like `model_for()` but downgrades simple planning goals to `execution_model` |
| `ModelRouter.with_override(model)` | Copy-on-write override of all task types to a single model (per-goal override, no leakage) |
| `get_router_for_tenant(tenant_cfg)` | Builds router from tenant's LLM config dict (provider + default_model) |

**Dependencies:** `app.observability.logging`

**Depended on by:** `app.main.create_app()`, `app.agent.graph` (nodes call `self._model_router.model_for()`)

**Flags/env vars:** Provider keys determine which `_PROVIDER_DEFAULTS` entry is used.

---

### 6. `app/services/goal_service.py` — GoalService

**Primary file:** `app/services/goal_service.py`  
**Lines:** 3085  
**Responsibility:** Goal lifecycle management, SSE event distribution, Celery→SSE bridge, per-tenant LLM provider dispatch, AgentGraph construction.

**Key classes/functions:**

| Symbol | Description |
|--------|-------------|
| `GoalRecord` | Dataclass: `goal_id`, `goal_text`, `status`, `tenant_id`, `priority`, `dry_run`, `events`, `task`, `subscribers: list[asyncio.Queue]` |
| `GoalService.__init__` | Accepts `audit_log`, `hitl`, `app_state`, `db_session_factory`, `task_queue` |
| `GoalService.submit_goal(goal, agent_id, tenant_ctx, execution_context)` | Creates GoalRecord, fires DB write, enqueues to Celery or local asyncio task |
| `GoalService._make_agent_loop_for_tenant(goal_data, tenant_ctx)` | Builds fully-wired `AgentGraph` for a tenant: resolves per-tenant LLM keys from vault, wires all governance + memory services |
| `GoalService._dispatch_event(goal_id, event)` | Puts event into all subscriber queues AND publishes to Redis pub/sub |
| `GoalService.stream_events(goal_id, tenant_ctx)` | Async generator yielding SSE frames. Adds subscriber queue to GoalRecord. |
| `GoalService._subscribe_celery_goal_events(redis_url)` | psubscribe `goal_events:*` → feed into in-process subscriber queues |
| `GoalService.start_celery_event_bridge(redis_url)` | Starts the bridge as a background asyncio task |
| `GoalService.resume_goal(goal_id, tenant_ctx)` | Resumes a WAITING_HUMAN goal (after HITL approval) |
| `_resolve_checkpointer(app_state)` | Priority: AsyncRedisSaver → RedisSaver → MemorySaver (warns on fallback) |
| `_SENTINEL` | `None` value placed on subscriber queue to signal end-of-stream |

**Dependencies:** `app.agent.graph`, `app.agent.state`, `app.governance.*`, `app.providers.*`, `app.services.goal_queue`, `langgraph.checkpoint.*`

**Depended on by:** `app.api.goals` router, `app.scaling.tasks.run_goal`

**DB tables:** `goals`, `goal_events`, `api_keys`

**Tests:** `tests/services/test_goal_service.py`

**Known limitations:** Acknowledged God-class in comments — decomposition into `goal_events.py`, `goal_metrics.py`, `goal_lifecycle.py` is in progress.

---

### 7. `app/providers/base.py` — LLMProvider Protocol

**Primary file:** `app/providers/base.py`  
**Lines:** 171  
**Responsibility:** Structural protocol for all LLM/embedding providers. All provider implementations satisfy this without inheritance.

**Key classes/functions:**

| Symbol | Description |
|--------|-------------|
| `Message` | Pydantic model: `role: MessageRole`, `content: str \| list[dict]`, `tool_call_id`, `tool_calls`, `image_data` |
| `ToolDefinition` | Dataclass: `name`, `description`, `input_schema: dict` |
| `CompletionRequest` | Dataclass: `messages`, `model`, `system`, `tools`, `max_tokens=4096`, `temperature=0.0`, `response_schema`, `cache_prefix` |
| `CompletionResponse` | Dataclass: `content`, `model`, `input_tokens`, `output_tokens`, `tool_calls`, `stop_reason`, `usage: TokenUsage` |
| `EmbedRequest` | Dataclass: `texts: list[str]`, `model` |
| `EmbedResponse` | Dataclass: `embeddings: list[list[float]]`, `model`, `total_tokens` |
| `TokenUsage` | Dataclass: `prompt_tokens`, `completion_tokens`, `total_tokens` |
| `LLMProvider` | `@runtime_checkable Protocol`: requires `complete(request) -> CompletionResponse`, `embed(request) -> EmbedResponse` |

**Dependencies:** `pydantic` (for `Message` only).

**Depended on by:** Every provider implementation, every node in `AgentGraph`, `MCPClient`, `RAGEngine`.

---

## RAG Stack

---

### 8. `app/rag/engine.py` — Retrieval Engine

**Primary file:** `app/rag/engine.py`  
**Lines:** 864  
**Responsibility:** Production-grade tri-leg retrieval with RRF fusion. pgvector ANN + PostgreSQL FTS + pg_trgm fuzzy, with 14 advanced strategy variants.

**Key classes/functions:**

| Symbol | Description |
|--------|-------------|
| `hybrid_search(session, query, query_embedding, collection_id, top_k, retrieval_mode)` | Core retrieval. Leg 1: pgvector `<=>` cosine. Leg 2: `plainto_tsquery` FTS. Leg 3: `similarity()` trigram. RRF fusion with `k=60`. |
| `retrieve(session, query, strategy, ...)` | Dispatch table to 14 strategies: `hybrid`, `lexical`, `vector`, `hyde`, `fusion`, `multi_hop`, `raptor`, `flare`, `rerank`, `colbert`, `graph`, `web`, `ltm`, `parametric` |
| `RetrievalResult` | Dataclass: `chunk_id`, `content`, `score`, `source_metadata`, `retrieval_legs: list[str]` |
| `_rrf_score(ranks)` | `Σ 1/(60 + rank_i)` — standard RRF formula |
| `RetrievalPlanner` | Heuristic strategy selector from goal text (code keywords → lexical, short query → vector, default → hybrid) |

**Dependencies:** `sqlalchemy.ext.asyncio`, `app.observability.logging`. Optional: `app.providers.base` (for HyDE/fusion LLM calls).

**Depended on by:** `app.agent.graph._node_rag_retrieval`, `app.rag.agentic.retriever_tool`

**DB tables:** `knowledge_chunks_{dim}` (dynamic table names based on embedding dimension), `knowledge_collections`

**Tests:** `tests/rag/test_engine.py`

**Migration:** `0008_knowledge.py`, `0062_knowledge_v2.py`, `0090_parent_child_retrieval.py`

---

### 9. `app/rag/store.py` — KnowledgeStore

**Primary file:** `app/rag/store.py`  
**Lines:** 800  
**Responsibility:** In-memory knowledge store with hybrid search (70% cosine + 30% trigram). In production, DB-backed via `hybrid_search_db()` and async fire-and-forget writes.

**Key classes/functions:**

| Symbol | Description |
|--------|-------------|
| `KnowledgeStore` | Main class. Key: `(tenant_id, collection_id) → _CollectionStore` |
| `KnowledgeStore.ingest_chunk(chunk, collection_id, tenant_ctx)` | Adds to in-memory store + fire-and-forget DB INSERT |
| `KnowledgeStore.hybrid_search(query, query_embedding, collection_id, tenant_ctx, top_k)` | In-memory: `0.7 * cosine + 0.3 * trigram`. Returns `HybridSearchResult` list |
| `KnowledgeStore.hybrid_search_db(query, query_embedding, collection_id, tenant_ctx, top_k)` | Async DB-backed search using `engine.hybrid_search()` |
| `KnowledgeStore.expand_to_parents(chunks, db)` | Retrieves parent chunks for parent-child chunking strategy |
| `HybridSearchResult` | Dataclass: `chunk_id`, `content`, `score`, `vector_score`, `trigram_score`, `source_url`, `page_number`, `metadata` |

**Dependencies:** `app.rag.models`, `app.rag.engine`, `app.db.rls`, `app.tenancy.context`

**DB tables:** `knowledge_chunks_{dim}`, `knowledge_collections`, `knowledge_documents`

**Tests:** `tests/rag/test_store.py`

**Migration:** `0008_knowledge.py`, `0035_knowledge_citations.py`, `0062_knowledge_v2.py`

---

### 10. `app/rag/bm25.py` — BM25Retriever

**Primary file:** `app/rag/bm25.py`  
**Lines:** ~180  
**Responsibility:** Okapi BM25 retrieval over in-memory corpus. Used as a fourth leg when PostgreSQL FTS is unavailable or as a local-only alternative.

**Key classes/functions:** `BM25Retriever.index(corpus)`, `BM25Retriever.retrieve(query, top_k)` — returns scored chunks using `Σ IDF * TF / (TF + k1*(1 - b + b*dl/avgdl))`.

**Dependencies:** None (pure Python).

---

### 11. `app/context/context_pipeline.py` — ContextPipeline

**Primary file:** `app/context/context_pipeline.py`  
**Lines:** 100  
**Responsibility:** Orchestrates 7-step context processing for planner/executor/verifier. Accepts raw chunks, returns role-specific context strings with citations.

**Key classes/functions:**

| Symbol | Description |
|--------|-------------|
| `ContextPipeline.run(chunks, query, goal_context, step_context, session_memory, reflexion_lessons, web_results)` | 7 steps: deduplicate → rerank → filter → diversity → budget → citation-thread → build role contexts |
| `PipelineResult` | `included_chunks`, `planner_context`, `executor_context`, `verifier_context`, `citations`, `total_tokens`, `dedup_removed`, `filtered_removed` |

**Dependencies:** `app.context.rerank_policy`, `app.context.context_budget`, `app.context.citation_manager`, `app.context.prompt_builder`, `app.rag.agentic.citation_threader`

**Depended on by:** `app.agent.graph._node_plan`

**Configuration:** `max_tokens=6000`, `min_relevance_score=0.35`, `max_chunks=20`, `max_per_source=5`

---

### 12. `app/context/rerank_policy.py` — RerankPolicy

**Primary file:** `app/context/rerank_policy.py`  
**Lines:** ~200  
**Responsibility:** Multi-strategy reranking: SCORE (sort by existing score), MMR (max marginal relevance for diversity), CROSS_ENCODER (ms-marco-MiniLM-L-6-v2), RRF.

**Key classes/functions:** `RerankPolicy.rerank(chunks, query)` — returns deduplicated, re-sorted chunks. `RerankStrategy` enum.

---

### 13. `app/rag/agentic/retriever_tool.py` — RetrieverTool

**Primary file:** `app/rag/agentic/retriever_tool.py`  
**Lines:** ~300  
**Responsibility:** Agentic retriever with automatic strategy selection, fallback chain, and corrective retrieval.

**Key classes/functions:**

| Symbol | Description |
|--------|-------------|
| `RetrieverTool.retrieve(query, tenant_ctx, strategy, top_k, min_confidence, allow_web_fallback)` | Strategy dispatch: hybrid → graph → hyde → web → ltm → parametric (FallbackChain) |
| `RetrieverTool.retrieve_corrective(query, initial_result, tenant_ctx)` | CRAG-style: assess initial result quality → retrieve more if confidence low |
| `RetrievalResult.source` | One of: `"knowledge_base"`, `"web"`, `"ltm"`, `"parametric"` |

---

### 14. `app/rag/cross_encoder.py` — CrossEncoder

**Primary file:** `app/rag/cross_encoder.py`  
**Lines:** ~120  
**Responsibility:** Local cross-encoder reranking using `ms-marco-MiniLM-L-6-v2`. Called by `RerankPolicy` when strategy=`CROSS_ENCODER`.

**Flags/env vars:** Model loaded lazily on first call. Requires `sentence-transformers` package.

---

### 15–16. `app/rag/parent_child_chunker.py` / `app/rag/sentence_window.py`

**Responsibility:**  
- `ParentChildChunker`: Creates parent chunks (larger) and child chunks (smaller, for retrieval). `expand_to_parents()` in KnowledgeStore uses this.  
- `SentenceWindowChunker` / `SentenceWindowRetriever`: Chunks at sentence boundaries, retrieves with ±k sentence window for richer context.

**Migration:** `0090_parent_child_retrieval.py`

---

## Memory Layer

---

### 17. `app/memory/execution.py` — ExecutionMemory

**Primary file:** `app/memory/execution.py`  
**Lines:** ~250  
**Responsibility:** Caches successful plan → goal mappings. Recalled before planning to surface proven approaches. Also tracks failure patterns.

**Key functions:** `recall_async(goal, tenant_id, db, limit=3)` — async pgvector or keyword similarity. `recall_failures(goal_hint, tenant_ctx, top_k=3)`. `record(goal, plan, success, tenant_ctx)`.

**DB tables:** `execution_memory`

---

### 18. `app/memory/long_term.py` — LongTermMemoryStore

**Primary file:** `app/memory/long_term.py`  
**Lines:** 356  
**Responsibility:** Cross-session learnings: domain facts, tool preferences, failure patterns. Backed by pgvector.

**Key functions:** `store(memory, tenant_ctx)`, `recall(query, tenant_ctx, top_k, memory_type)`, `recall_async(query, tenant_ctx, top_k, db, embedder)` (pgvector cosine), `extract_from_goal(goal, steps, tenant_ctx)` (LLM extraction).

**DB tables:** `long_term_memory` (columns: `id`, `tenant_id`, `content`, `memory_type`, `embedding`, `confidence`, `source_goal_id`)

**Migration:** `0024_ltm_embedding.py`, `0028_ltm_embedding_resize.py`

---

### 19–20. `app/memory/episodic.py` / `app/memory/procedural.py`

**Responsibility:**  
- `EpisodicMemoryStore`: Per-tenant deque (capped 1000). Records (goal, result) pairs. `recall()` uses trigram + optional embedding similarity.  
- `ProceduralMemoryStore`: Tracks patterns: `{pattern_key: {success_count, failure_count, avg_steps}}`. `learn()` on success. `recall()` on planning.

**DB tables:** `episodic_memory`, `procedural_patterns`

**Migration:** `0089_episodic_procedural_memory.py`

---

### 21. `app/state_runtime/reflexion_store.py` — ReflexionStore

**Primary file:** `app/state_runtime/reflexion_store.py`  
**Lines:** ~200  
**Responsibility:** Stores and recalls failure lessons from reflection nodes. Lazy DB hydration: first call loads from DB into memory, subsequent calls are in-memory.

**Key functions:** `recall(goal, tenant_id)` → `list[str]` lessons. `store_lesson(goal_id, lesson, tenant_id)`. Lazy hydration via `_ensure_loaded()`.

**DB tables:** `reflexion_store` (columns: `id`, `tenant_id`, `goal_id`, `lesson_text`, `embedding`, `created_at`)

---

### 22. `app/rag/semantic_cache.py` — SemanticCache

**Primary file:** `app/rag/semantic_cache.py`  
**Lines:** ~300  
**Responsibility:** 3-layer LLM call deduplication: exact hash → semantic embedding similarity → persistent DB. Prevents redundant LLM calls for similar goals.

**DB tables:** `semantic_cache_entries`

**Migration:** `0078_semantic_cache_entries.py`

---

## Ingestion Pipeline

---

### 23. `app/ingestion/orchestrator.py` — IngestionOrchestrator

**Responsibility:** Coordinates the full document ingestion pipeline: download → parse → classify → chunk → embed → ingest into KnowledgeStore. Fire-and-forget via Celery or asyncio task.

---

### 24. `app/ingestion/content_classifier.py` — ContentClassifier

**Responsibility:** Classifies document content type (code, prose, structured data, table) and domain (technical, legal, financial, etc.) to select optimal chunking strategy.

---

### 25. `app/ingestion/parsers/` — 5 Parsers

| Parser | Handles |
|--------|---------|
| `pdf_parser.py` | PDF via PyMuPDF, extracts text + page numbers |
| `docx_parser.py` | DOCX via python-docx |
| `html_parser.py` | HTML via BeautifulSoup4, strips scripts/styles |
| `markdown_parser.py` | Markdown via mistune |
| `code_parser.py` | Source code, preserves structure with function-level chunking |

---

### 26. `app/ingestion/chunkers/` — 7 Chunkers

| Chunker | Strategy |
|---------|---------|
| `fixed_size.py` | Token-count based, with overlap |
| `sentence.py` | Sentence boundary splitting |
| `semantic.py` | Embedding similarity breakpoints |
| `parent_child.py` | Hierarchical: large parent + small child |
| `sentence_window.py` | Sentence with ±k window |
| `recursive.py` | Recursive character splitting |
| `code_aware.py` | AST-aware for code (function/class boundaries) |

---

## Orchestration & Routing

---

### 27. `app/orchestration/runtime_profile_builder.py` — RuntimeProfileBuilder

**Primary file:** `app/orchestration/runtime_profile_builder.py`  
**Lines:** 159  
**Responsibility:** Builds `GoalRuntimeProfile` from goal text. Coordinates `GoalClassifier` (fast heuristic classification) → `PatternSelector` (strategy lookup) → profile assembly.

**Key functions:** `build_with_trace(goal, tenant_id, goal_id)` → `(GoalRuntimeProfile, DecisionTrace)`. Measures latency per step for the decision trace.

**Dependencies:** `app.orchestration.goal_classifier`, `app.orchestration.pattern_selector`, `app.orchestration.strategy_registry`

---

### 28. `app/orchestration/strategy_registry.py` — StrategyRegistry

**Responsibility:** Registry of 90 named execution strategies. Each strategy maps `(complexity, domain, risk)` → `{reasoning_patterns, rag_strategy, model_plan, security}`. `build_default_registry()` seeds the 90 built-in entries.

---

### 29. `app/orchestration/pattern_selector.py` — PatternSelector

**Responsibility:** Selects `AgentPatternConfig` from `StrategyRegistry` based on `GoalProperties`. Also routes RAG strategy via `select_rag_strategy()`.

---

### 30. `app/agent/pattern_assembler.py` — PatternAssembler

**Responsibility:** Applies `_RULES` (ordered rule list) against `GoalRuntimeProfile` to determine which LangGraph feature flags to set: `enable_cot`, `enable_reflection`, `enable_self_refine`, `enable_tree_of_thoughts`, `enable_peer_review`, etc.

---

### 31. `app/agent/dynamic_graph.py` — DynamicGraphAssembler

**Responsibility:** Post-profile assembly: takes a `GoalRuntimeProfile` and constructs the `AgentGraph` kwargs dict. Used by `GoalService._make_agent_loop_for_tenant()` when DYNAMIC_ORCHESTRATION is on.

---

## AI / Model Layer

---

### 32. `app/ai_router/model_orchestrator.py` — ModelOrchestrator

**Responsibility:** Routes planning calls to the best available model given task type, cost budget, and latency requirements. `ModelOrchestratorAdapter` wraps it as a drop-in for `AgentGraph._node_plan`.

---

### 33. `app/ai_router/router.py` — AIRouter

**Responsibility:** Top-level AI routing: given a goal and available providers, selects the provider + model combination. Considers: cost, latency, capability (vision, tool_use, json_mode).

---

## MCP Tool Layer

---

### 34. `app/mcp/client.py` — MCPClient

**Primary file:** `app/mcp/client.py`  
**Lines:** 1228  
**Responsibility:** HTTP client for MCP tool discovery and execution. Handles JSON-RPC, SSE streaming, builtin connectors, SSRF guard, secret resolution, OAuth, circuit breakers.

**Key classes/functions:**

| Symbol | Description |
|--------|-------------|
| `MCPClient.discover_tools(tenant_ctx)` | Lists all tools across tenant's registered servers. Caches 60s. |
| `MCPClient.call_tool(tool_name, arguments, tenant_ctx)` | Full dispatch: builtin check → SSRF guard → secret resolve → WebSocket or HTTP → circuit breaker |
| `MCPClient._dispatch_mcp_json_rpc(url, tool, args, headers)` | JSON-RPC 2.0 `tools/call` method |
| `ToolDefinition` | Dataclass: `name`, `description`, `input_schema`, `server_id`, `server_name` |
| `ToolCallResult` | Dataclass: `tool_name`, `success`, `output`, `error`, `server_id` |
| `_is_mcp_endpoint(url)` | Detects `/mcp` or `/mcp/authv2` endpoint |
| `_extract_credentials_from_server(cfg)` | Merges `auth_config` + server URL, prevents Jira Cloud URL overwrite |

**Dependencies:** `httpx`, `app.mcp.registry`, `app.net.ssrf_guard`, `app.providers.vault`

**DB tables:** None directly; reads from MCPRegistry (Redis)

**Tests:** `tests/mcp/test_client.py`

---

### 35. `app/mcp/registry.py` — MCPRegistry

**Primary file:** `app/mcp/registry.py`  
**Lines:** ~400  
**Responsibility:** Per-tenant connector registry backed by Redis. HSET/HGETALL `connectors:{tenant_id}`. Supports OAuth flow state management.

**Key functions:** `register_server(config, tenant_ctx)`, `list_servers(tenant_ctx)`, `get_server(server_id, tenant_ctx)`, `deregister_server(server_id, tenant_ctx)`

**DB tables:** Primarily Redis (`connectors:{tenant_id}`). Also `mcp_connectors` for persistent storage.

**Migration:** `0006_mcp.py`, `0018_connector_health_snapshots.py`

---

### 36. `app/mcp/ws_client.py` — MCPWebSocketClient

**Responsibility:** WebSocket transport for MCP servers that don't expose plain HTTP. Maintains persistent connection, handles reconnect.

---

## Evals & Self-Improvement

---

### 37. `app/evals/runtime_scorecard.py` — RuntimeScorecard

**Responsibility:** 9-dimension goal scoring run after every verification: `grounding_score`, `plan_efficiency`, `tool_selection_quality`, `hitl_rate`, `retry_rate`, `cost_efficiency`, `step_success_rate`, `context_utilization`, `output_completeness`. Returns `RuntimeScorecardResult{dim_scores: dict, overall: float}`.

---

### 38. `app/evals/self_improvement_engine.py` — SelfImprovementEngine

**Responsibility:** When `overall_score < improvement_threshold (0.7)`, calls LLM to generate improvement suggestion. Stores in `self_optimization_suggestions` table.

---

### 39. `app/intelligence/prompt_optimizer.py` — PromptOptimizer

**Responsibility:** Bayesian prompt variant selection. `record_result(variant_id, score)` → updates arm weights. `select_best_variant()` → picks highest-weight variant for next run. Variants stored in `prompt_variants` table.

**Migration:** `0029_prompt_variants.py`

---

### 40. `app/intelligence/self_optimizer_v2.py` — SelfOptimizerV2

**Responsibility:** Thompson-sampling Bayesian A/B testing for agent configurations. `get_arm_config(agent_id, goal_id, tenant_id)` selects arm. `record_arm_result(agent_id, arm, score)` updates Beta distribution parameters. Fixed 4 critical bugs vs v1 (race conditions, metric leakage, schema errors, DB commit missing).

**DB tables:** `experiments`, `experiment_arms`

**Migration:** `0061_self_improvement.py`, `170245f26dcb_add_self_optimization_suggestions_table.py`

**Flags/env vars:** `SELF_OPTIMIZER_V2_ENABLED`

---

## Observability

---

### 41. `app/observability/logging.py` — Structured Logging

**Responsibility:** `configure_logging(level, json_logs)` wires `structlog` with JSON renderer (production) or console renderer (development). `get_logger(name)` returns a bound logger.

**Flags/env vars:** `LOG_LEVEL`, `ENVIRONMENT` (production → JSON logs)

---

### 42. `app/observability/metrics.py` — Prometheus Metrics

**Responsibility:** All Prometheus counters/histograms: `record_goal_completed`, `record_goal_failed`, `record_goal_started`, `record_goal_duration`, `record_tool_call`, `record_plan_duration`, `record_verify_duration`, `record_cost_usd`, `record_approval_wait`, `track_tool_call` (context manager).

**Depended on by:** `app.agent.graph` (all node completions), `app.services.goal_service`

---

### 43. `app/observability/tracing.py` — OpenTelemetry Tracing

**Responsibility:** `configure_tracing()` wires OTel GRPC exporter to `OTEL_EXPORTER_OTLP_ENDPOINT`. Spans created in AgentGraph nodes via `self._tracer.start_as_current_span()`.

---

### 44. `app/observability/runtime_decision_trace.py` — RuntimeSSEEmitter

**Responsibility:** Emits 9 structured SSE event types for frontend observability: `pattern_assembled`, `runtime_profile_selected`, `rag_strategy_selected`, `embedding_strategy_selected`, `chunking_strategy_selected`, `model_routing_decision`, `tool_risk_assessed`, `guardrail_evaluated`, `cost_projection_computed`.

---

## Guardrails & Governance

---

### 45. `app/intelligence/guardrails.py` — GuardrailChecker

**Primary file:** `app/intelligence/guardrails.py`  
**Lines:** 226  
**Responsibility:** Input/output validation before and after LLM calls. Prompt injection detection, dangerous command blocking, PII detection, tool name allowlist.

**Key functions:**

| Function | Detects |
|----------|---------|
| `check_goal(goal)` | 15 injection phrases + base64-encoded phrases + dangerous commands (`rm -rf`, `DROP TABLE`, etc.) + Unicode normalization bypass |
| `check(tool_name, args)` | Tool name in known_tools allowlist |
| `check_output(text)` | PII patterns (SSN regex, credit card regex) + credential leakage |

**Pattern libraries:** `_INJECTION_PHRASES` (15), `_DANGEROUS_PATTERNS` (8 regex), `_PII_PATTERNS` (3 regex)

---

### 46. `app/guardrails_v2/engine.py` — GuardrailsV2

**Responsibility:** Declarative guardrail engine with per-layer policies. Layers: `INPUT`, `TOOL_CALL`, `OUTPUT`. Rules loaded from DB (`guardrails` table) and Redis-cached. `guardrails_engine` singleton.

**Migration:** `0055_guardrails.py`, `0057_audit_rails_v2.py`

---

### 47. `app/security_runtime/guardrail_enforcer.py` — GuardrailEnforcer

**Responsibility:** Profile-based tool argument enforcement. Given a `GovernanceProfile`, validates tool arguments against profile-specific constraints (e.g., `max_file_size`, `allowed_paths`, `blocked_operations`).

---

### 48. `app/governance/hitl.py` — HITLGateway

**Primary file:** `app/governance/hitl.py`  
**Lines:** 574  
**Responsibility:** Human-in-the-loop approval queue. Dual-mode: in-process `asyncio.Event` (single replica) + Redis BLPOP (cross-replica). HITL timeout defaults to 300s.

**Key classes/functions:**

| Symbol | Description |
|--------|-------------|
| `HITLGateway.request_approval(goal_id, action, risk_level, tenant_ctx)` | Creates `ApprovalRequest`, sets event, optionally pushes to Redis |
| `HITLGateway.approve(request_id, approver, note)` | Marks request approved, sets asyncio.Event, publishes to Redis `hitl:{request_id}` |
| `HITLGateway.reject(request_id, approver, note)` | Marks request rejected |
| `HITLGateway._wait_for_result(request_id, timeout)` | Async wait on Redis BLPOP with timeout |
| `ApprovalRequest` | Dataclass: `goal_id`, `action`, `risk_level`, `request_id`, `status`, `required_approvers`, `approvals_received` |
| `ApprovalStatus` | StrEnum: `PENDING`, `APPROVED`, `REJECTED`, `TIMED_OUT` |

**DB tables:** `hitl_approvals`

**Migration:** `0005_governance.py`

---

### 49. `app/governance/cost.py` — CostController

**Primary file:** `app/governance/cost.py`  
**Lines:** 483  
**Responsibility:** Per-goal and per-tenant-daily budget enforcement using Redis Lua atomic increment scripts.

**Key functions:**

| Function | Description |
|----------|-------------|
| `check_and_record(goal_id, cost_usd, tenant_ctx, tool_name)` | Atomically checks and records cost. Returns `True` if within budget. |
| `get_goal_spend(goal_id, tenant_id)` | Current spend for a goal |
| `get_daily_spend(tenant_id)` | Today's tenant total |

**Redis keys:** `goal_budget:{goal_id}`, `daily_budget:{tenant_id}:{YYYY-MM-DD}`  
**Defaults:** `per_goal_usd=10.0`, `per_tenant_daily_usd=500.0`

---

### 50. `app/governance/policies.py` — PolicyEngine

**Primary file:** `app/governance/policies.py`  
**Lines:** ~400  
**Responsibility:** Declarative tool policy engine. Rules loaded from DB and Redis-cached. Propagated across replicas via Redis pub/sub. Returns ALLOW / DENY / REQUIRE_APPROVAL per (tool_name, tenant).

**DB tables:** `policy_rules`

**Migration:** `0015_governance_policy_table.py`, `0080_policy_rules.py`

---

### 51. `app/governance/audit.py` — AuditLog

**Responsibility:** Append-only event trail. In-memory WAL buffer flushed every 10s via Celery beat task `flush_audit_wal`. SOC2-compliant: immutable after commit. `AuditEvent` dataclass captures `event_type`, `tenant_id`, `goal_id`, `user_id`, `metadata`, `timestamp`.

**DB tables:** `audit_events`

**Migration:** `0005_governance.py`, `0016_audit_soc2_fields.py`, `0031_audit_immutability.py`, `0057_audit_rails_v2.py`

---

## Tenancy & Auth

---

### 52. `app/tenancy/middleware.py` — TenantMiddleware

**Primary file:** `app/tenancy/middleware.py`  
**Lines:** 357  
**Responsibility:** API key auth, rate limiting, CORS preflight handling, security headers. Bypasses auth for health/docs/signup paths.

**Key functions:** `_extract_key(request)` — Bearer token OR X-API-Key. `_check_rate_limit_with_fallback()` — Redis sliding window with in-process fallback (120 rpm cap).

**Bypass paths:** `/health`, `/metrics`, `/docs`, `/redoc`, `/openapi.json`, `/tenants/signup`, `/auth/*`, `/integrations/`, `/billing/webhook`

---

### 53. `app/tenancy/context.py` — TenantContext

**Responsibility:** Immutable tenant context set on `request.state.tenant`. Fields: `tenant_id`, `plan_tier: PlanTier`, `rpm_limit`, `api_key_id`, `scopes: frozenset[str]`.

`PlanTier` StrEnum: `FREE`, `STARTER`, `PROFESSIONAL`, `ENTERPRISE`

---

### 54. `app/db/rls.py` — Row-Level Security

**Primary file:** `app/db/rls.py`  
**Lines:** 88  
**Responsibility:** PostgreSQL RLS context managers. `sqlalchemy_rls_context(session, tenant_id)` → `SET LOCAL app.tenant_id = '{tid}'` inside transaction. `system_session(session)` → `SET LOCAL row_security = off` for maintenance.

**Key functions:** `rls_context(conn, tenant_id)` (asyncpg), `sqlalchemy_rls_context(session, tenant_id)` (SQLAlchemy), `system_session(session)`

---

## Reliability

---

### 55. `app/reliability/circuit_breaker.py` — CircuitBreaker

**Responsibility:** Per-service circuit breaker. States: CLOSED → HALF_OPEN → OPEN. Thresholds: 5 failures in 60s → OPEN. 30s probe timeout → HALF_OPEN. `RedisCircuitBreaker` stores state in Redis for cross-replica consistency.

Also: `app/providers/circuit_breaker.py` — `call_with_circuit_breaker(provider, method, request)` context manager used by AgentGraph LLM calls.

---

### 56. `app/reliability/rollback.py` — RollbackEngine

**Primary file:** `app/reliability/rollback.py`  
**Lines:** 174  
**Responsibility:** LIFO stack of `(action, inverse_fn)` pairs. `rollback_all()` executes in reverse registration order. `rollback_all_async()` for async inverses.

**Key enum:** `RollbackAction` — `CREATE_FILE`, `DELETE_FILE`, `MODIFY_FILE`, `CREATE_BRANCH`, `DELETE_BRANCH`, `CREATE_PR`, `CLOSE_PR`, `CREATE_TICKET`, `CLOSE_TICKET`, `SEND_MESSAGE`, `CUSTOM`

---

### 57. `app/reliability/bulkhead.py` — BulkheadRegistry

**Responsibility:** Per-tenant concurrency limits (bulkhead pattern). `BulkheadRegistry.acquire(tenant_id)` blocks when tenant is at `max_concurrent` goals. `RedisBulkheadRegistry` for cross-replica enforcement.

---

### 58–59. `app/reliability/dedup.py` / `app/reliability/idempotency.py`

**Responsibility:**  
- `DeduplicationCache` / `RedisDeduplicationCache`: Prevents duplicate goal submissions within window. Key: `hash(goal_text + tenant_id)` with TTL.  
- `IdempotencyStore`: Stores request fingerprints to make goal submission idempotent across retries.

---

## Scaling — Celery

---

### 60. `app/scaling/celery_app.py` — Celery Configuration

**Primary file:** `app/scaling/celery_app.py`  
**Lines:** 238  
**Responsibility:** Celery app configuration, per-plan queue routing, Redis Sentinel support, RedBeat HA beat scheduler, beat schedule definition.

**PLAN_QUEUE_MAP:**
```python
{"free": "goals.free", "starter": "goals.starter",
 "professional": "goals.professional", "enterprise": "goals.enterprise"}
```

**Beat schedule (selected):**

| Task | Interval |
|------|----------|
| `check_mcp_health` | Every 30s |
| `fire_due_schedules` | Every 60s |
| `record_queue_depths` | Every 30s |
| `detect_stuck_goals` | Every 5 min |
| `expire_hitl_approvals` | Every 60s |
| `flush_audit_wal` | Every 10s |
| `enforce_hitl_sla` | Every 5 min |
| `scan_cost_anomalies` | Every hour |
| `execute_retention_policy` | 3 AM UTC daily |
| `run_goal_dlq` (DLQ drain) | Every 5 min |

**Flags/env vars:** `REDIS_URL`, `REDIS_SENTINEL_URLS`, `REDIS_SENTINEL_MASTER`, `REDIS_SENTINEL_PASSWORD`

---

### 61. `app/scaling/tasks.py` — Celery Tasks

**Primary file:** `app/scaling/tasks.py`  
**Lines:** 2918  
**Responsibility:** All Celery task implementations. `run_goal` is the primary task. Includes SIGTERM graceful shutdown, per-process Redis pool, per-worker LangGraph checkpointer.

**Key functions:**

| Function | Queue | Description |
|----------|-------|-------------|
| `run_goal(goal_id, tenant_data)` | `goals.{plan}` | Acquires `_SyncGoalLock`, builds AgentGraph, runs asyncio loop |
| `run_goal_dlq(goal_id, tenant_data)` | `goals_dlq` | DLQ retry with extended timeout |
| `fire_due_schedules()` | `schedules` | Queries `trigger_schedules` for due tasks, fires `run_scheduled_goal` |
| `detect_stuck_goals()` | `maintenance` | Finds goals stuck in EXECUTING without Redis lock |
| `check_mcp_health()` | `maintenance` | HTTP health check on all registered MCP servers |
| `flush_audit_wal()` | `maintenance` | Bulk flush audit WAL to DB |

**Key class:** `_SyncGoalLock` — synchronous Redis `SET NX PX` + Lua check-and-delete release. Prevents duplicate goal execution across replicas.

---

## Key Test Files

| Source module | Primary test file | Coverage notes |
|--------------|-------------------|----------------|
| `app/agent/graph.py` | `tests/agent/test_loop.py`, `tests/agent/test_graph.py` | Node execution, routing, feature flags |
| `app/agent/state.py` | `tests/agent/test_state.py` | Serialization |
| `app/services/goal_service.py` | `tests/services/test_goal_service.py` | submit_goal, SSE, Celery bridge |
| `app/mcp/client.py` | `tests/mcp/test_client.py` | builtin dispatch, SSRF guard, circuit breaker |
| `app/mcp/registry.py` | `tests/mcp/test_registry.py` | Redis registration |
| `app/rag/engine.py` | `tests/rag/test_engine.py` | RRF fusion, all 3 legs, edge cases |
| `app/rag/store.py` | `tests/rag/test_store.py` | hybrid_search, ingest, parent expansion |
| `app/governance/hitl.py` | `tests/governance/test_hitl.py` | approval, rejection, timeout |
| `app/governance/cost.py` | `tests/governance/test_cost.py` | budget enforcement, Redis Lua, daily reset |
| `app/governance/policies.py` | `tests/governance/test_policies.py` | ALLOW/DENY/REQUIRE rules |
| `app/tenancy/middleware.py` | `tests/tenancy/test_middleware.py` | auth, rate limit, bypass paths |
| `app/db/rls.py` | `tests/db/test_rls.py` | SET LOCAL, system_session |
| `app/reliability/circuit_breaker.py` | `tests/reliability/test_circuit_breaker.py` | state transitions |
| `app/reliability/rollback.py` | `tests/reliability/test_rollback.py` | LIFO order |
| `app/scaling/tasks.py` | `tests/scaling/test_tasks.py` (integration) | run_goal with testcontainers |
| `app/memory/long_term.py` | `tests/memory/test_long_term.py` | recall, extract |
| `app/context/context_pipeline.py` | `tests/context/test_pipeline.py` | 7-step pipeline |
| `app/intelligence/guardrails.py` | `tests/intelligence/test_guardrails.py` | injection patterns |

**Coverage gaps:** `app/agent/patterns/` pattern adapters have minimal unit tests. `app/enterprise/` compliance/red-team modules are lightly tested. `app/ai_router/` has no integration tests.

**Test markers:**
- `@pytest.mark.integration` — requires Docker (testcontainers, Postgres, Redis). Set `DOCKER_HOST` + `TESTCONTAINERS_RYUK_DISABLED=true`.
- `@pytest.mark.slow` — real LLM calls. Opt-in only.
- Default run (`pytest`) — unit tests only, uses `FakeProvider` and `_FakeRedis`.

---

## Cross-Module Dependency Graph

```
app.main
    ├── app.core.config (Settings)
    ├── app.core.pools (ConnectionPools)
    ├── app.providers.registry → [anthropic_provider | openai_compatible | gemini_provider | groq | ollama | fake]
    ├── app.services.goal_service
    │       ├── app.agent.graph
    │       │       ├── app.agent.state
    │       │       ├── app.agent.prompts
    │       │       ├── app.providers.base (LLMProvider Protocol)
    │       │       ├── app.agent.model_router
    │       │       ├── app.governance.* (hitl, cost, audit, policies)
    │       │       ├── app.reliability.* (circuit_breaker, rollback, dedup)
    │       │       ├── app.memory.* (execution, long_term, episodic, procedural)
    │       │       ├── app.rag.store (KnowledgeStore)
    │       │       ├── app.rag.engine (hybrid_search, retrieve)
    │       │       ├── app.context.context_pipeline
    │       │       ├── app.mcp.client
    │       │       ├── app.intelligence.guardrails
    │       │       ├── app.orchestration.runtime_profile_builder
    │       │       └── app.intelligence.eval_runner
    │       ├── app.services.goal_queue (CeleryGoalTaskQueue)
    │       └── app.services.event_store
    ├── app.mcp.registry → Redis
    ├── app.mcp.client → app.net.ssrf_guard, app.providers.vault
    ├── app.tenancy.middleware → app.tenancy.rate_limiter → Redis
    ├── app.db.rls → PostgreSQL GUC
    └── app.scaling.celery_app → Redis (broker + backend)
            └── app.scaling.tasks
                    └── app.services.goal_service (re-used)

app.rag.engine ──────────────────────────────────────────────────────
    ├── SQLAlchemy AsyncSession (knowledge_chunks_{dim})
    └── app.providers.base (optional, for HyDE/fusion LLM calls)

app.context.context_pipeline
    ├── app.context.rerank_policy → app.rag.cross_encoder (optional)
    ├── app.context.context_budget
    ├── app.context.citation_manager
    ├── app.context.prompt_builder
    └── app.rag.agentic.citation_threader

app.governance.cost → Redis (Lua scripts)
app.governance.hitl → asyncio.Event | Redis BLPOP
app.governance.policies → Redis pub/sub
app.governance.audit → in-memory WAL → PostgreSQL (flush via Celery)
```

---

## Migration Timeline

All 89 migrations in `app/db/migrations/versions/`. Prefix = order, never reorder.

| Migration | Purpose |
|-----------|---------|
| `0001_baseline.py` | Initial schema: `tenants`, `api_keys` tables |
| `0002_tenancy.py` | Tenant plan tiers, RLS policies on tenants |
| `0003_agents.py` | `agents` table: id, tenant_id, name, system_prompt, config JSON |
| `0004_goals.py` | `goals` table: id, tenant_id, goal_text, status, agent_id, created_at, result |
| `0005_governance.py` | `hitl_approvals`, `audit_events` tables |
| `0006_mcp.py` | `mcp_connectors` table: id, tenant_id, name, url, auth_config (encrypted) |
| `0007_scheduling.py` | `trigger_schedules` table: cron_expression, next_run, last_run |
| `0008_knowledge.py` | `knowledge_collections`, `knowledge_chunks_1536` tables + pgvector extension |
| `0009_intelligence.py` | `eval_results`, `self_optimizer_runs` tables |
| `0010_goal_agent_binding.py` | FK constraint: goals.agent_id → agents.id |
| `0011_goal_events_checkpoints.py` | `goal_events` table, LangGraph checkpoint tables |
| `0012_collab_metadata.py` | `collab_sessions` table for collaborative editing |
| `0013_cost_ledger_agent_id.py` | `cost_ledger` table: goal_id, tool_name, tokens, cost_usd |
| `0014_agent_versioning.py` | `agent_versions` table: snapshot of agent config at each change |
| `0015_governance_policy_table.py` | `policy_rules` table |
| `0016_audit_soc2_fields.py` | Add `event_hash`, `correlation_id` to audit_events (SOC2 trail) |
| `0017_artifacts_table.py` | `artifacts` table: goal_id, type, s3_key, content_type |
| `0018_connector_health_snapshots.py` | `connector_health_snapshots` table: latency_ms, status, checked_at |
| `0019_agent_system_prompt.py` | Add `system_prompt` column to agents |
| `0020_tool_capabilities.py` | `tool_capabilities` table: tool_name, capability_tags, reliability_score |
| `0021_eval_suites.py` | `eval_suites`, `eval_suite_runs` tables |
| `0022_rbac_tables.py` | `roles`, `role_assignments` tables |
| `0023_a2a_tasks.py` | `a2a_tasks` table for agent-to-agent delegation |
| `0024_ltm_embedding.py` | `long_term_memory` table with pgvector `embedding` column (1536-dim) |
| `0025_agent_snapshots.py` | `agent_snapshots` table for rollback |
| `0026_compliance_requests.py` | `compliance_requests` table (GDPR/DSAR) |
| `0027_decision_traces.py` | `decision_traces` table for orchestration observability |
| `0028_ltm_embedding_resize.py` | Alter `long_term_memory.embedding` to support variable dims |
| `0029_prompt_variants.py` | `prompt_variants` table: variant_id, prompt_text, arm_weight |
| `0030_tool_capabilities.py` | Adds `last_success_at`, `failure_count` to tool_capabilities |
| `0031_audit_immutability.py` | Trigger: prevents UPDATE/DELETE on audit_events |
| `0032_benchmark_runs.py` | `benchmark_runs` table |
| `0033_agent_schema_upgrade.py` | Add `capabilities`, `allowed_collection_ids` to agents |
| `0034_fix_snapshot_rls.py` | RLS policy fix on agent_snapshots |
| `0035_knowledge_citations.py` | `knowledge_citations` table: chunk references in goal results |
| `0036_artifacts_retention.py` | Add `expires_at` to artifacts, retention policy trigger |
| `0037_tool_reliability_memory.py` | `tool_reliability_memory` table: per-tool reliability tracking |
| `0038_marketplace_versions.py` | `marketplace_template_versions` table |
| `0039_golden_tasks.py` | `golden_tasks` table for eval ground truth |
| `0040_vault_key_versions.py` | `vault_key_versions` for key rotation support |
| `0041_per_agent_credentials.py` | `agent_credentials` table: per-agent encrypted secrets |
| `0042_consent_records.py` | `consent_records` table (GDPR consent tracking) |
| `0043_debate_audit.py` | `debate_audit` table for multi-agent debate logs |
| `0044_merge_migration_chains.py` | Alembic merge head (no schema changes) |
| `0045_civilization.py` | `civilizations`, `civilization_agents` tables |
| `0046_workflows.py` | `workflows`, `workflow_steps` tables |
| `0047_notification_channels.py` | `notification_channels` table |
| `0048_goal_templates.py` | `goal_templates` table |
| `0053_agent_credentials.py` | Adds `credential_type` to agent_credentials |
| `0054_scopes_rbac.py` | Adds `scopes` column to api_keys |
| `0055_guardrails.py` | `guardrails` table: declarative guardrail rules |
| `0056_governance_v2.py` | `governance_profiles` table |
| `0057_audit_rails_v2.py` | Partitioned `audit_events_v2` table (monthly partitions) |
| `0058_cost_optimization.py` | `cost_optimization_rules` table |
| `0059_marketplace_v2.py` | `marketplace_listings` table with monetization fields |
| `0060_enterprise_v2.py` | Enterprise feature flags table |
| `0061_self_improvement.py` | `experiments`, `experiment_arms` tables |
| `0062_knowledge_v2.py` | Add `knowledge_graph_nodes`, `knowledge_graph_edges` tables |
| `0063_civilization_history.py` | `civilization_history` table |
| `0064_goal_lineage.py` | Add `parent_goal_id` to goals (goal tree) |
| `0065_loop_engineering.py` | `agent_loop_configs` table |
| `0066_tenant_settings.py` | `tenant_settings` JSON column |
| `0067_consent_records_v2.py` | Add `dpo_reviewed_at` to consent_records |
| `0068_fix_oauth_and_knowledge_types.py` | Type fixes for OAuth state and knowledge metadata |
| `0069_rename_knowledge_embedding_model.py` | Rename column in knowledge_collections |
| `0070_api_keys_roles_column.py` | Add `roles` JSON to api_keys |
| `0071_fix_rls_missing_tables.py` | Add missing RLS policies to 12 tables |
| `0072_users_and_memberships.py` | `users`, `team_memberships` tables |
| `0073_verifier_calibration.py` | `verifier_calibration` table: tracks verifier accuracy |
| `0074_skills_table.py` | `skills` table: reusable agent skills |
| `0075_solutions_table.py` | `solutions` table: packaged multi-agent solutions |
| `0076_golden_datasets.py` | `golden_datasets` table |
| `0077_usage_billing.py` | `usage_records`, `invoices` tables |
| `0078_semantic_cache_entries.py` | `semantic_cache_entries` table |
| `0079_marketplace_monetization.py` | Monetization fields on marketplace_listings |
| `0080_policy_rules.py` | Adds `priority`, `condition_json` to policy_rules |
| `0081_dpdp_consent.py` | DPDP (India) consent records |
| `0082_goal_feedback.py` | `goal_feedback` table: user thumbs up/down |
| `0083_gst_invoices.py` | GST fields on invoices |
| `0084_add_tenant_mfa.py` | `mfa_configs` table |
| `0085_add_knowledge_graph.py` | Indexes on knowledge_graph_nodes/edges |
| `0086_add_runtime_profile_fields_to_goals.py` | Add `runtime_profile_id`, `complexity` to goals |
| `0087_add_orchestration_tables.py` | `orchestration_decisions` table |
| `0088_add_memory_conflicts.py` | `memory_conflicts` table |
| `0089_episodic_procedural_memory.py` | `episodic_memory`, `procedural_patterns` tables |
| `0090_parent_child_retrieval.py` | `knowledge_chunk_parents` table for parent-child chunking |
| `170245f26dcb_...py` | `self_optimization_suggestions` table |

---

## Environment Variables Reference

| Variable | Default | Effect |
|----------|---------|--------|
| `ENVIRONMENT` | `development` | `production` → JSON logs, refuses FakeProvider, strict DB credentials |
| `DATABASE_URL` | — | asyncpg DSN. Production refuses default `agentverse:agentverse@` credentials |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis for pub/sub, rate limiting, circuit breakers, cost tracking |
| `REDIS_SENTINEL_URLS` | — | Comma-separated Sentinel nodes → enables HA mode |
| `REDIS_SENTINEL_MASTER` | `mymaster` | Sentinel master name |
| `REDIS_SENTINEL_PASSWORD` | — | Sentinel auth password |
| `MANAGE_POOLS` | `0` | `1`/`true`/`yes` → enables DB/Redis pool management in lifespan |
| `LOG_LEVEL` | `info` | structlog level |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins for CORS |
| `ANTHROPIC_API_KEY` | — | Enables AnthropicProvider (planning: claude-opus-4-8, execution: sonnet-4-5, verifier: haiku-3-5) |
| `OPENAI_API_KEY` | — | Enables OpenAICompatibleProvider (planning: gpt-5.2, execution/verifier: gpt-4o-mini) |
| `GOOGLE_API_KEY` | — | Enables GeminiProvider |
| `GROQ_API_KEY` | — | Enables GroqProvider (llama-3.1-70b planning, llama-3.1-8b execution) |
| `OLLAMA_BASE_URL` | — | Enables OllamaProvider (llama3.2 for all task types) |
| `VERIFIER_API_KEY` | — | Dedicated verifier LLM key (separate from executor for cross-model verification) |
| `VOYAGE_API_KEY` | — | Enables VoyageProvider for embeddings (priority over OpenAI embeddings) |
| `SENTENCE_TRANSFORMERS_MODEL` | — | Enables LocalEmbedProvider (e.g., `all-MiniLM-L6-v2`) for offline embeddings |
| `LLM_PROVIDERS` | — | JSON array override: `[{"provider": "anthropic", "api_key": "...", "priority": 1}]` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | OTel GRPC endpoint for distributed tracing (e.g., `http://jaeger:4317`) |
| `DYNAMIC_ORCHESTRATION` | `false` | Enables RuntimeProfileBuilder for per-goal orchestration strategy selection |
| `ENABLE_RAG_STRATEGY_ROUTING` | `false` | Enables per-goal RAG strategy selection (FLARE/HyDE/fusion etc.) |
| `ENABLE_PATTERN_SSE_EVENTS` | `false` | Emits `pattern_assembled` SSE events to frontend |
| `SELF_OPTIMIZER_V2_ENABLED` | `false` | Enables SelfOptimizerV2 Bayesian A/B testing |
| `CELERY_BROKER_URL` | (derived from REDIS_URL) | Override Celery broker URL |
| `DOCKER_HOST` | — | Required for testcontainers integration tests (colima socket path) |
| `TESTCONTAINERS_RYUK_DISABLED` | `false` | Set `true` for colima compatibility |
| `SECRET_KEY` | — | JWT signing key for SSO tokens |
| `VAULT_ENCRYPTION_KEY` | — | AES key for connector secret encryption in vault |
| `ALLOWED_HOSTS` | `*` | Comma-separated trusted host names |
| `MAX_CONCURRENT_GOALS_FREE` | `2` | Concurrent goal limit for free tier |
| `MAX_CONCURRENT_GOALS_STARTER` | `5` | Concurrent goal limit for starter tier |
| `MAX_CONCURRENT_GOALS_PROFESSIONAL` | `20` | Concurrent goal limit for professional tier |
| `MAX_CONCURRENT_GOALS_ENTERPRISE` | `100` | Concurrent goal limit for enterprise tier |
| `DAILY_GOAL_LIMIT_FREE` | `50` | Daily goal submissions for free tier |
| `HITL_TIMEOUT_SECONDS` | `300` | Default HITL approval timeout |
| `GOAL_BUDGET_USD` | `10.0` | Default per-goal LLM spend cap |
| `TENANT_DAILY_BUDGET_USD` | `500.0` | Default tenant daily LLM spend cap |
| `MAX_AGENT_ITERATIONS` | `100` | AgentGraph max replanning iterations |
| `SEARXNG_BASE_URL` | — | SearXNG instance URL for web search fallback |
| `MINIO_ENDPOINT` | — | MinIO/S3 endpoint for artifact storage |
| `MINIO_ACCESS_KEY` | — | MinIO access key |
| `MINIO_SECRET_KEY` | — | MinIO secret key |
| `SMTP_HOST` | — | SMTP server for email trigger notifications |
| `MAILPIT_URL` | — | Mailpit URL for dev email capture |

---

*All file paths and line numbers reference the `agent-verse-backend/` tree. DB table names match the Alembic migrations listed above.*
