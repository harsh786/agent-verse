# AgentVerse World-Class Master Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan phase-by-phase. This is a **master plan**: Phase 0 is directly executable task-by-task; Phases 1–12 are scoped tracks that each get their own detailed task plan (`docs/superpowers/plans/2026-07-XX-<track>.md`) before execution, per the writing-plans scope rule (one plan per subsystem).

**Goal:** Take AgentVerse from "broad, largely-implemented platform with silent defects" to a world-class, million-scale, multi-tenant autonomous-agent SaaS — covering hallucination reduction, token/cost/latency optimization, world-class RAG, local/custom providers, intelligent model routing, a Skills platform, deep domain agents, SaaS identity/billing/admin, security guardrails, and self-improvement.

**Architecture:** Keep the existing FastAPI + LangGraph + Celery + Postgres(pgvector) + Redis backbone. Fix inert wiring first (P0), then build each capability as a track that follows the established `create_app()` → `app.state` → lifespan-upgrade pattern. New primitives introduced: `User` (identity), `Skill` (composable instruction packs), unified retrieval engine (BM25+vector+RRF+rerank), experiment registry (one self-improvement control plane), platform-admin plane.

**Tech Stack:** Python 3.12 / FastAPI / LangGraph / SQLAlchemy 2 async / Alembic / Celery / Redis / Postgres + pgvector + tsvector / React 19 / Vite / TanStack Query / Playwright / Vitest.

> **Two audits back this plan.** *Audit 1* (feature mapping, 5 agents) produced the capability tracks and the 14 wiring defects. *Audit 2* (adversarial bug hunt, 6 agents across agent-loop, services/eventing, API/tenancy, DB/Celery/infra, frontend, governance/MCP/providers) produced the **Defect Registry** in Part B — ~75 additional verified defects including 7 CRITICAL (a live HITL bypass on destructive steps, fleet-wide secret loss on key rotation, RLS-blocked maintenance that voids GDPR deletes, connection-pool exhaustion) and ~20 HIGH (4 SSRF vectors, cross-tenant IDOR, cost counter-poisoning, cross-replica HITL that never unblocks). These reorder the work: **security- and safety-critical fixes (Phase 0A) precede everything, including the wiring fixes.**

## Global Constraints

- Backend: `uv run` everything; ruff line-length 100, target py312; mypy strict; pytest `filterwarnings=error`; tests mirror `tests/<package>/`; markers `integration`, `slow`.
- Migrations are forward-only and additive; never edit deployed revisions (71 exist; next is 0072+).
- New services constructed in `create_app()`, bound to `app.state`, DB/Redis-upgraded in `lifespan` — follow the two-phase wiring pattern.
- All new tables carry tenant RLS policies (`app/db/rls.py` pattern) **and a test asserting isolation**, `created_at`/`updated_at`, UUID PKs.
- Frontend: no new component libraries; extend `src/components/ui/` and Tailwind tokens.
- No secrets in code; provider keys via env or the Fernet vault (`app/providers/vault.py`).
- **Fail-closed default:** every new security/safety check (authz, guardrail, budget, rate-limit, RLS context) fails closed on error or missing dependency — never silently passes. Replace `except Exception: pass` around correctness signals with logged, typed handling.
- **Standard deliverable per track (the "world-class bar"):** every capability track ships (1) **backend** — typed services on `app.state`, RLS + fail-closed authz, structured errors with correlation IDs (never `str(exc)` to clients), OpenTelemetry spans, Prometheus metrics; (2) **world-class UI** — a feature slice under `src/features/`, semantic design tokens, WCAG 2.2 AA (axe-clean), loading/empty/error states, optimistic updates with rollback; (3) **tests** — unit ≥80% line coverage on new code, integration tests for DB/RLS/Redis paths, and a **Playwright e2e spec** in `agent-verse-frontend/e2e/` plus an axe a11y assertion; (4) **load profile** — a k6/locust scenario for any endpoint on the goal-submission or streaming hot path.

---

## Part A — Codebase Audit Summary (what the analysis found)

Five parallel deep-explorations of the monorepo produced these verdicts. Full details are embedded in each phase below.

| Area | Verdict | Headline finding |
|---|---|---|
| Agent loop / hallucination | Strong prompts + verifier; inconsistent enforcement | Cross-model verification only active in the Celery path; verifier caching added without a skip-guard; no post-hoc grounding check |
| Token cost (uncommitted work) | **Written but largely unwired** | `LLMResponseCache` never injected (dead); prefetch `warm()` signature mismatch (silent no-op); `set_override()` doesn't exist; worker path misses semantic cache |
| RAG / knowledge | Mature pipeline, statistically unsound fusion | Ingestion writes legacy `documents` table while search reads `knowledge_chunks_{dim}` (retrieval misses new data); `federated_search` calls nonexistent `store.search()`; no rerank/BM25/RRF |
| Memory / evals / self-improvement | Genuinely advanced (Bayesian A/B, golden tasks) | LLM judge grades the **goal text**, not agent output; 3 uncoordinated optimizers mutate the same agents |
| Tenancy / auth | Deep tenancy, **no User entity** | SCIM writes to a phantom `users` table; 1 SSO login = 1 new tenant; 2 disjoint RBAC systems; no-roles keys bypass scope enforcement |
| Admin / billing | Absent | No platform-operator plane; no billing/metering; FREE plan limits are more permissive than STARTER (inverted) |
| Marketplace / templates | V2 DB-backed with security review, ~29 domain templates | V1/V2 duplication; templates inline prompts (no Skill primitive); MetaAgentPlanner ungrounded in the real connector catalog |
| MCP tools | 320+ connectors, self-healing calls | **Full tool list + full JSON schemas injected into every LLM call**; `CapabilitySearch` exists but is not wired into the loop |
| A2A / civilization | Most mature area; real A2A + org runtime | Single AgentCard (no directory); org semantics siloed in civilization; debate consensus is unweighted vote-count |
| Workflows / scheduling | Real DAG executor + 10 trigger types | No RAG node; `decision`/`loop`/`parallel` nodes are visual-only; DAG steps call MCP with empty `server_id` |
| Code/site generation | Docker-sandboxed interpreter + artifacts | Unsandboxed subprocess fallback; no site scaffolding/preview hosting |
| Frontend | 38 features, 76 unit + 44 e2e specs, high polish | No a11y/visual-regression layer; undocumented bespoke design system; duplicate test locations |

**User-ask → phase mapping** (your numbered list):

| Ask # | Topic | Phase |
|---|---|---|
| 1 | Hallucination reduction | P0 + Phase 3 |
| 2 | Token cost optimization | P0 + Phase 2 |
| 3 | Vector-DB-less operation | Phase 4 |
| 4 | Custom providers (Ollama/local) | Phase 5 |
| 5 | World-class RAG (goal-aware) | Phase 4 |
| 6 | Intelligent per-tenant model routing (plan/exec/verify) | P0 + Phase 5 |
| 7 | Default + custom Skills | Phase 6 |
| 8, 20, 23 | Domain/business use cases (law firm, e-commerce, education…) | Phase 7 |
| 9 | Agent interaction outside AgentVerse | Phase 8 |
| 10, 21 | Marketplace default agents + template registries | Phase 7 |
| 11 | Site/code building (lovable/emergent-style), Agent Lab | Phase 9 |
| 12 | Gmail login | Phase 1 |
| 13, 14 | SaaS tenants-with-users + global product | Phase 1 |
| 15 | Admin dashboard | Phase 1 |
| 16 | Golden data sets | Phase 11 |
| 17 | Self-improvement (agents, RPA, RAG) | Phase 11 |
| 18 | Agent civilization + scheduling | Phase 8 |
| 19 | Workflow builder with RAG step | Phase 9 |
| 22 | Whole-org architecture (CEO/CTO/HR agents) | Phase 8 |
| 24 | Prompt-injection/jailbreak/exfiltration guardrails | Phase 10 |
| 25 | Latency reduction at million scale | Phase 2 |
| — | World-class UI + tests, million-traffic | Phase 12 (+ Phase 2 infra) |

**Sequencing:** P0 → (Phase 1 ∥ Phase 2 ∥ Phase 4) → (Phase 3, 5, 6) → (Phase 7, 8, 9, 10, 11) → Phase 12 continuously. Phases marked ∥ are independent and can run as parallel work streams.

---

## Part B — Phase 0: Defect Registry & Stabilization

Audit 2 verified ~75 net-new defects on top of the 14 wiring defects from Audit 1. Below is the **full registry** (severity-ranked, with file references and the phase/task that fixes each), followed by the executable task breakdown: **Phase 0A (Critical — safety & security, fix first)**, **Phase 0B (Correctness wiring — the original 14)**, and **Phase 0C (High-severity hardening)**. Medium/low findings are folded into the capability track that owns their subsystem (noted in the registry).

Each task is one bite-sized TDD cycle: write a failing test proving the defect, fix minimally, run `uv run pytest <file> -v`, then `uv run ruff check . && uv run mypy app`, then commit. Frontend fixes run `npm run test`/`npm run test:e2e`.

### B.1 — Defect Registry (severity-ranked)

**CRITICAL — safety, security, or data-loss; fix before any feature work (Phase 0A):**

