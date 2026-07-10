# AgentVerse Documentation Index

> **Reading note:** Every file path cited here is relative to the monorepo root
> (`Agent-Verse/`). Line numbers reference the state of the codebase as of
> 2026-07-09. Use them as navigation anchors — not contracts.

---

## 1. System Mental Model: AgentVerse as an Operating System

AgentVerse is best understood as an **OS for autonomous AI agents**. Every
abstraction maps cleanly to a classical operating-systems concept:

| OS Concept | AgentVerse Equivalent | Primary Implementation |
|---|---|---|
| **Users / processes** | Tenants | `app/tenancy/context.py` — `TenantContext` (immutable frozen dataclass) |
| **Processes** | Goals | `app/agent/state.py` — `AgentState`; `app/services/goal_service.py` — `GoalRecord` |
| **Process scheduler** | Celery + per-plan queues | `app/scaling/celery_app.py:42` — `PLAN_QUEUE_MAP` |
| **Interactive shell** | Goal API | `POST /goals` → `app/api/goals.py`; SDK clients |
| **Device drivers** | MCP Connectors | `app/mcp/client.py`, `app/mcp/registry.py`; 320 built-in server files in `app/mcp/servers/` |
| **Syscalls** | MCP tool calls | `MCPClient.call_tool()` — HTTP/WebSocket to registered MCP servers |
| **Virtual memory / cache** | RAG context window | `app/rag/store.py` `KnowledgeStore`, `app/rag/semantic_cache.py` |
| **Filesystem** | PostgreSQL + pgvector | `app/db/` — SQLAlchemy 2 async + asyncpg + Alembic (88 migrations) |
| **IPC / signals** | Redis pub/sub | `GoalService._subscribe_celery_goal_events()` — `goal_events:{tid}:{gid}` channels |
| **Process isolation** | Row-Level Security | `app/db/rls.py` — `SET LOCAL app.tenant_id` Postgres GUC per transaction |
| **Resource quotas** | Budget + rate limits | `app/governance/cost.py` `RedisCostController`, `app/tenancy/context.py` `PLAN_LIMITS` |
| **Kernel** | FastAPI lifespan + LangGraph | `app/main.py` `create_app()`/`lifespan()` + `app/agent/graph.py` `AgentGraph` |
| **Watchdog / audit** | Append-only audit log + HITL | `app/governance/audit.py`, `app/governance/hitl.py` |
| **Core dump / rollback** | Compensating actions | `app/reliability/rollback.py` `RollbackEngine` (LIFO inverses) |

**Executive summary of the lifecycle:**

```
Tenant submits goal via HTTP/SDK
  → TenantMiddleware authenticates (API key or Keycloak JWT)
  → GoalService creates GoalRecord + dispatches Celery task
  → Celery worker acquires _SyncGoalLock + builds AgentGraph
  → AgentGraph runs LangGraph state machine
      initialize → rag_retrieval → [think] → [tree_of_thoughts]
      → plan → execute → [refine] → [self_consistency]
      → verify → [reflect/peer_review] → route
        → complete | replan | max_iter | waiting_human | rag_remediate
  → Events stream via Redis pub/sub → SSE → React frontend
  → Outcome persisted to Postgres + LangGraph Redis checkpoint
```

---

## 2. Repository Map

### Five independently deployable projects

| Directory | Stack | Role | Entry Point |
|---|---|---|---|
| `agent-verse-backend/` | Python 3.12 · FastAPI 0.111 · LangGraph · Celery · Postgres+pgvector | Source of truth; publishes OpenAPI contract | `app/main.py` → `create_app()` |
| `agent-verse-frontend/` | React 19 · Vite · TanStack Query · Zustand · Tailwind | Web UI consuming backend over HTTP + SSE + WebSocket | `src/main.tsx` |
| `agent-verse-sdk-python/` | Python 3.11+ · httpx · pydantic | Official Python client + `agentverse` CLI | `agentverse/client.py` |
| `agent-verse-sdk-typescript/` | TypeScript · vitest (zero runtime deps) | Official TS/JS client | `src/index.ts` |
| `agent-verse-github-action/` | Python entrypoint in Docker | CI/CD goal submission and await | `action.py` |

> The backend's `pyproject.toml` depends on `agentverse-sdk` via a local path
> source (`[tool.uv.sources]`). Backend tests therefore exercise the real Python
> SDK. Changes to the SDK propagate after `uv sync`.

### Backend sub-packages (all under `agent-verse-backend/app/`)

| Package | Lines (approx) | Role |
|---|---|---|
| `agent/` | ~8 000 | LangGraph graph, state, prompts, patterns, tool calls, reflexion |
| `api/` | ~5 000 | ~30 FastAPI routers (goals, agents, governance, knowledge, etc.) |
| `auth/` | ~1 000 | Keycloak SSO, scope enforcement, agent identity |
| `db/` | ~2 500 | SQLAlchemy models, 88 Alembic migrations, RLS context managers |
| `enterprise/` | ~2 000 | Compliance, simulation, red-team, marketplace |
| `governance/` | ~2 500 | Cost, HITL, audit, policies, permissions |
| `intelligence/` | ~3 000 | Eval, meta-agent, self-optimizer v2, guardrail engine v2 |
| `mcp/` | ~3 000 | Registry, HTTP/WS client, OAuth, 320 built-in server files |
| `memory/` | ~1 500 | Execution, long-term, episodic, procedural memory stores |
| `providers/` | ~2 000 | LLM Protocol + Anthropic, OpenAI-compatible, Gemini, Voyage, FakeProvider |
| `rag/` | ~2 000 | KnowledgeStore, SemanticCache, RRF engine, RetrievalPlanner |
| `reliability/` | ~1 500 | Circuit breakers, rollback, dedup, bulkhead, result processor |
| `scaling/` | ~3 000 | Celery app + tasks (2 918 lines), beat guards |
| `services/` | ~5 000 | GoalService (3 085 lines), TenantService, EventStore, NotificationService |
| `tenancy/` | ~1 000 | Middleware (357 lines), rate limiter, context, TenantScopedStore |
| `triggers/` | ~800 | NLScheduler (NL → TriggerSpec), ScheduleStore, croniter |

