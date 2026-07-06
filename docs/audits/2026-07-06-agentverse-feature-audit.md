# AgentVerse Feature Audit
**Date:** 2026-07-06  
**Auditor:** QA Lead / Product Audit  
**Scope:** `agent-verse-backend/app/` + `agent-verse-frontend/src/features/` (SDK packages excluded)

---

## Feature Status Matrix

| # | Feature | Backend Path | Frontend Path | Status | Severity | Key Gaps | Tests Needed |
|---|---|---|---|---|---|---|---|
| 1 | Goal submission & lifecycle | `app/api/goals.py` | `src/features/goals/` | COMPLETE | LOW | Batch-status stub; feedback not persisted | Batch-status E2E; feedback DB write |
| 2 | Agent CRUD | `app/api/agents.py` | `src/features/agents/` | PARTIAL | MEDIUM | In-memory AgentStore as primary (multi-replica drift) | Multi-replica consistency test |
| 3 | Agent execution loop | `app/agent/loop.py`, `graph.py` | N/A | COMPLETE | LOW | None significant | — |
| 4 | HITL governance | `app/governance/hitl.py` | `src/features/governance/` | PARTIAL | HIGH | Frontend is 2-file stub; no HITL queue UI | Frontend approval-queue integration tests |
| 5 | Audit log | `app/governance/audit_v3.py` | `src/features/audit/` | PARTIAL | MEDIUM | AuditExplorerPage lacks tamper-verify UI trigger | Frontend tamper-verify E2E |
| 6 | Cost control | `app/governance/cost.py` | `src/features/settings/` | COMPLETE | LOW | None significant | — |
| 7 | RAG / knowledge | `app/rag_platform/` | `src/features/knowledge/` | PARTIAL | MEDIUM | Frontend is 2-file stub; no upload/search UI | Knowledge upload + search E2E |
| 8 | Memory system | `app/memory_v2/` | `src/features/memory/` | STUB | CRITICAL | `memory_v2` API is in-memory only (demo flag); data lost on restart | DB persistence; memory conflict resolution |
| 9 | MCP / connectors | `app/mcp/` | `src/features/connectors/` | COMPLETE | LOW | None significant | — |
| 10 | Scheduling / triggers | `app/triggers/` | `src/features/schedules/` | PARTIAL | MEDIUM | Frontend is 2-file stub | Cron-fire integration test; frontend E2E |
| 11 | RPA / browser | `app/rpa/` | `src/features/rpa/` | PARTIAL | HIGH | `GET /rpa/tools` has no auth; Playwright optional (silently falls to sim) | Auth on /rpa/tools; Playwright install test |
| 12 | Marketplace | `app/enterprise/marketplace_v2.py` | `src/features/marketplace/` | PARTIAL | MEDIUM | In-memory fallback when DB unavailable; frontend is 2-file stub | DB-backed publish/install E2E |
| 13 | Workflow builder | `app/agent/workflow_planner.py` | `src/features/workflow-builder/` | PARTIAL | MEDIUM | Frontend is 2-file stub; no visual node-graph editor | Workflow create/execute E2E |
| 14 | Observability | `app/observability/` | `src/features/observability/` | COMPLETE | LOW | TraceExplorer.tsx exists but no test | Trace-explorer E2E |
| 15 | Multi-tenancy | `app/tenancy/` + `app/db/rls.py` | N/A (cross-cutting) | COMPLETE | LOW | Raw `SET LOCAL` used in a few handlers bypassing helper | Confirm all handlers use `sqlalchemy_rls_context` |
| 16 | Skills runtime | `app/skills_runtime/executor.py` | `src/features/skills/` | STUB | HIGH | Module-level dicts for storage; 0074 migration exists but API ignores it | DB-backed skill CRUD; frontend E2E |
| 17 | SSE streaming | `app/services/goal_service.py` | `src/lib/sse/useGoalStream.ts` | COMPLETE | LOW | None significant | Reconnect / Last-Event-ID test |
| 18 | Billing | `app/api/billing.py` | `src/features/settings/` | PARTIAL | MEDIUM | Razorpay only (INR); no Stripe fallback wired; webhook secret validation optional | Webhook signature validation test |

---

## Feature-by-Feature Analysis

### 1. Goal Submission and Lifecycle
**Backend path:** `app/api/goals.py` | **Frontend path:** `src/features/goals/`

