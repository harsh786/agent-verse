# AgentVerse Product Inventory Audit
**Date:** 2026-07-06  
**Auditor:** QA Lead / Product Audit  
**Scope:** `agent-verse-backend/` and `agent-verse-frontend/` (SDK packages excluded)

---

## Summary Table

| Dimension | Count / Status |
|---|---|
| Backend app modules | 44 packages under `app/` |
| API endpoint files | 62 files / ~453 routes |
| DB migrations | 82 revision files (latest: `170245f26dcb`) |
| Frontend features | 45 directories under `src/features/` |
| Backend test files | 616 files (616 test modules, excl. `__init__.py`) |
| E2E spec files | 69 `.spec.ts` files |
| CI workflow files | 2 (`ci.yml`, `deploy.yml`) |
| Celery tasks | 27 named tasks |
| Helm chart templates | 12 templates |
| Docker Compose services | 20 services |

---

## 1. Backend App Modules (`app/`)

| Module | Role |
|---|---|
| `agent/` | LangGraph state machine, planner/executor/verifier, debate, supervisor, consensus, routing, HITL integration |
| `agent_runtime/` | Agent runtime abstraction layer for per-agent process management |
| `ai_ops/` | AIOps anomaly detection, auto-remediation, predictive scaling integrations |
| `ai_router/` | LLM-based goal-to-agent routing decision engine |
| `analytics/` | Usage analytics, goal statistics, time-series aggregation |
| `api/` | FastAPI routers — all HTTP endpoints (62 files, see §2) |
| `auth/` | SSO (Keycloak/OAuth2), JWT validation, MFA, API-key management |
| `civilization/` | Multi-agent civilization simulation (long-horizon emergent behaviours) |
| `cli/` | Backend admin CLI (`agentverse` command entry point) |
| `collab/` | Real-time collaborative goal editing via CRDT / WebSocket |
| `content/` | Content generation utilities and structured output helpers |
| `core/` | Configuration (`Settings`), error types, dependency injection helpers |
| `db/` | SQLAlchemy async models, Alembic migrations, RLS context managers, session factory |
| `embedding/` | Embedding provider abstraction (Voyage, OpenAI, local sentence-transformers) |
| `enterprise/` | Compliance (GDPR/SOC2/PCI), red-team, simulation sandbox, marketplace_v2 |
| `governance/` | Audit log (v1/v3), cost controller, HITL gateway, policy engine, permissions |
| `guardrails_v2/` | LLM output guardrail rules engine (v2 with rule partitioning) |
| `integrations/` | Third-party integrations (Slack, Zapier, IMAP, Alertmanager, Datadog) |
| `intelligence/` | EvalRunner, MetaAgentPlanner, SelfOptimizer, PromptOptimizer, verifier calibration |
| `knowledge/` | KnowledgeStore (hybrid pgvector + trigram), document ingestion pipeline |
| `knowledge_graph/` | Graph-based knowledge representation and traversal |
| `mcp/` | Model Context Protocol — registry, client, OAuth PKCE, tool cache, OpenAPI importer |
| `memory/` | ExecutionMemory (per-goal) and LongTermMemoryStore (cross-session) |
| `memory_v2/` | Memory 2.0 with provenance tracking, lifecycle states, privacy classes |
| `multimodal/` | Vision/image processing, PDF extraction, multimodal goal context assembly |
| `net/` | Outbound network helpers (HTTP client wrappers, proxy support) |
| `observability/` | Structured logging, OTel tracing, Prometheus metrics, cost-breakdown API |
| `perception/` | Browser-based perception (page analysis, screenshot capture, vision) |
| `pipeline/` | Sequential pipeline orchestration primitives |
| `providers/` | Vendor-agnostic LLM/embedding abstraction — Anthropic, OpenAI-compatible, Gemini, Voyage, Fake |
| `rag/` | RAG primitives — LLM response cache, semantic cache, retrieval utilities |
| `rag_platform/` | Unified RAG platform — query planner (strategy selection), retriever, reranker |
| `reliability/` | Circuit breakers, bulkhead, deduplication, rollback engine, goal lifecycle signals |
| `rpa/` | Browser automation via Playwright — executor, session manager, credential injector |
| `scaling/` | Celery app, task definitions, beat guard, parallel executor, priority queue |
| `sdk/` | Internal SDK helpers used by the backend itself |
| `services/` | GoalService, TenantService, EventStore, GoalQueue, NotificationService, LLMConfigStore |
| `skills_runtime/` | Skills execution engine — trigger matcher, permission checker, skill executor |
| `tenancy/` | TenantMiddleware, rate limiter, RBAC, RLS context, plan tiers, entitlements |
| `testing/` | Test utilities, fake providers, test fixture helpers |
| `tools/` | Tool registry, tool risk classifier, tool capability store |
| `triggers/` | NLScheduler, ScheduleStore, TriggerSpec models |

