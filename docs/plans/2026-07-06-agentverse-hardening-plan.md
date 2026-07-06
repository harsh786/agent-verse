# AgentVerse Hardening Plan — 2026-07-06

> **Scope:** Derived from four audits conducted 2026-07-06:
> feature audit, security audit, scalability/architecture review, and frontend UX review.
> Every item is traceable to a specific finding in those documents.

---

## Priority Matrix

| Priority | Count | Theme |
|---|---|---|
| P0 (Launch Blocker) | 10 | Security exploits + data isolation failures |
| P1 (High) | 9 | Reliability, data durability, auth hardening |
| P2 (Medium) | 16 | Correctness, UX completeness, graceful degradation |
| P3 (Low) | 13 | Polish, defence-in-depth, mobile, accessibility |

---

## P0: Launch Blockers (must fix before launch)

### P0-1: SSRF in `test_connector` Generic Fallback
- **Risk:** Any tenant can register a connector pointing to `http://169.254.254.169/latest/meta-data/` (AWS IMDS) or internal Kubernetes services. The generic `httpx.get(url)` path in `test_connector` has no SSRF guard. Bearer tokens from `auth_config` are forwarded to the target, enabling credential exfiltration to attacker-controlled servers.
- **Files:** `app/api/connectors.py:487–513`
- **Fix:**
  ```python
  from app.net.ssrf_guard import SSRFError, assert_public_url
  try:
      assert_public_url(url, context=f"connector test {server_id}")
  except SSRFError as e:
      raise HTTPException(status_code=400, detail=f"Connector URL blocked: {e}")
  ```
  Add this before the generic `httpx.AsyncClient().get(url, ...)` call. Also strip bearer tokens from `auth_config` when the test URL does not match the connector's registered base URL.
- **Tests:** `tests/api/test_connectors.py::test_test_connector_ssrf_blocked` — register connector with `url="http://127.0.0.1:5432"`, call test endpoint, assert HTTP 400.
- **Verification command:** `uv run pytest tests/api/test_connectors.py -k "ssrf" -v`
- **Est. effort:** 2h

---

### P0-2: SSRF in `discover_tools` (MCP Client)
- **Risk:** `MCPClient.discover_tools()` makes `GET {url}/tools` and `POST {cfg.url}` without calling `assert_public_url()`. Authenticated tenants can probe internal network topology via `POST /connectors/{server_id}/discover`.
- **Files:** `app/mcp/client.py:321–364`, `app/api/connectors.py:1184–1195`
- **Fix:**
  ```python
  # client.py, start of discover_tools():
  _url = _absolute_http_url(cfg.url or cfg.base_url or "")
  if _url and not _url.startswith("builtin://"):
      assert_public_url(_url, context=f"discover_tools {server_id}")
  ```
- **Tests:** `tests/mcp/test_client.py::test_discover_tools_ssrf_blocked`
- **Verification command:** `uv run pytest tests/mcp/test_client.py -k "ssrf" -v`
- **Est. effort:** 1h

---

### P0-3: SSRF Guard Fails Open on DNS Resolution Failure
- **Risk:** When `socket.getaddrinfo` raises `gaierror`, `assert_public_url()` returns without error (line 149: `return`). DNS-rebinding and split-horizon DNS attacks bypass the guard entirely.
- **Files:** `app/net/ssrf_guard.py:145–149`
- **Fix:**
  ```python
  if not ips:
      raise SSRFError(
          f"SSRF guard [{context}]: cannot resolve '{hostname}' — blocked (fail-closed)"
      )
  ```
- **Tests:** `tests/net/test_ssrf_guard.py::test_unresolvable_dns_blocked` — mock `socket.getaddrinfo` to raise `socket.gaierror`; verify `SSRFError` raised.
- **Verification command:** `uv run pytest tests/net/test_ssrf_guard.py -v`
- **Est. effort:** 1h

---

### P0-4: Rotate Real Credentials in `.env` + Add Secret Scan to CI
- **Risk:** `agent-verse-backend/.env` contains live Atlassian (Jira + Confluence) and OpenAI API keys tied to a real enterprise account (`harsh.kumar01@pinelabs.com`). A workstation compromise, accidental Docker build context inclusion, or screen share exposes production credentials.
- **Files:** `agent-verse-backend/.env`, `.github/workflows/ci.yml`
- **Fix (immediate — do before anything else):**
  1. Rotate the OpenAI key at https://platform.openai.com/api-keys.
  2. Rotate the Atlassian token at https://id.atlassian.com/manage-profile/security/api-tokens.
  3. Replace all real values in `.env` with clearly fake placeholders: `OPENAI_API_KEY=sk-your-key-here`.
  4. Add `gitleaks` as a required CI step:
     ```yaml
     - name: Secret scan
       run: npx gitleaks detect --source . --no-git --exit-code 1
     ```
  5. Add `detect-secrets` as a pre-commit hook.
- **Tests:** CI fails on `gitleaks` finding. No pytest test — add to launch readiness checklist.
- **Verification command:** `npx gitleaks detect --source . --no-git`
- **Est. effort:** 1h (rotation) + 2h (CI integration)

---