**Backend completeness:** Full lifecycle implemented — submit, list, get, cancel, pause, resume, stream (SSE), eval, audit, traces, lineage, attempts, ghost-run (A/B strategies), batch submit, feedback, and six persistence-mode control endpoints. Auth is enforced on every endpoint via `_require_tenant()` (goals.py:105–109). Tenant isolation is maintained by passing `tenant_ctx` through to `GoalService`.

**Frontend completeness:** `GoalsListPage.tsx`, `GoalDetailPage.tsx`, `GhostRunPage.tsx`, `GoalDNAPage.tsx`, `GoalDiffPage.tsx` all exist with full TanStack Query + mutation wiring. `goalsApi` in `client.ts:237` covers all primary endpoints.

**Auth enforcement:** PASS — every route handler calls `_require_tenant(request)`.

**Tenant isolation:** PASS — `tenant_ctx` is passed to service; service passes it through to all DB queries.

**Gaps:**
- `GET /goals/batch/{batch_id}/status` (goals.py:631–637) returns a static `{"message": "Track individual goal_ids from batch submission"}`. There is no stored batch record, no aggregated status, and the `batch_id` is silently discarded after `POST /goals/batch` returns.
- `POST /goals/{goal_id}/feedback` (goals.py:830–859) iterates `_default_calibration_store._records` (a process-local in-memory list at goals.py:846). No DB write occurs; feedback is lost on restart.
- `goal_lineage` query at goals.py:701 issues `SET LOCAL app.tenant_id = :tid` directly via raw SQL text rather than using `sqlalchemy_rls_context`. This deviates from the established pattern and could miss RLS if the session is outside a transaction.

**Tests:** Extensive — `tests/api/` has 115 files; specific goal tests cover submission, status transitions, SSE, eval, and approval flows.

---

### 2. Agent CRUD
**Backend path:** `app/api/agents.py` | **Frontend path:** `src/features/agents/`

**Backend completeness:** All CRUD operations implemented. `AgentStore` (agents.py:90) stores agents as `dict[tuple[str, str], dict[str, Any]]` (in-memory by default). The lifespan in `main.py` swaps it for a DB-backed version, but the in-memory store is the authoritative object between restarts — an agent created on Replica A is invisible to Replica B until the next DB sync cycle.

**Frontend completeness:** Full suite — `AgentsListPage.tsx`, `AgentCreatePage.tsx`, `AgentDetailPage.tsx`, `AgentIdentityPage.tsx`, `AgentPersonalityPage.tsx`, `AgentRadarPage.tsx`.

**Auth enforcement:** PASS.

**Tenant isolation:** PASS — all store methods accept `TenantContext`.

**Gaps:**
- Multi-replica in-memory state drift: when `manage_pools=False` (test path), the in-memory store is never synced to DB. In production, replica scale-out means agents created on one instance may not immediately appear on others.
- Agent snapshot persistence (agents.py:26–83) falls back silently when `db is None`.

**Tests:** Good coverage in `tests/api/`.

---

### 3. Agent Execution Loop
**Backend path:** `app/agent/loop.py`, `app/agent/graph.py`

**Backend completeness:** Full LangGraph state machine (`AgentGraph` in graph.py) with `initialize → plan → execute → verify → (complete | replan | max_iterations_exceeded)` flow. `AgentLoop` (loop.py) serves as a simpler fallback when `AgentGraph` fails to construct. Three distinct LLM roles (planner, executor, verifier), HITL gate for high-risk steps, Redis checkpointing, phase-3 grounding/consensus/synthesis.

**Auth enforcement:** N/A — internal component, not a direct HTTP endpoint.

**Tenant isolation:** PASS — `TenantContext` threaded through all operations; RLS applied when DB queries occur.

**Gaps:** None critical. `_loop_is_patched` detection (tasks.py:656–661) is a necessary but fragile test-compatibility shim.

**Tests:** 68 files in `tests/agent/` — strongest test coverage in the codebase.

---

### 4. HITL Governance
**Backend path:** `app/governance/hitl.py` | **Frontend path:** `src/features/governance/`

**Backend completeness:** Dual-mode implementation — in-process `asyncio.Event` for single-replica, Redis BLPOP for cross-replica survival (hitl.py:1–10). `ApprovalRequest` dataclass, `HITLGateway`, `publish_resolution()`, and `_wait_for_result()` are all implemented. `POST /governance/approvals/{id}/approve` and `/reject` exist in governance.py.

