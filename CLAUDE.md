# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**AgentVerse** — a vendor-agnostic, multi-tenant operating system for autonomous AI agents.
An agent receives a natural-language goal, plans its own execution, calls real-world tools
via MCP, verifies the result, and replans on failure — with **zero hardcoded workflows**.

This is a **monorepo of independently deployable projects**. They share a git root but
have separate toolchains and build/test commands:

| Directory | Stack | Role |
|-----------|-------|------|
| `agent-verse-backend/` | Python 3.12 · FastAPI · LangGraph · Celery · Postgres+pgvector | Source of truth. Publishes the OpenAPI contract. |
| `agent-verse-frontend/` | React 19 · Vite · TanStack Query · Zustand · Tailwind | Consumes backend over HTTP / SSE / WebSocket. |
| `agent-verse-github-action/` | Python entrypoint in Docker | GitHub Action to submit/await a goal from CI. |

> The Python and TypeScript SDKs (`agent-verse-sdk-python/`, `agent-verse-sdk-typescript/`)
> were removed in `cabce1238` (2026-08-17). Backend tests that import `agentverse` skip.

## Local environment quirks (this machine)

These are non-obvious and have bitten previous sessions — read before running anything:

- **Python:** system Python is 3.9; the backend pins **3.12 via `uv`**. Always prefix backend
  Python commands with `uv run` (e.g. `uv run pytest`, `uv run mypy app`).
- **Docker runs via colima**, which is **not** auto-started — run `colima start` first. The
  `docker compose` v2 plugin is absent; use the standalone **`docker-compose`** binary.
- **Integration tests (testcontainers)** need these env vars set:
  `DOCKER_HOST="unix:///Users/harsh/.colima/default/docker.sock"` and
  `TESTCONTAINERS_RYUK_DISABLED=true`.
- **Tests can never reach the live local Postgres/Redis.** `tests/conftest.py` forces
  `DATABASE_URL`/`REDIS_URL` to an unreachable address before any app import and refuses
  socket connects to loopback `:5432`/`:6432`/`:6379` (earlier tests silently wrote rows into
  the dev DB through the app defaults). Tests that need a real DB/Redis use the
  testcontainers fixtures `pg_url` (migrated via alembic), `redis_url` and `test_backends`
  (points the global engine/`get_settings()` at them) and carry the `integration` marker;
  unit tests use fakes (`tests/_rls_recorder.RlsRecordingDb`, `in_memory_goal_lock`).
  Opt out only for disposable infra (e.g. a CI service container) with
  `AGENTVERSE_TESTS_ALLOW_LIVE_INFRA=1` — never against the dev stack.
- **`httpx2`** is a dev dependency because Starlette's `TestClient` requires it; plain httpx
  raises a deprecation that `filterwarnings=error` turns into a test failure.
- **pytest treats warnings as errors** (`filterwarnings = ["error"]`); only specific
  testcontainers/asyncpg deprecations are scoped-ignored in `pyproject.toml`.
- **Two runtimes share one Redis/Postgres.** A launchd job
  (`~/Library/LaunchAgents/com.local.agentverse.runforever.plist`) runs
  `scripts/run_forever.py` — a local API + Celery worker + beat from the repo `.venv` —
  against the same Redis/Postgres as the docker compose stack (project
  `agentverse-backend`). To keep ONE worker fleet and ONE beat, `run_forever.py` does not
  start its own worker/beat while the compose stack's `worker`/`*-worker`/`beat` containers
  are running (checked with `docker ps` every 60 s; its worker/beat stop if compose's come up
  later, and start if they go away) and logs why. Override with `--force-workers` or
  `AGENTVERSE_FORCE_WORKERS=1`; `AGENTVERSE_COMPOSE_PROJECT` changes the project name. Logs:
  `~/.local/state/agentverse_run_forever/run_forever.log`. Code changes reach the launchd
  runtime only after it is restarted (`launchctl kickstart -k gui/$(id -u)/com.local.agentverse.runforever`).

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
- **RLS only binds a least-privilege role.** A SUPERUSER or BYPASSRLS connection ignores every
  policy (the local stack once ran as the superuser `agentverse`, and `GET /runs` served every
  tenant's runs). So: (1) every tenant-scoped query ALSO carries an explicit
  `tenant_id = :tid` predicate — never rely on the GUC alone (regression net:
  `tests/e2e_full/test_cross_tenant_list_sweep_e2e.py`, run on the superuser harness); and
  (2) three DB roles/DSNs: `DATABASE_URL` = app role (`agentverse_app` locally, NOSUPERUSER,
  NOBYPASSRLS, DML only), `MAINTENANCE_DATABASE_URL` = BYPASSRLS role for cross-tenant system
  jobs (`get_system_session_factory()` + `system_session`), `MIGRATION_DATABASE_URL` = schema
  owner alembic runs as. After every `alembic upgrade`, `app/db/migrations/env.py` creates or
  repairs `APP_DB_USER` with grants (`app/db/app_role.py`, idempotent, no-op when unset). In
  compose, the one-shot `db-migrate` service does this before any app service starts (fresh
  volumes are also covered by `infra/postgres/init/10-agentverse-app-role.sh`), and pgbouncer
  learns the app role via `infra/pgbouncer/add-app-user.sh`. `agentverse` remains only the
  owner/maintenance role. Change the app password with `APP_DB_PASSWORD` (shell or
  `infra/.env`); production refuses the dev default.

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