### Infrastructure services (full `docker-compose.yml` stack)

| Service | Port | Purpose |
|---|---|---|
| `backend` | 8000 | FastAPI REST + SSE + WebSocket |
| `frontend` | 5173 | React SPA (nginx in container) |
| `postgres` | 5432 | Primary database (pgvector enabled) |
| `pgbouncer` | 6432 | Connection pool proxy |
| `redis` | 6379 | Celery broker, pub/sub, rate-limit counters, checkpoints |
| `keycloak` | 8080 | SSO / OIDC identity provider |
| `minio` | 9000/9001 | Object storage for RPA artifacts |
| `mailpit` | 1025/8025 | Dev email catcher |
| `otel-collector` | 4317/4318 | OpenTelemetry ingest |
| `jaeger` | 16686 | Distributed tracing UI |
| `searxng` | 8081 | Privacy-preserving web search |
| `prometheus` | 9090 | Metrics collection |
| `grafana` | 3001 | Metrics dashboards |

---

## 3. Concept-to-Code Map

Every major concept in the platform, mapped to its canonical implementation file and symbol.

### Application lifecycle

| Concept | File | Symbol / line |
|---|---|---|
| Application factory | `app/main.py:447` | `create_app(settings, manage_pools, ...)` |
| Service wiring lifespan | `app/main.py:638` | `lifespan()` — `@asynccontextmanager` |
| Settings / env config | `app/core/config.py` | `Settings` (Pydantic), `get_settings()` |
| Connection pools | `app/core/pools.py` | `ConnectionPools` — asyncpg + Redis |
| In-memory Redis stub | `app/main.py:250` | `_FakeRedis` (tests + no-pool mode) |
| Error handlers | `app/main.py:430` | `_register_error_handlers()` |
| Health registry | `app/observability/health.py` | `HealthRegistry` |

### Authentication & tenancy

| Concept | File | Symbol / line |
|---|---|---|
| Tenant auth middleware | `app/tenancy/middleware.py:170` | `TenantMiddleware.dispatch()` |
| Security headers | `app/tenancy/middleware.py:331` | `SecurityHeadersMiddleware` |
| API key extraction | `app/tenancy/middleware.py:90` | `_extract_key()` |
| Keycloak JWT resolution | `app/auth/keycloak.py` | `resolve_tenant_from_jwt()` |
| MFA enforcement | `app/tenancy/middleware.py:218` | `_mfa_db_store`, `_mfa_verified_sessions` |
| Tenant identity + plan | `app/tenancy/context.py:62` | `TenantContext` frozen dataclass |
| Plan tiers | `app/tenancy/context.py:9` | `PlanTier` StrEnum |
| Per-plan limits table | `app/tenancy/context.py:26` | `PLAN_LIMITS: dict[PlanTier, PlanLimits]` |
| Sliding window rate limiter | `app/tenancy/rate_limiter.py` | `SlidingWindowRateLimiter.check_and_record()` |
| In-process rate fallback | `app/tenancy/middleware.py:35` | `_check_rate_limit_with_fallback()` |
| Row-Level Security (Postgres) | `app/db/rls.py:20` | `rls_context()`, `sqlalchemy_rls_context()` |
| System-level bypass (RLS off) | `app/db/rls.py:57` | `system_session()` |

### Agent execution

| Concept | File | Symbol / line |
|---|---|---|
| Goal status state machine | `app/agent/state.py:16` | `GoalStatus` StrEnum (7 values) |
| Step status | `app/agent/state.py:26` | `StepStatus` StrEnum |
| Agent runtime state | `app/agent/state.py:61` | `AgentState` dataclass |
| LangGraph graph type | `app/agent/graph.py:97` | `GraphState` TypedDict |
| Agent graph class | `app/agent/graph.py:180` | `AgentGraph` |
| Graph construction | `app/agent/graph.py:327` | `_build()` — nodes + edges + compile() |
| Feature flags | `app/agent/graph.py:216` | `enable_cot`, `enable_reflection`, `enable_tree_of_thoughts`, `enable_self_refine`, `enable_self_consistency`, `enable_peer_review`, `enable_goal_tree` |
| High-risk keywords | `app/agent/graph.py:87` | `_HIGH_RISK_KEYWORDS` frozenset |
| Routing function | `app/agent/graph.py:4023` | `_route()` — synchronous, called by LangGraph |
| LangGraph checkpoint | `app/agent/graph.py:281` | `self._checkpointer` (RedisSaver or MemorySaver) |
| Prompts | `app/agent/prompts.py` | `PLANNER_SYSTEM`, `EXECUTOR_SYSTEM`, `VERIFIER_SYSTEM`, `CHAIN_OF_THOUGHT_SYSTEM`, `REFLECTION_SYSTEM` |
| Tool risk classification | `app/agent/tool_risk.py` | `classify_tool_risk()` |
| Indirect injection scanner | `app/agent/sanitization.py` | `IndirectInjectionScanner` |
| Goal tree decomposition | `app/agent/goal_tree.py` | `GoalTreePlanner` |
| Agent router | `app/agent/router.py` | `AgentRouter.route()` |
| Supervisor / debate | `app/agent/supervisor.py`, `app/agent/debate.py` | Multi-agent orchestration |
| Reflexion store | `app/agent/reflexion_wirer.py` | `get_reflexion_wirer()` |
| Model router | `app/agent/model_router.py` | `ModelRouter.model_for(task)` |

