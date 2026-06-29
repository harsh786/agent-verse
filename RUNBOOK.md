# AgentVerse — Complete Local & Production Runbook

> **One document to run everything.** Infra → Backend → Celery → Frontend → Observability → SDKs → Tests → Production K8s.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Environment Setup](#2-environment-setup)
3. [Local Development (Quick Start)](#3-local-development-quick-start)
4. [Infrastructure Services (Docker)](#4-infrastructure-services-docker)
5. [Backend (FastAPI)](#5-backend-fastapi)
6. [Celery Worker & Beat Scheduler](#6-celery-worker--beat-scheduler)
7. [Frontend (React + Vite)](#7-frontend-react--vite)
8. [Observability Stack](#8-observability-stack)
9. [Full Docker Stack (All-in-One)](#9-full-docker-stack-all-in-one)
10. [Database Management](#10-database-management)
11. [SDKs](#11-sdks)
12. [Tests](#12-tests)
13. [Production Kubernetes](#13-production-kubernetes)
14. [Service URLs & Ports](#14-service-urls--ports)
15. [Troubleshooting](#15-troubleshooting)
16. [Environment Variables Reference](#16-environment-variables-reference)

---

## 1. Prerequisites

### Required Tools

| Tool | Version | Install |
|------|---------|---------|
| **uv** | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **Node.js** | ≥ 18 | `brew install node` |
| **Docker Engine** | ≥ 24 | macOS: [Colima](https://github.com/abiosoft/colima) or Docker Desktop |
| **docker-compose** | standalone v1/v2 | `brew install docker-compose` |
| **k6** (optional, load tests) | latest | `brew install k6` |
| **kubectl** (optional, prod) | latest | `brew install kubectl` |
| **helm** (optional, prod) | ≥ 3 | `brew install helm` |

### macOS — Colima (Docker without Docker Desktop)

```bash
# Install Colima
brew install colima docker docker-compose

# Start the VM (do this every machine boot)
colima start

# Verify Docker works
docker info

# For integration tests (testcontainers), export these:
export DOCKER_HOST="unix://${HOME}/.colima/default/docker.sock"
export TESTCONTAINERS_RYUK_DISABLED=true
```

> **Important:** Colima does NOT auto-start on login. Always run `colima start` before any Docker operation. Add it to your shell profile to be safe:
> ```bash
> echo 'colima start 2>/dev/null || true' >> ~/.zshrc
> ```

### Python Notes

System Python on macOS is 3.9. The backend requires Python 3.12 — `uv` manages this automatically. **Never** use `python` or `pip` directly in the backend; always use `uv run`.

---

## 2. Environment Setup

### Backend `.env`

```bash
cd agent-verse-backend

# Copy the example env file
cp .env.example .env

# Edit .env and set at minimum ONE LLM provider key:
# ANTHROPIC_API_KEY=sk-ant-...      ← recommended (best reasoning)
# OPENAI_API_KEY=sk-...             ← alternative
# GOOGLE_API_KEY=...                ← Gemini alternative
# SENTENCE_TRANSFORMERS_MODEL=all-MiniLM-L6-v2  ← free, local, no API key

# Without any key, the backend runs in FakeProvider mode (dummy responses only)
```

### Frontend `.env`

```bash
cd agent-verse-frontend

# Create env file
cat > .env.local << 'EOF'
VITE_API_URL=http://localhost:8000
VITE_GRAFANA_URL=http://localhost:3001
EOF
```

### Install Dependencies

```bash
# Backend — installs into .venv, respects uv.lock
cd agent-verse-backend && uv sync

# Frontend
cd agent-verse-frontend && npm install

# Python SDK (optional)
cd agent-verse-sdk-python && uv sync

# TypeScript SDK (optional)
cd agent-verse-sdk-typescript && npm install
```

---

## 3. Local Development (Quick Start)

**Minimum setup to get the platform running in under 5 minutes:**

```bash
# Terminal 1 — Start Docker VM (macOS/Colima only)
colima start

# Terminal 1 — Start only the required infra services
cd agent-verse-backend
docker-compose -f infra/docker-compose.yml up -d postgres redis

# Terminal 1 — Apply DB migrations
uv sync && uv run alembic upgrade head

# Terminal 1 — Start the backend
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2 — Start Celery worker (needed for async goal execution)
cd agent-verse-backend
uv run celery -A app.scaling.celery_app worker \
  --loglevel=info \
  -Q goals,goals.free,schedules,maintenance \
  --concurrency=2

# Terminal 3 — Start the frontend
cd agent-verse-frontend
npm run dev
```

Open: **http://localhost:5173** — create a tenant via `POST /tenants/signup`, grab the API key, and submit your first goal.

---

## 4. Infrastructure Services (Docker)

All infra runs via Docker Compose. Services are defined in `agent-verse-backend/infra/docker-compose.yml`.

### Minimum Stack (Backend dev only)

```bash
cd agent-verse-backend

# Start just Postgres + Redis (fastest — no Keycloak wait)
docker-compose -f infra/docker-compose.yml up -d postgres redis

# Verify both are healthy
docker-compose -f infra/docker-compose.yml ps
```

### Standard Stack (Postgres + Redis + PgBouncer + MinIO + Mailpit)

```bash
docker-compose -f infra/docker-compose.yml up -d \
  postgres redis pgbouncer minio mailpit

# When using PgBouncer, update DATABASE_URL in .env:
# DATABASE_URL=postgresql+asyncpg://agentverse:agentverse@localhost:6432/agentverse
```

### Full Observability Stack (+ OTel + Jaeger + Prometheus + Grafana)

```bash
docker-compose -f infra/docker-compose.yml up -d \
  postgres redis pgbouncer minio mailpit \
  otel-collector jaeger prometheus grafana searxng
```

### Full Stack Including Keycloak SSO

```bash
# Keycloak takes 60-90s to start — start it early
docker-compose -f infra/docker-compose.yml up -d keycloak-db keycloak

# Wait for Keycloak to be healthy
docker-compose -f infra/docker-compose.yml wait keycloak

# Then start everything else
docker-compose -f infra/docker-compose.yml up -d \
  postgres redis pgbouncer minio mailpit \
  otel-collector jaeger prometheus grafana searxng
```

### Service Management

```bash
# Stop everything (keeps volumes)
docker-compose -f infra/docker-compose.yml down

# Stop and delete ALL data (full reset)
docker-compose -f infra/docker-compose.yml down -v

# Restart a single service
docker-compose -f infra/docker-compose.yml restart redis

# View logs for a service
docker-compose -f infra/docker-compose.yml logs -f backend

# View all service status + health
docker-compose -f infra/docker-compose.yml ps

# Inspect resource usage
docker stats
```

### Individual Service Details

#### PostgreSQL + pgvector

```bash
# Connect to DB
docker-compose -f infra/docker-compose.yml exec postgres \
  psql -U agentverse -d agentverse

# Check pgvector extension
docker-compose -f infra/docker-compose.yml exec postgres \
  psql -U agentverse -d agentverse -c "SELECT extname, extversion FROM pg_extension WHERE extname='vector';"

# Show all tables
docker-compose -f infra/docker-compose.yml exec postgres \
  psql -U agentverse -d agentverse -c "\dt"
```

#### Redis

```bash
# Connect to Redis CLI
docker-compose -f infra/docker-compose.yml exec redis redis-cli

# Common Redis commands
KEYS mcp:servers:*          # all registered MCP connectors
KEYS ratelimit:*            # rate limiter keys
INFO server                 # Redis version and info
DBSIZE                      # total key count

# Flush all data (careful!)
docker-compose -f infra/docker-compose.yml exec redis redis-cli FLUSHALL
```

#### MinIO (Object Storage)

```bash
# MinIO Console: http://localhost:9001
# Login: minioadmin / minioadmin

# List buckets via CLI
docker-compose -f infra/docker-compose.yml exec minio \
  mc ls local/

# Create agentverse-artifacts bucket (done automatically on first upload)
docker-compose -f infra/docker-compose.yml exec minio \
  mc mb local/agentverse-artifacts
```

#### Mailpit (Email Testing)

```bash
# Mailpit Web UI: http://localhost:8025
# SMTP: localhost:1025 (no auth required in dev)

# All emails sent by the backend (HITL approvals, goal completions) appear here.
```

---

## 5. Backend (FastAPI)

### Start Modes

#### Development (hot reload)

```bash
cd agent-verse-backend
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- Hot reload on any file change in `app/`
- Uses `MemorySaver` for LangGraph if Redis isn't available
- FakeProvider for LLM calls if no API keys are set

#### Production-like (multiple workers)

```bash
# 4 workers — do NOT use --reload in production
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

> **Warning:** Multiple uvicorn workers share no in-memory state. Run Celery workers instead for async goal execution, and ensure Redis is available for cross-replica pub/sub and LangGraph checkpointing.

#### Run via Docker

```bash
# Build the image
docker build -t agentverse-backend .

# Run with Docker (needs running Postgres + Redis)
docker run -p 8000:8000 \
  -e DATABASE_URL="postgresql+asyncpg://agentverse:agentverse@host.docker.internal:5432/agentverse" \
  -e REDIS_URL="redis://host.docker.internal:6379/0" \
  -e ANTHROPIC_API_KEY="sk-ant-..." \
  agentverse-backend
```

### Key Endpoints

```
GET  /health                   → Health check (all registered checks)
GET  /metrics                  → Prometheus metrics (text/plain)
GET  /docs                     → Swagger UI (interactive API docs)
GET  /redoc                    → ReDoc API docs
POST /tenants/signup           → Create new tenant (returns api_key)
GET  /tenants/me               → Current tenant info (requires X-API-Key)
POST /goals                    → Submit a goal
GET  /goals                    → List goals
GET  /goals/{id}/stream        → SSE stream of goal execution events
```

### Tenant & API Key Setup (First Time)

```bash
# Create your first tenant (returns a raw api_key — save it!)
curl -s -X POST http://localhost:8000/tenants/signup \
  -H "Content-Type: application/json" \
  -d '{"name": "My Team", "email": "me@example.com"}' | python3 -m json.tool

# Verify with the returned api_key
curl -s http://localhost:8000/tenants/me \
  -H "X-API-Key: av-YOUR-KEY-HERE" | python3 -m json.tool

# Submit your first goal
curl -s -X POST http://localhost:8000/goals \
  -H "X-API-Key: av-YOUR-KEY-HERE" \
  -H "Content-Type: application/json" \
  -d '{"goal": "List all registered connectors and summarise their capabilities"}' \
  | python3 -m json.tool
```

### Backend CLI

```bash
# Backend admin CLI (app.cli.main via Typer)
uv run agentverse --help

uv run agentverse goals list --api-key av-...
uv run agentverse agents list --api-key av-...
uv run agentverse connectors list --api-key av-...
uv run agentverse schedules list --api-key av-...
```

---

## 6. Celery Worker & Beat Scheduler

Celery processes goals asynchronously. Without a running worker, goals submitted via API queue in Redis but never execute.

### Start the Worker

```bash
cd agent-verse-backend

# Development (2 concurrent tasks, all queues)
uv run celery -A app.scaling.celery_app worker \
  --loglevel=info \
  -Q goals,goals.free,goals.starter,goals.professional,goals.enterprise,schedules,maintenance,goals_dlq \
  --concurrency=2

# Production (use --concurrency matching CPU cores)
uv run celery -A app.scaling.celery_app worker \
  --loglevel=warning \
  -Q goals,goals.free,goals.starter,goals.professional,goals.enterprise,schedules,maintenance,goals_dlq \
  --concurrency=4 \
  --max-tasks-per-child=100
```

### Queue Architecture

| Queue | Purpose |
|-------|---------|
| `goals` | Default goal execution |
| `goals.free` | Free-tier tenant goals |
| `goals.starter` | Starter-tier goals |
| `goals.professional` | Pro-tier goals |
| `goals.enterprise` | Enterprise (priority) goals |
| `schedules` | Scheduled trigger evaluation |
| `maintenance` | Cleanup, health checks, GDPR retention |
| `goals_dlq` | Dead-letter queue (failed goals) |

### Start Celery Beat (Scheduler)

Beat fires periodic tasks (schedule evaluation, MCP health checks, HITL expiry, data retention, etc.).

```bash
cd agent-verse-backend

# Always run exactly ONE beat instance per deployment
uv run celery -A app.scaling.celery_app beat --loglevel=info

# With celery-redbeat (schedule survives restarts — recommended)
# celery-redbeat stores beat schedule in Redis automatically
# No extra configuration needed if REDIS_URL is set
```

### Celery Management Commands

```bash
cd agent-verse-backend

# Inspect active workers
uv run celery -A app.scaling.celery_app inspect active

# List registered tasks
uv run celery -A app.scaling.celery_app inspect registered

# Check queue depths (requires Redis)
uv run celery -A app.scaling.celery_app inspect active_queues

# Purge all pending tasks (careful!)
uv run celery -A app.scaling.celery_app purge

# Revoke a specific task by ID
uv run celery -A app.scaling.celery_app control revoke <task-id> --terminate

# Monitor in real-time (Flower web UI)
uv run pip install flower
uv run celery -A app.scaling.celery_app flower --port=5555
# Open: http://localhost:5555
```

### Celery Beat — Scheduled Tasks

| Task | Interval | Purpose |
|------|----------|---------|
| `check_mcp_health` | 30s | Ping all registered MCP connectors |
| `fire_due_schedules` | 60s | Execute cron/interval/webhook triggers |
| `detect_stuck_goals` | 5 min | Mark goals stuck > timeout as failed |
| `expire_hitl_approvals` | 60s | Expire pending approvals past deadline |
| `check_email_goals` | 60s | Poll IMAP inbox for new goal emails |
| `enforce_data_retention` | 3 AM daily | Delete data older than DATA_RETENTION_DAYS |
| `warm_jwks_cache` | 15 min | Pre-warm Keycloak JWKS for fast auth |
| `scan_cost_anomalies` | 6 hr | Detect unusual LLM cost spikes |

---

## 7. Frontend (React + Vite)

### Development Mode

```bash
cd agent-verse-frontend

# Install deps (only needed once or after package.json changes)
npm install

# Start Vite dev server (hot module replacement)
npm run dev
# Open: http://localhost:5173

# The dev server proxies /api → http://localhost:8000 automatically
# (see vite.config.ts proxy config)
```

### Build for Production

```bash
cd agent-verse-frontend

# Type-check + build (output in dist/)
npm run build

# Preview the production build locally
npm run preview
# Open: http://localhost:4173
```

### Run via Docker

```bash
cd agent-verse-frontend

# Build Docker image
docker build \
  --build-arg VITE_API_URL=http://localhost:8000 \
  --build-arg VITE_GRAFANA_URL=http://localhost:3001 \
  -t agentverse-frontend .

# Run (served by Nginx on port 80)
docker run -p 5173:80 agentverse-frontend
# Open: http://localhost:5173
```

### Frontend App Structure

| Route | Purpose |
|-------|---------|
| `/dashboard` | KPI cards, activity feed, quick goal submit |
| `/goals` | Submit goals, view list, cancel |
| `/goals/:id` | Live SSE execution timeline, HITL controls, eval scorecard |
| `/agents` | Create/manage agents |
| `/connectors` | Register MCP connectors |
| `/connectors/catalog` | Browse 32+ connector templates |
| `/governance` | Policies, approvals, audit log, emergency stop |
| `/knowledge` | Ingest documents (PDF, DOCX, URL, GitHub, etc.) |
| `/workflow-builder` | Visual drag-drop workflow editor (ReactFlow) |
| `/memory` | Long-term memory explorer |
| `/tools` | Code runner, file manager, email composer |
| `/integrations` | Slack/Zapier/Webhook configuration |
| `/observability` | Health checks, Prometheus metrics |
| `/observability/cost` | LLM cost time-series, by-model breakdown |
| `/analytics` | Goal/tool/eval analytics charts |
| `/eval` | Red-team tests, simulation, eval suites |
| `/marketplace` | Agent template gallery |
| `/settings` | API keys, LLM config, BYOK |

---

## 8. Observability Stack

### Jaeger (Distributed Tracing)

```bash
# Start Jaeger + OTel Collector
docker-compose -f infra/docker-compose.yml up -d otel-collector jaeger

# Open Jaeger UI: http://localhost:16686
# Service to search: agentverse-backend
# Look for spans: agentverse.plan, agentverse.step.execute, agentverse.tool.call
```

To enable tracing in the backend, set in `.env`:
```
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
OTEL_SERVICE_NAME=agentverse-backend
```

### Prometheus + Grafana

```bash
# Start monitoring stack
docker-compose -f infra/docker-compose.yml up -d prometheus grafana

# Prometheus: http://localhost:9090
# Grafana:    http://localhost:3001  (admin / agentverse)
```

Key Prometheus metrics scraped from `GET /metrics`:

| Metric | Description |
|--------|-------------|
| `agentverse_goals_started_total` | Goals submitted |
| `agentverse_goals_completed_total` | Goals completed successfully |
| `agentverse_goals_failed_total` | Goals failed |
| `agentverse_plan_duration_seconds` | LLM planning latency |
| `agentverse_tool_call_total` | MCP tool invocations |
| `agentverse_approval_wait_seconds` | HITL approval wait time |

### Structured Logging

Backend emits JSON logs in production, human-readable in development.

```bash
# Stream pretty-printed logs
uv run uvicorn app.main:app --reload 2>&1 | python3 -m json.tool 2>/dev/null || cat

# Filter logs by level
uv run uvicorn app.main:app --reload 2>&1 | grep '"level":"ERROR"'

# Log to file
uv run uvicorn app.main:app --reload >> /tmp/agentverse.log 2>&1 &
tail -f /tmp/agentverse.log | python3 -m json.tool
```

---

## 9. Full Docker Stack (All-in-One)

Run the entire platform (all 16 services) with a single command:

```bash
cd agent-verse-backend

# Start all services (first run: ~3-5 min for image builds)
docker-compose -f infra/docker-compose.yml up --build

# Detached mode (background)
docker-compose -f infra/docker-compose.yml up --build -d

# Follow logs from all services
docker-compose -f infra/docker-compose.yml logs -f

# Follow logs from specific services
docker-compose -f infra/docker-compose.yml logs -f backend worker beat
```

### First-Run Checklist (Docker Stack)

After `up --build -d`, verify everything is healthy:

```bash
# 1. Check all services are healthy
docker-compose -f infra/docker-compose.yml ps

# 2. Check backend is up
curl http://localhost:8000/health

# 3. Create your first tenant
curl -s -X POST http://localhost:8000/tenants/signup \
  -H "Content-Type: application/json" \
  -d '{"name": "Admin", "email": "admin@local.dev"}' | python3 -m json.tool

# 4. Open the frontend
open http://localhost:5173

# 5. Open Keycloak Admin (if SSO needed)
open http://localhost:8080  # admin / admin
```

### Rebuild After Code Changes

```bash
# Rebuild only the changed service
docker-compose -f infra/docker-compose.yml up --build backend

# Rebuild worker after Python changes
docker-compose -f infra/docker-compose.yml up --build worker beat
```

---

## 10. Database Management

### Alembic Migrations

```bash
cd agent-verse-backend

# Apply all pending migrations (run after git pull or fresh DB)
uv run alembic upgrade head

# Roll back one migration
uv run alembic downgrade -1

# Roll back to a specific revision
uv run alembic downgrade <revision_id>

# Check current revision
uv run alembic current

# Show migration history
uv run alembic history --verbose

# Generate a new migration from model changes
uv run alembic revision --autogenerate -m "add_my_table"
# Then review app/db/migrations/versions/XXXX_add_my_table.py before applying!
```

> **Rule:** Never edit a deployed migration. If you need to change schema, create a new migration.

### Database Backup & Restore

```bash
# Backup (while Postgres container is running)
docker-compose -f infra/docker-compose.yml exec postgres \
  pg_dump -U agentverse agentverse > backup_$(date +%Y%m%d_%H%M%S).sql

# Restore
docker-compose -f infra/docker-compose.yml exec -T postgres \
  psql -U agentverse agentverse < backup_20260628_120000.sql

# Full reset (drops and recreates DB)
docker-compose -f infra/docker-compose.yml exec postgres \
  psql -U agentverse -c "DROP DATABASE IF EXISTS agentverse; CREATE DATABASE agentverse;"
docker-compose -f infra/docker-compose.yml exec postgres \
  psql -U agentverse -d agentverse -c "CREATE EXTENSION IF NOT EXISTS vector;"
uv run alembic upgrade head
```

### Reset Everything (Clean Slate)

```bash
cd agent-verse-backend

# Stop all services and delete all data volumes
docker-compose -f infra/docker-compose.yml down -v

# Start fresh
docker-compose -f infra/docker-compose.yml up -d postgres redis
sleep 5

# Recreate schema
uv run alembic upgrade head
```

---

## 11. SDKs

### Python SDK

```bash
cd agent-verse-sdk-python
uv sync

# Run tests
uv run pytest

# Use the SDK in your own project
pip install agentverse-sdk  # or: uv add agentverse-sdk
```

```python
import asyncio
from agentverse import AgentVerseClient

async def main():
    async with AgentVerseClient(
        api_key="av-YOUR-KEY",
        base_url="http://localhost:8000"
    ) as client:
        # Submit a goal and stream events
        goal = await client.submit_goal("List all open GitHub issues")
        async for event in client.stream_goal(goal.goal_id):
            print(f"{event.type}: {event.payload}")

        # Or wait for completion
        result = await client.wait_for_goal(goal.goal_id, timeout=120)
        print(result.status, result.result)

asyncio.run(main())
```

### TypeScript SDK

```bash
cd agent-verse-sdk-typescript
npm install && npm run build

# Run tests
npm test
```

```typescript
import { AgentVerseClient } from '@agentverse/sdk';

const client = new AgentVerseClient({
  apiKey: 'av-YOUR-KEY',
  baseUrl: 'http://localhost:8000',
});

// Submit and stream
const goal = await client.submitGoal('Summarise all open Jira tickets');
for await (const event of client.streamGoal(goal.goalId)) {
  console.log(event.type, event.payload);
}

// Wait for completion
const result = await client.waitForGoal(goal.goalId, { timeout: 120 });
console.log(result.status, result.result);
```

### GitHub Action

```yaml
# .github/workflows/agent-task.yml
name: AgentVerse Goal
on: [push]
jobs:
  run-goal:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: agentverse/run-goal@v1
        with:
          api-key: ${{ secrets.AGENTVERSE_API_KEY }}
          base-url: https://your-agentverse-instance.com
          goal: "Review this PR diff and post a Slack summary"
          wait-timeout: 300
```

---

## 12. Tests

### Backend Tests

```bash
cd agent-verse-backend

# Full unit test suite (10,000+ tests, ~5 min)
uv run pytest

# Quick run (no coverage, parallel)
uv run pytest -n auto --no-cov

# Single file
uv run pytest tests/agent/test_graph_rollback.py -v

# Single test
uv run pytest tests/agent/test_graph_rollback.py::test_rollback_all_async_awaits_coroutines -v

# By marker
uv run pytest -m integration          # needs Docker (testcontainers)
uv run pytest -m slow                 # hits real LLM APIs (needs API keys)
uv run pytest -m "not integration and not slow"  # unit tests only (default)

# With coverage report
uv run pytest --cov=app --cov-report=html
open htmlcov/index.html

# Watch mode (re-run on file change)
uv run ptw -- -x tests/services/

# Lint + type check
uv run ruff check app/ tests/
uv run mypy app/
```

#### Integration Tests (needs Docker)

```bash
# Set Docker socket for testcontainers
export DOCKER_HOST="unix://${HOME}/.colima/default/docker.sock"
export TESTCONTAINERS_RYUK_DISABLED=true

colima start  # if not already running

# Run integration tests (Postgres + Redis spun up automatically)
uv run pytest -m integration -v
```

### Frontend Tests

```bash
cd agent-verse-frontend

# Unit + component tests (Vitest)
npm run test

# Watch mode
npm run test -- --watch

# With coverage
npm run test -- --coverage
open coverage/index.html

# E2E tests (Playwright — needs backend running on :8000)
npm run test:e2e

# E2E in headed mode (see browser)
npm run test:e2e -- --headed

# Specific E2E spec
npm run test:e2e -- e2e/goals.spec.ts
```

### Load Tests (k6)

```bash
cd agent-verse-backend/tests/load

# Smoke test (2 VUs, 1 min) — verify API is reachable
k6 run smoke.js

# Auth throughput (50 VUs, 3 min) — stress TenantMiddleware
k6 run auth_throughput.js

# Goal submission (10 VUs, 5 min) — test main path
k6 run -e API_KEY=av-... goal_submission.js

# Soak test (10 VUs, 30 min) — detect memory leaks
k6 run --env DURATION=30m soak.js
```

---

## 13. Production Kubernetes

### Namespace Setup

```bash
# Create namespace
kubectl apply -f agent-verse-backend/infra/k8s/namespace.yaml

# Verify
kubectl get ns agentverse
```

### Secrets

```bash
# Edit secrets.yaml with your values (base64-encoded)
cp agent-verse-backend/infra/k8s/secrets.yaml /tmp/secrets.yaml
# Edit /tmp/secrets.yaml
kubectl apply -f /tmp/secrets.yaml -n agentverse
shred -u /tmp/secrets.yaml  # delete plaintext copy
```

### Database Migration (run once before deployment)

```bash
kubectl apply -f agent-verse-backend/infra/k8s/migration-job.yaml -n agentverse

# Wait for completion
kubectl wait --for=condition=complete job/agentverse-migration -n agentverse --timeout=120s

# Check logs
kubectl logs -n agentverse job/agentverse-migration
```

### Deploy All Services

```bash
cd agent-verse-backend/infra/k8s

# Apply all manifests
kubectl apply -k . -n agentverse

# Or apply individually
kubectl apply -f configmap.yaml -n agentverse
kubectl apply -f postgres-pvc.yaml -n agentverse
kubectl apply -f pgbouncer-deployment.yaml -n agentverse
kubectl apply -f backend-deployment.yaml -n agentverse
kubectl apply -f backend-service.yaml -n agentverse
kubectl apply -f backend-hpa.yaml -n agentverse
kubectl apply -f backend-pdb.yaml -n agentverse
kubectl apply -f worker-deployment.yaml -n agentverse
kubectl apply -f worker-hpa.yaml -n agentverse
kubectl apply -f worker-pdb.yaml -n agentverse
kubectl apply -f beat-deployment.yaml -n agentverse
kubectl apply -f frontend-deployment.yaml -n agentverse
kubectl apply -f ingress.yaml -n agentverse
kubectl apply -f keda-scaledobject.yaml -n agentverse

# Verify all pods are running
kubectl get pods -n agentverse
kubectl get hpa -n agentverse
```

### Blue/Green Zero-Downtime Deployment

```bash
cd agent-verse-backend/infra/k8s

# Deploy green (new version) alongside blue (current)
kubectl apply -f backend-deployment-green.yaml -n agentverse

# Wait for green to be ready
kubectl rollout status deployment/agentverse-backend-green -n agentverse

# Run smoke tests against green
kubectl port-forward -n agentverse deployment/agentverse-backend-green 8001:8000 &
curl http://localhost:8001/health

# Switch traffic to green
./switch-traffic.sh green

# Verify traffic is on green
kubectl get service agentverse-backend -n agentverse -o yaml | grep selector

# Delete old blue deployment
kubectl delete deployment agentverse-backend-blue -n agentverse
```

### Scaling

```bash
# Manual scale workers
kubectl scale deployment agentverse-worker -n agentverse --replicas=5

# KEDA autoscales workers automatically (1-20 replicas) based on Redis queue depth
# Check KEDA ScaledObject status:
kubectl get scaledobject -n agentverse

# Check HPA for backend
kubectl describe hpa agentverse-backend-hpa -n agentverse
```

### Helm Deployment

```bash
cd agent-verse-backend/infra/helm

# Install
helm install agentverse ./agentverse \
  --namespace agentverse \
  --create-namespace \
  --set backend.apiKey="your-master-key" \
  --set postgresql.password="your-db-password" \
  --set llm.anthropicApiKey="sk-ant-..."

# Upgrade
helm upgrade agentverse ./agentverse \
  --namespace agentverse \
  --set backend.image.tag="v1.2.0"

# Rollback
helm rollback agentverse 1 -n agentverse

# Uninstall (keeps PVCs)
helm uninstall agentverse -n agentverse
```

### Common K8s Operations

```bash
# View backend logs
kubectl logs -n agentverse -l app=agentverse-backend -f

# View worker logs
kubectl logs -n agentverse -l app=agentverse-worker -f

# Execute into a pod
kubectl exec -it -n agentverse deployment/agentverse-backend -- /bin/bash

# Port-forward for local debugging
kubectl port-forward -n agentverse svc/agentverse-backend 8000:8000

# Check DB backup job
kubectl get cronjob pg-backup -n agentverse
kubectl get jobs -n agentverse | grep pg-backup
```

---

## 14. Service URLs & Ports

| Service | Local URL | Docker URL | Purpose |
|---------|-----------|------------|---------|
| **Backend API** | http://localhost:8000 | http://backend:8000 | FastAPI REST + SSE |
| **API Docs** | http://localhost:8000/docs | — | Swagger UI |
| **Frontend** | http://localhost:5173 | http://frontend:80 | React SPA |
| **PostgreSQL** | localhost:5432 | postgres:5432 | Primary database |
| **PgBouncer** | localhost:6432 | pgbouncer:6432 | Connection pooler |
| **Redis** | localhost:6379 | redis:6379 | Cache + broker |
| **MinIO API** | http://localhost:9000 | http://minio:9000 | Object storage S3 |
| **MinIO Console** | http://localhost:9001 | — | MinIO web UI |
| **Mailpit SMTP** | localhost:1025 | mailpit:1025 | Test SMTP server |
| **Mailpit UI** | http://localhost:8025 | — | Email viewer |
| **Keycloak** | http://localhost:8080 | http://keycloak:8080 | SSO identity provider |
| **Jaeger UI** | http://localhost:16686 | — | Distributed traces |
| **OTel Collector (gRPC)** | localhost:4317 | otel-collector:4317 | Trace ingestion |
| **Prometheus** | http://localhost:9090 | — | Metrics collection |
| **Grafana** | http://localhost:3001 | — | Metrics dashboards |
| **SearXNG** | http://localhost:8081 | http://searxng:8080 | Self-hosted search |
| **Flower** (optional) | http://localhost:5555 | — | Celery monitor |

---

## 15. Troubleshooting

### Backend won't start

```bash
# Check for import errors
uv run python -c "from app.main import create_app; print('OK')"

# Check Postgres connectivity
uv run python -c "
import asyncio, asyncpg
async def check():
    conn = await asyncpg.connect('postgresql://agentverse:agentverse@localhost:5432/agentverse')
    print('Postgres OK:', await conn.fetchval('SELECT version()'))
    await conn.close()
asyncio.run(check())"

# Check Redis connectivity
uv run python -c "
import asyncio, redis.asyncio as aioredis
async def check():
    r = await aioredis.from_url('redis://localhost:6379')
    print('Redis OK:', await r.ping())
    await r.aclose()
asyncio.run(check())"
```

### Migrations fail

```bash
# Check current revision vs head
uv run alembic current
uv run alembic heads

# If there's a merge conflict in migrations
uv run alembic merge heads -m "merge"
uv run alembic upgrade head
```

### Goals are stuck / not executing

```bash
# 1. Check Celery worker is running
uv run celery -A app.scaling.celery_app inspect ping

# 2. Check queue depth
uv run celery -A app.scaling.celery_app inspect active_queues

# 3. Check Redis for stuck tasks
docker-compose -f infra/docker-compose.yml exec redis redis-cli LLEN goals

# 4. Look at worker logs for errors
# In the terminal running celery worker, look for exceptions

# 5. Submit a goal with dry_run=true to test without execution
curl -X POST http://localhost:8000/goals \
  -H "X-API-Key: av-..." \
  -H "Content-Type: application/json" \
  -d '{"goal": "test", "dry_run": true}'
```

### Frontend can't reach backend (CORS errors)

```bash
# Verify backend CORS settings in .env
CORS_ORIGINS=http://localhost:5173

# Or set to allow all (dev only!)
CORS_ORIGINS=*

# Restart backend
uv run uvicorn app.main:app --reload
```

### Colima / Docker issues

```bash
# Docker daemon not running
colima start

# Check Docker socket exists
ls -la ~/.colima/default/docker.sock

# Reset Colima (nuclear option)
colima stop
colima delete
colima start

# View Colima VM status
colima status
colima list
```

### LLM returning fake/dummy responses

This happens when no API key is set. FakeProvider returns predetermined strings.

```bash
# Check which provider is active
curl http://localhost:8000/health | python3 -m json.tool | grep -i provider

# Set at least one key in .env
ANTHROPIC_API_KEY=sk-ant-...

# Restart backend
uv run uvicorn app.main:app --reload
```

### Keycloak SSO not working

```bash
# Keycloak takes 60-90s to fully start
docker-compose -f infra/docker-compose.yml logs keycloak | tail -20

# Check Keycloak health
curl http://localhost:8080/health/ready

# Verify realm was imported
curl http://localhost:8080/realms/agentverse/.well-known/openid-configuration

# If realm is missing, reimport
docker-compose -f infra/docker-compose.yml restart keycloak
```

### High memory / OOM

```bash
# Check memory usage
docker stats --no-stream

# The backend loads Playwright Chromium — it's heavy (~200MB)
# Reduce uvicorn workers if OOM
uv run uvicorn app.main:app --workers 1

# Or disable Playwright in .env
AGENTVERSE_PLAYWRIGHT_ENABLED=false
```

---

## 16. Environment Variables Reference

Set these in `agent-verse-backend/.env`. See `.env.example` for the full template.

### Required for Basic Operation

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | — | PostgreSQL asyncpg DSN (`postgresql+asyncpg://user:pass@host:port/db`) |
| `REDIS_URL` | — | Redis DSN (`redis://localhost:6379/0`) |
| `ENVIRONMENT` | `development` | `development` / `staging` / `production` |

### LLM Providers (set at least one for real execution)

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic Claude (recommended — best reasoning) |
| `OPENAI_API_KEY` | OpenAI GPT-4 (alternative) |
| `GOOGLE_API_KEY` | Google Gemini (alternative) |
| `VOYAGE_API_KEY` | Voyage AI embeddings (best RAG quality) |
| `SENTENCE_TRANSFORMERS_MODEL` | Local embedding model (e.g. `all-MiniLM-L6-v2`) — no API key needed |

### Security

| Variable | Default | Description |
|----------|---------|-------------|
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allowed origins |
| `SSO_ENABLED` | `false` | Enable Keycloak SSO |
| `KEYCLOAK_URL` | `http://localhost:8080` | Keycloak base URL |
| `KEYCLOAK_REALM` | `agentverse` | Keycloak realm name |
| `KEYCLOAK_CLIENT_ID` | `agentverse-backend` | Keycloak client ID |
| `KEYCLOAK_CLIENT_SECRET` | — | Keycloak client secret |

### Object Storage

| Variable | Default | Description |
|----------|---------|-------------|
| `MINIO_ENDPOINT` | `http://localhost:9000` | MinIO / S3 endpoint |
| `MINIO_ACCESS_KEY` | `minioadmin` | MinIO access key |
| `MINIO_SECRET_KEY` | `minioadmin` | MinIO secret key |

### Email (SMTP)

| Variable | Default | Description |
|----------|---------|-------------|
| `SMTP_HOST` | `localhost` | SMTP server host |
| `SMTP_PORT` | `1025` | SMTP port (1025 = Mailpit dev, 587 = production) |
| `SMTP_USER` | — | SMTP username |
| `SMTP_PASSWORD` | — | SMTP password |
| `SMTP_TLS` | `false` | Enable TLS |

### Integrations

| Variable | Description |
|----------|-------------|
| `SLACK_TENANT_ID` | Tenant ID for Slack slash commands |
| `SLACK_SIGNING_SECRET` | Slack app signing secret |
| `SLACK_BOT_TOKEN` | `xoxb-...` Slack bot OAuth token |
| `ZAPIER_TENANT_ID` | Tenant ID for Zapier webhook triggers |
| `ZAPIER_WEBHOOK_SECRET` | Zapier HMAC signing secret |
| `IMAP_ENABLED` | `false` — enable email-to-goal polling |
| `IMAP_HOST` | IMAP server hostname |
| `IMAP_USER` | IMAP account username |
| `IMAP_PASSWORD` | IMAP account password |

### Observability

| Variable | Default | Description |
|----------|---------|-------------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | OTel Collector gRPC endpoint (`http://localhost:4317`) |
| `OTEL_SERVICE_NAME` | `agentverse-backend` | Service name in traces |
| `SEARXNG_URL` | `http://localhost:8081` | SearXNG search engine URL |

### Runtime Tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_RETENTION_DAYS` | `90` | Days to keep goal/event data |
| `EMBEDDING_DIM` | `1536` | Embedding vector dimension |
| `AGENTVERSE_DB_SCHEDULE_DISCOVERY` | `true` | Enable DB-backed schedule discovery |
| `AGENTVERSE_ALLOW_SHELL_EXEC` | `false` | Allow shell tool (security risk) |
| `AGENTVERSE_ALLOW_SUBPROCESS_EXEC` | `false` | Allow subprocess tool |
| `POSTGRES_MCP_ALLOW_WRITES` | `false` | Allow write queries via Postgres MCP |

---

*Last updated: 2026-06-29 · AgentVerse monorepo*
