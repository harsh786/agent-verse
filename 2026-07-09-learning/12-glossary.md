# 12 — Glossary

This glossary defines every significant term, class, pattern, and concept used in AgentVerse. Terms are listed alphabetically. Each entry provides a general software definition followed by the AgentVerse-specific meaning, usage context, and a source file reference where applicable.

---

## A

**`_node_execute`** — The execution node in the LangGraph `StateGraph`. Receives the current `Plan` from state, calls `_execute_step()` for each step in sequence, applies `GuardrailEnforcer.check_tool_args()` before each tool call, runs the `IndirectInjectionScanner` on tool results, and populates `state.executed_tool_calls`. Transitions to `_node_verify` on step completion or to the terminal failure node on a non-retryable error. Source: `app/agent/graph.py` (approx. line 1377).

**`_node_plan`** — The planning node. Constructs the full planner prompt: goal text + injected RAG context (from `ContextPipeline`) + reflexion lessons (from `ReflexionStore`) + episodic memory recalls + procedural memory recalls + any `verification_feedback` from the previous iteration. Calls the planner LLM to produce a `Plan` (list of `Step` objects). Stores `state.context["planner_variant_id"]` for A/B tracking. Emits `plan_produced` log event. Source: `app/agent/graph.py` (approx. line 1102).

**`_node_verify`** — The verification node and the central hub of the self-improvement system. Calls the verifier LLM to assess step success; populates `state.verification_feedback`. On success, fires in order: `EvalRunner.score_and_persist()`, `RuntimeScorecard.score()`, `SelfImprovementEngine.decide_actions()`, `RegressionGate.maybe_create_regression()`, `SelfOptimizerV2.on_goal_completed()`, `ABTestingEngine.record_result_async()`, and `RuntimeSSEEmitter.eval_score_recorded()`. Source: `app/agent/graph.py` (approx. line 2895).

**`_route()`** — The LangGraph conditional edge function. Reads `AgentState` after each node exit and returns the string name of the next node. Key routing decisions: `_node_verify → _node_plan` (retry with feedback), `_node_verify → complete` (goal done), `_node_verify → fail` (permanent failure or max iterations exceeded), `_node_execute → _node_verify` (step done). Source: `app/agent/graph.py`.

---

## A (continued)

**ABTestingEngine** — Module-level singleton (`ab_testing_engine`) that persists A/B experiment results to the `ab_test_results` table. `record_result_async(experiment_id, variant_id, metric, value)` accumulates per-goal, per-variant observations. `get_summary(experiment_id)` returns mean, median, p95, and significance tests. Wired in `main.py` with a `db_factory`; called in `_node_verify` after eval score computation. Source: `app/optimization/ab_testing.py`.

**AgentGraph** — The compiled LangGraph `StateGraph` encoding the plan → execute → verify → (replan | complete) state machine. Assembled by `assemble_graph()`, compiled with a `checkpointer` (Redis-backed `AsyncRedisSaver` in production, `MemorySaver` fallback). Compilation produces a runnable graph where each node is an async callable that reads and mutates `AgentState`. Source: `app/agent/graph.py`.

**AgentState** — The mutable dataclass that flows through every node of the `AgentGraph`. Key fields: `goal` (str), `goal_id` (str), `tenant_ctx` (TenantContext), `plan` (Plan), `steps` (list[Step]), `iterations` (int), `status` (GoalStatus), `final_answer` (str), `verification_feedback` (str), `ungrounded_claims` (list[str]), `provenance` (list[dict]), `cited_answer` (str), `retrieval_result` (RetrievalResult | None), `executed_tool_calls` (list[ToolCall]), `context` (dict — key-value bag for latency, cost, variant IDs, etc.). Source: `app/agent/state.py`.

**ASTChunker** — A content-aware document chunker that parses source code using Python's `ast` module and splits at function and class definition boundaries rather than arbitrary token counts. Produces semantically coherent chunks where each chunk contains exactly one function or class. Avoids splitting a function definition across two chunks (which would make both chunks useless for code search). Source: `app/rag/chunking/`.

---

## B

**BM25Retriever** — Sparse keyword-based retriever using the BM25 (Best Match 25) ranking function: combines term frequency normalised by document length with inverse document frequency. In AgentVerse, implemented via PostgreSQL's `tsvector`/`tsquery` GIN index, which approximates BM25 scoring. Complements dense vector retrieval in hybrid search; results from both are merged via RRF. Best for queries containing exact product names, error codes, and proper nouns. Source: `app/rag/retrievers/`.

**BulkheadRegistry** — Per-tenant `asyncio.Semaphore` registry preventing a concurrency burst by one tenant from monopolising async workers. Default limit: 20 concurrent operations per tenant. `RedisBulkheadRegistry` provides cross-replica enforcement using atomic Lua INCR/DECR scripts. `RedisBulkhead._SLOT_TTL = 300s` auto-reclaims slots from crashed workers. Source: `app/reliability/bulkhead.py`.

---

## C

**Celery** — Distributed task queue for asynchronous goal execution. Goals are submitted as `execute_goal_task` Celery tasks and routed to plan-tier queues: `goals.free`, `goals.starter`, `goals.professional`, `goals.enterprise`. `PLAN_QUEUE_MAP` in `celery_app.py` enforces routing. Each queue has dedicated workers preventing noisy-neighbour starvation. KEDA `ScaledObject` uses the `agentverse_desired_workers{plan="..."}` gauge to autoscale workers per tier. Source: `app/scaling/celery_app.py`, `app/scaling/tasks.py`.

**ChunkingStrategySelector** — Selects the appropriate chunking strategy for ingested content based on detected content type. Routes code to `ASTChunker`, long prose to `SentenceWindowChunker`, structured tables to a fixed-size chunker, hierarchical documents to `ParentChildChunker`. Emits `chunking_strategy_selected` SSE event with `content_type`, `strategy`, and `reason` fields. Source: `app/rag/chunking/strategy_selector.py`.

