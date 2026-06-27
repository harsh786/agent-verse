# AgentVerse — World-Class Agentic OS Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan phase-by-phase. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AgentVerse the world's best Agentic OS — production-grade, enterprise-ready, with everything any agentic platform needs to let "any agentic thing be done."

**Architecture:** 10 independent phases implemented in parallel. Each phase is a self-contained vertical slice with its own tests, migrations, and docker-compose updates. Open-source only — no cloud lock-in.

**Tech Stack:** FastAPI, SQLAlchemy async, PostgreSQL 16 + pgvector, Redis 7, LangGraph, Celery + RedBeat, React 19 + ReactFlow, Vitest, Playwright, pytest-asyncio, python-jose, aiosmtplib, aioimaplib, python-keycloak, celery-redbeat, Helm 3.

**Spec files:** `docs/superpowers/specs/worldclass/phase-{1-10}-*.md`

---

## Phase Dependency Map

```
Phase 1 (Persistence)  ──► Phase 2 (Intelligence)  ──► Phase 5 (Multi-agent)
Phase 3 (RBAC)         ──► Phase 7 (Integrations)
Phase 4 (Connectors)   ──► Phase 5 (Multi-agent)   ──► Phase 6 (SDK)
Phase 8 (AI/ML)        ──► Phase 2 (Intelligence)
Phase 9 (Infra)        ──► All phases
Phase 10 (UX/UI)       ──► All phases (surfaces all backend features)
```

Phases 1, 3, 4, 6, 9 can run fully in parallel.
Phases 2, 5, 7, 8, 10 depend on Phase 1 completing first.

---

## Phase 1: Core Persistence ✅ CRITICAL

**Spec:** `docs/superpowers/specs/worldclass/phase-1-core-persistence.md`
**Priority:** P0 — system collapses on pod restart without this

- [ ] **1.1** AgentStore → DB-backed (use `agents` table)
- [ ] **1.2** HITLGateway → DB-backed (use `approval_requests` table)
- [ ] **1.3** AuditLog `query()` → reads from DB with filters + pagination
- [ ] **1.4** LongTermMemoryStore → pgvector semantic search
- [ ] **1.5** LangGraph → RedisSaver checkpointer (install `langgraph-checkpoint-redis`)
- [ ] **1.6** PolicyEngine → DB-backed (governance_policies table)
- [ ] **1.7** EvalSuiteRunner → DB-backed (new migrations)
- [ ] **Run:** `uv run pytest tests/ --no-cov -q -m "not integration and not slow"` → all pass
- [ ] **Commit:** `feat(persistence): all core state DB-backed, zero in-memory loss on restart`

---

## Phase 2: Intelligence Upgrades

**Spec:** `docs/superpowers/specs/worldclass/phase-2-intelligence-upgrades.md`
**Priority:** P0/P1

- [ ] **2.1** Wire StructuredPlan into execution with parallel wave execution
- [ ] **2.2** Model-per-role routing wired into graph (planner/executor/verifier)
- [ ] **2.3** Chain-of-thought `_node_think` before `_node_plan`
- [ ] **2.4** Reflection `_node_reflect` for targeted step repair (not full replan)
- [ ] **2.5** Agent domain specialization (system_prompt field injected)
- [ ] **2.6** Semantic cache wired into execution path
- [ ] **2.7** Token streaming: `stream_complete()` providers + `/goals/{id}/stream/tokens`
- [ ] **Run:** `uv run pytest tests/agent/ tests/providers/ --no-cov -q` → all pass
- [ ] **Commit:** `feat(intelligence): parallel steps, model routing, CoT, reflection, streaming`

---

## Phase 3: RBAC + Security

**Spec:** `docs/superpowers/specs/worldclass/phase-3-rbac-security.md`
**Priority:** P0 — enterprise blocker