---

## 2. API Endpoint Files (`app/api/`)

| File | Route Prefix | ~Routes |
|---|---|---|
| `a2a.py` | `/.well-known/agents` | 3 |
| `admin.py` | `/admin` | 5 |
| `agent_credentials_api.py` | `/agents/{agent_id}/keys` | 4 |
| `agent_directory.py` | `/.well-known/agents` (A2A) | 3 |
| `agent_runtime.py` | `/agent-runtime` | 6 |
| `agents.py` | `/agents` | 22 |
| `ai_ops.py` | `/ai-ops` | 9 |
| `analytics.py` | `/analytics` | 7 |
| `artifacts.py` | `/artifacts` | 5 |
| `auth.py` | `/auth`, `/auth/mfa`, `/auth/sessions` | 12 |
| `billing.py` | `/billing` | 9 |
| `builder.py` | `/builder` | 5 |
| `civilization.py` | `/civilizations` | 18 |
| `collab.py` | `/collab` | 11 |
| `connectors.py` | `/connectors` | 17 |
| `costs.py` | `/costs` | 10 |
| `dpdp.py` | `/compliance/dpdp` | 4 |
| `embeddings.py` | `/embeddings` | 3 |
| `enterprise.py` | `/enterprise` | 21 |
| `errors.py` | (error handler registration) | — |
| `goals.py` | `/goals` | 25 |
| `golden_datasets.py` | `/eval/golden-datasets` | 5 |
| `governance.py` | `/governance` | 29 |
| `gst_billing.py` | `/billing/gst` | 4 |
| `guardrails.py` | `/guardrails` | 7 |
| `guardrails_v2.py` | `/guardrails-v2` | 7 |
| `insights.py` | `/insights` | 6 |
| `integrations.py` | `/integrations` | 7 |
| `knowledge.py` | `/knowledge` | 27 |
| `knowledge_graph.py` | `/knowledge-graph` | 10 |
| `lab.py` | `/lab` | 4 |
| `marketplace_monetization.py` | `/marketplace/monetization` | 5 |
| `memory.py` | `/memory` | 9 |
| `memory_v2.py` | `/memory-v2` | 10 |
| `mfa.py` | `/auth/mfa` | 7 |
| `mfa_crypto.py` | (crypto helpers, no router) | — |
| `model_registry.py` | `/models` | 5 |
| `multimodal.py` | `/multimodal` | 4 |
| `observability.py` | `/observability` | 4 |
| `perception.py` | `/perception` | 6 |
| `policy_rules.py` | `/governance/policy-rules` | 5 |
| `public_status.py` | `/status` | 3 |
| `rag_platform.py` | `/rag` | 3 |
| `replay.py` | (replay helpers) | 2 |
| `rpa.py` | `/rpa` | 8 |
| `sandbox.py` | `/sandbox` | 4 |
| `schedules.py` | `/schedules` | 10 |
| `sessions.py` | `/auth/sessions` | 4 |
| `skills.py` | `/skills` | 6 |
| `skills_runtime.py` | `/skills-runtime` | 15 |
| `sla.py` | `/sla` | 4 |
| `solutions.py` | `/solutions` | 5 |
| `system.py` | `/admin` | 5 |
| `templates.py` | `/templates` | 6 |
| `tenants.py` | `/tenants` | 25 |
| `tools.py` | `/tools` | 6 |
| `training_export.py` | (training data export) | 3 |
| `trust_governance.py` | `/trust` | 8 |
| `workflows.py` | `/workflows` | 7 |
| `v1/` | `/v1/...` (versioned endpoints) | — |

**Total: ~453 routes across 62 files**

---

## 3. Database Migration Files

**Total: 82 revision files** (`app/db/migrations/versions/`)

| Range | Files | Notes |
|---|---|---|
| `0001` – `0048` | 48 files | Baseline through collab metadata, cost ledger |
| `0053` – `0085` | 33 files | (0049–0052 absent — presumably merged or skipped) |
| `170245f26dcb` | 1 file | `add_self_optimization_suggestions_table` — latest autogenerated revision |