**CitationManager** — Post-processes LLM output to validate inline bracket citations (`[1]`, `[2]`). Strips hallucinated citations (bracket indices that exceed the number of retrieved chunks), verifies each cited source actually supports its associated claim, builds `AgentState.cited_answer` (clean output) and `AgentState.provenance` (list of `{"source": ..., "confidence": ..., "chunk_id": ...}` dicts). Powers the `citation_quality` dimension in `RuntimeScorecard`. Source: `app/rag/citation_manager.py`.

**CircuitBreaker** — Three-state protection against cascading failures: CLOSED (normal), OPEN (blocked after N failures), HALF_OPEN (probe allowed after cooldown). Default parameters: `failure_threshold=3`, `cooldown_seconds=60.0`. `RedisCircuitBreaker` stores state in Redis for cross-replica sharing under key `circuit:{tenant_id}:{tool_name}`. Source: `app/reliability/circuit_breaker.py`, `app/reliability/redis_circuit_breaker.py`.

**ColBERTPattern** — Late-interaction retrieval pattern where queries and documents are encoded as sequences of token-level embeddings and scored via MaxSim (maximum similarity): `score = Σ_qt max_{dt} cos(qt, dt)`. More accurate than single-vector bi-encoder similarity for complex multi-hop or entity-rich queries. Source: `app/agent/patterns/`.

**CompletionRequest** — The vendor-agnostic LLM request dataclass used throughout the providers abstraction. Fields: `messages` (list[Message]), `model` (str), `temperature` (float), `max_tokens` (int), `tools` (list | None — MCP tool schemas), `response_format` (dict | None). Each concrete provider translates this to its own API call format. Source: `app/providers/base.py`.

**ConsensusVerifier** — Multi-vote verifier used for high-risk and critical goals. Calls the verifier LLM three times with temperatures `[0.0, 0.3, 0.5]` in parallel via `asyncio.gather`. Requires 2-of-3 votes for `success=True`. Prevents a single hallucinated verification result from incorrectly passing a dangerous step. Adds 3× verifier cost and latency. Source: `app/agent/graph.py`.

**ContentClassifier** — Detects the content type of ingested documents (code, markdown, HTML, PDF, plain prose, structured data/CSV, JSON). Routes each document to the appropriate `ChunkingStrategySelector` and `EmbeddingRouter`. Source: `app/rag/ingestion/content_classifier.py`.

**ContextGapDetector** — Scans retrieved text for 12 signals indicating the knowledge base cannot answer the query: "insufficient information", "cannot determine", "not available", "no information found", "I don't know", "unclear from context", "not specified", "not mentioned", "no data available", "cannot answer", "outside the scope", "not documented". On detection, triggers CRAG before any LLM call. Source: `app/rag/agentic/retriever_tool.py`.

**ContextPipeline** — The full retrieval pipeline executed before each planner and executor LLM call. Steps: query expansion → hybrid retrieval (dense + sparse) → cross-encoder reranking → gap detection → CRAG if needed → context injection into prompt. Returns a structured context window with numbered, cited chunks. Source: `app/rag/context_pipeline.py`.

**CostController** — Enforces per-goal spending limits (`profile.model_plan.max_cost_usd`) and per-tenant daily budgets (from plan limits). `check_and_record(goal_id, cost_usd, tenant_ctx)` returns `False` when budget would be exceeded. Emits a warning event at 80% of daily budget. `RedisCostController` uses atomic Lua INCR for cross-replica accuracy. Source: `app/governance/cost.py`.

**CRAG** — Corrective Retrieval-Augmented Generation. When standard retrieval confidence < 0.5 or a context gap is detected, `retrieve_corrective()` performs web search as fallback. Returns `RetrievalResult(corrected=True, source="web", correction_reason="low_confidence"|"gap_detected")`. Source: `app/rag/agentic/retriever_tool.py`.

---

## D

**DecisionTrace** — The structured record of all orchestration decisions for a goal: runtime profile, RAG strategy, model routes, guardrail bundle, pattern assembly. Built from the 9 `RuntimeSSEEmitter` event types. Queryable via `GET /observability/goals/{goal_id}/trace`. Source: `app/observability/runtime_decision_trace.py`.

**DeduplicationCache** — In-memory per-tenant TTL cache preventing duplicate goal submissions. `RedisDeduplicationCache` extends this to cross-replica deduplication via Redis `SET NX EX` on key `dedup:{tenant_id}:{hash(goal_text)}` with 3600s TTL. Returns the existing `goal_id` so the client can be redirected to the in-progress goal. Source: `app/reliability/dedup.py`.

**DynamicGraphAssembler** — Assembles the active set of agentic patterns for a goal based on its `GoalRuntimeProfile`. Reads complexity, risk level, tenant plan capabilities, and feature flags to produce a `PatternConfig`. Emits `pattern_assembled` SSE event when `DYNAMIC_ORCHESTRATION=true`. Source: `app/orchestration/dynamic_graph_assembler.py`.

---

## E

**EmbedRequest** — The vendor-agnostic embedding request dataclass. Fields: `text` (str) or `texts` (list[str] for batch), `model` (str), `input_type` ("query" or "document" — affects normalisation in some models). Translated to provider-specific embedding calls by `EmbeddingRouter`. Source: `app/providers/base.py`.

**EmbeddingOrchestrator** — Coordinates embedding generation during ingestion and retrieval. Selects the appropriate embedding model for the content modality, handles rate-limiting and batching, caches results via `SemanticCache` to avoid re-embedding identical content. Source: `app/rag/embedding/orchestrator.py`.

**EmbeddingRouter** — Routes embedding requests to the appropriate provider (Voyage, OpenAI, Azure OpenAI, Google, local) based on modality (text/code/image/multimodal), required dimension (256/512/1024/1536/3072), and cost class. Emits `embedding_strategy_selected` SSE event with `model_id`, `modality`, `dimension`, `cost_class`. Source: `app/rag/embedding/router.py`.

