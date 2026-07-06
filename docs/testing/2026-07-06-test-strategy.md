# AgentVerse Test Strategy
**Date:** 2026-07-06  
**Author:** Test Strategy Architect  
**Based on:** Direct analysis of `agent-verse-backend/tests/`, `agent-verse-frontend/src/` test files, `agent-verse-frontend/e2e/`, `playwright.config.ts`, and `pyproject.toml`

---

## Existing Test Inventory

### Backend (`agent-verse-backend/tests/`)

**Total test files:** 662  
**Test markers defined in `pyproject.toml`:**
```
integration — tests requiring real Redis/Postgres via testcontainers
slow        — tests that hit real LLM providers (opt-in)
```

**Test file distribution by subdirectory:**

| Directory | Domain |
|-----------|--------|
| `tests/agent/` | LangGraph loop, planner/executor/verifier, DAG, debate, goal tree |
| `tests/api/` | REST endpoint tests for all routers |
| `tests/analytics/` | Metrics aggregation, self-improvement scoring |
| `tests/auth/` | API key auth, tenant isolation, session management |
| `tests/civilization/` | Multi-agent civilization, blackboard, spawn lineage |
| `tests/collab/` | CRDT collaboration, WebSocket |
| `tests/compliance/` | GDPR/SOC2/PCI compliance |
| `tests/core/` | Config, settings, base utilities |
| `tests/costs/` | Budget enforcement, cost tracking, Redis cross-replica |
| `tests/db/` | Alembic migrations, RLS, model fixtures |
| `tests/governance/` | Policies, HITL, audit, emergency stop |
| `tests/intelligence/` | Eval runner, meta-agent planner, prompt optimizer |
| `tests/knowledge/` | KnowledgeStore, semantic cache, pgvector |
| `tests/mcp/` | MCP client, tool registry, OAuth |
| `tests/memory/` | ExecutionMemory, LongTermMemoryStore |
| `tests/observability/` | Traces, spans, metrics endpoints |
| `tests/providers/` | LLM provider adapters, FakeProvider |
| `tests/rag/` | RAG pipeline, hybrid search, embeddings |
| `tests/reliability/` | Circuit breakers, bulkhead, rollback |
| `tests/security/` | Rate limiting, injection prevention |
| `tests/tenancy/` | Multi-tenant isolation, RLS enforcement |
| `tests/triggers/` | NLScheduler, cron, TriggerSpec |
| Root-level files | Contract schema, gap completions, mock fixes, wiring integrity |

### Frontend Unit/Component (`agent-verse-frontend/src/`)

**Total `.test.tsx/.test.ts` files:** 93  
Files co-located with components and pages (e.g., `GoalsListPage.test.tsx`, `ExecutionTimeline.test.tsx`, `GoalDetailPage.test.tsx`)

### Frontend E2E (`agent-verse-frontend/e2e/`)

**Total Playwright spec files:** 68  
**Playwright projects defined in `playwright.config.ts`:**

| Project | Match Pattern | Purpose |
|---------|---------------|---------|
| `smoke-live` | `**/*.smoke.spec.ts` | Critical paths, fast |
| `full-live` | `**/*.spec.ts` | All specs |
| `mobile` | `**/*.spec.ts` | Pixel 5 viewport |
| `accessibility` | `**/*.a11y.spec.ts` | Axe/ARIA checks |
| `security-smoke` | `**/*.security.spec.ts` | Auth/injection |
| `failure-states` | `**/*.failure.spec.ts` | Error boundary paths |
| `provider-live` | `**/*.provider.spec.ts` | Real LLM gated |
| `eval-regression` | `**/*.eval.spec.ts` | Eval scoring regression |
| `multimodal-live` | `**/*.multimodal.spec.ts` | Vision/audio features |
| `rag-live` | `**/*.rag.spec.ts` | RAG with real data |
| `governance-live` | `**/*.governance.spec.ts` | Policy/audit |
| `observability-live` | `**/*.observability.spec.ts` | Metrics/trace |