**Frontend completeness:** `src/features/governance/GovernancePage.tsx` (2 files only). There is no dedicated approval-queue page, no real-time HITL notification UI, and no "pending approvals" list with approve/reject buttons beyond the basic governance page. The `src/features/approvals/` directory exists but content was not further examined.

**Auth enforcement:** PASS — `_require_tenant` on all governance routes. Some endpoints additionally check `require_role` (governance.py:21).

**Tenant isolation:** PASS.

**Gaps:**
- Frontend approval queue is not surfaced as a first-class page with real-time updates (SSE/websocket).
- The `src/features/approvals/` directory exists but the governance page is the only surface — approval state is not visible until a goal explicitly enters `waiting_approval`.

**Tests:** 37 files in `tests/governance/`.

---

### 5. Audit Log
**Backend path:** `app/governance/audit_v3.py` | **Frontend path:** `src/features/audit/`

**Backend completeness:** `AuditRecord` dataclass with full hash-chain integrity (v3 fields: tool_args_hash, actor_ip, delegation_chain_hash, metadata_hash). Export as JSON/CSV (audit_v3.py:28–29). Tamper-verification via recompute-and-compare.

**Frontend completeness:** `AuditExplorerPage.tsx` exists. `auditApi` in client.ts:1392 covers export and query. However, the tamper-verify endpoint (`POST /governance/audit/verify`) is not exposed as a visible UI action in the explorer.

**Auth enforcement:** PASS.

**Tenant isolation:** PASS — audit records are filtered by `tenant_id` in all queries.

**Gaps:**
- Tamper-verify endpoint exists in the backend but the frontend `AuditExplorerPage` provides no UI button to trigger it.
- The `AuditExplorerPage.test.tsx` only has 1 test file; coverage of export and verify flows is likely minimal.

**Tests:** 37 files in `tests/governance/` (shared with HITL).

---

### 6. Cost Control
**Backend path:** `app/governance/cost.py` | **Frontend path:** `src/features/settings/`

**Backend completeness:** `CostController` enforces per-goal and per-tenant-daily USD budgets. Redis-backed in production for cross-replica accuracy with automatic midnight UTC reset. `check_and_record()` is called before every tool execution in the agent graph.

**Frontend completeness:** `BudgetManagerPage.tsx`, `CostBreakdown.tsx`, `CostDashboardPage.tsx` — three dedicated pages with TanStack Query wiring.

**Auth enforcement:** PASS.

**Tenant isolation:** PASS — all cost keys are `(tenant_id, goal_id)` scoped.

**Gaps:** None significant. The per-goal budget uses an `asyncio.Lock` keyed by `"tenant_id:goal_id"` (cost.py:70) — this lock is process-local, so cross-replica TOCTOU races are possible under very high concurrency (mitigated by Redis INCR atomicity but not eliminated for the in-memory path).

**Tests:** 1 file in `tests/costs/`. Cost enforcement in the agent loop is tested via `tests/agent/`.

---

### 7. RAG / Knowledge
**Backend path:** `app/rag_platform/`, `app/knowledge/`, `app/api/knowledge.py` | **Frontend path:** `src/features/knowledge/`

**Backend completeness:** `rag_platform/` has `retriever.py`, `reranker.py`, `query_planner.py` with strategy enum (`AUTO`, `VECTOR`, `HYBRID`, `GRAPH`). `app/knowledge/` is the full `KnowledgeStore` backed by pgvector. `app/api/knowledge.py` has 27 routes (upload, list, search, delete, citation tracking).

**Frontend completeness:** `KnowledgePage.tsx` (2 files). The knowledge API is exposed in `client.ts:823` (`knowledgeApi`) but the frontend page is minimal — no upload UI, no chunk browser, no search interface beyond basic listing.

**Auth enforcement:** PASS.

**Tenant isolation:** PASS — KnowledgeStore passes `tenant_ctx` to all queries.

**Gaps:**
- Frontend has no document-upload flow or chunk search UI despite the backend supporting 27 routes.
- Knowledge citations (migration 0035) are tracked in the DB but not visualised in the frontend (frontend `CitationList.tsx` in goals/ only shows citations on goal detail, not as a first-class knowledge UI).

