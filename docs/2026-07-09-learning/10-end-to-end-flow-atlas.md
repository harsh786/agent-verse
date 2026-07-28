# AgentVerse — End-to-End Execution Flow Atlas

> **Purpose:** Every major execution path in the platform, shown as precise text diagrams with component names, data transformations, error branches, and timing. Use this as a debugging map: when something breaks, find the flow it belongs to and trace each box.

---

## 1. Platform Startup Flow

The application uses a two-phase wiring model. Phase 1 (`create_app`) builds all services in-memory so the FastAPI app is immediately available for requests (and so tests can skip Phase 2 entirely). Phase 2 (`lifespan`) upgrades every service with real DB/Redis connections when `manage_pools=True`.

```
[uvicorn launch]
      │  calls create_app() from app.main
      ▼
[create_app(settings, manage_pools)]
      │
      ├─ configure_logging(level, json_logs)
      ├─ configure_tracing()             ← OTel GRPC exporter (OTEL_EXPORTER_OTLP_ENDPOINT)
      │
      ├─ PHASE 1 — In-Memory Service Construction ──────────────────────────────
      │
      ├─ providers.registry.resolve_provider()
      │     ├── LLM_PROVIDERS JSON array? → iterate and wire providers
      │     ├── ANTHROPIC_API_KEY set?   → AnthropicProvider
      │     ├── OPENAI_API_KEY set?      → OpenAICompatibleProvider
      │     ├── GOOGLE_API_KEY set?      → GeminiProvider
      │     ├── GROQ_API_KEY set?        → GroqProvider
      │     ├── OLLAMA_BASE_URL set?     → OllamaProvider
      │     └── (none)                  → FakeProvider
      │           └── ENVIRONMENT=production? → RuntimeError (refuses to start)
      │
      ├─ _build_verifier_provider()
      │     ├── VERIFIER_API_KEY set? → dedicated verifier (breaks self-confirmation)
      │     ├── OPENAI primary + ANTHROPIC_API_KEY set? → cross-model verifier
      │     └── (none) → same as primary
      │
      ├─ _embedder resolution
      │     ├── VOYAGE_API_KEY     → VoyageProvider
      │     ├── OPENAI_API_KEY     → OpenAICompatibleProvider (text-embedding-3-small)
      │     ├── GOOGLE_API_KEY     → GeminiProvider
      │     ├── SENTENCE_TRANSFORMERS_MODEL → LocalEmbedProvider
      │     └── (none)             → None  (KB search uses FTS+trigram only)
      │
      ├─ ModelRouter(provider_name)   ← picks planning/execution/verification models
      ├─ TenantService()              ← in-memory dict, no DB
      ├─ GoalService()                ← in-memory GoalRecord dict
      ├─ AgentStore()                 ← in-memory agent configs
      ├─ MCPRegistry(redis=_FakeRedis)← in-memory connector store
      ├─ MCPClient(registry, llm)     ← HTTP client
      ├─ OAuthFlowManager()
      ├─ HITLGateway()                ← asyncio.Event based
      ├─ AuditLog()                   ← append-only list
      ├─ CostController()             ← in-memory budget counters
      ├─ PolicyEngine()
      ├─ KnowledgeStore()             ← in-memory chunks dict
      ├─ SemanticCache()
      ├─ LongTermMemoryStore()
      ├─ EvalRunner()
      ├─ SelfOptimizerV2(redis=_FakeRedis, db_factory=None)
      ├─ ExecutionMemory()
      ├─ MetaAgentPlanner(provider)
      ├─ ScheduleStore(), NLScheduler(provider)
      ├─ ComplianceController(), SimulationRunner(), RedTeamRunner()
      ├─ Marketplace(agent_store), MarketplaceV2(db_factory=None)
      ├─ CostTracker(redis=_FakeRedis)
      ├─ GuardrailEngineV2()
      ├─ AgentIdentityService(db=None, vault, redis=_FakeRedis)
      └─ BrowserSessionManager(), artifact_store
      │
      ├─ app = FastAPI(title, lifespan)
      ├─ _register_error_handlers(app)
      │     ├── PlatformError → structured JSON + HTTP code
      │     └── Exception → InternalError (detail hidden from client)
      ├─ app.add_middleware(TenantMiddleware, key_resolver, rate_limiter)
      ├─ app.add_middleware(SecurityHeadersMiddleware)  ← HSTS, CSP, etc.
      ├─ app.add_middleware(ScopeEnforcementMiddleware)
      ├─ app.add_middleware(CORSMiddleware, origins=CORS_ORIGINS)
      ├─ [25+ routers included via app.include_router]
      └─ app returned — ready for requests, all services wired to app.state
      │
      │ (manage_pools=True path only)
      ▼
[lifespan(app) — FastAPI startup event]
      │
      ├─ ConnectionPools.start()
      │     ├── asyncpg pool        (DATABASE_URL)
      │     ├── aioredis pool       (REDIS_URL or Sentinel)
      │     └── httpx pool          (shared HTTP for MCP)
      │
      ├─ PHASE 2 — DB/Redis Service Upgrade ───────────────────────────────────
      │
      ├─ MCPRegistry(redis=real_redis)  → replaces fake on app.state
      ├─ MCPClient(registry=new, redis=real_redis)
      ├─ TenantService(db=pool) → sync_from_db()  ← hydrates tenant rows
      ├─ GoalService(db=pool, redis=real_redis)
      │     ├─ start_celery_event_bridge(redis_url)  ← pub/sub bridge task
      │     └─ start_hitl_rejection_subscriber(redis_url)
      ├─ AgentStore(db=pool) → sync_from_db()
      ├─ KnowledgeStore(db_session_factory=pool)
      ├─ LongTermMemoryStore(db=pool, embedder)
      ├─ ScheduleStore(db=pool)
      ├─ PolicyEngine(redis=real_redis)  ← pub/sub policy propagation
      ├─ CostController(redis=real_redis)
      ├─ AuditLog(db=pool)
      ├─ HITLGateway(redis=real_redis)  ← cross-replica BLPOP approvals
      ├─ SelfOptimizerV2(redis=real_redis, db_factory=pool)
      ├─ EvalRunner(db=pool)
      ├─ MarketplaceV2(db_factory=pool)
      ├─ AgentIdentityService(db=pool, redis=real_redis)
      ├─ LangGraph checkpointer:
      │     1. AsyncRedisSaver.from_conn_string(REDIS_URL) — preferred
      │     2. RedisSaver (sync)
      │     3. MemorySaver (warns: GOAL STATE WILL BE LOST ON PROCESS RESTART)
      └─ app.state.langgraph_checkpointer = checkpointer
      │
[app serving requests]
```

