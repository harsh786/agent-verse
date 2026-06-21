# AgentVerse — World-Class Implementation Plan

## Context

**What we're building.** AgentVerse is a vendor-agnostic, multi-tenant **operating system for autonomous AI agents**. An agent receives a natural-language goal, plans its own execution, calls real-world tools via MCP, verifies the result, and replans on failure — with **zero hardcoded workflows**. The same infrastructure runs a bug-fix agent, a payment reconciler, or any other autonomous workflow; specialization comes from five config inputs: **MCP connectors** (tools), **RAG knowledge** (domain facts), **governance policies** (allowed actions), **triggers** (when it acts), and the **goal string** (what to do).

**Why now / source of truth.** The full spec lives in `~/Downloads/full-docs-v2.html` — **47 components across 9 layers, 30+ API endpoints, 7 LLM providers**. The two target directories (`agent-verse-backend/`, `agent-verse-frontend/`) are **empty** → this is a greenfield build, not a migration.

**Confirmed decisions (from clarification):**
- Frontend built **from scratch** (React/TS dashboard matching the provided screenshots).
- This document is the **complete detailed plan**; implementation is deferred to a follow-up.
- **Anthropic/Claude** is the default LLM provider; the vendor-agnostic `LLMProvider` protocol still covers all 7. **Voyage AI** for embeddings (with local `sentence-transformers` fallback for CI).
- Target: production-grade, **TDD throughout**, full E2E (Playwright + pytest), Docker + Kubernetes deployable.

**Intended outcome.** Two production-grade, **independently deployable projects** — `agent-verse-backend` (the agent platform/API) and `agent-verse-frontend` (the operator dashboard) — where any authorized tenant can create, govern, schedule, and run autonomous agents against any domain, with full observability, safety, and reliability. The two are decoupled: the backend is the source of truth and publishes an **OpenAPI contract**; the frontend is a separate app/repo with its own tooling, CI, Docker image, and release cadence, consuming that contract over HTTP/SSE/WebSocket.

---

## Guiding Principles (non-negotiable, enforced in code review)

1. **LLM is the control flow.** No `if/else` routing on goal type. Planner / Executor / Verifier are **three separate LLM calls** with distinct system prompts, orchestrated by a LangGraph state machine.
2. **Every tool call goes through the 12-step pipeline.** Governance, cost, circuit-breaking, HITL, rollback, streaming, usage are cross-cutting concerns wrapped around one `execute_tool()` — implemented as a composable middleware chain, never inlined.
3. **Tenant isolation at 3 layers:** FastAPI middleware (API key → tenant ctx) → Redis (all keys tenant-prefixed) → PostgreSQL **RLS** (row filtering). No query may bypass the tenant context.
4. **Encryption-first.** All credentials AES-256-GCM (Fernet, PBKDF2 480k iters) before they touch Redis. Secrets read via `read_secret()` (`_FILE` env var first, then plain) so one image works dev→prod.
5. **Provider/tool/embedder are pluggable interfaces.** Swapping is a config change, never a code change.
6. **TDD is the default workflow.** Red → green → refactor. No production code without a failing test first. Coverage gate: 80% line / 75% branch on new code (per repo testing rules).

---

## Domain-Agnostic Autonomy — the core promise

**One command, any domain, executed autonomously.** A user types a single natural-language command and gets a working, governed, autonomous agent — whether the domain is **software, devops, bug-fixing, testing, HR, sales, onboarding, finance, support**, or anything else. There is **zero domain-specific code**: the platform never knows what "HR" or "devops" means. Domain behavior emerges entirely from the five runtime config inputs (MCP connectors, RAG knowledge, policies, triggers, goal). Adding a new domain = adding config, never code.

**The single command surface.** `POST /agents/create` (and a parity `agentverse` **CLI**) accept one NL command and run the full bootstrap autonomously:

```
"Create an agent that onboards new engineers: create their accounts, file IT tickets,
 assign first-week tasks, and message them a welcome guide — run it whenever HR adds a hire."
        │
   meta-agent (LLM)  ─►  decomposes intent → selects/【auto-provisions】 connectors
        │                 (HR system, IdP, JIRA, Slack) → writes goal template
        │                 → infers triggers (event: HR.new_hire) → drafts governance policies
        ▼
   live agent created  ─►  (optional autorun / or waits for its trigger) ─► autonomous loop
```