### P0-5: `GET /rpa/tools` Has No Authentication
- **Risk:** The full RPA tool inventory (names, argument schemas, descriptions) is publicly accessible without an API key. Every other endpoint in the same router requires `_require_tenant`.
- **Files:** `app/api/rpa.py:31–34`
- **Fix:**
  ```python
  @router.get("/tools")
  async def list_rpa_tools(request: Request) -> list[dict[str, Any]]:
      _require_tenant(request)
      return [dict(tool) for tool in RPA_TOOLS]
  ```
- **Tests:** `tests/api/test_rpa.py::test_list_rpa_tools_requires_auth` — call without API key, assert 401/403.
- **Verification command:** `uv run pytest tests/api/test_rpa.py -k "auth" -v`
- **Est. effort:** 0.5h

---

### P0-6: No Rate Limiting on `/tenants/signup` (Public Endpoint)
- **Risk:** Unauthenticated endpoint with no rate limit allows automated creation of thousands of tenant rows, DB exhaustion, email enumeration via `409 Conflict` responses, and Redis namespace pollution.
- **Files:** `app/api/tenants.py:62–75`, `app/tenancy/middleware.py:78`
- **Fix:** Apply a Redis-backed rate limiter (5 signups per minute per IP):
  ```python
  # In tenants.py or a dedicated signup limiter:
  from slowapi import Limiter
  from slowapi.util import get_remote_address
  limiter = Limiter(key_func=get_remote_address)

  @router.post("/signup", status_code=201)
  @limiter.limit("5/minute")
  async def signup(body: SignupRequest, request: Request) -> JSONResponse:
      ...
  ```
  Also consider email-domain allowlist for production.
- **Tests:** `tests/api/test_tenants.py::test_signup_rate_limited` — 10 requests from same IP in 1 minute, assert 11th returns 429.
- **Verification command:** `uv run pytest tests/api/test_tenants.py -k "rate" -v`
- **Est. effort:** 2h

---

### P0-7: Admin API Key Comparison Vulnerable to Timing Attack
- **Risk:** `x_admin_key != admin_key` in `_require_admin()` short-circuits on first character mismatch. An attacker co-located on the same network can reconstruct the admin key character-by-character via timing oracle in ~8,192 requests. Admin access grants cross-tenant control.
- **Files:** `app/api/admin.py:30–36`
- **Fix:**
  ```python
  import hmac
  if not hmac.compare_digest(x_admin_key.encode(), admin_key.encode()):
      raise HTTPException(status_code=401, detail="Invalid admin key")
  ```
- **Tests:** `tests/api/test_admin.py::test_admin_auth_uses_compare_digest` — inspect `_require_admin` source to verify `hmac.compare_digest` is used; assert wrong key returns 401.
- **Verification command:** `uv run pytest tests/api/test_admin.py -v`
- **Est. effort:** 0.5h

---

### P0-8: SSE Stream Does Not Verify Goal Ownership
- **Risk:** An authenticated tenant who knows (or guesses) another tenant's `goal_id` UUID can open a real-time SSE stream for that goal and observe execution events, tool calls, LLM outputs, and HITL decisions.
- **Files:** `app/api/goals.py:359–408`
- **Fix:** Add a resource-ownership check before yielding events:
  ```python
  async def stream_goal(request: Request, goal_id: str):
      tenant_ctx = _require_tenant(request)
      svc: GoalService = request.app.state.goal_service
      # Raises 404 if goal_id does not belong to this tenant:
      await svc.get_goal(goal_id=goal_id, tenant_ctx=tenant_ctx)
      # ... then proceed with StreamingResponse
  ```
- **Tests:** `tests/api/test_goals.py::test_stream_goal_rejects_wrong_tenant` — submit goal as tenant A, stream it as tenant B, assert 404.
- **Verification command:** `uv run pytest tests/api/test_goals.py -k "stream" -v`
- **Est. effort:** 1h

---

### P0-9: `memory_v2` API Has No DB Persistence (All Data Lost on Restart)
- **Risk:** All 10 memory-v2 endpoints read/write a module-level `_memories: dict` (marked "# In-memory store for demo"). Migration `0062_knowledge_v2.py` exists but is unused. Every rolling deploy or scale-out wipes all memory-v2 entries — an agent's long-term memory is silently destroyed.
- **Files:** `app/api/memory_v2.py:18–20`
- **Fix:** Replace `_memories` and `_conflicts` module-level dicts with a `MemoryV2Repository` class that persists to the `memories_v2` table using `sqlalchemy_rls_context`. Follow the same pattern as `KnowledgeStore` in `app/knowledge/store.py`. Wire the repository in `create_app()` lifespan.
- **Tests:** `tests/memory/test_memory_v2_persistence.py` — write a memory, restart the repository object, read back and assert data is present.
- **Verification command:** `uv run pytest tests/memory/ -v`
- **Est. effort:** 6h

---

### P0-10: `skills_runtime` API Has No DB Persistence (Dead Migration, Unbounded Memory)
- **Risk:** All skill registrations live in four module-level dicts. Migration `0074_skills_table.py` creates a `skills` table that is never used. `_executions` dict has no size bound — unbounded memory growth under sustained load. Custom tenant skills are lost on restart.
- **Files:** `app/api/skills_runtime.py:34–38`
- **Fix:** Route all skill CRUD through the `skills` DB table. Wire `SkillRepository` using `sqlalchemy_rls_context` in `create_app()`. Add execution history TTL/cleanup in the periodic maintenance Celery task. Backfill builtin skills from `BUILTIN_SKILLS` at startup.
- **Tests:** `tests/skills/test_skills_persistence.py` — create a skill, simulate restart (new repo instance), assert skill persists.
- **Verification command:** `uv run pytest tests/skills/ -v`
- **Est. effort:** 6h