| # | Defect | Location | Fix in |
|---|---|---|---|
| C1 | **HITL is a no-op in `AgentLoop._execute`** — high-risk steps (`delete/deploy/prod/destroy`) request approval then auto-proceed without awaiting it; live via the Celery `AgentLoop` fallback | `app/agent/loop.py:259-272` | 0A.1 |
| C2 | **Vault key rotation destroys all secrets** — mismatched salt/encoding derivation + `self._fernet` never reassigned → connector creds & OAuth tokens undecryptable fleet-wide after rotation | `app/providers/vault.py:196-236` | 0A.2 |
| C3 | **Maintenance tasks silently affect 0 rows** — `detect_stuck_goals`, `execute_retention_policy` (GDPR!), `expire_hitl_approvals`, `run_goal_dlq` run cross-tenant SQL with no `app.tenant_id` GUC under `FORCE ROW LEVEL SECURITY`; all report success | `app/scaling/tasks.py:1608,1656,1695,345` | 0A.3 |
| C4 | **Cost counter poisoning** — `check_and_record` increments before checking budget and never refunds rejected/failed calls → tenant blocked forever; atomic Lua `try_record_and_check` exists but is unwired | `app/governance/cost.py:146-170,246-257` | 0A.4 |
| C5 | **AsyncEngine leak** — `run_goal` creates a new engine+pool per DB op (4–10/goal), never disposed → pgbouncer/Postgres connection exhaustion | `app/scaling/tasks.py:433,451,466,488` · `session.py:18-35` | 0A.5 |
| C6 | **Cross-replica HITL approval never unblocks the agent** — `approve()` sets only the in-process `asyncio.Event`, never `publish_resolution`; waiter on another replica times out despite a real approval (found independently by 2 agents) | `app/governance/hitl.py:243-270,355-378` · `graph.py:1246,1271` | 0A.6 |
| C7 | **`write_high`/step-level approval requested but never awaited or executed** in the graph — high-risk write replaced by a placeholder string; approval orphaned even after human approves | `app/agent/graph.py:1804-1858` | 0A.1 |

**HIGH — exploitable or corrupting; Phase 0C (or the security track where noted):**

| # | Defect | Location | Fix in |
|---|---|---|---|
| H1 | **SSRF ×4** — attacker-controlled URLs fetched with no private-IP/metadata block: A2A `callback_url`, `/ingest/url`, `/ingest/rpa-url`, tenant-registered MCP `base_url` | `a2a.py:124-138` · `knowledge.py:744,1305` · `mcp/client.py:56-61` | 0C.1 + Ph10 |
| H2 | **Cross-tenant IDOR** — `GET /a2a/tasks/{id}` and DB-down list fallback return any tenant's task (no tenant filter, no RLS ctx) | `app/api/a2a.py:102-121,264-292` | 0C.2 |
| H3 | **IP allowlist bypass** via spoofable `X-Forwarded-For` (no trusted-proxy validation) | `app/auth/scope_enforcement.py:346-352` | 0C.2 |
| H4 | **Rate limiter & IP allowlist fail OPEN** when Redis is down | `tenancy/middleware.py:183-201` · `scope_enforcement.py:264` | 0C.2 |
| H5 | **Whole routers lack scope enforcement** — `/a2a`, `/artifacts`, `/costs`, `/memory`, `/collab`, `/rpa`, `/perception`, `/tools`, `/enterprise`, `/guardrails` unregistered → viewer key can write/delete | `app/auth/scope_enforcement.py:36-90` | 0C.2 + Ph1 |
| H6 | **Blocking sync `httpx.Client` in async execute path** — up to 24s event-loop stall per Jira JQL step, freezing all concurrent goals on the worker | `app/agent/tool_calls.py:199-241` | 0C.3 |
| H7 | **Checkpoint resume is dead** — `run()` randomizes `thread_id` every call; `_load_checkpoint` has zero callers → crashed goals re-run side-effecting steps | `app/agent/graph.py:2455,2546` | 0C.3 + Ph2 |
| H8 | **`cancel_goal` no terminal guard** — double-decrements the per-tenant concurrency counter → underflow, tenant exceeds/undercounts plan limits | `app/services/goal_service.py:1989-2004,1176-1185` | 0B.15 |
| H9 | **Celery→SSE bridge never marks records terminal** — stub `GoalRecord`s leak forever; worker-run goals stuck `EXECUTING` in memory | `app/services/goal_service.py:417-435` | 0B.16 |
| H10 | **Unbounded SSE subscriber queue** — slow/half-open client → per-connection unbounded memory during token streaming | `app/services/goal_service.py:2137` | 0B.17 |
| H11 | **`run_goal` retry re-runs whole goal** — double/triple LLM-cost charging on transient 429 or OOM redelivery; no per-attempt idempotency | `app/scaling/tasks.py:945,885` | 0A.4 + 0C.3 |
| H12 | **Distributed goal lock never released** — cross-event-loop Redis client; lock survives to 30-min TTL → scheduled/resubmitted goals silently dropped as "already_executing" | `app/scaling/tasks.py:532,966,136-142` | 0A.5 |
| H13 | **6 tenant tables have no RLS** — `eval_suites`, `eval_suite_results`, `connector_health_snapshots`, `self_optimization_suggestions`, legacy `ip_allowlist`, `user_roles` → cross-tenant read/write | migrations 0021,0018,170245f26dcb,0022 | 0A.3 |
| H14 | **Rollback fire-and-forgets MCP inverses** — `_sync_wrapper` schedules via `create_task`, returns `None`, never awaited → reports success while created issue/page never deleted | `reliability/tool_inverses.py:60-72` · `rollback.py:117-124` | 0C.4 |
| H15 | **Audit hash chain forks + partial coverage** — in-process chain cache unseeded (false tamper alarms on restart), 2 flushers fork; hash omits `tool_name/args/actor/ip/metadata` (alterable without breaking chain) | `app/governance/audit_v2.py:224,136-149` | 0C.4 + Ph10 |
| H16 | **Circuit breaker stores per-process `time.monotonic()` in shared Redis** — cross-replica elapsed-time math meaningless → breaker never recovers or never opens | `reliability/redis_circuit_breaker.py:107,85-86` | 0C.4 |
| H17 | **HITL timeout clobbers a concurrently-granted approval** (`TIMED_OUT` overwrites `APPROVED`) | `app/governance/hitl.py:232-238` | 0A.6 |
| H18 | **Encoded/oversized/deep-nested tool-arg injections bypass scanner** — ROT13 only on goal text, no base64/hex decode on args, >50KB strings & >depth-20 silently skipped | `intelligence/guardrail_engine.py:521-549,213-219` | Ph10 |
| H19 | **Streamed output bypasses `OutputScanner`** — PII/secret/system-prompt leaks stream unredacted; even non-stream returns `allowed=True` on HIGH `system_prompt_leak` | providers stream paths · `guardrail_engine.py:458-477` | Ph10 |
| H20 | **File upload broken** — API client forces `Content-Type: application/json` onto `FormData` → every knowledge file ingest fails/garbles | `frontend src/lib/api/client.ts:45-48` | 0C.5 |
| H21 | **Refresh token leaked in URL query string** → access/proxy logs, history | `frontend src/hooks/useTokenRefresh.ts:88-91` | 0C.5 |
| H22 | **SSE hooks retry 401/403 into reconnect storm** — 8× backoff against a guaranteed-401 endpoint, session never cleared | `frontend useGoalStream/useEventStream/useCivilizationStream` | 0C.5 |

**MEDIUM / LOW — folded into owning tracks** (representative; full list in the six agent reports under the session tasks dir): supervisor fan-out has no aggregate budget & orphans sub-goals on cancel (Ph8); cross-goal HITL `list_pending` contamination pauses unrelated goals (0A.6); parallel-wave cost mutation race (0C.3); unbounded `steps` growth re-injects stale failures into verifier (Ph3); debate/goal-tree parse from raw LLM text (Ph3/Ph8); A2A no replay protection & hardcoded foreign tenant (Ph8); unbounded `top_k`/query-length cost bombs, no upload size/type limits (0C.2); `str(exc)` internal-error leakage across ~12 routers (0C.2); CORS `*`+credentials guard (0C.2); OAuth PKCE state process-local + no refresh single-flight (Ph5); tool output not injection-scanned / indirect injection (Ph10); notification send no retry → lost approval alerts (Ph1); collab opens a Redis connection per WS message & tenancy-by-UUID-only (Ph8/Ph2); event dedup by full-JSON drops legit-identical events, no heartbeat, no resume cursor (Ph2); WorkflowBuilder unsaved-changes loss, NL-generate clobbers edits, ref-based undo state (Ph9/Ph12); hardcoded `localhost:8000` fallbacks, unbounded event arrays, WS message loss on reconnect (Ph12); docker-compose missing restart policies & resource limits, beat-task overlap, non-deterministic scheduled `goal_id` on retry (0C.6 / Ph2).

### B.2 — Phase 0A: Critical Safety & Security (fix FIRST, ~3–4 days)

These block production and gate all other work. Each is TDD with a regression test that stays in the suite.