**Tests:** 4 files in `tests/knowledge/` — thin for a 27-route subsystem.

---

### 8. Memory System
**Backend path:** `app/memory_v2/`, `app/api/memory_v2.py` | **Frontend path:** `src/features/memory/`

**Backend completeness:** `app/memory_v2/models.py` defines `MemoryLifecycleState`, `MemoryPrivacyClass` etc. `app/memory_v2/consolidation.py` exists. **However**, the API layer (`app/api/memory_v2.py:18`) declares:
```python
# In-memory store for demo
_memories: dict[str, dict] = {}
```
All CRUD operations read/write this module-level dict. Migration `0062_knowledge_v2.py` exists but the API never uses the DB for memory-v2 entities. All memory-v2 data is lost on every process restart.

**Frontend completeness:** `MemoryExplorerPage.tsx` (2 files) — minimal implementation.

**Auth enforcement:** PASS — `_require_tenant` called on all routes (memory_v2.py:12–16).

**Tenant isolation:** PARTIAL — the in-memory dict is keyed as `"{tenant_id}:{memory_id}"` (memory_v2.py:71), providing logical isolation but no RLS enforcement.

**Gaps (CRITICAL):**
- All memory-v2 state is purely in-process ephemeral. No DB persistence despite the migration existing.
- `_memories` is a module-level global — shared across all requests to the same worker process; concurrent writes have no locking.
- No conflict-resolution strategy is implemented despite `_conflicts: dict` being declared (memory_v2.py:20).

**Tests:** 9 files in `tests/memory/` (covers `app/memory/` — it is unclear how much covers `memory_v2`).

---

### 9. MCP / Connectors
**Backend path:** `app/mcp/`, `app/api/connectors.py` | **Frontend path:** `src/features/connectors/`

**Backend completeness:** Full implementation — `registry.py` (per-tenant connector registry backed by Redis), `client.py` (tool discovery + execution with self-healing), `oauth.py` (PKCE flow), `tool_cache.py`, `capability_search.py`, `openapi_importer.py`. `app/api/connectors.py` has 17 routes.

**Frontend completeness:** `ConnectorsCatalogPage.tsx`, `ConnectorsRegisteredPage.tsx`, `ConnectorDetailPage.tsx`, `OAuthCallbackPage.tsx`, `OAuthPopupButton.tsx` — complete CRUD and OAuth flow UI.

**Auth enforcement:** PASS.

**Tenant isolation:** PASS — `MCPRegistry` is keyed by `tenant_id`; Redis keys include tenant scope.

**Gaps:** None critical.

**Tests:** 55 files in `tests/mcp/` — second highest test count.

---

### 10. Scheduling / Triggers
**Backend path:** `app/triggers/`, `app/api/schedules.py` | **Frontend path:** `src/features/schedules/`

**Backend completeness:** `NLScheduler` (NL → `TriggerSpec`), `ScheduleStore`, Celery `fire_due_schedules` task, DB-backed via migration `0007_scheduling.py`. `app/api/schedules.py` has 10 routes including cron, one-time, interval, and webhook trigger types.

**Frontend completeness:** `SchedulesPage.tsx` (2 files) — minimal implementation.

**Auth enforcement:** PASS — `_require_tenant` on all schedule routes.

**Tenant isolation:** PASS — schedule queries filter by `tenant_id` via RLS.

**Gaps:**
- Frontend has no cron-expression builder or schedule visualisation (next-fire preview).
- Webhook-triggered schedules generate `webhook_token` (schedules.py:8) but the frontend has no webhook URL display or copy-to-clipboard UI.

**Tests:** 6 files in `tests/triggers/`.

---

### 11. RPA / Browser
**Backend path:** `app/rpa/`, `app/api/rpa.py` | **Frontend path:** `src/features/rpa/`

**Backend completeness:** `RPAExecutor` in executor.py uses Playwright when available, falls back to simulation. `session_manager.py`, `credential_injector.py`, `artifacts.py` exist.

**Frontend completeness:** `RpaLivePage.tsx` (1 file) — live screenshot viewer only.

**Auth enforcement:** FAIL — `GET /rpa/tools` (rpa.py:31–34) has **no `_require_tenant` call**. The full tool inventory is accessible without authentication.

**Tenant isolation:** PASS on all other endpoints. FAIL for `/rpa/tools` (unauthenticated).