**Capability auto-provisioning (closes the "new domain has no tools" gap).** When the meta-agent or a running agent needs a tool it doesn't have, it resolves capability instead of failing: (1) search the **dynamic MCP registry** + **connector catalog** for a matching server; (2) if found, register it (kicking off OAuth/credential flow via the vault if needed); (3) if none exists, surface a **missing-capability** request (HITL) or fall back to the **browser agent** for systems with no API. All provisioning is governed and audited.

**Autonomy modes (per-agent config).** Every agent declares how much rope it has, layered on top of the permission matrix:
- **Supervised** — every write/high-risk action pauses for HITL approval.
- **Bounded-autonomous** — runs freely within scope/budget/time limits; only `Approval`-level tools pause.
- **Fully-autonomous** — runs end-to-end with no human in the loop; guardrails + circuit breakers + rollback are the only checks (used for trusted, reversible, well-scoped domains).

**Long-horizon & cross-domain decomposition.** A single goal may span domains (onboarding touches HR + IT + devops). The planner decomposes it into a goal tree; independent sub-goals run in parallel (Phase 9), dependent ones sequence; sub-goals may be **delegated to specialized sub-agents** (agent-collaboration / A2A). The verifier checks the whole tree before `complete`.

**Per-domain illustrations (same engine, different config — no code change):**

| Domain | One-command example | Connectors auto-selected | Trigger |
|---|---|---|---|
| Software / bug-fix | "Fix JIRA bugs labeled `prod-down` and open a PR" | GitHub, JIRA, Sentry, sandbox | Webhook (Sentry) |
| DevOps | "Roll back the last deploy if error rate > 2% for 5 min" | Datadog, K8s/CI, Slack | Event (deploy done) |
| Testing | "Generate and run E2E tests for the checkout flow nightly" | GitHub, sandbox (Playwright) | Cron |
| HR / Onboarding | "Onboard new hires end-to-end" | HRIS, IdP, JIRA, Slack | Event (new hire) |
| Sales | "Follow up with leads idle 7+ days and log to CRM" | Salesforce/HubSpot, email | Interval |
| Support | "Triage new tickets, draft replies, escalate P1s" | Zendesk, Slack, RAG KB | Webhook |

This makes the platform a true **operating system for agents** — the user's "fully autonomous, any domain, by command" requirement is a first-class, verifiable capability, not an emergent hope.

## Tech Stack (pinned at scaffold time; verify latest via context7/find-docs before locking)

**Backend (`agent-verse-backend/`)** — Python 3.12
- FastAPI + Uvicorn/Gunicorn · Pydantic v2 (strict models on all 8 route groups)
- **LangGraph** (stateful agent loop + checkpointing) · **Anthropic SDK** (default provider)
- **Celery** + **Redis** (broker, state, pub/sub, locks, priority queues, cache)
- **PostgreSQL 16** + **pgvector** (HNSW) + **pg_trgm** + **RLS** · **asyncpg** · **SQLAlchemy 2.0** (async) · **Alembic**
- **OpenTelemetry** (traces) + **Prometheus** (metrics) + structlog (JSON logs)
- **Playwright** (browser agent) · **Docker SDK** (code sandbox) · **httpx** (MCP/A2A clients)
- `cryptography` (Fernet) · `voyageai` + `sentence-transformers`
- Test: **pytest**, pytest-asyncio, testcontainers (real Redis+PG), respx, **ruff**, mypy

**Frontend (`agent-verse-frontend/`)** — TypeScript
- **React 19 + Vite** · **TanStack Query** (server state) + **Zustand** (UI state) · **React Router**
- **Tailwind** + shadcn/ui (matches the clean screenshot aesthetic) · Recharts (metrics)
- Auth: API-key/JWT bearer context · **native WebSocket** + **SSE** (EventSource) clients
- Test: **Vitest** + Testing Library (unit) · **Playwright** (E2E) · MSW (only for unit-test isolation, never in app code)

**Infra** — Docker Compose (local + prod), Kubernetes manifests (Deployments, Services, Ingress, HPA, Secrets, ConfigMaps, PVCs), GitHub Actions CI/CD.

---

## Project Structure — two separate, independently deployable projects