### Goal service & queue

| Concept | File | Symbol / line |
|---|---|---|
| Goal lifecycle service | `app/services/goal_service.py:286` | `GoalService` |
| Goal runtime record | `app/services/goal_service.py:71` | `GoalRecord` dataclass |
| Checkpointer resolution | `app/services/goal_service.py:106` | `_resolve_checkpointer()` |
| Celery → SSE bridge | `app/services/goal_service.py:392` | `_subscribe_celery_goal_events()` |
| HITL rejection subscriber | `app/services/goal_service.py:334` | `start_hitl_rejection_subscriber()` |
| Celery task queue | `app/services/goal_queue.py` | `CeleryGoalTaskQueue` |
| Celery app config | `app/scaling/celery_app.py` | `celery_app` |
| Per-plan queue map | `app/scaling/celery_app.py:42` | `PLAN_QUEUE_MAP` |
| Goal Celery task | `app/scaling/tasks.py` | `run_goal()` |
| Distributed goal lock | `app/scaling/tasks.py:115` | `_SyncGoalLock` (Redis SET NX + Lua release) |
| Worker checkpointer setup | `app/scaling/tasks.py:82` | `_setup_worker_checkpointer()` (`@worker_init.connect`) |

### MCP connectors

| Concept | File | Symbol / line |
|---|---|---|
| MCP server config | `app/mcp/registry.py:47` | `MCPServerConfig` Pydantic model |
| Auth types | `app/mcp/registry.py:27` | `AuthType` StrEnum (BEARER, API_KEY, OAUTH_AC, PKCE, MTLS, HMAC, …) |
| MCP server registry | `app/mcp/registry.py` | `MCPRegistry` — Redis-backed per-tenant |
| Built-in handler registry | `app/mcp/registry.py:24` | `_BUILTIN_HANDLER_REGISTRY` module dict |
| MCP HTTP/WS client | `app/mcp/client.py:124` | `MCPClient` |
| SSRF guard | `app/net/ssrf_guard.py` | `assert_public_url()` |
| PKCE OAuth flows | `app/mcp/oauth.py` | `OAuthFlowManager` |
| Secret resolution | `app/providers/vault.py` | `resolve_connector_secret_ref()` |
| Built-in server wiring | `app/mcp/servers/registry_wiring.py` | `register_builtin_servers()` |
| Tool result cache | `app/mcp/tool_cache.py` | `ToolResultCache` (Redis-backed) |

### LLM providers

| Concept | File | Symbol / line |
|---|---|---|
| Provider protocol | `app/providers/base.py:100` | `LLMProvider` (`Protocol`) — `complete()` + `embed()` |
| Completion request/response | `app/providers/base.py:40` | `CompletionRequest`, `CompletionResponse` |
| Embed request/response | `app/providers/base.py:84` | `EmbedRequest`, `EmbedResponse` |
| Token usage | `app/providers/base.py:54` | `TokenUsage` |
| Provider auto-detection | `app/providers/registry.py` | `resolve_provider()` |
| Anthropic provider | `app/providers/anthropic_provider.py` | `AnthropicProvider` |
| OpenAI-compatible | `app/providers/openai_compatible.py` | `OpenAICompatibleProvider` |
| Voyage embedding | `app/providers/voyage_provider.py` | `VoyageProvider`, `LocalEmbedProvider` |
| Gemini provider | `app/providers/gemini_provider.py` | `GeminiProvider` |
| Fake provider (tests) | `app/providers/fake.py` | `FakeProvider` (cycling responses) |
| Circuit breaker wrapper | `app/providers/circuit_breaker.py` | `call_with_circuit_breaker()` |
| Encrypted credential vault | `app/providers/vault.py` | `get_vault()`, `RedisConnectorSecretStore` |
| Cross-model verifier | `app/main.py:192` | `_build_verifier_provider()` |

### Governance

| Concept | File | Symbol / line |
|---|---|---|
| In-memory cost enforcement | `app/governance/cost.py:43` | `CostController.check_and_record()` |
| Redis cost enforcement | `app/governance/cost.py:169` | `RedisCostController` |
| Atomic Lua budget script | `app/governance/cost.py:145` | `_LUA_CHECK_AND_INCREMENT` |
| HITL approval gateway | `app/governance/hitl.py` | `HITLGateway.request_approval()` |
| Approval status | `app/governance/hitl.py` | `ApprovalStatus` StrEnum |
| Append-only audit log | `app/governance/audit.py` | `AuditLog`, `AuditEvent` |
| Policy engine | `app/governance/policies.py` | `PolicyEngine.evaluate()` → `PolicyResult` |
| Permission matrix | `app/governance/permissions.py` | `PermissionMatrix`, `ActionLevel` |

### Reliability

| Concept | File | Symbol / line |
|---|---|---|
| Rollback engine | `app/reliability/rollback.py` | `RollbackEngine.register()`, `rollback_all_async()` |
| Tool inverses | `app/reliability/tool_inverses.py` | Compensating action registry |
| Circuit breaker | `app/reliability/circuit_breaker.py` | `CircuitBreaker` |
| Deduplication cache | `app/reliability/dedup.py` | `DeduplicationCache`, `RedisDeduplicationCache` |
| Per-tenant bulkhead | `app/reliability/bulkhead.py` | Concurrent goal limiter |
| Result processor | `app/reliability/result_processor.py` | `ResultProcessor` — output truncation |

### Memory and RAG

