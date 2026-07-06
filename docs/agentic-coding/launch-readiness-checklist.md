# AgentVerse Launch Readiness Checklist

**Date:** 2026-07-06
**Version:** 1.0
**Owner:** Lead Engineer

Use this checklist before any production launch or major release. Every item must be checked off
or have an explicit waiver documented below.

---

## Security (must all pass)

- [ ] No real API keys in `.env` or committed files — `npx gitleaks detect --source . --no-git` returns 0 findings
- [ ] OpenAI API key rotated after appearing in `.env` (see security audit finding SEC-HIGH-7)
- [ ] Atlassian Jira + Confluence API tokens rotated
- [ ] `gitleaks` added as required CI step in `.github/workflows/ci.yml`
- [ ] SSRF protection on `test_connector` generic fallback — `assert_public_url` called before `httpx.get` (`app/api/connectors.py:487–513`)
- [ ] SSRF protection on `discover_tools` — `assert_public_url` called at start of method (`app/mcp/client.py:321–364`)
- [ ] SSRF guard fails-closed on DNS errors — `not ips` raises `SSRFError`, not `return` (`app/net/ssrf_guard.py:149`)
- [ ] Admin key uses `hmac.compare_digest` — not `!=` (`app/api/admin.py:32`)
- [ ] MFA TOTP replay prevention is Redis-backed — not a module-level dict (`app/api/mfa.py:112`)
- [ ] MFA session store is Redis-backed — not a module-level dict (`app/api/mfa.py:300`)
- [ ] MFA enroll response does NOT include raw `"secret"` field (`app/api/mfa.py:434–443`)
- [ ] Rate limiting on `/tenants/signup` — returns 429 after 5 requests/minute per IP
- [ ] No raw secrets in API responses — run `grep -rn '"secret"\|"api_key"\|"password"' app/api/` and audit each
- [ ] CORS origins locked to production domain — `CORS_ORIGINS` does not contain `"*"`
- [ ] `CORS_ORIGINS=["*"]` startup guard enabled — app fails to start if wildcard + credentials
- [ ] Vault master key set in all environments — no `"dev-insecure-master-key"` in staging
- [ ] `ALLOW_DEV_VAULT` not set in production environment
- [ ] OAuth tokens encrypted at rest — `OAuthFlowManager._vault` is wired (`app/mcp/oauth.py:54`)
- [ ] Billing webhook secret validated — `RAZORPAY_WEBHOOK_SECRET` required when Razorpay is configured
- [ ] `GET /rpa/tools` requires authentication — `_require_tenant(request)` added (`app/api/rpa.py:31`)

---

## Tenant Isolation

- [ ] All `GoalService` methods pass `tenant_ctx` through to DB queries — no bare SELECT without RLS
- [ ] SSE stream verifies goal ownership — `svc.get_goal(goal_id, tenant_ctx)` called before streaming (`app/api/goals.py:359`)
- [ ] `memory_v2` API uses DB persistence — no module-level `_memories` dict (`app/api/memory_v2.py:18`)
- [ ] `skills_runtime` API uses DB persistence — no module-level `_platform_skills` dict (`app/api/skills_runtime.py:34`)
- [ ] `goal_lineage` uses `sqlalchemy_rls_context` — not bare `SET LOCAL` (`app/api/goals.py:701`)
- [ ] `goal_attempts` uses `sqlalchemy_rls_context` — not bare `SET LOCAL` (`app/api/goals.py:769`)
- [ ] `connector_usage` uses `sqlalchemy_rls_context` (`app/api/connectors.py:941`)
- [ ] Marketplace in-memory fallback is tenant-keyed — not shared across all tenants (`app/enterprise/marketplace_v2.py:1285`)
- [ ] SSE pub/sub channels scoped per tenant — `goal_events:{tenant_id}:{goal_id}` format
- [ ] Celery tasks propagate tenant context — `TenantContext` contains correct `tenant_id`
- [ ] Celery worker validates `tenant_id` exists in DB before executing goal

---

## Reliability