**EpisodicMemoryStore** — Persists the episodic history of an agent's past goal executions: goal text, step sequence, tool calls, verification outcomes, final answer. Recalled at plan time via semantic similarity to the current goal. All queries include `WHERE tenant_id = :tid AND agent_id = :aid`. Source: `app/memory/episodic.py`.

**EvalRunner** — Scores seven dimensions on every completed goal: `task_completion`, `efficiency`, `accuracy` (LLM judge), `safety`, `coherence` (LLM judge), `sla`, `tool_relevance`. `score_and_persist()` writes to the `eval_results` table. Distinct from `RuntimeScorecard` — `EvalRunner` focuses on output quality; `RuntimeScorecard` focuses on execution quality. Source: `app/intelligence/eval_runner.py`, `app/evals/eval_runner.py`.

**EvalSuiteRunner** — Offline batch evaluator that runs multiple `EvalRunner` dimensions against a curated golden dataset built by `RegressionGate`. Used as a regression gate before deploying prompt variants, model changes, or configuration updates. Reports per-dimension regression (> 0.05 drop) and improvement. Source: `app/evals/eval_suite_runner.py`.

**ExecutionMemory** — Per-goal working memory accumulating the sequence of steps, tool call arguments, tool results, and intermediate outputs within a single goal execution. All data lives in `AgentState` fields. Cleared when the goal reaches a terminal `GoalStatus`. Source: `app/agent/state.py` (fields of `AgentState`).

---

## F

**FakeProvider** — The deterministic test and emergency-fallback LLM provider. Returns scripted responses based on input pattern matching. No API keys required. Used in unit tests (no real LLM calls), in CI, and as the final link in `FallbackChain` during LLM provider outages. Source: `app/providers/fake_provider.py`.

**FallbackChain** — Ordered list of LLM providers tried in sequence when the primary provider fails or has an open circuit. `FallbackChain.complete(request)` iterates providers, skipping those with OPEN circuits. Raises `AllProvidersUnavailableError` only when all providers are blocked. Source: `app/providers/fallback.py`.

**FLAREPattern** — Forward-Looking Active Retrieval Augmented Generation. The LLM generates output sentence by sentence; for each sentence it predicts the probability that retrieval would improve it. If the probability exceeds a threshold, retrieval is triggered before generating the sentence. Reduces unnecessary retrieval calls compared to retrieve-first strategies. Source: `app/agent/patterns/`.

---

## G

**GoalRuntimeProfile** — The complete orchestration plan built for a single goal before execution begins. Key fields: `profile_id` (UUID), `complexity` (`simple`/`moderate`/`complex`), `risk_level` (`low`/`medium`/`high`/`critical`), `model_plan` (planner/executor/verifier model assignments + `max_cost_usd`), `rag_strategy` (RAGStrategyConfig), `patterns` (PatternConfig), `eval_config` (score_threshold), `security` (compliance_tags, guardrail_bundle), `tenant_plan` (PlanTier). Built by `RuntimeProfileBuilder`. Source: `app/orchestration/runtime_profile.py`.

**GoalScorer** — Scores goal completion quality: `COMPLETE` = 1.0 (minus efficiency penalty of 0.01 per iteration above 5, capped at 0.3), `FAILED` = 0.0, `WAITING_HUMAN` = 0.5. Source: `app/evals/goal_score.py`.

**GoalStatus** — Enum of goal lifecycle states: `PENDING`, `PLANNING`, `EXECUTING`, `VERIFYING`, `COMPLETE`, `FAILED`, `CANCELLED`, `WAITING_HUMAN`. `_route()` reads this to determine graph transitions. `GoalScorer` maps each status to a score. Source: `app/agent/state.py`.

**GoalTree** — A hierarchical decomposition of a complex goal into a tree of independently-executable sub-goals. Each leaf node is a standalone `AgentGraph` run; the parent node is complete when all children complete. Used for goals like "migrate all 47 services to the new API" that are too large for a single agent context window. Source: `app/agent/goal_tree.py`.

**GuardrailChecker** — First-pass injection and dangerous command detector. Implements 6 detection techniques: direct phrase matching, base64 decoding, ROT13, Unicode homoglyph normalisation, leet-speak normalisation, and indirect injection (double-newline heuristic). Also detects dangerous patterns (`rm -rf`, `DROP TABLE`, `eval(`, etc.) and PII in tool outputs. Source: `app/intelligence/guardrails.py`.

**GuardrailConfig** — The scanner configuration dataclass selected by `GuardrailProfileSelector`. Fields: `name` (GuardrailBundle), `scan_prompt_injection`, `scan_output_pii`, `scan_toxicity`, `exfiltration_guard_enabled`, `pii_redaction_enabled`, `output_schema_validation`, `block_on_injection`, `block_on_pii`, `max_output_tokens`, `enabled_scanners`. Source: `app/security_runtime/guardrail_profile.py`.

**GuardrailEnforcer** — Profile-driven guardrail layer. `check_tool_args(tool_name, tool_args, profile)` and `check_final_output(output, profile)` defer to `GuardrailsV2` when available; fall back to inline regex patterns. Returns `EnforcementResult(checked, blocked, injection_detected, pii_detected, reason)`. The `reason` field is written to the audit trail. Source: `app/security_runtime/guardrail_enforcer.py`.

---

## H

**HITLGateway** — Human-In-The-Loop approval gateway. `request_approval()` creates an `ApprovalRequest` persisted to DB. `wait_for_approval()` uses a dual-listen strategy (in-process `asyncio.Event` + Redis BLPOP) for cross-replica delivery. Supports multi-person approval (`required_approvers > N`). CAS guard prevents `TIMED_OUT` from overwriting a concurrent `APPROVED`. Source: `app/governance/hitl.py`.

**HyDE** — Hypothetical Document Embeddings. Instead of embedding the raw query, the LLM generates a hypothetical answer to the query, which is then embedded and used as the retrieval vector. Improves recall for queries phrased differently from document language (e.g., a question vs. a statement-style knowledge-base entry). Source: `app/rag/strategies/`.