The backend and frontend are **separate projects** (separate dependency manifests, Dockerfiles, CI workflows, and release cycles). They share **no build tooling and no source code** — the only contract between them is the backend's published **OpenAPI schema**, from which the frontend generates a typed API client. Either can be deployed, versioned, and scaled on its own.

```
Agent-Verse/
├─ agent-verse-backend/          # PROJECT 1 — Python platform/API (own repo, CI, Docker, infra)
│  ├─ app/
│  │  ├─ main.py                      # FastAPI app factory, middleware wiring, lifespan
│  │  ├─ core/                        # config, settings, read_secret(), connection pools, errors
│  │  ├─ db/                          # SQLAlchemy models, session, RLS helpers, alembic/
│  │  ├─ tenancy/                     # middleware, TenantScopedStore (Redis), RLS context
│  │  ├─ providers/                   # LLMProvider protocol + 7 impls + embedders
│  │  ├─ agent/                       # LangGraph graph, nodes (init/rag/plan/execute/verify), state
│  │  ├─ pipeline/                    # 12-step tool-call middleware chain + each step
│  │  ├─ mcp/                         # registry, client, auth (9 types), vault, OAuth, SDK, catalog
│  │  ├─ rag/                         # ingestion pipelines, hybrid search, chunking, embeddings
│  │  ├─ memory/                      # semantic cache, execution memory, long-term memory
│  │  ├─ governance/                  # permissions, policies, HITL, audit, cost, dry-run
│  │  ├─ reliability/                 # circuit breaker, rollback, dedup, result processor
│  │  ├─ triggers/                    # cron, interval, webhook, event, REST, once + NL scheduler
│  │  ├─ perception/                  # browser agent, vision
│  │  ├─ collab/                      # agent-collab protocol, human-agent WS, A2A
│  │  ├─ intelligence/                # explainability, eval, self-optimization, guardrails
│  │  ├─ enterprise/                  # compliance, simulation, red-team, marketplace, meta-agent
│  │  ├─ scaling/                     # celery app, queues, priority queue, parallel executor
│  │  ├─ observability/               # otel setup, prometheus metrics, structured logging
│  │  ├─ cli/                         # `agentverse` CLI — NL command → create + autorun agent
│  │  └─ api/                         # routers (goals, agents, connectors, schedules, tenants,
│  │                                  #          llm, permissions, approvals, knowledge, collab,
│  │                                  #          marketplace, webhooks, events, health, a2a)
│  ├─ tests/                          # mirrors app/ ; unit + integration (testcontainers)
│  ├─ pyproject.toml · Dockerfile · alembic.ini
│  ├─ openapi.json                    # published contract — frontend's only dependency on backend
│  ├─ infra/ (docker-compose.yml, docker-compose.prod.yml, k8s/, grafana/, prometheus/)
│  └─ .github/workflows/ci.yml        # backend-only CI
│
├─ agent-verse-frontend/         # PROJECT 2 — React/TS dashboard (own repo, CI, Docker)
│  ├─ src/ (app, routes, features/<auth|dashboard|goals|agents|connectors|schedules|
│  │        knowledge|governance|collaboration|observability|eval|marketplace|
│  │        enterprise|settings>, lib/api (generated from openapi.json),
│  │        lib/ws, lib/sse, components/ui, components/command-palette, stores, hooks)
│  ├─ tests/ (unit) · e2e/ (Playwright)
│  ├─ package.json · Dockerfile · nginx.conf
│  └─ .github/workflows/ci.yml        # frontend-only CI
```

**Decoupling rules:** the frontend talks to the backend only via HTTP/SSE/WebSocket using a typed client **generated from `openapi.json`** (e.g. `openapi-typescript`); base URL is injected by env/config. No shared packages, no cross-imports. Each project builds and ships its own Docker image; an optional top-level `docker-compose.yml` wires both together for local full-stack runs only.

---

## Data Model (PostgreSQL, all tenant-scoped via RLS)

Core tables (each with `tenant_id`, `created_at`, `updated_at`, RLS policy `tenant_id = current_setting('app.tenant_id')`):
`tenants`, `api_keys`, `agents` (incl. `autonomy_mode`, connector/goal/trigger config), `agent_permissions`, `goals` (with `parent_goal_id` for goal trees), `goal_steps`, `audit_log` (append-only), `approval_requests`, `mcp_servers`, `mcp_credentials` (encrypted), `oauth_tokens`, `policies`, `schedules`, `triggers`, `knowledge_collections`, `documents` (with `embedding vector(N)` + `content_tsv`/trigram), `execution_memory`, `long_term_memory`, `decision_traces`, `evaluations`, `cost_ledger`, `collab_sessions`, `collab_operations`, `agent_templates`.

