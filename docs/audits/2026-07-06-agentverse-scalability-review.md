# AgentVerse Scalability & Architecture Risk Review
**Date:** 2026-07-06  
**Reviewer:** Distributed-Systems Architect  
**Scope:** Backend — `agent-verse-backend/` (Python 3.12, FastAPI, LangGraph, Celery, Postgres+pgvector, Redis)  
**Method:** Static analysis of production source files. Every claim is tied to a specific file:line.

---

## Preamble: How Analysis Was Performed

Files read in full before any finding was written:

| Area | Files Read |
|---|---|
| Multi-tenancy | `app/tenancy/middleware.py`, `app/db/rls.py`, `app/tenancy/context.py`, `app/tenancy/limits.py` |
| Agent loop | `app/agent/loop.py`, `app/agent/graph.py` |
| Celery | `app/scaling/tasks.py`, `app/scaling/celery_app.py` |
| SSE/pub-sub | `app/services/goal_service.py` (SSE sections) |
| Redis | `app/tenancy/rate_limiter.py`, `app/governance/cost.py`, `app/services/goal_service.py` |
| Connection pooling | `app/db/session.py`, `app/main.py` (lifespan) |
| Reliability | `app/reliability/circuit_breaker.py`, `app/reliability/bulkhead.py` |
| Migrations | `app/db/migrations/versions/` (directory listing + 0031 sample) |
| Health | `app/api/system.py` |
| In-memory state | `app/main.py` (lifespan + service construction) |

---

## [CRITICAL] Architecture Risk 1: LangGraph Checkpointer Falls Back to MemorySaver in Celery Workers

**Component:** Agent loop checkpointing (`app/agent/graph.py`, `app/scaling/tasks.py`)

**Evidence:**
- `app/agent/graph.py:268` — `self._checkpointer = checkpointer if checkpointer is not None else MemorySaver()`
- `app/scaling/tasks.py:887-913` — `AgentGraph(...)` is constructed with no `checkpointer=` argument
- `app/services/goal_service.py:101-198` — `_resolve_checkpointer()` attempts Redis savers but this is called in the API process, not in the Celery worker
- `app/services/goal_service.py:183` — logs `"impact=GOAL STATE WILL BE LOST ON PROCESS RESTART"` on fallback

**Problem:** Celery workers construct `AgentGraph` without passing a checkpointer, so the graph defaults to `MemorySaver` — an in-process dict. Every checkpoint written after each LangGraph node exists only in the Celery worker's RAM for that task's duration. If the worker crashes, is OOM-killed (memory limit is 512 MB per `docker-compose.yml:235-237`), or is SIGTERM'd between plan and execute, the entire goal state is lost and must be re-executed from scratch.

**Impact:** Goals currently executing at worker crash time are silently abandoned. `_recover_interrupted_goals()` (`goal_service.py:556`) re-enqueues them but the recovery is a full restart, not a resume — all LLM tokens consumed in the previous run are wasted and re-billed. Under memory pressure this becomes a retry storm.

**Recommendation:**
1. Pass the `_resolve_checkpointer(app_state)` result into `AgentGraph` at Celery task construction time. The Celery worker must independently connect to Redis and build its own `RedisSaver` or `AsyncRedisSaver`.
2. Add a module-level `_WORKER_CHECKPOINTER` initialized once per Celery process (in `@worker_init.connect`) so connection is not re-established per task.
3. Add a test that kills a worker mid-goal and verifies the goal resumes from the last checkpoint.

---

## [CRITICAL] Architecture Risk 2: In-Memory Goal State Diverges Across API Replicas

**Component:** Goal service / SSE fanout (`app/services/goal_service.py`)

**Evidence:**
- `app/services/goal_service.py:283` — `self._goals: dict[str, GoalRecord] = {}` — per-process dict
- `app/services/goal_service.py:87` — `subscribers: list[asyncio.Queue[dict[str, Any] | None]]` — per-process asyncio queues
- `app/services/goal_service.py:364-468` — Redis pub/sub bridge exists to forward Celery worker events to SSE streams, but only the process that holds the `asyncio.Queue` subscriber can deliver them
- `app/main.py:770` — `app.state.goal_service = _goal_svc_with_db` — each replica has its own GoalService instance