---

## I

**IdempotencyStore** — Redis-backed idempotency guard for `POST /goals`. Uses `SET NX EX` (atomic set-if-not-exists with expiry). An `Idempotency-Key` header causes duplicate API requests to return the cached original response without re-executing. `release(key, tenant_id)` allows retry after server errors. Source: `app/reliability/idempotency.py`.

**IngestionOrchestrator** — Coordinates the full document ingestion pipeline: HTTP/S3 fetch → `ContentClassifier` → `ChunkingStrategySelector` → `EmbeddingOrchestrator` → `KnowledgeStore.upsert()`. Handles batching, duplicate detection (content hash), and progress tracking. Emits `chunking_strategy_selected` and `embedding_strategy_selected` SSE events. Source: `app/rag/ingestion/orchestrator.py`.

---

## K

**KnowledgeGraphStore** — Property graph store for entities and relationships extracted from ingested documents. Enables structured queries ("what services depend on auth-service?") that vector search cannot answer directly. Used by `KnowledgeGraphPattern` which routes graph-type queries to this store rather than the vector store. Source: `app/knowledge/graph_store.py`.

**KnowledgeStore** — Primary document retrieval store. Wraps hybrid search: pgvector cosine similarity (dense) + PostgreSQL trigram/GIN index for keyword matching (sparse). All queries include `WHERE collection.tenant_id = :tid` plus PostgreSQL RLS enforcement. `search(query, tenant_id, top_k)` → list[Chunk]. Source: `app/knowledge/store.py`.

---

## L

**LangGraph** — Open-source Python library for building stateful multi-step agent workflows as typed `StateGraph`s. Provides: `StateGraph` (node + conditional edge definition), `checkpointers` (persist state across interruptions), `RunnableConfig` (run metadata), and `interrupt()` (pause for HITL). Used for the core agent loop in `app/agent/graph.py`. External dependency: `langgraph`.

**LTM (Long-Term Memory)** — `LongTermMemoryStore` persists cross-session learnings extracted from completed goals. Unlike `EpisodicMemoryStore` (raw execution history), LTM stores generalised patterns: "for tenant X, Jira queries are 40% faster with JQL syntax vs. REST API calls". Recalled at plan time. All rows: `WHERE tenant_id = :tid AND agent_id = :aid`. Source: `app/memory/ltm.py`.

---

## M

**MCPClient** — HTTP client for the Model Context Protocol. `tools_list()` enumerates available tools with schemas. `call_tool(tool_name, arguments)` executes a tool and parses the result. Wraps every call in a `CircuitBreaker`. Handles OAuth token refresh via `VaultStore`. Logs structured events for every call attempt and result. Source: `app/mcp/client.py`.

**MCPRegistry** — Per-tenant registry of MCP server connections (`tenant_id → {connector_name: MCPClient}`). Populated from the `mcp_connectors` DB table at startup and updated on connector registration/deletion. `registry.get_connectors(tenant_id)` returns only that tenant's connectors. Source: `app/mcp/registry.py`.

**ModelOrchestratorAdapter** — Adapter between the provider abstraction (`CompletionRequest`) and the LangGraph tool-calling interface. Translates tool schemas from MCP format to OpenAI function-calling format. Handles streaming responses and tool call parsing. Source: `app/providers/orchestrator_adapter.py`.

**ModelRouter** — Selects LLM provider and model for each role (planner, executor, verifier) based on goal complexity, cost class, and provider availability. Default routing: complex goals → expensive models; simple goals → cheap models; verification always uses a different provider than execution. Emits `model_route_selected` SSE event. Source: `app/agent/model_router.py`.

---

## N

**NLScheduler** — Converts natural language schedule descriptions to structured `TriggerSpec` objects. Example: `"every weekday at 9am London time"` → `TriggerSpec(cron="0 9 * * 1-5", timezone="Europe/London")`. Uses `croniter` for cron expression validation and `zoneinfo` for timezone handling. Source: `app/triggers/nl_scheduler.py`.

---

## P

**ParentChildChunker** — Hierarchical chunker that creates large parent chunks (semantic units) and small child chunks (sentences or paragraphs). Retrieval indexes child chunks for precision; on retrieval hit, the full parent chunk is returned as context for completeness. Optimal for long technical documents where individual sentences lack standalone context. Source: `app/rag/chunking/`.

**PatternAssembler** — Assembles the set of active patterns from `PatternConfig` into the actual LangGraph node wiring for a goal execution. Emits `pattern_assembled` SSE event (requires `DYNAMIC_ORCHESTRATION=true`) with `complexity`, `risk`, `patterns_active`, `models`, and `selection_reasons`. Source: `app/orchestration/pattern_assembler.py`.

**PatternConfig** — The output of `DynamicGraphAssembler`. Dataclass listing enabled patterns: `use_self_rag` (bool), `use_colbert` (bool), `use_consensus_verify` (bool), `use_debate` (bool), `use_peer_review` (bool), `use_self_consistency` (bool), `use_tree_of_thoughts` (bool), `use_raptor` (bool), `use_hyde` (bool), `use_speculative` (bool). Source: `app/orchestration/runtime_profile.py`.

**PatternSelector** — Selects which agentic reasoning patterns to activate. Rules: `complexity=complex` → multi-step patterns; `risk=critical` → ConsensusVerifier; `query_type=factual` → SelfRAG; `query_type=multi_hop` → ColBERT; `tenant_plan=enterprise` → all patterns available. Source: `app/orchestration/pattern_selector.py`.

**PeerReviewPattern** — Two executor LLM instances independently complete a step; each then reviews the other's output and provides a critique. The final output is the version that received the higher peer review score. Used for high-stakes writing, analysis, and decision tasks where independent review reduces systematic bias. Source: `app/agent/patterns/`.

