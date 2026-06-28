# AgentVerse

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688.svg)](https://fastapi.tiangolo.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-agentic-orange.svg)](https://langchain-ai.github.io/langgraph/)
[![React 19](https://img.shields.io/badge/React-19-61dafb.svg)](https://react.dev)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178c6.svg)](https://www.typescriptlang.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A vendor-agnostic, multi-tenant operating system for autonomous AI agents. An agent receives a natural-language goal, plans its own execution, calls real-world tools via MCP, verifies the result, and replans on failure — with zero hardcoded workflows. The platform is multi-tenant at every layer (API auth, row-level security, cost tracking, rate limiting) and ships a full observability stack out of the box.

---

## Architecture Overview

This is a monorepo of five independently deployable projects sharing a single git root:

| Directory | Stack | Role |
|-----------|-------|------|
| `agent-verse-backend/` | Python 3.12 · FastAPI · LangGraph · Celery · Postgres+pgvector | Source of truth. Publishes the OpenAPI contract. |
| `agent-verse-frontend/` | React 19 · Vite · TanStack Query · Zustand · Tailwind | Consumes backend over HTTP / SSE / WebSocket. |
| `agent-verse-sdk-python/` | Python 3.11+ · httpx · pydantic | Official Python client + CLI (`agentverse`). |
| `agent-verse-sdk-typescript/` | TypeScript · vitest (zero runtime deps) | Official TS/JS client. |
| `agent-verse-github-action/` | Python entrypoint in Docker | GitHub Action to submit/await a goal from CI. |

---

## Quick Start (5 minutes)

### Prerequisites

- **Docker** (via [colima](https://github.com/abiosoft/colima) on macOS, or Docker Desktop)
- **Node.js 20+** (frontend dev only)
- **Python 3.12 + [uv](https://docs.astral.sh/uv/)** (backend dev only)
- At least one LLM API key: `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`

### 1 — Start the container runtime (macOS)

```bash
colima start
```

### 2 — Set LLM credentials

```bash
cp agent-verse-backend/.env.example agent-verse-backend/.env
# Edit .env and set at least one of:
#   ANTHROPIC_API_KEY=sk-ant-...
#   OPENAI_API_KEY=sk-...
```

### 3 — Bring up the full stack

```bash
docker-compose -f agent-verse-backend/infra/docker-compose.yml up -d
```

This starts Postgres, Redis, the backend API, a Celery worker, Celery beat, Keycloak, MinIO, Mailpit, OpenTelemetry collector, Jaeger, SearXNG, Prometheus, and Grafana.

For a minimal dev stack (Postgres + Redis only):

```bash
docker-compose -f agent-verse-backend/infra/docker-compose.yml up -d postgres redis
```

### 4 — Access the frontend

Open [http://localhost:5173](http://localhost:5173) (served by the `frontend` container, or run `npm run dev` locally — see [Frontend Dev Commands](#frontend-dev-commands)).

Sign up at `/signup` to create a tenant and receive an API key.

---

## Services Map

| Service | Port | Description |
|---------|------|-------------|
| `backend` | 8000 | FastAPI REST + SSE + WebSocket |
| `frontend` | 5173 | React SPA (nginx in container) |
| `postgres` | 5432 | Primary database (pgvector enabled) |
| `pgbouncer` | 6432 | Connection pool proxy |
| `redis` | 6379 | Celery broker, pub/sub, cache |
| `keycloak` | 8080 | SSO / OIDC identity provider |
| `minio` | 9000 / 9001 | Object storage (artifacts) |
| `mailpit` | 1025 (SMTP) / 8025 (UI) | Dev email catcher |
| `otel-collector` | 4317 (gRPC) / 4318 (HTTP) | OpenTelemetry ingest |
| `jaeger` | 16686 | Distributed tracing UI |
| `searxng` | 8081 | Privacy-respecting web search |
| `prometheus` | 9090 | Metrics collection |
| `grafana` | 3001 | Metrics dashboards |

---

## Backend Dev Commands

All commands run from `agent-verse-backend/` with `uv run` (Python 3.12 managed by uv):

```bash
# Install dependencies
uv sync

# Start the API locally (hot-reload)
uv run uvicorn app.main:app --reload

# Run the full test suite
uv run pytest

# Run a single test file
uv run pytest tests/agent/test_loop.py

# Integration tests (requires Docker / testcontainers)
DOCKER_HOST="unix:///Users/<you>/.colima/default/docker.sock" \
TESTCONTAINERS_RYUK_DISABLED=true \
uv run pytest -m integration

# Lint
uv run ruff check .

# Type-check (strict)
uv run mypy app

# Apply DB migrations
uv run alembic upgrade head

# Create a new migration
uv run alembic revision --autogenerate -m "describe change"

# Regenerate OpenAPI contract
uv run python scripts/export_openapi.py
```

---

## Frontend Dev Commands

All commands run from `agent-verse-frontend/`:

```bash
# Start Vite dev server (localhost:5173)
npm run dev

# Type-check
npm run typecheck

# Production build (tsc + vite)
npm run build

# Lint
npm run lint

# Unit / component tests (vitest)
npm run test

# End-to-end tests (Playwright)
npm run test:e2e
```

---

## Key Features

- **Autonomous agent loop** — LangGraph state machine: plan → execute → verify → replan. Zero hardcoded workflows.
- **Three distinct LLM roles** — independent Planner, Executor, and Verifier models, each tunable separately.
- **MCP (Model Context Protocol)** — per-tenant connector registry; dynamic tool discovery; PKCE OAuth flows.
- **Human-in-the-loop (HITL)** — high-risk steps (deploy, delete, prod keywords) route to an approval queue before executing.
- **Multi-tenant row-level security** — PostgreSQL RLS enforces tenant isolation at the database layer, not just app code.
- **Vendor-agnostic LLM providers** — Anthropic, OpenAI-compatible, Google Gemini, Voyage; FakeProvider for tests.
- **Cost control** — per-goal and per-tenant budget enforcement backed by Redis for cross-replica accuracy.
- **Semantic cache** — deduplicates LLM calls by embedding similarity to reduce cost.
- **RAG / knowledge store** — hybrid pgvector + trigram search; per-collection ingestion and retrieval.
- **Long-term memory** — cross-session learnings stored and recalled by the agent.
- **Workflow builder** — visual DAG editor (frontend) backed by a versioned workflow engine (backend).
- **Celery task queues** — per-plan queue routing (free / starter / professional / enterprise) to prevent noisy-neighbour effects.
- **SSO via Keycloak** — OIDC PKCE flow; JWT validated by backend middleware; API-key auth also supported.
- **Compliance** — GDPR export, legal hold, consent records, SOC2/PCI policy engine.
- **Observability** — OpenTelemetry traces → Jaeger; Prometheus metrics → Grafana; append-only audit trail.
- **Eval framework** — multi-dimension goal scoring with eval suites; self-optimiser and prompt optimiser.
- **RPA / browser automation** — Playwright-backed perception, screenshot, page extraction.
- **GitHub Action** — submit a goal from any CI pipeline and await the result.

---

## Architecture Diagram

```
Browser / CLI / GitHub Action
          │
          ▼
  ┌───────────────┐
  │  React SPA    │  (TanStack Query, Zustand, SSE stream)
  └──────┬────────┘
         │ HTTP / SSE / WebSocket
         ▼
  ┌───────────────────────────────────────────────┐
  │  FastAPI (app/main.py)                        │
  │  ├─ TenantMiddleware  (API-key / JWT auth)    │
  │  ├─ Rate limiter      (Redis sliding window)  │
  │  ├─ ~25 routers       (goals, agents, MCP…)  │
  │  └─ lifespan          (DB/Redis pool swap)    │
  └──────┬─────────────────────┬─────────────────┘
         │                     │
         ▼                     ▼
  ┌─────────────┐      ┌──────────────────┐
  │  LangGraph  │      │  Celery workers  │
  │  Agent Loop │      │  (goal tasks,    │
  │  plan →     │      │   schedules,     │
  │  execute →  │      │   maintenance)   │
  │  verify     │      └──────────────────┘
  └──────┬──────┘
         │ tool calls
         ▼
  ┌─────────────────────────────────┐
  │  MCP Connector Registry         │
  │  (HTTP client → external tools) │
  └─────────────────────────────────┘
         │
         ▼
  ┌──────────────────────────────────────┐
  │  Postgres (pgvector) + Redis         │
  │  • RLS per tenant                    │
  │  • pgvector for embeddings/RAG       │
  │  • Redis: broker, pub/sub, cache     │
  └──────────────────────────────────────┘
```

---

## Contributing

1. Fork the repo and create a feature branch: `git checkout -b feat/my-feature`
2. Follow the stack-specific conventions in [`CLAUDE.md`](CLAUDE.md).
3. Ensure tests pass:
   - Backend: `uv run pytest` + `uv run ruff check .` + `uv run mypy app`
   - Frontend: `npm run test` + `npm run typecheck` + `npm run lint`
4. Open a pull request against `main` with a clear description of the change.

---

## License

MIT — see [LICENSE](LICENSE).
