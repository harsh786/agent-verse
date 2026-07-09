# AgentVerse — Platform Lifecycle and Core Workflows

> **Scope:** This document covers every hop from `uvicorn create_app()` through
> Celery worker completion: service wiring, authentication, goal submission,
> distributed execution, the full LangGraph node cycle, and the SSE/WebSocket
> fan-out back to the browser. Exact file references are given for every
> mechanism described.
>
> **Source files cited:**
> - `agent-verse-backend/app/main.py` (1 697 lines)
> - `agent-verse-backend/app/agent/graph.py` (4 534 lines)
> - `agent-verse-backend/app/services/goal_service.py` (3 085 lines)
> - `agent-verse-backend/app/scaling/tasks.py` (2 918 lines)
> - `agent-verse-backend/app/scaling/celery_app.py` (238 lines)
> - `agent-verse-backend/app/tenancy/middleware.py` (357 lines)

---

## Table of Contents

1. [Two-Phase Service Wiring](#1-two-phase-service-wiring)
2. [Tenant Resolution and Authentication](#2-tenant-resolution-and-authentication)
3. [Goal Submission Lifecycle](#3-goal-submission-lifecycle)
4. [Worker Execution](#4-worker-execution)
5. [AgentGraph Node-by-Node Execution](#5-agentgraph-node-by-node-execution)
6. [Routing Logic — `_route()`](#6-routing-logic--_route)
7. [MCP Tool Discovery and Execution](#7-mcp-tool-discovery-and-execution)
8. [Memory Write Flows After Completion](#8-memory-write-flows-after-completion)
9. [SSE Event Timeline](#9-sse-event-timeline)
10. [Frontend Update Path](#10-frontend-update-path)
11. [Celery Scaling and Multi-Tenancy](#11-celery-scaling-and-multi-tenancy)

---

## 1. Two-Phase Service Wiring

### Why two phases?

`create_app()` in `app/main.py:447` is the single factory used by both the
production ASGI server (uvicorn) and the test suite. Tests construct the app
without real database connections or Redis, so they get deterministic in-memory
services. Production gets DB/Redis-backed equivalents that are swapped in during
`lifespan()`. A single flag — `manage_pools: bool = False` — controls which path
runs.

```
manage_pools=False  →  Phase 1 only  →  in-memory stubs  (test path)
manage_pools=True   →  Phase 1 then Phase 2  →  real backends  (production path)
```

When uvicorn launches without explicit arguments, the env var `MANAGE_POOLS=1`
triggers the production path automatically (`app/main.py:461`).

---

### Phase 1: `create_app()` constructs in-memory services

Every service is instantiated unconditionally as an in-memory object the moment
`create_app()` runs. No network connections are attempted. Services that depend
on a DB or Redis receive `None` or a `_FakeRedis` stub (`app/main.py:511`).

The complete `app.state.*` registry after Phase 1:

| `app.state` key | Class | What it does |
|---|---|---|
| `tenant_service` | `TenantService()` | API-key auth, tenant CRUD |
| `goal_service` | `GoalService(audit_log, hitl, task_queue)` | Goal lifecycle + SSE fan-out |
| `mcp_registry` | `MCPRegistry(redis=_FakeRedis)` | Per-tenant connector registry |
| `mcp_client` | `MCPClient(mcp_registry)` | HTTP tools/list + tool execution |
| `oauth_manager` | `OAuthFlowManager()` | PKCE OAuth2 flows |
| `agent_store` | `AgentStore()` | Per-tenant agent config store |
| `meta_agent` | `MetaAgentPlanner(provider)` | NL → agent config generator |
| `hitl_gateway` | `HITLGateway()` | Human-in-the-loop approval queue |
| `audit_log` | `AuditLog()` | Append-only event trail |
| `cost_controller` | `CostController()` | Per-goal / per-tenant budgets |
| `policy_engine` | `PolicyEngine()` | Tool policy evaluation engine |
| `schedule_store` | `ScheduleStore()` | Trigger schedule CRUD |
| `nl_scheduler` | `NLScheduler(provider)` | NL → `TriggerSpec` |
| `knowledge_store` | `KnowledgeStore()` | Hybrid pgvector + trigram search |
| `semantic_cache` | `SemanticCache()` | LLM call deduplication by embedding |
| `long_term_memory` | `LongTermMemoryStore()` | Cross-session domain learnings |
| `eval_runner` | `EvalRunner()` | 5-dimension goal scoring |
| `eval_suite_runner` | `EvalSuiteRunner()` | Multi-suite evaluation runner |
| `compliance_controller` | `ComplianceController()` | GDPR/SOC2/PCI-DSS |
| `simulation_runner` | `SimulationRunner()` | Mock-tool sandbox |
| `red_team_runner` | `RedTeamRunner()` | Adversarial goal injection tests |
| `marketplace` | `Marketplace(agent_store)` | Template gallery (v1) |
| `marketplace_v2` | `MarketplaceV2(db_factory=None)` | Template gallery + seed (upgraded) |
| `self_optimizer` | `SelfOptimizer()` | Failed-eval improvement suggestion |
| `self_optimizer_v2` | `SelfOptimizerV2(redis=_FakeRedis, ...)` | Bayesian A/B + arm config |
| `compliance_checker` | `ComplianceChecker(db_factory=None)` | Compliance v2 (no hardcoded booleans) |
| `notification_service` | `NotificationService()` | Persistent notification channels |
| `exec_memory` | `ExecutionMemory()` | Per-goal episodic memory (H-3) |
| `cost_tracker` | `CostTracker(redis=_FakeRedis)` | Per-role cost ledger |
| `agent_identity_svc` | `AgentIdentityService(db=None, ...)` | Agent OAuth2 identity (Entra-style) |
| `guardrail_engine_v2` | `GuardrailEngineV2()` | Injection/grounding guardrail engine |
| `embedder` | `VoyageProvider` / `OpenAICompatibleProvider` / `LocalEmbedProvider` / `None` | Resolved from env at startup |
| `model_router` | `ModelRouter(provider_name)` | Task-type → model selection |

Provider resolution priority (`app/main.py:475`):

```
1. declarative ProviderRegistry (LLM_PROVIDERS JSON env var override)
2. ANTHROPIC_API_KEY → AnthropicProvider
3. OPENAI_API_KEY → OpenAICompatibleProvider
4. GOOGLE_API_KEY → GeminiProvider
5. GROQ_API_KEY / OLLAMA_BASE_URL → OpenAICompatibleProvider variants
6. FakeProvider (dev/test only; fatal in production)
```

---

### Phase 2: `lifespan()` replaces with DB/Redis-backed services

Phase 2 runs only when `manage_pools=True`. The flow inside `lifespan()`
(`app/main.py:639`) is:

1. `ConnectionPools(settings).startup()` — starts asyncpg pool + aioredis pool.
2. `real_redis` is extracted from the pool and distributed to every Redis-dependent
   service.
3. The in-memory `MCPRegistry(_FakeRedis)` is replaced by
   `MCPRegistry(redis=real_redis)` (`app/main.py:650`).
4. `RedisCostController(redis=real_redis)` is created for cross-replica budget
   accuracy and stored at `app.state.redis_cost_controller`.
5. LangGraph checkpointer is wired in priority order:
   - `AsyncRedisSaver.from_conn_string(redis_url)` (preferred)
   - `RedisSaver.from_conn_string(redis_url)` (sync fallback)
   - `MemorySaver()` (no Redis — emits a warning, state lost on restart)
6. `get_session_factory()` builds the asyncpg session factory.
7. DB-backed replacements are constructed and synced:
   ```python
   _tenant_svc_with_db = TenantService(db_session_factory=db_factory)
   _goal_svc_with_db   = GoalService(..., db_session_factory=db_factory, event_store=event_store)
   _agent_store_with_db = AgentStore(db_session_factory=db_factory)
   await _tenant_svc_with_db.sync_from_db()
   await _goal_svc_with_db.sync_from_db()
   await _agent_store_with_db.sync_from_db()
   ```
8. These replace the in-memory versions on `app.state`.
9. Additional DB wiring: `AuditLog`, `ScheduleStore`, `KnowledgeStore`,
   `CollaborationStore`, `WorkflowStore`, `TemplateStore`, `ExecutionMemory`,
   `LongTermMemoryStore`, `ToolReliabilityStore`, `ReflexionWirer`,
   `VerifierCalibrationStore`, and `MFAStore` all receive `db_factory`.
10. Built-in MCP servers are registered per active tenant
    (`app/main.py:783`).

**Key insight for debugging:** A "works in tests, fails in prod" behaviour is
almost always caused by a service reading from `app.state` that has the in-memory
version in tests (Phase 1) but the DB-backed version in prod (Phase 2). The
canonical example is `GoalService._goals` vs. `GoalService.sync_from_db()`.

---

## 2. Tenant Resolution and Authentication

Every request (except bypass paths) flows through `TenantMiddleware` in
`app/tenancy/middleware.py`. The middleware is a `BaseHTTPMiddleware` registered
during `create_app()`.

### Bypass paths

The following path prefixes skip authentication entirely (`middleware.py:71`):

```
/health   /metrics   /status   /docs   /redoc   /openapi.json
/tenants/signup   /auth/login   /auth/callback   /auth/config   /auth/token
/integrations/   /billing/webhook
```

CORS preflight (`OPTIONS` with `origin` and `access-control-request-method`
headers) also bypasses auth.

### Key extraction

`_extract_key(request)` at `middleware.py:90`:

```python
auth = request.headers.get("Authorization", "")
if auth.startswith("Bearer "):
    return auth[7:].strip() or None
return request.headers.get("X-API-Key") or None
```

Both `Authorization: Bearer <key>` and `X-API-Key: <key>` are accepted
interchangeably.

### Authentication paths

**Path A — Keycloak SSO JWT** (`middleware.py:105`, `_try_resolve_sso`):

1. Enabled when `KEYCLOAK_SERVER_URL` (or equivalent) env var is set.
2. `Bearer` token must look like a JWT: `token.count(".") >= 2`.
3. `resolve_tenant_from_jwt(token, tenant_service)` validates the JWT signature
   against Keycloak's JWKS endpoint and maps `sub` or a custom claim to a
   `TenantContext`.
4. Failure falls through to the API key path silently.

**Path B — API key** (primary path):

1. `key_resolver(key)` is called — in production this hits the DB; in tests it is
   a duck-typed fake.
2. Returns `TenantContext` or `None`.
3. `None` → 401 JSON response with code `AUTHENTICATION_ERROR`.

### TenantContext

```python
@dataclass
class TenantContext:
    tenant_id: str
    plan:       PlanTier          # FREE | STARTER | PROFESSIONAL | ENTERPRISE
    api_key_id: str
    roles:      tuple[str, ...]   # ("admin",) | ("user",) | ...
```

`PlanTier` maps directly to the `PLAN_QUEUE_MAP` in Celery:

```python
PLAN_QUEUE_MAP = {
    "free":         "goals.free",
    "starter":      "goals.starter",
    "professional": "goals.professional",
    "enterprise":   "goals.enterprise",
}
```

### Rate limiting

After successful authentication, `TenantMiddleware` calls
`_check_rate_limit_with_fallback(tenant_id, redis, rpm_limit)`:

1. If Redis is available: `SlidingWindowRateLimiter.check_and_record("api", limit=rpm_limit)`.
2. If Redis is unavailable: in-process sliding-window counter (`_fallback_counters`)
   with a hard cap of `min(plan_limit, 120)` RPM.
3. `429 Too Many Requests` when the limit is exceeded.
4. Response always includes `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and
   `X-RateLimit-Reset` headers.

`PLAN_LIMITS` defines per-plan RPM ceilings (resolved from `app.state` at request
time so limits can be updated without restart).

### MFA validation

When the tenant has MFA configured (`MFAStore.is_enabled(tenant_id)`), an
`X-MFA-Token` header is checked before elevated operations (governed by
`ScopeEnforcementMiddleware`). MFA secrets are stored in the DB via `MFAStore`
(wired with `db_factory` during Phase 2).

---

## 3. Goal Submission Lifecycle

### Request flow: `POST /goals → Celery task`

```
Client → TenantMiddleware (auth + rate limit)
       → goals_router (app/api/goals.py)
       → GoalService.submit_goal()
       → CeleryGoalTaskQueue.enqueue()
       → run_goal.apply_async(queue=PLAN_QUEUE_MAP[plan])
```

### Step-by-step inside `GoalService.submit_goal()`

**Step 1 — Deduplication check (Redis hash)**

`submit_goal()` computes `SHA-256(tenant_id + goal_text + agent_id)` and checks
`SETNX` in Redis with a short TTL (typically 10–30 seconds). If the key already
exists, a `409 Conflict` is returned. This prevents double-submits from retry
storms.

**Step 2 — Daily goal limit check**

`_check_daily_goal_limit_redis()` at `goal_service.py:545` uses an atomic Redis
counter keyed `daily_goals:{tenant_id}:{date}` with a 24-hour TTL. Exceeding the
plan's daily limit raises `PlanLimitExceededError` (HTTP 429).

**Step 3 — Concurrent goal limit (bulkhead)**

`BulkheadRegistry.check_and_increment(tenant_id)` enforces per-plan in-flight
goal caps. The counter is stored in Redis so it works across replicas.

**Step 4 — Dry-run check**

When `dry_run=True`, the goal record is created and immediately marked
`COMPLETE` with zero iterations. The Celery task still runs but returns early
after acquiring the lock (see §4).

**Step 5 — Agent auto-routing when `agent_id=None`**

`AgentRouter.route(goal_text, tenant_ctx)` scans all agents registered for the
tenant, scores each by embedding similarity against their description + historical
performance (DB-backed when `manage_pools=True`), and returns the best
`agent_id`. This makes the system self-routing: submitting a goal without
specifying an agent selects the most capable one automatically.

**Step 6 — `RuntimeProfileBuilder.build_with_trace()` (when `DYNAMIC_ORCHESTRATION=true`)**

When the `DYNAMIC_ORCHESTRATION` env flag is active, the submission path calls
`RuntimeProfileBuilder.build_with_trace(goal, tenant_id, goal_id)` to classify
goal complexity, risk level, required reasoning patterns, and RAG strategy
before the goal even reaches the worker. The resulting `RuntimeProfile` is
serialised into `execution_context["_runtime_profile"]` and passed through to
the Celery task, saving the worker from re-running classification.

**Step 7 — Goal record creation**

```python
record = GoalRecord(
    goal_id=str(uuid.uuid4()),
    goal_text=goal,
    status=GoalStatus.PLANNING,
    tenant_id=tenant_ctx.tenant_id,
    ...
)
self._goals[goal_id] = record
```

**Step 8 — Celery task dispatch**

```python
run_goal.apply_async(
    args=[goal_id, tenant_id, goal_text, ...],
    queue=PLAN_QUEUE_MAP[plan],           # tier-based queue
    task_id=goal_id,
    countdown=0,
    expires=3600,
)
```

The `queue` argument overrides the default `goals.free` route in
`celery_app.py:74` at dispatch time.

**Step 9 — SSE bridge: Celery → Redis pub/sub → in-process SSE queues**

After the Celery worker publishes events to `goal_events:{tenant_id}:{goal_id}`,
the API process bridges them back to waiting SSE subscribers:

```
Worker                  Redis pub/sub             API process
run_goal() → _r.publish("goal_events:{tid}:{gid}", json)
                           ↓
                  _subscribe_celery_goal_events()  (goal_service.py:400)
                           ↓
             record.subscribers[].put_nowait(event)
                           ↓
          GET /goals/{id}/stream  ←  asyncio.Queue drain
```

The subscriber loop at `goal_service.py:405` uses `psubscribe("goal_events:*")`
so a single background task handles all tenants. Stub `GoalRecord` objects are
created for goals the API process has not yet seen (cross-replica case).

---

## 4. Worker Execution

### `run_goal()` Celery task — `app/scaling/tasks.py:447`

```python
@celery_app.task(name="app.scaling.tasks.run_goal", bind=True, max_retries=3)
def run_goal(self, goal_id, tenant_id, goal_text, priority, dry_run,
             agent_id, connector_ids, workflow_mode, goal_template, plan):
```

Worker configuration inherited from `celery_app.conf`:
- `task_acks_late=True` — task not acknowledged until complete (prevents data loss on crash).
- `task_reject_on_worker_lost=True` — requeued if worker dies mid-task.
- `worker_prefetch_multiplier=1` — one task at a time per worker thread.
- `worker_max_tasks_per_child=100` — recycle process after 100 tasks (memory leak prevention).
- `worker_max_memory_per_child=500_000` KB (500 MB) — RSS kill switch.

### Emergency stop check

Before any lock or DB operation, the worker reads `emergency_stop:{tenant_id}`
from Redis (`tasks.py:509`). If set by an operator, the task returns immediately
with `{"status": "blocked", "reason": "Emergency stop active"}`.

### `_SyncGoalLock` — exactly-once execution

`tasks.py:115` implements a synchronous Redis distributed lock using `SET NX PX`:

```python
def acquire(self, goal_id: str, ttl_ms: int = 1_800_000) -> bool:
    key = f"goal_lock:{goal_id}"
    result = self._redis.set(key, self._value, px=ttl_ms, nx=True)
    return bool(result)
```

The TTL of 30 minutes prevents a dead worker from holding the lock indefinitely.
Release uses an atomic Lua script to prevent another worker from releasing a lock
it doesn't own (`tasks.py:127`):

```lua
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
```

If `acquire()` returns `False`, the worker logs and returns
`{"status": "skipped", "reason": "already_executing"}` without error.

The lock is intentionally **synchronous** (`tasks.py:632`): each `run_goal()`
invocation creates a fresh asyncio event loop via `_run_async()`, so an async
Redis client would be bound to a dead loop on release. The sync client shares the
module-level `_REDIS_POOL` instead.

### Fresh DB session factory

The worker never reuses the parent API process's DB connections (which are bound
to the parent's event loop). Instead, `_make_worker_goal_bridge()` calls
`_make_session_factory()` — bypassing the global cache — to get a fresh asyncpg
pool (`tasks.py:529`).

### LLM provider resolution (worker)

```
1. Tenant-specific key from Redis (llm_config:{tenant_id}) → decrypt via Vault
2. ANTHROPIC_API_KEY env var → AnthropicProvider
3. OPENAI_API_KEY env var → OpenAICompatibleProvider
4. FakeProvider (last resort — logs warning)
```

### `GoalService._make_agent_loop_for_tenant()` — graph construction

Called from the worker (via `goal_bridge._make_agent_loop_for_tenant()`) or
directly from `submit_goal()` in async mode. Assembles `AgentGraph` with:

- Feature flags from `agent_config` + `execution_context["_runtime_profile"]`
- `max_iterations` from agent config (default 15)
- Per-connector `RedisCircuitBreaker` or in-memory `CircuitBreaker`
- `GroundingChecker`, `AnswerSynthesizer`, `ConsensusVerifier`
- LangGraph checkpointer from `app.state` (Redis-backed in production)
- All services from `app.state`: `eval_runner`, `knowledge_store`,
  `long_term_memory`, `exec_memory`, `hitl_gateway`, `policy_engine`, etc.

### Pause/cancel polling

`_run_with_signals()` at `tasks.py:290` wraps `agent_runner.run()` in a
`asyncio.create_task`. Every 5 seconds it checks:
- `is_cancelled_sync(goal_id, redis)` → cancels task, raises `GoalCancelledError`
- `is_paused_sync(goal_id, redis)` → cancels task, polls until resumed, then
  recreates the task (LangGraph resumes from last checkpoint)

### Post-execution cleanup

Regardless of outcome, the worker always:
1. Releases `_SyncGoalLock`
2. Calls `_decrement_after_completion(tenant_id, REDIS_URL)` to decrement the
   per-tenant concurrent-goal counter in Redis
3. Calls `_record_goal_duration_metric(status, started_monotonic, priority)` for
   OpenTelemetry metrics

---

## 5. AgentGraph Node-by-Node Execution

The LangGraph `StateGraph` is compiled in `graph.py:327` with a conditional node
topology. The canonical path without optional reasoning nodes is:

```
START → initialize → rag_retrieval → plan → execute → verify → _route()
                                          ↗
                                  rag_remediate
```

Optional nodes inserted by feature flags:

| Flag | Nodes added | Position |
|---|---|---|
| `enable_cot=True` | `think` | after `rag_retrieval`, before `plan` |
| `enable_tree_of_thoughts=True` | `tree_of_thoughts` | after `think`, before `plan` |
| `enable_self_refine=True` | `refine` | after `execute`, before `verify` |
| `enable_self_consistency=True` | `self_consistency` | after `refine`/`execute`, before `verify` |
| `enable_reflection=True` | `reflect` | after `_route()` → back to `plan` |
| `enable_peer_review=True` | `peer_review` | after `verify`, before `_route()` |
| `enable_supervisor=True` | `supervisor` | conditional |
| `enable_debate=True` | `debate` | conditional |

---

### 5.1 `_node_initialize` (graph.py ~422–553)

1. Constructs or recovers `AgentState` from LangGraph checkpoint state.
2. Emits `{"type": "goal_started"}` SSE event.
3. **Guardrail screening:** `GuardrailChecker.check_goal(goal)` scans for prompt
   injection patterns. If violations found → `agent_state.status = FAILED`,
   emits `goal_rejected`, returns `terminal_reason="guardrail_rejected"`,
   short-circuiting the entire graph.
4. **A/B experiment arm:** `SelfOptimizerV2.get_arm_config(agent_id, goal_id,
   tenant_id)` assigns this run to a Bayesian experiment arm. The arm name is
   stored in `agent_state.context["_experiment_arm"]`.
5. **Runtime profile:** If `_runtime_profile` not already in context (i.e., not
   pre-built by `submit_goal()`), calls `RuntimeProfileBuilder.build_with_trace()`.
   Emits two SSE events via `RuntimeSSEEmitter`: `pattern_assembled` and
   `runtime_profile_selected`.
6. **Identity + governance profiles:** `IdentityResolver.resolve(tenant_ctx)` →
   `agent_state.context["_identity_scope"]`. `GovernanceProfileSelector.select(
   runtime_profile, tenant_ctx)` → `agent_state.context["_governance_bundle"]`.
7. **Source inventory:** `SourceInventory.build(tenant_ctx)` catalogues available
   knowledge sources for planner awareness.
8. Returns `{"agent_state": ..., "iteration": 0, "rag_context": ""}`.

---

### 5.2 `_node_rag_retrieval` (graph.py ~555–800)

1. **Strategy selection:**
   - Checks `agent_state.context["_rag_strategy_override"]` first.
   - Falls back to `runtime_profile.rag_strategy.strategy`.
   - Stores active strategy in `agent_state.context["_active_rag_strategy"]`.
   - `RetrievalPlanner.select_strategy(goal)` applies heuristics (question
     vs. command, domain keywords) to pick from: `direct`, `hybrid`, `flare`,
     `raptor`, `fusion`, `colbert`, `auto`.
2. **SSE events emitted:** `chunking_strategy_selected`, `rag_strategy_selected`,
   `embedding_strategy_selected`.
3. **Episodic memory recall:** `ExecutionMemory.recall(goal, tenant_ctx)` returns
   prior successful plans for similar goals.
4. **Long-term memory recall:** `LongTermMemoryStore.recall(goal, tenant_ctx)`
   returns domain-level learnings from past sessions.
5. **KnowledgeStore hybrid search:** `KnowledgeStore.hybrid_search(query,
   tenant_ctx)` combines pgvector cosine similarity + full-text search + trigram
   matching + BM25 Reciprocal Rank Fusion (RRF). Chunked with pgvector index on
   embedding column.
6. **Advanced retrieval engine:** When `_active_rag_strategy` is set (e.g.
   `flare`, `raptor`), `engine.retrieve(query, strategy=strategy, tenant_ctx)`
   dispatches to the appropriate advanced retrieval implementation.
7. **Web fallback:** When the knowledge base is empty and `_web_search_tool` is
   wired, a web search is executed to bootstrap context.
8. All retrieved context is concatenated into `rag_context` (the LangGraph state
   key used by all downstream nodes).

---

### 5.3 `_node_plan` (graph.py ~1100–1450)

1. **ContextPipeline (7 steps):** Builds the planner prompt by assembling:
   `rag_context`, episodic memory, procedural memory, reflexion lessons,
   source inventory, governance bundle, and system prompt.
2. **Reflexion injection:** `agent_state.context["_reflexion_lessons"]` contains
   lessons stored from prior failed runs. The `ReflexionWirer` makes these
   available so the planner avoids repeating the same mistakes.
3. **OutputContractBuilder:** Auto-detects expected output format from the goal
   text (code, JSON, prose, table) and embeds a schema constraint in the prompt.
4. **Model selection:** `ModelOrchestratorAdapter.update_from_profile(profile,
   budget_ratio)` selects the planner model:
   - `budget_ratio > 75%` → downgrade to medium tier
   - `budget_ratio > 90%` → downgrade to low tier
   Cost auto-downgrade: economy model forced for verification when near budget.
5. **Prompt A/B:** `PromptVariantSelector` picks from registered prompt variants
   for this agent. The selected `variant_id` is stored for later eval attribution.
6. **LLM call** with structured output schema: `CompletionRequest` sent to the
   planner provider with a `response_schema` when `supports_structured_output()`.
   Circuit-breaker-wrapped: `PermissionError("Planning unavailable: ...")` if
   the breaker is open.
7. **SSE:** `model_route_selected` emitted with provider name and model ID.
8. Plan parsed as `{"steps": [...]}`. Each step is a natural-language instruction.

---

### 5.4 `_node_execute` (graph.py ~1450–3060)

The most complex node. Executes each planned step, respecting sequential vs.
parallel wave ordering.

**Parallel wave detection:** Steps prefixed with `[PARALLEL]` or grouped by the
planner into a `parallel_wave` are launched as concurrent `asyncio.create_task`
calls, joined with `asyncio.gather`. Parallel step mutations to
`agent_state.total_cost_usd` are protected by `self._state_lock`.

**Per-step execution (`_execute_step()`):**

1. `ActionSafetyProfileSelector.select(step_text)` — picks the HITL/policy bundle.
2. **Deduplication:** Step hash checked against `agent_state.context["_completed_steps"]`
   to prevent replaying already-executed steps after a replan.
3. **Step-level RAG:** Embedding computed for the step text; micro-recall from
   KnowledgeStore for step-specific context.
4. `GuardrailChecker.check_goal(step)` — injection scan on the step text itself.
5. `GuardrailEnforcer.check_tool_args(tool_name, args, profile)` — argument-level
   policy enforcement based on the action safety profile.
6. `PolicyEngine.evaluate(tool_name, tenant_ctx)` — returns `ALLOW`, `DENY`, or
   `REQUIRE_APPROVAL`. `DENY` → step skipped with error. `REQUIRE_APPROVAL` → HITL.
7. **HITL gate:** `HITLGateway.request_approval(goal_id, action, risk_level,
   tenant_ctx)` — goal pauses at `WAITING_HUMAN` status. Approval unblocks;
   rejection feeds `hitl_rejection_note` back to the planner.
8. `SearchDirectiveParser` — recognises inline search directives:
   `[SEARCH:kb:query]`, `[SEARCH:web:query]`, `[SEARCH:graph:query]`,
   `[SEARCH:memory:query]`. Dispatches to the correct backend and injects results
   into the step context.
9. `ToolPromptBuilder.build(tools, context)` — formats tool descriptions from the
   MCP registry into the executor's system prompt.
10. **LLM streaming call** to the executor provider. Token chunks emitted as
    `token_chunk` SSE events.
11. **Tool call parsing:** `extract_tool_calls(response)` finds `<tool_call>` or
    JSON tool-call syntax in the LLM output.
12. `MCPClient.call_tool(tool_name, args, tenant_ctx)` — HTTP dispatch to the MCP
    server. Wrapped in the connector's `CircuitBreaker`. SSRF guard filters
    private-range IPs. WebSocket transport path used for streaming-capable servers.
13. `RollbackEngine.register(tool_name, args, result)` — records compensating
    action via `tool_inverses.py` for LIFO rollback on failure.
14. **Grounding scan:** `GroundingChecker.check(output, sources)` — detects
    hallucinated citations.
15. **Indirect injection scan:** `IndirectInjectionScanner.scan(output)` — checks
    tool outputs for embedded prompt injection.
16. `persist_tool_outcome()` — fire-and-forget DB write of tool call result.
17. SSE: `step_complete`, `guardrail_profile_selected`, `token_chunk`.

---

### 5.5 `_node_verify` (graph.py ~2700–3659)

1. **Latency computation:** `_latency_ms = time.monotonic() * 1000 - _goal_start_ms`
   stored in `agent_state.context["_latency_ms"]` for `RuntimeScorecard` (N3 fix).
2. **Step summary:** All step outputs formatted into a summary string for the
   verifier prompt.
3. **LLM response cache:** Semantic cache checked before making the verifier call.
4. **Verifier LLM call:** `CompletionRequest` to `self._verifier` (cross-model
   when `VERIFIER_API_KEY` or `ANTHROPIC_API_KEY+OPENAI_API_KEY` both set). Wrapped
   in circuit breaker.
5. **Verifier response parsing:** `parse_verifier_verdict(resp.content)` returns
   `{"success": bool, "reason": str, "retry": bool}`. Falls back from JSON to
   legacy `SUCCESS:`/`RETRY:`/`FAIL:` text formats.
6. **3-way consensus:** When `_consensus_verifier` is wired and primary verdict
   is failure, `ConsensusVerifier.verify()` polls a second model. Disagreement
   triggers a HITL request.
7. `VerifierCalibrationStore.record_verdict(...)` — tracks verifier accuracy
   over time (Phase 3 Track E).

**On success:**

- `ExecutionMemory.record()` (sync in-memory) + `record_async()` (async DB) —
  winning plan stored for future episodic recall.
- `LongTermMemoryStore.extract_from_goal()` (sync) + `extract_from_goal_async()`
  (async DB) — domain learnings extracted.
- `EvalRunner.score_and_persist(agent_state, tenant_ctx, provider, db)` —
  7-dimension goal scoring, persisted to `eval_scores` table.
- `RuntimeScorecard.score(state, profile)` — 9-dimension runtime scoring (only
  when `DYNAMIC_ORCHESTRATION=true`).
- `OrchestrationPersistence.persist_scorecard()` → `eval_scorecards` table.
- `RegressionGate.maybe_create_regression()` — if score below threshold,
  catalogues goal as regression test case.
- `SelfImprovementEngine.decide_actions(scorecard, profile, state)` — dispatches
  improvement actions:
  - `UPDATE_PROMPT_VARIANT` → `PromptOptimizer.record_result(variant_id, score)`
  - `SWITCH_MODEL` / `UPDATE_MODEL_ROUTING` → patches agent config in `AgentStore`
  - `BLACKLIST_TOOL_PATTERN` → `ToolReliabilityStore.record(tool, success=False)`
- `ABTestingEngine.record_result_async()` — registers outcome against experiment arm.
- `SelfOptimizerV2.on_goal_completed()` — Bayesian arm update.
- `CitedAnswerSynthesizer` — builds citation-annotated final answer.
- SSE: `eval_score_recorded`, `self_improvement_suggested`.

**On failure:**

- `ReflexionWirer.maybe_store_async()` — extracts lesson from failure and stores
  in `reflexion_lessons` table for next replan.
- `RollbackEngine.rollback_all_async()` — executes compensating actions in LIFO
  order (only when `retry=False`, i.e. permanent failure).
- `OrchestrationPersistence.persist_reflexion_lesson()` — persists lesson.
- SSE: `verification_done` with `success=false`.

---

## 6. Routing Logic — `_route()`

`_route(state)` is the conditional edge function called after `verify` (or
`peer_review` when enabled). It examines `agent_state` and returns a string key
that LangGraph maps to the next node.

```python
routing_map = {
    "complete":      END,
    "replan":        "plan",
    "max_iter":      END,
    "waiting_human": END,
    "rag_remediate": "rag_remediate",
    "reflect":       "reflect",   # only when enable_reflection=True
}
```

Decision tree:

```
if terminal_reason == "guardrail_rejected":
    → END  (failed, no routing)

if agent_state.status == WAITING_HUMAN:
    → "waiting_human"  → END

if agent_state.status == COMPLETE (verification_success=True):
    → "complete"  → END

if agent_state.iterations >= max_iterations:
    → "max_iter"  → END  (failed: max iterations exceeded)

if not verification_success:
    if context["verification_retry"] == False:
        → trigger rollback, mark FAILED
        → "max_iter"  → END  (permanently blocked)
    if context.get("_rag_gap_detected"):
        → "rag_remediate"  (re-retrieve then re-plan)
    if enable_reflection and iterations < max_iterations // 2:
        → "reflect"  (reflexion lesson generation then re-plan)
    else:
        → "replan"  → "plan"  (standard replan)
```

`rag_remediate` is a lightweight re-retrieval node: it calls `_node_rag_retrieval`
with expanded query terms, then edges back to `plan` with enriched context.

The `reflect` node generates a structured lesson from the failure, stores it via
`ReflexionWirer`, and edges to `plan` with the lesson prepended to the prompt.

---

## 7. MCP Tool Discovery and Execution

### MCPRegistry

`app/mcp/registry.py` maintains a per-tenant map of connector configurations.
In production the registry is Redis-backed (`MCPRegistry(redis=real_redis)`),
making it consistent across replicas. The in-memory variant is used in tests.

320+ built-in server files are registered at worker startup:

```python
for _bc in get_builtin_server_configs():
    if _bc.get("handler") is not None:
        MCPRegistry.register_builtin_handler(_bc["server_id"], _bc["handler"])
```

This populates `_BUILTIN_HANDLER_REGISTRY` in the worker process so builtin
servers (Confluence, Jira, GitHub, Slack, etc.) do not need a network hop.

### Tool name sanitization

MCP tool names must conform to OpenAI function naming rules
(alphanumeric + underscores, max 64 chars). The registry sanitizes names at
registration time so the LLM always receives valid function names.

### `MCPClient.discover_tools(tenant_ctx)`

1. Cache check: Redis key `mcp_tools:{tenant_id}:{server_id}` with TTL.
2. If miss: HTTP `GET {server_url}/tools/list` with PKCE-authenticated header.
3. SSRF guard: private-range IPs (`10.x`, `172.16-31.x`, `192.168.x`, `127.x`)
   are rejected before the outbound request.
4. Circuit breaker: `RedisCircuitBreaker(failure_threshold=5, cooldown=60s)` per
   connector. Open circuit returns `ServiceUnavailableError`.
5. Tool list cached in Redis for subsequent calls in the same planning cycle.

### `MCPClient.call_tool(tool_name, args, tenant_ctx)`

1. Looks up connector URL from `MCPRegistry`.
2. Builds HTTP `POST {server_url}/tools/call` with JSON body `{"name": ..., "arguments": ...}`.
3. WebSocket transport: for servers that declare `transport: "websocket"`, a
   persistent WS connection is reused per session, enabling streaming tool outputs.
4. Response sanitized by `ResultProcessor` before passing back to the executor.

---

## 8. Memory Write Flows After Completion

The following table summarises every persistent write triggered by a successful
goal completion:

| Store | Write method | When | Destination |
|---|---|---|---|
| `ExecutionMemory` | `record()` + `record_async()` | Verifier success | `execution_memory` table (pgvector) |
| `LongTermMemoryStore` | `extract_from_goal()` + `extract_from_goal_async()` | Verifier success | `long_term_memory` table |
| `EvalRunner` | `score_and_persist()` | Always on success | `eval_scores` table |
| `RuntimeScorecard` | `score()` + `persist_scorecard()` | DYNAMIC_ORCHESTRATION + success | `eval_scorecards` table |
| `VerifierCalibrationStore` | `record_verdict()` | Every verification | `verifier_calibration` table |
| `ReflexionWirer` | `maybe_store_async()` | Verifier failure | `reflexion_lessons` table |
| `ABTestingEngine` | `record_result_async()` | Always | Redis (Bayesian arm stats) |
| `SelfOptimizerV2` | `on_goal_completed()` | Always | Redis (arm weights) |
| `ToolReliabilityStore` | `record(success=False)` | BLACKLIST_TOOL action | `tool_reliability` table |
| `PromptOptimizer` | `record_result(variant_id, score)` | UPDATE_PROMPT_VARIANT action | Redis |
| `RollbackEngine` | `rollback_all_async()` | Permanent failure | Compensating MCP tool calls |
| `AgentStore` | `update_config(model_downgrade_recommended=True)` | SWITCH_MODEL action | `agents` table |
| `EventStore` | `append_event()` | Every SSE event | `goal_events` table |
| `AuditLog` | `log()` | Key lifecycle events | `audit_log` table |
| `GoalCheckpoint` | `_write_checkpoint()` | After each step | `goal_checkpoints` table |

All async DB writes are wrapped in `asyncio.create_task()` and tracked in
`self._background_tasks` to prevent premature garbage collection while still
being non-blocking from the graph node's perspective.

---

## 9. SSE Event Timeline

Every node emits structured SSE events via `self._emit(event)` which calls
`event_callback(sanitize_event(event))`. Events flow through the Celery→Redis
pub/sub bridge described in §3 and are delivered to all active SSE subscribers.

| Node | Event type | Key fields | Condition |
|---|---|---|---|
| `initialize` | `goal_started` | `goal` | Always |
| `initialize` | `goal_rejected` | `reason` | Guardrail violation |
| `initialize` | `pattern_assembled` | `complexity`, `risk`, `patterns_active`, `models` | `DYNAMIC_ORCHESTRATION` or `enable_pattern_sse_events` |
| `initialize` | `runtime_profile_selected` | `profile_id`, `complexity`, `patterns`, `rag_strategy` | `DYNAMIC_ORCHESTRATION` or `enable_rag_strategy_routing` |
| `rag_retrieval` | `rag_strategy_selected` | `strategy`, `goal_id` | `_active_rag_strategy` set |
| `rag_retrieval` | `embedding_strategy_selected` | `model`, `dimensions` | Embedder configured |
| `rag_retrieval` | `chunking_strategy_selected` | `content_type`, `strategy`, `reason` | `_rag_strategy` set |
| `plan` | `model_route_selected` | `provider`, `model`, `tier` | Always |
| `plan` | `plan_created` | `steps[]` | Always |
| `execute` | `step_started` | `step_index`, `step_text` | Each step |
| `execute` | `guardrail_profile_selected` | `profile`, `step_text` | Per-step |
| `execute` | `token_chunk` | `content`, `step_index` | LLM streaming |
| `execute` | `step_complete` | `step_index`, `result`, `tool_calls[]` | Each step |
| `execute` | `hitl_requested` | `request_id`, `action`, `risk_level` | REQUIRE_APPROVAL policy |
| `verify` | `verification_done` | `success`, `reason` | Always |
| `verify` | `goal_complete` | — | Verifier success |
| `verify` | `goal_failed` | `reason` | Verifier failure, no retry |
| `verify` | `eval_score_recorded` | `scores{}`, `overall` | EvalRunner success |
| `verify` | `self_improvement_suggested` | `actions[]` | `_actions` non-empty |
| Worker | `worker_started` | `goal`, `worker` | Celery task begins |
| Worker | `worker_complete` | `status`, `iterations` | Celery task ends |
| Worker | `worker_failed` | `reason` | Celery task exception |

---

## 10. Frontend Update Path

### SSE stream — `GET /goals/{goal_id}/stream`

The router at `app/api/goals.py` handles SSE subscription:

```python
async def stream_goal_events(goal_id: str, request: Request):
    q: asyncio.Queue = asyncio.Queue(maxsize=512)
    record.subscribers.append(q)
    async def event_generator():
        while True:
            event = await asyncio.wait_for(q.get(), timeout=30.0)
            if event is _SENTINEL:
                break  # end of stream — terminal event received
            yield f"data: {json.dumps(event)}\n\n"
    return EventSourceResponse(event_generator())
```

The `_SENTINEL` (`None`) is placed on the queue by the bridge when a terminal
event (`goal_complete`, `worker_complete`, `goal_failed`, `goal_cancelled`) is
received from Redis pub/sub.

### `useGoalStream` — `agent-verse-frontend/src/lib/sse/useGoalStream.ts`

The React hook wraps the browser `EventSource` API with TanStack Query integration:

1. Opens `EventSource(GET /goals/{goal_id}/stream)` on mount.
2. Each `message` event is parsed and dispatched to the Zustand goal store.
3. The `useGoalStream` hook exposes `events[]`, `status`, and `isStreaming`.
4. On `goal_complete` or `goal_failed`, `isStreaming` is set to `false` and the
   `EventSource` is closed.

### `useCollabSocket` — `agent-verse-frontend/src/lib/ws/useCollabSocket.ts`

For multi-user collaboration on the same goal (e.g., workflow builder):

1. Opens `WebSocket(ws[s]://.../collab/{goal_id})`.
2. Sends cursor position, selection, and annotation events.
3. Broadcasts to all participants via the `CollaborationStore` (Postgres-backed in
   production).
4. Used by the workflow-builder feature (`src/features/workflow-builder/`) for
   real-time co-editing.

---

## 11. Celery Scaling and Multi-Tenancy

### Queue topology and noisy-neighbour prevention

`PLAN_QUEUE_MAP` at `celery_app.py:42` defines four dedicated goal queues:

```python
PLAN_QUEUE_MAP = {
    "free":         "goals.free",
    "starter":      "goals.starter",
    "professional": "goals.professional",
    "enterprise":   "goals.enterprise",
}
```

Enterprise goals are never delayed by a free-tier storm. Each tier can have its
own worker pool:

```bash
# Example: dedicated enterprise workers
celery -A app.scaling.celery_app worker \
    -Q goals.enterprise \
    -c 8 \
    --max-tasks-per-child=100

# Shared lower-tier workers
celery -A app.scaling.celery_app worker \
    -Q goals.free,goals.starter,goals.professional \
    -c 4
```

Additional queues:
- `goals_dlq` — dead-letter queue for goals that exhausted 3 retries
- `schedules` — `run_scheduled_goal`, `fire_due_schedules`
- `maintenance` — health checks, stuck-goal detection, retention, HITL SLA

### Beat schedule

All Beat tasks are configured in `celery_app.py:100`. HA Beat uses
`RedBeatScheduler` (if installed) with a Redis lock key
`agentverse:beat:lock` and a 5-minute timeout to elect a single Beat leader
across replicas.

| Task | Schedule | Queue | Purpose |
|---|---|---|---|
| `check_mcp_health` | every 30 s | maintenance | MCP connector health check |
| `fire_due_schedules` | every 60 s | schedules | Trigger due NL schedules |
| `record_queue_depths` | every 30 s | maintenance | Metrics: queue depth gauge |
| `detect_stuck_goals` | every 5 min | maintenance | Mark goals stuck >30 min as failed |
| `execute_retention_policy` | 3 AM UTC | maintenance | GDPR data retention cleanup |
| `expire_hitl_approvals` | every 60 s | maintenance | HITL SLA enforcement |
| `check_email_goals` | every 60 s | maintenance | Email-triggered goal ingestion |
| `flush_audit_wal` | every 10 s | maintenance | Flush audit WAL buffer to DB |
| `scan_cost_anomalies` | every hour | maintenance | Alert on 80%+ budget consumption |
| `embed_marketplace_templates` | every 15 min | maintenance | Embed new templates for similarity |
| `warm_jwks_cache` | every 9 min | maintenance | Pre-warm Keycloak JWKS public keys |
| `enforce_hitl_sla` | every 5 min | maintenance | Escalate overdue HITL requests |
| `conclude_stale_experiments` | 3 AM UTC | maintenance | Close expired A/B experiments |
| `expire_stale_documents` | 1 AM UTC | maintenance | Remove expired knowledge chunks |
| `reindex_stale_knowledge` | every hour | maintenance | Re-embed stale knowledge chunks |
| `purge_expired_artifacts` | daily | maintenance | Remove RPA artifacts past TTL |
| `drain-goals-dlq` | every 5 min | goals_dlq | Drain + mark dead-lettered goals |

### Redis Sentinel support

Both the broker URL and result backend support Sentinel topology:

```python
if _SENTINEL_URLS:
    broker = "sentinel://[:pw@]host1;host2;host3/db"
    celery_app.conf.broker_transport_options = {
        "master_name": _SENTINEL_MASTER
    }
    celery_app.conf.result_backend = broker
```

### Worker lifecycle guarantees

- `task_acks_late=True`: the broker receives the ACK only after `run_goal()`
  returns, ensuring no goal is silently dropped if the worker crashes mid-execution.
- `task_reject_on_worker_lost=True`: the task is requeued (up to `max_retries=3`)
  rather than discarded.
- Dead-letter queue: after 3 retries, the task is routed to `run_goal_dlq` which
  marks the goal `failed` with `error_message="Dead lettered: {reason}"` in the DB.
- SIGTERM handler (`tasks.py:24`): registers a handler that logs the shutdown and
  raises `SystemExit(0)`. LangGraph writes a checkpoint after each completed step,
  so the last durable state is preserved — the goal can be resumed from the last
  checkpoint after worker restart.
- `_SyncGoalLock` ensures that even if a task is requeued (e.g., after a
  `task_reject_on_worker_lost`), the second worker skips execution when the first
  worker is still holding the lock.

---

## Appendix: Environment Variables

| Variable | Default | Effect |
|---|---|---|
| `MANAGE_POOLS` | `""` | Set to `1`/`true` to enable Phase 2 wiring without code change |
| `ENVIRONMENT` | `development` | `production` refuses FakeProvider and default DB credentials |
| `REDIS_URL` | `redis://localhost:6379/0` | Primary Redis URL for all services |
| `REDIS_SENTINEL_URLS` | `""` | Comma-separated Sentinel nodes (enables HA Redis) |
| `REDIS_SENTINEL_MASTER` | `mymaster` | Sentinel master name |
| `DATABASE_URL` | — | asyncpg DSN (required in production) |
| `ANTHROPIC_API_KEY` | — | Primary or verifier LLM provider |
| `OPENAI_API_KEY` | — | Primary or verifier LLM provider |
| `GOOGLE_API_KEY` | — | Gemini provider |
| `VOYAGE_API_KEY` | — | Voyage embedding provider (preferred over OpenAI for embeddings) |
| `VERIFIER_API_KEY` | — | Explicit separate key for cross-model verification |
| `DYNAMIC_ORCHESTRATION` | — | Enable `RuntimeProfileBuilder`, `RuntimeScorecard`, `SelfImprovementEngine` |
| `CORS_ORIGINS` | — | Comma-separated allowed origins |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | OpenTelemetry OTLP endpoint for traces |
| `SENTENCE_TRANSFORMERS_MODEL` | — | Local embedding model name (no API key required) |

---

*Last updated: 2026-07-09. Derived from source at commit HEAD.*