**Problem:** When a client submits a goal via replica A and then opens an SSE stream on replica B, replica B has no `GoalRecord` entry and no subscriber queue for that goal. The Redis pub/sub bridge (`_subscribe_celery_goal_events`) creates a stub `GoalRecord` when it receives a Celery event, but this only works if the SSE connection reaches the same replica that received the first Celery event. There is no sticky-session enforcement. Goal list queries (`GET /goals`) on replica B return stale or empty data until the next `sync_from_db()` call.

**Impact:** SSE streams silently drop events in multi-replica deployments. Goal status is inconsistent across replicas until DB sync. Clients that reconnect to a different replica after a pod restart miss all historical events queued in the old replica's memory.

**Recommendation:**
1. **SSE must be Redis-pub/sub-only at scale.** Remove the in-process subscriber queue as the primary delivery mechanism. Each replica subscribes to `goal_events:{tenant_id}:{goal_id}` and streams directly to the HTTP client.
2. Goal status reads must always go to DB, never to the in-memory cache, unless a consistent read-your-writes guarantee can be proven.
3. If sticky sessions are used as a short-term fix, document it explicitly and enforce at the load balancer level.

---

## [HIGH] Architecture Risk 3: Rate Limiter Has TOCTOU Race — Not Atomic

**Component:** Rate limiting (`app/tenancy/rate_limiter.py`)

**Evidence:**
- `app/tenancy/rate_limiter.py:44-56` — three separate Redis operations: `zremrangebyscore` → `zcard` → `zadd`, without a Lua script
- The check (`zcard` ≥ limit) and the write (`zadd`) are two separate round-trips with no atomicity guarantee
- `app/governance/cost.py:147-166` — by contrast, the cost controller uses a Lua script (`_LUA_CHECK_AND_INCREMENT`) for atomic check-and-increment — demonstrating the pattern is known

**Problem:** Under concurrent requests from the same tenant (common with agentic clients making parallel tool calls), two requests can both read `count < limit` before either writes `zadd`. Both are admitted. The effective limit is thus `limit + N` where N is the number of concurrent requests at the boundary. For an enterprise tenant with 6,000 RPM limit this is probably acceptable; for a free-tier tenant with 30 RPM it could be a significant overage.

**Impact:** Rate limits are not enforced exactly. In particular, the fallback in-process counter (`middleware.py:60-68`) is also not atomic across async tasks in the same process (no lock around the tuple read/write).

**Recommendation:**
```lua
-- Replace the 3-step zremrangebyscore/zcard/zadd with a single Lua script:
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window_start = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, 0, window_start)
local count = tonumber(redis.call('ZCARD', key))
if count >= limit then return {0, 0} end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, tonumber(ARGV[5]))
return {1, limit - count - 1}
```
The in-process fallback should use `asyncio.Lock` per tenant.

---

## [HIGH] Architecture Risk 4: BulkheadRegistry Uses In-Process Semaphores — Per-Replica Limit Multiplication

**Component:** Per-tenant concurrency bulkhead (`app/reliability/bulkhead.py`)

**Evidence:**
- `app/reliability/bulkhead.py:37-54` — `self._semaphores: dict[str, asyncio.Semaphore]` — process-local
- `app/tenancy/limits.py:87-94` — concurrent goal limits: free=2, starter=5, professional=20, enterprise=100
- There is a `RedisBulkheadRegistry` referenced in comments (`graph.py:226`) but it is not confirmed as the default

**Problem:** With K API replicas, a tenant can hold K × limit concurrent goal slots simultaneously — one per replica. For a free-tier tenant with limit=2, with 3 replicas they can run 6 concurrent goals. The `check_and_increment_concurrent_goals` Lua script in `limits.py:99-117` correctly uses Redis to enforce cross-replica counting, but this is only called for goal submission, not for the bulkhead tool-call limit inside the agent loop.

**Impact:** The isolation guarantee advertised to enterprise customers ("prevents noisy-neighbour effects") degrades proportionally to replica count. On a high-availability 10-replica deployment, free-tier tenants can run 20 concurrent goals.

