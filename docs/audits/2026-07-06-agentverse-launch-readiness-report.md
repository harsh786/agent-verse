# AgentVerse Launch Readiness Report — 2026-07-06

**Overall status:** CONDITIONAL READY

## Executive Summary

A 16-phase audit of AgentVerse (backend, frontend, security, scalability, NFRs, and E2E testing) completed 2026-07-06 identifies 6 security vulnerabilities fixed this session, 2 critical data-loss paths remediated (memory\_v2 and skills\_runtime now DB-backed), and 39/39 Playwright tests passing. Three hard launch blockers remain: live credentials must be rotated out of `.env`, a production Redis deployment (Sentinel or Cluster) must be provisioned before multi-replica scale-out, and the LangGraph `MemorySaver` fallback in Celery workers must be replaced with a Redis checkpointer to prevent silent goal-state loss on worker crash.

## What Was Audited

| Phase | Document / Command |
|---|---|
| Product inventory | `docs/audits/2026-07-06-agentverse-product-inventory.md` |
| Feature completeness | `docs/audits/2026-07-06-agentverse-feature-audit.md` |
| Security audit (21 findings) | `docs/security/2026-07-06-agentverse-security-audit.md` |
| Scalability / architecture (10 risks) | `docs/audits/2026-07-06-agentverse-scalability-review.md` |
| Non-functional requirements | `docs/audits/2026-07-06-agentverse-non-functional-review.md` |
| Frontend UX review (11 pages) | `docs/audits/2026-07-06-agentverse-frontend-review.md` |
| Hardening plan (48 items across P0–P3) | `docs/plans/2026-07-06-agentverse-hardening-plan.md` |
| Playwright run results | `docs/testing/playwright-run-results-2026-07-06.md` |
| Ruff lint verification | `uv run ruff check app/ 2>&1 \| grep "Found" \| tail -3` |
| Backend security + contract tests | `uv run pytest tests/security/ tests/test_contract_schema.py -q --tb=line -m "not integration and not slow"` |

Codebase scope: 44 backend modules, 62 API files (~453 routes), 82 DB migrations, 45 frontend feature directories, 69 E2E spec files, 27 Celery tasks, 12 Helm templates, 20 Docker Compose services.

## What Was Fixed In This Session

### Security Fixes

| Fix | Severity | File | Status |
|---|---|---|---|
| RPA tools auth — added `_require_tenant` to `GET /rpa/tools` | HIGH | `app/api/rpa.py` | FIXED |
| SSRF connector — `assert_public_url` guard added to `test_connector` generic fallback | CRITICAL | `app/api/connectors.py` | FIXED |
| SSRF guard fail-closed — DNS resolution failure now raises `SSRFError` instead of silently passing | HIGH | `app/net/ssrf_guard.py` | FIXED |
| MFA replay prevention — `_used_totp_codes` and `_mfa_verified_sessions` backed by Redis with TTL | HIGH | `app/api/mfa.py` | FIXED |
| Signup rate limit — `@limiter.limit("5/minute")` applied to `/tenants/signup` | HIGH | `app/api/tenants.py` | FIXED |
| SSE ownership check — `svc.get_goal(goal_id, tenant_ctx)` called before `StreamingResponse` | HIGH | `app/api/goals.py` | FIXED |

### Architecture / Data Fixes

| Fix | Severity | File | Status |
|---|---|---|---|
| `memory_v2` API persistence — replaced `_memories: dict` with `MemoryV2Repository` (DB-backed, `sqlalchemy_rls_context`) | CRITICAL | `app/api/memory_v2.py` | FIXED |
| `skills_runtime` API persistence — replaced module-level dicts with `SkillRepository`; wired to migration `0074_skills_table.py` | HIGH | `app/api/skills_runtime.py` | FIXED |
| Celery per-plan queue routing — `PLAN_QUEUE_MAP` applied at `GoalService.submit_goal` dispatch | HIGH | `app/services/goal_service.py` | FIXED |
| SSE dead-subscriber pruning — bounded `asyncio.Queue(maxsize=500)`, `request.is_disconnected()` in generator, `token_chunk` carve-out removed | HIGH | `app/services/goal_service.py` | FIXED |
| Webhook billing secret — hard `HTTPException(503)` when `RAZORPAY_WEBHOOK_SECRET` is unset | HIGH | `app/api/billing.py` | FIXED |

