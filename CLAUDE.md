# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**AgentVerse** — a vendor-agnostic, multi-tenant operating system for autonomous AI agents.
An agent receives a natural-language goal, plans its own execution, calls real-world tools
via MCP, verifies the result, and replans on failure — with **zero hardcoded workflows**.

This is a **monorepo of five independently deployable projects**. They share a git root but
have separate toolchains and build/test commands:

| Directory | Stack | Role |
|-----------|-------|------|
| `agent-verse-backend/` | Python 3.12 · FastAPI · LangGraph · Celery · Postgres+pgvector | Source of truth. Publishes the OpenAPI contract. |
| `agent-verse-frontend/` | React 19 · Vite · TanStack Query · Zustand · Tailwind | Consumes backend over HTTP / SSE / WebSocket. |
| `agent-verse-sdk-python/` | Python 3.11+ · httpx · pydantic | Official Python client + CLI (`agentverse`). |
| `agent-verse-sdk-typescript/` | TypeScript · vitest (zero runtime deps) | Official TS/JS client. |
| `agent-verse-github-action/` | Python entrypoint in Docker | GitHub Action to submit/await a goal from CI. |

> The backend's dev dependencies include `agentverse-sdk` via a local path source
> (`[tool.uv.sources]` in `agent-verse-backend/pyproject.toml`) so backend tests exercise the
> real SDK. Changes to the Python SDK are picked up by the backend after `uv sync`.

## Local environment quirks (this machine)

These are non-obvious and have bitten previous sessions — read before running anything:

- **Python:** system Python is 3.9; the backend pins **3.12 via `uv`**. Always prefix backend
  Python commands with `uv run` (e.g. `uv run pytest`, `uv run mypy app`).
- **Docker runs via colima**, which is **not** auto-started — run `colima start` first. The
  `docker compose` v2 plugin is absent; use the standalone **`docker-compose`** binary.
- **Integration tests (testcontainers)** need these env vars set:
  `DOCKER_HOST="unix:///Users/harsh.kumar01/.colima/default/docker.sock"` and
  `TESTCONTAINERS_RYUK_DISABLED=true`.
- **`httpx2`** is a dev dependency because Starlette's `TestClient` requires it; plain httpx
  raises a deprecation that `filterwarnings=error` turns into a test failure.
- **pytest treats warnings as errors** (`filterwarnings = ["error"]`); only specific
  testcontainers/asyncpg deprecations are scoped-ignored in `pyproject.toml`.

## Common commands

### Backend (`agent-verse-backend/`)
```bash
uv sync                                   # install deps into .venv (pinned via uv.lock)
uv run uvicorn app.main:app --reload      # run the API locally (default :8000)
uv run pytest                             # full test suite (with coverage)
uv run pytest tests/agent/test_loop.py    # a single test file
uv run pytest tests/agent/test_loop.py::test_name   # a single test
uv run pytest -m integration              # integration tests (needs Docker/testcontainers)
uv run pytest -m "not slow"               # skip tests that hit real LLM providers
uv run ruff check .                       # lint  (ruff config in pyproject.toml)
uv run mypy app                           # type-check (strict mode)
uv run alembic upgrade head               # apply DB migrations
uv run alembic revision --autogenerate -m "msg"   # create a migration
uv run agentverse --help                  # backend admin CLI (app.cli.main)
uv run python scripts/export_openapi.py   # regenerate the OpenAPI contract
```

Local infra (Postgres+pgvector and Redis are the minimum for most work):
```bash
colima start
docker-compose -f infra/docker-compose.yml up -d postgres redis
```
The full stack (`infra/docker-compose.yml`) also includes pgbouncer, keycloak, celery
worker + beat, minio, mailpit, otel-collector, jaeger, searxng, and the frontend.

### Frontend (`agent-verse-frontend/`)
```bash
npm run dev          # Vite dev server
npm run build        # tsc + vite build
npm run lint         # eslint
npm run typecheck    # tsc --noEmit
npm run test         # vitest (unit/component, src/)
npm run test:e2e     # Playwright e2e (e2e/)
```