**Recommendation:**
1. Replace `BulkheadRegistry` in the API process with `RedisBulkheadRegistry` (confirm it exists and is correct).
2. The `check_and_increment_concurrent_goals` Redis Lua path (already implemented in `limits.py`) is the correct pattern — ensure it is called for every goal execution path, including Celery-dispatched goals.

---

## [HIGH] Architecture Risk 5: Redis Is a Single Point of Failure With No HA Configuration

**Component:** Infrastructure (`infra/docker-compose.yml`, multiple service files)

**Evidence:**
- `infra/docker-compose.yml:45-57` — single `redis:7-alpine` container, no Sentinel, no Cluster
- `app/tenancy/middleware.py:44-68` — on Redis failure, falls back to `_fallback_counters` (in-process dict, not cross-replica)
- `app/governance/cost.py:291-296` — `return os.getenv("ENVIRONMENT", "development") != "production"` — **budget enforcement fails open in non-production** but in production the exception returns `False` (blocks all requests)
- `app/services/goal_service.py:364-468` — Celery→SSE bridge is Redis pub/sub; if Redis is unavailable, live goal events are never delivered to SSE clients
- `app/scaling/celery_app.py:24-30` — Celery broker and backend are both `REDIS_URL` — complete outage if Redis goes down
- `app/reliability/circuit_breaker.py:32` — in-memory circuit breaker; Redis circuit breaker state not shared

**Problem:** Redis is used simultaneously as: Celery broker, Celery result backend, LangGraph checkpointer, rate limiter, cost controller, SSE pub/sub bus, distributed lock store, connector secret store, semantic cache, and JWKS cache. A Redis outage takes down all of these simultaneously. There is no Redis Sentinel or Cluster configured anywhere in the stack.

**Impact:** A Redis pod restart (even a 10-second restart) causes: Celery workers to stop accepting tasks, in-flight SSE streams to break, rate limiting to fall back to per-process counters, and LangGraph checkpoints to be lost. RTO is effectively the Redis restart time plus Celery reconnection time (typically 30–60s).

**Recommendation:**
1. Configure Redis Sentinel (minimum) with 1 primary + 2 replicas for HA.
2. Separate Celery broker/backend from application Redis (different Redis instances or Redis Cluster with separate keyspaces).
3. Add Redis health check as a registered `HealthCheck` that returns 503 immediately on `app/api/system.py` health endpoint.
4. Test the Redis failover path explicitly — verify Celery reconnects, SSE resumes, and rate limiting degrades gracefully.

---

## [HIGH] Architecture Risk 6: Per-Plan Celery Queue Routing Is Not Applied at Dispatch Time

**Component:** Celery queue architecture (`app/scaling/celery_app.py`, `app/scaling/tasks.py`)

**Evidence:**
- `app/scaling/celery_app.py:16-21` — `PLAN_QUEUE_MAP = {"free": "goals.free", "starter": "goals.starter", ...}` is defined
- `app/scaling/celery_app.py:47` — `"app.scaling.tasks.run_goal": {"queue": "goals"}` — the default route is always the shared `goals` queue
- `app/scaling/tasks.py:402` — `@celery_app.task(name="app.scaling.tasks.run_goal", ...)` — no per-plan routing logic in the decorator
- The plan is resolved inside `run_goal` (tasks.py:429-446) **after** the task has already been picked up from the shared queue

**Problem:** The per-plan queue map exists in code but is never used for `run_goal`. Every goal, regardless of tenant plan tier, lands in the shared `goals` queue. The dedicated `goals.enterprise`, `goals.professional`, etc. queues are defined and workers are configured to consume them (`docker-compose.yml:216`), but no goal ever enters them via the standard submission path.

**Impact:** The noisy-neighbour isolation promise (enterprise tenants get dedicated workers) is not delivered. A free-tier tenant submitting 1000 goals will delay enterprise tenant goals equally. The infrastructure has been built (dedicated queue consumers) but the routing at dispatch time was never wired.

**Recommendation:**
At the call site where `run_goal.apply_async()` is invoked (in `GoalService.submit_goal` and `CeleryGoalTaskQueue.enqueue_goal`), use `PLAN_QUEUE_MAP` to choose the queue:
```python
queue = PLAN_QUEUE_MAP.get(tenant_ctx.plan.value, "goals")
run_goal.apply_async(kwargs={...}, queue=queue)
```