---

## P1: High Priority (fix this sprint)

### P1-1: LangGraph Checkpointer Falls Back to `MemorySaver` in Celery Workers
- **Risk:** Celery workers construct `AgentGraph` with no `checkpointer=` argument; the graph defaults to `MemorySaver`. If a worker is OOM-killed or SIGTERM'd during plan→execute, the entire goal state is lost and re-execution re-bills all LLM tokens. Under memory pressure this becomes a retry storm.
- **Files:** `app/agent/graph.py:268`, `app/scaling/tasks.py:887–913`, `app/services/goal_service.py:101–198`
- **Fix:**
  1. Initialise a module-level `_WORKER_CHECKPOINTER` in `@worker_init.connect` signal handler (once per Celery process, not per task).
  2. Pass `checkpointer=_WORKER_CHECKPOINTER` when constructing `AgentGraph` in `run_goal`.
  3. `_WORKER_CHECKPOINTER` should use `RedisSaver` (sync) since Celery uses `_run_async()` per call.
- **Tests:** `tests/agent/test_checkpointing.py::test_celery_goal_resumes_after_worker_restart`
- **Verification command:** `uv run pytest tests/agent/test_checkpointing.py -v`
- **Est. effort:** 4h

---

### P1-2: In-Memory Goal State Diverges Across API Replicas (SSE Drops Events)
- **Risk:** Each API replica holds its own `_goals: dict` and `asyncio.Queue` subscribers. A client that submits on replica A and opens SSE on replica B silently receives no events.
- **Files:** `app/services/goal_service.py:87, 283, 364–468`
- **Fix:**
  1. SSE delivery must be Redis-pub/sub-only: each replica subscribes to `goal_events:{tenant_id}:{goal_id}` and streams directly to the HTTP client.
  2. Remove the in-process subscriber queue as the primary delivery path (keep as fallback for single-replica dev).
  3. Goal status reads must query DB, not `_goals` dict, except for hot-path caching with TTL.
  4. Document and enforce sticky sessions at the load balancer as a short-term fallback.
- **Tests:** `tests/services/test_goal_service.py::test_sse_cross_replica_delivery` — simulate two GoalService instances sharing Redis; publish event via one, assert it is received by SSE subscriber on the other.
- **Verification command:** `uv run pytest tests/services/test_goal_service.py -k "cross_replica" -v`
- **Est. effort:** 8h

---

### P1-3: MFA TOTP Replay Prevention and Session Store Are Process-Local
- **Risk:** With 2+ replicas, a TOTP code consumed on replica A can be replayed on replica B within the 30-second window. MFA session tokens are also process-local — subsequent requests routed to a different replica get MFA_REQUIRED despite having authenticated.
- **Files:** `app/api/mfa.py:112, 300`, `app/tenancy/middleware.py:239`
- **Fix:**
  ```python
  # Replay prevention: Redis SET with 90s TTL
  # Key: f"mfa_replay:{tenant_id}:{window}:{code}"
  # Session: Redis HASH at f"mfa_session:{token}" with 3600s TTL
  ```
  Use `request.app.state._redis` (already available).
- **Tests:** `tests/api/test_mfa.py::test_totp_replay_blocked_across_replicas`
- **Verification command:** `uv run pytest tests/api/test_mfa.py -v`
- **Est. effort:** 3h

---

### P1-4: Vault Dev Master Key Used Silently in Non-Production
- **Risk:** Any staging/QA environment without `VAULT_MASTER_KEY` set encrypts all MCP connector credentials with `"dev-insecure-master-key"` — a key hardcoded in public source. A DB dump from staging decrypts every stored credential.
- **Files:** `app/providers/vault.py:21, 314–338`
- **Fix:**
  1. Log a prominent startup `WARNING` when dev key is used.
  2. Require `ALLOW_DEV_VAULT=true` env var to be set explicitly to acknowledge the risk.
  3. In CI, fail if `ALLOW_DEV_VAULT` is unset and no `VAULT_MASTER_KEY` is provided.
- **Tests:** `tests/providers/test_vault.py::test_dev_vault_emits_warning`
- **Verification command:** `uv run pytest tests/providers/test_vault.py -v`
- **Est. effort:** 1h

---

### P1-5: OAuth Access/Refresh Tokens Stored in Plaintext
- **Risk:** `OAuthFlowManager._vault` is never assigned, so `hasattr(self, "_vault")` always returns `False`. All OAuth tokens stored in `oauth_tokens` table are plaintext. DB read access (backup, compromise, misconfigured RLS) exposes all tenant OAuth credentials.
- **Files:** `app/mcp/oauth.py:54–61, 247–254`, `app/main.py` (lifespan)
- **Fix:**
  ```python
  # In main.py lifespan:
  _oauth_manager = OAuthFlowManager(vault=get_vault())
  # OR in OAuthFlowManager.__init__:
  def __init__(self, vault: CredentialVault | None = None) -> None:
      self._vault = vault or get_vault()
  ```