**PlanTier** — The four execution tiers: `free`, `starter`, `professional`, `enterprise`. Determines `max_cost_usd` per goal, goals per day, max concurrent operations, and which Celery queue the goal routes to. Also controls which `PatternConfig` features are available (enterprise tenants can enable all patterns). Source: `app/tenancy/limits.py`, `app/scaling/celery_app.py`.

**PolicyEngine** — Evaluates tool calls against `Policy` objects using `fnmatch` glob matching. Returns `ALLOW`, `DENY`, or `REQUIRE_APPROVAL`. Supports time-window policies (business hours), regulated-domain fail-closed semantics, and cross-replica propagation via Redis pub/sub. `PolicyVersionManager` tracks immutable mutation history. Source: `app/governance/policies.py`.

**ProceduralMemoryStore** — Stores learned procedures: generalised step sequences that have consistently worked across multiple goal executions. Example: "when accessing Jira for tenant X, always authenticate with OAuth2 before using the REST API". Recalled at plan time via semantic query on the procedure description. Source: `app/memory/procedural.py`.

**PromptOptimizer** — Manages A/B prompt variant experiments per tenant. Uses epsilon-greedy selection (70% control, 30% random challenger). Auto-promotes challengers after `min_runs_for_promotion=100` runs using a one-sided Mann-Whitney U test (p < 0.05). Persists variants and outcomes to the `prompt_variants` table. Cross-replica invalidation via Redis. Source: `app/intelligence/prompt_optimizer.py`.

**PromptVariant** — Dataclass representing one prompt variant in a `PromptOptimizer` A/B experiment. Fields: `variant_id` (UUID), `name`, `prompt_text`, `prompt_key` (e.g., "planner_prompt"), `is_control`, `is_active`, `run_count`, `eval_scores` (list[float]), `promoted_at` (datetime | None). Source: `app/intelligence/prompt_optimizer.py`.

---

## R

**RaisonableGate** — A sanity-check filter applied at goal submission that rejects obviously malformed, circular, or self-referential goals before they consume any compute. Checks for: empty goal text, goal text that is pure punctuation, goals longer than `max_goal_length` characters, goals that appear to be injections (forwarded to `GuardrailChecker`). Source: `app/agent/raison_gate.py`.

**RAPTORPattern** — Recursive Abstractive Processing for Tree-Organized Retrieval. Documents are hierarchically summarised: leaf nodes are raw chunks; intermediate nodes are LLM-generated summaries of groups of chunks; the root is a summary of summaries. Retrieval can traverse from coarse (root) to fine (leaf), providing multi-granularity context for complex questions that require both high-level understanding and specific details. Source: `app/agent/patterns/`, `app/rag/strategies/`.

**ReadinessGate** — Pre-execution gate that checks whether all required dependencies are available before starting a goal: MCP connectors reachable, LLM provider circuit not OPEN, cost budget not exhausted, required knowledge collections populated. Emits `orchestration_readiness_gate_blocked_total` Prometheus counter with the blocking dependency. Source: `app/orchestration/readiness_gate.py`.

**ReflexionStore** — In-memory (and DB-backed) store for lessons learned from goal failures. `_lessons[tenant_id]` is a `deque` of lesson strings (max size configurable). `maybe_store_async(state)` persists the lesson when `SelfImprovementEngine` returns `STORE_REFLEXION_LESSON`. `recall(tenant_id, agent_id, limit=5)` returns the most recent lessons for injection into the planner's context. Source: `app/memory/reflexion.py`.

**RetrievalResult** — The structured output of retrieval operations. Fields: `chunks` (list[Chunk]), `source` (str: `knowledge_base`/`web`/`parametric`/`memory`/`none_available`), `confidence` (float 0–1), `corrected` (bool — True when CRAG fired), `correction_reason` (str: `low_confidence` | `gap_detected`). Scored by `RAGScorer` with source-aware scoring. Source: `app/rag/agentic/retriever_tool.py`.

**RetrieverTool** — MCP-compatible tool wrapper around the full retrieval pipeline. Exposed as `retrieve(query, collection_id)` in the executor LLM's tool schema. Internally calls `ContextPipeline`, applies CRAG if needed, returns a `RetrievalResult`. Source: `app/rag/agentic/retriever_tool.py`.

**RLS (Row-Level Security)** — PostgreSQL feature enforcing row-level access control via declarative policies. AgentVerse sets `app.tenant_id` GUC via `SELECT set_config('app.tenant_id', :tid, true)` (`SET LOCAL` — transaction-scoped). RLS policies: `USING (tenant_id = current_setting('app.tenant_id', true))`. Admin bypass: `system_session()` issues `SET LOCAL row_security = off`. Source: `app/db/rls.py`.

**RollbackEngine** — LIFO stack of compensating actions. `register(action, inverse)` or `register_typed(action_type, description, inverse_fn)` adds entries. `rollback_all_async(executed_tool_calls=...)` uses `tool_inverses` registry; `rollback_all_async()` (no args) uses the internal stack. Tool-call mode fully awaits all async inverses. Errors are logged but never abort the sequence. Source: `app/reliability/rollback.py`.

**RRF (Reciprocal Rank Fusion)** — Algorithm for merging ranked lists from multiple retrieval sources. Score: `RRF(d) = Σ 1/(k + rank(d))` where `k=60` smooths the contribution of highly-ranked documents. A document ranked 1st by both dense and sparse retrievers scores `1/61 + 1/61 = 0.0328`. A document ranked 1st by dense only scores `1/61 = 0.0164`. Consistently outperforms score averaging for hybrid retrieval. Source: `app/rag/fusion/`.

**RuntimeProfileBuilder** — Builds a `GoalRuntimeProfile` for every submitted goal. Reads: goal text complexity (measured by LLM token count and entity density), risk assessment (keyword scan + LLM classification), tenant plan, feature flags, active experiments. Emits `runtime_profile_selected` SSE event and records `orchestration_profile_built_total` Prometheus counter. Source: `app/orchestration/runtime_profile_builder.py`.