**Error branches at startup:**
- `ENVIRONMENT=production` + `FakeProvider` → hard `RuntimeError`, process exits.
- `DATABASE_URL` credentials are the default `agentverse:agentverse@` in production → hard error (checked in `Settings`).
- Pool start failure → lifespan raises, uvicorn refuses to serve traffic.

---

## 2. Goal Submission and Queuing Flow

```
[Client] POST /goals
         {goal, agent_id?, priority, dry_run, context}
      │
      ▼
[TenantMiddleware]
      ├─ Extract API key (Authorization: Bearer <key> OR X-API-Key header)
      ├─ key_resolver(key) → DB lookup → TenantContext
      │     └── 401 if missing/invalid
      ├─ _check_rate_limit_with_fallback(tenant_id, redis, rpm_limit)
      │     ├── Redis SlidingWindowRateLimiter (zadd/zremrangebyscore/zcard)
      │     └── In-process fallback if Redis unavailable (120 rpm cap)
      │     └── 429 if exceeded
      └─ request.state.tenant = TenantContext
      │
      ▼
[ScopeEnforcementMiddleware] checks JWT scopes if token-auth path
      │
      ▼
[POST /goals handler — app/api/goals.py]
      │
      ├─ DeduplicationCache.check(goal_text, tenant_id)
      │     └── 409 CONFLICT if identical goal submitted within dedup window
      │
      ├─ GoalService.get_active_count(tenant_id)
      │     └── 429 if concurrent goal limit reached (plan tier)
      │
      ├─ GoalService.get_daily_count(tenant_id)
      │     └── 429 if daily goal limit reached
      │
      ├─ agent_id == None?
      │     ├── YES: AgentRouter.route(goal, tenant_ctx) → select best agent
      │     │         ├── embedding similarity vs agent descriptions
      │     │         └── falls back to default agent_id
      │     └── NO:  use provided agent_id
      │
      ├─ DYNAMIC_ORCHESTRATION=true?
      │     └── RuntimeProfileBuilder.build_with_trace(goal, tenant_id)
      │           ├── ContentClassifier → complexity, risk, domain
      │           ├── StrategyRegistry.lookup(complexity, domain, risk)
      │           ├── PatternAssembler._RULES evaluation
      │           └── RuntimeProfile {agent_patterns, rag_strategy, security, model_plan}
      │
      ├─ GoalService.submit_goal(goal, agent_id, tenant_ctx, execution_context)
      │     ├── goal_id = uuid hex
      │     ├── GoalRecord created → stored in self._goals[goal_id]
      │     ├── DB INSERT (goals table) via fire-and-forget task
      │     ├── AuditLog.log(goal_submitted)
      │     └── CeleryGoalTaskQueue.enqueue(goal_id, tenant_ctx)?
      │           ├── YES (task_queue set): apply_async(run_goal, queue=PLAN_QUEUE_MAP[plan])
      │           │     ├── free         → "goals.free"
      │           │     ├── starter      → "goals.starter"
      │           │     ├── professional → "goals.professional"
      │           │     └── enterprise   → "goals.enterprise"
      │           └── NO: asyncio.create_task(_run_agent_loop_local(goal_id))
      │
      ├─ SSE bridge started for this goal_id
      └─ 202 Accepted {goal_id, status: "planning"}

[Goal now executing — see Flow 3]
```

---

## 3. AgentGraph Full Execution Flow

The graph is compiled once per `AgentGraph` instance in `_build()`. The topology depends on feature flags set at construction time.