- **Tests:** `tests/mcp/test_oauth.py::test_tokens_encrypted_at_rest`
- **Verification command:** `uv run pytest tests/mcp/test_oauth.py -v`
- **Est. effort:** 2h

---

### P1-6: Per-Plan Celery Queue Routing Is Defined but Never Used
- **Risk:** Enterprise isolation promise ("enterprise tenants get dedicated workers") is not delivered. Every goal, regardless of plan, lands in the shared `goals` queue. `PLAN_QUEUE_MAP` exists in code but is never applied at dispatch time.
- **Files:** `app/scaling/celery_app.py:16–21, 47`, `app/scaling/tasks.py:402`
- **Fix:**
  ```python
  # In GoalService.submit_goal and CeleryGoalTaskQueue.enqueue_goal:
  queue = PLAN_QUEUE_MAP.get(tenant_ctx.plan.value, "goals")
  run_goal.apply_async(kwargs={...}, queue=queue)
  ```
- **Tests:** `tests/scaling/test_celery_routing.py::test_enterprise_goal_routes_to_enterprise_queue`
- **Verification command:** `uv run pytest tests/scaling/ -v`
- **Est. effort:** 2h

---

### P1-7: SSE Dead Subscriber Memory Leak
- **Risk:** Disconnected clients' subscriber queues are only pruned on `QueueFull` for non-heartbeat event types. `token_chunk` events skip the dead-subscriber check entirely. A single disconnected client on a 30-minute enterprise goal accumulates ~180,000 queued dicts in RAM.
- **Files:** `app/services/goal_service.py:87, 430–442, 480–506`
- **Fix:**
  1. Detect disconnect in SSE generator: `if await request.is_disconnected(): break`.
  2. Use bounded queues: `asyncio.Queue(maxsize=500)`.
  3. Remove `token_chunk`/`heartbeat` carve-out — `QueueFull` on any event type triggers subscriber removal.
  4. Remove subscriber in `finally` block of the SSE generator.
- **Tests:** `tests/services/test_goal_service.py::test_disconnected_subscriber_pruned`
- **Verification command:** `uv run pytest tests/services/ -v`
- **Est. effort:** 3h

---

### P1-8: Rate Limiter Has TOCTOU Race (Non-Atomic Redis Ops)
- **Risk:** Three separate Redis operations (`zremrangebyscore` → `zcard` → `zadd`) with no atomicity. Under concurrent requests, multiple requests can both observe `count < limit` before either writes `zadd`. Effective free-tier limit is `30 + N_concurrent`.
- **Files:** `app/tenancy/rate_limiter.py:44–56`
- **Fix:** Replace 3-step ops with a single Lua script (same pattern as `_LUA_CHECK_AND_INCREMENT` in `cost.py`):
  ```lua
  redis.call('ZREMRANGEBYSCORE', key, 0, window_start)
  local count = redis.call('ZCARD', key)
  if count >= limit then return {0, 0} end
  redis.call('ZADD', key, now, member)
  redis.call('EXPIRE', key, window_ttl)
  return {1, limit - count - 1}
  ```
  Also add `asyncio.Lock` per tenant for the in-process fallback.
- **Tests:** `tests/tenancy/test_rate_limiter.py::test_concurrent_requests_respect_limit`
- **Verification command:** `uv run pytest tests/tenancy/ -v`
- **Est. effort:** 3h

---

### P1-9: Webhook Billing Secret Not Enforced When Env Var Is Unset
- **Risk:** When `RAZORPAY_WEBHOOK_SECRET` is not set, HMAC validation compares against an empty string — any HTTP client can forge billing webhook events, triggering plan upgrades or payment confirmations.
- **Files:** `app/api/billing.py` (webhook handler)
- **Fix:** Fail hard if webhook secret is unset when a webhook arrives:
  ```python
  webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
  if not webhook_secret:
      raise HTTPException(status_code=503, detail="Billing webhook not configured")
  ```
  Also add `RAZORPAY_WEBHOOK_SECRET` as a required setting in `core/config.py` when `RAZORPAY_KEY_ID` is set.
- **Tests:** `tests/costs/test_billing.py::test_webhook_rejects_when_secret_unset`
- **Verification command:** `uv run pytest tests/costs/ -v`
- **Est. effort:** 1h

---

## P2: Medium Priority (next sprint)

### P2-1: `AuditV3.verify_chain()` Returns False-Positive "valid" After Restart
- **Risk:** Empty in-memory buffer after process restart causes `verify_chain()` to return `{"valid": True, "records_checked": 0}` — a false assurance of integrity with zero records checked.
- **Files:** `app/governance/audit_v3.py:216–267`
- **Fix:** Return `{"valid": None, "records_checked": 0, "reason": "no_in_memory_records_use_db_verify"}` for empty buffers. The DB-backed `HashChainVerifier.verify()` should be the canonical path.
- **Tests:** `tests/governance/test_audit_v3.py::test_verify_chain_empty_buffer_returns_unknown`
- **Verification command:** `uv run pytest tests/governance/ -v`
- **Est. effort:** 1h

---