Indexes: **HNSW** on `documents.embedding`, **GIN/trigram** on document text, B-tree on all FKs + `(tenant_id, status)` hot paths. Alembic migration enables `vector` + `pg_trgm` extensions and installs RLS policies.

---

## Phased Roadmap (9 layers → 47 components, TDD each)

Each phase = its own milestone with failing tests first, then implementation, then refactor. Phases are ordered so every phase ends in something runnable end-to-end.

### Phase 0 — Foundation & scaffolding
- Monorepo, `pyproject.toml`/`package.json`, Dockerfiles, docker-compose (PG+pgvector, Redis, backend, worker, beat, frontend, nginx).
- `core/config` (pydantic-settings), `read_secret()`, `ConnectionPools` (asyncpg 5–20, Redis 50, httpx 100/20-keepalive), `PlatformError` hierarchy + `retry_with_backoff()` + `ErrorHandlerMiddleware`.
- Alembic baseline: extensions, core tables, RLS policies, HNSW/trigram indexes.
- OTel + Prometheus + structlog bootstrap; `/health` + `/metrics`.
- CI skeleton (ruff → mypy → pytest w/ coverage gate → frontend build → audits).
- **Exit:** `docker compose up` boots all 7 containers; health green; CI passes on empty suite.

### Phase 1 — Tenancy & identity (Layer: Multi-tenancy + Governance core)
- Tenant middleware (API key → tenant context), `TenantScopedStore`, RLS context manager.
- `/tenants/signup`, `/tenants/me`, `/tenants/me/keys` (scoped, rotation, expiry), 4 plan tiers + limits.
- Sliding-window rate limiter (per-tenant/per-endpoint, 429 + `Retry-After`/`X-RateLimit-*`).
- Security headers (explicit CORS, CSRF, HSTS, X-Frame DENY, nosniff, Permissions-Policy).
- **Exit:** isolation proven by integration test (tenant A cannot read tenant B at all 3 layers).

### Phase 2 — Intelligence: providers + credential vault
- `LLMProvider` protocol (`complete`, `embed`, `supports_vision`, `supports_tool_use`); `AnthropicProvider` (default), `OpenAICompatibleProvider` (covers OpenAI/Ollama/Groq/Together/Azure/vLLM), `GeminiProvider`.
- Embedder interface: Voyage / OpenAI / Gemini / local sentence-transformers.
- **Credential vault** (Fernet AES-256-GCM, PBKDF2 480k), `/tenants/me/llm`.
- **Exit:** provider swap is one config line; vault round-trip + never-logged tests pass.

### Phase 3 — Execution core: the agent loop (the vertical slice)
- LangGraph state machine: `initialize → rag_retrieval → plan → execute → verify`, conditional edges → `complete | replan | max_iterations | waiting_human`, checkpointing for crash recovery, `max_iterations=15`.
- Three separate LLM roles (planner/executor/verifier) with distinct prompts.
- **Goal-tree decomposition + sub-agent spawning:** planner may split a goal into a tree of sub-goals (parallel where independent, sequenced where dependent), each runnable by a spawned sub-agent; verifier validates the whole tree before `complete`. This is what makes cross-domain, long-horizon commands (e.g. end-to-end onboarding) execute autonomously.
- **Autonomy modes** (`supervised` / `bounded-autonomous` / `fully-autonomous`) read from agent config and enforced at the HITL gate (pipeline step 7).
- **12-step tool pipeline** as composable middleware: cost check → smart context → exec-memory lookup → dedup → circuit breaker → governance/auth → HITL gate → execute → result processor → record rollback → stream (SSE) → record usage. (Stubs for steps owned by later phases, real wiring as they land.)
- Code sandbox (Docker, resource-limited, network-off, auto-remove): `run_command`, `run_tests`, `lint_code`.
- Smart context manager (relevance-ranked, summarize large files, token budget).
- `/goals` (POST), `/goals/{id}`, `/goals/{id}/stream` (SSE).
- **Exit:** submit a goal → plan → execute one real MCP tool → verify → stream result, fully traced.