```
[GoalService._make_agent_loop_for_tenant()]
      │  Builds AgentGraph with:
      │    planner, executor, verifier (separate LLM providers)
      │    governance: permission_matrix, audit_log, cost_controller, hitl_gateway, policy_engine
      │    reliability: circuit_breakers, rollback_engine, dedup_cache
      │    memory: exec_memory, long_term_memory, knowledge_store
      │    flags: enable_cot, enable_reflection, enable_self_refine,
      │           enable_self_consistency, enable_tree_of_thoughts, enable_peer_review
      │
      ▼
[AgentGraph.run(goal, tenant_ctx, initial_context)]
      │
      ▼
[START → _node_initialize]
      │  Creates AgentState(goal, tenant_ctx) or reuses existing
      │  Emits: goal_started SSE event
      │
      ├─ GuardrailChecker.check_goal(goal)
      │     └── injection phrases / dangerous commands detected?
      │           └── GoalStatus.FAILED + goal_rejected SSE → END (terminal)
      │
      ├─ SelfOptimizerV2.get_arm_config(agent_id, goal_id, tenant_id)
      │     └── Bayesian A/B: picks experiment arm → context["_experiment_arm"]
      │
      ├─ DYNAMIC_ORCHESTRATION=true (and profile not pre-built)?
      │     └── RuntimeProfileBuilder.build_with_trace(goal)
      │           └── profile → context["_runtime_profile"]
      │           └── RuntimeSSEEmitter.pattern_assembled() → SSE
      │
      ├─ IdentityResolver.resolve(tenant_ctx) → context["_identity_scope"]
      ├─ GovernanceProfileSelector.select(profile) → context["_governance_bundle"]
      └─ SourceInventory.build(tenant_ctx) → context["_source_inventory"]
      │
      ▼
[_node_rag_retrieval]
      │  1. ExecutionMemory.recall_async(goal) → past winning plans
      │  2. ExecutionMemory.recall_failures(goal) → failure patterns to avoid
      │  3. LongTermMemoryStore.recall_async(goal, embedder) → domain knowledge
      │  4. KnowledgeStore.hybrid_search_db(goal, embedding, collection_ids)
      │     └── pgvector ANN + FTS + trigram via rag/engine.hybrid_search()
      │  5. _active_rag_strategy != basic?
      │     └── engine.retrieve(session, strategy=advanced)
      │           ├── "flare"    → FLARE iterative retrieval
      │           ├── "raptor"   → recursive summarisation
      │           ├── "fusion"   → multi-query fusion
      │           ├── "colbert"  → token-level late interaction
      │           ├── "hyde"     → hypothetical document expansion
      │           ├── "multi_hop"→ iterative multi-hop
      │           └── "rerank"   → cross-encoder reranking
      │  6. web_search_tool fallback (KB empty)
      │  Emits: rag_strategy_selected SSE, knowledge_retrieved SSE
      └─ Returns: rag_context string, citations list
      │
      ▼ (branch based on feature flags set at construction time)
      │
      ├──[enable_cot=True]────→ [_node_think]
      │                              │  CHAIN_OF_THOUGHT_SYSTEM prompt
      │                              │  model_router.model_for("think") = planning_model
      │                              │  → cot_reasoning string
      │                              ▼
      │                        [enable_tree_of_thoughts?]
      │                              ├── YES → [_node_tree_of_thoughts]
      │                              │         TreeOfThoughtsPattern(n_thoughts=3, max_depth=2)
      │                              │         → context["tot_answer"]
      │                              └── NO  → direct to plan
      │
      └──[enable_tree_of_thoughts, !cot]──→ [_node_tree_of_thoughts] → plan
      │
      ▼
[_node_plan]
      │  1. ContextPipeline.run(chunks, query)   ← 7-step context processing
      │     │  Step 1: RerankPolicy.rerank() — deduplicate + MMR/cross-encoder/RRF
      │     │  Step 2: filter < min_relevance_score (0.35)
      │     │  Step 3: source diversity cap (max 5 per source)
      │     │  Step 4: ContextBudget.apply() — token budget (6000 tokens)
      │     │  Step 5: CitationThreader.thread() — attach [N] inline refs
      │     │  Step 6: CitationManager.attach_citations()
      │     │  Step 7: PromptBuilder → planner_context, executor_context, verifier_context
      │  2. EpisodicMemoryStore.recall() → recent experience
      │  3. ProceduralMemoryStore.recall() → proven patterns
      │  4. ReflexionStore.recall() → past failure lessons
      │  5. HITL rejection note? → append to context
      │  6. ModelOrchestratorAdapter.route_to_model(goal) → pick model
      │  7. OutputContractBuilder → structured output schema
      │  8. ToolPromptBuilder.build() → inject available tool names
      │  9. LLM call (planner, STRUCTURED_PLANNER_SYSTEM)
      │     └── JSON parse → [{id, description, tool, arguments, risk, depends_on}]
      │  Emits: plan_created SSE
      └─ agent_state.plan = step list, agent_state.status = EXECUTING
      │
      ▼
[_node_execute]
      │  For each step in StructuredPlan (wave execution — parallel deps):
      │
      │  Per step:
      │  ├─ GuardrailChecker.check_goal(step.description)  ← injection in step text
      │  │     └── fail step if detected
      │  ├─ GuardrailChecker.check(tool_name, {})          ← tool name check
      │  ├─ GuardrailEnforcer.check_tool_args(profile, tool_name, args) ← profile-based
      │  ├─ PolicyEngine.check(tool_name, tenant_ctx)
      │  │     ├── ALLOW   → proceed
      │  │     ├── DENY    → step.error = "policy denied"
      │  │     └── REQUIRE_APPROVAL → HITL gate (below)
      │  │
      │  ├─ autonomy_mode == "supervised" OR tool_risk in _HIGH_RISK_KEYWORDS?
      │  │     └── HITLGateway.request_approval(goal_id, action, risk)
      │  │           ├── asyncio.Event.wait(timeout=300s) ← in-process path
      │  │           ├── Redis BLPOP key hitl:{request_id} ← cross-replica path
      │  │           ├── ApprovalStatus.APPROVED → proceed
      │  │           ├── ApprovalStatus.REJECTED → step.error, skip
      │  │           └── TIMED_OUT (300s) → step.error = "hitl_timeout"
      │  │                 └── GoalStatus.FAILED (terminal — see Flow 11)
      │  │
      │  ├─ CostController.check_and_record(goal_id, estimated_cost, tenant_ctx)
      │  │     └── budget exceeded? → raise CostBudgetExceeded (see Flow 11)
      │  │
      │  ├─ MCPClient.call_tool(tool_name, arguments, tenant_ctx) ← see Flow 4
      │  │
      │  ├─ RollbackEngine.register(action, inverse_fn)  ← compensating action recorded
      │  ├─ IndirectInjectionScanner.scan(result.output) ← output injection check
      │  ├─ GroundingChecker.check(step.output, tool_results) ← hallucination check
      │  │     └── ungrounded? → step.status = UNGROUNDED
      │  └─ persist_tool_outcome(step, db)   ← async fire-and-forget
      │  Emits: step_started, step_completed (or step_failed) SSE
      │
      ├──[enable_self_refine=True]──→ [_node_refine]
      │                                    │  SELF_REFINE_SYSTEM prompt
      │                                    │  Improves last step output
      │                                    │  max_refine_iterations = 2
      │                                    └─ last_step.output = refined
      │
      ├──[enable_self_consistency=True]──→ [_node_self_consistency]
      │                                         │  SelfConsistencyPattern(n_samples=3)
      │                                         │  3 LLM samples → majority vote
      │                                         └─ last_step.output = consensus answer
      ▼
[_node_verify]
      │  1. Build verifier summary (_build_verifier_summary):
      │     - All FAILED/UNGROUNDED steps highlighted
      │     - Last 5 steps included verbatim
      │  2. LLM call (verifier, VERIFIER_SYSTEM)
      │     └── {"success": bool, "reason": str, "retry": bool}
      │  3. EvalRunner.score(goal, steps, context)
      │     └── 5 dimensions → float scores
      │  4. RuntimeScorecard.score(agent_state)
      │     └── 9 dimensions → RuntimeScorecardResult
      │  5. SelfImprovementEngine.record_result(goal, plan, score)
      │     └── if score < threshold: generate_suggestion()
      │  6. RegressionGate.check(score, baseline)
      │     └── regression detected? → log warning
      │  Emits: verification_complete, eval_score_recorded SSE
      │
      ├──[enable_peer_review=True]──→ [_node_peer_review]
      │                                    │  PeerReviewPattern(quality_threshold=0.7)
      │                                    │  Independent LLM scores output
      │                                    └─ context["peer_review_score"]
      ▼
[_route(state)] ← conditional edge router
      │
      ├─ terminal_reason == "guardrail_rejected" → END (FAILED)
      ├─ verification_success == True:
      │     └─ EpisodicMemoryStore.record(goal, result)
      │     └─ ProceduralMemoryStore.learn(plan, outcome)
      │     └─ LongTermMemoryStore.extract(goal, steps)
      │     └─ ExecutionMemory.record(goal, plan, success=True)
      │     └─ GoalStatus.COMPLETE → END
      │
      ├─ retry == False (permanent failure): GoalStatus.FAILED → END
      │
      ├─ iteration >= max_iterations (100): GoalStatus.FAILED (max_iter) → END
      │
      ├─ stagnation (3 identical verification_feedbacks):
      │     GoalStatus.FAILED (stagnation) → END
      │
      ├─ context gap detected (feedback contains "insufficient"/"unclear"/etc.):
      │     └─ → [rag_remediate] → [plan]  (targeted re-retrieval)
      │
      ├─ enable_reflection=True AND verification failed:
      │     └─ ReflexionWirer.maybe_store_async(goal, feedback)
      │     └─ RollbackEngine.rollback_all_async()
      │     └─ → [reflect] → [plan]
      │
      └─ default (retry=True, < max_iterations):
           └─ iteration += 1
           └─ → [plan]  (replan with feedback in context)
```