### P2-2: Bulkhead Registry Uses In-Process Semaphores (Multiplied by Replica Count)
- **Risk:** Free-tier tenants with `limit=2` can run `K × 2` concurrent goals with K replicas.
- **Files:** `app/reliability/bulkhead.py:37–54`
- **Fix:** Confirm `RedisBulkheadRegistry` exists and is the default in production; fall back to in-process only for local dev.
- **Tests:** `tests/reliability/test_bulkhead.py::test_cross_replica_concurrency_limit`
- **Est. effort:** 3h

---

### P2-3: Health Endpoint Does Not Verify Celery, Redis, or DB Liveness
- **Risk:** API returns `200 healthy` even when Celery workers are down, Redis is unreachable, or Postgres is unavailable. Goals silently pile up.
- **Files:** `app/api/system.py:16–27`, `app/main.py:465`
- **Fix:** Register three `HealthCheck` objects in `create_app()` lifespan:
  - `CeleryHealthCheck`: `celery_app.control.inspect(timeout=2).ping()` — fails if no workers respond.
  - `RedisHealthCheck`: `await redis.ping()` — fails if Redis unreachable.
  - `PostgresHealthCheck`: `await session.execute(text("SELECT 1"))` — fails if DB unreachable.
- **Tests:** `tests/api/test_health.py::test_health_reports_celery_down`
- **Est. effort:** 3h

---

### P2-4: CORS Configuration Allows All Methods and Headers
- **Risk:** `allow_methods=["*"]` and `allow_headers=["*"]` with `allow_credentials=True` is overly permissive; misconfigured `CORS_ORIGINS` makes CSRF trivial.
- **Files:** `app/main.py:1336–1342`
- **Fix:** Restrict to explicit method and header lists. Add startup validation rejecting `CORS_ORIGINS=["*"]` when `allow_credentials=True`.
- **Tests:** `tests/test_main.py::test_cors_rejects_wildcard_with_credentials`
- **Est. effort:** 1h

---

### P2-5: MFA Enrollment Returns Raw TOTP Secret in API Response
- **Risk:** Raw base32 TOTP secret in response body can be logged by observability tools or intercepted in transit, giving an attacker permanent MFA bypass.
- **Files:** `app/api/mfa.py:399, 434–443`
- **Fix:** Remove `"secret"` from the enrollment response; the `provisioning_uri` already embeds it.
- **Tests:** `tests/api/test_mfa.py::test_enroll_response_does_not_include_raw_secret`
- **Est. effort:** 0.5h

---

### P2-6: OAuth PKCE State Store Is Process-Local
- **Risk:** OAuth callback may land on a different replica than where the flow was started, causing `400 Invalid or expired OAuth state token`. Replay within 10-minute window is theoretically possible if attacker intercepts the state token.
- **Files:** `app/api/connectors.py:558–568`
- **Fix:** Store OAuth state in Redis with 600s TTL keyed as `oauth_state:{state}`.
- **Tests:** `tests/api/test_connectors.py::test_oauth_state_validated_cross_replica`
- **Est. effort:** 2h

---

### P2-7: Celery Worker Creates New Event Loop Per Task (Engine Bound to Dead Loop)
- **Risk:** `asyncpg` connection pools bind to the event loop at creation time. New event loop per task (`asyncio.new_event_loop()`) can cause `"Future attached to a different loop"` errors under load.
- **Files:** `app/scaling/tasks.py:174–180`, `app/db/session.py:39–46`
- **Fix:** Use `asyncio.run()` instead of manual loop lifecycle. Create a fresh session factory per task or use synchronous `psycopg3` for short-lived status updates.
- **Tests:** `tests/scaling/test_tasks.py::test_sequential_tasks_no_event_loop_error`
- **Est. effort:** 4h

---

### P2-8: Migration Chain Has Gaps (0049–0052 Missing) and No Startup Schema Check
- **Risk:** Missing revisions risk Alembic chain breakage on merge. No startup check means new code can start against an old schema, causing runtime column-not-found errors.
- **Files:** `app/db/migrations/versions/`, `app/main.py` (lifespan)
- **Fix:**
  1. Run `alembic history` to audit the gap; create placeholder revisions if needed.
  2. Add startup check: compare `alembic current` to `alembic heads`; fail fast if behind.
  3. Add `alembic upgrade head` as a required pre-deploy CI step.
- **Tests:** CI deploy script validates migration head before switching traffic.
- **Est. effort:** 2h

---

### P2-9: Marketplace In-Memory Fallback Is Cross-Tenant
- **Risk:** When DB is unavailable, `list_templates()` and `install()` use a shared process-level dict; templates published by Tenant A are visible to Tenant B.
- **Files:** `app/enterprise/marketplace_v2.py:1285`
- **Fix:** Key in-memory cache as `{tenant_id}:{template_id}`, or treat DB unavailability as a hard error for write operations.
- **Tests:** `tests/enterprise/test_marketplace.py::test_inmem_fallback_tenant_isolation`
- **Est. effort:** 2h

---

### P2-10: `goal_lineage` and `goal_attempts` Bypass `sqlalchemy_rls_context`
- **Risk:** Raw `SET LOCAL app.tenant_id = :tid` outside the context manager. Functionally correct when inside a transaction, but fragile — could miss a `BEGIN` in some code paths and expose cross-tenant rows.
- **Files:** `app/api/goals.py:701, 769`
- **Fix:**
  ```python
  async with sqlalchemy_rls_context(session, tenant.tenant_id):
      result = await session.execute(...)
  ```