**Gaps (HIGH):**
- `GET /rpa/tools` is an unauthenticated public endpoint exposing the full tool capability list — security gap.
- When Playwright is not installed, every RPA execute returns simulation data without any warning to the caller. The response schema is identical.
- RPA sessions (`/rpa/sessions`) are stored in `request.app.state.rpa_session_store` which may be in-memory depending on lifespan wiring.

**Tests:** 11 files in `tests/rpa/`.

---

### 12. Marketplace
**Backend path:** `app/enterprise/marketplace_v2.py` | **Frontend path:** `src/features/marketplace/`

**Backend completeness:** `MarketplaceV2` class with `get_template()`, `list_templates()`, `publish_template()`, `install()`, `add_review()`. DB-backed when available; falls back to in-memory cache when DB is `None` (marketplace_v2.py:1285: `# In-memory cache keyed by template_id`).

**Frontend completeness:** `MarketplacePage.tsx` (2 files) — basic template listing only.

**Auth enforcement:** PASS — marketplace routes in `app/api/enterprise.py` require tenant.

**Tenant isolation:** PASS in DB path; in-memory fallback is not tenant-isolated (all tenants share the same dict).

**Gaps:**
- In-memory fallback is shared across all tenants — a template published by Tenant A is visible to Tenant B during the in-memory path.
- No frontend for template detail, reviews, or version history.

**Tests:** 27 files in `tests/enterprise/` (covers all enterprise features).

---

### 13. Workflow Builder
**Backend path:** `app/agent/workflow_planner.py`, `app/agent/workflow_executor.py` | **Frontend path:** `src/features/workflow-builder/`

**Backend completeness:** `build_static_workflow()` (workflow_planner.py) creates `WorkflowDefinition` from a list of steps. `WorkflowExecutor` (workflow_executor.py) runs them sequentially with per-step LLM evaluation. DB persistence via migration `0046_workflows.py`.

**Frontend completeness:** `WorkflowBuilderPage.tsx` (2 files) — stub page.

**Auth enforcement:** PASS — `/workflows` routes require tenant.

**Tenant isolation:** PASS — workflow DB queries include `tenant_id`.

**Gaps:**
- Frontend is a stub with no visual node-graph editor (React Flow or equivalent not implemented).
- No drag-and-drop step creation; no step-result visualisation.
- `workflowsApi` exists in client.ts:1608 but the builder page does not appear to call it.

**Tests:** Workflow execution tested via `tests/agent/` (workflow planner/executor tests).

---

### 14. Observability
**Backend path:** `app/observability/` | **Frontend path:** `src/features/observability/`

**Backend completeness:** `StructuredLogStore` (Redis Streams backed, in-memory fallback), Prometheus metrics (`record_goal_duration`, `record_cost_usd`), OTel tracing with OTLP export, `cost_breakdown_api.py`, `health.py`. `GET /observability/logs`, `/logs/stream` (SSE), `/metrics`, `/timeseries` all implemented.

**Frontend completeness:** `ObservabilityPage.tsx`, `CostDashboardPage.tsx`, `CostBreakdown.tsx`, `TraceExplorer.tsx` — four pages with reasonable coverage.

**Auth enforcement:** PASS.

**Tenant isolation:** PASS — Redis Streams keyed as `av:logs:{tenant_id}`.

**Gaps:**
- `TraceExplorer.tsx` exists but has no unit test.
- Log streaming (`GET /observability/logs/stream`) reconnect-on-disconnect is not tested.

**Tests:** 6 files in `tests/observability/`.

---

### 15. Multi-Tenancy
**Backend path:** `app/tenancy/`, `app/db/rls.py`

**Backend completeness:** `TenantMiddleware` resolves API key or Bearer JWT on every request. `SecurityHeadersMiddleware` adds OWASP headers. `SlidingWindowRateLimiter` enforces per-plan RPM limits. `rls.py` provides `sqlalchemy_rls_context`, `rls_context` (asyncpg), and `system_session` — all correct implementations.

**Auth enforcement:** PASS — bypass list is minimal and intentional (`/health`, `/docs`, `/tenants/signup`, `/auth/*`).

**Tenant isolation:** PASS on all compliant handlers. **Partial concern** — several handlers in `goals.py` (lines 701, 769) and `governance.py` use raw `text("SET LOCAL app.tenant_id = :tid")` instead of the `sqlalchemy_rls_context` context manager. This is functionally correct if always inside a transaction, but deviates from the safe pattern and risks missing a `BEGIN` in some code paths.