### Phase 4 — Tool integration: MCP + A2A
- Dynamic Redis-backed **MCP registry** (register/unregister no-restart, `tools/list` discovery, Celery-Beat health checks /30s, priority conflict resolution, graceful drain).
- **Connector auth — 9 types** via one `AuthConfig`; `MCPAuthMiddleware`; `OAuthFlowManager` (auth URL → callback → token exchange → encrypted store → auto-refresh).
- Pre-built **connector catalog** (GitHub, JIRA, Slack, Salesforce, Linear, Notion, Sentry, Datadog, Stripe).
- **Custom MCP SDK** (define tools → generated MCP-compliant FastAPI server + schema validation + test runner).
- **A2A**: Agent Card at `/.well-known/agent.json`, `send_task`/`receive_task`.
- Endpoints: `/connectors/{catalog,register,oauth/callback,connected,{id}/test}`.

### Phase 5 — Agentic RAG + memory
- Hybrid search: vector cosine (70%) + trigram FTS (30%), HNSW, configurable threshold.
- Ingestion pipelines: git repos (git metadata, sentence-boundary chunking, content-hash dedup, incremental re-embed), JIRA, markdown, OpenAPI, Slack.
- `/knowledge/{collections,ingest,search}`.
- **Semantic cache** (cosine ≥ 0.92 → cached result, zero LLM calls).
- **Execution memory** (winning plans, failed approaches, tool reliability) + **long-term memory** (cross-session learnings) → fed into planning prompts.

### Phase 6 — Governance & safety (full)
- Agent **permission matrix** (Allow / Allow+log / Approval / Deny) + daily/per-goal limits, scope restrictions (repos/file patterns/JIRA projects), cooldowns, budget caps, time windows.
- **Policies** (tool-attached, cross-agent, stack with permissions).
- **HITL gateway** (ApprovalRequest → Redis → notify Slack/email/UI → pause/free worker → resume/abort → timeout escalation).
- **Audit trail** (immutable, queryable per goal/agent/tool).
- **Cost controls** (per-goal/per-tenant caps, pre-call estimate+abort, pricing table).
- **Dry-run mode** (skip real execution, preview plan/side-effects/cost).
- Endpoints: `/agents`, `/agents/{id}/permissions`, `/goals/{id}/audit`, `/goals/{id}/approve`.

### Phase 7 — Reliability layer
- **Circuit breakers** (CLOSED/OPEN/HALF_OPEN, 3-fail open, 60s recovery, fallback tool routing).
- **Rollback engine** (side-effect → inverse map, LIFO `rollback_all()` on failure).
- **Deduplication** (content hash + Redis distributed locks).
- **Result processor** (redact secrets via regex, standardize errors, truncate, flatten JSON).

### Phase 8 — Control layer: triggers & scheduling
- 6 trigger types: cron (Celery Beat), interval, **webhook** (`/webhooks/{token}`), **event** (Redis pub/sub, `/events`), **REST**, **once** (Celery ETA).
- **NL scheduler** (`/nl/schedule`): LLM parses "every weekday at 10 AM IST" → cron + tz; compound/modify/delete/query; multi-turn refinement. Example: "remind me to take meds at 8 AM, 2 PM, 8 PM daily" → 3 schedules.
- **Condition checker** (NL conditions evaluated at run time; skip silently if unmet).
- `/schedules` CRUD.

### Phase 9 — Scale & performance
- **Celery** task routing (goals/schedules/maintenance queues), dead-letter, worker mem limits (500MB, restart/100 tasks), lifecycle signals.
- **Priority queue** (Redis sorted set, score `priority*1e12 + ts`, P0→P4, FIFO within level).
- **Parallel step execution** (dependency analysis → `asyncio.gather` + semaphore).
- Load/scale validation toward the "millions of requests" target (horizontal workers + HPA + connection pooling + pgBouncer note).

### Phase 10 — Perception & collaboration
- **Browser agent** (Playwright headless Chromium in sandbox → screenshot → vision LLM → click/type/scroll/navigate).
- **Multimodal vision** (`analyze_image`, `extract_text_from_screenshot`, `analyze_error_screenshot`).
- **Agent collaboration** (multi-round propose/critique/counter/agree + consensus synthesis).
- **Human-agent collab** (WebSocket shared doc, 4 modes Suggest/Co-write/Review/Autonomous, versioned ops, inline diffs + confidence). `/collab/sessions`, `/collab/sessions/{id}/ws`.