- **Tests:** `tests/api/test_goals.py::test_goal_lineage_rls_isolation`
- **Est. effort:** 1h

---

### P2-11: Batch Goal Status Endpoint Is a Non-Functional Stub
- **Risk:** `GET /goals/batch/{batch_id}/status` returns a static message. Batch submitters have no server-side way to aggregate progress.
- **Files:** `app/api/goals.py:630–637`
- **Fix:** At `POST /goals/batch` time, store `{batch_id: [goal_ids], tenant_id}` in Redis with 24h TTL. `GET /batch/{id}/status` fetches goal statuses from DB and returns aggregated counts.
- **Tests:** `tests/api/test_goals.py::test_batch_status_returns_aggregated_counts`
- **Est. effort:** 3h

---

### P2-12: Goal Feedback Endpoint Has No DB Persistence
- **Risk:** `POST /goals/{id}/feedback` updates only an in-process calibration list. All feedback is lost on restart. Migration `0082_goal_feedback.py` already exists.
- **Files:** `app/api/goals.py:830–859`
- **Fix:** Insert into `goal_feedback` table using `sqlalchemy_rls_context`.
- **Tests:** `tests/api/test_goals.py::test_goal_feedback_persisted_to_db`
- **Est. effort:** 2h

---

### P2-13: GoalsListPage Has No Error State
- **Risk:** Network failure, 401, or 500 on the goal list query renders a blank table body silently. Users see nothing and don't know to retry.
- **Files:** `src/features/goals/GoalsListPage.tsx:95`
- **Fix:**
  ```tsx
  const { data, isLoading, isError, refetch } = useQuery({ ... });
  if (isError) return <ErrorPanel message="Failed to load goals" onRetry={refetch} />;
  ```
- **Tests:** `npm run test -- GoalsListPage` (vitest) — mock `useQuery` to return `isError=true`; assert error panel renders.
- **Est. effort:** 1h

---

### P2-14: Onboarding Not Auto-Triggered for New Users
- **Risk:** New tenants land on the dashboard with no agents or goals and no guidance. Activation rate will be low.
- **Files:** `src/features/dashboard/DashboardPage.tsx`
- **Fix:** Check `agents.length === 0 && goals.length === 0` on mount; redirect to `/onboarding`. Set `localStorage["av-onboarding-complete"]` flag after wizard completion.
- **Tests:** Playwright E2E — new user logs in, assert redirect to `/onboarding`.
- **Est. effort:** 2h

---

### P2-15: BillingPage Exists but Has No Route in `App.tsx`
- **Risk:** `BillingPage.tsx` (194 lines) is inaccessible. Users cannot manage their billing plan or view invoices.
- **Files:** `src/app/App.tsx`, `src/features/settings/SettingsPage.tsx`
- **Fix:**
  1. Add `<Route path="settings/billing" element={<BillingPage />} />` to `App.tsx`.
  2. Add a "Billing" tab to `SettingsPage.tsx`'s `SETTINGS_TABS` array.
- **Tests:** `npm run typecheck` passes; Playwright — navigate to `/settings/billing`, assert page renders.
- **Est. effort:** 1h

---

### P2-16: `connector_usage` Uses `SET LOCAL` Outside Explicit Transaction
- **Risk:** `SET LOCAL` without `session.begin()` may not be transaction-scoped; RLS GUC could reset between the `SET LOCAL` and the subsequent SELECT.
- **Files:** `app/api/connectors.py:941–976`
- **Fix:**
  ```python
  async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant.tenant_id):
      rows = (await session.execute(...)).fetchall()
  ```
- **Tests:** `tests/api/test_connectors.py::test_connector_usage_rls_isolation`
- **Est. effort:** 1h

---

## P3: Low Priority (backlog)

### P3-1: Vault PBKDF2 Uses Fixed Public Salt
- **Risk:** All AgentVerse deployments using a weak master key produce the same Fernet key — rainbow table attacks can be shared across instances.
- **Files:** `app/providers/vault.py:134–144`
- **Fix:** Store a per-deployment random 32-byte salt in a `vault_metadata` DB record; read it on vault init.
- **Est. effort:** 4h

---

### P3-2: `/integrations/` Prefix Bypass Is Too Broad
- **Risk:** Any future endpoint under `/integrations/` will be unauthenticated by default unless the developer explicitly adds auth.
- **Files:** `app/tenancy/middleware.py:83`
- **Fix:** Remove prefix bypass; add `Depends(verify_slack_signature)` per endpoint explicitly.
- **Est. effort:** 2h

---

### P3-3: Docker Compose Default Hardcoded Credentials
- **Risk:** `agentverse:agentverse` as Postgres credentials in `docker-compose.yml`. Production check only logs, doesn't fail.
- **Files:** `infra/docker-compose.yml:10–15`, `app/core/config.py:185`
- **Fix:** Hard-fail (not just log) in `get_settings()` when production + default password detected.
- **Est. effort:** 1h

---