---

## 4. Tool Execution Flow

The complete path from executor LLM response to tool result, with all safety layers:

```
[_node_execute receives step.description]
      │
      │  Executor LLM call → raw response JSON:
      │    {"tool": "server_name.tool_name", "arguments": {"k": "v"}}
      │
      ▼
[extract_tool_call(raw_response)]
      │  JSON parse → ToolCall(tool_name, arguments)
      │  repair_tool_call_arguments() if malformed
      │
      ▼
[Safety gate 1 — Input sanitization]
      ├─ GuardrailChecker.check_goal(step.description)
      │     ├─ _normalize_text (NFKC + lowercase)
      │     ├─ _INJECTION_PHRASES scan (15 patterns)
      │     ├─ _detect_base64_injection (base64-encoded phrases)
      │     ├─ _DANGEROUS_PATTERNS (rm -rf, DROP TABLE, mkfs…)
      │     └─ issues? → step.status=FAILED, skip execution
      │
      ├─ GuardrailChecker.check(tool_name, {})
      │     └─ tool in known_tools set? (prevents hallucinated tool names)
      │
      └─ GuardrailEnforcer.check_tool_args(profile, tool_name, args)
            └─ profile-based arg constraints from governance_bundle
      │
      ▼
[Safety gate 2 — Policy engine]
      ├─ PolicyEngine.evaluate(tool_name, tenant_ctx, args)
      │     ├─ Redis LRANGE policy_rules:{tenant_id} → policy list
      │     ├─ Match rules by tool glob, risk tier
      │     ├─ PolicyResult.ALLOW   → proceed
      │     ├─ PolicyResult.DENY    → step.error = "policy_denied"
      │     │     └─ AuditLog.log(tool_denied, tool_name, tenant_id)
      │     └─ PolicyResult.REQUIRE_APPROVAL → HITL gate
      │
      ▼
[Safety gate 3 — HITL gate (when needed)]
      ├─ autonomy_mode == "supervised"?
      │     OR tool_risk_keyword (deploy/delete/prod/destroy/truncate)?
      │
      ├─ HITLGateway.request_approval(goal_id, action, risk_level)
      │     ├─ ApprovalRequest stored in _pending_requests dict
      │     ├─ AuditLog.log(approval_requested)
      │     ├─ NotificationService.notify(operator, request)
      │     ├─ Redis path: LPUSH hitl:{request_id} → any replica can resolve
      │     ├─ asyncio.Event path: event.wait(timeout=300s)
      │     │     ├─ APPROVED (operator calls POST /governance/approvals/{id}/approve)
      │     │     │     └─ event.set() → gate unblocks
      │     │     ├─ REJECTED → step.error = "hitl_rejected"
      │     │     └─ timeout → status=TIMED_OUT → GoalStatus.FAILED
      │     └─ record_approval_wait(duration_ms) metric
      │
      ▼
[Cost gate]
      ├─ CostController.check_and_record(goal_id, estimated_cost, tenant_ctx)
      │     ├─ Lua script: atomic check-and-increment on Redis
      │     │     ├─ goal_budget:{goal_id} key
      │     │     └─ daily_budget:{tenant_id}:{date} key
      │     ├─ GOAL_BUDGET_EXCEEDED → exception → GoalStatus.FAILED
      │     └─ DAILY_BUDGET_EXCEEDED → exception → GoalStatus.FAILED
      │
      ▼
[MCPClient.call_tool(tool_name, arguments, tenant_ctx)]
      │
      ├─ Split "server_name.tool_name" → (server_id, tool)
      ├─ MCPRegistry.get_server(server_id, tenant_ctx)
      │     └─ Redis HGET connectors:{tenant_id} → MCPServerConfig
      │
      ├─ Builtin handler check:
      │     ├─ name starts with "builtin-"?
      │     │     └─ dispatch to app/mcp/builtins/{name}.py
      │     │           e.g., jira_server.py, github_server.py, slack_server.py
      │     └─ no builtin → continue to HTTP dispatch
      │
      ├─ SSRFError guard: assert_public_url(server.url)
      │     └─ private IPs (RFC1918) / localhost / metadata URLs → SSRFError
      │
      ├─ Secret resolution: is_connector_secret_ref(auth_header)?
      │     └─ resolve_connector_secret_ref_for_tenant(ref, tenant_id)
      │           └─ RedisConnectorSecretStore.get(tenant_id, key_name) → plaintext
      │
      ├─ WebSocket transport?
      │     └─ MCPWebSocketClient.call(url, tool, args) → result
      │
      ├─ HTTP dispatch (standard MCP path):
      │     ├─ _is_mcp_endpoint(url)?
      │     │     ├─ YES: POST {url} + JSON-RPC tools/call envelope
      │     │     └─ NO:  POST {url}/tools/{tool_name}
      │     ├─ CircuitBreaker.call(httpx.post, timeout=30s)
      │     │     ├─ CLOSED → execute request
      │     │     ├─ HALF_OPEN → probe attempt
      │     │     └─ OPEN → raise CircuitBreakerOpenError → fallback + warning log
      │     └─ httpx.AsyncClient.post() → response
      │
      ├─ Response parsing:
      │     ├─ content-type: text/event-stream? → SSE parse (data: line)
      │     └─ JSON parse → result dict
      │
      └─ ToolCallResult(success, output, error)
      │
      ▼
[Post-execution safety]
      ├─ RollbackEngine.register(action=tool_name, inverse=compensating_fn)
      │     └─ inverse from tool_inverses.py per action type
      │
      ├─ IndirectInjectionScanner.scan(result.output)
      │     └─ injection in tool output? → warning, sanitize
      │
      ├─ GroundingChecker.check(step.output, tool_output)
      │     └─ claims match tool evidence? → ungrounded? step.status=UNGROUNDED
      │
      ├─ step.tool_calls.append({tool_name, success, output, error})
      ├─ record_tool_call(tool_name, success, duration_ms) metric
      └─ persist_tool_outcome(step, db) fire-and-forget
```

---

## 5. RAG Retrieval Flow