- [ ] **Task 0A.1 — Enforce HITL on high-risk steps (C1, C7).** Files: `app/agent/loop.py:259-272`, `app/agent/graph.py:1804-1858`. Failing test: a step with a destructive keyword under a fake gateway that returns REJECTED must raise `PermissionError` and must NOT execute the tool; an APPROVED gateway must execute exactly once *after* approval. Implement `await wait_for_approval(...)` in both the loop and the graph write_high path; on APPROVED dispatch the tool, on REJECTED/TIMED_OUT raise. Add a test asserting no code path between "request_approval" and "execute" lacks an await. Commit `fix(hitl): block high-risk steps on approval in AgentLoop and graph write_high paths`.
- [ ] **Task 0A.2 — Fix vault key rotation (C2).** File: `app/providers/vault.py:196-236`. Failing test: encrypt → `rotate_key(new)` → decrypt in-process succeeds, AND a freshly constructed vault with `new` decrypts the rotated ciphertext. Use `_derive_fernet_key` (same salt/encoding) inside `rotate_key`; assign `self._fernet = fernet_new` after a successful re-encrypt scan; make rotation transactional (all-or-nothing). Commit.
- [ ] **Task 0A.3 — Maintenance RLS + close the 6 unprotected tables (C3, H13).** Files: `app/scaling/tasks.py` maintenance helpers; new migration `0072_rls_maintenance_and_missing_policies.py`; `app/db/session.py` (a `system_session()` / `BYPASSRLS` role or per-statement `SET LOCAL row_security=off` on a privileged role). Failing integration tests: (a) retention delete actually removes expired `goal_events` across tenants; (b) `eval_suites`/`eval_suite_results`/`connector_health_snapshots`/`self_optimization_suggestions` reject cross-tenant reads under RLS; (c) legacy `ip_allowlist`/`user_roles` dropped or policied. Add `ENABLE`+`FORCE`+policy for the 4 active tables; run maintenance under a system context. Commit.
- [ ] **Task 0A.4 — Atomic, refunding cost control (C4, H11).** File: `app/governance/cost.py` — wire all callers (`loop.py:221`, `graph.py:1428`, `pipeline/steps.py:41`) through the existing Lua `try_record_and_check` (check-then-increment atomically); refund on rejection and on downstream call failure; make charging idempotent per `(goal_id, attempt)`. Failing tests: over-budget rejection does not inflate the counter; a failed LLM call is refunded; a retried attempt does not double-charge. Commit.
- [ ] **Task 0A.5 — One engine per task + reliable lock release (C5, H12).** Files: `app/scaling/tasks.py`, `app/db/session.py`. Build one session factory per Celery task and `dispose()` it in `finally`; run all async work for a task on a single loop (`asyncio.run(main())`) so the distributed-lock Redis client acquires and releases on the same loop. Failing tests: (a) `run_goal` creates ≤1 engine and disposes it; (b) lock acquired then released (next identical `goal_id` is not skipped). Commit.
- [ ] **Task 0A.6 — Cross-replica HITL correctness (C6, H17, + list_pending contamination).** File: `app/governance/hitl.py`. Make `approve()`/`reject()` always `publish_resolution` to the Redis BLPOP path; have `wait_for_approval` select on both the local Event and the Redis result; only downgrade to `TIMED_OUT` if still `PENDING` (CAS); persist terminal status to DB + set `_expires_at_dt` on restore; scope the graph's `list_pending` check to the current `goal_id` (not tenant-wide). Failing tests: approval on "replica B" (separate gateway instance sharing fake-Redis) unblocks a waiter on "replica A"; a timeout racing an approval keeps APPROVED; a pending approval on goal B does not pause goal A. Commit.

**Phase 0A exit:** a `tests/security/` suite covering every C-item + H8/H12/H13; no destructive step executes without a resolved approval; secret rotation round-trips; retention deletes rows; cost counters are refund-correct.

### B.3 — Phase 0B: Correctness Wiring (the original 14 + 3 promoted)

Every item below is a discovered defect where existing code silently fails. Each is one bite-sized TDD task. Tasks 0B.15–0B.17 are the promoted HIGH concurrency bugs H8–H10.

### Task 0.1: Wire `LLMResponseCache` into both `AgentGraph` construction sites
**Files:** Modify `app/services/goal_service.py:764` (AgentGraph kwargs), `app/scaling/tasks.py:785`, `app/agent/graph.py` (accept `llm_response_cache=` in `__init__`, assign `self._llm_response_cache`). Test: `tests/agent/test_perf_optimizations.py`.
**Defect:** `grep "llm_response_cache=" app/` → zero matches; the graph reads `getattr(self, "_llm_response_cache", None)` which is always `None`, so the entire planner/verifier caching diff is inert.
- [ ] Failing test: construct `AgentGraph(..., llm_response_cache=fake)`, run plan node twice with identical goal, assert second call hits cache (fake provider call-count == 1).
- [ ] Add constructor param + wire both call sites from `app.state.llm_response_cache`.
- [ ] Verify pass; commit `fix(perf): wire LLMResponseCache into goal_service and worker AgentGraph paths`.

### Task 0.2: Guard the verifier cache with `should_skip_cache`
**Files:** Modify `app/agent/graph.py` verify-node cache block; `app/rag/llm_response_cache.py` if the guard needs a `verification` branch. Test: `tests/rag/test_llm_response_cache.py`.
**Defect:** planner path calls `should_skip_cache`; verifier path does not — a cached "success" verdict could replay against a different execution state.
- [ ] Failing test: verifier summary containing `[TOOL FAILED]` must bypass cache get/set.
- [ ] Apply guard; also include a hash of step statuses in the verifier cache key.
- [ ] Commit `fix(hallucination): verifier response cache respects should_skip_cache and step-state key`.

### Task 0.3: Fix `SemanticCache.warm()` call signature (dead prefetch)
**Files:** `app/agent/graph.py` prefetch block; `app/rag/semantic_cache.py:379`.
**Defect:** graph calls `warm(queries=..., embeddings=..., tenant_id=...)` but the method signature is `warm(patterns, embedder, tenant_id)` → `TypeError` swallowed by bare `except` → silent no-op every goal.
- [ ] Failing test: prefetch invocation with a spy cache asserts entries were actually warmed.
- [ ] Align signature (add an overload accepting precomputed embeddings), replace bare `except` with `except Exception: logger.warning(...)`.
- [ ] Commit.

### Task 0.4: Implement `ModelRouter.set_override()` (per-agent model override is ignored)
**Files:** `app/agent/model_router.py`; caller `app/services/goal_service.py:718`. Test: `tests/agent/test_model_router.py`.
**Defect:** `set_override` doesn't exist; `AttributeError` swallowed → agent `model_override` never applies. Also: overrides on a process-shared router leak across goals — return a **copied router** instead of mutating shared state.
- [ ] Failing test: `router.with_override("claude-x") .model_for("execution") == "claude-x"`, and the shared router is unchanged.
- [ ] Implement `with_override(model) -> ModelRouter` (copy-on-write); update the goal_service call site; remove the silent `except: pass`.
- [ ] Commit.

### Task 0.5: Fix `LLMConfigStore.get` → `get_config` in the worker
**Files:** `app/scaling/tasks.py:392`; test `tests/scaling/test_tasks.py` (or nearest existing).
**Defect:** worker calls nonexistent `.get()`; exception swallowed → per-tenant provider/key/model never applies in Celery workers; everything runs on the default provider and `professional` plan.
- [ ] Failing test: worker path resolves tenant LLM config via `get_config`.
- [ ] Fix call; add a WARN log when tenant config lookup fails rather than silent fallback.
- [ ] Commit.

### Task 0.6: Wire `semantic_cache` + `embedder` into the worker `AgentGraph`
**Files:** `app/scaling/tasks.py:785`.
**Defect:** only the in-process `goal_service` path passes `semantic_cache=`/`embedder=`; the actual production execution path (Celery) has zero semantic caching.
- [ ] Failing test (integration-marked ok): worker graph construction includes cache/embedder when configured.
- [ ] Wire; commit.

### Task 0.7: Call `ToolResultCache.invalidate_writes` after successful write-tool calls
**Files:** `app/mcp/client.py` (`call_tool` success path for `classify_tool(...) == "write"`). Test: `tests/mcp/test_tool_cache.py`.
**Defect:** writes never invalidate cached reads → up to 5 minutes of stale `list/search` results after a mutation (correctness bug, not just staleness).
- [ ] Failing test: `create_issue` success → subsequent cached `search_issues` for that server is invalidated.
- [ ] Wire invalidation keyed by server_id; commit.

### Task 0.8: Fix knowledge write/read schema drift (ingestion invisible to search)
**Files:** `app/rag/store.py` (`_db_ingest_chunk`, `_db_ingest_with_citations`), `app/db/models/knowledge.py`, new migration `0069_knowledge_write_path_v2.py`. Test: `tests/rag/test_store.py` + one `integration` test.
**Defect:** ingestion writes legacy `documents` (fixed `Vector(768)`); `hybrid_search_db` reads `knowledge_chunks_{dim}` when the collection has `embedding_dim` → **newly ingested data is unsearchable** for v2 collections. ORM `KnowledgeCollection` lacks `embedding_dim`.
- [ ] Failing integration test: ingest into a dim-registered collection, then `hybrid_search_db` must return the chunk.
- [ ] Point the write path at `knowledge_chunks_{dim}` (dimension chosen from the collection), add `embedding_dim` to the ORM model, backfill migration copies recent `documents` rows into the dim tables.
- [ ] Commit `fix(knowledge): unify write path onto knowledge_chunks_{dim}; retrieval sees ingested data`.

### Task 0.9: Fix `federated_search` calling nonexistent `store.search()`
**Files:** `app/knowledge/federated_search.py:100`. Test: `tests/knowledge/test_federated_search.py` with the **real** `KnowledgeStore` (not a mock defining `.search`).
**Defect:** every real collection search raises `AttributeError`, is swallowed, federated search returns `[]` always. Mock-only tests hid it — this is also a test-quality lesson: fakes must match real interfaces.
- [ ] Failing test against real store; switch call to `hybrid_search_db` (fallback `hybrid_search`); commit.

### Task 0.10: Fix LLM judge grading the goal instead of the output + evaluations schema mismatch
**Files:** `app/intelligence/eval_suite.py:357-410`. Test: `tests/intelligence/test_eval_suite.py`.
**Defect:** `run_with_llm_judge` sets `all_output = task_result.goal` (judges the prompt, not the answer) and INSERTs columns `(suite_id, run_id, results, evaluated_at)` that don't exist on the `Evaluation` ORM (`goal_id, scores, average_score, passed`) → silent persistence failure.
- [ ] Failing test: judge receives the agent's final output events, not the goal string; persisted row matches ORM.
- [ ] Collect real output from the goal event stream / final state; fix the INSERT to use the ORM; commit.