### Phase 11 — Intelligence (advanced) & enterprise
- **Explainability** (`DecisionTrace`: action, reasoning chain, evidence, alternatives, confidence).
- **Eval + benchmarking** (5 dims, 70% pass threshold, trend aggregation).
- **Self-optimization** (analyze failed evals → prompt/tool/retry suggestions, auto/human-applied).
- **Output guardrails** (hallucinated tool names, malformed params, unsafe patterns, data leakage).
- **Compliance** (GDPR delete/export/residency, SOC2, PCI-DSS, retention sweep).
- **Simulation + red team** (mock-tool sandbox; adversarial cases: prompt injection, bad formats, resource exhaustion, exfiltration).
- **Marketplace + meta-agent — the one-command, any-domain surface** (`/marketplace/{browse,deploy}`, `/agents/create`, and the `agentverse` CLI):
  - NL command → meta-agent decomposes intent → selects connectors → writes goal template → infers triggers → drafts governance policies → creates a **live agent**; `autorun` flag optionally executes immediately.
  - **Capability auto-provisioning:** when a needed tool is absent, search the MCP registry + connector catalog, register/OAuth it via the vault, or raise a governed missing-capability request / fall back to the browser agent.
  - Sets the agent's **autonomy mode** and permission matrix from the command + tenant policy defaults.
  - Validated across the six reference domains (software, devops, testing, HR/onboarding, sales, support) as acceptance tests.

### Phase 12 — Frontend (separate project, built from scratch, real APIs from day one)

Stands up as its **own project/repo** with independent tooling, CI, and Docker image; depends on the backend only through the API client **generated from `openapi.json`**. Auth context (API key/JWT), React Router, TanStack Query (server state) + Zustand (UI state). **No mock data in app code** — SSE + WebSocket live everywhere. Every backend capability has a screen; every screen below lists its full action set.

**Complete view → action map (every feature, every action):**