**Latest migration ID:** `170245f26dcb_add_self_optimization_suggestions_table.py`

Notable migrations:
- `0005_governance.py` — audit + policy tables
- `0022_rbac_tables.py` — roles and permissions
- `0031_audit_immutability.py` — hash-chain audit
- `0071_fix_rls_missing_tables.py` — RLS policy coverage fix
- `0074_skills_table.py` — skills persistence table
- `0077_usage_billing.py` — billing/usage tables
- `0085_add_knowledge_graph.py` — knowledge graph tables

---

## 4. Celery Tasks

All tasks defined in `app/scaling/tasks.py` (and registered via `celery_app`):

| Task Name | Queue | Purpose |
|---|---|---|
| `app.scaling.tasks.run_goal` | `goals.*` | Main goal execution task (max_retries=3) |
| `app.scaling.tasks.run_goal_dlq` | `goals_dlq` | Dead-letter queue handler for exhausted goals |
| `app.scaling.tasks.run_scheduled_goal` | `schedules` | Dispatch a scheduled goal trigger |
| `app.scaling.tasks.fire_due_schedules` | `schedules` | Periodic beat: fire all due schedule rows |
| `app.scaling.tasks.record_queue_depths` | `maintenance` | Record Celery queue depth metrics |
| `app.scaling.tasks.check_mcp_health` | `maintenance` | Periodic MCP connector health probe |
| `app.scaling.tasks.detect_stuck_goals` | `maintenance` | Find goals stuck in `executing` >1h |
| `app.scaling.tasks.execute_retention_policy` | `maintenance` | Delete goal/audit rows past retention |
| `app.scaling.tasks.expire_hitl_approvals` | `governance` | Timeout pending HITL approvals |
| `app.scaling.tasks.check_email_goals` | `maintenance` | IMAP email-to-goal polling |
| `app.scaling.tasks.civilization_tick` | — | Advance civilization simulation step |
| `app.scaling.tasks.civilization_learning_step` | — | Civilization agent learning update |
| `app.scaling.tasks.warm_jwks_cache` | `maintenance` | Pre-warm Keycloak JWKS cache |
| `app.scaling.tasks.create_guardrail_partitions` | `maintenance` | Partition maintenance for guardrails table |
| `app.scaling.tasks.enforce_hitl_sla` | `governance` | Escalate overdue HITL approvals |
| `app.scaling.tasks.flush_audit_wal` | `maintenance` | Flush pending audit WAL entries to DB |
| `agentverse.maintenance.consolidate_memories` | `maintenance` | Compress/consolidate long-term memory |
| `agentverse.maintenance.reindex_stale_knowledge` | `maintenance` | Reindex knowledge chunks with stale embeddings |
| `agentverse.maintenance.purge_expired_artifacts` | `maintenance` | Remove artifacts past retention TTL |
| `agentverse.compliance.run_gdpr_export` | — | Async GDPR data export (max_retries=1) |
| + 7 additional tasks | — | Beat-guard wrappers, billing, SLA, SSO |

**Total: 27 named Celery tasks**

---

## 5. Frontend Feature Directories (`src/features/`)