### Task 0.11: Fix inverted FREE vs STARTER plan limits
**Files:** `app/tenancy/context.py:26-58`. Test: `tests/tenancy/test_context.py`.
**Defect:** FREE allows `goals_per_day=1000, max_agents=50`; STARTER allows `100/10`. Monotonicity broken.
- [ ] Failing test: assert plan limits are monotonically non-decreasing FREE→STARTER→PROFESSIONAL→ENTERPRISE.
- [ ] Set sane ladder (e.g. FREE 25/3, STARTER 200/10, PRO 2000/50, ENT custom); commit.

### Task 0.12: Close the no-roles scope-enforcement bypass
**Files:** `app/auth/scope_enforcement.py:294-296`. Test: `tests/auth/test_scope_enforcement.py`.
**Defect:** API keys with no roles skip scope enforcement entirely (legacy allow-all).
- [ ] Failing test: role-less key hitting a scoped endpoint → 403 (behind an env flag `SCOPE_ENFORCEMENT_LEGACY_ALLOW=false` default-secure, `true` preserves old behavior for migration).
- [ ] Implement flag + default deny; commit.

### Task 0.13: Cross-model verification in the `goal_service` path
**Files:** `app/services/goal_service.py:764` (pass `verifier=_build_verifier_provider()` result, mirroring `app/scaling/tasks.py:777`); factor `_build_verifier_provider` out of `app/main.py:191` into `app/providers/verifier.py` so both paths share it. Test: `tests/services/test_goal_service.py`.
- [ ] Failing test: when two provider keys are configured, in-process path verifies with the cross-model provider.
- [ ] Extract + wire; commit.

### Task 0.14: `GoalDeduplicator.release()` on terminal goal states
**Files:** `app/services/goal_service.py` (completion/failure/cancel handlers), `app/services/dedup.py`. Test: `tests/services/test_dedup.py`.
**Defect:** dedup keys only expire via 60s TTL; a goal that fails in 5s blocks identical resubmission for the remaining TTL, and long-running goals lose dedup protection after 60s. Also replace the broad `except: pass` around dedup with logged handling, and refresh TTL while the goal is active.
- [ ] Failing tests for both behaviors; implement `release()` call + TTL heartbeat; commit.

**Phase 0B exit criteria:** all 14 wiring fixes merged; `uv run pytest` green; a one-page "wiring integrity" test module (`tests/test_wiring_integrity.py`) asserts by construction that `create_app()` + worker path inject every `app.state` cache/service the graph consumes (prevents this whole defect class from recurring).

- [ ] **Task 0B.15 — Terminal guard on `cancel_goal` (H8).** File: `app/services/goal_service.py:1989-2004`. Failing test: cancelling an already-COMPLETE goal is a no-op and does not decrement the concurrency counter a second time. Add `if record.status in _TERMINAL_STATUSES: return {...}` at the top; make `decrement_concurrent_goals` fire once per goal via a "counted" flag. Commit.
- [ ] **Task 0B.16 — Celery→SSE bridge sets terminal status (H9).** File: `app/services/goal_service.py:417-435`. Failing test: a worker `goal_complete` event routed through the bridge sets `record.status`/`completed_at` so `_evict_stale_goals` reclaims it. Route bridge terminal events through `_dispatch_event` (or set status+`completed_at` explicitly). Commit.
- [ ] **Task 0B.17 — Bound SSE subscriber queues + heartbeat (H10 + medium: no heartbeat/resume).** Files: `app/services/goal_service.py:2137`, `app/api/goals.py:325-337`. Failing tests: a slow subscriber's queue is bounded (drop-oldest of ephemeral `token_chunk`/disconnect on overflow); an idle stream emits a `heartbeat` every ≤15s; reconnect with `Last-Event-ID` resumes from `EventStore` sequence without gap/dup. Implement `asyncio.Queue(maxsize=N)`, `asyncio.wait_for(queue.get(), 15)` → heartbeat, and sequence-based resume. Commit.

### B.4 — Phase 0C: High-Severity Hardening (~4–5 days)

Grouped by subsystem; each bullet is one TDD task with a regression test. Items marked "+ Ph_N_" are deepened later by that track but must be baseline-safe now.

- [ ] **Task 0C.1 — SSRF egress guard (H1).** New `app/net/ssrf_guard.py`: `assert_public_url(url)` resolves DNS and rejects loopback/RFC-1918/link-local/metadata (`169.254.169.254`) with re-validation after resolution (anti-rebinding) and an https+scheme allowlist. Wire into A2A `_send_callback`, `/ingest/url`, `/ingest/rpa-url`, and `MCPClient` request sites; per-tenant allowed-domain lists. Failing tests: each sink rejects a metadata/loopback URL. (Ph10 adds egress-content DLP.)
- [ ] **Task 0C.2 — API authz & input hardening (H2, H3, H4, H5 + mediums).** Add tenant filter/RLS ctx to `GET /a2a/tasks/{id}` and both list fallbacks; derive client IP from `request.client.host` (trusted-proxy XFF only); fail-closed rate limiter + IP allowlist with in-process fallback ceiling; register `ENDPOINT_SCOPES` for every mutating route on the 10 unregistered routers (default-deny unknown methods); clamp `top_k` (1–100) and cap query/text lengths; enforce upload size + MIME allowlist on `/ingest/file|pdf|docx`; replace `detail=str(exc)` with generic message + correlation id across the ~12 routers; reject CORS `*` when credentials enabled; timing-safe HMAC already OK. One test per sub-item. (Ph1 formalizes scopes with entitlements.)
- [ ] **Task 0C.3 — Async-safety in the agent loop (H6, H7, H11-idempotency, cost-race).** Convert `_resolve_jira_account_id` to `AsyncClient`+cache (no event-loop blocking); derive `thread_id` from `goal_id` and add a resume path seeding state from checkpoint; guard parallel-wave `total_cost_usd` mutation with `_state_lock`. Tests: no sync httpx in async paths (import-lint + timing test); resubmit resumes instead of re-running; concurrent wave increments don't lose cost. (Ph2 builds durable resume UX.)
- [ ] **Task 0C.4 — Reliability correctness (H14, H15-baseline, H16).** Make `get_inverse_fn` return an awaitable and await it in `rollback_all_async` (log unregistered inverses instead of silent no-op); seed audit chain from DB tip under a single-writer lock and include all integrity fields in the hash; store `time.time()` (wall-clock) for circuit-breaker `opened_at` with a TTL. Tests: rollback actually deletes the created resource; audit chain verifies across a simulated restart; breaker recovers across replicas. (Ph10/Ph11 extend audit + red-team.)
- [ ] **Task 0C.5 — Frontend correctness (H20, H21, H22 + mediums).** In `client.ts`, skip JSON `Content-Type` when body is `FormData`; move refresh token to POST body; short-circuit SSE/WS hooks on 401/403 → `logout()` (no retry storm); read the api key/token from the store at each (re)connect; add `Last-Event-ID` resume + dedupe + `slice(-N)` caps to SSE hooks; centralize base-URL resolution (fail fast if unset in prod); TanStack Query `retry` predicate excludes 401. Vitest unit tests + an e2e that uploads a file and asserts ingestion succeeds. Commit.
- [ ] **Task 0C.6 — Infra hardening (docker-compose + Celery beat).** Add `restart: unless-stopped` to worker/beat/backend/postgres/redis and `deploy.resources.limits` (memory on worker ≥ `worker_max_memory_per_child`); wrap overlap-sensitive beat tasks (`fire_due_schedules`, `record_queue_depths`, `flush_audit_wal`) in a Redis `SET NX` guard; require a deterministic `fire_instance_id` for scheduled goals (no `now()` fallback); fix the exec-form `$HOSTNAME` healthcheck (`CMD-SHELL`); heartbeat `goals.updated_at` during execution so `detect_stuck_goals` doesn't false-positive. Commit.

**Phase 0C exit:** SSRF/IDOR/authz regression suite green; no sync I/O in async paths (lint gate); frontend uploads + streaming e2e green; infra brings services back after kill.

---

## Part C — Capability Tracks (each becomes its own detailed plan before execution)

### Phase 1 — Identity, SaaS Hierarchy, Admin & Billing (asks 12, 13, 14, 15)

**Current state:** No `users` table (SCIM writes to a phantom one — `app/auth/scim_handler.py`); Keycloak JIT creates one tenant per SSO subject (`app/auth/keycloak.py:198`); two disjoint RBAC stores (`user_roles` vs `role_assignments`/`custom_roles`); tenant/key authority is in-memory (`app/services/tenant_service.py:37`) with fire-and-forget DB writes; no platform admin; no billing.

**Design decisions:**
- **Identity model:** `users` (global identity, email-unique) + `tenant_memberships` (user↔tenant, role, status, invited_by) + existing `tenants`. A user may belong to many tenants (solves ask 13 *and* 14: "global product to users" = personal tenant auto-created on first login; "SaaS to tenants" = org tenants with invited members).
- **Gmail login:** direct Google OIDC (`app/auth/google_oauth.py`, authorization-code + PKCE, `openid email profile`) as the first-party path, *and* a Keycloak identity-broker config for enterprises that already use the Keycloak flow. Login upserts `users`, resolves memberships, and mints AgentVerse JWTs (15-min access / 7-day refresh per security rules).
- **RBAC consolidation:** `RoleAssignment`/`CustomRole` (the scope/ABAC system) becomes canonical; `/tenants/me/roles` API migrates to write it; `user_roles` gets a data migration + shim reader; per-tenant roles come from `tenant_memberships.role` seeded with templates (`owner`, `admin`, `builder`, `operator`, `viewer`) + domain templates from `app/tenancy/domain_role_templates.py`. Plan-tier feature gating stays in `PLAN_LIMITS` but is validated by a single `entitlements.py` module (one place answering "can tenant T use feature F at volume V").
- **Durable tenancy:** `TenantService` becomes DB-first (transactional writes, Redis read-through cache), keeping `sync_from_db()` only as warm-up.
- **Platform admin:** `platform_admins` table (allowlist by user id) + `/admin/*` router (tenant lifecycle, plan changes, aggregated usage from `CostController` + goal counts, key revocation, guardrail incident feed, impersonation that mints a scoped, audited, time-boxed token). Frontend: new `src/features/admin/` (tenant table, usage charts, plan editor, incident feed).
- **Billing/metering:** `usage_records` (tenant, metric [goals, llm_tokens, tool_calls, storage], period, qty) emitted from `CostController` and goal completion; Stripe subscriptions per plan; webhook → plan sync; self-serve upgrade/downgrade endpoint + UI in `src/features/settings/`. Metering export (CSV/API) for enterprise invoicing.