| Concept | File | Symbol / line |
|---|---|---|
| Execution memory (per-goal) | `app/memory/execution.py` | `ExecutionMemory.recall_async()`, `record_async()` |
| Long-term memory (cross-session) | `app/memory/long_term.py` | `LongTermMemoryStore.recall_async()`, `extract_from_goal_async()` |
| Episodic memory | `app/memory/episodic.py` | `EpisodicMemoryStore.record()` |
| Procedural memory | `app/memory/procedural.py` | `ProceduralMemoryStore.learn()` |
| Tool reliability stats | `app/memory/tool_reliability.py` | `ToolReliabilityStore` |
| Knowledge store (pgvector + trigram) | `app/rag/store.py` | `KnowledgeStore.hybrid_search_db()` |
| Semantic LLM call cache | `app/rag/semantic_cache.py` | `SemanticCache` |
| RRF retrieval engine | `app/rag/engine.py` | `hybrid_search()`, `retrieve()` |
| Retrieval planner | `app/rag/engine.py` | `RetrievalPlanner.select_strategy()` |
| CRAG context gap detector | `app/rag/agentic/context_gap_detector.py` | `ContextGapDetector.has_gap()` |
| RAGTrace (observability) | `app/rag/agentic/rag_trace.py` | `RAGTrace.record_retrieval()`, `to_sse_event()` |

### Frontend

| Concept | File | Symbol |
|---|---|---|
| HTTP API client | `agent-verse-frontend/src/lib/api/client.ts` | `ApiClient` |
| SSE goal stream hook | `agent-verse-frontend/src/lib/sse/useGoalStream.ts` | `useGoalStream()` |
| WebSocket collab socket | `agent-verse-frontend/src/lib/ws/useCollabSocket.ts` | `useCollabSocket()` |
| Goal feature slice | `agent-verse-frontend/src/features/goals/` | Goal submission, status, results |
| Agent feature slice | `agent-verse-frontend/src/features/agents/` | Agent config, marketplace |

---

## 4. Reading Paths for 4 Audiences

### Audience A — Beginner (just learned what an LLM is)

Goal: Understand what AgentVerse _does_ before reading any implementation code.

1. `README.md` — 5-minute overview, architecture table, quick start guide
2. `AGENTS.md` §"What this is" — mental model: tenants, goals, agents, MCP connectors
3. `app/agent/state.py` — read all 98 lines. Understand `GoalStatus` (7 states), `AgentState` (goal, plan, steps, status), `StepResult`
4. `app/tenancy/context.py` — understand `TenantContext` (who is making requests), `PlanTier`, `PLAN_LIMITS`
5. `app/providers/base.py` — understand `LLMProvider` protocol (just `complete()` and `embed()`), `CompletionRequest` / `CompletionResponse`
6. Run the quick-start in `README.md` — see a real goal execute end to end
7. `app/agent/prompts.py` — read `PLANNER_SYSTEM`, `EXECUTOR_SYSTEM`, `VERIFIER_SYSTEM` to understand what each LLM role does
8. Read **Section 5** (AgentGraph nodes) in `01-platform-lifecycle-and-core-workflows.md`
9. Explore one complete end-to-end flow from Section 5 of this document (the Jira example)
10. Run `uv run pytest tests/agent/test_loop.py -v` and read the test cases as executable documentation

### Audience B — Backend Engineer (wants to contribute)

Goal: Understand exactly what code runs for every request, and how to extend it.

1. `AGENTS.md` completely — conventions, toolchain quirks (`uv`, `colima`, `httpx2`, `filterwarnings=error`)
2. `app/main.py:447–638` — `create_app()` — which services are built in what order and why
3. `app/main.py:638–1100` — `lifespan()` — the DB/Redis upgrade path; why two phases exist
4. `app/agent/graph.py:180–405` — `AgentGraph.__init__()` + `_build()` — all nodes, all edges, feature-flag-driven topology
5. Walk each node: `_node_initialize` (422), `_node_rag_retrieval` (555), `_node_plan` (wherever it starts), `_node_execute`, `_node_verify`, `_route` (4023)
6. `app/services/goal_service.py:286–500` — `GoalService` structure, `GoalRecord`, the Celery→SSE bridge
7. `app/scaling/tasks.py:115–200` — `_SyncGoalLock`, `run_goal()` Celery task, worker checkpointer setup
8. `app/governance/cost.py:145–300` — Lua atomic check-and-increment, `RedisCostController`
9. `app/db/rls.py` — all 88 lines, understand `SET LOCAL` GUC pattern
10. Run `uv run pytest -m "not slow and not integration"` to validate changes; use `uv run ruff check . && uv run mypy app` before committing

### Audience C — Platform Architect (evaluating for production)

Goal: Understand the isolation model, failure boundaries, and horizontal scaling properties.

1. **Multi-tenancy boundaries:** `app/db/rls.py` (database layer), `app/tenancy/middleware.py` (HTTP layer), `app/tenancy/context.py` `PLAN_LIMITS` (quota layer)
2. **Cost isolation:** `app/governance/cost.py:169` `RedisCostController` — atomic Lua script, daily TTL keys, cross-replica accuracy; key patterns: `cost:daily:{tenant}:{date}`, `cost:goal:{tenant}:{goal_id}`
3. **Rate limiting:** `app/tenancy/middleware.py:276–328` — sliding-window Redis limiter with in-process fallback (`_check_rate_limit_with_fallback`), X-RateLimit-* headers
4. **Queue isolation:** `app/scaling/celery_app.py:42` — `PLAN_QUEUE_MAP` (enterprise tenants never share workers with free tier unless workers listen to all queues)
5. **Exactly-once execution:** `app/scaling/tasks.py:115` `_SyncGoalLock` — Redis `SET NX PX` acquisition + Lua check-and-delete release; 30-minute TTL
6. **Durability:** `app/main.py:681–712` — LangGraph checkpoint hierarchy: `AsyncRedisSaver` → `RedisSaver` → `MemorySaver`; `app/scaling/tasks.py:82` `_setup_worker_checkpointer()` — one-time per worker
7. **Auth security:** `app/tenancy/middleware.py:105` `_try_resolve_sso()` — Keycloak JWT path; `app/auth/keycloak.py`; `app/auth/scope_enforcement.py` `ScopeEnforcementMiddleware`; MFA enforcement at lines 218–274
8. **Rollback guarantees:** `app/reliability/rollback.py` — LIFO compensating actions registered per tool call; `app/reliability/tool_inverses.py` — inverse mapping
9. **Security headers:** `app/tenancy/middleware.py:331` — HSTS (max-age=63072000), CSP, X-Frame-Options DENY, no-sniff
10. **Observability stack:** OTel → Jaeger (traces), Prometheus + Grafana (metrics), structured JSON logs; `app/observability/tracing.py`, `app/observability/metrics.py`