---

## Coverage Gaps Identified

### Backend Gaps

| Feature Area | Current State | Gap |
|---|---|---|
| **Tenant isolation / RLS** | `tests/tenancy/test_policy_isolation.py` exists | Cross-tenant data leak paths under concurrent writes not covered |
| **Agent loop replanning** | `test_agent_loop_comprehensive.py` exists | Replan-on-failure cycle count exhaustion edge cases sparse |
| **HITL cross-replica** | `test_hitl_cross_replica.py` exists | Redis pub/sub delivery guarantee under partition not tested |
| **Provider failover** | `tests/providers/` exists | Fallback chain (Anthropic → OpenAI → FakeProvider) not tested as a unit |
| **Cost anomaly detection** | `test_cost_atomic.py` exists | Budget-exceeded mid-goal-execution rollback path not covered |
| **MCP OAuth PKCE** | `tests/mcp/` exists | Full PKCE code-verifier/challenge round-trip not tested |
| **Celery queue routing** | `tests/scaling/` exists | Per-plan queue (`goals.free`/`goals.enterprise`) routing validation absent |
| **RLS enforcement** | `tests/db/` exists | Direct SQL injection attempts against RLS policies not in suite |
| **SDK contract** | `test_contract_schema.py` exists | TypeScript SDK type contracts not validated against OpenAPI export |

### Frontend Unit Gap

| Component | Test File | Gap |
|---|---|---|
| `GoalsListPage.tsx` | `GoalsListPage.test.tsx` ✅ | Error state (isError) not tested |
| `GovernancePage.tsx` | `GovernancePage.test.tsx` ✅ | Tab keyboard navigation not tested |
| `WorkflowBuilderPage.tsx` | `WorkflowBuilderPage.test.tsx` ✅ | Undo/redo stack behavior not tested |
| `KnowledgePage.tsx` | `KnowledgePage.test.tsx` ✅ | Streaming RAG response parsing not tested |
| `MissionControlLayout.tsx` | None | No unit tests for layout wrapper |
| `CommandPalette.tsx` | None | No unit tests for command palette |
| `LiveCostTicker.tsx` | None | No unit tests for real-time cost display |
| `AppLayout.tsx` | None | No layout integration test |

---

## Test Pyramid Recommendation

```
                    ┌─────────────────────────────────┐
                    │          E2E / Live              │  ~5%
                    │  68 Playwright specs             │
                    │  (mocked backend + live gated)   │
                    ├─────────────────────────────────┤
                    │         Integration              │  ~25%
                    │  Testcontainers (marked          │
                    │  @pytest.mark.integration)       │
                    │  + Frontend API mocking layer    │
                    ├─────────────────────────────────┤
                    │           Unit                   │  ~70%
                    │  Backend: 662 files, FakeProvider│
                    │  Frontend: 93 component tests    │
                    └─────────────────────────────────┘
```

**Target ratios (by test count):**
- Unit: 70% — fast, no I/O, use `FakeProvider` for LLM, `MemorySaver` for checkpointer
- Integration: 25% — `@pytest.mark.integration` with testcontainers; frontend with `msw` mocks
- E2E: 5% — Playwright against running stack, gated in CI

**Current ratio (estimated):**  
- Unit ≈ 65%, Integration ≈ 30%, E2E ≈ 5% — reasonably balanced  
- Main risk: integration tests are expensive (testcontainers); should not gate every PR

---

## CI / Nightly / Live Split

### CI (every PR, must pass in < 10 minutes)

```bash
# Backend
uv run pytest -m "not integration and not slow" --cov=app -q

# Frontend unit
npm run test -- --run

# E2E smoke (mocked backend)
npx playwright test --project=smoke-live
```

**Exclusions from CI:** `@pytest.mark.integration`, `@pytest.mark.slow`, Playwright `full-live` / `mobile` / `provider-live`

### Nightly (scheduled, runs against full stack)