---

## [HIGH] Architecture Risk 7: SSE Subscriber List Can Accumulate Dead Entries Indefinitely

**Component:** SSE fanout (`app/services/goal_service.py`)

**Evidence:**
- `app/services/goal_service.py:87` — `subscribers: list[asyncio.Queue[...]]` — a plain list
- `app/services/goal_service.py:430-442` — dead subscribers are removed on `QueueFull` exception only
- `app/services/goal_service.py:433-435` — for `token_chunk` and `heartbeat` event types, `QueueFull` does **not** trigger removal: `if event_type not in {"token_chunk", "heartbeat"}: dead.append(q)`
- `app/services/goal_service.py:480-506` — `_evict_stale_goals()` removes completed `GoalRecord` objects but only after 1 hour TTL

**Problem:** Subscriber queues are closed and removed only when they fill up on non-heartbeat events. A client that disconnects (TCP RST or timeout) does not immediately trigger removal. The queue for a disconnected client will continue to accumulate `token_chunk` events (which are high-frequency) until the queue fills. Since `token_chunk` events skip dead-subscriber detection, the queue may never fill if the goal completes before the default `asyncio.Queue` maxsize is hit. Result: one disconnected client per long-running goal → one unbounded queue held in memory for up to 1 hour.

**Impact:** Memory leak proportional to (disconnected clients) × (goal duration) × (event size). For a 30-minute enterprise goal emitting 100 events/second, a single disconnected client holds ~180,000 queued dicts.

**Recommendation:**
1. Detect client disconnection in the SSE generator (`request.is_disconnected()`) and immediately remove the subscriber queue.
2. Remove the `token_chunk`/`heartbeat` carve-out from dead-subscriber detection — `QueueFull` on any event type should trigger removal.
3. Use bounded queues (`asyncio.Queue(maxsize=500)`) and remove on `QueueFull` unconditionally.

---

## [MEDIUM] Architecture Risk 8: Celery Worker Creates a New Event Loop Per Task, Reusing Engine Bound to a Dead Loop

**Component:** Database session management in Celery (`app/scaling/tasks.py`, `app/db/session.py`)

**Evidence:**
- `app/scaling/tasks.py:174-180` — `_run_async()` creates a fresh `asyncio.new_event_loop()` and closes it after every call
- `app/db/session.py:39-46` — `_session_factory` is a module-level singleton; `get_session_factory()` creates it once and reuses it
- `app/db/session.py:21-30` — `create_async_engine()` is called once to back the session factory; asyncpg connections are created against the event loop active at construction time
- `app/scaling/tasks.py:594-602` — `_run_async(ensure_submitted_goal_row())`, then `_run_async(mark_worker_started())` — each call creates and destroys a new event loop

**Problem:** `asyncpg` connection pools bind themselves to the asyncio event loop that was running when the pool was created. When `_run_async()` creates a new event loop, any existing asyncpg connections in the pool are bound to the old (now closed) loop. This can cause `"Future attached to a different loop"` errors or silently corrupt connection state. The module-level `_session_factory` singleton is created in the first `_run_async()` call's event loop — subsequent calls from new event loops may hit this issue.

**Impact:** Intermittent DB errors (`asyncio.InvalidStateError`, `RuntimeError: Future attached to a different loop`) under load, particularly visible when a Celery worker handles many tasks in sequence. These errors manifest as goal failures with confusing tracebacks.

**Recommendation:**
1. Use `asyncio.run()` instead of `new_event_loop()`/`run_until_complete()`/`close()` — it properly cleans up event loop resources.
2. Do not use a module-level singleton session factory in Celery workers. Create a fresh factory at the start of each task's `_run_async()` call and close it at the end.
3. Alternatively, use a synchronous DB driver (`psycopg2`/`psycopg3` sync) for the short-lived status-update operations in `run_goal`.

---

## [MEDIUM] Architecture Risk 9: Health Endpoint Does Not Verify Celery Worker Liveness

**Component:** Health checks (`app/api/system.py`, `app/observability/health.py`)