```
[AgentGraph._node_rag_retrieval] OR [RetrieverTool.retrieve()]
      │
      ▼
[Strategy selection]
      ├─ context["_rag_strategy_override"] set? → use it
      ├─ RuntimeProfile.rag_strategy.strategy set? → use it
      └─ RetrievalPlanner.select_strategy(goal)
            ├─ goal length < 50 chars → "vector"
            ├─ contains code keywords → "lexical"
            ├─ contains "what is"/"explain" → "vector"
            └─ default → "hybrid"
      │
      ▼ (parallel execution of retrieval legs)
      │
[KnowledgeStore.hybrid_search_db(query, embedding, collection_id)]
      │  → delegates to engine.hybrid_search(session, ...)
      │
[engine.hybrid_search(session, query, embedding, collection_id)]
      │
      ├─ Leg 1: pgvector ANN (when embedding available + mode != "lexical")
      │     SET LOCAL hnsw.ef_search = 200
      │     SELECT id, content FROM knowledge_chunks_{dim}
      │     WHERE collection_id = :cid
      │     ORDER BY embedding <=> :emb::vector LIMIT top_k*3
      │     → vector_ranks {chunk_id: (content, meta, rank)}
      │
      ├─ Leg 2: PostgreSQL FTS BM25-like (mode != "vector")
      │     SELECT id, content, ts_rank_cd(to_tsvector, plainto_tsquery)
      │     WHERE collection_id = :cid
      │       AND to_tsvector('english', content) @@ plainto_tsquery('english', :q)
      │     → fts_ranks {chunk_id: (content, meta, rank)}
      │
      ├─ Leg 3: pg_trgm fuzzy (mode != "vector")
      │     SELECT id, content, similarity(content, :q)
      │     WHERE collection_id = :cid AND content % :q
      │     → trgm_ranks {chunk_id: (content, meta, rank)}
      │
      ├─ RRF fusion:
      │     for each unique chunk_id:
      │       score = Σ 1/(60 + rank_i) for each leg that matched
      │     sort by score DESC → top_k results
      │
      └─ Optional: cross-encoder reranking of top-50
            model: ms-marco-MiniLM-L-6-v2
            → re-sort by cross-encoder score
      │
      ▼ (advanced strategies via engine.retrieve())
      │
      ├─ "flare":
      │     Generate → extract uncertain spans → re-retrieve → insert
      │
      ├─ "hyde" (HyDE):
      │     LLM.complete("Generate a hypothetical document for: {query}")
      │     → embed hypothetical doc → vector search with that embedding
      │
      ├─ "fusion":
      │     LLM.complete("Generate 3 sub-queries for: {query}")
      │     → run hybrid_search for each sub-query
      │     → RRF fuse all results
      │
      ├─ "multi_hop":
      │     retrieve(query, top_k=3) → for each result:
      │       extract follow-up question → retrieve again
      │     → aggregate all results
      │
      ├─ "raptor":
      │     Recursive summarization of clusters
      │     (multi-level) → search at each level
      │
      └─ "rerank" / "colbert":
            Cross-encoder or ColBERT reranking of initial results
      │
      ▼
[ContextPipeline.run(chunks, query)] — 7 steps
      │
      │  Step 1: RerankPolicy.rerank(chunks, query)
      │     ├─ SCORE: sort by existing score
      │     ├─ MMR: max marginal relevance (diversity)
      │     ├─ CROSS_ENCODER: ms-marco-MiniLM rerank
      │     └─ RRF: reciprocal rank fusion
      │
      │  Step 2: Filter chunks with score < min_relevance_score (0.35)
      │
      │  Step 3: Source diversity — max_per_source = 5 per unique source_url
      │
      │  Step 4: ContextBudget.apply(chunks)
      │     └─ tiktoken estimate, accumulate until 6000 token limit
      │
      │  Step 5: CitationThreader.thread(chunks)
      │     └─ inject [1], [2], ... references inline in content
      │
      │  Step 6: CitationManager.attach_citations(chunks)
      │     └─ extract {source_url, title, excerpt} → Citation list
      │
      │  Step 7: PromptBuilder.build_{planner,executor,verifier}_context(bundle)
      │     └─ role-specific context strings with citations appendix
      │
      └─ PipelineResult{planner_ctx, executor_ctx, verifier_ctx, citations}
```

---

## 6. Memory Write / Read Flow

### Write path (after goal completion or failure)

```
[_route() — success branch]
      │
      ├─ EpisodicMemoryStore.record(goal, result, tenant_ctx)
      │     └─ DB INSERT episodic_memory (goal_text, result, embedding, tenant_id)
      │     └─ in-memory deque capped at 1000 per tenant
      │
      ├─ ProceduralMemoryStore.learn(plan, outcome, tenant_ctx)
      │     └─ if outcome == success: DB UPSERT procedural_patterns
      │           (pattern_key → success_count, failure_count, avg_steps)
      │
      ├─ LongTermMemoryStore.extract_and_store(goal, steps, tenant_ctx)
      │     └─ LLM.complete("Extract generalizable lessons from: {goal+steps}")
      │     └─ embed(lesson_text) → pgvector INSERT ltm_memories
      │
      └─ ExecutionMemory.record(goal, plan, success=True, tenant_ctx)
            └─ DB UPSERT execution_memory (goal_text_hash → plan, success_rate)

[_route() — failure branch]
      │
      ├─ ReflexionWirer.maybe_store_async(goal, verification_feedback)
      │     └─ DB INSERT reflexion_store (goal_id, lesson_text, tenant_id)
      │
      └─ RollbackEngine.rollback_all_async()
            ├─ Pop stack in LIFO order
            ├─ For each (action, inverse_fn):
            │     try: await inverse_fn() (e.g. delete_branch, close_ticket)
            │     except: log warning, continue
            └─ log rolled_back_actions list
```

### Read path (next goal start, _node_rag_retrieval)

```
[_node_rag_retrieval — memory recall before planning]
      │
      ├─ ExecutionMemory.recall_async(goal, tenant_id, db, limit=3)
      │     ├─ Cosine similarity on goal_text_hash in execution_memory table
      │     └─ Returns [{plan: [...], success_rate: 0.9, ...}]
      │     context_parts += "[Past winning plans]\n- Plan: [...]"
      │
      ├─ ExecutionMemory.recall_failures(goal_hint, tenant_ctx, top_k=3)
      │     └─ Returns [{goal: "...", error: "..."}]
      │     context_parts += "[Previously Failed Approaches — Avoid These]"
      │
      ├─ LongTermMemoryStore.recall_async(goal, tenant_ctx, top_k=3, embedder)
      │     └─ embed(goal) → pgvector <=> cosine search on ltm_memories
      │     context_parts += "[Domain knowledge]\n- {lesson}"
      │
      ├─ EpisodicMemoryStore.recall(goal, tenant_ctx, top_k=5)
      │     └─ trigram + embedding similarity on episodic_memory
      │     context_parts += "[Recent episodes]\n- {episode}"
      │
      └─ ProceduralMemoryStore.recall(goal, tenant_ctx)
            └─ pattern matching on procedural_patterns
            context_parts += "[Proven patterns]\n- {pattern}"
      │
[_node_plan — reflexion recall]
      └─ ReflexionStore.recall(goal, tenant_id)
            └─ lazy DB hydration on first call (or cache hit after)
            planner_context += "[Reflexion lessons]\n- {lesson}"
```