### P3-4: Frontend JWT Refresh Token Persisted to `sessionStorage`
- **Risk:** Any stored XSS vulnerability on the same origin can exfiltrate the refresh token from `sessionStorage["av-auth"]`.
- **Files:** `src/stores/auth.ts:63–78`
- **Fix:**
  ```typescript
  partialize: (state) => ({ ...state, refreshToken: "" }),
  ```
- **Est. effort:** 1h

---

### P3-5: Celery Worker TenantContext Not Validated Against DB
- **Risk:** Crafted task injected into Redis queue sets `tenant_id` to any value; worker executes it without verifying the tenant exists.
- **Files:** `app/scaling/tasks.py:448–452`
- **Fix:** Add `await _tenant_exists(db_factory, tenant_id)` check before executing.
- **Est. effort:** 2h

---

### P3-6: Tab Bars on GovernancePage and KnowledgePage Lack ARIA Semantics
- **Risk:** Screen reader users cannot identify or navigate tab bars.
- **Files:** `src/features/governance/GovernancePage.tsx:61`, `src/features/knowledge/KnowledgePage.tsx:36`
- **Fix:** Apply the ARIA tablist pattern from `GoalDetailPage.tsx:709` — add `role="tablist"`, `role="tab"`, `aria-selected`, `aria-controls`, keyboard navigation.
- **Est. effort:** 3h

---

### P3-7: Mobile Table Layouts Break on GoalsListPage, AgentsListPage, GovernancePage
- **Risk:** Three most-used pages are unusable on viewports < 640px.
- **Files:** `src/features/goals/GoalsListPage.tsx:257`, `src/features/agents/AgentsListPage.tsx:264`, `src/features/governance/GovernancePage.tsx:707`
- **Fix:** Add responsive card layout for `sm:hidden` viewports.
- **Est. effort:** 4h

---

### P3-8: Memory Provenance Truncated Without Tooltip
- **Risk:** `r.source.slice(0, 12)…` makes source tracing impossible.
- **Files:** `src/features/memory/MemoryExplorerPage.tsx:429`
- **Fix:** Wrap in `<Tooltip content={r.source}>`.
- **Est. effort:** 0.5h

---

### P3-9: AgentCreatePage Not in Mission Control Theme
- **Risk:** White/light page after navigating from dark-themed AgentsListPage is jarring.
- **Files:** `src/features/agents/AgentCreatePage.tsx:53`
- **Fix:** Wrap in `<MissionControlLayout>`. Replace generic tokens with `bg-panel-graphite/border-neural-violet/20`.
- **Est. effort:** 1h

---

### P3-10: Sidebar Navigation Cognitive Overload
- **Risk:** 50+ items in 9 groups overwhelms new users.
- **Files:** `src/components/ui/Sidebar.tsx`
- **Fix:** Default-collapse all groups except "Mission Control". Promote search. Add `?` shortcut hint for `CommandPalette`.
- **Est. effort:** 2h

---

### P3-11: WorkflowBuilder Unusable on Mobile With No Fallback
- **Files:** `src/features/workflow-builder/WorkflowBuilderPage.tsx:1001`
- **Fix:** Detect `window.innerWidth < 768` and render `<MobileBlocker>` explaining desktop requirement.
- **Est. effort:** 1h

---

### P3-12: Knowledge API Under-Tested (27 Routes, 4 Test Files)
- **Fix:** Add test coverage for: upload, chunk retrieval, hybrid search, graph search, citation tracking, delete, and collection management.
- **Est. effort:** 6h

---

### P3-13: ScopeEnforcementMiddleware Should Be Audited to Fail Closed
- **Files:** `app/auth/scope_enforcement.py`, `app/main.py:1318`
- **Fix:** Full audit to verify missing scope definitions default to `deny`, not `allow`.
- **Est. effort:** 2h

---

## Security Fixes Summary

All security findings from the 2026-07-06 security audit, prioritized:

| Priority | ID | Finding | Effort |
|---|---|---|---|
| P0 | SEC-CRIT-1 | SSRF in `test_connector` generic fallback | 2h |
| P0 | SEC-HIGH-7 | Real credentials in `.env` — rotate immediately | 3h |
| P0 | SEC-HIGH-8 | SSRF guard fails open on DNS error | 1h |
| P0 | SEC-HIGH-2 | SSRF in `discover_tools` | 1h |
| P0 | SEC-HIGH-5 | No rate limiting on `/tenants/signup` | 2h |
| P0 | SEC-HIGH-3 | Admin key timing attack | 0.5h |
| P1 | SEC-HIGH-4 | MFA TOTP replay + session store process-local | 3h |
| P1 | SEC-HIGH-6 | Vault dev key used silently in staging | 1h |
| P1 | SEC-MED-10 | OAuth tokens stored in plaintext | 2h |
| P2 | SEC-MED-12 | AuditV3 verify_chain false positive after restart | 1h |
| P2 | SEC-MED-9 | MFA enrollment returns raw TOTP secret | 0.5h |
| P2 | SEC-MED-11 | OAuth PKCE state store process-local | 2h |
| P2 | SEC-MED-14 | CORS allows all methods and headers | 1h |
| P2 | SEC-MED-16 | `/integrations/` prefix bypass too broad | 2h |
| P2 | SEC-MED-15 | connector_usage SET LOCAL outside transaction | 1h |
| P3 | SEC-MED-13 | Vault PBKDF2 fixed public salt | 4h |
| P3 | SEC-LOW-17 | Docker default hardcoded credentials | 1h |
| P3 | SEC-LOW-18 | Frontend refresh token in sessionStorage | 1h |
| P3 | SEC-LOW-19 | test_connector forwards auth headers to new URLs | 1h |
| P3 | SEC-LOW-20 | Celery TenantContext not validated against DB | 2h |
| P3 | SEC-LOW-21 | ScopeEnforcementMiddleware — audit for fail-closed | 2h |