| # | Directory | Description |
|---|---|---|
| 1 | `a2a/` | Agent-to-Agent (A2A) protocol UI |
| 2 | `admin/` | Admin panel (tenant management, system ops) |
| 3 | `agents/` | Agent CRUD — list, create, detail, identity, personality, radar |
| 4 | `analytics/` | Usage analytics dashboard |
| 5 | `approvals/` | HITL approval queue UI |
| 6 | `artifacts/` | Goal artifact browser |
| 7 | `audit/` | Audit log explorer |
| 8 | `auth/` | Login, SSO callback, API key management |
| 9 | `builder/` | Low-code agent builder |
| 10 | `civilization/` | Civilization simulation viewer |
| 11 | `collaboration/` | Real-time collaborative editing |
| 12 | `compliance/` | GDPR/SOC2/PCI compliance reports |
| 13 | `connectors/` | MCP connector catalog, OAuth flow, management |
| 14 | `dashboard/` | Main dashboard / home |
| 15 | `domains/` | Domain-specific agent templates |
| 16 | `enterprise/` | Enterprise feature management |
| 17 | `errors/` | Error boundary and error pages |
| 18 | `eval/` | Eval suite runner and scorecard |
| 19 | `goals/` | Goal submission, list, detail, ghost-run, DNA, diff |
| 20 | `governance/` | Governance policies and controls |
| 21 | `integrations/` | Third-party integration configuration |
| 22 | `knowledge-graph/` | Knowledge graph viewer |
| 23 | `knowledge/` | RAG knowledge base management |
| 24 | `lab/` | Experimental features playground |
| 25 | `landing/` | Public landing / marketing page |
| 26 | `marketplace/` | Agent template marketplace |
| 27 | `memory/` | Memory explorer |
| 28 | `models/` | LLM model registry UI |
| 29 | `notifications/` | Notification center |
| 30 | `observability/` | Cost dashboard, trace explorer, observability |
| 31 | `onboarding/` | New tenant onboarding flow |
| 32 | `perception/` | Browser perception viewer |
| 33 | `playground/` | Prompt/model playground |
| 34 | `rbac/` | Role-based access control management |
| 35 | `rpa/` | RPA live session viewer |
| 36 | `schedules/` | Trigger schedule management |
| 37 | `security/` | Security settings and audit |
| 38 | `settings/` | Billing, budget, guardrails, MFA, RBAC scopes |
| 39 | `simulation/` | Goal simulation sandbox |
| 40 | `skills/` | Skills management |
| 41 | `status/` | Public status page |
| 42 | `templates/` | Goal template library |
| 43 | `tools/` | Tool catalog |
| 44 | `training/` | Training data export |
| 45 | `workflow-builder/` | Visual workflow builder |

---

## 6. E2E Spec Files (`e2e/`)

**Total: 69 spec files** + `helpers/` directory

| Category | Files |
|---|---|
| Core features | `goals.spec.ts`, `goal-lifecycle.spec.ts`, `goal-dna.spec.ts`, `ghost-run.spec.ts` |
| Agents | `agents.spec.ts`, `agents-e2e.spec.ts`, `agent-personality.spec.ts`, `agent-radar.spec.ts` |
| Governance | `governance.spec.ts`, `governance.governance.spec.ts`, `security-governance.spec.ts` |
| Knowledge & RAG | `knowledge.spec.ts`, `knowledge-rag.spec.ts`, `rag.rag.spec.ts` |
| Observability | `observability.observability.spec.ts`, `cost-breakdown.spec.ts`, `cost-estimate.spec.ts` |
| Marketplace | `marketplace.spec.ts`, `connector-catalog.spec.ts`, `connectors.spec.ts` |
| Workflows & Builders | `workflow-builder.spec.ts`, `workflow-jira-triage.spec.ts`, `builder.spec.ts` |
| Security & Auth | `auth.spec.ts`, `mfa.spec.ts`, `rbac.spec.ts`, `security.security.spec.ts` |
| Eval | `eval.spec.ts`, `eval.eval.spec.ts`, `eval-scorecard.spec.ts` |
| Memory | `memory.spec.ts` |
| Schedules | `schedules.spec.ts` |
| RPA / Perception | `perception.spec.ts` |
| Compliance | `compliance.spec.ts` |
| Accessibility | `accessibility.spec.ts`, `accessibility.a11y.spec.ts` |
| Collaboration | `collaboration.spec.ts`, `crdt-collaboration.spec.ts` |
| Provider | `provider.provider.spec.ts` |
| Multimodal | `multimodal.multimodal.spec.ts` |
| Civilization | `civilization.spec.ts`, `civilization-real-e2e.spec.ts` |
| Analytics | `analytics.spec.ts` |
| Navigation & smoke | `navigation.spec.ts`, `app.smoke.spec.ts`, `dashboard.spec.ts` |
| Fix/polish passes | `final-10-fixes.spec.ts`, `final-polish.spec.ts`, `world-class-fixes.spec.ts`, `audit-fixes.spec.ts` |
| Other | `templates.spec.ts`, `tools.spec.ts`, `notifications.spec.ts`, `settings.spec.ts`, etc. |

---

## 7. CI Workflow Files (`.github/workflows/`)

| File | Trigger | Jobs |
|---|---|---|
| `ci.yml` | push/PR to `main`, `develop`, `feature/**` | lint (ruff + mypy), unit-tests (coverage + Codecov upload), integration-tests (real Postgres + Redis via GH services) |
| `deploy.yml` | push to `main` (or manual) | build+push Docker image to GHCR, deploy-staging (kubectl + `alembic upgrade head`), deploy-production (manual approval gate) |

---

## 8. Backend Test Files by Category