- [ ] **3.1** Role model + DB migration + `GET/POST/DELETE /tenants/me/roles`
- [ ] **3.2** RBAC middleware: extend TenantContext with roles
- [ ] **3.3** Role-protected endpoints (`require_role` decorator)
- [ ] **3.4** IP allowlisting: `ip_allowlist` table + middleware
- [ ] **Run:** `uv run pytest tests/tenancy/ tests/api/ --no-cov -q` → all pass
- [ ] **Commit:** `feat(rbac): role-based access control, IP allowlisting`

---

## Phase 4: Connector Ecosystem

**Spec:** `docs/superpowers/specs/worldclass/phase-4-connector-ecosystem.md`
**Priority:** P0

- [ ] **4.1** OpenAPI auto-import: `POST /connectors/import-openapi`
- [ ] **4.2** Docker code interpreter: `POST /tools/execute-code` + `code.execute` MCP tool
- [ ] **4.3** Native file ops: `file.read/write/list/delete` tools
- [ ] **4.4** Native email: `email.send/read` via aiosmtplib + aioimaplib
- [ ] **4.5** 20+ new connectors in catalog
- [ ] **Docker:** Add `mailhog` to docker-compose for email testing
- [ ] **Run:** `uv run pytest tests/tools/ tests/mcp/ --no-cov -q` → all pass
- [ ] **Commit:** `feat(connectors): OpenAPI auto-import, code interpreter, email, 20+ connectors`

---

## Phase 5: Multi-Agent Patterns

**Spec:** `docs/superpowers/specs/worldclass/phase-5-multiagent-patterns.md`
**Priority:** P1

- [ ] **5.1** Supervisor agent: `POST /goals` with `workflow_mode: supervised`
- [ ] **5.2** Debate/voting: `workflow_mode: debate` (N agents + voting)
- [ ] **5.3** A2A protocol: DB persistence + callbacks + streaming + HMAC auth
- [ ] **5.4** Batch processing: `POST /goals/batch` + progress polling
- [ ] **Run:** `uv run pytest tests/agent/ tests/api/ --no-cov -q` → all pass
- [ ] **Commit:** `feat(multiagent): supervisor, debate, A2A complete, batch processing`

---

## Phase 6: Developer SDK

**Spec:** `docs/superpowers/specs/worldclass/phase-6-developer-sdk.md`
**Priority:** P0

- [ ] **6.1** Python SDK: `agent-verse-sdk-python/` package
- [ ] **6.2** TypeScript SDK: `agent-verse-sdk-typescript/` package
- [ ] **6.3** `agentverse dev` local sandbox command
- [ ] **6.4** `AgentTestHarness` testing framework
- [ ] **Run:** SDK unit tests pass independently
- [ ] **Commit:** `feat(sdk): Python + TypeScript SDKs, local dev sandbox, test harness`

---

## Phase 7: Integrations

**Spec:** `docs/superpowers/specs/worldclass/phase-7-integrations.md`
**Priority:** P1

- [ ] **7.1** Slack app: `/agentverse` slash command + HITL approval buttons
- [ ] **7.2** Zapier adapter: trigger + action endpoints
- [ ] **7.3** GitHub Actions: `agent-verse-github-action/` directory
- [ ] **7.4** Email-to-goal: IMAP listener + Celery task
- [ ] **7.5** WebSocket tool support in MCPClient
- [ ] **Docker:** Add `mailhog` for email, Slack env vars
- [ ] **Run:** `uv run pytest tests/integrations/ --no-cov -q` → all pass
- [ ] **Commit:** `feat(integrations): Slack, Zapier, GitHub Actions, email-to-goal`

---

## Phase 8: AI/ML Capabilities

**Spec:** `docs/superpowers/specs/worldclass/phase-8-ai-ml-capabilities.md`
**Priority:** P1