- [ ] LangGraph checkpointer wired in Celery workers — `RedisSaver` not `MemorySaver` (`app/scaling/tasks.py:887`)
- [ ] Per-plan Celery queue routing applied — enterprise goals routed to `goals.enterprise` (`app/scaling/tasks.py`)
- [ ] SSE dead subscriber pruning — `request.is_disconnected()` checked; bounded queues (`maxsize=500`)
- [ ] Rate limiter uses atomic Lua script — not 3-step TOCTOU (`app/tenancy/rate_limiter.py:44`)
- [ ] Postgres connection pool sized correctly for replica count — `pool_size × replicas ≤ max_connections`
- [ ] Redis HA configured in production — Sentinel or Cluster, not single-node
- [ ] Celery broker and result backend use separate Redis keyspaces (or separate instances)
- [ ] Health checks return Celery + Redis + DB liveness — not just application boot
- [ ] `GET /health` returns 503 when any critical dependency is down
- [ ] Celery worker crash recovery tested — `_recover_interrupted_goals()` re-enqueues correctly
- [ ] Celery event loop per task fixed — `asyncio.run()` used, not manual loop management (`app/scaling/tasks.py:174`)

---

## Testing

- [ ] `uv run pytest tests/ -q -m "not integration and not slow"` — passes (except known pre-existing failures)
- [ ] Known pre-existing failure documented: `tests/agent/test_graph_comprehensive_coverage.py::test_agent_run_completes_on_first_verify`
- [ ] `npm run typecheck` — 0 TypeScript errors
- [ ] `npm run lint` — 0 ESLint errors
- [ ] `npx playwright test --project=smoke-live` — all tests pass
- [ ] Security tests pass: `uv run pytest tests/security/ -v`
- [ ] Tenant isolation tests pass: `uv run pytest tests/tenancy/ -v`
- [ ] SSRF tests pass: `uv run pytest tests/net/ tests/api/test_connectors.py -k "ssrf" -v`
- [ ] Rate limiter tests pass: `uv run pytest tests/tenancy/test_rate_limiter_behavior.py -v`
- [ ] `memory_v2` persistence tests pass: `uv run pytest tests/memory/ -v`
- [ ] `skills_runtime` persistence tests pass: `uv run pytest tests/skills/ -v`

---

## Data + Persistence

- [ ] `memory_v2` API persists to DB — migration `0062_knowledge_v2.py` is the backing store
- [ ] `skills_runtime` API persists to DB — migration `0074_skills_table.py` is the backing store
- [ ] `goal_feedback` persists to DB — migration `0082_goal_feedback.py` in use
- [ ] Batch goal `batch_id` stored in Redis — `GET /goals/batch/{id}/status` returns aggregated counts
- [ ] Migration chain has no gaps — `alembic history` shows continuous chain; 0049–0052 accounted for
- [ ] `alembic current` matches `alembic heads` — schema is up to date
- [ ] Startup schema check in place — app fails fast if DB schema is behind code
- [ ] All migrations have tested `downgrade()` path

---

## Frontend

- [ ] GoalsListPage has error state — `isError` + `ErrorPanel` with retry button
- [ ] BillingPage route registered in `App.tsx` — `/settings/billing` navigates to BillingPage
- [ ] Onboarding auto-triggered for new users — `DashboardPage` redirects when 0 agents + 0 goals
- [ ] Onboarding completion flag stored — `localStorage["av-onboarding-complete"]` prevents re-trigger
- [ ] WorkflowBuilderPage shows mobile blocker on viewports < 768px
- [ ] AgentCreatePage uses `MissionControlLayout` — not generic `bg-card`
- [ ] `npm run typecheck` passes with 0 errors

---

## Operations

- [ ] Migrations applied in deploy order — `alembic upgrade head` runs before new code serves traffic
- [ ] Backup/restore tested — Postgres dump + restore verified in staging
- [ ] Monitoring dashboards configured — goal throughput, error rates, queue depth, cost per tenant
- [ ] Alerts configured for: error rate > 1%, Celery queue depth > 500, Redis memory > 80%, goal failure rate > 10%
- [ ] Log level set to `INFO` in production (not `DEBUG`)
- [ ] Docker default credentials changed — `agentverse:agentverse` password not in use
- [ ] Production startup refuses default DB credentials — `RuntimeError` not just log
- [ ] Grafana/OTel collector endpoints configured (`OTEL_EXPORTER_OTLP_ENDPOINT`)
- [ ] CORS origins set to production domain only (not `localhost` or `*`)
- [ ] `ENVIRONMENT=production` set in all production pods
- [ ] Health check endpoints verified in Kubernetes liveness/readiness probes

---

## Waivers

If any item cannot be completed before launch, document it here with:
- Item reference
- Reason it cannot be completed
- Compensating control in place
- Target completion date
- Owner

| Item | Reason | Compensating Control | Target Date | Owner |
|---|---|---|---|---|
| | | | | |

---

*This checklist is generated from the 2026-07-06 feature audit, security audit, scalability
review, and frontend UX review. Re-run the audits after significant changes to update it.*