**Evidence:**
- `app/api/system.py:16-27` — `/health` delegates to `HealthRegistry.run()` which returns all registered checks
- `app/main.py:465` — `registry = HealthRegistry(list(health_checks or []))` — health checks are injected at app creation; production default is `health_checks=None` (empty list unless wired explicitly)
- No evidence of a Celery-specific health check being registered (e.g., `celery inspect ping`)
- `infra/docker-compose.yml:246-248` — the Celery worker's own Docker healthcheck calls `celery inspect ping` but this is separate from the API's `/health` response

**Problem:** The API's `/health` endpoint cannot indicate that goal execution is broken if Celery workers are down or unresponsive. A deployment that has the API up but zero Celery workers will return `200 healthy` and accept goal submissions that will never be executed. Goals will silently pile up in the Redis queue.

**Impact:** Ops/SRE teams cannot use the `/health` endpoint as a single signal for system health. Goals may be accepted with 202 and then silently time out with no indication at the platform level.

**Recommendation:**
1. Register a `HealthCheck` that calls `celery_app.control.inspect(timeout=2).ping()` and fails if no workers respond.
2. Register a `HealthCheck` for Redis connectivity (attempt a `PING`).
3. Register a `HealthCheck` for PostgreSQL connectivity (attempt a `SELECT 1`).
4. Set `manage_pools=True` automatically in production so the lifespan starts real pools and health checks have real connections to test.

---

## [MEDIUM] Architecture Risk 10: Migration Chain Has Gaps (0048 → 0053) and No Deploy-Order Guarantee

**Component:** Database migrations (`app/db/migrations/versions/`)

**Evidence:**
- Migration versions jump from `0048_goal_templates.py` to `0053_agent_credentials.py` — revisions 0049–0052 are missing from the directory listing
- No evidence of a deploy-order enforcement mechanism (e.g., the app fails to start if migrations are pending)
- `app/main.py` lifespan does not call `alembic upgrade head` before serving traffic
- `app/db/migrations/versions/0031_audit_immutability.py:49-51` — `downgrade()` exists and correctly reverses all DDL; migrations are reversible for inspected files

**Problem:** The gap in migration numbers (0049–0052) suggests either lost migrations or a merge that was not properly resolved. If those revisions exist on a branch and are merged, Alembic's linear chain will break. Additionally, there is no application-level guard preventing startup with a stale schema — the app will start and serve traffic even if the DB is one migration behind, leading to column-not-found errors at runtime for features that depend on the missing columns.

**Impact:** Silent schema drift in production. A botched deploy that applies migrations after traffic is switched over exposes a window where new code runs against old schema. The missing revisions (0049–0052) could represent real features that need their schema changes.

**Recommendation:**
1. Audit the migration chain: run `alembic history` to identify if 0049–0052 were never created (numbering gap) or are genuinely missing.
2. Add a startup check: before the app begins serving requests, verify the DB schema version matches the codebase's head revision. Fail-fast if it doesn't.
3. Enforce migration-before-code-deploy in CI/CD: run `alembic upgrade head` as a pre-deploy step in `deploy.yml`.
4. All new migrations must have a tested `downgrade()` path.

---

## Summary Table

| # | Severity | Component | Root Cause |
|---|---|---|---|
| 1 | CRITICAL | Agent checkpointing | MemorySaver used in Celery workers |
| 2 | CRITICAL | Goal state / SSE | In-process `GoalRecord` dict not replicated |
| 3 | HIGH | Rate limiting | Non-atomic TOCTOU on Redis sorted-set ops |
| 4 | HIGH | Bulkhead / concurrency | In-process semaphores multiply by replica count |
| 5 | HIGH | Infrastructure | Redis single-node, no Sentinel/Cluster |
| 6 | HIGH | Celery queues | Per-plan routing defined but never used |
| 7 | HIGH | SSE memory | Dead subscribers not pruned for token_chunk events |
| 8 | MEDIUM | Celery DB sessions | New event loop per task vs engine created once |
| 9 | MEDIUM | Health checks | Celery worker liveness not verified |
| 10 | MEDIUM | Migrations | Gap in revision chain, no startup schema check |