- [ ] **8.1** Prompt optimizer: A/B testing + auto-promotion
- [ ] **8.2** Retrieval evaluator: precision@k, recall@k, MRR
- [ ] **8.3** Behavior analytics: `/analytics/goals|tools|costs|agents` endpoints
- [ ] **8.4** Cost optimizer: model downgrade suggestions
- [ ] **8.5** Fine-tuning data export: JSONL download
- [ ] **Frontend:** `AnalyticsDashboard.tsx` with Recharts
- [ ] **Run:** `uv run pytest tests/intelligence/ tests/analytics/ --no-cov -q` → all pass
- [ ] **Commit:** `feat(ai-ml): prompt optimization, retrieval eval, analytics, cost optimizer`

---

## Phase 9: Infrastructure

**Spec:** `docs/superpowers/specs/worldclass/phase-9-infrastructure.md`
**Priority:** P1

- [ ] **9.1** celery-redbeat HA beat (2 replicas, distributed lock)
- [ ] **9.2** Helm chart: `helm/agentverse/` with 12 templates
- [ ] **9.3** Blue/green deployment scripts
- [ ] **9.4** PgBouncer + SQLAlchemy pool tuning in docker-compose
- [ ] **9.5** Jaeger + OTel Collector in docker-compose
- [ ] **Validate:** `docker-compose up` starts all services including Jaeger UI at :16686
- [ ] **Commit:** `feat(infra): RedBeat HA, Helm chart, blue/green, PgBouncer, Jaeger`

---

## Phase 10: UX/UI World-Class

**Spec:** `docs/superpowers/specs/worldclass/phase-10-ux-ui.md`
**Priority:** P1

- [ ] **10.1** Visual Workflow Builder (`/workflow-builder`) with ReactFlow
- [ ] **10.2** Agent Playground (`/playground`) with step-through execution
- [ ] **10.3** Push notifications (bell icon + browser API + SSE)
- [ ] **10.4** Live token streaming in GoalDetailPage
- [ ] **10.5** Mobile-responsive design (bottom tabs, stacked layouts)
- [ ] **Frontend tests:** `npm run test -- --run` → all pass
- [ ] **E2E:** `npx playwright test --list` → 120+ tests registered
- [ ] **Commit:** `feat(ux): workflow builder, playground, notifications, streaming, mobile`

---

## Docker-Compose Final State (all services)

After all phases, `docker-compose up` starts:
```
postgres       :5432  — PostgreSQL 16 + pgvector
redis          :6379  — Redis 7 (cache + broker + pub/sub)
minio          :9000  — MinIO S3-compatible artifact storage
keycloak       :8080  — Keycloak SSO
mailhog        :8025  — Email testing UI
jaeger         :16686 — Distributed tracing UI
otel-collector :4317  — OTel gRPC collector
pgbouncer      :5433  — PostgreSQL connection pooler
backend        :8000  — FastAPI + AgentVerse API
worker               — Celery worker (goals + schedules + maintenance)
beat                 — Celery beat (HA with RedBeat)
frontend       :5173  — React + Vite dev server
```

---

## Final Acceptance Criteria

```bash
# Backend: 1500+ tests, 0 failures
cd agent-verse-backend
uv run pytest --no-cov -q -m "not integration and not slow" \
  --ignore=tests/core/test_pools_integration.py \
  --ignore=tests/db/test_migrations.py
# Expected: 1500+ passed

# Frontend: 150+ tests, 0 failures  
cd agent-verse-frontend
npm run test -- --run
# Expected: 150+ passed

# Full local stack
cd agent-verse-backend
docker-compose up -d
# Expected: all 12 services healthy within 2 minutes

# OpenAPI coverage
uv run python scripts/export_openapi.py
python -c "import json; s=json.load(open('openapi.json')); print(len(s['paths']), 'paths')"
# Expected: 120+ paths
```

---

## Commit Convention

```
feat(phase-{N}-{area}): {description}

- {bullet list of what was implemented}
- Tests: {N} new tests
- Migrations: {list new migration files}
- Docker: {services added/changed}
```