---

## 7. Tenant Isolation Flow

```
[Any API request]
      │
      ▼
[TenantMiddleware]
      ├─ Extract: Authorization: Bearer <key> | X-API-Key: <key>
      ├─ Bypass paths: /health /metrics /docs /tenants/signup /auth/*
      ├─ key_resolver(api_key) → SQL: SELECT * FROM api_keys WHERE key_hash=hash(key)
      │     ├─ DB lookup (with RLS bypassed for this check)
      │     └─ TenantContext{tenant_id, plan_tier, rpm_limit, api_key_id, scopes}
      ├─ Rate limiter: sliding window in Redis
      │     key: rate:{tenant_id}:api   zadd/zremrangebyscore/zcard (60s window)
      │     └─ 429 Too Many Requests with X-RateLimit-* headers
      └─ request.state.tenant = TenantContext
      │
      ▼
[Handler — e.g. GoalService.submit_goal()]
      │
      ├─ GoalRecord.tenant_id = tenant_ctx.tenant_id  ← stored with goal
      │
      ├─ DB write via sqlalchemy_rls_context():
      │     async with session.begin():
      │       async with sqlalchemy_rls_context(session, tenant_id):
      │         await session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"))
      │         → SET LOCAL app.tenant_id = '{tenant_id}'
      │         ← RLS policies on every table apply this filter automatically
      │
      │  Example RLS policy on goals table:
      │    CREATE POLICY tenant_isolation ON goals
      │    USING (tenant_id = current_setting('app.tenant_id')::uuid);
      │
      ├─ Redis keys always namespaced:
      │     MCPRegistry:   connectors:{tenant_id}  (HSET/HGET)
      │     CostController: goal_budget:{goal_id}   daily_budget:{tenant_id}:{date}
      │     RateLimiter:   rate:{tenant_id}:{action}
      │     GoalEvents:    goal_events:{tenant_id}:{goal_id}
      │     HITLGateway:   hitl:{request_id}        (request_id is globally unique)
      │
      ├─ MCPRegistry per-tenant connector list:
      │     Redis HGET connectors:{tenant_id} → only this tenant's tools visible
      │
      ├─ KnowledgeStore collections:
      │     key: (tenant_id, collection_id) in-memory dict
      │     DB: knowledge_collections + knowledge_chunks_{dim} with RLS
      │
      └─ Celery queue per plan tier:
            goals.free / goals.starter / goals.professional / goals.enterprise
            → enterprise workers never share queue with free-tier noisy neighbors
```

---

## 8. Improvement Feedback Loop

```
[Goal execution completes (success or failure)]
      │
      ▼
[EvalRunner.score(goal, steps, context)] — 5 dimensions
      │  - goal_achievement: did the final step output match the goal?
      │  - tool_selection:   were the right tools called?
      │  - plan_quality:     was the plan minimal and correct?
      │  - output_quality:   is the output well-formed and useful?
      │  - efficiency:       step count relative to complexity
      └─ EvalResult{dimension_scores: {}, weighted_total: float}
      │
      ▼
[RuntimeScorecard.score(agent_state)] — 9 dimensions
      │  Additionally scores:
      │  - grounding_score: ratio of grounded vs ungrounded steps
      │  - hitl_rate: fraction of steps requiring human approval
      │  - retry_rate: iteration count vs max_iterations
      │  - cost_efficiency: cost_usd / steps_completed
      └─ RuntimeScorecardResult{dim_scores, overall: float}
      │
      ▼ (forked — each path fires independently)
      │
      ├─[SelfImprovementEngine.record_result(goal, plan, eval_score)]
      │     └─ score < improvement_threshold (0.7)?
      │           └─ generate_suggestion(goal, plan, score)
      │                 └─ LLM.complete(improvement_prompt)
      │                 → INSERT self_optimization_suggestions table
      │
      ├─[PromptOptimizer.record_result(variant_id, score, goal_id)]
      │     └─ UPDATE prompt_variants SET success_count += 1
      │     └─ if score < 0.5: arm_weight -= 0.1 (Bayesian downweight)
      │     └─ if accumulated data sufficient:
      │           → select_best_variant() for next planning call
      │
      ├─[SelfOptimizerV2.record_arm_result(agent_id, arm, score)]
      │     └─ UPDATE experiments table (arm_name, reward_sum, trial_count)
      │     └─ Thompson sampling: sample Beta(alpha+reward, beta+1-reward)
      │     └─ next goal for this agent: get_arm_config() selects arm with highest sample
      │
      ├─[ReflexionWirer.maybe_store_async() — failure path]
      │     └─ LLM.complete("Extract lesson from failure: {goal}\nFeedback: {feedback}")
      │     └─ INSERT reflexion_store (lesson_text, tenant_id, embedding)
      │     └─ next similar goal: ReflexionStore.recall() surfaces this lesson
      │
      └─[EpisodicMemoryStore.record() — success path]
            └─ INSERT episodic_memory (goal, result, embedding)
            └─ next similar goal: EpisodicMemoryStore.recall() returns this episode
```

---

## 9. MCP Tool Discovery and Execution Flow