### Audience D — Production Operator (running in prod)

Goal: Know exactly what to look at when something breaks.

1. **Start here for incidents:** `RUNBOOK.md` — escalation matrix and common failure modes
2. **Critical env vars:** `DATABASE_URL` (asyncpg DSN), `REDIS_URL`, `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`, `ENVIRONMENT=production`, `MANAGE_POOLS=1` (enables lifespan pool startup)
3. **Production guards in code:** `app/main.py:479–486` — refuses FakeProvider in production; `app/core/config.py` — refuses default `agentverse:agentverse@` DB credentials
4. **Health and metrics (auth-bypassed):** `GET /health`, `GET /metrics`, `GET /status` — listed in `_BYPASS_PREFIXES` at `app/tenancy/middleware.py:71`
5. **Celery queue names** (worker `-Q` flag must include all): `goals`, `goals.free`, `goals.starter`, `goals.professional`, `goals.enterprise`, `goals_dlq`, `schedules`, `maintenance`
6. **Redis key namespaces:**
   - `goal_lock:{goal_id}` — `_SyncGoalLock` (30-min TTL)
   - `goal_events:{tenant_id}:{goal_id}` — Celery→SSE pub/sub channel
   - `cost:daily:{tenant_id}:{date}` — daily budget counter (auto-resets at UTC midnight)
   - `cost:goal:{tenant_id}:{goal_id}` — per-goal budget counter
   - `mcp:servers:{tenant_id}:{server_id}` — MCP connector config
   - LangGraph checkpoint thread keys: `goal-{goal_id}`
7. **DB migrations:** `uv run alembic upgrade head` — 88 revisions; never edit a deployed migration, always add a new one
8. **Stuck goal detection:** `app/scaling/tasks.py` `detect_stuck_goals` beat task — runs in `maintenance` queue
9. **Cost 80% alert:** `app/governance/cost.py:116–125` — logs `budget_80pct_alert` when daily spend crosses 80%; search logs for this key to find tenants approaching limits
10. **Graceful shutdown:** `app/scaling/tasks.py:24` — SIGTERM handler; LangGraph writes checkpoint after every completed step, so the last durable state is always safe on worker restart

---

## 5. Quick-Start Flows

Three representative end-to-end flows. Each traces every system boundary.

---

### Flow 1: "List all open Jira tickets assigned to me" (Simple ReAct, ~1 iteration)

This is the simplest flow: one agent, one tool connector, one plan, one verify cycle.