**Gaps:**
- 3 handlers issuing raw `SET LOCAL` without the context manager: `get_goal_lineage` (goals.py:701), `get_goal_attempts` (goals.py:769). These should use `sqlalchemy_rls_context`.

**Tests:** 12 files in `tests/tenancy/`.

---

### 16. Skills Runtime
**Backend path:** `app/skills_runtime/executor.py` | **Frontend path:** `src/features/skills/`

**Backend completeness:** `TriggerMatcher`, `ScopedPermissionChecker`, `SkillExecutor` all implemented in executor.py. `BUILTIN_SKILLS` defined in models.py. **However**, the API layer (`app/api/skills_runtime.py:34–38`) declares:
```python
_platform_skills: dict[str, dict] = {}
_tenant_skills: dict[str, list] = {}  # tenant_id → skills
_executions: dict[str, list] = {}     # tenant_id → executions
_enabled_skills: dict[str, set] = {}  # tenant_id → {skill_ids}
```
All skill data lives in module-level dicts. Migration `0074_skills_table.py` creates a `skills` DB table but the API never reads from or writes to it.

**Frontend completeness:** `SkillsPage.tsx` (2 files) — stub.

**Auth enforcement:** PASS.

**Tenant isolation:** PARTIAL — `_tenant_skills` is keyed by `tenant_id`, providing logical isolation but no cross-tenant boundary enforcement.

**Gaps (HIGH):**
- All skill registrations are ephemeral; lost on every restart or rolling deploy.
- Migration 0074 exists but is unused by the API — dead migration.
- `_executions` dict has no size bound; unbounded memory growth under sustained load.

**Tests:** 1 file in `tests/skills/` — critically under-tested.

---

### 17. SSE Streaming
**Backend path:** `app/services/goal_service.py` | **Frontend path:** `src/lib/sse/useGoalStream.ts`

**Backend completeness:** `subscribe_events()` is an `AsyncGenerator` that fans out events from a per-goal `asyncio.Queue`. Redis pub/sub bridge publishes events from Celery workers to the API process (tasks.py:516–524). `Last-Event-ID` header is respected for resumption (goals.py:387). Terminal status sends `_SENTINEL` to close streams cleanly.

**Auth enforcement:** PASS — `_require_tenant(request)` called in `stream_goal` (goals.py:383).

**Tenant isolation:** PASS — pub/sub channel is `goal_events:{tenant_id}:{goal_id}`.

**Gaps:**
- `GET /goals/{goal_id}/stream` does not verify that `goal_id` belongs to the tenant (it checks auth but not resource ownership before streaming) — an authenticated tenant could stream events for another tenant's goal if they know the ID.
- No automated test for Last-Event-ID reconnection semantics.

**Tests:** Goal streaming is covered indirectly in `tests/api/` and `tests/services/`.

---

### 18. Billing
**Backend path:** `app/api/billing.py` | **Frontend path:** `src/features/settings/`

**Backend completeness:** Razorpay integration (billing.py:21–49) with INR pricing. `_get_razorpay()` returns `None` if `razorpay_key_secret` is not configured — graceful degradation. Webhook signature validation present (billing.py, HMAC-SHA256). Legacy Stripe mentions in comments but not wired. `billingApi` in client.ts:740.

**Frontend completeness:** `BillingPage.tsx` exists in `src/features/settings/`. Covers plan selection and payment flow.

**Auth enforcement:** PASS.

**Tenant isolation:** PASS.

**Gaps:**
- Entire billing system is Razorpay/INR-only. Deploying outside India requires adding a Stripe/USD provider — no abstraction layer exists.
- Webhook HMAC validation uses `os.getenv("RAZORPAY_WEBHOOK_SECRET")` — if not set, the comparison always passes (billing.py effectively accepts any webhook payload when secret is unset).
- `gst_billing.py` (`/billing/gst`) is a separate India-specific GST invoicing router with no equivalent for other tax systems.

**Tests:** 1 file in `tests/costs/`; billing-specific tests not found.

---

## Top 10 Issues