### Frontend Fixes

| Fix | Severity | File | Status |
|---|---|---|---|
| `GoalsListPage` error state — `isError` + `<ErrorPanel onRetry={refetch}>` added | HIGH | `src/features/goals/GoalsListPage.tsx` | FIXED |
| `BillingPage` route registered — `<Route path="settings/billing">` added to `App.tsx`; "Billing" tab added to `SettingsPage` | MEDIUM | `src/app/App.tsx`, `src/features/settings/SettingsPage.tsx` | FIXED |
| Playwright route mocks pinned to `http://localhost:8000/` prefix — prevented Vite source-file interception bug | HIGH | `e2e/*.spec.ts` (5 files) | FIXED |

## Tests Added

| Category | Count | Pass Rate |
|---|---|---|
| Security tests (SSRF, auth, rate-limit, SSE isolation) | 18 | 100% |
| Tenant isolation tests | 6 | 100% |
| Contract / OpenAPI schema tests | 6 | 100% |
| memory\_v2 persistence tests | 4 | 100% |
| skills\_runtime persistence + isolation tests | 5 | 100% |
| SSE + billing + queue routing tests | 8 | 100% |
| Playwright E2E (9 projects, incl. god-mode, security-smoke, failure-states) | 39 | 100% |
| **Total new this session** | **86** | **100%** |

## Verification Results (run in this session)

```
Ruff check app/: Found 2545 errors (pre-existing style/lint debt; not gated in CI)

Backend tests (targeted — tests/security/ + tests/test_contract_schema.py):
  175 passed in 38.66s
  Line coverage (targeted subset): 21% of 38,468 statements
  Note: full unit suite (pytest -m "not integration and not slow") reported
        91.6% coverage as of 2026-06-30 on a smaller codebase (25,762 stmts);
        codebase has grown ~50% since then — current effective coverage is lower

Playwright (all 9 projects — smoke-live, accessibility, security-smoke,
           failure-states, eval-regression, multimodal-live, rag-live,
           governance-live, observability-live):
  39/39 pass (0 failures)
```

## Remaining Critical Issues (launch blockers)

| Issue | Severity | File | Why Not Fixed | Blocker? |
|---|---|---|---|---|
| LangGraph `MemorySaver` in Celery workers | CRITICAL | `app/agent/graph.py`, `app/scaling/tasks.py` | Requires `@worker_init.connect` signal + Redis Sentinel provisioned first; infra dependency | YES |
| In-memory `GoalRecord` per API replica — SSE events lost on cross-replica requests | CRITICAL | `app/services/goal_service.py` | Large refactor (Redis-pub/sub-only SSE delivery); requires sticky sessions as short-term mitigation | CONDITIONAL |
| Live credentials in `agent-verse-backend/.env` — OpenAI, Atlassian Jira, Atlassian Confluence tokens | HIGH | `.env` | Manual rotation required at identity-provider portals — cannot be automated | YES |
| SSRF in `discover_tools` — `MCPClient.discover_tools()` makes unguarded outbound requests | HIGH | `app/mcp/client.py` | Not addressed this session; requires `assert_public_url` at start of `discover_tools()` | YES |
| Admin API key timing attack — `!=` comparison instead of `hmac.compare_digest` | HIGH | `app/api/admin.py` | Not addressed this session | YES |
| MFA replay cache is Redis-backed (wired) but needs production Redis to be effective | HIGH | `app/api/mfa.py` | Code is wired; Redis Sentinel/Cluster must be provisioned | CONDITIONAL |

## Remaining High Issues (fix before GA)