```bash
# Backend integration suite
DOCKER_HOST="unix:///..." TESTCONTAINERS_RYUK_DISABLED=true \
  uv run pytest -m "integration" -q

# Full Playwright suite (mock backend)
npx playwright test --project=full-live

# Accessibility
npx playwright test --project=accessibility

# Failure states
npx playwright test --project=failure-states

# Mobile
npx playwright test --project=mobile
```

### Weekly / On-Demand (requires real infra + credentials)

```bash
# Real LLM provider tests (requires keys)
ANTHROPIC_API_KEY=... uv run pytest -m "slow" -q

# Playwright provider-live
ANTHROPIC_API_KEY=... npx playwright test --project=provider-live

# RAG live (requires Postgres + pgvector)
npx playwright test --project=rag-live

# Eval regression
npx playwright test --project=eval-regression
```

---

## Provider-Live Gating Strategy

Tests touching real LLM APIs must be:

1. **Marked** with `@pytest.mark.slow` (backend) or filename suffix `.provider.spec.ts` (frontend E2E)
2. **Gated** by env var check at test start:
   ```python
   @pytest.mark.slow
   def test_anthropic_completion(settings):
       if not settings.ANTHROPIC_API_KEY:
           pytest.skip("ANTHROPIC_API_KEY not set")
   ```
3. **Not included** in PR CI by default (added to `addopts` exclusion or `--ignore`)
4. **Run nightly** on a dedicated CI job with secret injection
5. **Budget-capped** — each provider-live run should not exceed $2 USD. Use `gpt-4o-mini` / `claude-haiku` for functional tests; reserve larger models for regression suites

---

## Test Data Management

### Backend

| Data Type | Approach |
|-----------|----------|
| Tenant/API key | `conftest.py` fixtures create an in-memory `TenantService` with test tenant `test-tenant-01` |
| Goal objects | `GoalService` in-memory mode (no `manage_pools`); factory fixtures in `tests/fixtures/` |
| LLM responses | `FakeProvider` with deterministic responses keyed by prompt hash |
| Postgres/Redis | Testcontainers — ephemeral, created per session, torn down after |
| MCP tools | `MockMCPClient` returning pre-defined tool lists and responses |
| Embeddings | `FakeEmbeddingProvider` returning zero-vectors of correct dimension |

### Frontend

| Data Type | Approach |
|-----------|----------|
| API responses | `msw` (Mock Service Worker) handlers in `src/test/` — intercept fetch at network level |
| Auth state | Zustand `useAuthStore` pre-seeded via `beforeEach` setup in Vitest |
| SSE streams | Custom `MockSSEServer` utility in `e2e/helpers/` — replays event sequences |
| Playwright auth | `e2e/helpers/auth.ts` — sets localStorage `apiKey` + `tenantId` before page load |

### Test Data Isolation

- Each integration test creates a unique `tenant_id` via UUID to prevent cross-test contamination
- RLS ensures Postgres isolation at the DB layer
- Frontend tests use per-test `QueryClient` instances to prevent cache pollution
- Playwright tests run in separate browser contexts per spec file (`fullyParallel: true`)

---

## Coverage Targets by Feature Area

| Feature Area | Unit | Integration | E2E |
|---|---|---|---|
| Agent Loop (plan/execute/verify) | 90% | 70% | smoke only |
| Goal lifecycle (submit → complete) | 85% | 80% | critical path |
| Multi-tenancy / RLS | 80% | 95% | smoke + security |
| HITL approvals | 85% | 80% | critical path |
| Governance policies | 85% | 75% | smoke |
| Knowledge / RAG | 80% | 70% | rag-live gated |
| Memory (long-term + execution) | 85% | 70% | smoke |
| MCP connectors | 75% | 65% | smoke |
| Cost tracking | 90% | 85% | nightly |
| Observability / traces | 75% | 60% | observability-live |
| Workflow builder | 70% | 50% | smoke |
| Provider adapters | 85% | 40% (mock) | provider-live gated |
| SDK Python | 80% | 70% | contract only |
| Frontend UI components | 75% | N/A | a11y + mobile |