**RuntimeScorecard** — Computes a 9-dimension weighted quality scorecard for every completed goal. Overall score formula: `goal_success×0.30 + rag_quality×0.15 + safety×0.15 + grounding×0.10 + tool_success_rate×0.10 + citation_quality×0.05 + retrieval_confidence×0.05 + latency×0.05 + cost_efficiency×0.05`. Emits `eval_score_recorded` SSE event. Source: `app/evals/runtime_scorecard.py`.

**RuntimeSSEEmitter** — Produces 9 structured SSE event types for real-time orchestration observability. Two fire unconditionally (rag_strategy_selected, model_route_selected); four require DYNAMIC_ORCHESTRATION=true; three fire at specific call sites. Emit path: `event_callback → GoalService._dispatch_event() → Redis PUBLISH → SSE stream`. Source: `app/observability/runtime_decision_trace.py`.

---

## S

**SafetyScorer** — Computes the `safety` dimension of `RuntimeScorecard`. Penalty formula: `penalty = guardrail_violations×0.20 + hitl_bypasses×0.30 + audit_gaps×0.10`. Score: `max(0.0, 1.0 - penalty)`. HITL bypasses are weighted highest (0.30) because they represent governance policy circumvention. Source: `app/evals/safety_score.py`.

**SemanticCache** — Redis-backed cache that deduplicates LLM calls by embedding similarity. Before each LLM call, the query is embedded and compared against cached query embeddings. A cosine similarity above the threshold returns the cached response. Redis keys: `scv2:entry:{tenant_id}:{sha256(content)}`. Cross-tenant isolation is enforced by the key namespace. Source: `app/rag/semantic_cache.py`.

**SelfConsistencyPattern** — Runs the executor LLM multiple times (typically 5×) with non-zero temperature; selects the most-consistent answer by majority voting over the reasoning chains. Reduces variance for deterministic reasoning tasks (arithmetic, logic, code generation) at the cost of 5× executor calls. Source: `app/agent/patterns/`.

**SelfImprovementEngine** — Translates a `ScorecardResult` into a list of `ImprovementDecision` objects. Only fires when `overall_score < profile.eval_config.score_threshold`. Six action types: `UPDATE_PROMPT_VARIANT`, `UPDATE_MODEL_ROUTING`, `UPDATE_RAG_STRATEGY`, `STORE_REFLEXION_LESSON`, `BLACKLIST_TOOL_PATTERN`, `CREATE_REGRESSION_CASE`. Source: `app/evals/self_improvement_engine.py`.

**SelfOptimizerV2** — Bayesian (Thompson sampling) experiment manager. `on_goal_completed()` updates Beta distribution parameters for the active experiment arm. `apply_suggestion()` writes real agent config UPDATEs to DB. `maybe_conclude_experiment()` + `_maybe_start_experiment()` manage the full experiment lifecycle. Source: `app/intelligence/self_optimizer_v2.py`.

**SelfRAGPattern** — Self-Reflective RAG. The LLM decides per-sentence whether retrieval is needed, retrieves when uncertain, and critiques its own retrieved results (relevance check and support check). Three stages: retrieve decision, retrieve, reflect. Reduces unnecessary retrieval calls while maintaining factual grounding for claims that need it. Source: `app/agent/patterns/`.

**SentenceWindowChunker** — Creates sentence-level chunks but returns a surrounding window of sentences as context on retrieval. Indexing on sentences provides high precision; returning the surrounding window provides sufficient context for the LLM to understand the sentence's meaning. Window size is configurable (default: 3 sentences before and after). Source: `app/rag/chunking/`.

**SpeculativeRAGPattern** — Generates a speculative (draft) answer without retrieval, evaluates confidence, retrieves only when confidence is below a threshold, then refines using retrieved content. Fastest for high-confidence domains (the LLM knows the answer well); falls back to full retrieval for uncertain topics. Source: `app/agent/patterns/`.

**StrategyRegistry** — Central registry mapping strategy names to implementation classes. `RAGStrategySelector` queries the registry for available strategies per tenant plan. Strategies: `standard`, `hybrid`, `crag`, `hyde`, `flare`, `self_rag`, `raptor`, `speculative`, `colbert`. Source: `app/rag/strategy_registry.py`.

---

## T

**TenantContext** — The per-request identity and authorisation object. Fields: `tenant_id` (UUID str), `plan` (PlanTier), `roles` (list[str]), `scopes` (list[str]), `api_key_id` (str). Constructed by `TenantMiddleware` from the API key. Bound to `structlog.contextvars` so all log lines for the request carry `tenant_id` automatically. Passed to every service method. Source: `app/tenancy/context.py`.

**TenantMiddleware** — FastAPI middleware performing: API key authentication, `TenantContext` construction, endpoint scope validation, structlog context binding, and `SecurityHeadersMiddleware` delegation. Raises `HTTP 401` on missing/invalid key and `HTTP 403` on insufficient scope. Source: `app/tenancy/middleware.py`.

**TreeOfThoughtsPattern** — The LLM explores multiple reasoning branches simultaneously (typically 3–5 branches), evaluates each branch's promise via a scoring heuristic or self-evaluation, and selects the most promising branch for continuation. Effective for planning tasks with multiple valid approaches and situations where a greedy step-by-step approach would miss the global optimum. Source: `app/agent/patterns/`.

---

## V

**VaultStore** — Encrypted credential store for MCP connector OAuth tokens and API keys. Credentials are stored per-tenant with a tenant-specific key derivation function. Retrieved by `MCPClient` when establishing a connector connection. OAuth PKCE flow handled by `app/mcp/oauth.py`. Source: `app/mcp/oauth.py`.

**VerifierCalibrationStore** — Tracks verifier LLM accuracy over time by comparing verifier verdicts (`success=True/False`) against ground-truth outcomes (did the goal actually succeed?). Used to detect verifier model drift (a verifier that is systematically wrong in one direction) and calibrate the `ConsensusVerifier` threshold. Source: `app/evals/verifier_calibration.py`.