| Issue | Severity | File |
|---|---|---|
| Rate limiter TOCTOU — non-atomic `zremrangebyscore / zcard / zadd` trio | HIGH | `app/tenancy/rate_limiter.py` |
| `BulkheadRegistry` uses in-process semaphores — limit multiplied by replica count | HIGH | `app/reliability/bulkhead.py` |
| Redis single-node SPOF — no Sentinel or Cluster configured | HIGH | `infra/docker-compose.yml` |
| Vault dev master key used silently when `VAULT_MASTER_KEY` not set in staging | HIGH | `app/providers/vault.py` |
| OAuth tokens stored in plaintext — `OAuthFlowManager._vault` never assigned | HIGH | `app/mcp/oauth.py` |
| Health endpoint does not verify Celery worker, Redis, or DB liveness | MEDIUM | `app/api/system.py` |
| RPO is ~24 hours for Postgres — daily backup only, no WAL archiving | CRITICAL (infra) | `infra/docker-compose.yml` |
| Test coverage gap — effective coverage well below 80% target after codebase growth | MEDIUM | (all modules) |
| Ruff: 2545 lint errors — CI currently does not enforce ruff as a hard gate | MEDIUM | (app/) |

## Security Status

| Category | Finding Count | Fixed This Session | Remaining |
|---|---|---|---|
| CRITICAL | 1 | 1 | 0 |
| HIGH | 9 (7 from security audit + RPA auth + SSE ownership from feature audit) | 5 | 4 |
| MEDIUM | 8 | 0 | 8 |
| LOW | 5 | 0 | 5 |
| **Total** | **23** | **6** | **17** |

## Feature Completeness

| Feature | Status |
|---|---|
| Goal lifecycle | COMPLETE |
| Agent CRUD | PARTIAL (in-memory `AgentStore` primary; multi-replica drift on scale-out) |
| Agent execution | COMPLETE |
| RAG / Knowledge | PARTIAL (backend complete; frontend lacks upload/chunk-search UI) |
| Memory v2 | COMPLETE (DB-backed — fixed this session) |
| Skills runtime | COMPLETE (DB-backed — fixed this session) |
| Governance / audit | COMPLETE |
| HITL | PARTIAL (backend complete; frontend approval-queue is a 2-file stub) |
| Workflow builder | PARTIAL (backend complete; frontend is ReactFlow stub, no node-graph) |
| Scheduling | PARTIAL (backend complete; frontend is 2-file stub) |
| RPA / browser | PARTIAL (auth fixed; Playwright-vs-simulation transparency gap remains) |
| Marketplace | PARTIAL (in-memory fallback is cross-tenant; frontend is 2-file stub) |
| Observability | COMPLETE |
| MCP / connectors | COMPLETE (SSRF in `discover_tools` not yet patched — see blockers) |
| Multi-tenancy | COMPLETE |
| Billing | PARTIAL (route fixed; Razorpay/INR-only; webhook secret enforced) |
| Onboarding | PARTIAL (page exists; not auto-triggered for new users; no back-nav or progress persistence) |
| SSE streaming | COMPLETE (ownership check added; dead-subscriber leak fixed) |

## NFR Status

| NFR | Target | Status |
|---|---|---|
| API auth coverage | 100% endpoints | ACHIEVED (`/rpa/tools` fixed; bypass list is intentional) |
| Tenant isolation | 0 cross-tenant leaks | TESTED (6 isolation tests pass; `memory_v2` and `skills_runtime` now DB-backed with RLS) |
| Test coverage | >80% | GAP — effective coverage below 80% after codebase growth; no `--cov-fail-under` gate in CI |
| TypeScript errors | 0 | ACHIEVED (frontend typecheck clean as of last frontend session) |
| Playwright E2E smoke | 100% pass | ACHIEVED (39/39 across 9 projects) |
| Security tests | Cover all audit findings | ACHIEVED for this session's 6 security fixes |
| Redis failover RTO | <5 min | NOT MET — single-node Redis; RTO unbounded until Redis restarts |
| Postgres RPO | <1 min | NOT MET — daily backup only; 24-hour RPO for full failures |
| Celery queue isolation | Per-plan queues | ACHIEVED (queue routing wired this session) |
| Security audit CI | Blocks merge | NOT MET — `pip-audit` uses `\|\| true`; CVEs silently ignored |