| View | Surfaces (features) | All user actions |
|---|---|---|
| **Auth / Onboarding** | Tenant signup, login (API key/JWT), plan-tier picker | Sign up, log in/out, switch tenant, accept invite |
| **Dashboard** | KPI cards (active goals, success rate, avg latency, cost today/tokens), live activity feed, connector health strip | Change time range, filter feed by status/agent, click row → goal detail, pause/resume feed |
| **Goals — list** | Table by status (planning/executing/waiting-human/complete/failed), priority, agent, cost, duration | Submit goal, **dry-run** preview, filter/search/sort, set priority (P0–P4), bulk cancel, export |
| **Goals — detail** | Live **SSE stream** of pipeline steps, **goal-tree** visualization (sub-goals/sub-agents), plan view, per-step tool calls + I/O, **audit trail**, **decision traces** (explainability: reasoning/evidence/alternatives/confidence), **eval scorecard** (5 dims), cost/token meter, rollback log, browser-agent screenshots/vision | Cancel, retry/replan, **approve/reject HITL** (with reason), trigger **rollback_all**, re-run, download audit, copy trace, expand/collapse sub-goals, view raw tool output |
| **Agents — list** | All agents, autonomy mode badge, status, owner, last run | **Create via NL command** (meta-agent), clone, enable/disable, delete, search |
| **Agents — create (meta-agent)** | NL command box → preview of selected connectors/goal/triggers/policies + auto-provisioning report | Submit command, edit any inferred field, **autorun toggle**, deploy, save as template |
| **Agents — detail** | Config, **permission matrix editor** (allow/log/approval/deny per tool), daily & per-goal limits, scope restrictions (repos/file globs/projects), cooldowns, budget caps, time windows, **autonomy mode** selector | Edit config, edit each permission cell, set limits/scopes/budgets, change autonomy mode, test/dry-run, view this agent's goals & audit |
| **Connectors — catalog** | Pre-built catalog (GitHub, JIRA, Slack, Salesforce, Linear, Notion, Sentry, Datadog, Stripe) | Browse/search, **one-click connect**, start OAuth flow |
| **Connectors — registered** | Connected servers, health status, discovered tools (`tools/list`), conflict priority, auth type | Register custom (9 auth types: bearer/API-key/OAuth-AC/OAuth-CC/PKCE/basic/custom-header/mTLS/HMAC), **test credentials**, re-auth/refresh, set priority, **graceful drain + unregister**, view health history, inspect tools |
| **Connectors — custom SDK builder** | Define tool name/desc/schema → generated MCP server scaffold + validation + test runner | Define tools, validate against MCP spec, run test, register |
| **Schedules & Triggers** | All 6 types (cron/interval/webhook/event/REST/once), next-run, run history, NL conditions | **NL scheduler chat** (create/modify/delete/query, multi-turn), manual create per type, edit, pause/resume, delete, copy webhook URL, manage event subscriptions, edit condition, test-fire |
| **Knowledge (RAG)** | Collections, ingestion sources (git/JIRA/markdown/OpenAPI/Slack), ingestion progress, **hybrid-search playground**, document/chunk browser, embedder config, semantic-cache stats | Create collection, **ingest source**, monitor/cancel ingestion, re-ingest (incremental), run hybrid search, view chunks + scores, delete docs, choose embedding provider, clear cache |
| **Governance** | Cross-agent **policies**, **HITL approval inbox** (risk level, reasoning, timeout), **audit-log explorer** (per goal/agent/tool), cost & budget controls, secrets/vault status, dry-run, retention | Create/edit/stack policy, **approve/reject** (with note), set per-goal/per-tenant budgets, filter/export audit, configure retention sweep, view vault status (never values) |
| **Collaboration** | **Agent-collab** session viewer (rounds: propose/critique/counter/agree + consensus), **human-agent shared doc** editor (WebSocket) | Start/join session, switch mode (suggest/co-write/review/autonomous), **accept/reject inline diffs** (with confidence), view consensus, version history |
| **Observability** | OTel **trace explorer** (spans plan→execute→verify), metrics (the 6 Grafana rows: goals/cost/tool-health/queue-depth/RAG-perf/tenant-usage), **alerts** + runbook links | Filter/search traces, drill into spans, view metric trends, acknowledge alert, open runbook |
| **Intelligence / Eval** | Eval history (5 dims, 70% threshold), benchmark trends, **self-optimization suggestions** | View scorecards/trends, **apply/reject optimization suggestion**, compare runs |
| **Marketplace** | Template gallery, preview, meta-agent create | Browse/search, preview config, **one-click deploy**, fork & customize, publish template |
| **Enterprise / Compliance** | GDPR export/delete, retention, **simulation** runner (mock tools), **red-team** runner + report | Request data export/delete, run simulation, run red-team, view reports |
| **Settings / Tenant** | Profile, plan tier + limits, **LLM provider config** (all 7 + keys), **API-key management** (scoped, rotation, expiry), usage/billing, team & roles | Edit profile, configure/switch LLM provider, add provider key, **create/rotate/revoke API key**, set scopes, view usage, manage team, upgrade plan |

**Cross-cutting UI standards (apply to every view):**
- **Real-time first** — SSE for goal streams, WebSocket for collab + live dashboards; optimistic updates via TanStack Query.
- Consistent **loading / empty / error / success** states; toasts for async actions; **command palette** (⌘K) for quick navigation + goal submission.
- **Dry-run everywhere** an action mutates state (preview before commit).
- **WCAG 2.2 AA** (semantic HTML, keyboard nav, focus indicators, 4.5:1 contrast), `prefers-color-scheme` dark mode, `prefers-reduced-motion`, 44px touch targets, mobile-first responsive — per accessibility/design-standards rules.
- Role-aware rendering (actions hidden/disabled by tenant permission + autonomy mode).

**Frontend test coverage:** Vitest unit tests per component/hook/store; Playwright E2E for each view's primary action set (submit goal + watch stream, create agent via NL, connect connector via OAuth, approve HITL, ingest + search knowledge, NL schedule, collab session, rotate API key) — all against real APIs.

### Phase 13 — Hardening, observability dashboards, deployment
- Grafana dashboard (6 rows: goals, cost, tool health, queue depth, RAG perf, tenant usage) + 6 alert rules + runbooks.
- Full K8s manifests + HPA; docker-compose.prod; secrets via K8s/Swarm. Each project ships its own image + manifests.
- **Two independent CI/CD pipelines** (one per project):
  - *Backend:* ruff → mypy → unit → integration (real Redis+PG via testcontainers) → pip-audit → publish `openapi.json` → docker build/push → k8s rollout + smoke test.
  - *Frontend:* lint → typecheck → regenerate client from published `openapi.json` (fail on drift) → Vitest → Playwright E2E → npm audit → docker build/push → k8s rollout.