```
[Operator: POST /connectors  {name, url, auth_config}]
      │
      ▼
[MCPRegistry.register_server(config, tenant_ctx)]
      ├─ MCPServerConfig{server_id, name, url, auth_config, capabilities}
      ├─ Redis HSET connectors:{tenant_id} field=server_id value=json(config)
      └─ in-memory cache updated
      │
      ▼
[Goal starts — executor LLM needs tool list]
      │
      ▼
[MCPClient.discover_tools(tenant_ctx)]
      │
      ├─ Cache check: _tool_cache[tenant_id] fresh? (TTL 60s)
      │     └─ cache hit → return cached list (no HTTP round-trip)
      │
      ├─ MCPRegistry.list_servers(tenant_ctx)
      │     └─ Redis HGETALL connectors:{tenant_id} → MCPServerConfig list
      │
      ├─ For each server config:
      │     ├─ assert_public_url(server.url) ← SSRF guard
      │     ├─ is_mcp_endpoint(url)?
      │     │     ├─ YES: POST {url} JSON-RPC {"method": "tools/list"}
      │     │     └─ NO:  GET {url}/tools
      │     ├─ HTTP response → [{name, description, inputSchema}]
      │     └─ ToolDefinition{name, description, input_schema, server_id}
      │
      └─ tools cached for 60s → returned to executor
      │
      ▼
[ToolPromptBuilder.build(tools)]
      └─ ALLOWED TOOLS:\n{tool_name}: {description}\n...
      └─ injected into executor system prompt before LLM call
      │
      ▼
[Executor LLM responds]
      └─ {"tool": "jira.create_issue", "arguments": {"summary": "...", "project": "AV"}}
      │
      ▼
[MCPClient.call_tool("jira.create_issue", args, tenant_ctx)]
      │  (Full flow in Flow 4)
      │
      ├─ Builtin handler: "builtin-jira" → app/mcp/builtins/jira_server.py
      │     ├─ auth_config: {url, username, api_token}
      │     ├─ httpx.post("{url}/rest/api/3/issue", headers=Basic auth)
      │     └─ return {issue_key, url}
      │
      └─ Standard HTTP MCP server:
            POST {server_url}/tools/create_issue
            JSON body: {"arguments": {...}}
            → ToolCallResult{success: True, output: {...}}
      │
      ▼
[IndirectInjectionScanner.scan(output)]
      └─ result.output contains injection? → sanitize + warn
      │
      ▼
[step.tool_calls.append(result)]
[step.output = sanitize_tool_raw_output(result.output)]
```

---

## 10. SSE Event Stream Flow (Frontend Update)

```
[AgentGraph node fires an event]
      │
      ▼
[AgentGraph._emit(event_dict)]
      │  event_dict e.g.:
      │    {"type": "step_completed", "step": {...}, "goal_id": "abc123"}
      │
      ├─ Sanitize: sanitize_event(event_dict, result_processor)
      │     └─ truncate large values, redact secrets
      │
      ├─ Append to agent_state.events (checkpointed)
      │
      ├─ self._event_callback(event_dict)  (when set by goal_service)
      │     └─ GoalService._dispatch_event(goal_id, event)
      │
      ▼
[GoalService._dispatch_event(goal_id, event)]
      │
      ├─ In-process path (direct asyncio task):
      │     ├─ GoalRecord = self._goals[goal_id]
      │     ├─ For each q in GoalRecord.subscribers:
      │     │     q.put_nowait(event)  ← asyncio.Queue
      │     └─ QueueFull? → remove dead subscriber
      │
      ├─ Redis pub/sub path (cross-replica):
      │     └─ redis.publish("goal_events:{tenant_id}:{goal_id}", json(event))
      │           └─ _subscribe_celery_goal_events() bridge picks this up
      │
      ▼
[Celery worker path (when goal runs on worker)]
      ├─ Worker publishes: redis.publish("goal_events:{tenant_id}:{goal_id}", event)
      ├─ API process bridge subscriber receives message
      │     └─ GoalService._subscribe_celery_goal_events() loop
      └─ Feeds event into GoalRecord.subscribers queues
      │
      ▼
[Client SSE connection: GET /goals/{goal_id}/stream]
      │
      ├─ GoalService.stream_events(goal_id, tenant_ctx)
      │     └─ subscriber_queue = asyncio.Queue(maxsize=200)
      │     └─ GoalRecord.subscribers.append(subscriber_queue)
      │
      ├─ async for event in subscriber_queue:
      │     ├─ event == None (sentinel)? → break (stream ended)
      │     └─ yield f"data: {json(event)}\n\n"  ← SSE frame
      │
      ▼
[Frontend: useGoalStream(goalId)]   ← agent-verse-frontend/src/lib/sse/useGoalStream.ts
      │
      ├─ new EventSource("/goals/{goalId}/stream")
      ├─ onmessage: dispatch to React state
      │     ├─ type == "step_completed" → append to steps array
      │     ├─ type == "plan_created"   → set planSteps
      │     ├─ type == "goal_complete"  → setStatus("complete"), close EventSource
      │     ├─ type == "goal_failed"    → setStatus("failed"), show error
      │     └─ type == "knowledge_retrieved" → show citation panel
      │
      ├─ TanStack Query invalidation:
      │     └─ queryClient.invalidateQueries(["goals", goalId])
      │           → refetch goal detail → re-render Goal detail panel
      │
      └─ useCollabSocket (WebSocket) ← collaborative editing awareness
            ws: GET /collab/{goal_id}/ws
            → presence updates, shared cursor, concurrent edits
```

---

## 11. Failure and Recovery Flow