**Work breakdown (each → detailed plan):** 1a users+memberships+migrations+Google OIDC; 1b RBAC consolidation + entitlements; 1c durable TenantService; 1d admin plane (API+UI); 1e billing/metering + Stripe. **KPIs:** SSO user can join an org tenant; admin can change any tenant's plan with audit; every goal produces usage records; zero in-memory-only tenant state.

### Phase 2 — Token Cost & Million-Scale Latency (asks 2, 25)

**Current state after P0:** caches wired but crude. Biggest waste: `_build_tool_context` injects **every tool + full JSON schema** of all connectors + all 14 RPA tools into every planner prompt (`app/agent/tool_context.py::to_prompt_block`, consumed at `app/agent/graph.py:558`); `PromptCompressor` is regex-only; semantic cache L2 is an O(n) Python scan; no Anthropic prompt caching.

**Design decisions:**
1. **Per-goal tool retrieval (largest single win):** wire the existing `CapabilitySearch` (`app/mcp/capability_search.py`) into `_build_tool_context` — embed the goal, select top-k (default 12) tools, rank-boosted by `ToolReliabilityStore` success rates; RPA tools included only when the goal/agent signals browser work (capability tag on the agent or goal classifier). Full schema injected **only for selected tools**, compact one-line signature (via `SchemaAwarePromptInjector` format) for the next 20, names-only beyond. Fallback to full list when discovery < 15 tools.
2. **Provider-native prompt caching:** add `cache_control` ephemeral breakpoints in `anthropic_provider.py` for the stable prefix (system prompt + skill pack + tool block); order prompts stable-prefix-first. This typically cuts input cost 60–90% on multi-iteration goals — far more than regex compression.
3. **Honest compression:** keep `PromptCompressor` but scope it to RAG-context truncation with a real tokenizer (tiktoken already in `chunker_v2`); never run regex rewriting over JSON-format instructions; measure and log savings per call (`myapp_prompt_tokens_saved_total`).
4. **Semantic cache at scale:** replace L2 linear scan with pgvector table (`semantic_cache_entries`, HNSW) or Redis Stack `FT.SEARCH` vector index if available — O(log n), per-tenant partitions; keep L1 LRU.
5. **Scale hardening:** pool sizes from env (`app/core/pools.py`); pgbouncer already in compose — document sizing; per-plan Celery worker autoscaling signal from `record_queue_depths`; SSE resume-from-sequence using `EventStore` sequence numbers (reconnect without replay storms); load-test suite (`locust` or `k6` in `infra/loadtest/`) with a CI smoke profile targeting p95 goal-submission < 300ms, p95 SSE event latency < 500ms at 10k concurrent streams.
6. **Cost observability:** per-goal token/cost breakdown by role (planner/executor/verifier) persisted and shown in the goal detail UI; tenant cost dashboards; alert at 80% budget.

**KPIs:** ≥60% reduction in mean input tokens per goal iteration (measure before/after on a golden-goal set); cache hit-rate dashboards; verified p95 targets under load.

### Phase 3 — Hallucination Reduction v7+ (ask 1)

**Current state:** strong grounding prompts, verifier with anti-self-confirmation rules, tool-name/argument validation, cross-model verifier (after P0, in both paths). Gaps: prompt-only grounding, no claim-level checks, no consensus for high-stakes goals.

**Design decisions:**
1. **Claim grounding checker** (`app/agent/grounding.py`): after each executor step, extract concrete claims (IDs, counts, URLs, dates, quoted values) via a cheap model and verify each appears in (or is derivable from) the step's tool outputs; ungrounded claims → step marked `UNGROUNDED`, fed to verifier, and replan triggered. Deterministic pre-pass (regex for IDs/numbers cross-checked by substring match against tool output) before the LLM pass so cheap cases don't cost a call.
2. **Citation-carrying answers:** final synthesis must cite step/tool provenance for every factual claim (the RAG `/chat` path already does citations — extend the pattern to goal results); UI renders citations in the goal result view.
3. **Consensus verification for high-risk goals:** for goals touching `tool_risk` write_high/destructive classes or regulated-domain policies, run 3-way verification (primary verifier + cross-model + a rubric-scored `LLMJudge`) — majority required; wire through the existing HITL gateway when verifiers disagree.
4. **Verifier calibration loop:** log verifier verdicts vs eventual human/eval outcomes (`verifier_calibration` table); report false-confirm rate; use golden tasks (Phase 11) as the regression suite for every prompt change (`docs/superpowers/plans/2026-07-04-hallucination-elimination.md` merges into this track).
5. **Structured outputs everywhere:** move planner/verifier JSON parsing to provider-native structured outputs / tool-call responses where the provider supports it (eliminates a whole class of parse-then-hallucinate bugs in `_parse_verifier_response`).

**KPIs:** ungrounded-claim rate on golden set < 1%; verifier false-confirm rate < 2%; zero fabricated tool names (already enforced) and zero fabricated entity IDs in outputs.

### Phase 4 — World-Class, Goal-Aware RAG + Vector-DB-Less Mode (asks 3, 5)

**Current state:** hybrid = fixed `0.7*cosine + 0.3*pg_trgm` (non-comparable scales); no rerank, no BM25, no query rewriting, single-shot top-k; good connector-ingestion breadth; RAGAS-style evaluator exists but unwired.

**Design decisions:**
1. **Retrieval engine rebuild** (`app/rag/engine.py`): three legs — pgvector ANN, **Postgres full-text (`tsvector` + `ts_rank_cd`, BM25-like)**, `pg_trgm` fuzzy — fused with **Reciprocal Rank Fusion** (rank-based, scale-free — fixes the unsound blend), then **cross-encoder rerank** of the top-50 (hosted reranker if key present — Voyage/Cohere via `providers/`, else a small local cross-encoder, else skip). `ef_search` tunable per query.
2. **Vector-DB-less mode (ask 3):** because RRF is rank-based, the engine degrades gracefully to FTS+trigram only when a collection has no embeddings or no embedding provider is configured — a first-class `retrieval_mode: hybrid | lexical | vector` on `knowledge_collections`. This removes the hard embedding dependency for cost-sensitive tenants and makes local/air-gapped deployments (Phase 5) viable with zero embedding calls. Document the trade-off; RAG evals quantify the recall gap.
3. **Contextual retrieval:** at ingest, prepend an LLM-generated 1–2 sentence chunk-context (Anthropic contextual-retrieval pattern) before embedding; store both raw and contextualized text; content-hash upsert dedup on re-ingest; use the structure-aware `SemanticChunker` (markdown/code) in the live path instead of flat token windows.
4. **Agentic RAG for goals (ask 5 "intelligent, best-suited per task"):** a `RetrievalPlanner` picks a strategy per goal step — direct top-k for lookups; **query decomposition + multi-hop** for comparative/analytical goals; **HyDE** when queries are short/abstract; parent-document (small-to-big) retrieval for code/contracts; federated cross-collection when the agent has several collections. The planner is a cheap-model classifier with heuristics first, and its choice is logged for eval.
5. **RAG in the agent loop:** retrieval becomes a first-class graph step with retrieved-context provenance in `AgentState`, so the verifier can check groundedness against retrieved chunks (ties into Phase 3).
6. **Wire evals:** `RetrievalEvaluator` (P@K/R@K/MRR) + faithfulness/answer-relevancy judges run per-collection on golden Q&A pairs (Phase 11 datasets); regression gate before changing fusion weights or chunkers.
7. **Optional GraphRAG (behind a flag):** entity/relation extraction into a `knowledge_graph_edges` table for org-chart/legal-entity style corpora where multi-hop entity queries dominate; only after evals prove the need — YAGNI otherwise.

**KPIs:** +20% MRR on golden retrieval sets vs current hybrid; faithfulness ≥ 0.9 on RAG chat; lexical-only mode within 15% of hybrid recall on keyword-heavy corpora.

### Phase 5 — Custom Providers (Ollama/Local) & Intelligent Model Routing (asks 4, 6)

**Current state:** `OpenAICompatibleProvider` already speaks Ollama/vLLM/Groq via `base_url`, and `ModelRouter._PROVIDER_DEFAULTS` has an `ollama` profile — but `_resolve_provider_for_app()` (`app/main.py:141`) only checks Anthropic/OpenAI keys, so local models can't be the platform provider; per-tenant config exists (`LLMConfigStore`) and works after P0.5.