---

## W

**WorkingMemory** — The in-context state accumulated during a single goal execution: retrieved chunks, tool call results, intermediate outputs, generated plans, and the growing `executed_tool_calls` list. Implemented as fields on `AgentState`. Distinct from `EpisodicMemoryStore` (persisted, cross-session execution history) and `LTM` (generalised cross-session learnings). Source: `app/agent/state.py`.

---

## Appendix A: Key Acronyms

| Acronym | Expansion | Primary context |
|---------|-----------|----------------|
| BM25 | Best Match 25 (sparse retrieval) | RAG |
| CRAG | Corrective RAG | `08-hallucination-risk-and-reliability.md §5` |
| FLARE | Forward-Looking Active REtrieval | Agent patterns |
| GUC | Grand Unified Configuration (PostgreSQL session variable) | `app/db/rls.py` |
| HITL | Human-In-The-Loop | `07-guardrails-governance-scopes-safety.md §B.1` |
| HyDE | Hypothetical Document Embeddings | RAG strategies |
| LTM | Long-Term Memory | `app/memory/ltm.py` |
| MCP | Model Context Protocol | `app/mcp/` |
| NLScheduler | Natural Language Scheduler | `app/triggers/` |
| OTEL | OpenTelemetry | `06-improvement-evals-observability.md §C.3` |
| PII | Personally Identifiable Information | Guardrails |
| RAPTOR | Recursive Abstractive Processing for Tree-Organized Retrieval | Agent patterns |
| RLS | Row-Level Security (PostgreSQL) | `app/db/rls.py` |
| RRF | Reciprocal Rank Fusion | RAG fusion |
| SSE | Server-Sent Events | Goal streaming transport |

---

## Appendix B: Source File Quick Reference

| Class / concept | File |
|-----------------|------|
| `ABTestingEngine` | `app/optimization/ab_testing.py` |
| `AgentGraph`, `_node_*`, `_route()` | `app/agent/graph.py` |
| `AgentState`, `GoalStatus` | `app/agent/state.py` |
| `AgentScorer`, `GoalScorer`, `RAGScorer`, `SafetyScorer`, `ModelScorer` | `app/evals/agent_score.py`, `goal_score.py`, `rag_score.py`, `safety_score.py`, `model_score.py` |
| `BulkheadRegistry`, `RedisBulkhead` | `app/reliability/bulkhead.py` |
| `CircuitBreaker`, `RedisCircuitBreaker` | `app/reliability/circuit_breaker.py`, `redis_circuit_breaker.py` |
| `CitationManager` | `app/rag/citation_manager.py` |
| `ContextPipeline` | `app/rag/context_pipeline.py` |
| `CostController` | `app/governance/cost.py` |
| `DeduplicationCache`, `RedisDeduplicationCache` | `app/reliability/dedup.py` |
| `EvalRunner` | `app/intelligence/eval_runner.py`, `app/evals/eval_runner.py` |
| `GoalRuntimeProfile`, `PatternConfig` | `app/orchestration/runtime_profile.py` |
| `GuardrailChecker` | `app/intelligence/guardrails.py` |
| `GuardrailConfig`, `GuardrailProfileSelector` | `app/security_runtime/guardrail_profile.py` |
| `GuardrailEnforcer` | `app/security_runtime/guardrail_enforcer.py` |
| `HITLGateway`, `ApprovalRequest` | `app/governance/hitl.py` |
| `IdempotencyStore` | `app/reliability/idempotency.py` |
| `KnowledgeStore` | `app/knowledge/store.py` |
| `MCPClient` | `app/mcp/client.py` |
| `MCPRegistry` | `app/mcp/registry.py` |
| `ModelRouter` | `app/agent/model_router.py` |
| Prometheus metrics | `app/observability/metrics.py` |
| `PolicyEngine`, `PolicyVersionManager` | `app/governance/policies.py` |
| `PromptOptimizer`, `PromptVariant` | `app/intelligence/prompt_optimizer.py` |
| `ReflexionStore` | `app/memory/reflexion.py` |
| `RegressionGate` | `app/evals/regression_gate.py` |
| `RLS helpers` | `app/db/rls.py` |
| `RollbackEngine`, tool inverses | `app/reliability/rollback.py`, `tool_inverses.py` |
| `RuntimeProfileBuilder` | `app/orchestration/runtime_profile_builder.py` |
| `RuntimeScorecard`, `ScorecardResult` | `app/evals/runtime_scorecard.py` |
| `RuntimeSSEEmitter`, `SSEEventType` | `app/observability/runtime_decision_trace.py` |
| `SemanticCache` | `app/rag/semantic_cache.py` |
| `SelfImprovementEngine`, `ImprovementAction` | `app/evals/self_improvement_engine.py` |
| `SelfOptimizerV2` | `app/intelligence/self_optimizer_v2.py` |
| `TenantContext` | `app/tenancy/context.py` |
| `TenantMiddleware` | `app/tenancy/middleware.py` |
| `VaultStore` | `app/mcp/oauth.py` |
| Cost breakdown tracking | `app/observability/cost_breakdown.py` |
| OTEL tracing | `app/observability/tracing.py` |
| Structured logging | `app/observability/logging.py` |
| Audit trail | `app/governance/audit.py` |

---

## Appendix C: Self-Improvement Action Reference

Complete mapping from scorecard dimensions to `SelfImprovementEngine` actions, including the exact thresholds and what changes downstream:

