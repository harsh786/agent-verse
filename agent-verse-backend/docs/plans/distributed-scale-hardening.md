# Distributed-Scale & Persistence Hardening

> Core-platform audit: is anything stored only in memory, does the platform behave
> correctly on multiple pods, does every feature scale to millions of rows without
> hallucination, and does agent self-improvement actually work? Five parallel audits
> (persistence, multi-pod, DB-scale, self-improvement, frontend). Findings are
> deduplicated and cross-referenced — an in-memory store is often a persistence **and**
> a multi-pod **and** a scale defect at once.

Legend: `[ ]` todo · `[~]` doing (delegated/in-flight) · `[x]` done. Sources:
P=persistence, M=multi-pod, S=scale, I=self-improvement, F=frontend.

**DONE (committed):** X1 auth-cross-pod ✓ · X3 mission at-most-once ✓ · X4 trigger
fail-closed ✓ · X5 goal/audit mirrors + goal pagination ✓ · X7 self-improvement
worker wiring + auto-apply-on ✓ · X9 unbounded-query bounds ✓ · X10 connected-
services DB ✓ · X11 usage SQL rollup ✓ · X12-indexes ✓ · X13 rate-limit
observability ✓ · X14 frontend pagination ✓ · X18 SQL aggregation ✓.

**Non-issue (verified false positive):** X8-`permission_matrix` — `set_rule` has no
callers; `_rules` only holds deterministic code-seeded defaults (identical every
pod/restart); real per-agent tool perms live in a DB table. No fix needed.

**REMAINING (a read-path subagent failed on an account rate limit mid-task):**
- **X6** rag/engine vector rework — REVERTED (it regressed a retrieval-widening
  test); redo carefully: the 2048-dim halfvec cast (X6a) + BM25 corpus gating (X6b).