---

## Testing Strategy (TDD + E2E, every phase)

- **Unit (pytest/Vitest):** every module; mock only at boundaries (HTTP via respx/MSW, clock, randomness, Docker, LLM provider via a `FakeProvider`). Prefer in-memory fakes over mocks.
- **Integration (testcontainers):** real PostgreSQL+pgvector and Redis — RLS isolation, hybrid search ranking, Celery task routing, circuit-breaker state, vault round-trip.
- **Contract:** MCP client against a reference MCP test server; A2A against a stub agent card.
- **E2E (Playwright):** full flows against a running stack — submit goal → watch SSE stream → HITL approval → completion; NL schedule creation; connector OAuth (mocked IdP); collab WebSocket session; tenant isolation in UI.
- **Adversarial:** red-team suite (prompt injection, guardrail bypass attempts) runs in CI.
- Gates: 80% line / 75% branch on new code; coverage may not decrease; no skipped tests on main.

---

## Verification (how we prove it works end-to-end)

1. `docker compose up` → all 7 containers healthy; `GET /health` returns per-dependency status; `GET /metrics` exposes Prometheus.
2. **Vertical slice:** `POST /goals` with a real goal + a registered MCP connector → poll `GET /goals/{id}` and watch `GET /goals/{id}/stream` (SSE) → goal reaches `complete`; `GET /goals/{id}/audit` shows every pipeline step; an OTel trace spans plan→execute→verify.
3. **Governance:** configure a tool as Approval → goal pauses at `waiting_human` → `POST /goals/{id}/approve` resumes it; deny-listed tool is blocked; budget-exceeded goal aborts at step 1.
4. **Tenancy:** automated test asserts tenant A cannot read/list/query tenant B resources via API, Redis keys, or SQL.
5. **RAG:** ingest a repo via `/knowledge/ingest` → `/knowledge/search` returns hybrid-ranked results; identical query hits semantic cache (zero LLM calls, verified by metrics).
6. **Scheduling:** NL "remind me at 8 AM, 2 PM, 8 PM daily" → 3 schedules created; Celery Beat fires them.
6b. **One-command, any-domain autonomy:** for each of the six reference domains, a single `POST /agents/create` (or `agentverse create "..."`) command produces a live agent with the right connectors/goal/triggers/policies and, with `autorun`, completes a goal autonomously — proving zero domain-specific code. Capability auto-provisioning registers a missing connector mid-flow.
7. **Frontend E2E (per-view action sets, all against real APIs):** Playwright covers — submit goal + watch SSE stream + view trace/audit/eval; create agent via NL command (meta-agent) + edit permission matrix + set autonomy mode; connect a connector via OAuth + test + inspect tools; approve/reject a HITL request; create collection + ingest + hybrid search; NL schedule creation; agent-collab + human-agent shared-doc session; rotate an API key; configure LLM provider; deploy a marketplace template.
8. **CI:** full pipeline green including integration + security audits + coverage gates.

---

## Risks & Mitigations

- **Scope (47 components).** Mitigated by strict phase ordering where every phase is independently runnable and tested; later phases plug into earlier interfaces (pipeline steps are stubbed then replaced).
- **LLM nondeterminism in tests.** A `FakeProvider` returns scripted plans/verdicts; real-provider tests are marked slow/opt-in.
- **pgvector scale.** HNSW + partitioning + pgBouncer; load-test in Phase 9 before claiming the "millions" target.
- **Version drift (LangGraph/pgvector/Anthropic SDK move fast).** Pin versions at scaffold time and verify current APIs via context7/find-docs before locking each phase.
- **Security of the code sandbox & browser agent.** Network-off by default, resource caps, auto-teardown, no host mounts.

---

## First Implementation Step (when approved)

Begin **Phase 0**: scaffold the monorepo, write the failing health-check + container-boot integration tests, then implement `core/config`, `read_secret()`, `ConnectionPools`, error hierarchy, the Alembic baseline (extensions + RLS + HNSW), OTel/Prometheus bootstrap, and docker-compose — closing the loop with a green `docker compose up` and passing CI.