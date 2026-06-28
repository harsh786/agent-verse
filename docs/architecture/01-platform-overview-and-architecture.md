# AgentVerse OS — Platform Overview and Architecture

> **Document Version:** 1.0  
> **Last Updated:** 2026-06-29  
> **Audience:** Engineers, architects, technical leads, and system integrators  
> **Scope:** Complete platform overview covering all architectural layers, components, and subsystems

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [The Agent OS Metaphor](#2-the-agent-os-metaphor)
3. [High-Level Architecture Diagram](#3-high-level-architecture-diagram)
4. [Monorepo Structure — Five Independent Packages](#4-monorepo-structure--five-independent-packages)
5. [Backend Application Modules](#5-backend-application-modules)
6. [API Router Catalogue — 27 Routers](#6-api-router-catalogue--27-routers)
7. [Database Architecture](#7-database-architecture)
8. [Message Queue and Background Workers](#8-message-queue-and-background-workers)
9. [Middleware Stack](#9-middleware-stack)
10. [Infrastructure Services](#10-infrastructure-services)
11. [Two-Phase Service Wiring Pattern](#11-two-phase-service-wiring-pattern)
12. [Observability Stack](#12-observability-stack)
13. [Configuration System](#13-configuration-system)
14. [Security Architecture Summary](#14-security-architecture-summary)

---

## 1. Executive Summary

AgentVerse OS is a **vendor-agnostic, multi-tenant operating system for autonomous AI agents**. The platform enables organizations to submit natural-language goals and have autonomous agents plan, execute, verify, and replan their own workflows — connecting to over 119 real-world services via the Model Context Protocol (MCP) — without any hardcoded workflows.

Where traditional automation tools require engineers to write deterministic scripts for every process, AgentVerse OS inverts the model: agents receive a goal, decompose it into steps using a planner LLM, execute each step by calling tools, verify the outcome using a verifier LLM, and either complete or replan on failure. The entire execution graph is dynamic, checkpointed, audited, cost-tracked, and observable in real time.

### Key Differentiators

| Dimension | AgentVerse OS Capability |
|-----------|--------------------------|
| **Multi-tenancy** | Database-level Row-Level Security (RLS) on every table — not application-layer |
| **Vendor freedom** | Supports Anthropic, OpenAI, Google Gemini, Voyage, and any OpenAI-compatible endpoint |
| **Compliance** | GDPR, HIPAA, SOC 2, PCI-DSS, SAML 2.0, SCIM 2.0 built-in |
| **Scalability** | Per-plan Celery queue isolation — enterprise tenants get dedicated worker pools |
| **Observability** | OpenTelemetry → Jaeger traces, Prometheus metrics, Grafana dashboards, structlog |
| **Agent Civilization** | Multi-agent governance with Constitutional AI, EWMA reputation scoring, shared blackboard |
| **Cost control** | Real token counts, hierarchical budgets, Redis-backed cross-replica accuracy |
| **Guardrails** | 6-layer guardrail engine with 100+ patterns, LLM judge, per-tenant configurability |

---

## 2. The Agent OS Metaphor

The name "AgentVerse OS" is deliberate and precise. Traditional operating systems manage processes, memory, I/O, and security on behalf of programs running on a machine. AgentVerse OS does the equivalent for AI agents running in the cloud:

```
Traditional OS                    AgentVerse OS
──────────────────────────────────────────────────────────────────
Process scheduler              → Celery per-plan queue routing
Process isolation              → Row-Level Security per tenant
System calls (I/O)             → MCP tool dispatch (119 connectors)
File system                    → Knowledge Store (pgvector + pg_trgm)
Memory management              → ExecutionMemory + LongTermMemoryStore
Security & permissions         → PolicyEngine + GuardrailEngine
Process checkpointing          → LangGraph AsyncRedisSaver
Signals & IPC                  → Redis pub/sub, SSE event streams
Process groups / supervision   → Agent Civilization (Governor + Society)
System audit log               → Append-only AuditLog (SHA-256 chained)
Init system (systemd)          → create_app() + lifespan two-phase wiring
```

Every component maps cleanly to the OS metaphor because the design principle is the same: **provide a safe, observable, multi-tenant environment in which autonomous programs (agents) can execute arbitrary workloads against real-world resources**.

---

## 3. High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              AgentVerse OS Platform                              │
├───────────────────────┬───────────────────────────┬────────────────────────────┤
│   Browser / CLI       │   Backend API Layer        │   Background Workers       │
│                       │                            │                            │
│  React 19 + Vite      │  FastAPI (Python 3.12)     │  Celery Workers            │
│  TanStack Query       │  Uvicorn (ASGI)            │  goals.free queue          │
│  Zustand state        │  27+ API routers           │  goals.starter queue       │
│  Tailwind CSS         │  SSE event streaming       │  goals.professional queue  │
│  @xyflow/react        │  WebSocket collab          │  goals.enterprise queue    │
│  D3-force vis         │  OpenAPI auto-generated    │  schedules queue           │
│                       │                            │  maintenance queue         │
│  Python SDK (httpx)   │  Middleware Stack:         │  goals_dlq (dead-letter)   │
│  TypeScript SDK       │  → CORS                    │                            │
│  GitHub Action        │  → SecurityHeaders         │  RedBeat (cron scheduler)  │
│                       │  → ScopeEnforcement        │  Per-plan queue routing    │
│                       │  → TenantMiddleware        │  Max 100 tasks/child       │
│                       │  → RateLimiter             │  500 MB memory limit       │
└───────────┬───────────┴────────────┬──────────────┴─────────────┬──────────────┘
            │                        │                             │
            ▼                        ▼                             ▼
┌───────────────────┐  ┌─────────────────────────┐  ┌────────────────────────────┐
│  Vite Dev Server  │  │  Connection Pool Layer   │  │  Redis 7 (Alpine)          │
│  HMR + ESBuild    │  │                          │  │                            │
│  :5173            │  │  PgBouncer :6432         │  │  Celery broker/backend     │
└───────────────────┘  │  (connection pooling)    │  │  SSE pub/sub per goal      │
                       │                          │  │  LangGraph checkpoints     │
                       │  PostgreSQL 16 + pgvector│  │  API key cache (5 min TTL) │
                       │  :5432                   │  │  HITL approval queue       │
                       │                          │  │  Semantic cache (1 hr TTL) │
                       │  Row-Level Security       │  │  Policy propagation        │
                       │  64 Alembic migrations   │  │  Rate limiter (ZSET)       │
                       │  Monthly partitions       │  │  Session store (RPA)       │
                       │  pgvector HNSW index      │  │  :6379                     │
                       │  pg_trgm for hybrid search│  └────────────────────────────┘
                       └─────────────────────────┘
                                   │
            ┌──────────────────────┼─────────────────────────┐
            ▼                      ▼                         ▼
┌──────────────────┐  ┌────────────────────────┐  ┌─────────────────────────────┐
│   Keycloak       │  │  MinIO (S3-compatible) │  │  Observability Stack        │
│   :8080          │  │  Artifact storage      │  │                             │
│   SAML 2.0       │  │  GDPR export bundles   │  │  OTel Collector :4317       │
│   OIDC           │  │  RPA screenshots       │  │  Jaeger :16686              │
│   SCIM 2.0       │  │  Eval reports          │  │  Prometheus :9090           │
│   SSO federation │  │  Audit exports         │  │  Grafana :3000              │
└──────────────────┘  │  :9000 / :9001         │  │  structlog JSON             │
                       └────────────────────────┘  └─────────────────────────────┘
                                   │
            ┌──────────────────────┼─────────────────────────┐
            ▼                      ▼                         ▼
┌──────────────────┐  ┌────────────────────────┐  ┌─────────────────────────────┐
│  SearXNG         │  │  Mailpit (dev)         │  │  119 MCP Tool Connectors    │
│  Private search  │  │  Email notifications   │  │                             │
│  :8080           │  │  HITL approvals        │  │  Slack, GitHub, Stripe      │
│  No tracking     │  │  Goal alerts           │  │  Salesforce, Notion         │
└──────────────────┘  │  :8025                 │  │  AWS, GCP, Azure            │
                       └────────────────────────┘  │  Jira, Linear, Asana       │
                                                    │  Postgres, Redis, MySQL    │
                                                    │  And 105 more...           │
                                                    └─────────────────────────────┘
```

### Request Flow (Goal Execution)

```
Client (Browser/SDK/CLI)
        │  POST /api/goals  (API key in X-API-Key header)
        ▼
TenantMiddleware  ──► API key lookup in Redis cache (5 min TTL)
        │              └─► Cache miss → Postgres lookup → cache
        ▼
ScopeEnforcementMiddleware  ──► Check scope "goals:write" for endpoint
        │
        ▼
GoalService.submit()  ──► Validate, persist to DB, emit SSE event
        │
        ▼
CeleryGoalTaskQueue.enqueue()  ──► Route to goals.{plan} queue
        │
        ▼
Celery Worker (per-plan queue)
        │
        ▼
PersistenceEngine  ──► Upsert goal attempt, set status=PLANNING
        │
        ▼
AgentGraph.run()  ──► LangGraph state machine
        ├── initialize    (hydrate AgentState, RAG context)
        ├── plan          (Planner LLM → structured steps)
        ├── execute       (Executor LLM → tool calls)
        │     ├── PolicyEngine.evaluate()
        │     ├── GuardrailEngine.check() [layer 4]
        │     ├── MCPClient.call_tool()
        │     ├── GuardrailEngine.check() [layer 5, output]
        │     ├── CostController.record()
        │     └── AuditLog.append()
        ├── verify        (Verifier LLM → success/failure)
        └── complete | replan | max_iterations | waiting_human
        │
        ▼
GoalService.complete()  ──► Update DB status, emit terminal SSE event
        │
        ▼
Client receives final event via SSE stream
```

---

## 4. Monorepo Structure — Five Independent Packages

The repository follows a **monorepo-with-independent-toolchains** pattern. Each package has its own dependency manifest, test suite, and CI stage. They share git history and the OpenAPI contract that the backend publishes.

```
Agent-Verse/
├── agent-verse-backend/          # Python 3.12 · FastAPI · LangGraph · Celery
│   ├── app/                      # Application source (27 modules)
│   ├── tests/                    # Unit + integration tests (mirrors app/)
│   ├── infra/                    # docker-compose, Prometheus config, Grafana dashboards
│   ├── scripts/                  # export_openapi.py, seed_data.py
│   ├── pyproject.toml            # uv-managed deps, ruff + mypy config
│   └── .github/workflows/ci.yml # ruff → mypy → pytest CI pipeline
│
├── agent-verse-frontend/         # React 19 · Vite · TanStack Query · Tailwind
│   ├── src/
│   │   ├── features/             # 35 feature slices (one dir per domain)
│   │   ├── lib/                  # api/client.ts, sse/, ws/, hooks/
│   │   ├── components/           # Shared UI components (shadcn/ui based)
│   │   └── pages/                # Route-level page components
│   ├── e2e/                      # Playwright end-to-end tests
│   ├── vite.config.ts
│   └── package.json
│
├── agent-verse-sdk-python/       # Python 3.11+ · httpx · pydantic · CLI
│   ├── agentverse/               # Client, models, CLI entrypoint
│   ├── tests/
│   └── pyproject.toml            # Published to PyPI as "agentverse-sdk"
│
├── agent-verse-sdk-typescript/   # TypeScript · vitest · zero runtime deps
│   ├── src/                      # AgentVerseClient, typed models, stream helpers
│   ├── tests/
│   └── package.json              # Published to npm as "@agentverse/sdk"
│
├── agent-verse-github-action/    # Python entrypoint in Docker container
│   ├── entrypoint.py             # Reads GitHub Action inputs, submits goal, polls
│   ├── Dockerfile
│   └── action.yml                # GitHub Action manifest
│
├── CLAUDE.md                     # Codebase guidance for AI assistants
└── docs/                         # Architecture docs, specs, plans
    ├── architecture/             # This document lives here
    └── superpowers/
        ├── specs/                # Feature specifications
        └── plans/                # Implementation plans
```

### Package Interdependencies

```
agent-verse-backend  ←──(local path uv source)──  agent-verse-sdk-python
                          (SDK tested against real backend in CI)

agent-verse-frontend ←──(HTTP/SSE/WebSocket)──    agent-verse-backend
                          (OpenAPI contract consumed by openapi-typescript)

agent-verse-github-action ←──(PyPI install)──     agent-verse-sdk-python
                          (GitHub Action uses SDK CLI to submit goals)
```

---

## 5. Backend Application Modules

The `app/` directory contains 27 top-level packages, each with a single clear responsibility. Below is an exhaustive catalogue:

### 5.1 `app/agent/` — Core Execution Engine

The heart of the platform. Implements the LangGraph-powered autonomous agent loop.

| File | Responsibility |
|------|---------------|
| `loop.py` | Entry point for goal execution — sets up LangGraph graph, injects dependencies, runs to completion |
| `graph.py` | Defines LangGraph `StateGraph` with all nodes and conditional edges |
| `state.py` | `AgentState` TypedDict — all mutable state that flows through the graph |
| `prompts.py` | System prompts for Planner, Executor, and Verifier LLM roles |
| `model_router.py` | Selects model per task type: haiku for verify, sonnet for execute, opus for complex planning |
| `structured_plan.py` | `StructuredPlan` Pydantic model — steps with dependencies, loop_until, parallel markers |
| `tool_calls.py` | Tool call execution loop within the executor node |
| `tool_context.py` | Assembles tool descriptions and schemas from MCPRegistry for the Executor prompt |
| `tool_risk.py` | Classifies tool calls as low/medium/high risk (triggers HITL for high-risk) |
| `sanitization.py` | Pre-execution input cleaning and injection prevention |
| `router.py` | Auto-routes a goal to the best matching agent when no `agent_id` is provided |
| `supervisor.py` | Multi-agent supervisor: decomposes goal into sub-goals, spawns parallel agent loops |
| `debate.py` | Agent debate pattern: two agents propose competing approaches, arbitrator decides |
| `persistence.py` | `PersistenceEngine` — 6 retry strategies, DB-persisted attempts, recovery on restart |
| `goal_tree.py` | `GoalTree` — tracks parent/child relationships for supervisor-spawned sub-goals |
| `workflow_planner.py` | Converts a visual workflow (from `@xyflow/react` builder) to a structured plan |
| `workflow_executor.py` | Executes a structured workflow plan node-by-node with branching logic |
| `errors.py` | Agent-specific exception hierarchy |

### 5.2 `app/api/` — HTTP API Layer (27 Routers)

See full catalogue in Section 6.

### 5.3 `app/auth/` — Authentication & Identity

| File | Responsibility |
|------|---------------|
| `agent_identity.py` | Per-agent RS256 JWT credentials, JWKS endpoint, scoped capability tokens |
| `scope_enforcement.py` | `ScopeEnforcementMiddleware` — maps every endpoint to required scopes via `ENDPOINT_SCOPES` dict |
| `keycloak.py` | Keycloak OIDC integration for user-facing SSO |
| `saml_provider.py` | SAML 2.0 SP-init, IdP-init, and SLO via `python3-saml` |
| `scim_handler.py` | SCIM 2.0 user/group provisioning, group-to-role mapping |
| `ip_allowlist.py` | CIDR-based IP allowlisting with Redis-cached lookup |
| `permission_cache.py` | Redis-backed 10-minute permission cache to reduce DB pressure |
| `cache_warmer.py` | Pre-warms permission and API key caches on startup |
| `scope_seeder.py` | Seeds the 30+ built-in scopes into the DB on first startup |

### 5.4 `app/civilization/` — Agent Civilization (Multi-Agent Governance)

| File | Responsibility |
|------|---------------|
| `orchestrator.py` | `CivilizationOrchestrator` — lifecycle management for civilization instances |
| `governor.py` | `Governor` — evaluates whether to spawn specialist sub-agents, enforces Constitution |
| `constitution.py` | Constitutional AI rules for civilization — defines behavioral constraints |
| `society.py` | `Society` — tracks EWMA reputation scores for each agent in the civilization |
| `blackboard.py` | `Blackboard` — shared key-value store for inter-agent findings |
| `learning.py` | `LearningPipeline` — extracts learnings from completed goals, runs anti-poisoning gate |
| `bus.py` | Civilization event bus — broadcasts events to all agents in the civilization |
| `events.py` | Civilization event types (agent_spawned, finding_posted, constitution_violation, etc.) |
| `a2a_dispatch.py` | Agent-to-Agent task dispatch within a civilization |
| `spawn_tool.py` | MCP-style tool that agents can call to spawn sub-agents |
| `metrics.py` | Per-civilization Prometheus metrics |
| `models.py` | `CivilizationState`, `AgentReputation`, `ConstitutionRule` Pydantic models |

### 5.5 `app/core/` — Framework Foundation

| File | Responsibility |
|------|---------------|
| `config.py` | `Settings` Pydantic model — all config from environment (12-factor) |
| `errors.py` | `PlatformError` hierarchy with severity, error_id, HTTP status |
| `pools.py` | `ConnectionPools` — asyncpg pool + redis.asyncio pool lifecycle management |
| `logging.py` (in observability) | structlog configuration, JSON formatter |

### 5.6 `app/db/` — Database Layer

| Directory/File | Responsibility |
|----------------|---------------|
| `models/` | 16 SQLAlchemy model files (one per domain) |
| `migrations/versions/` | 64 Alembic migration scripts (baseline → consent_records_v2) |
| `rls.py` | `rls_context()` async context manager — sets `app.tenant_id` GUC via `SET LOCAL` |
| `session.py` | Async session factory, dependency injection helper |

### 5.7 `app/enterprise/` — Enterprise Features

| File | Responsibility |
|------|---------------|
| `compliance.py` | GDPR export, cascade deletion, HIPAA minimum-necessary access controls |
| `compliance_v2.py` | `ComplianceChecker` — SOC 2 certification tracking, legal holds, PCI-DSS checks |
| `marketplace.py` | `Marketplace` — template gallery with security review, atomic deploy |
| `marketplace_v2.py` | `MarketplaceV2` — ratings, versioning, publisher analytics |
| `red_team.py` | `RedTeamRunner` — adversarial testing against guardrails |
| `simulation.py` | `SimulationRunner` — dry-run mode with mocked tool responses |

### 5.8 `app/governance/` — Policy, Cost, Audit, HITL

| File | Responsibility |
|------|---------------|
| `audit.py` | `AuditLog` — append-only SHA-256 chained audit trail, Redis WAL for at-least-once delivery |
| `audit_v2.py` | Enhanced audit with SIEM integration (Splunk, Elasticsearch, Datadog, CEF, LEEF) |
| `cost.py` | `CostController` — per-goal/per-tenant/per-agent budgets, Redis-backed cross-replica accuracy |
| `hitl.py` | `HITLGateway` — Redis BLPOP cross-replica human-in-the-loop approval queue |
| `legal_holds.py` | Legal hold management — prevents deletion under active litigation |
| `permissions.py` | Permission evaluation engine for 5 built-in roles + custom ABAC conditions |
| `policies.py` | `PolicyEngine` — glob + semantic + cost_threshold + rate_limit policy rules |
| `pricing.py` | Per-model pricing table for cost estimation and anomaly detection |
| `siem_adapters.py` | SIEM format serializers (Splunk HEC, Elasticsearch bulk, Datadog logs API) |

### 5.9 `app/intelligence/` — AI Quality & Safety

| File | Responsibility |
|------|---------------|
| `guardrail_engine.py` | `GuardrailEngine` — 6-layer safety pipeline with LLM judge |
| `guardrail_patterns.py` | 100+ regex/semantic patterns for injection, PII, toxicity detection |
| `guardrails.py` | Per-tenant guardrail configuration model |
| `eval_runner.py` | `EvalRunner` — 6-dimension goal scoring (completion, efficiency, accuracy, safety, coherence, SLA) |
| `eval_suite.py` | `EvalSuiteRunner` — runs eval suites across multiple goals |
| `self_optimization.py` | `SelfOptimizer` v1 — Bayesian A/B improvement suggestions |
| `self_optimizer_v2.py` | `SelfOptimizerV2` — multi-armed bandit with min_goals=5 threshold |
| `meta_agent.py` | `MetaAgentPlanner` — NL description → full agent config |
| `cost_tracker.py` | `CostTracker` — real token count tracking with provider SDK callbacks |
| `cost_optimizer.py` | Cost reduction suggestions based on usage patterns |
| `benchmarking.py` | Cross-tenant anonymized performance benchmarks |
| `explainability.py` | Step-level decision traces for explainable AI output |
| `prompt_optimizer.py` | A/B test prompt variants and select best performing |

### 5.10 `app/knowledge/` + `app/rag/` — Knowledge Management

| File | Responsibility |
|------|---------------|
| `rag/store.py` | `KnowledgeStore` — hybrid pgvector HNSW + pg_trgm trigram search |
| `rag/semantic_cache.py` | `SemanticCache` — deduplicates LLM calls by embedding similarity (1-hour TTL) |
| `knowledge/` | Ingest pipeline: 11 source types, token-aware chunking, citation tracking |

### 5.11 `app/mcp/` — Model Context Protocol

| File | Responsibility |
|------|---------------|
| `registry.py` | `MCPRegistry` — per-tenant connector registry, OAuth credential injection |
| `client.py` | `MCPClient` — HTTP client for tools/list and tool execution calls |
| `oauth.py` | `OAuthFlowManager` — PKCE OAuth 2.0 flows for connector authentication |
| `catalog.py` | Static connector catalog with category metadata |
| `capability_search.py` | Semantic search over tool capabilities for auto-routing |
| `a2a.py` | Agent-to-Agent protocol support |
| `openapi_importer.py` | Import any OpenAPI spec as an MCP connector |
| `ws_client.py` | WebSocket-based MCP client for streaming tool servers |
| `servers/` | **119 pre-built connector implementations** (one file per service) |

### 5.12 `app/memory/` — Agent Memory Systems

| File | Responsibility |
|------|---------------|
| `execution.py` | `ExecutionMemory` — per-goal context window management |
| `long_term.py` | `LongTermMemoryStore` — cross-session learnings with pgvector embedding |

### 5.13 `app/observability/` — Telemetry

| File | Responsibility |
|------|---------------|
| `health.py` | `HealthRegistry` — dependency health check aggregator |
| `logging.py` | structlog configuration, request ID injection |
| `tracing.py` | OpenTelemetry trace configuration, OTLP exporter |

### 5.14 `app/providers/` — LLM Provider Abstraction

| File | Responsibility |
|------|---------------|
| `base.py` | `LLMProvider`, `CompletionRequest`, `Message` protocol |
| `anthropic_provider.py` | Anthropic Claude (claude-3-haiku, sonnet, opus) |
| `openai_compatible.py` | OpenAI + any compatible endpoint (Groq, Together, Fireworks) |
| `gemini_provider.py` | Google Gemini (gemini-1.5-pro, gemini-flash) |
| `voyage_provider.py` | Voyage AI embeddings for semantic search |
| `fake.py` | `FakeProvider` — deterministic test/no-key fallback |
| `vault.py` | `RedisConnectorSecretStore` — encrypted credential storage |

### 5.15 `app/reliability/` — Fault Tolerance

| File | Responsibility |
|------|---------------|
| `circuit_breaker.py` | Per-connector circuit breakers (closed/open/half-open) |
| `redis_circuit_breaker.py` | Redis-backed circuit breaker state (cross-replica) |
| `bulkhead.py` | Per-tenant concurrency limits (prevents noisy-neighbour) |
| `dedup.py` | Goal deduplication (idempotency keys) |
| `distributed_lock.py` | Redis-backed distributed locking |
| `idempotency.py` | Request idempotency for API endpoints |
| `rollback.py` | `RollbackEngine` — executes compensating actions on failure |
| `tool_inverses.py` | Maps tools to their inverse compensating actions |
| `goal_lifecycle.py` | Goal state machine transitions with validation |
| `result_processor.py` | Processes and normalizes tool execution results |

### 5.16 `app/rpa/` + `app/perception/` — Browser Automation

| File | Responsibility |
|------|---------------|
| `rpa/executor.py` | Playwright command executor — 20 operations |
| `rpa/session_manager.py` | `BrowserSessionManager` — Redis-backed cross-worker sessions |
| `rpa/runner.py` | Goal ↔ browser session lifecycle management |
| `rpa/session.py` | `BrowserSession` model and state tracking |
| `rpa/tools.py` | MCP-compatible tool wrappers for browser operations |
| `rpa/artifacts.py` | Screenshot and artifact storage (MinIO) |
| `rpa/credential_injector.py` | Secure credential injection into browser sessions |
| `perception/` | Vision-based page analysis, element extraction, auto-healing locators |

### 5.17 `app/scaling/` — Celery Task Queue

| File | Responsibility |
|------|---------------|
| `celery_app.py` | Celery app configuration, per-plan queue routing, RedBeat beat schedule |
| `tasks.py` | Task definitions: `run_goal`, `fire_due_schedules`, `check_mcp_health`, maintenance tasks |

### 5.18 `app/services/` — Orchestration Layer

| File | Responsibility |
|------|---------------|
| `goal_service.py` | `GoalService` — goal lifecycle + SSE events, Redis pub/sub cross-replica delivery |
| `tenant_service.py` | `TenantService` — API key auth, tenant CRUD, plan management |
| `event_store.py` | `EventStore` — event persistence and replay |
| `goal_queue.py` | `CeleryGoalTaskQueue` — adapts GoalService to Celery tasks |
| `notification_service.py` | Email + webhook notifications for goal events |
| `llm_config_store.py` | Per-tenant LLM provider configuration |

### 5.19 `app/tenancy/` — Multi-Tenancy

| File | Responsibility |
|------|---------------|
| `middleware.py` | `TenantMiddleware` (API key auth), `SecurityHeadersMiddleware` |
| `rate_limiter.py` | Sliding-window rate limiter using Redis sorted sets |
| `context.py` | Thread-local/contextvar tenant context propagation |
| `rbac.py` | Role-based access control, custom roles, ABAC conditions |
| `store.py` | Tenant configuration store |
| `limits.py` | Per-plan resource limits (goals/hour, agents, connectors) |
| `domain_role_templates.py` | Domain-specific role templates (legal, healthcare, finance) |

### 5.20 `app/triggers/` — Scheduled Goals

| File | Responsibility |
|------|---------------|
| `nl_scheduler.py` | `NLScheduler` — converts natural language to `TriggerSpec` using LLM |
| `store.py` | `ScheduleStore` — CRUD for scheduled goals |

---

## 6. API Router Catalogue — 27 Routers

Every router is mounted in `create_app()` and produces auto-documented OpenAPI endpoints.

| Router Module | Prefix | Description |
|---------------|--------|-------------|
| `goals_router` | `/api/goals` | Goal submission (single, supervisor, parallel, debate, dry-run), status, cancel, retry, SSE stream, sub-goals |
| `agents_router` | `/api/agents` | Agent CRUD, versioning, snapshots, rollback, export, health radar |
| `analytics_router` | `/api/analytics` | Goal metrics, tool usage stats, cost trends, eval score aggregates |
| `artifacts_router` | `/api/artifacts` | MinIO-backed artifact upload/download, RPA screenshot retrieval |
| `auth_router` | `/api/auth` | API key management, SAML 2.0 endpoints (ACS, SLO), SCIM 2.0 provisioning |
| `civilization_router` | `/api/civilizations` | Civilization CRUD, governor control, blackboard access, society metrics |
| `collab_router` | `/api/collab` | Real-time collaboration WebSocket, presence, shared cursors |
| `connectors_router` | `/api/connectors` | MCP connector catalog, enable/disable, OAuth flows, health checks |
| `costs_router` | `/api/costs` | Cost ledger, per-goal breakdown, budget management, anomaly alerts |
| `compliance_router` | `/api/compliance` | GDPR export/delete, HIPAA audit, SOC 2 reports, legal holds |
| `enterprise_router` | `/api/enterprise` | White-labeling, IP allowlists, SSO config, SCIM provisioning |
| `intelligence_router` | `/api/intelligence` | Eval runs, red team tests, self-improvement experiments, benchmarks |
| `marketplace_router` | `/api/marketplace` | Template browse, deploy, publish, ratings, version history |
| `scim_router` | `/api/scim/v2` | SCIM 2.0 Users and Groups endpoints (RFC 7644) |
| `goals_router` | `/api/goals` | Already listed |
| `governance_router` | `/api/governance` | Policy CRUD, HITL approval queue, audit log query |
| `guardrails_router` | `/api/guardrails` | Guardrail configuration, test, violation log |
| `insights_router` | `/api/insights` | Pre-run cost estimation, complexity analysis, model recommendation |
| `integrations_router` | `/api/integrations` | Third-party webhooks, event subscriptions |
| `knowledge_router` | `/api/knowledge` | Knowledge base CRUD, document ingest, hybrid search |
| `memory_router` | `/api/memory` | Long-term memory CRUD, execution memory query |
| `perception_router` | `/api/perception` | Page analysis, visual element extraction, screenshot comparison |
| `replay_router` | `/api/replay` | Goal execution replay for debugging, diff between runs |
| `rpa_router` | `/api/rpa` | Browser session management, takeover mode, screenshot stream |
| `schedules_router` | `/api/schedules` | Schedule CRUD, NL schedule creation |
| `events_router` | `/api/events` | Webhook event subscriptions |
| `nl_router` | `/api/schedules/nl` | Natural language → schedule specification |
| `webhooks_router` | `/api/webhooks` | Inbound webhook handlers |
| `system_router` | `/api/system` | Health check, readiness probe, metrics (Prometheus format) |
| `templates_router` | `/api/templates` | Goal template CRUD, parameter filling, validation |
| `tenants_router` | `/api/tenants` | Tenant admin CRUD (internal/ops only) |
| `tools_router` | `/api/tools` | Tool catalog, capability search, reliability memory |
| `training_export_router` | `/api/training` | Export goal execution traces for fine-tuning |
| `workflows_router` | `/api/workflows` | Visual workflow CRUD, execution, node types |
| `a2a_router` | `/api/a2a` | Agent-to-Agent task delegation |

---

## 7. Database Architecture

### 7.1 Technology Stack

| Component | Version | Purpose |
|-----------|---------|---------|
| PostgreSQL | 16 | Primary relational database |
| pgvector | 0.7+ | Vector similarity search (HNSW index) |
| pg_trgm | built-in | Trigram similarity for hybrid text search |
| asyncpg | 0.29+ | Async Python driver |
| SQLAlchemy | 2.0 async | ORM with async session support |
| Alembic | 1.13+ | Schema migrations |
| PgBouncer | 1.25.2 | Connection pooling (:6432) |

### 7.2 Database Models (16 Domain Files)

| Model File | Primary Tables |
|------------|---------------|
| `tenant.py` | tenants, api_keys, tenant_settings |
| `agent.py` | agents, agent_versions, agent_snapshots |
| `goal.py` | goals, goal_attempts, goal_events |
| `governance.py` | audit_logs, hitl_approvals, policies |
| `mcp.py` | connectors, connector_health_snapshots |
| `knowledge.py` | knowledge_bases, documents, chunks (with embedding column) |
| `scheduling.py` | schedules, trigger_specs |
| `intelligence.py` | eval_runs, eval_scores, guardrail_violations |
| `rbac.py` | roles, permissions, role_assignments |
| `eval.py` | eval_suites, golden_tasks |
| `artifacts.py` | artifacts (MinIO keys + metadata) |
| `civilization.py` | civilizations, civilization_agents, blackboard_entries |
| `workflow.py` | workflows, workflow_nodes, workflow_edges |
| `template.py` | goal_templates, template_parameters |
| `auth.py` | agent_credentials, scope_grants, consent_records |

### 7.3 Row-Level Security (RLS)

Every table in the system has RLS enabled. The `app/db/rls.py` module provides `rls_context()`:

```python
async with rls_context(session, tenant_id="tenant-uuid"):
    result = await session.execute(select(Goal))
    # PostgreSQL SET LOCAL app.tenant_id = 'tenant-uuid'
    # RLS policy: WHERE tenant_id = current_setting('app.tenant_id')
```

The GUC `app.tenant_id` is set at the beginning of each database transaction using `SET LOCAL`, which means it is scoped to that transaction and cannot leak across connections from the pool.

RLS policies are defined in Alembic migrations as PostgreSQL policy objects:

```sql
CREATE POLICY tenant_isolation ON goals
    USING (tenant_id::text = current_setting('app.tenant_id', true));
```

This provides **database-enforced tenant isolation** — even if application code has a bug that omits the tenant filter, the RLS policy at the database level ensures cross-tenant data leakage is impossible.

### 7.4 Migration History (64 Revisions)

The migration chain represents the complete evolutionary history of the schema:

```
0001_baseline          → Core tables (tenants, api_keys)
0002_tenancy           → Multi-tenancy tables
0003_agents            → Agent configuration
0004_goals             → Goal lifecycle tables
0005_governance        → Audit + cost tables
0006_mcp               → Connector registry
0007_scheduling        → Cron schedule tables
0008_knowledge         → Vector knowledge store
0009_intelligence      → Eval + guardrail tables
0010_goal_agent_binding
0011_goal_events_checkpoints
...
0045_civilization      → Multi-agent civilization tables
0046_workflows         → Visual workflow builder
0047_notification_channels
0048_goal_templates
...
0065_loop_engineering  → Loop control configuration
0066_tenant_settings   → Per-tenant feature flags
0067_consent_records_v2 → GDPR consent management
```

### 7.5 Vector Search Architecture

The knowledge store uses a hybrid search strategy:

```
Query: "What is the return policy for damaged goods?"
         │
         ├─► Embedding: Voyage AI text-embedding-3 (1024 dims)
         │       └─► pgvector HNSW: cosine similarity top-k=20
         │
         └─► Trigram: pg_trgm similarity_threshold=0.3
                 └─► exact phrase + word n-gram matches
         
Results merged by: Reciprocal Rank Fusion (RRF)
         └─► Final top-k=5 chunks with citations
```

The HNSW index parameters are tuned for recall vs. performance:

```sql
CREATE INDEX ON document_chunks 
USING hnsw (embedding vector_cosine_ops) 
WITH (m = 16, ef_construction = 64);
```

At query time: `SET hnsw.ef_search = 40` for higher recall in production.

---

## 8. Message Queue and Background Workers

### 8.1 Celery Configuration

```python
# Per-plan queue routing — enterprise gets dedicated workers
PLAN_QUEUE_MAP = {
    "free":         "goals.free",
    "starter":      "goals.starter",
    "professional": "goals.professional",
    "enterprise":   "goals.enterprise",
}

# Additional queues
# goals_dlq    → Dead-letter queue for failed goals
# schedules    → Scheduled goal execution
# maintenance  → Periodic tasks (health checks, cleanup, etc.)
```

### 8.2 Worker Configuration

| Setting | Value | Rationale |
|---------|-------|-----------|
| `task_acks_late = True` | Ack after completion | Prevents message loss on worker crash |
| `task_reject_on_worker_lost = True` | Requeue on crash | Ensures at-least-once delivery |
| `worker_max_tasks_per_child = 100` | 100 tasks | Prevents memory leaks in long-running workers |
| `worker_max_memory_per_child = 500_000` KB | 500 MB | Guards against unbounded memory growth |
| `worker_prefetch_multiplier = 1` | No prefetch | Fair scheduling, prevents starvation |
| `task_default_retry_delay = 30` | 30 seconds | Exponential backoff base |

### 8.3 Beat Schedule (RedBeat)

| Task | Schedule | Purpose |
|------|---------|---------|
| `check_mcp_health` | Every 30s | Connector circuit breaker state updates |
| `fire_due_schedules` | Every 60s | Trigger scheduled goals |
| `detect_stuck_goals` | Every 5m | Re-queue goals in PLANNING state > 10m |
| `execute_retention_policy` | Daily 3 AM | GDPR retention enforcement |
| `expire_hitl_approvals` | Every 15m | SLA enforcement for pending approvals |
| `civilization_tick` | Every 30s | Run civilization cycle (governor evaluation) |
| `record_queue_depths` | Every 60s | Prometheus queue depth metrics |

---

## 9. Middleware Stack

Request processing flows through this exact middleware stack from outermost to innermost:

```
Incoming HTTP Request
         │
         ▼
1. CORSMiddleware (FastAPI built-in)
   - Reads CORS_ORIGINS from Settings
   - Handles preflight OPTIONS requests
   - Sets Access-Control-Allow-Origin header
         │
         ▼
2. SecurityHeadersMiddleware (app/tenancy/middleware.py)
   - X-Content-Type-Options: nosniff
   - X-Frame-Options: DENY
   - X-XSS-Protection: 1; mode=block
   - Strict-Transport-Security: max-age=31536000
   - Content-Security-Policy (configurable)
   - Referrer-Policy: strict-origin-when-cross-origin
         │
         ▼
3. ScopeEnforcementMiddleware (app/auth/scope_enforcement.py)
   - Reads X-API-Key header
   - Resolves required scopes via ENDPOINT_SCOPES dict
   - Checks tenant's granted scopes from permission cache
   - Returns 403 if scope missing
         │
         ▼
4. TenantMiddleware (app/tenancy/middleware.py)
   - Extracts API key from X-API-Key header
   - Checks Redis cache (5 min TTL)
   - Falls back to Postgres lookup on cache miss
   - Sets request.state.tenant_id, tenant_plan, tenant
   - Enforces IP allowlist (CIDR check) if configured
         │
         ▼
5. SlidingWindowRateLimiter (app/tenancy/rate_limiter.py)
   - Redis ZSET per (tenant_id, endpoint)
   - Window: 60 seconds, limit: per-plan
   - Returns 429 with Retry-After header on limit
         │
         ▼
Route Handler
```

---

## 10. Infrastructure Services

### 10.1 Development Stack (docker-compose.yml)

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `postgres` | pgvector/pgvector:pg16 | 5432 | Primary database with pgvector |
| `redis` | redis:7-alpine | 6379 | Broker, cache, pub/sub, checkpoints |
| `pgbouncer` | edoburu/pgbouncer:v1.25.2-p0 | 6432 | Connection pooling |
| `backend` | Custom Dockerfile | 8000 | FastAPI API server |
| `keycloak-db` | postgres:16-alpine | — | Keycloak's own Postgres |
| `keycloak` | quay.io/keycloak/keycloak:24.0 | 8080 | SAML/OIDC/SCIM IdP |
| `worker` | Custom Dockerfile | — | Celery goal worker |
| `beat` | Custom Dockerfile | — | Celery beat scheduler |
| `minio` | minio/minio:latest | 9000/9001 | S3-compatible artifact storage |
| `mailpit` | axllent/mailpit:latest | 8025 | Dev email server (SMTP trap) |
| `otel-collector` | otel/opentelemetry-collector-contrib:0.103.0 | 4317 | OTel trace aggregation |
| `jaeger` | jaegertracing/all-in-one:1.58 | 16686 | Distributed trace UI |
| `frontend` | Custom Dockerfile | 80 | React production build (Nginx) |
| `searxng` | searxng/searxng:latest | 8080 | Private meta-search engine |
| `prometheus` | prom/prometheus:v2.51.0 | 9090 | Metrics scraping |
| `grafana` | grafana/grafana:10.4.0 | 3000 | Metrics dashboards |

**Total: 16 containerized services in the development stack.**

All services have health checks with readiness probes. The `backend` service depends on `postgres` (healthy), `redis` (healthy), `pgbouncer` (healthy), and `keycloak` (started).

### 10.2 Minimum Required Services

For development of most features, only two services are required:

```bash
colima start
docker-compose -f infra/docker-compose.yml up -d postgres redis
# Backend starts with in-memory fallbacks for non-critical services
```

---

## 11. Two-Phase Service Wiring Pattern

This is the most important architectural pattern to understand. It explains why tests behave differently from production and why service initialization is split into two phases.

### Phase 1: In-Memory Construction (`create_app()`)

When `create_app()` is called, all services are constructed with **in-memory/fake dependencies**:

```python
def create_app(settings=None, manage_pools=False, ...):
    app = FastAPI(lifespan=lifespan)
    
    # Phase 1: In-memory services
    app.state.tenant_service = TenantService(redis=_FakeRedis())
    app.state.goal_service = GoalService(redis=_FakeRedis())
    app.state.mcp_registry = MCPRegistry(redis=_FakeRedis())
    app.state.knowledge_store = KnowledgeStore(pool=None)
    app.state.cost_controller = CostController(redis=_FakeRedis())
    app.state.policy_engine = PolicyEngine(redis=_FakeRedis())
    # ... all 22 services get in-memory versions
    
    # Include all routers (they read from app.state dynamically)
    app.include_router(goals_router)
    app.include_router(agents_router)
    # ... 25 more routers
```

**Why this matters for tests:** Tests call `create_app()` without `manage_pools=True`, so they get the in-memory path. Services work without Docker dependencies. This enables fast, isolated unit tests.

### Phase 2: DB/Redis Upgrade (`lifespan`)

When `manage_pools=True` (production), the FastAPI `lifespan` context manager upgrades services:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Only runs when manage_pools=True
    pools = ConnectionPools(settings)
    await pools.start()  # asyncpg pool + redis.asyncio pool
    
    # Upgrade in-memory services to DB-backed
    app.state.tenant_service = TenantService(
        pool=pools.postgres, redis=pools.redis
    )
    await app.state.tenant_service.sync_from_db()  # hydrate from Postgres
    
    app.state.goal_service = GoalService(
        pool=pools.postgres, redis=pools.redis
    )
    
    # Wire semantic cache with real embedder
    app.state.semantic_cache = SemanticCache(
        redis=pools.redis, embedder=embedder
    )
    
    # ... upgrade all 22 services
    
    yield  # Server accepts requests
    
    await pools.stop()  # Graceful shutdown
```

**Why lifespan (not startup events):** The lifespan async context manager guarantees cleanup on both normal shutdown and signal interruption. `startup`/`shutdown` events are deprecated in newer FastAPI versions.

**Dependency injection after the swap:** Route handlers use FastAPI `Depends` to read from `app.state`:

```python
def get_goal_service(request: Request) -> GoalService:
    return request.app.state.goal_service
    # This reads whatever is currently on app.state — works for both phases
```

Because `app.state` is mutable and the lifespan swaps the reference, the dependency injection sees the upgraded service without any code changes in the route handlers.

---

## 12. Observability Stack

### 12.1 Structured Logging (structlog)

All backend code uses structlog with JSON output:

```python
logger = get_logger(__name__)
logger.info("goal_submitted", goal_id=str(goal.id), tenant_id=tenant_id, 
            model=agent.model, estimated_cost=estimate)
```

Every log entry includes:
- `timestamp` (ISO 8601)
- `level` (debug/info/warning/error/critical)
- `logger` (module name)
- `request_id` (injected by TenantMiddleware)
- `tenant_id` (from request context)
- All structured key-value pairs passed by the logger call

### 12.2 OpenTelemetry Traces

The `configure_tracing()` call in `create_app()` sets up OTel with:
- OTLP exporter to `otel-collector:4317`
- Auto-instrumentation for FastAPI, asyncpg, httpx, and Redis
- Custom spans for agent loop phases (plan, execute, verify)
- Trace correlation IDs propagated in request/response headers

Traces flow: FastAPI → OTel Collector → Jaeger (dev) or vendor (prod).

### 12.3 Prometheus Metrics

The `/api/system/metrics` endpoint exposes Prometheus-format metrics:

| Metric | Type | Description |
|--------|------|-------------|
| `agentverse_goals_total` | Counter | Goals submitted by status/plan |
| `agentverse_goal_duration_seconds` | Histogram | Goal execution time |
| `agentverse_tool_calls_total` | Counter | Tool calls by connector/status |
| `agentverse_llm_tokens_total` | Counter | Token usage by model/role |
| `agentverse_cost_usd_total` | Counter | Cumulative cost by tenant |
| `agentverse_queue_depth` | Gauge | Celery queue depth by queue name |
| `agentverse_hitl_pending` | Gauge | Pending HITL approvals |
| `agentverse_guardrail_violations_total` | Counter | Violations by layer/pattern |
| `agentverse_cache_hits_total` | Counter | Semantic cache hit rate |
| `agentverse_rls_context_duration_seconds` | Histogram | RLS context setup overhead |

---

## 13. Configuration System

The `app/core/config.py` `Settings` class inherits from Pydantic `BaseSettings` and loads all configuration from environment variables. Key configuration groups:

### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://agentverse:agentverse@localhost/agentverse` | asyncpg DSN |
| `DATABASE_POOL_SIZE` | 20 | asyncpg pool size |
| `DATABASE_MAX_OVERFLOW` | 10 | Max connections above pool_size |

### Redis

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |

### LLM Providers (resolved in priority order)

| Variable | Provider Activated |
|----------|-------------------|
| `ANTHROPIC_API_KEY` | Anthropic Claude (haiku/sonnet/opus) |
| `OPENAI_API_KEY` | OpenAI GPT-4 or any compatible endpoint |
| `OPENAI_API_BASE` | Custom endpoint for OpenAI-compatible providers |
| `GOOGLE_API_KEY` | Google Gemini |
| `VOYAGE_API_KEY` | Voyage AI embeddings |

### Application

| Variable | Default | Description |
|----------|---------|-------------|
| `ENVIRONMENT` | `development` | `development` or `production` |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allowed origins |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | None | OTel collector endpoint |

**Production safety:** The backend refuses to start in production with default `agentverse:agentverse@` DB credentials, preventing accidental deployment with insecure defaults.

---

## 14. Security Architecture Summary

Security is enforced at multiple independent layers — defense in depth:

```
Layer 1: Network      → TLS everywhere, HSTS headers, CSP
Layer 2: Transport    → IP allowlist (CIDR), rate limiting (sliding window)  
Layer 3: Auth         → API key (NIST 800-131A, 256-bit), SAML 2.0, OIDC
Layer 4: Authorization → Scope enforcement (30+ scopes), RBAC (5 roles + custom)
Layer 5: Application  → PolicyEngine (glob + semantic + cost + rate rules)
Layer 6: AI Safety    → GuardrailEngine (6 layers, 100+ patterns, LLM judge)
Layer 7: Database     → Row-Level Security on ALL tables
Layer 8: Audit        → SHA-256 chained append-only audit log, SIEM integration
```

No single layer failure can expose another tenant's data or allow policy bypass. The layered defense ensures that even a compromised API key only grants access to the scopes associated with that key, filtered through RLS at the database level.

---

*This document covers the complete platform architecture. For agent execution internals, see [02-agent-execution-engine.md](./02-agent-execution-engine.md). For the complete feature catalogue, see [03-features-catalogue.md](./03-features-catalogue.md).*