**Design decisions:**
1. **Provider registry, not if-chains:** replace `_resolve_provider_for_app` with a declarative registry reading `LLM_PROVIDERS` env/JSON (ordered list of `{type, base_url, key_ref, models}`) — first healthy wins; supports `ollama` (`OLLAMA_BASE_URL`, no key), `openai_compatible`, `anthropic`, `gemini`. Health-check at startup (1-token completion) with clear failure logs. Gemini becomes a first-class primary option, not embedder-only.
2. **Tenant BYO-model, plan-gated:** extend `/tenants/me/llm` to accept per-**role** config — `{planning: {...}, execution: {...}, verification: {...}, embedding: {...}}` — stored in `LLMConfigStore`, vault-encrypted keys. Entitlements (Phase 1b): FREE = platform defaults only; PRO = BYO key; ENTERPRISE = BYO endpoints (Ollama/vLLM on their VPC) + per-role overrides. This is exactly ask 6: "planning by one model, execution by another, per tenant, per plan."
3. **Routing intelligence:** today's `complexity_tier()` is keyword regex. Upgrade to a learned router: features = goal embedding cluster + tool-count + historical iterations for similar goals (from `ExecutionMemory`) + budget tier (`get_cost_tier`); output = model tier per role. Start with a transparent scoring function (log every decision + outcome), then fit thresholds from `evaluations` data. Execution/verification also route through `model_for_goal` (today only planning does).
4. **Failover & rate-limit handling in `base.py`:** provider-level retry with exponential backoff, 429-aware, automatic fallback to the next registry provider; per-provider circuit breaker (reuse `reliability/`).
5. **Model catalog + pricing:** `app/providers/catalog.py` maps model → context window, $/Mtok in/out (feeds `governance/pricing.py`), capability flags (vision, tools, structured output) so the router never picks an incapable model.

**KPIs:** platform boots with only `OLLAMA_BASE_URL` set; tenant with per-role config demonstrably uses 2+ models in one goal (visible in the cost breakdown); routing decisions logged with outcome quality; ≥25% cost drop on simple-goal cohort with no eval regression.

### Phase 6 — Skills Platform (ask 7)

**Current state:** no skill primitive at all (backend-wide "skill" search ≈ 1 stray hit); agent instructions are monolithic `system_prompt` strings; templates copy-paste prompt logic; every goal loads everything.