```
Step 1 — HTTP ingress
  POST /goals HTTP/1.1
  Authorization: Bearer avk_live_xxxx
  {"goal": "List all open Jira tickets assigned to me", "agent_id": "jira-agent"}

  → app/tenancy/middleware.py TenantMiddleware.dispatch():
      _extract_key() → "avk_live_xxxx"
      raw_key has no dots → skip _try_resolve_sso()
      await self._resolver("avk_live_xxxx") → TenantContext(tenant_id="t1", plan=STARTER)
      request.state.tenant = tenant_ctx
      SlidingWindowRateLimiter.check_and_record() → allowed=True (STARTER: 10k rpm)
      X-RateLimit-Limit: 10000 added to response headers

Step 2 — Goal submission
  → app/services/goal_service.py GoalService.submit_goal():
      DeduplicationCache: SHA256(tenant_id + goal_text) → not in cache → proceed
      Plan limit: PLAN_LIMITS[STARTER].goals_per_day = 100,000 → not exceeded
      Concurrent-goal bulkhead: below threshold → increment counter in Redis
      GoalRecord created: {goal_id="g1", status=PLANNING, tenant_id="t1"}
      _goals["g1"] = record

Step 3 — Queue routing and Celery dispatch
      tenant.plan = STARTER → PLAN_QUEUE_MAP["starter"] = "goals.starter"
      CeleryGoalTaskQueue.enqueue():
          run_goal.apply_async(
              args=["g1", "t1", "List all open Jira tickets..."],
              queue="goals.starter"
          )
      return {"goal_id": "g1"} HTTP 202

Step 4 — Worker picks up task
  app/scaling/tasks.py run_goal():
      _SyncGoalLock.acquire("g1", ttl_ms=1_800_000) → SET NX → True (acquired)
      GoalService._make_agent_loop_for_tenant():
          planner = AnthropicProvider(api_key=tenant_llm_key or system_key)
          executor = AnthropicProvider(...)
          verifier = AnthropicProvider(...)  # or cross-model if VERIFIER_API_KEY set
          AgentGraph(
              planner=planner, executor=executor, verifier=verifier,
              mcp_client=MCPClient(registry=MCPRegistry(redis=real_redis)),
              checkpointer=RedisSaver (thread_id="goal-g1"),
              enable_cot=False, enable_reflection=False,
              ... all feature flags per agent config ...
          )

Step 5 — LangGraph _node_initialize (graph.py:422)
      AgentState(goal="List all open...", tenant_ctx=ctx) constructed
      GuardrailChecker.check_goal("List all open Jira tickets...") → no issues (read-only)
      RuntimeProfileBuilder.build_with_trace() → profile{complexity=LOW, risk=LOW}
      agent_state.context["_runtime_profile"] = profile
      SourceInventory.build() lists available Jira tools
      emit SSE: {type: "goal_started", goal: "..."}
      emit SSE: {type: "pattern_assembled", complexity: "low", rag: ["hybrid"]}

Step 6 — LangGraph _node_rag_retrieval (graph.py:555)
      ExecutionMemory.recall_async("List all open Jira tickets", tenant_id="t1", limit=3)
          → [{plan: ["Call jira_search(assignee=currentUser, status=open)"]}]  (past win)
      LongTermMemoryStore.recall_async("List open Jira tickets") → domain notes
      KnowledgeStore.hybrid_search_db(query, collection_ids=[...], top_k=3) → 2 chunks
      rag_context = "[Past winning plans]...\n[Domain knowledge]...\n[KB chunks]..."
      emit SSE: {type: "rag_strategy_selected", strategy: "hybrid"}

Step 7 — LangGraph _node_plan (plan node)
      ContextPipeline assembles 7 context sections:
        system prompt, tenant plan, rag_context, memory, tools schema, CoT reasoning, CoT output
      LLM call (AnthropicProvider.complete(CompletionRequest)):
          model = ModelRouter.model_for("plan") → "claude-3-5-sonnet-20241022"
          response → {"steps": ["Call jira_search with assignee=currentUser() and status=open"]}
      plan = ["Call jira_search with assignee=currentUser() and status=open"]
      emit SSE: {type: "plan_created", steps: [...]}

Step 8 — LangGraph _node_execute (execute node)
      StructuredPlan.wave_execute():
        Step 0: "Call jira_search with assignee=currentUser() and status=open"
          GuardrailChecker.check_step_text("Call jira_search...") → no issues
          PolicyEngine.evaluate("jira_search") → PolicyResult.ALLOW
          MCPClient.list_tools(tenant_ctx="t1") → discovers: jira_search, jira_create, jira_update
          ToolPromptBuilder injects tool JSON schemas into executor prompt
          Executor LLM call → tool_call: {tool: "jira_search", args: {assignee: "currentUser()", status: "Open"}}
          extract_tool_call() parses response
          classify_tool_risk("jira_search") → LOW (read-only)
          HITL not triggered (not high-risk, not supervised mode)
          CostController.check_and_record(goal_id="g1", cost_usd=0.001) → True (within budget)
          MCPClient.call_tool("jira_search", {"assignee": "currentUser()", "status": "Open"}, tenant_ctx)
              → assert_public_url(cfg.url) → no SSRF
              → HTTP POST https://jira.company.com/mcp/tools/jira_search
              → result: [{"key": "PROJ-123", "summary": "Fix login bug"}, ...]
          RollbackEngine.register(tool_name="jira_search", inverse=None)  # read-only: no inverse
          StepResult(status=COMPLETE, output="PROJ-123: Fix login bug, PROJ-124: ...")
          emit SSE: {type: "step_complete", step: 0, output: "..."}

Step 9 — LangGraph _node_verify (verify node)
      _build_verifier_summary(steps) → "MOST RECENT STEPS:\n- Call jira_search: PROJ-123..."
      Verifier LLM call (AnthropicProvider, cross-model if configured):
          → {"success": true, "reason": "Jira tickets successfully retrieved and listed", "retry": true}
      agent_state.verification_success = True
      CalibrationStore.record_verdict(goal_id="g1", verdict="success")
      ExecutionMemory.record_async(goal_id, tenant_id, goal_text, plan, result) → persisted to DB
      LongTermMemoryStore.extract_from_goal_async() → stores "How to list Jira tickets" pattern
      RuntimeScorecard.score(9 dimensions): completeness=0.9, correctness=0.9, ...
      CitedAnswerSynthesizer.synthesize() → cited_answer with Jira ticket links
      emit SSE: {type: "eval_score_recorded", score: 0.9, dimensions: {...}}

Step 10 — Routing and completion
      _route() → agent_state.verification_success = True → return "complete" → END
      AgentState(status=COMPLETE, cited_answer="Open tickets: PROJ-123 Fix login bug...")
      Celery worker publishes to Redis: goal_events:t1:g1 → {type: "worker_complete", ...}
      GoalService._subscribe_celery_goal_events() puts event on GoalRecord.subscribers queues
      Frontend useGoalStream() receives step_complete + eval_score_recorded + goal_complete
      HTTP GET /goals/g1 → {status: "complete", result: "Open tickets: PROJ-123..."}
      _SyncGoalLock.release("g1")
```

---

### Flow 2: "Analyze all PRs in this GitHub repo and produce a quality report" (Expert RAPTOR)

Multi-source RAG, goal tree decomposition, longer iteration, cited answer synthesis.