| Category | Test Files |
|---|---|
| `api/` | 115 |
| `agent/` | 68 |
| `mcp/` | 55 |
| `intelligence/` | 38 |
| `governance/` | 37 |
| `services/` | 29 |
| `enterprise/` | 27 |
| `auth/` | 18 |
| `rag/` | 17 |
| `scaling/` | 15 |
| `reliability/` | 15 |
| `e2e/` | 15 |
| `civilization/` | 15 |
| `providers/` | 14 |
| `tenancy/` | 12 |
| `rpa/` | 11 |
| `memory/` | 9 |
| `core/` | 9 |
| `tools/` | 8 |
| `collab/` | 6 |
| `integrations/` | 6 |
| `observability/` | 6 |
| `perception/` | 6 |
| `triggers/` | 6 |
| `infra/` | 7 |
| `db/` | 7 |
| `analytics/` | 5 |
| `security/` | 4 |
| `knowledge/` | 4 |
| `cli/` | 3 |
| `sdk/` | 2 |
| `pipeline/` | 2 |
| `identity/` | 2 |
| `content/` | 2 |
| `skills/` | 1 |
| `persistence/` | 1 |
| `net/` | 1 |
| `live/` | 1 |
| `integration/` | 1 |
| `frontend/` | 1 |
| `costs/` | 1 |
| `compliance/` | 1 |
| `agentic/` | 1 |
| Top-level test files | 10 |
| **Total** | **616** |

---

## 9. Helm Chart Templates (`infra/helm/agentverse/templates/`)

| Template | Purpose |
|---|---|
| `_helpers.tpl` | Template helper macros (labels, names) |
| `app-workloads.yaml` | Backend Deployment, Celery worker/beat Deployments, HPA |
| `backup-cronjob.yaml` | Postgres backup CronJob |
| `configmaps.yaml` | Application configuration ConfigMaps |
| `gateway-observability.yaml` | Prometheus ServiceMonitor, Grafana ingress |
| `local-static-pvs.yaml` | PersistentVolumes for local development |
| `metrics-mail.yaml` | Metrics email alerting CronJob |
| `minio.yaml` | MinIO StatefulSet + Service |
| `postgres.yaml` | Postgres StatefulSet + Service |
| `pvcs.yaml` | PersistentVolumeClaims |
| `redis.yaml` | Redis Deployment + Service |
| `secrets.yaml` | Secrets (DB, Redis, LLM provider keys) |

---

## 10. Docker Compose Services (`infra/docker-compose.yml`)

| Service | Image | Purpose |
|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | Primary database with pgvector extension |
| `pgbackup` | `prodrigestivill/postgres-backup-local:16` | Automated daily Postgres backups |
| `redis` | `redis:7-alpine` | Message broker, cache, pub/sub |
| `pgbouncer` | `edoburu/pgbouncer:v1.25.2-p0` | Connection pool proxy (transaction mode) |
| `backend` | Local build | FastAPI application server (port 8000) |
| `keycloak-db` | `postgres:16-alpine` | Dedicated Postgres for Keycloak |
| `keycloak` | `quay.io/keycloak/keycloak:24.0` | SSO identity provider (port 8080) |
| `worker` | Local build | Celery worker (all goal/schedule/maintenance queues) |
| `beat` | Local build | Celery beat scheduler |
| `minio` | `minio/minio:latest` | Object storage for artifacts (port 9000/9001) |
| `mailpit` | `axllent/mailpit:latest` | SMTP + web UI for local email testing (port 1025/8025) |
| `otel-collector` | `otel/opentelemetry-collector-contrib:0.103.0` | OTLP gRPC/HTTP receiver, forwards to Jaeger |
| `jaeger` | `jaegertracing/all-in-one:1.58` | Distributed trace storage and UI (port 16686) |
| `frontend` | Local build | React frontend served via nginx (port 5173) |
| `kong` | `kong:3.7` | API gateway / reverse proxy (port 8080 external) |
| `searxng` | `searxng/searxng:latest` | Privacy-preserving web search for agent tools |
| `prometheus` | `prom/prometheus:v2.51.0` | Metrics collection (port 9090) |
| `loki` | `grafana/loki:2.9.8` | Log aggregation (port 3100) |
| `promtail` | `grafana/promtail:2.9.8` | Ships Docker container logs to Loki |
| `grafana` | `grafana/grafana:10.4.0` | Metrics and log dashboards (port 3001) |

**Total: 20 services**