**Design decisions:**
1. **`Skill` entity** (`app/db/models/skill.py`, migration): `{id, tenant_id (null = platform), name, version, description, trigger_hints, instructions, few_shot_examples JSONB, allowed_tools JSONB, required_connectors, eval_fixture_ids, token_estimate, visibility (platform|tenant|marketplace), created_by}`. Versioned, immutable-per-version (new version on edit) — same discipline as migrations.
2. **Skill selection at goal time** (token *reduction*, not addition): `SkillSelector` embeds `trigger_hints` and matches against the goal (same infra as `CapabilitySearch`), loading only the top 1–3 relevant skills into the planner/executor context. A skill's `allowed_tools` further narrows Phase 2's tool retrieval — this compounds the token savings (skill says "use jira + slack" → only those schemas load).
3. **Default skill pack** (platform-seeded, inspired by the ones you named): `summarize-and-compress` (headroom-style output discipline: don't restate tool output, cite instead of quote), `knowledge-graphify` (entity/relation extraction into collections — pairs with Phase 4.7), `frontend-design` (UI-building conventions for Phase 9 site generation), `structured-reporting`, `web-research`, `data-extraction`, `code-review`, plus per-domain packs shipped with Phase 7 templates. Each ships with eval fixtures.
4. **Custom skills (ask 7 second half):** CRUD API (`app/api/skills.py`) + `src/features/skills/` editor (markdown instructions, example manager, tool picker, live token estimate, "test against a goal" dry-run using the simulation runner); import from a SKILL.md-style file.
5. **Marketplace crossover:** skills are publishable/installable via marketplace_v2 with the same `TemplateSecurityReviewer` pipeline (injection scan on instructions is mandatory — skills are a prompt-injection vector; ties to Phase 10).
6. **Agent/template integration:** `agents.skill_ids JSONB` + templates reference skills instead of inlining prompts; `system_prompt` becomes the short identity/personality layer only.

**KPIs:** median system-context tokens per goal drop ≥30% for skill-equipped agents vs monolithic prompts at equal eval scores; ≥8 platform skills with eval fixtures; a tenant-authored skill can be created, tested, attached, and used in a goal end-to-end (e2e spec).

### Phase 7 — Marketplace Depth, Domain Agents & Business Use Cases (asks 8, 10, 20, 21, 23-domains)

**Current state:** marketplace_v2 is real (DB, security review, full-text search, ratings, ~29 templates across 10 domains incl. 5 legal); V1 (8 templates, in-memory) still coexists; templates are single-shot goal strings without knowledge/eval/guardrail bundles; MetaAgentPlanner invents connector names.

**Design decisions:**
1. **Consolidate on V2:** V1 endpoints become thin shims over marketplace_v2; single `_BUILTIN_TEMPLATES` source; delete drift.
2. **"Solution" packaging (the law-firm ask, 20):** upgrade templates to **Solutions** = `{agents[] (with skills), knowledge_collections[] (seed schemas + ingestion recipes incl. RPA crawl configs), workflows[], schedules[], policies/guardrail bundle, eval_suite (golden tasks), sample data, onboarding checklist}`. Atomic install (extends the existing transactional deploy). The Law Firm solution becomes the reference: intake agent, contract-review agent (clause library collection), case-research agent (multi-hop RAG), billing agent, matter-deadline schedules, legal-domain fail-closed policies, citation-accuracy eval suite; knowledge base fed by document ingestion **and** the Phase 8 RPA→knowledge bridge (court portals, firm DMS).
3. **Domain depth rollout (asks 8, 23):** one flagship solution per domain, each built on the same Solution schema: E-commerce (catalog ops, order-exception handling, review-response), Education (curriculum assistant, grading with rubric evals), Operations (incident mgmt, vendor onboarding), Software (PR review + release notes + triage — extends existing templates), Finance (close checklist, reconciliation), Healthcare-admin (prior-auth — already seeded, extend with FHIR collection recipes). Each ships with eval suites and demo sandbox data so tenants can trial in simulation mode before connecting real systems.
4. **Grounded MetaAgentPlanner:** constrain NL→agent generation to the real catalog — retrieval over the 320+ connector catalog + installed servers + available skills before the LLM call; validate output against connector/tool schemas; offer "save as template/solution" promotion path (unifies the three template concepts).
5. **Certification tie-in:** "verified" badge requires passing the solution's own eval suite in simulation + `TemplateSecurityReviewer` at `safe/low` risk.

**KPIs:** ≥6 flagship solutions installable end-to-end in < 5 minutes each; every solution has a passing eval suite; V1 code deleted.

### Phase 8 — External Agent Interaction, Org Architecture & Civilization (asks 9, 18, 22)

**Current state:** genuine A2A protocol (AgentCard at `/.well-known/agent.json`, HMAC-verified task ingress, callbacks); civilization runtime with spawn lineage (`parent_agent_id`, budgets, EWMA reputation, constitution, blackboard, tick loop); supervisor/debate/goal-tree patterns. Gaps: single AgentCard, HMAC-only external auth, org semantics siloed in civilization, unweighted debate votes.

**Design decisions:**
1. **A2A directory + identity (ask 9):** publish per-agent AgentCards (`/.well-known/agents/{agent_id}.json`) + a queryable directory endpoint; **outbound** A2A client so AgentVerse agents can call external A2A/MCP agents as tools (an `a2a_call` tool in the loop, policy-gated + budget-metered like any connector); upgrade auth from shared-secret HMAC to the existing `agent_identity.py` RS256 JWTs (per-caller identity, revocable) with HMAC kept for backward compat. Webhook/event bridges (Slack handler exists; add generic outbound webhooks per goal-lifecycle events) cover "interaction outside AgentVerse" for non-A2A systems.
2. **First-class Org structure (ask 22):** promote civilization's hierarchy into a general `agent_teams` model: departments with role-typed agents (`executive`, `manager`, `specialist`), role-scoped tool/skill/budget policies, and a **delegation pattern** — manager agents decompose via `SupervisorAgent` and route to reports via `AgentRouter`, escalation via HITL to human owners. Ship an **"AI Org" solution** (Phase 7 packaging): CEO-agent (weekly synthesis from all departments), CTO-agent (repo/incident oversight), HR-agent (onboarding workflows), Finance-agent (spend/reconciliation), each with department knowledge collections and schedules. This is configuration + packaging on top of existing primitives — not a new runtime.
3. **Civilization hardening (ask 18):** reputation-weighted debate consensus (weight votes by reputation × historical eval accuracy); specialization-aware routing (skill/embedding match, not just reputation fallback); scheduled civilization goals via the existing `ScheduleStore`/beat ticks (recurring department standups, report generation); backfill/missed-fire recovery for beat outages; centralized next-fire service with tz-database validation (fixes the ad-hoc croniter + LLM-timezone gaps); "next 5 runs" preview endpoint for the schedules UI.

**KPIs:** external agent completes a task via A2A with JWT identity + callback; AI-Org solution runs a scheduled weekly cycle producing department reports; debate consensus weighted; schedule preview in UI.

### Phase 9 — Builder Experiences: Site/Code Generation, Workflow RAG, Agent Lab (asks 11, 19)

**Current state:** Docker-sandboxed `CodeInterpreter` + `ShellTool` (unsandboxed subprocess fallback!), persistent artifact store with retention, `SimulationRunner` (mock-tool dry-runs), playground/lab frontend features exist; workflow builder has 9 node types but no RAG node, and `decision`/`loop`/`parallel` don't execute.

**Design decisions:**
1. **Workflow engine completion (ask 19):** implement real semantics for `decision` (LLM-or-expression condition eval selecting outgoing edge), `loop` (bounded iteration/map over a list input with per-iteration context), `delay` (durable timer via Celery ETA), and add **`rag` node** (collection picker + query template + top_k + strategy from Phase 4's `RetrievalPlanner`; output = chunks + citations injected into downstream context) and **`skill` node**. Fix DAG step tool dispatch (empty `server_id` bug) by binding nodes to concrete `server_id`+tool with argument mapping UI. Update `WorkflowBuilderPage` palette + inspector + e2e specs.
2. **Sandbox hardening first (prerequisite for 11):** remove the unsandboxed subprocess fallback in production (`ENVIRONMENT=production` → refuse, not fall through); add resource limits (CPU/mem/pids/network-egress policy) on Docker runs; evaluate gVisor/Firecracker/E2B as a follow-up flag.
3. **Site/app generation (ask 11, lovable/emergent-style):** a **Builder solution** on existing primitives rather than a new engine: `project_workspaces` (persistent per-project artifact trees), a `builder` agent equipped with the `frontend-design` skill + code tools operating on the workspace, **live preview hosting** — static build artifacts served at `preview.{host}/p/{workspace_id}` (extends the artifact store with a serving path + CSP sandbox headers), iterative chat-edit loop in a new `src/features/builder/` UI (chat pane + file tree + preview iframe + deploy-to-artifact button). Scope V1 to static sites/SPAs built in-sandbox (`npm build` inside the container); full-stack preview environments are a later flag.
4. **Agent Lab / playground / simulation upgrades:** unify `features/lab`, `playground`, `simulation` into one Lab with: side-by-side model comparison on a goal (uses Phase 5 routing), simulation dry-runs with real cost estimates (replace the `len(steps)*0.001` stub with `pricing.py` estimates), and one-click "promote lab config → agent".

**KPIs:** workflow with decision+loop+rag nodes executes correctly (integration test); a "build me a landing page" goal produces a previewable site; zero unsandboxed execution paths in production.

### Phase 10 — Security Guardrails & Red-Team Depth (ask 24)

**Current state (strongest area):** six-layer `guardrail_engine.py` v2 (injection, recursive arg scan, PII, cloud-destruction, LLM judge fail-closed, output/system-prompt-leak scanner) enforced in the graph; legacy v1 runs in parallel; policy engine fail-closed for regulated domains; HITL is cross-replica. Gaps: v1/v2 duplication, 5-case red-team corpus, LLM judge off by default, no egress/exfiltration controls.

**Design decisions:**
1. **Consolidate on v2:** v1 (`guardrails.py`) callers migrate to `guardrail_engine.py`; port v1's unique decoders (homoglyph/leetspeak) into `InjectionGuard`; delete v1.
2. **Defaults by plan/domain:** LLM judge + HITL-on-guardrail (`HITL_QUEUED`) enabled by default for write_high/destructive tools and all regulated-domain tenants (entitlements-driven); economy tenants get regex layers + fail-closed.
3. **Data-exfiltration layer (the gap you named):** egress policy on tool arguments and outputs — detect secrets/PII/knowledge-chunk verbatim leakage heading to external-write tools (email/slack/http POST/webhooks); per-tenant allowed-destination lists; volume anomaly detection (unusually large payloads to external sinks) → HITL. Indirect-injection defense: content retrieved from tools/RAG/web is wrapped in delimited untrusted-content blocks and scanned before re-entering prompts (tool-result poisoning is the top real-world agent attack).
4. **Red-team program:** expand `RedTeamRunner` from 5 static cases to a versioned corpus (100+ cases: direct/indirect injection, jailbreak families, multi-turn escalation, tool-poisoning, exfiltration attempts, social-engineering of HITL approvers) sourced from public benchmarks + house-written; run in CI against the guardrail engine (fast, mocked) and weekly `BehavioralRedTeamRunner` live runs in simulation; publish a per-tenant safety scorecard.
5. **Skill/template injection review:** mandatory `TemplateSecurityReviewer` + injection scan on marketplace skills/solutions (from Phase 6/7) — user-authored instructions are an injection vector.

**KPIs:** red-team pass rate ≥ 99% on corpus in CI (gate); v1 deleted; exfiltration test suite (secrets → external sinks) fully blocked; guardrail incident feed visible in admin (Phase 1d).

### Phase 11 — Golden Datasets & Unified Self-Improvement (asks 16, 17)

**Current state:** golden tasks + LLM judge + rollout gate exist and are DB-backed; `SelfOptimizerV2` (Bayesian A/B with apply/rollback) is genuinely advanced. Defects fixed in P0.10. Gaps: three uncoordinated optimizers, fabricated variance in the "Bayesian" posterior, no dataset governance, no eval-gated promotion, naive memory extraction.

**Design decisions:**
1. **Dataset governance (ask 16):** version golden datasets (`golden_datasets` + `golden_dataset_items`, immutable versions); splits (regression/holdout); per-domain seed sets shipped with Phase 7 solutions; UI (`src/features/eval/`) for curating from real goals ("promote this goal+outcome to golden set" button — human-labeled); judge calibration set (human-scored sample to measure judge agreement, target κ ≥ 0.7); baseline snapshots + trend charts per dimension.
2. **One experiment control plane (ask 17):** `ExperimentRegistry` (`app/intelligence/experiments.py` + table) through which SelfOptimizerV2, PromptOptimizer, and v1 suggestions all register — one active experiment per (tenant, agent) enforced by lock; unified lifecycle `proposed → offline-eval → live A/B → promoted/rolled-back`, all history in one place, admin-visible.
3. **Statistical honesty:** replace fabricated `std = mean*0.3` with real per-arm sample variance (Welford); minimum n≥20 per arm before decisions; scipy required (remove the `mean*1.05` fallback promotion).
4. **Eval-gated promotion:** no candidate (config, prompt variant, skill edit) goes live without beating the golden regression set offline (`EvalSuiteRunner` in simulation) *and* the live A/B; auto-rollback triggers when live eval EWMA regresses > threshold — closing the loop between evals, memory, and optimization.
5. **Reflective memory:** replace 200-char truncation extraction (`long_term.py::extract_from_goal`) with an LLM reflection step producing structured learnings `{insight, evidence, applicability, confidence}`; consolidation job (dedupe/merge/decay memories, cap per tenant with importance scoring); semantic recall for `ExecutionMemory` (embedding, not substring). Self-improvement explicitly covers **RPA** (v1's failure-pattern heuristics feed the registry) and **RAG** (retrieval-strategy choices from Phase 4.4 are logged and optimized as experiments, using retrieval evals as the metric).

**KPIs:** every optimizer action traceable in one registry; zero unguarded promotions; golden datasets versioned with trend dashboards; memory consolidation keeps recall precision flat as memory count grows 10×.

### Phase 12 — World-Class UI, Design System & Test Depth (continuous)

**Current state:** 38 features, 76 unit tests, 44 Playwright specs, high polish, bespoke Tailwind components; no a11y automation, no visual regression, token usage inconsistent (`bg-blue-600` mixed with semantic tokens), duplicate test locations.

**Work:**
1. **Design tokens + docs:** codify the bespoke system — semantic color/spacing/typography tokens in `tailwind.config`, migrate raw-palette usages, document components (Storybook or a lightweight in-app `/design` gallery given zero-new-deps preference — decide in the track plan); dark mode + `prefers-reduced-motion` audits per accessibility rules.
2. **A11y automation:** axe-core checks in Playwright for every page spec (WCAG 2.2 AA); keyboard-flow tests for the workflow builder, civilization map, and modals (focus trap/restore).
3. **Visual regression:** Playwright screenshot snapshots for the 10 highest-traffic pages, light+dark.
4. **Test hygiene:** consolidate `__tests__/` vs colocated duplicates (pick colocated); interface-conformance tests for fakes (the `federated_search` lesson — fakes must be type-checked against real protocols); add contract tests generated from the OpenAPI export so frontend client drift fails CI.
5. **New-surface coverage:** every phase above ships its e2e specs (admin, skills editor, builder, RAG workflow node, solution install).

**KPIs:** zero axe violations at AA on shipped pages; visual-regression suite green in CI; coverage thresholds enforced (80% line on new code).

---

### Phase 13 — Suggested World-Class Capabilities (beyond the 25 asks)

These are gaps the audits surfaced that a world-class autonomous-agent SaaS needs but weren't in the 25. Each carries the standard deliverable (backend + world-class UI + e2e). Prioritize 13.1–13.4 (they compound with earlier phases); 13.5–13.13 are high-value follow-ons.

1. **End-to-end observability & trace explorer (highest-value addition).** OpenTelemetry spans across plan→execute→verify→tool→LLM with cost/latency/token attribution per span; a LangSmith-style **trace viewer** UI (timeline, prompt/response with PII scrubbing, tool I/O, replay from any step — extends the existing `replay.py`). Prometheus golden-signal dashboards per tenant. Without this, "world-class" quality/latency claims aren't measurable and prod debugging is blind. Backend: OTel instrumentation + trace store; UI: `src/features/observability/` trace explorer; e2e: submit goal → open trace → assert spans.
2. **Human feedback (RLHF-lite) loop.** Thumbs up/down + correction on any goal result/step; feedback flows to golden datasets (Phase 11), the experiment registry (as a reward signal), and memory (as a negative/positive example). Closes the loop from real usage to self-improvement. UI: inline feedback widgets on goal results; e2e: rate a result → appears in eval curation queue.
3. **Multi-modal goals.** Image/PDF/audio inputs to goals (vision already exists in `perception/`); document-in → structured-out flows; screenshot-grounded RPA self-correction (unify the two Playwright stacks per Audit-1 Phase 9). UI: file/image drop on goal submit; e2e: submit an image goal.
4. **Cost forecasting, anomaly detection & guardrail-budget alerts.** Predict goal/tenant spend before execution (from `pricing.py` + historical iterations), detect cost anomalies (a goal burning 10× its cohort), alert at 80/100% budget, and auto-pause runaway goals. Backend: forecasting service + anomaly job; UI: cost dashboards with projections in `src/features/analytics/`; e2e: trigger a budget alert.
5. **Time-travel debugging & deterministic replay.** Replay a goal from any checkpoint with the exact same inputs (needs Phase 0C.3 checkpoint fix + trace store); "fork from step N with an edited plan." Powerful for support and for reproducing hallucinations.
6. **Tenant-configurable feature flags & white-labeling.** Per-tenant feature toggles (ties to entitlements), custom domains/branding/logo, configurable email templates — table stakes for B2B SaaS. UI: settings + admin.
7. **Fine-tuning / distillation pipeline on tenant golden data.** Export a tenant's golden dataset (Phase 11) to fine-tune or distill a small local model (Phase 5 Ollama) for their domain — a cost + latency + privacy story for enterprise.
8. **Disaster recovery & multi-region readiness.** Documented backup/restore for Postgres+Redis, RPO/RTO targets, checkpoint durability, region-failover runbook; chaos-test harness (kill worker/Redis/DB, assert recovery) building on the Phase 0C infra fixes.
9. **Compliance evidence automation.** Auto-collect SOC2/GDPR/HIPAA evidence (audit-trail exports, access reviews, data-residency proofs) from the existing `enterprise/compliance.py`; a compliance dashboard + scheduled evidence bundles.
10. **Marketplace monetization & revenue share.** Paid templates/skills/solutions with author payouts (Stripe Connect), usage-based pricing for premium agents — turns the marketplace (Phase 7) into an ecosystem.
11. **Notification inbox & digests.** A unified in-app inbox (approvals, goal completions, alerts, incidents) with digest scheduling and per-channel routing — beyond the fire-once `notification_service` (also fixes its no-retry gap). UI: `src/features/notifications/` inbox.
12. **Agent explainability.** "Why did the agent choose this plan/tool/model?" — surface planner reasoning, retrieval provenance, routing decision, and guardrail verdicts in the UI. Builds trust and aids debugging; leans on the trace store (13.1).
13. **Policy-as-code (OPA/Rego).** Let enterprises express tool/data policies as code, versioned and testable, evaluated by the policy engine — a step beyond the current glob matcher for regulated tenants.

### Phase 14 — Enterprise & Government Readiness (from the completeness re-audit)

A third re-audit found the plans strong on the *engine* but with a systematic **"sell-it-to-a-regulated-enterprise" blind spot** — capabilities missing from both the code AND Phases 0–13 that are hard legal/procurement gates for closing Indian-government, BFSI, and Fortune-500 deals. This phase closes them. Standard deliverable (backend + UI + e2e) applies; several items are legal/compliance gates, not features, and block revenue in regulated verticals regardless of product quality.

**Hard gates (block the first regulated deal — do these before scaling regulated verticals):**
1. **Data-residency enforcement + India DPDP Act.** Today only *attestation* exists (`compliance_v2.py` reads a declared region; `dpdp` = 0 hits). Build: a `region` attribute on tenant → region-routed Postgres/object storage, per-tenant data locality, and a residency-enforcement test. Add DPDP data-principal rights (consent management, right-to-erasure workflow, grievance officer, consent-purpose tracking) alongside the existing GDPR export. **Hard gate for RBI/SEBI/govt/EU.**
2. **GST-compliant, multi-currency billing.** Phase 1e only did Stripe subscriptions + usage records. Expand to a full billing engine: GST tax-invoice generation (GSTIN, HSN/SAC, IGST/CGST/SGST), proration, dunning, credit notes, INR+USD multi-currency, accurate usage metering. **You legally cannot invoice Indian customers without a GST tax invoice.**
3. **Trust & Compliance program.** No MFA exists today; no DPA artifact, sub-processor registry, or pen-test/VAPT cadence. Build: MFA/2FA (into Phase 1a auth), a DPA template + signed-DPA tracking, a public sub-processor list, a scheduled VAPT/pen-test program + platform red-team (distinct from the agent red-team in Phase 10), and SOC2 Type II evidence automation (extends Phase 13.9). **Every enterprise/govt security review gates on these.**

**Contractual & operational readiness:**
4. **Per-plan SLA + support tiers + escalation** — contractual uptime SLA, response-time tiers, support-ticketing/escalation product (entitlements-driven, Phase 1b).
5. **Public status page + uptime + incident comms** — driven from the HealthRegistry; also the evidence surface for the SLA (pairs with Phase 13.1 observability).
6. **Backup / PITR / tested DR drills** — deepen Phase 13.8 from *documented* to *executable*: WAL archiving (wal-g/barman), point-in-time restore, automated restore-drill + chaos test for Postgres+Redis, proven RPO/RTO evidence.
7. **Session management** — active-session listing, remote revocation, idle timeout, device management (into Phase 1a).

**Adoption & ecosystem (India-first makes these load-bearing):**
8. **Internationalization / localization** — English-only today (`i18n`/`Hindi` = 0). Build: backend message catalog, an agent output-language parameter (Hindi + regional per domain), locale-aware money/date/number formatting, and frontend i18n. **India field agents / CAs / govt staff operate in regional languages** — blocks mass adoption.
9. **Public API productization** — `/v1` versioning + deprecation policy, a developer portal, per-application scoped keys with published rate-limit contracts, and public **webhook-subscription CRUD + event catalog + signing-secret rotation** and a **public idempotency-key + bulk contract** (the internal delivery/idempotency engines exist — `webhook_service.py`, `reliability/idempotency.py` — but aren't exposed as versioned tenant contracts). Surfaces the existing Python/TS SDKs.
10. **Per-tenant staging / sandbox environment** — a "draft mode" or staging tenant to test agents against *real* connectors with test data before prod (today only the code sandbox + mock-tool `SimulationRunner` exist; enterprise change-management forbids testing automations in prod). Pairs with Phase 9 builder + Phase 7 simulation trial.
11. **PWA / mobile-first** — no `manifest.json`/service worker today; only partial responsive. Add PWA manifest + service worker + a mobile-first audit into Phase 12. Domain buyers (field agents, CAs, RTO/logistics staff) are phone-first.

**KPIs:** pass an enterprise security questionnaire end-to-end; issue a GST-compliant invoice in INR and USD; a tenant pins data to a region and a test proves cross-region reads are blocked; MFA enforced; agents respond in Hindi; a versioned public API with a dev portal and webhook subscriptions.

## Part D — Cross-Cutting Delivery Rules

1. **One track = one detailed plan** in `docs/superpowers/plans/`, written with the writing-plans skill (bite-sized TDD tasks, exact files, code in every step) immediately before that track starts. Phase 0 above is already at that granularity.
2. **Branching:** `feature/<phase>-<track>` per track; squash-merge; PRs ≤ 400 lines where feasible (split tracks into multiple PRs).
3. **Regression safety:** golden-goal eval suite (Phase 11.1 seed set can be created in week 1 with ~20 tasks) runs on every PR that touches `app/agent/`, `app/rag/`, or prompts — this is the guardrail that lets all other phases move fast.
4. **Measurement first:** every optimization phase (2, 4, 5) starts by recording the baseline metric on the golden set so "world-class" claims are numbers, not adjectives.
5. **Suggested staffing/parallelism:** **Phase 0A first, serially (~3–4 days) — it gates production and everything else.** Then 0B + 0C can run in parallel (~1 week). Then three parallel streams: (A) Phase 1 → 7 → 8 (product/SaaS), (B) Phase 2 → 5 → 6 (cost/models/skills), (C) Phase 4 → 3 → 11 (quality/RAG/evals); Phase 10 joins stream C after Phase 3; Phase 9 joins stream A after Phase 7; **Phase 13.1 (observability) should land early in stream B — it is the measurement substrate for Phases 2/3/4/5 KPIs**; Phase 12 and remaining Phase 13 items run continuously.
6. **Severity gate:** no capability track (Phase 1+) starts on a subsystem until that subsystem's CRITICAL and HIGH registry items are fixed — e.g. Phase 8 (A2A/civilization) must wait for H1(SSRF)/H2(IDOR)/0A.6(HITL); Phase 4 (RAG ingest) waits for H1(`/ingest/*` SSRF); the security track (Phase 10) subsumes H18/H19 and the audit deepening.

## Self-Review (per writing-plans skill)

- **Spec coverage:** all 25 numbered asks mapped (table in Part A); the second ask ("analyze the whole code, tell me the potential bugs, and your suggested features") is covered by the **Defect Registry** (B.1, ~89 verified defects with file refs) and **Phase 13** (13 suggested world-class capabilities); "world-class UI + e2e for everything" → the standard deliverable in Global Constraints + Phase 12 + per-phase UI/e2e; "million traffic" → Phase 2 + Phase 13.1 observability + Part D load profiles; "enhance if feature exists" → every phase starts from audited current state with file:line references.
- **Placeholder scan:** Phase 0A/0B/0C tasks are fully specified (files/lines/defects/tests/commit). Phases 1–13 are intentionally track-level per the scope rule — each declares design decisions, files, and KPIs and defers step-level code to its own plan (called out in the header and Part D.1), so no hidden "TBD"s.
- **Severity consistency:** every registry item maps to exactly one owning task/phase; CRITICALs C1–C7 all land in Phase 0A; the severity gate (Part D.6) prevents building on unfixed subsystems.
- **Type consistency:** new primitives named once and reused (`users`/`tenant_memberships`, `Skill`, `ExperimentRegistry`, `RetrievalPlanner`, `entitlements.py`, `ssrf_guard.assert_public_url`, `system_session`, Solutions). No task references undefined symbols within Phase 0.
- **Note on "one go":** the *plan* is delivered complete in one pass. Execution is deliberately staged (0A → 0B/0C → tracks) because ~89 defects and 13 tracks cannot be safely landed in a single change set without the regression scaffolding each task builds; attempting it at once would reintroduce exactly the "looks done, silently broken" class this audit found. Part C tracks are executed one detailed plan at a time.

## Appendix — Raw Audit Reports

The six Audit-2 bug-hunt reports (agent-loop, services/eventing, API/tenancy, DB/Celery/infra, frontend, governance/MCP/providers) and the five Audit-1 feature reports are archived under the session tasks directory. Each registry item traces to a verified finding with a concrete failure scenario in those reports. When writing a track's detailed plan, pull the corresponding agent report for the full reproduction detail.