```
Step 1 — HTTP ingress
  POST /goals
  {"goal": "Analyze all PRs in repo 'acme/api' and produce a code quality report",
   "agent_id": "code-review-agent",
   "execution_context": {"github_repo": "acme/api"}}
  → TenantContext(plan=PROFESSIONAL)
  → queue = "goals.professional" (PLAN_QUEUE_MAP["professional"])

Step 2 — Goal submission
  GoalService.submit_goal():
      DedupCache: not duplicate → proceed
      Concurrent bulkhead: PROFESSIONAL allows higher concurrency
      GoalRecord(goal_id="g2", execution_context={github_repo: "acme/api"})

Step 3 — Celery dispatch
  run_goal.apply_async(queue="goals.professional", ...)

Step 4 — Worker task begins
  AgentGraph built with:
      enable_goal_tree=True, goal_tree_threshold=4
      enable_cot=True  (PROFESSIONAL plan enables CoT)
      enable_self_refine=True
      mcp_client with GitHub connector + Knowledge connector

Step 5 — _node_initialize
  RuntimeProfileBuilder → {complexity=HIGH, risk=LOW, reasoning="raptor", rag=["vector","web"]}
  IdentityResolver → _identity_scope = "tenant_scoped"
  GovernanceProfileSelector → _governance_bundle = "standard"
  SourceInventory.build() → lists github_list_prs, github_get_diff, knowledge_query tools
  emit SSE: {type: "pattern_assembled", complexity: "high", reasoning: "raptor", rag: ["vector","web"]}
  emit SSE: {type: "runtime_profile_selected", profile_id: "...", patterns: ["raptor"]}

Step 6 — _node_rag_retrieval (comprehensive multi-source)
  ExecutionMemory.recall_async("Analyze PRs") → 2 past PR analysis plans
  ExecutionMemory.recall_failures("Analyze PRs") → 1 failure: "GitHub rate limit hit"
  LongTermMemoryStore.recall_async(query, embedder=VoyageProvider) → 5 domain-memory items
  KnowledgeStore.hybrid_search_db(query, collections=agent_collections, top_k=3)
    → pgvector cosine + Postgres trigram GIN, RRF merges results
  RRF engine hybrid_search() additional pass on 2 collections, mode="hybrid"
  _active_rag_strategy = "raptor" → _node_rag_retrieval dispatches to engine.retrieve()
     with strategy="raptor", provider=planner (LLM-assisted reranking)
  RAGTrace.record_retrieval(strategy="raptor", result_count=8)
  emit SSE: {type: "rag_strategy_selected", strategy: "raptor"}
  emit SSE: {type: "chunking_strategy_selected", strategy: "semantic"}
  rag_context = concatenated multi-source text, 6000 chars

Step 7 — _node_think (CoT enabled)
  Planner LLM call with CHAIN_OF_THOUGHT_SYSTEM prompt:
      "Goal: Analyze all PRs in repo 'acme/api'..."
      → cot_reasoning: "I need to: list PRs, fetch diffs, analyze quality metrics,
         check review coverage, aggregate scores, generate report..."
  GraphState["cot_reasoning"] = "I need to: list PRs..."
  emit SSE: implicit (CoT feeds into plan)

Step 8 — _node_plan
  ContextPipeline assembles: system + CoT output + RAG context + memory + tools
  Planner LLM: STRUCTURED_PLANNER_SYSTEM → structured plan with 6 steps
  plan.length (6) >= goal_tree_threshold (4)
    → GoalTreePlanner.decompose() → 4 sub-goals:
        sg1: "List and fetch all PR metadata"
        sg2: "Analyze code quality for each PR"
        sg3: "Check review coverage and test coverage"
        sg4: "Generate quality report with citations"
  EpisodicMemory consulted for similar past analysis patterns
  ProceduralMemory.recall() → "PR analysis standard procedure" skill
  emit SSE: {type: "plan_created", steps: [...], sub_goals: [sg1, sg2, sg3, sg4]}

Step 9 — _node_execute (wave execution for each sub-goal)
  Sub-goal sg1: github_list_prs(repo="acme/api", state="open", per_page=100) → 47 PRs
  Sub-goal sg2: [wave] 47 × github_get_pr_diff() → collected diffs
    CostController.check_and_record() called for each LLM analysis call
    classify_tool_risk("github_get_pr_diff") → LOW
    RollbackEngine: no write actions registered
  Sub-goal sg3: Executor analyzes review comments, CI check results
  Sub-goal sg4: CitedAnswerSynthesizer.synthesize()
    → cited_answer with PR links, quality metrics, code smell counts
  emit SSE: step_complete for each wave
  emit SSE: {type: "step_complete", step: 5, output: "Quality report: 47 PRs analyzed..."}

Step 10 — _node_refine (self_refine enabled)
  SelfRefinePattern: executor reviews report output against SELF_REFINE_SYSTEM checklist
  Response: "NO_CHANGES_NEEDED" → refine skipped (0 changes)

Step 10 — _node_verify
  Verifier LLM: {success: true, reason: "Comprehensive quality report generated for 47 PRs"}
  RuntimeScorecard: completeness=0.93, correctness=0.88, helpfulness=0.91
  EvalRunner.eval() → multi-dimension scoring against eval suite
  SelfOptimizerV2.record_outcome(arm="raptor_rag", score=0.91) → A/B arm update
  PromptOptimizer.record_result(variant_id, eval_score=0.91) → persisted to DB
  LongTermMemoryStore.extract_from_goal_async() → stores "PR analysis with RAPTOR" skill
  ProceduralMemory.learn(state, success=True) → updates procedure
  EpisodicMemory.record(quality_score=0.91) → stored for future recall
  emit SSE: {type: "eval_score_recorded", score: 0.91}

  _route() → "complete" → AgentState(status=COMPLETE, cited_answer=report)
  Celery → Redis → SSE → frontend renders Markdown report with 47 PR citations
```

---

### Flow 3: "Triage PagerDuty alerts and create Jira tickets" (Supervisor + HITL)

Destructive actions require human approval; demonstrates the full governance path.