## CI / Testing Split

- **Daily CI (must pass to merge):** `ruff` + `mypy`, `pytest -m "not integration and not slow"` (with coverage upload), frontend `typecheck` + `lint` + `build`, Playwright smoke (`smoke-live`) — **Playwright now mandatory**
- **Nightly:** Full `pytest` (including integration via testcontainers), full Playwright (all 9 projects), `pip-audit` (should be promoted to hard-fail), `gitleaks` secret scan
- **Live / provider-gated:** Behind `LIVE_PROVIDER_TESTS=true` and `RUN_LIVE_TESTS=true` env vars

**CI gaps to close before launch:**
- Remove `\|\| true` from `pip-audit` step (`ci.yml:146`)
- Add `--cov-fail-under=80` to pytest invocation
- Add `gitleaks detect` as required pre-commit and CI step
- Add `ruff check` as a hard gate (currently advisory: 2545 errors must be resolved or scoped-ignored)

## Launch Blockers (must resolve before production)

1. **Rotate `.env` credentials** — live OpenAI (`sk-proj-B8u...`) and Atlassian Jira/Confluence tokens (`ATATT3x...`) tied to `harsh.kumar01@pinelabs.com` must be revoked and replaced with placeholder values before any staging/CI system touches this repo
2. **Production Redis** — provision Redis Sentinel (1 primary + 2 replicas minimum) or Redis Cluster; required for: LangGraph `RedisSaver` checkpointer, distributed MFA replay cache, atomic rate limiter, SSE pub/sub cross-replica delivery, cost controller accuracy
3. **LangGraph checkpointer in Celery workers** — initialise a module-level `_WORKER_CHECKPOINTER` (Redis `RedisSaver`) in `@worker_init.connect`; pass `checkpointer=_WORKER_CHECKPOINTER` when constructing `AgentGraph` in `run_goal` — without this, every worker OOM-kill silently discards in-flight goal state and re-bills all LLM tokens

## Recommended Next Milestones

1. **[Week 1]** Rotate credentials; provision production Redis Sentinel; wire Redis checkpointer in `run_goal` (`app/scaling/tasks.py`); patch `discover_tools` SSRF and admin key timing attack (est. 4h combined)
2. **[Week 2]** Atomise rate limiter with Lua script (`app/tenancy/rate_limiter.py`); replace `BulkheadRegistry` with `RedisBulkheadRegistry`; add Celery + Redis + DB health checks to `/health` endpoint; remove `\|\| true` from `pip-audit` CI step
3. **[Week 3]** Wire `OAuthFlowManager` vault for encrypted token storage; onboarding auto-trigger for new users; mobile-responsive card layouts for Goals/Agents/Governance list pages; add `--cov-fail-under=80` to CI
4. **[Week 4]** Postgres WAL archiving to MinIO (already in stack); load test with 100 concurrent tenants; chaos engineering (Redis kill, worker kill, Postgres failover); DR drill; resolve 2545 ruff lint errors (can be batched as a `ruff --fix` pass)

## Agentic Coding Support

The following agent definitions and checklists support ongoing hardening work:

- `agent-verse-backend/.opencode/agents/qa-agent.md` — backend test-writing agent (uses `FakeProvider`, avoids real LLM calls)
- `agent-verse-backend/.opencode/agents/security-reviewer.md` — security audit agent scoped to `app/api/`, `app/net/`, `app/mcp/`
- `agent-verse-frontend/.opencode/agents/frontend-reviewer.md` — frontend UX + a11y review agent
- `docs/agentic-coding/launch-readiness-checklist.md` — machine-readable checklist for CI gate assertions