### SDKs
```bash
# Python SDK (agent-verse-sdk-python/)
uv run pytest
# TypeScript SDK (agent-verse-sdk-typescript/)
npm run build && npm test
```

CI (`agent-verse-backend/.github/workflows/ci.yml`) runs ruff, mypy, and pytest.

## Backend architecture (the big picture)

The backend is the part that requires reading multiple files to understand. Start here.

### Application assembly: `app/main.py` → `create_app()`
A single factory wires every service onto `app.state` and includes ~25 routers. Two things
are critical to understand:

1. **Two-phase service wiring.** `create_app()` first constructs **in-memory** versions of
   every service (`TenantService`, `GoalService`, `AgentStore`, `KnowledgeStore`,
   `MCPRegistry`, rate limiter, etc.). Then the FastAPI **`lifespan`** (only when
   `manage_pools=True`) starts `ConnectionPools`, and **swaps the in-memory services for
   DB/Redis-backed ones**, re-hydrating state from Postgres via `sync_from_db()`. Auth, SSE,
   cost control, and the MCP client are all re-wired here. When debugging "works in tests but
   not in prod" (or vice versa), this swap is usually why — tests typically build the app
   without `manage_pools`, getting the in-memory path.

2. **Dependency resolution reads from `app.state`** dynamically (e.g. the tenant key
   resolver), so the lifespan swap takes effect without breaking already-registered
   middleware.

### The agent loop: `app/agent/`
This is the core product. `app/agent/loop.py` and `graph.py` implement a **LangGraph**
state machine:
```
initialize → plan → execute → verify → (complete | replan | max_iterations_exceeded)
```
- **Three distinct LLM roles**, each with its own provider/prompt so they can be tuned
  independently: **Planner** (goal+context → steps), **Executor** (one step → tool calls),
  **Verifier** (step+result → success/failure). Prompts live in `app/agent/prompts.py`.
- State is `AgentState` (`app/agent/state.py`), checkpointable. With Redis available the
  lifespan wires an `AsyncRedisSaver` LangGraph checkpointer so agent state survives across
  replicas; it falls back to sync `RedisSaver` then `MemorySaver`.
- High-risk steps (keywords like `deploy`, `delete`, `prod`) route through the **HITL
  gateway** for human approval before executing.
- Related agent modules: `router.py` (auto-routes a goal to an agent when no `agent_id` is
  given), `supervisor.py` / `debate.py` (multi-agent patterns), `model_router.py` (picks a
  model per task type), `workflow_planner.py` / `workflow_executor.py`, `goal_tree.py`,
  `tool_risk.py`, `sanitization.py`.

### Cross-cutting subsystems (each is an `app/<name>/` package)
- **`providers/`** — vendor-agnostic LLM/embedding abstraction (`base.py`: `LLMProvider`,
  `CompletionRequest`, `Message`). Real providers (`anthropic_provider`, `openai_compatible`,
  `voyage_provider`, `gemini_provider`) are resolved from env keys at startup; `FakeProvider`
  is the deterministic test/no-key fallback. `vault.py` holds the encrypted credential store.
- **`mcp/`** — Model Context Protocol: per-tenant connector `registry.py`, HTTP `client.py`
  (tools/list + tool execution), PKCE `oauth.py` flows.
- **`tenancy/`** — multi-tenancy: `TenantMiddleware` (API-key auth), `SecurityHeadersMiddleware`,
  sliding-window rate limiter. Enforced at the DB layer too (see RLS below).
- **`governance/`** — `audit.py` (append-only trail), `cost.py` (per-goal/per-tenant budgets,
  Redis-backed for cross-replica accuracy), `hitl.py` (approval queue), `policies.py` (tool
  policy engine with Redis pub/sub propagation across replicas), `permissions.py`.