```
Step 1 — HTTP ingress
  POST /goals
  {"goal": "Triage all critical PagerDuty alerts and create Jira P0 tickets",
   "agent_id": "ops-triage-agent",
   "execution_context": {"autonomy_mode": "supervised"}}
  → TenantContext(plan=ENTERPRISE)
  → queue = "goals.enterprise" (PLAN_QUEUE_MAP["enterprise"])

Step 2 — Goal submission, GoalRecord created
  GoalService: daily limit, bulkhead checks pass
  GoalRecord(goal_id="g3", execution_context={autonomy_mode: "supervised"})

Step 3 — Celery dispatch to enterprise queue
  run_goal.apply_async(queue="goals.enterprise")

Step 4 — Worker
  AgentGraph built with:
      autonomy_mode="supervised"
      hitl_gateway=HITLGateway
      policy_engine=PolicyEngine (loaded from DB: "jira_create" → REQUIRE_APPROVAL)
      rollback_engine=RollbackEngine
      enable_reflection=True

Step 5 — _node_initialize
  GuardrailChecker.check_goal("Triage PagerDuty...") → flags "create" operation → note added
  RuntimeProfileBuilder → {complexity=MEDIUM, risk=HIGH}
  GovernanceProfileSelector → _governance_bundle = "strict"
  emit SSE: {type: "pattern_assembled", risk: "high", safety: ["strict_governance"]}

Step 6 — _node_rag_retrieval
  ExecutionMemory.recall_async("PagerDuty triage") → past triage plans
  KB search: runbook articles, on-call procedures

Step 7 — _node_plan
  LLM → 4-step plan:
    1. pagerduty_list_incidents(severity="critical", status="triggered")
    2. Analyze and categorize incidents
    3. jira_create_issue for each P0 incident
    4. pagerduty_acknowledge incidents

Step 8 — _node_execute: step 1
  pagerduty_list_incidents() → 12 critical alerts
  classify_tool_risk("pagerduty_list_incidents") → LOW
  PolicyEngine.evaluate("pagerduty_list_incidents") → ALLOW
  MCPClient.call_tool("pagerduty_list_incidents") → 12 incidents returned
  StepResult(status=COMPLETE) → emit SSE: step_complete

Step 9 — _node_execute: step 2
  Executor LLM analyzes → 3 P0, 9 P1
  StepResult(status=COMPLETE)

Step 10a — _node_execute: step 3 (HITL triggered)
  "jira_create_issue" → PolicyEngine.evaluate() → REQUIRE_APPROVAL
  HITLGateway.request_approval(
      goal_id="g3",
      request_id="hitl-001",
      step="Create 3 Jira P0 tickets: DB latency, API errors, Auth service down",
      tool="jira_create_issue",
      risk_level=HIGH
  )
  agent_state.status = GoalStatus.WAITING_HUMAN
  emit SSE: {type: "hitl_approval_requested", request_id: "hitl-001", step: "..."}
  LangGraph checkpoint written; execution pauses at verify/route boundary

Step 10b — Operator approves via UI
  GET /governance/hitl/pending → [{request_id: "hitl-001", step: "Create 3 Jira P0 tickets"}]
  POST /governance/hitl/hitl-001/approve
    {"note": "Proceed — add label INCIDENT-2024 to all tickets"}
  HITLGateway stores approval; Redis pub/sub publishes to hitl_rejected:* channel

Step 10c — _route() clears HITL
  autonomy_mode="supervised" → hitl_gateway.list_pending("g3") → empty (approved)
  → return "replan" (include operator note in next plan context)

Step 11 — _node_plan (replan with note)
  GoalRecord.hitl_rejection_note = "" (approved, not rejected)
  Planner incorporates HITL approval note: "add label INCIDENT-2024"
  Revised plan step: jira_create_issue({summary: "DB latency", labels: ["INCIDENT-2024"]})

Step 12 — _node_execute: step 3 (approved)
  PolicyEngine.evaluate("jira_create_issue") → ALLOW (approval recorded)
  MCPClient.call_tool("jira_create_issue", {summary: "DB latency...", labels: [...]})
  → result: {issue_key: "OPS-789"}
  RollbackEngine.register(tool="jira_create_issue",
      inverse=jira_delete_issue, args={issue_key: "OPS-789"})
  [repeat for 2 more P0 incidents]
  AuditLog.append(AuditEvent(action="jira_create_issue", goal_id="g3", tenant_id="t1"))
  emit SSE: step_complete

Step 13 — _node_execute: step 4
  pagerduty_acknowledge(incident_ids=[...]) → all 12 acknowledged
  classify_tool_risk("pagerduty_acknowledge") → MEDIUM (state change)
  GuardrailEnforcer profile=strict → allows (acknowledge ≠ delete)
  RollbackEngine.register(inverse=pagerduty_unacknowledge)

Step 14 — _node_verify
  Verifier: {success: true, reason: "3 P0 tickets created (INCIDENT-2024), 12 alerts acknowledged"}
  RuntimeScorecard: completeness=0.95, correctness=0.92
  ComplianceController records action in SOC2 audit trail
  LTM.extract_from_goal_async() → stores "PagerDuty→Jira triage workflow"

Step 15 — _route() → "complete"
  AgentState(status=COMPLETE)
  Celery → Redis pub/sub: goal_events:t1:g3 → worker_complete
  GoalService bridge → SSE queues → frontend goal_complete
  GET /goals/g3/result → {status: "complete", result: "3 tickets: OPS-789, OPS-790, OPS-791"}
```

---

## 6. Full Navigation Links

| Document | Contents |
|---|---|
| `00-index.md` **(this file)** | OS mental model · Repository map · Concept-to-code table · 4 audience reading paths · 3 end-to-end flows |
| `01-platform-lifecycle-and-core-workflows.md` | App startup → lifespan wiring → auth → goal submission → Celery → AgentGraph nodes (step-by-step) → routing → MCP → memory writes → SSE observability timeline → frontend update path |

> Additional documentation lives in:
> - `docs/superpowers/specs/` — phased roadmap and component specifications
> - `docs/superpowers/plans/` — implementation plans for each feature phase
> - `RUNBOOK.md` — production operations playbook
> - `CONTRIBUTING.md` — contribution guidelines
> - `CHANGELOG.md` — version history