| Dimension | Threshold | Action triggered | Downstream effect |
|-----------|-----------|-----------------|------------------|
| `rag_quality` | < 0.50 | `UPDATE_RAG_STRATEGY` | `RAGStrategySelector` prefers higher-confidence sources next run |
| `retrieval_confidence` | < 0.40 | `UPDATE_RAG_STRATEGY` | Combined with rag_quality check |
| `goal_success` | < 0.70 | `UPDATE_PROMPT_VARIANT` | `prompt_optimizer.record_result()` updates arm; may promote challenger |
| `tool_success_rate` | < 0.50 | `UPDATE_PROMPT_VARIANT` | Combined with goal_success check |
| `goal_success` | < 0.70 | `STORE_REFLEXION_LESSON` | (Only when `verification_feedback` is non-empty) Lesson stored for future plan context |
| `tool_success_rate` | < 0.30 | `BLACKLIST_TOOL_PATTERN` | `ToolReliabilityStore.record()` marks tool unreliable; excluded from tool routing |
| `cost_efficiency` | < 0.30 | `UPDATE_MODEL_ROUTING` | `agent_store.update_config()` switches to cheaper model for this agent |
| `latency` | < 0.30 | `UPDATE_MODEL_ROUTING` | `agent_store.update_config()` switches to faster model |
| `overall_score` | < 0.40 | `CREATE_REGRESSION_CASE` | Goal persisted as regression test case in `eval_scorecards` table |

All actions only trigger when `overall_score < profile.eval_config.score_threshold`. A score below threshold is required before any individual action is evaluated.

---

## Appendix D: SSE Event Field Reference

Complete field reference for all 9 `RuntimeSSEEmitter` event types:

**`runtime_profile_selected`**
```json
{
  "type": "runtime_profile_selected",
  "goal_id": "string",
  "profile_id": "UUID string",
  "complexity": "simple|moderate|complex",
  "patterns": ["SelfRAGPattern", "ConsensusVerifier"],
  "rag_strategy": "hybrid",
  "assembly_latency_ms": 3.2
}
```

**`pattern_assembled`** (requires `DYNAMIC_ORCHESTRATION=true`)
```json
{
  "type": "pattern_assembled",
  "goal_id": "string",
  "complexity": "complex",
  "risk": "high",
  "patterns_active": {"retrieval": ["SelfRAG"], "verification": ["ConsensusVerifier"]},
  "models": {"planner": "gpt-4o", "executor": "gpt-4o-mini", "verifier": "claude-haiku"},
  "selection_reasons": {"SelfRAG": "factual query", "ConsensusVerifier": "risk=high"},
  "assembly_latency_ms": 12.4
}
```

**`rag_strategy_selected`** (always fires)
```json
{
  "type": "rag_strategy_selected",
  "goal_id": "string",
  "strategy": "hybrid",
  "sources": ["knowledge_base"],
  "reranker": "cross-encoder"
}
```

**`model_route_selected`** (always fires)
```json
{
  "type": "model_route_selected",
  "goal_id": "string",
  "planner": "gpt-4o",
  "executor": "gpt-4o-mini",
  "verifier": "claude-3-haiku-20240307",
  "cost_class": "medium"
}
```

**`guardrail_profile_selected`** (requires `DYNAMIC_ORCHESTRATION=true`)
```json
{
  "type": "guardrail_profile_selected",
  "goal_id": "string",
  "bundle": "STRICT",
  "scanners": ["injection", "toxicity", "pii"]
}
```

**`eval_score_recorded`** (requires `DYNAMIC_ORCHESTRATION=true`)
```json
{
  "type": "eval_score_recorded",
  "goal_id": "string",
  "overall_score": 0.847,
  "scores": {
    "goal_success": 1.0, "rag_quality": 0.75, "safety": 1.0,
    "latency": 0.8, "cost_efficiency": 1.0, "grounding": 0.8,
    "citation_quality": 0.7, "retrieval_confidence": 0.6, "tool_success_rate": 0.857
  }
}
```

**`self_improvement_suggested`** (fires after improvement dispatch)
```json
{
  "type": "self_improvement_suggested",
  "goal_id": "string",
  "suggestions": [
    "Consider switching RAG strategy — low retrieval confidence",
    "High iteration count — consider goal decomposition"
  ]
}
```

**`chunking_strategy_selected`** (fires at ingestion)
```json
{
  "type": "chunking_strategy_selected",
  "goal_id": "string",
  "content_type": "code",
  "strategy": "ASTChunker",
  "reason": "Python source file detected — splitting at function boundaries"
}
```

**`embedding_strategy_selected`** (fires at embedding)
```json
{
  "type": "embedding_strategy_selected",
  "goal_id": "string",
  "model_id": "voyage-code-2",
  "modality": "code",
  "dimension": 1024,
  "cost_class": "medium",
  "reason": "code modality → voyage-code-2"
}
```

---

## Appendix E: Prometheus Label Bounded Sets

All metric labels use bounded vocabularies to prevent high-cardinality explosion. Raw strings are always normalised before recording.

**Status labels** (used in `agentverse_goal_total`, `agentverse_tool_call_total`):
`started`, `complete`, `failed`, `cancelled`, `denied`, `approval_required`, `cache_hit`, `circuit_open`, `success`, `error`, `unknown`

**Priority labels** (used in `agentverse_goal_total`, `agentverse_queue_wait_seconds`):
`low`, `normal`, `high`, `urgent`

**Tool category labels** (used in `agentverse_tool_call_total`):
`jira`, `rpa`, `confluence`, `email`, `rag`, `unknown`

**Provider labels** (used in `agentverse_llm_tokens_total`):
`openai`, `azure_openai`, `anthropic`, `google`, `local`, `unknown`

**Model labels** (used in `agentverse_llm_tokens_total`):
`gpt-4o`, `gpt-4o-mini`, `gpt-5`, `gpt-5-mini`, `claude-sonnet`, `claude-haiku`, `gemini`, `embedding`, `local`, `unknown`

**Token type labels** (used in `agentverse_llm_tokens_total`):
`prompt`, `completion`, `cached`, `reasoning`, `total`, `unknown`

**Cost scope labels** (used in `agentverse_cost_usd_total`):
`goal`, `tool`, `llm`, `workflow`, `queue`, `unknown`

**Queue labels** (used in `agentverse_queue_depth`):
`goals`, `schedules`, `maintenance`, `default`, `unknown`