---

## Architecture Fixes Summary

All architecture risks from the 2026-07-06 scalability review, prioritized:

| Priority | ID | Finding | Effort |
|---|---|---|---|
| P1 | ARCH-CRIT-1 | LangGraph checkpointer MemorySaver in Celery | 4h |
| P1 | ARCH-CRIT-2 | In-memory goal state diverges across replicas | 8h |
| P1 | ARCH-HIGH-3 | Rate limiter non-atomic TOCTOU | 3h |
| P1 | ARCH-HIGH-6 | Per-plan Celery queue routing never applied | 2h |
| P1 | ARCH-HIGH-7 | SSE dead subscriber memory leak | 3h |
| P2 | ARCH-HIGH-4 | Bulkhead in-process semaphores × replica count | 3h |
| P2 | ARCH-HIGH-5 | Redis single-node SPOF — no Sentinel/Cluster | 6h |
| P2 | ARCH-MED-8 | Celery new event loop per task vs engine singleton | 4h |
| P2 | ARCH-MED-9 | Health endpoint missing Celery + Redis + DB checks | 3h |
| P2 | ARCH-MED-10 | Migration chain gaps, no startup schema check | 2h |

> **Note:** Redis HA (ARCH-HIGH-5) is flagged P2 rather than P1 because the infrastructure work
> (Sentinel/Cluster config) is separate from the code changes, but it should be tracked in the
> infra runbook and completed before production scale-out beyond 2 replicas.

---

## Test Coverage Gaps

Critical missing test coverage identified across all audits:

| Subsystem | Current Files | Required Coverage | Priority |
|---|---|---|---|
| `skills_runtime` | 1 | DB write/read cycle, cross-tenant isolation, execution history pagination | P0 |
| `memory_v2` (new) | 0 | Persistence round-trip, concurrent write safety, conflict resolution | P0 |
| `billing` | 1 (shared with costs) | Webhook signature validation, missing secret rejection | P1 |
| `agent checkpointing` | 0 | Celery worker crash + resume from checkpoint | P1 |
| `SSE cross-replica` | 0 | Event delivery across GoalService instances sharing Redis | P1 |
| `MFA distributed` | partial | TOTP replay blocked across replicas | P1 |
| `SSRF` | partial | All three SSRF vectors blocked | P0 |
| `knowledge API` | 4 | Upload, hybrid search, graph search, citation, delete | P3 |
| `compliance` | 1 | GDPR export path | P2 |
| `persistence loop` | 1 | Goal persistence loop recovery | P2 |

---

## Launch Readiness Checklist

- [ ] **P0 items all resolved** (10 items listed above)
- [ ] **P1 items all resolved** (9 items listed above)
- [ ] Real API keys rotated — OpenAI, Atlassian Jira, Atlassian Confluence
- [ ] `gitleaks` CI step added and passing — `npx gitleaks detect --source . --no-git`
- [ ] SSRF protection verified on all 3 vectors (test_connector, discover_tools, ssrf_guard DNS)
- [ ] Tenant isolation tested — SSE goal ownership check, memory_v2 RLS, skills RLS
- [ ] Rate limiting verified — `/tenants/signup` returns 429 at 6th request/minute
- [ ] Admin key timing attack fixed — `hmac.compare_digest` in use
- [ ] MFA TOTP replay blocked — Redis-backed replay cache in place
- [ ] OAuth tokens encrypted at rest — vault wired to OAuthFlowManager
- [ ] LangGraph Redis checkpointer in Celery workers — not MemorySaver
- [ ] Celery per-plan queue routing applied — enterprise goals go to `goals.enterprise`
- [ ] Health checks complete — Celery + Redis + DB liveness in `/health` response
- [ ] Backend test suite passing: `uv run pytest tests/ -q -m "not integration and not slow"`
- [ ] Frontend typecheck passing: `npm run typecheck` — 0 errors
- [ ] Playwright smoke passing: `npx playwright test --project=smoke-live`
- [ ] Security tests passing: `uv run pytest tests/security/ -v`
- [ ] Tenant isolation tests passing: `uv run pytest tests/tenancy/ -v`
- [ ] No secrets in codebase: `npx gitleaks detect --source . --no-git` returns 0 findings
- [ ] CORS origins locked to production domain (not `["*"]`)
- [ ] Docker compose default credentials changed or hard-fail on production startup
- [ ] Migrations applied in order: `alembic current` matches `alembic heads`
- [ ] Billing webhook secret validated — `RAZORPAY_WEBHOOK_SECRET` required when Razorpay is configured
- [ ] BillingPage route registered in `App.tsx`
- [ ] GoalsListPage error state implemented