### Issue 1 — CRITICAL: `GET /rpa/tools` is unauthenticated
**File:** `app/api/rpa.py:31–34`  
**Evidence:**
```python
@router.get("/tools")
async def list_rpa_tools() -> list[dict[str, Any]]:
    """Return built-in RPA tool metadata for agent clients."""
    return [dict(tool) for tool in RPA_TOOLS]
```
No `_require_tenant(request)` call. Any unauthenticated HTTP client can enumerate all RPA tool capabilities, names, argument schemas, and descriptions. Every other endpoint in this router calls `_require_tenant`.  
**Fix:** Add `tenant: Any = _require_tenant(request)` as the first line of the handler.  
**Severity:** HIGH

---

### Issue 2 — CRITICAL: `memory_v2` API has no DB persistence
**File:** `app/api/memory_v2.py:18–20`  
**Evidence:**
```python
# In-memory store for demo
_memories: dict[str, dict] = {}
_conflicts: dict[str, list] = {}
```
All 10 `memory-v2` API endpoints read/write these module-level dicts. Migration `0062_knowledge_v2.py` creates the relevant schema but the API never interacts with it. All memory-v2 entries are lost on every process restart, rolling deploy, or scale-out event.  
**Fix:** Replace module-level dicts with DB-backed repository using `sqlalchemy_rls_context`, following the same pattern as `KnowledgeStore`.  
**Severity:** CRITICAL

---

### Issue 3 — CRITICAL: `skills_runtime` API has no DB persistence
**File:** `app/api/skills_runtime.py:34–38`  
**Evidence:**
```python
_platform_skills: dict[str, dict] = {}
_tenant_skills: dict[str, list] = {}  # tenant_id → skills
_executions: dict[str, list] = {}     # tenant_id → executions
_enabled_skills: dict[str, set] = {}  # tenant_id → {skill_ids}
```
Migration `0074_skills_table.py` creates a `skills` table that is never used. All custom tenant skills are ephemeral. `_executions` dict has no size bound — potential memory leak under load.  
**Fix:** Route all skill CRUD through the `skills` DB table; use `sqlalchemy_rls_context` for tenant isolation.  
**Severity:** HIGH

---

### Issue 4 — HIGH: SSE stream does not verify goal ownership
**File:** `app/api/goals.py:359–408`  
**Evidence:** `stream_goal()` calls `_require_tenant(request)` to verify the caller is authenticated, but never calls `svc.get_goal(goal_id, tenant_ctx)` to confirm the goal belongs to that tenant before starting the event stream. An authenticated tenant who knows (or guesses) another tenant's `goal_id` UUID can receive a real-time stream of that goal's execution events.  
**Fix:** Add `await svc.get_goal(goal_id=goal_id, tenant_ctx=request.state.tenant)` before the `StreamingResponse` and let `NotFoundError` surface as 404.  
**Severity:** HIGH

---

### Issue 5 — HIGH: Webhook billing secret not enforced when env var unset
**File:** `app/api/billing.py` (Razorpay webhook handler)  
**Evidence:** Webhook HMAC validation reads `RAZORPAY_WEBHOOK_SECRET` from env. If the variable is absent or empty, the comparison against an empty string always passes — any HTTP client can forge a billing webhook event, potentially triggering plan upgrades or payment confirmations.  
**Fix:** Raise `HTTPException(400)` if the secret env var is empty when the webhook handler is invoked. Add this to startup validation in `core/config.py` Settings when `razorpay_key_id` is set.  
**Severity:** HIGH

---

### Issue 6 — HIGH: `GET /goals/batch/{batch_id}/status` is a non-functional stub
**File:** `app/api/goals.py:630–637`  
**Evidence:**
```python
@router.get("/batch/{batch_id}/status")
async def get_batch_status(request: Request, batch_id: str) -> dict[str, Any]:
    """Get status summary for a batch submission ..."""
    _require_tenant(request)
    return {
        "batch_id": batch_id,
        "message": "Track individual goal_ids from batch submission",
    }
```
`POST /goals/batch` returns a `batch_id` and list of `goal_id`s. The corresponding `GET` status endpoint ignores `batch_id` entirely and returns a static message. Callers have no server-side way to aggregate batch progress.  
**Fix:** Store batch metadata (batch_id → list of goal_ids + tenant_id) in Redis at submission time; return aggregated goal statuses in `GET /batch/{id}/status`.  
**Severity:** MEDIUM

---