```
[verification_failure (retry=True)]
      │
      ├─ agent_state.verification_feedback = "{specific failure reason}"
      ├─ agent_state.iterations += 1
      │
      ├─ stagnation check:
      │     last 3 feedbacks identical? → _route() returns "max_iter" → END
      │
      ├─ context gap? (feedback contains "insufficient"/"unclear"/"cannot determine"):
      │     └─ _route() returns "rag_remediate"
      │           └─ [_node_rag_remediate]
      │                 ├─ ContextGapDetector.extract_missing_topic(feedback)
      │                 ├─ RetrieverTool.retrieve(topic, allow_web_fallback=True)
      │                 ├─ context["remediation_count"] += 1 (max 2)
      │                 └─ context["remediation_context"] = new RAG text
      │           └─ → [_node_plan] (replan with remediation context)
      │
      ├─ enable_reflection=True:
      │     └─ _route() returns "reflect"
      │           └─ [_node_reflect]
      │                 ├─ REFLECTION_SYSTEM prompt
      │                 ├─ identify FAILED_STEP + ROOT_CAUSE + FIX
      │                 └─ agent_state.verification_feedback = reflection output
      │           └─ → [_node_plan] (replan with reflection)
      │
      └─ default: → [_node_plan] (replan with feedback injected into context)

[retry=False (permanent failure)]
      │
      ├─ GoalStatus.FAILED
      ├─ AuditLog.log(goal_failed, reason)
      ├─ record_goal_failed() Prometheus metric
      └─ GoalService._dispatch_event("goal_failed") → SSE → frontend

[max_iterations (100) reached]
      │
      ├─ terminal_reason = "max_iter"
      ├─ GoalStatus.FAILED
      └─ SSE "goal_failed: max iterations exceeded"

[budget_exceeded]
      │
      ├─ CostController raises CostBudgetExceeded
      ├─ AgentGraph catches → GoalStatus.FAILED
      ├─ AuditLog.log(budget_exceeded)
      └─ SSE "goal_failed: budget_exceeded"

[tool_permission_denied — PolicyEngine DENY]
      │
      ├─ step.error = "policy_denied: {tool_name}"
      ├─ step.status = FAILED
      ├─ AuditLog.log(tool_denied)
      └─ executor continues to next step (non-fatal per-step)

[circuit_breaker OPEN]
      │
      ├─ CircuitBreakerOpenError raised from call_with_circuit_breaker
      ├─ MCPClient catches → ToolCallResult(success=False, error="circuit_open")
      ├─ step.tool_calls.append(failed_result)
      └─ log: "circuit_breaker_open tool={name}" (warning, not fatal)

[HITL timeout (300s)]
      │
      ├─ HITLGateway.request_approval → asyncio.wait_for(timeout=300s) raises TimeoutError
      ├─ approval_request.status = TIMED_OUT
      ├─ AuditLog.log(hitl_timeout)
      ├─ GoalStatus.FAILED
      └─ SSE "goal_failed: hitl_timeout"

[Manual rollback path]
      │
      POST /goals/{goal_id}/rollback
      │
      ├─ GoalService.get_rollback_plan(goal_id)
      │     └─ RollbackEngine._stack → list of (action, inverse)
      ├─ Operator reviews list
      └─ GoalService.execute_rollback(goal_id)
            └─ RollbackEngine.rollback_all_async()
                  ├─ LIFO execution of inverse_fn list
                  ├─ Each inverse: e.g. delete_branch, close_ticket, revert_file
                  └─ AuditLog.log(rollback_executed, actions=[...])
```

---

## 12. Celery Task Lifecycle

```
[GoalService.submit_goal() — with CeleryGoalTaskQueue]
      │
      ├─ task_queue.enqueue(goal_id, tenant_ctx, execution_context)
      │     └─ CeleryGoalTaskQueue._apply_async():
      │           queue = PLAN_QUEUE_MAP[tenant_ctx.plan_tier.value]
      │             ├─ "free"         → "goals.free"
      │             ├─ "starter"      → "goals.starter"
      │             ├─ "professional" → "goals.professional"
      │             └─ "enterprise"   → "goals.enterprise"
      │           celery_app.send_task(
      │             "app.scaling.tasks.run_goal",
      │             args=[goal_id, tenant_data],
      │             queue=queue,
      │             task_id=goal_id,
      │           )
      │           └─ Redis LPUSH {queue} → task serialized as JSON
      │
      ▼
[Celery worker process — picks up task]
      │
      ├─ (worker_init signal on startup):
      │     _setup_worker_checkpointer()
      │     └─ RedisSaver.from_conn_string(REDIS_URL)
      │           → _WORKER_CHECKPOINTER set globally
      │
      ├─ run_goal(goal_id, tenant_data) task fires
      │
      ▼
[run_goal task — app/scaling/tasks.py]
      │
      ├─ sync_redis = _get_sync_redis()  ← module-level pool
      │
      ├─ _SyncGoalLock.acquire(goal_id, ttl_ms=1_800_000)
      │     └─ SET NX PX: SET goal_lock:{goal_id} {lock_value} NX PX 1800000
      │     └─ already locked? → log "duplicate_goal_execution" + return (idempotent)
      │
      ├─ goal_data = DB SELECT goals WHERE id = goal_id
      │     └─ not found? → release lock + return
      │
      ├─ goal_service = _build_goal_service_for_worker(app_state)
      │     └─ creates GoalService with DB pool + real Redis
      │
      ├─ agent_graph = goal_service._make_agent_loop_for_tenant(goal_data, tenant_ctx)
      │     └─ builds AgentGraph with all governance + memory + RAG wired
      │     └─ sets graph._db_session_factory = db_pool
      │     └─ sets graph._event_callback = lambda event: redis.publish(...)
      │
      ├─ asyncio.run(_run_async(goal_id, agent_graph, goal_service))
      │     └─ fresh event loop per task (Celery tasks are sync)
      │
      ├─ _run_async():
      │     ├─ await agent_graph.run(goal, tenant_ctx, execution_context)
      │     │     └─ Full AgentGraph execution (see Flow 3)
      │     │     └─ emits events via: redis.publish("goal_events:{tid}:{gid}", event)
      │     │
      │     ├─ goal.status = COMPLETE or FAILED
      │     ├─ DB UPDATE goals SET status, result, completed_at
      │     └─ redis.publish("goal_events:{tid}:{gid}", {type: "worker_complete"})
      │           └─ API process bridge subscriber picks this up → SSE → frontend
      │
      ├─ _SyncGoalLock.release(goal_id)  ← Lua atomic check-and-delete
      │
      ▼
[Task success — result stored in Redis backend (TTL 24h)]

[Task failure (exception)]
      │
      ├─ Celery retry (task_default_retry_delay=30s, up to max_retries)
      │     └─ apply_async(countdown=30)
      │
      ├─ max_retries exceeded:
      │     └─ run_goal_dlq task fired (goals_dlq queue)
      │           └─ DLQ drain: every 5 min (beat schedule)
      │           └─ DB UPDATE goals SET status=FAILED, error_message
      │
      ▼
[Beat scheduler — periodic tasks]
      │
      ├─ fire_due_schedules  (every 60s):
      │     └─ SELECT * FROM trigger_schedules WHERE next_run <= now()
      │     └─ For each: run_scheduled_goal.apply_async(queue="schedules")
      │     └─ UPDATE next_run = croniter.get_next()
      │
      ├─ check_mcp_health (every 30s):
      │     └─ HTTP GET each registered MCP server /health
      │     └─ INSERT connector_health_snapshots (latency, status)
      │
      ├─ detect_stuck_goals (every 5 min):
      │     └─ SELECT goals WHERE status=EXECUTING AND updated_at < now-30min
      │     └─ UPDATE status=FAILED WHERE not lock held in Redis
      │
      ├─ expire_hitl_approvals (every 60s):
      │     └─ HITLGateway.expire_old(max_age=300s)
      │
      ├─ flush_audit_wal (every 10s):
      │     └─ INSERT bulk from in-memory WAL buffer → audit_events table
      │
      └─ execute_retention_policy (3 AM UTC daily):
            └─ DELETE goals older than retention period (per plan tier)
```

---

*All line numbers reference the `agent-verse-backend/` source tree. Diagram boxes map directly to Python classes and functions named in the codebase.*