- **`reliability/`** — circuit breakers, deduplication, bulkhead (per-tenant concurrency),
  rollback engine + `tool_inverses.py` (compensating actions for executed tools).
- **`rag/`** + **`knowledge/`** — `KnowledgeStore` (hybrid pgvector + trigram search),
  `SemanticCache` (dedupes LLM calls by embedding).
- **`memory/`** — `ExecutionMemory` (per-goal) and `LongTermMemoryStore` (cross-session
  learnings).
- **`intelligence/`** — `EvalRunner` / `EvalSuiteRunner` (multi-dimension goal scoring),
  `MetaAgentPlanner` (NL → agent config), `SelfOptimizer`, `prompt_optimizer.py`.
- **`enterprise/`** — `compliance.py` (GDPR/SOC2/PCI), `simulation.py` (mock-tool sandbox),
  `red_team.py`, `marketplace.py` (template gallery).
- **`rpa/`** + **`perception/`** — browser automation (Playwright), page analysis, vision via
  the embedder when it supports it, artifact storage.
- **`triggers/`** — schedules: `NLScheduler` (NL → `TriggerSpec`), `ScheduleStore`, croniter.
- **`scaling/`** — Celery: `celery_app.py` defines **per-plan queue routing**
  (`goals.free`/`starter`/`professional`/`enterprise`) so enterprise tenants avoid
  noisy-neighbour effects; `tasks.py` holds the goal/schedule/maintenance tasks.
- **`services/`** — orchestration glue: `GoalService` (goal lifecycle + SSE, Redis pub/sub for
  cross-replica delivery), `tenant_service.py`, `event_store.py`, `goal_queue.py`,
  `notification_service.py`, `llm_config_store.py`.

### Persistence & multi-tenancy
- **SQLAlchemy 2 async + asyncpg**, models in `app/db/models/` (one file per domain).
- **Migrations via Alembic** in `app/db/migrations/` (~44 revisions). Never edit a deployed
  migration; add a new one.
- **Row-Level Security** (`app/db/rls.py`): `rls_context()` sets the `app.tenant_id` Postgres
  GUC via `SET LOCAL` so RLS policies filter rows per tenant. Tenant isolation is enforced at
  the database, not just in app code.

### Configuration
`app/core/config.py` — typed `Settings` loaded from env (12-factor). Key vars: `DATABASE_URL`
(asyncpg DSN), `REDIS_URL`, `ENVIRONMENT` (`development`/`production`), `CORS_ORIGINS`
(comma-separated), `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `VOYAGE_API_KEY` / `GOOGLE_API_KEY`
(select LLM + embedding providers at startup), `OTEL_EXPORTER_OTLP_ENDPOINT`. Production
refuses the default `agentverse:agentverse@` DB credentials.

## Frontend architecture (`agent-verse-frontend/`)
- Feature-sliced under `src/features/` (one folder per domain: `goals`, `agents`, `governance`,
  `rpa`, `marketplace`, `workflow-builder`, `observability`, …).
- Backend transport lives in `src/lib/`: `api/client.ts` (HTTP), `sse/useGoalStream.ts`
  (Server-Sent Events for live goal execution), `ws/useCollabSocket.ts` (WebSocket for
  collaboration). Server state via TanStack Query; local state via Zustand.

## Conventions
- **Backend lint/type config is in `pyproject.toml`**: ruff line-length 100, target py312,
  rule set `E,F,I,N,UP,B,A,C4,SIM,RUF`; mypy `strict` with the pydantic plugin. Match the
  existing style — no separate config files.
- New backend service classes are typically constructed in `create_app()` and bound to
  `app.state`, then DB/Redis-upgraded in `lifespan`. Follow that pattern when adding services.
- Tests mirror the source layout under `tests/<package>/`. Markers: `integration` (real
  Redis/Postgres via testcontainers) and `slow` (real LLM calls — opt-in).
- Phased roadmap and the full component architecture live in `docs/superpowers/specs/` and
  `docs/superpowers/plans/`.