- **X12-ANN** `long_term_memory` halfvec HNSW index + its query cast (pair together).
- **X2** agent-credential store → DB (low severity: resolve isn't wired to auth, so
  keys don't authenticate; created keys are lost on restart).
- **X8** magentic_human_review / custom_roles / sub_tenant (minimal usage), X15-X17
  (per-pod caches, RPA sessions, other in-memory app.state), and backend pagination
  params (/agents, /schedules, /admin search, /governance/audit outcome+q — surfaced
  by the frontend work).

Prod topology: **3 backend + 3 worker replicas; beat = 1 replica** (helm values). So the
risk is *overlapping runs across the 3 workers*, not multiple beats.

---

## CRITICAL — security / correctness / data-loss on multi-pod or OOM at scale

- [ ] **X1. API-key auth is per-pod; revocation never propagates (SECURITY).** [M-C1,P]
  `TenantService` has no Redis and only a one-time `sync_from_db()` at startup
  (`services/tenant_service.py:197-244`, wired `main.py:1281,1292`); `sync_from_db`
  selects only `is_active=True` (`:570`). Revoke/compromise a key on pod A → pods B/C
  keep authenticating it until restart; new keys 401 on pods that didn't mint them;
  `invalidate_tenant_cache` deletes the wrong Redis key (`:707`). **Fix:** Redis pub/sub
  (or short-TTL revalidation from DB) on key create/revoke; overwrite deactivated keys.

- [ ] **X2. `AgentCredentialStore` is a pure in-memory singleton (SECURITY).** [P-1]
  `auth/agent_credentials.py:143` (`_keys`/`_agent_keys` dicts, no DB). Agent API keys
  lost on restart; a revoked key stays valid on other pods. **Fix:** Postgres table +
  Redis revocation propagation (mirror the api_keys pattern).

- [ ] **X3. `fire_due_org_mission_schedules` double-launches autonomous missions.** [M-C2]
  `scaling/tasks.py:754,772` — no `@beat_task_guard`, advances `next_fire_at` in a
  non-conditional UPDATE (no `FOR UPDATE SKIP LOCKED`/CAS), dispatches a fresh
  `mission_id` with no idempotency key. A run >60s overlaps two workers → two missions
  both execute autonomously. **Fix:** beat guard + atomic claim (`SKIP LOCKED` /
  conditional UPDATE) + stable idempotency key.

- [ ] **X4. Trigger dedup fails OPEN; broadcast consumers run in every pod.** [M-H1,H2,M4]
  `triggers/dispatcher.py:266`, `goal_service.py:2500` — the only cross-pod guard is a
  Redis `SET NX ex=60` that returns "not duplicate" when Redis is down/errors, and goal
  creation doesn't enforce the key. `supervisor.py`+`consumers/event.py` `psubscribe`
  broadcast, so N pods each dispatch. Redis blip → N-fold double goal execution. **Fix:**
  durable idempotency at goal creation (fail-safe), stable interval keys.

- [ ] **X5. In-memory table mirrors → startup OOM at scale.** [S-1,2,5]
  `services/goal_service.py:3927` (`sync_from_db` loads all tenants' 24h goals into
  `self._goals`) + `:3031` (`list_goals` sorts the whole dict, no limit); `governance/
  audit.py:270` loads the entire append-only `audit_log`; `api/memory_v2.py:60` hydrates
  all of `long_term_memory`. At ~1M rows → startup OOM + O(N) per request. **Fix:** delete
  the RAM mirrors; serve reads from SQL with keyset pagination.

- [ ] **X6. Two vector paths silently degrade to full seq scans.** [S-3,4]
  `rag/engine.py:313` — halfvec-HNSW cast is special-cased for `embedding_dim==3072`
  only, so a **2048-dim** collection (the configured NVIDIA nemotron dim!) never hits its
  `halfvec(2048)` HNSW → exact full scan per query. `engine.py:452,585` — app-side BM25
  walks the whole collection **twice** per query. **Fix:** extend cast to `in (2048,3072)`;
  cap/replace app-side BM25 with the candidate-union.

## HIGH — feature silently resets, wrong at scale, or self-improvement inert

- [ ] **X7. Agent self-improvement is inert on the worker path that runs goals.** [I-1,3,5,6]
  Goals execute in the Celery worker (`scaling/tasks.py:1827` builds `AgentGraph` with no
  `app_state`, no `reflexion_service`, no DB-loaded PromptOptimizer). So: SelfOptimizerV2
  never fires (+ `ENABLE_SELF_IMPROVEMENT_AUTO_APPLY` off by default); PromptOptimizer
  variants never `load_from_db` in the worker → default prompts always; reflexion lessons
  + `ExecutionMemory` failures are *written* to DB but recalled only from an in-process
  dict (`execution.py:61`, `load_from_db` loads success only) → fresh pod replans blind.
  **Works:** LTM + winning-plan ExecutionMemory + Eval scoring (DB-backed, recalled).
  **Fix:** inject app_state/optimizer + reflexion into the worker graph; `load_from_db`
  in `worker_init`; DB-backed failure recall.

- [ ] **X8. Security/approval state in-memory, not cross-pod.** [P-5,6, M-H3]
  `permission_matrix` tool-permission rules (`main.py:2423`, reset on restart),
  `magentic_human_review` pending approval tokens (`main.py:2383`, invisible cross-pod),
  per-tenant skill-disable (`skills_runtime/executor.py:90`, guardrail bypass on other
  pods), custom RBAC roles (`auth/custom_roles.py:182`), sub-tenant hierarchy
  (`org/advanced_services.py:579`). **Fix:** persist + propagate.

- [ ] **X9. Unbounded core-path queries.** [S-7,8,9,10]
  `chat/repository.py:158` `list_messages` / `:76` `list_sessions`, `event_store.py:88`
  `list_events` (feeds SSE replay), `memory/long_term.py:97` `list_all` — all `ORDER BY`
  with **no LIMIT**, returned as bare lists. **Fix:** LIMIT + keyset cursor.

- [ ] **X10. `_services_api` connection records still in-memory.** [P-3]
  `chat/router.py:34` — status honesty was fixed earlier, but records still vanish on
  restart / diverge per pod. **Fix:** persist to DB (MCPRegistry or a table).

- [ ] **X11. `usage_service.get_usage_summary` aggregates the in-memory buffer only.** [S-12]
  `services/usage_service.py:121` never queries `usage_records`; `/billing/usage` reads
  ≈0 after each flush. **Fix:** SQL `GROUP BY` rollup over `usage_records`.

- [ ] **X12. Missing hot composite indexes + long_term ANN index.** [S-11,15,16,22]
  `goals(tenant_id, created_at)`, `goal_events(goal_id, created_at)`,
  `usage_records(tenant_id, period_start)`, and an HNSW/halfvec index on
  `long_term_memory.embedding` (KNN is a full scan today). **Fix:** one migration.

- [ ] **X13. Rate limiter / dedup / bulkhead fail OPEN to per-pod on Redis error.** [M-M3,M5,L4]
  `tenancy/rate_limiter.py:107` → in-process window on any Redis `eval` error (3× global
  limit across pods). **Fix:** fail-safe posture / short-circuit, not silent per-pod.

- [ ] **X14. Frontend: most-used lists fetch whole tables, paginate in the browser.** [F-1..5,10]
  Agents (`client.ts:423` no params) and Goals (`GoalsListPage.tsx:105`, +5s full
  refetch) fetch ALL rows then filter/sort/slice client-side; Chat sessions, Workflows,
  Schedules, Admin (`limit:200`) likewise; Audit filters apply only to the current page
  (`AuditExplorerPage.tsx:131` — wrong results). **Fix:** server-side page_size/cursor +
  search; back-port the Marketplace/Artifacts/Memory pattern already in the repo.

## MEDIUM — degradations, admin inconsistency, deep-offset

- [ ] **X15. Per-pod caches without cross-pod invalidation.** [M-M1,L1,L2]
  policy list/delete registry (`api/governance.py:96`), SemanticCache L1, feature flags.
- [ ] **X16. RPA browser sessions per-pod in-process** (`rpa/session_manager.py:75`). [M-M2]
- [ ] **X17. More in-memory `app.state`/singletons never swapped.** [P-9..17]
  strategy_context_store, learning_experiment_service (A/B assignments), marketplace v1,
  dept_memory, KG access-control overrides, org digital-twin (`set_db` never called),
  workflow `set_variable_step` global store (cross-tenant smell), decision-intelligence
  version store.
- [ ] **X18. In-memory aggregation + deep OFFSET + per-goal unbounded sub-lists.** [S-6,13,17,19,20,21,23]
  `/insights/benchmarks` cross-tenant full scan + 12 percentile sorts; `agent_metrics`
  no SQL path; deep OFFSET in audit/org lists; per-goal traces/attempts unbounded.

## Testing / rollout
Each fix ships with a test where logic changed and a live check against :8001 where
observable. Backend: `uv run pytest`, ruff, mypy. Frontend: `npm run test`, tsc, eslint.
Migrations: `uv run alembic upgrade head`. Behaviour-changing defaults (self-improvement
auto-apply; trigger fail-open→safe) are called out and kept flag-gated unless approved.

## Explicitly credited as already-correct (do NOT touch)
SSE cross-pod (Redis pub/sub), HITL cross-replica (Redis BLPOP + DB), goal execution
at-most-once (Redis lock + `FOR UPDATE`), PolicyEngine eval invalidation (pub/sub both
sides), org-brain/civilization/audit-flusher tick locks, RedisCostController (atomic Lua),
knowledge-chunk ANN + RRF fusion + batched event counts, and the many correctly-cursored
endpoints (coordination transcript, approvals history, memory, knowledge docs, invoices).