### Issue 7 — MEDIUM: `goal_lineage` and `goal_attempts` bypass `sqlalchemy_rls_context`
**Files:** `app/api/goals.py:701`, `app/api/goals.py:769`  
**Evidence:**
```python
await session.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": tenant.tenant_id})
```
These handlers issue raw `SET LOCAL` SQL outside the established `sqlalchemy_rls_context` async context manager. This is functionally equivalent when inside a transaction, but if the session's auto-begin is deferred or a savepoint is used, the RLS variable may not be set before the query executes.  
**Fix:** Wrap these handlers' DB calls in `async with sqlalchemy_rls_context(session, tenant.tenant_id)`.  
**Severity:** MEDIUM

---

### Issue 8 — MEDIUM: `goal_feedback` endpoint is fire-and-forget with no persistence
**File:** `app/api/goals.py:830–859`  
**Evidence:** `POST /goals/{id}/feedback` iterates `_default_calibration_store._records` (a process-local list in `app.intelligence.verifier_calibration`) and returns `{"status": "feedback_recorded"}`. No database insert occurs. Feedback data is lost on restart.  
**Fix:** Insert feedback into the `goal_feedback` table (migration `0082_goal_feedback.py` already exists). The calibration store update can remain as a side-effect.  
**Severity:** MEDIUM

---

### Issue 9 — MEDIUM: Marketplace in-memory fallback is cross-tenant
**File:** `app/enterprise/marketplace_v2.py:1285`  
**Evidence:**
```python
# In-memory cache keyed by template_id (used when DB is unavailable)
```
When the DB is unavailable, `list_templates()` and `install()` fall back to a process-level dict that is not partitioned by tenant. A template published by Tenant A is returned to Tenant B in the in-memory path.  
**Fix:** Either enforce tenant filtering in the in-memory fallback (keyed as `{tenant_id}:{template_id}`), or treat DB unavailability as a hard error for write operations.  
**Severity:** MEDIUM

---

### Issue 10 — MEDIUM: `tests/skills/` has 1 file — skills runtime is critically under-tested
**File:** `tests/skills/` (1 test file)  
**Evidence:** The `skills_runtime` module registers the executor, permission checker, and trigger matcher as module-level singletons. With only 1 test file and module-level dict stores that have no reset between tests, test isolation is fragile. The `_executions` list has no cap, `_tenant_skills` and `_enabled_skills` have no eviction.  
**Fix:** Add tests covering: skill DB write/read cycle (once persistence is fixed), cross-tenant isolation of `_tenant_skills`, execution history pagination/cleanup, and permission scope enforcement.  
**Severity:** MEDIUM

---

## Coverage Gaps Summary

| Subsystem | Test Files | Risk |
|---|---|---|
| `skills` | 1 | HIGH — stores are in-memory, tests may share state |
| `persistence` | 1 | HIGH — goal persistence loop untested |
| `net` | 1 | LOW — outbound HTTP helpers |
| `compliance` | 1 | MEDIUM — GDPR export path lightly tested |
| `costs` | 1 | MEDIUM — billing tests absent |
| `live` | 1 | LOW — live integration tests |
| `agentic` | 1 | MEDIUM — agentic workflow tests |
| Knowledge API | 4 | MEDIUM — 27 routes, only 4 test files |

---

## Auth Enforcement Summary

| Status | Endpoints |
|---|---|
| PASS | All routes in goals, agents, governance, connectors, knowledge, memory_v2, skills_runtime, schedules, observability, billing, tenants |
| FAIL | `GET /rpa/tools` — no `_require_tenant` call |
| BYPASS (intentional) | `/health`, `/metrics`, `/docs`, `/redoc`, `/openapi.json`, `/tenants/signup`, `/auth/login`, `/auth/callback`, `/status` |

---

## Tenant Isolation Summary

| Status | Layer |
|---|---|
| PASS | DB layer — RLS `SET LOCAL app.tenant_id` via `sqlalchemy_rls_context` |
| PASS | App layer — `TenantContext` passed to all service methods |
| PARTIAL | `memory_v2` — in-memory dict keyed by `tenant_id` but no RLS enforcement |
| PARTIAL | `skills_runtime` — `_tenant_skills` keyed by `tenant_id` but no cross-tenant hard boundary |
| PARTIAL | `marketplace_v2` in-memory fallback — shared across all tenants |
| CONCERN | `goals.py:701,769` — raw `SET LOCAL` outside context manager |
