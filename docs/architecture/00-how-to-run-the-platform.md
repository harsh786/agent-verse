# AgentVerse OS — Complete Setup & Run Guide

> **The definitive step-by-step guide** to running the entire AgentVerse platform locally, from zero to fully operational.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Repository Setup](#2-repository-setup)
3. [Environment Configuration](#3-environment-configuration)
4. [Option A: Full Docker Stack (Recommended)](#4-option-a-full-docker-stack-recommended)
5. [Option B: Hybrid Mode (Docker Infra + Local Code)](#5-option-b-hybrid-mode-docker-infra--local-code)
6. [Database Migrations](#6-database-migrations)
7. [Running the Backend](#7-running-the-backend)
8. [Running the Frontend](#8-running-the-frontend)
9. [Running Celery Workers & Beat](#9-running-celery-workers--beat)
10. [Verifying Everything Works](#10-verifying-everything-works)
11. [Service URLs & Credentials](#11-service-urls--credentials)
12. [Creating Your First Tenant & Goal](#12-creating-your-first-tenant--goal)
13. [Running Tests](#13-running-tests)
14. [SDK & CLI Usage](#14-sdk--cli-usage)
15. [Grafana & Observability](#15-grafana--observability)
16. [Troubleshooting](#16-troubleshooting)
17. [Production Deployment](#17-production-deployment)
18. [Useful Shortcuts Reference](#18-useful-shortcuts-reference)

---

## 1. Prerequisites

### 1.1 Required Software

Install these before anything else:

```bash
# ── macOS ──────────────────────────────────────────────────────────────────

# 1. Homebrew (if not installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 2. Colima — lightweight Docker runtime for macOS (no Docker Desktop needed)
brew install colima

# 3. Docker CLI + docker-compose
brew install docker docker-compose

# 4. Node.js 20+ (for frontend)
brew install node@20
# or use nvm:
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
nvm install 20 && nvm use 20

# 5. uv — fast Python package manager (required for backend)
curl -LsSf https://astral.sh/uv/install.sh | sh
# Restart shell after install:
source ~/.zshrc   # or ~/.bashrc

# 6. Git
brew install git

# ── Ubuntu / Debian ────────────────────────────────────────────────────────

sudo apt-get update && sudo apt-get install -y \
  docker.io docker-compose-plugin \
  nodejs npm git curl

# Install uv:
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
```

### 1.2 Verify Installations

```bash
# Check all tools are installed and versions are correct
colima --version          # ≥ 0.6
docker --version          # ≥ 24.0
docker-compose --version  # ≥ 2.20
node --version            # ≥ v20.0
npm --version             # ≥ 10.0
uv --version              # ≥ 0.4
python3 --version         # System Python (uv manages 3.12 separately)
git --version             # Any recent version
```

### 1.3 LLM API Key (Required for real agent execution)

You need at least ONE of:

| Provider | Environment Variable | Get Key At |
|---------|---------------------|-----------|
| Anthropic (recommended) | `ANTHROPIC_API_KEY` | https://console.anthropic.com |
| OpenAI | `OPENAI_API_KEY` | https://platform.openai.com |
| Google Gemini | `GOOGLE_API_KEY` | https://aistudio.google.com |
| Voyage AI (for embeddings) | `VOYAGE_API_KEY` | https://www.voyageai.com |
| Groq (fast/cheap) | `GROQ_API_KEY` | https://console.groq.com |

> **Without an LLM key**: The platform starts and all APIs work, but agent goals will execute using `FakeProvider` which returns deterministic stub responses. Good for testing the platform itself.

---

## 2. Repository Setup

### 2.1 Clone the Repository

```bash
# Clone
git clone https://github.com/harsh786/agent-verse.git
cd agent-verse

# Verify structure
ls -la
# Expected:
# agent-verse-backend/
# agent-verse-frontend/
# agent-verse-sdk-python/
# agent-verse-sdk-typescript/
# agent-verse-github-action/
# docs/
# README.md
```

### 2.2 Install Backend Dependencies

```bash
cd agent-verse-backend

# Install Python 3.12 + all dependencies via uv
uv sync

# Verify Python version
uv run python --version
# Expected: Python 3.12.x

# Verify key packages installed
uv run python -c "import fastapi; import langraph; import sqlalchemy; print('OK')"
# If "langraph" fails try: import langgraph
```

### 2.3 Install Frontend Dependencies

```bash
cd agent-verse-frontend

# Install all npm packages
npm install

# Verify
node_modules/.bin/vite --version
# Expected: vite/6.x.x
```

---

## 3. Environment Configuration

### 3.1 Backend Environment File

```bash
cd agent-verse-backend

# Copy the example environment file
cp .env.example .env

# Open and edit:
nano .env   # or: code .env / vim .env
```

**Minimum required changes in `.env`**:

```bash
# ═══════════════════════════════════════════════════════════════
# MINIMUM REQUIRED: Set at least one LLM provider key
# ═══════════════════════════════════════════════════════════════
ANTHROPIC_API_KEY=sk-ant-api03-YOUR_KEY_HERE
# OPENAI_API_KEY=sk-YOUR_KEY_HERE       # alternative
# VOYAGE_API_KEY=pa-YOUR_KEY_HERE       # optional: best embeddings

# ═══════════════════════════════════════════════════════════════
# These defaults work with docker-compose (no changes needed):
# ═══════════════════════════════════════════════════════════════
ENVIRONMENT=development
DATABASE_URL=postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse
REDIS_URL=redis://localhost:6379/0
```

### 3.2 Full .env Reference

```bash
# ── Core ────────────────────────────────────────────────────────────────────
ENVIRONMENT=development         # development | staging | production
DEBUG=true
LOG_LEVEL=INFO

# ── Database & Cache ─────────────────────────────────────────────────────────
DATABASE_URL=postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse
REDIS_URL=redis://localhost:6379/0

# Use PgBouncer (recommended, lower connection count):
# DATABASE_URL=postgresql+asyncpg://agentverse:agentverse@localhost:6432/agentverse

# ── LLM Providers ────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-...        # Claude Opus/Sonnet/Haiku
OPENAI_API_KEY=sk-...               # GPT-4o + text embeddings
VOYAGE_API_KEY=pa-...               # Best RAG embeddings
GOOGLE_API_KEY=...                  # Gemini models
# GROQ_API_KEY=...                  # Fast inference (Llama)
# SENTENCE_TRANSFORMERS_MODEL=all-MiniLM-L6-v2  # local CPU (no API key)

# ── Object Storage ────────────────────────────────────────────────────────────
MINIO_ENDPOINT=http://localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin

# ── SSO (optional for dev, set SSO_ENABLED=false to skip) ───────────────────
KEYCLOAK_URL=http://localhost:8080
KEYCLOAK_REALM=agentverse
KEYCLOAK_CLIENT_ID=agentverse-backend
KEYCLOAK_CLIENT_SECRET=agentverse-backend-secret
SSO_ENABLED=false                   # Set true to enable Keycloak login

# ── Observability ────────────────────────────────────────────────────────────
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317   # Leave empty to disable
SERVICE_NAME=agentverse-backend

# ── Search ───────────────────────────────────────────────────────────────────
SEARXNG_URL=http://localhost:8081

# ── Email (MailPit for dev) ──────────────────────────────────────────────────
SMTP_HOST=localhost
SMTP_PORT=1025
SMTP_TLS=false

# ── Feature Flags ────────────────────────────────────────────────────────────
CIVILIZATION_ENABLED=true
DATA_RETENTION_DAYS=90

# ── Security ─────────────────────────────────────────────────────────────────
AGENTVERSE_ALLOW_SHELL_EXEC=false   # Enable only if you need shell tools
FRONTEND_URL=http://localhost:5173
CORS_ORIGINS=http://localhost:5173
```

### 3.3 Frontend Environment (Optional)

```bash
cd agent-verse-frontend

# Only needed if backend is not at localhost:8000
cat > .env.local << 'EOF'
VITE_API_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000
VITE_GRAFANA_URL=http://localhost:3001
EOF
```

---

## 4. Option A: Full Docker Stack (Recommended)

This runs everything — database, cache, backend, frontend, workers, and all supporting services — in Docker containers. Best for getting started quickly.

### 4.1 Start Docker Runtime (macOS only)

```bash
# Start Colima with enough resources for the full stack
colima start \
  --cpu 4 \
  --memory 8 \
  --disk 50

# Verify Docker is working
docker ps
# Expected: empty table (no containers yet)
```

### 4.2 Build & Start All Services

```bash
cd agent-verse-backend

# Build all images (first time takes 3-5 minutes)
docker-compose -f infra/docker-compose.yml build

# Start everything in background
docker-compose -f infra/docker-compose.yml up -d

# Watch startup progress
docker-compose -f infra/docker-compose.yml logs -f

# Wait for all services to be healthy (about 60 seconds)
docker-compose -f infra/docker-compose.yml ps
```

**Expected output of `docker-compose ps`** (all should be "Up"):
```
NAME                        STATUS              PORTS
agentverse-postgres         Up (healthy)        0.0.0.0:5432->5432/tcp
agentverse-redis            Up (healthy)        0.0.0.0:6379->6379/tcp
agentverse-pgbouncer        Up (healthy)        0.0.0.0:6432->6432/tcp
agentverse-backend          Up (healthy)        0.0.0.0:8000->8000/tcp
agentverse-worker           Up                  
agentverse-beat             Up                  
agentverse-frontend         Up                  0.0.0.0:5173->80/tcp
agentverse-keycloak-db      Up (healthy)        
agentverse-keycloak         Up (healthy)        0.0.0.0:8080->8080/tcp
agentverse-minio            Up (healthy)        0.0.0.0:9000-9001->9000-9001/tcp
agentverse-mailpit          Up                  0.0.0.0:1025->1025/tcp, 0.0.0.0:8025->8025/tcp
agentverse-otel-collector   Up                  
agentverse-jaeger           Up                  0.0.0.0:16686->16686/tcp
agentverse-searxng          Up                  0.0.0.0:8081->8080/tcp
agentverse-prometheus       Up                  0.0.0.0:9090->9090/tcp
agentverse-grafana          Up                  0.0.0.0:3001->3000/tcp
```

### 4.3 Run Database Migrations (First Time Only)

```bash
# Run inside the running backend container
docker-compose -f infra/docker-compose.yml exec backend \
  uv run alembic upgrade head

# Expected output:
# INFO  [alembic.runtime.migration] Context impl PostgreSQLImpl.
# INFO  [alembic.runtime.migration] Will assume transactional DDL.
# INFO  [alembic.runtime.migration] Running upgrade  -> 0001, ...
# ...
# INFO  [alembic.runtime.migration] Running upgrade 0066_tenant_settings -> 0067, consent_records_v2
```

### 4.4 Verify Platform is Running

```bash
# Health check
curl http://localhost:8000/health
# Expected: {"status": "ok", "dependencies": {...}}

# API docs
open http://localhost:8000/docs    # macOS
# or:
xdg-open http://localhost:8000/docs  # Linux

# Frontend
open http://localhost:5173
```

### 4.5 Stop All Services

```bash
# Stop (preserves volumes/data)
docker-compose -f infra/docker-compose.yml stop

# Stop and remove containers (preserves volumes)
docker-compose -f infra/docker-compose.yml down

# Stop and remove EVERYTHING including volumes (fresh start)
docker-compose -f infra/docker-compose.yml down -v
```

---

## 5. Option B: Hybrid Mode (Docker Infra + Local Code)

This runs infrastructure (Postgres, Redis, etc.) in Docker but runs the backend and frontend locally. Best for active development with hot-reload.

### 5.1 Start Only Infrastructure Services

```bash
cd agent-verse-backend

# Start only the infrastructure (no backend/frontend/workers)
docker-compose -f infra/docker-compose.yml up -d \
  postgres redis pgbouncer minio keycloak-db keycloak \
  mailpit otel-collector jaeger searxng prometheus grafana

# Wait for postgres to be healthy
docker-compose -f infra/docker-compose.yml exec postgres \
  pg_isready -U agentverse
# Expected: /var/run/postgresql:5432 - accepting connections
```

### 5.2 Run Database Migrations Locally

```bash
cd agent-verse-backend

# Set DATABASE_URL to localhost (not pgbouncer in compose)
export DATABASE_URL=postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse

# Run migrations
uv run alembic upgrade head

# Verify migrations applied
uv run alembic current
# Expected: INFO  [alembic.runtime.migration] Context impl PostgreSQLImpl.
#           0067 (head)

# Check all 67 migrations applied
uv run alembic history | wc -l
# Expected: ~70 lines
```

### 5.3 Run Backend Locally (Hot-reload)

```bash
cd agent-verse-backend

# Export your LLM key
export ANTHROPIC_API_KEY=sk-ant-...

# Start FastAPI with hot-reload
uv run uvicorn app.main:app --reload --port 8000 --host 0.0.0.0

# Expected output:
# INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
# INFO:     Started reloader process [12345] using StatReload
# INFO:     Started server process [12346]
# INFO:     Waiting for application startup.
# INFO:     Application startup complete.
```

### 5.4 Run Celery Workers Locally

Open new terminal tabs for each:

```bash
# ── Terminal 2: Celery Worker ───────────────────────────────────────────────
cd agent-verse-backend
export ANTHROPIC_API_KEY=sk-ant-...

uv run celery -A app.scaling.celery_app worker \
  --loglevel=info \
  --concurrency=4 \
  -Q goals,goals.free,goals.starter,goals.professional,goals.enterprise,goals.persistence,schedules,maintenance,governance,goals_dlq

# Expected:
# [config]
# .> app:         agent_verse:0x...
# .> transport:   redis://localhost:6379/0
# .> results:     redis://localhost:6379/0
# .> concurrency: 4 (prefork)
# .> task events: OFF (enable -E to monitor tasks)
# [queues]
# .> goals             exchange=goals ...
# ...
# [2025-01-01 00:00:00,000: INFO/MainProcess] Connected to redis://localhost:6379/0
# [2025-01-01 00:00:00,000: INFO/MainProcess] celery@hostname ready.
```

```bash
# ── Terminal 3: Celery Beat (Scheduled Tasks) ───────────────────────────────
cd agent-verse-backend

uv run celery -A app.scaling.celery_app beat \
  --loglevel=info \
  --scheduler=redbeat.RedBeatScheduler

# Expected:
# [2025-01-01 00:00:00,000: INFO/MainProcess] beat: Starting...
# LocalTime -> 2025-01-01 00:00:00
```

### 5.5 Run Frontend Locally (Hot-reload)

```bash
# ── Terminal 4: Frontend Dev Server ────────────────────────────────────────
cd agent-verse-frontend

npm run dev

# Expected:
#   VITE v6.3.5  ready in 432 ms
#   ➜  Local:   http://localhost:5173/
#   ➜  Network: http://192.168.1.x:5173/
```

---

## 6. Database Migrations

### 6.1 Migration Commands

```bash
cd agent-verse-backend

# Apply all pending migrations (upgrade to latest)
uv run alembic upgrade head

# Apply one migration at a time
uv run alembic upgrade +1

# Rollback last migration
uv run alembic downgrade -1

# Rollback to specific revision
uv run alembic downgrade 0053

# Show current migration status
uv run alembic current

# Show full migration history
uv run alembic history --verbose

# Show pending migrations
uv run alembic history -r current:head

# Create a new migration (auto-generate from model changes)
uv run alembic revision --autogenerate -m "add_my_new_table"

# Create empty migration
uv run alembic revision -m "custom_sql_migration"
```

### 6.2 Migration Chain Reference

The full chain from 0001 to 0067:
```
0001-0009:   Core tables (tenants, api_keys, goals, agents, mcp_servers)
0010-0020:   Knowledge base, governance, scheduling tables
0021-0030:   RBAC, intelligence, cost ledger, vector indexes
0031-0040:   Audit immutability, evaluation, benchmarking
0041-0048:   Civilization tables, workflows, notifications, templates
0053:        Agent credentials (domain_context, RS256 keys)
0054:        RBAC v2 (custom_roles, role_assignments, api_key_scopes)
0055:        Guardrails (guardrail_configs, guardrail_violations partitioned)
0056:        Governance v2 (policy_versions, hitl_approval_requests)
0057:        Audit Rails v2 (audit_events partitioned, legal_holds, SIEM)
0058:        Cost optimization (model_pricing, budget_configs)
0059:        Marketplace v2 (templates, reviews, installs, security reviews)
0060:        Enterprise v2 (contracts, certifications, whitelabel, SCIM)
0061:        Self-improvement (improvement_experiments, results)
0062:        Knowledge v2 (variable dimensions: 768/1024/1536/3072, HNSW)
0063:        Civilization history (reputation_history, constitution_history)
0064:        Goal lineage (parent→child spawn tree)
0065:        Loop engineering (goal_attempts, goal_step_loops)
0066:        Tenant settings
0067:        Consent records v2
```

### 6.3 Verify Database Tables

```bash
# Connect to Postgres and list all tables
docker-compose -f infra/docker-compose.yml exec postgres \
  psql -U agentverse -d agentverse -c "\dt" | head -80

# Count tables (should be 70+)
docker-compose -f infra/docker-compose.yml exec postgres \
  psql -U agentverse -d agentverse -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public';"

# Check RLS is enabled on a table
docker-compose -f infra/docker-compose.yml exec postgres \
  psql -U agentverse -d agentverse -c "SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname='public' AND rowsecurity=true LIMIT 10;"
```

---

## 7. Running the Backend

### 7.1 Development Mode (with hot-reload)

```bash
cd agent-verse-backend

# Basic start
uv run uvicorn app.main:app --reload

# With specific host/port
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# With more workers (no reload in multi-worker mode)
uv run uvicorn app.main:app --workers 4 --host 0.0.0.0 --port 8000

# With log level
uv run uvicorn app.main:app --reload --log-level debug
```

### 7.2 Production Mode

```bash
cd agent-verse-backend

# Gunicorn with uvicorn workers (production)
uv run gunicorn app.main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --timeout 120 \
  --keepalive 5 \
  --max-requests 1000 \
  --max-requests-jitter 50
```

### 7.3 Backend Management Commands

```bash
cd agent-verse-backend

# Run the admin CLI
uv run agentverse --help

# Available CLI commands:
uv run agentverse run "Deploy my service to staging"   # submit a goal
uv run agentverse agents                               # list agents
uv run agentverse goals                                # list goals
uv run agentverse approve <request-id>                 # approve HITL
uv run agentverse replay <goal-id>                     # replay a goal
uv run agentverse logs <goal-id>                       # tail goal events
uv run agentverse simulate <goal>                      # dry-run simulation
uv run agentverse connectors                           # list connectors
uv run agentverse schedules                            # list schedules
uv run agentverse dev                                  # start dev server with mock tools

# Export OpenAPI schema
uv run python scripts/export_openapi.py > openapi.json

# Check what app sees for settings
uv run python -c "from app.core.config import get_settings; s=get_settings(); print(s.environment, s.database_url[:50])"
```

### 7.4 Backend Health Checks

```bash
# Basic health
curl http://localhost:8000/health
# Expected: {"status": "ok", "dependencies": {"postgres": "ok", "redis": "ok"}}

# Detailed health with latency
curl http://localhost:8000/health | python3 -m json.tool

# Prometheus metrics
curl http://localhost:8000/metrics | head -50

# OpenAPI schema
curl http://localhost:8000/openapi.json | python3 -m json.tool | head -30

# JWKS endpoint
curl http://localhost:8000/.well-known/jwks.json
```

---

## 8. Running the Frontend

### 8.1 Development Server

```bash
cd agent-verse-frontend

# Start Vite dev server (hot-reload)
npm run dev
# → http://localhost:5173

# Start on different port
npm run dev -- --port 3000

# Expose on network (accessible from other devices)
npm run dev -- --host 0.0.0.0
```

### 8.2 Production Build

```bash
cd agent-verse-frontend

# Type-check then build
npm run typecheck && npm run build

# Built files are in dist/
ls dist/
# index.html  assets/  ...

# Preview production build locally
npm run preview
# → http://localhost:4173
```

### 8.3 Frontend Configuration

```bash
cd agent-verse-frontend

# Set API URL for production build
VITE_API_URL=https://api.yourdomain.com npm run build

# Or in .env.local:
echo "VITE_API_URL=https://api.yourdomain.com" > .env.local
echo "VITE_WS_URL=wss://api.yourdomain.com" >> .env.local
echo "VITE_GRAFANA_URL=https://grafana.yourdomain.com" >> .env.local

npm run build
```

---

## 9. Running Celery Workers & Beat

### 9.1 Development Workers (Local)

```bash
cd agent-verse-backend

# ── All queues in one worker (development) ──────────────────────────────────
uv run celery -A app.scaling.celery_app worker \
  --loglevel=info \
  --concurrency=4 \
  -Q goals,goals.free,goals.starter,goals.professional,goals.enterprise,goals.persistence,schedules,maintenance,governance,goals_dlq

# ── Enterprise-only worker (isolated) ──────────────────────────────────────
uv run celery -A app.scaling.celery_app worker \
  --loglevel=info \
  --concurrency=8 \
  --hostname=enterprise@%h \
  -Q goals.enterprise

# ── Maintenance worker (background tasks only) ──────────────────────────────
uv run celery -A app.scaling.celery_app worker \
  --loglevel=info \
  --concurrency=2 \
  --hostname=maintenance@%h \
  -Q maintenance,governance

# ── Beat scheduler (periodic tasks) ────────────────────────────────────────
uv run celery -A app.scaling.celery_app beat \
  --loglevel=info \
  --scheduler=redbeat.RedBeatScheduler
```

### 9.2 Monitor Celery

```bash
cd agent-verse-backend

# Check active tasks
uv run celery -A app.scaling.celery_app inspect active

# Check registered tasks
uv run celery -A app.scaling.celery_app inspect registered

# Check queue depths
uv run celery -A app.scaling.celery_app inspect reserved

# Celery Flower UI (monitoring dashboard)
uv run pip install flower
uv run celery -A app.scaling.celery_app flower --port=5555
# → http://localhost:5555

# Purge a queue (clear all pending tasks)
uv run celery -A app.scaling.celery_app purge -Q goals.free

# Check Redis queue depths directly
redis-cli llen goals                   # tasks in goals queue
redis-cli llen goals.enterprise        # tasks in enterprise queue
```

---

## 10. Verifying Everything Works

### 10.1 Quick Sanity Check

```bash
# 1. Backend is up
curl -s http://localhost:8000/health | python3 -m json.tool
# Expected: {"status": "ok", ...}

# 2. Database connected
curl -s http://localhost:8000/health | python3 -c "import sys,json; h=json.load(sys.stdin); print('DB:', h['dependencies'].get('postgres', 'missing'))"
# Expected: DB: ok

# 3. Redis connected
curl -s http://localhost:8000/health | python3 -c "import sys,json; h=json.load(sys.stdin); print('Redis:', h['dependencies'].get('redis', 'missing'))"
# Expected: Redis: ok

# 4. Frontend loads
curl -s http://localhost:5173 | grep -o "<title>.*</title>"
# Expected: <title>AgentVerse</title>

# 5. Create a tenant
curl -s -X POST http://localhost:8000/tenants/signup \
  -H "Content-Type: application/json" \
  -d '{"name": "Test Org", "email": "test@example.com", "plan": "free"}' \
  | python3 -m json.tool
# Expected: {"tenant_id": "...", "name": "Test Org", "raw_key": "av_..."}
```

### 10.2 Full Stack Test

```bash
# Store API key from signup response
export API_KEY="av_..."  # Replace with actual key from signup

# List agents (should be empty initially)
curl -s http://localhost:8000/agents \
  -H "X-API-Key: $API_KEY" \
  | python3 -m json.tool
# Expected: []

# Create an agent
curl -s -X POST http://localhost:8000/agents \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "Test Agent", "autonomy_mode": "bounded-autonomous"}' \
  | python3 -m json.tool
# Expected: {"agent_id": "...", "name": "Test Agent", ...}

export AGENT_ID="..."  # From response

# Submit a goal (dry-run)
curl -s -X POST http://localhost:8000/goals \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"goal\": \"List all files in the current directory\", \"dry_run\": true}" \
  | python3 -m json.tool
# Expected: {"id": "...", "status": "planning", ...}
```

---

## 11. Service URLs & Credentials

### 11.1 All Services

| Service | URL | Default Credentials |
|---------|-----|-------------------|
| **Frontend** (React UI) | http://localhost:5173 | Create tenant via API or signup page |
| **Backend API** | http://localhost:8000 | `X-API-Key` header |
| **API Docs** (Swagger) | http://localhost:8000/docs | No auth needed |
| **API Docs** (ReDoc) | http://localhost:8000/redoc | No auth needed |
| **Health Check** | http://localhost:8000/health | No auth needed |
| **Prometheus Metrics** | http://localhost:8000/metrics | No auth needed |
| **Keycloak Admin** | http://localhost:8080 | `admin` / `admin` |
| **MinIO Console** | http://localhost:9001 | `minioadmin` / `minioadmin` |
| **MailPit** (email testing) | http://localhost:8025 | No auth needed |
| **Jaeger** (traces) | http://localhost:16686 | No auth needed |
| **Prometheus** | http://localhost:9090 | No auth needed |
| **Grafana** (dashboards) | http://localhost:3001 | `admin` / `agentverse` |
| **SearXNG** (web search) | http://localhost:8081 | No auth needed |
| **Celery Flower** | http://localhost:5555 | No auth (if started) |
| **PostgreSQL** | localhost:5432 | `agentverse` / `agentverse` DB: `agentverse` |
| **PgBouncer** | localhost:6432 | Same as PostgreSQL |
| **Redis** | localhost:6379 | No password in dev |

### 11.2 Direct Database Access

```bash
# Connect directly to PostgreSQL
psql postgresql://agentverse:agentverse@localhost:5432/agentverse

# Via PgBouncer (preferred, uses connection pooling)
psql postgresql://agentverse:agentverse@localhost:6432/agentverse

# Via Docker exec
docker-compose -f infra/docker-compose.yml exec postgres \
  psql -U agentverse -d agentverse

# Useful queries:
\dt                          # list all tables
\d agents                    # describe agents table
SELECT COUNT(*) FROM goals;  # count goals
SELECT * FROM tenants LIMIT 5;
```

### 11.3 Redis Direct Access

```bash
# Connect to Redis
redis-cli -h localhost -p 6379

# Useful commands:
PING                         # → PONG
KEYS *                       # list all keys (careful in production!)
KEYS "api_key:*"             # list API key cache entries
LLEN goals                   # pending goals queue depth
LLEN goals.enterprise        # enterprise queue depth
GET "jwks:cache"             # JWKS cache
INFO keyspace                # database stats
DBSIZE                       # total key count
```

---

## 12. Creating Your First Tenant & Goal

### 12.1 Sign Up via API

```bash
# Create a tenant account
curl -s -X POST http://localhost:8000/tenants/signup \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Organization",
    "email": "admin@myorg.com",
    "plan": "professional"
  }' | python3 -m json.tool

# Save the raw_key from response!
# Example: "raw_key": "av_7Xk2mN9pQ3vR8wT1yU6fA4bC5dE0gH..."
export API_KEY="av_7Xk2mN9pQ3vR8wT1yU6fA4bC5dE0gH..."
```

### 12.2 Sign Up via Frontend

1. Open http://localhost:5173
2. Click "Request access" on the login page (or navigate to `/onboarding`)
3. Follow the 4-step wizard:
   - **Step 1**: Configure LLM provider (select Anthropic, enter your key)
   - **Step 2**: Register a connector (optional, can skip)
   - **Step 3**: Create your first agent
   - **Step 4**: Submit your first goal

### 12.3 Create a Connector (MCP Tool Integration)

```bash
# Register a GitHub connector
curl -s -X POST http://localhost:8000/connectors \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "GitHub",
    "url": "https://api.github.com/mcp",
    "auth_type": "bearer",
    "auth_config": {
      "token": "ghp_YOUR_GITHUB_TOKEN"
    },
    "description": "GitHub API connector"
  }' | python3 -m json.tool

# Browse available connector templates
curl -s http://localhost:8000/connectors/catalog \
  -H "X-API-Key: $API_KEY" | python3 -m json.tool
```

### 12.4 Create an Agent

```bash
# Create a software engineering agent
curl -s -X POST http://localhost:8000/agents \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Senior Software Engineer",
    "autonomy_mode": "bounded-autonomous",
    "system_prompt": "You are an expert software engineer. When solving problems, always write clean, well-documented code. Prefer existing libraries over writing from scratch.",
    "goal_template": "Analyze the codebase and {task}",
    "max_iterations": 12,
    "domain_context": "engineering"
  }' | python3 -m json.tool

export AGENT_ID="..."  # From response
```

### 12.5 Submit Your First Goal

```bash
# Submit a real goal (requires LLM API key)
curl -s -X POST http://localhost:8000/goals \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"goal\": \"Search GitHub for the top 3 Python repositories by stars and summarize each one\",
    \"agent_id\": \"$AGENT_ID\",
    \"priority\": \"normal\"
  }" | python3 -m json.tool

export GOAL_ID="..."  # From response

# Stream real-time events
curl -s -N http://localhost:8000/goals/$GOAL_ID/stream \
  -H "X-API-Key: $API_KEY"
# Events appear in real-time:
# data: {"type": "goal_started", "goal_id": "..."}
# data: {"type": "plan_ready", "steps": [...]}
# data: {"type": "step_started", "description": "Search GitHub..."}
# data: {"type": "tool_call_complete", "tool_name": "github.search_repositories", ...}
# data: {"type": "goal_complete", "cost_usd": 0.0124}

# Check goal status
curl -s http://localhost:8000/goals/$GOAL_ID \
  -H "X-API-Key: $API_KEY" | python3 -m json.tool
```

### 12.6 Submit a Dry-Run (No LLM key needed)

```bash
# Preview what an agent would do without executing
curl -s -X POST http://localhost:8000/goals \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Create a comprehensive test plan for the login feature",
    "dry_run": true
  }' | python3 -m json.tool
# Returns: planned steps without executing
```

---

## 13. Running Tests

### 13.1 Backend Tests

```bash
cd agent-verse-backend

# ── Run all tests (recommended) ─────────────────────────────────────────────
uv run pytest

# ── Quick run (skip integration tests) ──────────────────────────────────────
uv run pytest -m "not integration and not slow"

# ── Specific test file ───────────────────────────────────────────────────────
uv run pytest tests/api/test_goals.py -v

# ── Specific test function ───────────────────────────────────────────────────
uv run pytest tests/api/test_goals.py::test_submit_goal_returns_202_accepted -v

# ── With coverage report ─────────────────────────────────────────────────────
uv run pytest --cov=app --cov-report=html
open htmlcov/index.html   # macOS
# or: xdg-open htmlcov/index.html

# ── Integration tests (requires running Docker) ──────────────────────────────
export DOCKER_HOST="unix:///Users/$(whoami)/.colima/default/docker.sock"
export TESTCONTAINERS_RYUK_DISABLED=true
uv run pytest -m integration -v

# ── Watch mode (re-run on file changes) ──────────────────────────────────────
uv run pytest-watch

# ── Parallel execution (faster) ──────────────────────────────────────────────
uv run pytest -n auto -m "not integration"
```

### 13.2 Backend Lint & Type Check

```bash
cd agent-verse-backend

# Lint (ruff)
uv run ruff check .

# Lint + auto-fix
uv run ruff check . --fix

# Type check (mypy strict)
uv run mypy app

# Format check
uv run ruff format --check .

# Format fix
uv run ruff format .

# Run all quality checks
uv run ruff check . && uv run mypy app && echo "All checks passed!"
```

### 13.3 Frontend Tests

```bash
cd agent-verse-frontend

# ── Unit/component tests (Vitest) ───────────────────────────────────────────
npm run test

# ── Watch mode ───────────────────────────────────────────────────────────────
npm run test:watch

# ── Specific file ────────────────────────────────────────────────────────────
npm run test -- src/features/goals/GoalsListPage.test.tsx

# ── With coverage ────────────────────────────────────────────────────────────
npm run test -- --coverage

# ── TypeScript check ─────────────────────────────────────────────────────────
npm run typecheck

# ── Lint ─────────────────────────────────────────────────────────────────────
npm run lint
```

### 13.4 End-to-End Tests (Playwright)

```bash
cd agent-verse-frontend

# Prerequisite: Backend must be running at localhost:8000
# Start backend first (Option A or B above)

# Install Playwright browsers (first time)
npx playwright install --with-deps chromium

# ── Run all E2E tests ────────────────────────────────────────────────────────
npm run test:e2e

# ── Run specific spec file ───────────────────────────────────────────────────
npx playwright test e2e/goals.spec.ts

# ── Run with UI (interactive) ────────────────────────────────────────────────
npm run test:e2e:ui

# ── Run in headed mode (see the browser) ────────────────────────────────────
npx playwright test --headed

# ── Run specific test by name ────────────────────────────────────────────────
npx playwright test --grep "can submit a goal"

# ── Debug a test ────────────────────────────────────────────────────────────
npx playwright test --debug e2e/goals.spec.ts

# ── Generate HTML report ─────────────────────────────────────────────────────
npx playwright test --reporter=html
npx playwright show-report
```

### 13.5 Full Test Suite (All Together)

```bash
# Backend: 2669 tests
cd agent-verse-backend
uv run pytest -m "not integration and not slow" -q && echo "✅ Backend tests passed"

# Frontend: 258 tests
cd agent-verse-frontend
npm run test -- --reporter=dot && echo "✅ Frontend tests passed"

# TypeScript
npm run typecheck && echo "✅ TypeScript clean"

# Backend lint
cd agent-verse-backend
uv run ruff check . && echo "✅ Backend lint passed"

# E2E (optional — requires running platform)
cd agent-verse-frontend
npm run test:e2e && echo "✅ E2E tests passed"
```

---

## 14. SDK & CLI Usage

### 14.1 Python SDK

```bash
cd agent-verse-sdk-python

# Install
pip install -e .
# or via uv:
uv run pip install -e .

# Use the CLI
agentverse --help

# Set API key
export AGENTVERSE_API_KEY=av_...
export AGENTVERSE_URL=http://localhost:8000

# Submit a goal
agentverse run "Analyze the top 5 Python repositories on GitHub"

# List goals
agentverse goals

# List agents
agentverse agents

# Approve a HITL request
agentverse approve <request-id>

# Stream goal events
agentverse logs <goal-id>

# Simulate a goal (dry-run)
agentverse simulate "Deploy my application"
```

```python
# Python SDK usage in code
from agentverse import AgentVerseClient

client = AgentVerseClient(
    api_key="av_...",
    base_url="http://localhost:8000"
)

# Submit a goal
goal = client.goals.submit(
    goal="Search GitHub for the top Python repositories",
    agent_id="agent-uuid",  # optional
    dry_run=False
)
print(f"Goal ID: {goal.goal_id}, Status: {goal.status}")

# Stream events
for event in client.goals.stream(goal.goal_id):
    print(f"Event: {event.type} - {event.payload}")

# Approve HITL
client.governance.approve_request(
    request_id="req-uuid",
    approver="my-user",
    note="Approved after review"
)
```

### 14.2 TypeScript SDK

```bash
cd agent-verse-sdk-typescript

# Install
npm install

# Build
npm run build

# Run tests
npm test
```

```typescript
// TypeScript SDK usage
import { AgentVerseClient } from '@agentverse/sdk';

const client = new AgentVerseClient({
  apiKey: 'av_...',
  baseUrl: 'http://localhost:8000',
});

// Submit a goal
const goal = await client.goals.submit({
  goal: 'Analyze GitHub repositories',
  priority: 'normal',
});

// Stream events
const stream = await client.goals.stream(goal.goal_id);
for await (const event of stream) {
  console.log(event.type, event.payload);
}

// Emergency stop
await client.emergencyStop();
```

### 14.3 GitHub Action

```yaml
# .github/workflows/deploy.yml
name: Deploy via AgentVerse

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Deploy via AgentVerse Agent
        uses: ./agent-verse-github-action  # or published action
        with:
          api-key: ${{ secrets.AGENTVERSE_API_KEY }}
          base-url: https://your-agentverse.com
          goal: "Deploy commit ${{ github.sha }} to staging and run smoke tests"
          agent-id: ${{ vars.DEPLOY_AGENT_ID }}
          timeout-minutes: "30"
```

---

## 15. Grafana & Observability

### 15.1 Access Dashboards

```bash
# Open Grafana
open http://localhost:3001
# Login: admin / agentverse

# Navigate to pre-built dashboards:
# Home → Dashboards → AgentVerse folder
# ├── AgentVerse Overview    — Goals, Agents, Success Rate, Latency
# ├── AgentVerse Costs       — LLM costs, token usage per tenant
# └── AgentVerse Reliability — Circuit breakers, retry rates, errors
```

### 15.2 Prometheus Queries

```bash
# Open Prometheus
open http://localhost:9090

# Useful queries to try:
# Goals submitted in last hour:
agentverse_goals_total

# Success rate:
rate(agentverse_goals_total{status="complete"}[5m]) / rate(agentverse_goals_total[5m])

# Active goals right now:
agentverse_active_goals

# LLM cost per tenant:
sum by (tenant_id) (agentverse_llm_cost_usd)

# P99 goal duration:
histogram_quantile(0.99, rate(agentverse_goal_duration_seconds_bucket[5m]))

# Guardrail violations:
rate(agentverse_guardrail_violations_total[5m])

# Celery queue depth:
celery_queue_length{queue="goals.enterprise"}
```

### 15.3 Jaeger Traces

```bash
# Open Jaeger UI
open http://localhost:16686

# Search for traces:
# Service: agentverse-backend
# Operation: POST /goals (goal submission)
# Operation: AgentGraph.run (agent execution)
# Operation: MCPClient.call_tool (tool calls)

# Find slow traces (> 5 seconds):
# Min Duration: 5000ms
```

### 15.4 Log Tailing

```bash
# Backend logs (Docker mode)
docker-compose -f infra/docker-compose.yml logs -f backend

# Worker logs
docker-compose -f infra/docker-compose.yml logs -f worker

# Filter logs for specific goal
docker-compose -f infra/docker-compose.yml logs backend 2>&1 | grep "goal_id.*g-1234"

# All services
docker-compose -f infra/docker-compose.yml logs -f

# Structured JSON logs (local mode)
uv run uvicorn app.main:app 2>&1 | python3 -m json.tool
```

---

## 16. Troubleshooting

### 16.1 Docker Issues

```bash
# Port already in use
sudo lsof -i :8000   # find process using port
sudo lsof -i :5432   # find process using postgres port
kill -9 <PID>        # kill it

# Container won't start
docker-compose -f infra/docker-compose.yml logs backend
# Look for: "Error" or "Exception"

# Out of disk space
docker system df      # check usage
docker system prune   # remove unused resources (careful!)

# Container keeps restarting
docker-compose -f infra/docker-compose.yml ps   # check STATUS
docker-compose -f infra/docker-compose.yml logs --tail=50 backend

# Reset everything
docker-compose -f infra/docker-compose.yml down -v   # removes volumes too!
docker-compose -f infra/docker-compose.yml up -d

# Colima out of memory
colima stop
colima start --cpu 6 --memory 12  # increase resources
```

### 16.2 Database Issues

```bash
# Cannot connect to database
# Check if postgres is running:
docker-compose -f infra/docker-compose.yml ps postgres

# Check postgres logs
docker-compose -f infra/docker-compose.yml logs postgres

# Reset database (DESTRUCTIVE - loses all data)
docker-compose -f infra/docker-compose.yml down -v postgres
docker-compose -f infra/docker-compose.yml up -d postgres
uv run alembic upgrade head

# Migration failed
uv run alembic current     # see current state
uv run alembic history     # see what's been applied
uv run alembic stamp head  # force-stamp as current (dangerous)

# "Multiple head revisions" error
uv run alembic heads       # see all heads
# Should output: 0067 (head) — only ONE head
# If multiple: check for duplicate revision IDs
grep -rn "^revision = " app/db/migrations/versions/ | sort | uniq -d

# Connection pool exhausted
# Increase PgBouncer pool size in docker-compose.yml:
# MAX_CLIENT_CONN: "2000"
# DEFAULT_POOL_SIZE: "100"
```

### 16.3 Backend Issues

```bash
# ImportError on startup
uv sync   # reinstall all dependencies
uv run python -c "import app.main"  # test import

# LLM not working
uv run python -c "
import os
from app.main import _resolve_provider_for_app
from app.core.config import get_settings
p = _resolve_provider_for_app(get_settings())
print(type(p).__name__)
"
# If it prints "FakeProvider", your API key isn't being read

# Check env var is loaded
uv run python -c "import os; print(os.environ.get('ANTHROPIC_API_KEY', 'NOT SET')[:10])"

# Alembic "Can't locate revision"
uv run alembic stamp base    # reset
uv run alembic upgrade head  # re-apply all

# Redis connection refused
redis-cli ping  # test locally
# if fails: docker-compose up -d redis

# "Event loop is closed" errors
# Ensure you're using uv run (Python 3.12), not system Python 3.9
uv run python --version  # should be 3.12.x
```

### 16.4 Frontend Issues

```bash
# npm install fails
rm -rf node_modules package-lock.json
npm install

# TypeScript errors
npm run typecheck 2>&1 | head -20
# Fix errors in src/

# Blank page after login
# Open browser DevTools → Console tab for errors
# Most common: API_KEY not set, backend not reachable

# Backend unreachable (CORS)
# Check: CORS_ORIGINS in backend .env includes http://localhost:5173
grep CORS_ORIGINS agent-verse-backend/.env

# Vite port conflict
npm run dev -- --port 3000
```

### 16.5 Celery Issues

```bash
# Tasks not being picked up
# Check worker is running:
uv run celery -A app.scaling.celery_app inspect ping

# Check queue the task is going to:
uv run celery -A app.scaling.celery_app inspect registered | grep run_goal

# Force-run a task manually
uv run python -c "
from app.scaling.tasks import run_goal
result = run_goal.delay(goal_id='test', tenant_id='test', goal_text='test', priority='normal', dry_run=True)
print(result.id)
"

# Celery worker crashed
uv run celery -A app.scaling.celery_app worker --loglevel=DEBUG

# Beat not firing tasks
uv run celery -A app.scaling.celery_app beat --loglevel=DEBUG
# Check: is RedBeat connecting to Redis?
```

---

## 17. Production Deployment

### 17.1 Docker Compose Production

```bash
cd agent-verse-backend

# Copy and edit production config
cp infra/docker-compose.prod.yml infra/docker-compose.local-prod.yml
# Edit: set real passwords, API keys, domains

# Set required environment variables
export POSTGRES_PASSWORD=strong_random_password_here
export REDIS_PASSWORD=another_strong_password
export SECRET_KEY=32_byte_random_secret
export ANTHROPIC_API_KEY=sk-ant-...
export KEYCLOAK_CLIENT_SECRET=strong_secret
export GRAFANA_PASSWORD=strong_password
export AGENTVERSE_VAULT_KEY=base64_encoded_32_byte_key
export FRONTEND_URL=https://app.yourdomain.com
export CORS_ORIGINS=https://app.yourdomain.com

# Start production stack
docker-compose -f infra/docker-compose.prod.yml up -d

# Run migrations
docker-compose -f infra/docker-compose.prod.yml exec backend \
  uv run alembic upgrade head
```

### 17.2 Kubernetes (Helm)

```bash
cd agent-verse-backend/infra/helm

# Add dependencies
helm dependency update agentverse/

# Install
helm install agentverse ./agentverse \
  --namespace agentverse \
  --create-namespace \
  --set backend.image.tag=latest \
  --set backend.env.ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  --set postgres.auth.password=$POSTGRES_PASSWORD \
  --set redis.auth.password=$REDIS_PASSWORD \
  --values production-values.yaml

# Upgrade
helm upgrade agentverse ./agentverse \
  --namespace agentverse \
  --values production-values.yaml

# Run migrations job
kubectl run alembic-migrate \
  --image=agentverse-backend:latest \
  --restart=Never \
  --namespace=agentverse \
  -- uv run alembic upgrade head

# Check status
kubectl get pods -n agentverse
kubectl logs -f deployment/agentverse-backend -n agentverse
```

### 17.3 Environment Variables for Production

```bash
# Required in production:
ENVIRONMENT=production
DATABASE_URL=postgresql+asyncpg://user:STRONG_PASS@db:5432/agentverse
REDIS_URL=redis://:STRONG_PASS@redis:6379/0
SECRET_KEY=<64-char random string>
AGENTVERSE_VAULT_KEY=<base64 32-byte key: python3 -c "import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())">
ANTHROPIC_API_KEY=sk-ant-...  # or another LLM key
FRONTEND_URL=https://app.yourdomain.com
CORS_ORIGINS=https://app.yourdomain.com
KEYCLOAK_CLIENT_SECRET=<strong_secret>
GRAFANA_PASSWORD=<strong_password>
MINIO_SECRET_KEY=<strong_secret>
```

---

## 18. Useful Shortcuts Reference

### 18.1 One-Liner Commands

```bash
# ── Start everything (Docker) ───────────────────────────────────────────────
colima start && cd agent-verse-backend && docker-compose -f infra/docker-compose.yml up -d

# ── Start infra only + run backend locally ──────────────────────────────────
colima start && docker-compose -f infra/docker-compose.yml up -d postgres redis pgbouncer minio && uv run uvicorn app.main:app --reload

# ── Full test suite ─────────────────────────────────────────────────────────
cd agent-verse-backend && uv run pytest -m "not integration and not slow" -q && cd ../agent-verse-frontend && npm run test -- --reporter=dot && npm run typecheck

# ── Quick health check ──────────────────────────────────────────────────────
curl -s http://localhost:8000/health | python3 -m json.tool

# ── Tail all Docker logs ─────────────────────────────────────────────────────
docker-compose -f infra/docker-compose.yml logs -f 2>&1 | grep -v "^$"

# ── Restart just the backend ─────────────────────────────────────────────────
docker-compose -f infra/docker-compose.yml restart backend

# ── Fresh database (DESTRUCTIVE) ─────────────────────────────────────────────
docker-compose -f infra/docker-compose.yml down -v && docker-compose -f infra/docker-compose.yml up -d && sleep 10 && docker-compose -f infra/docker-compose.yml exec backend uv run alembic upgrade head

# ── Check migration status ───────────────────────────────────────────────────
cd agent-verse-backend && uv run alembic current
```

### 18.2 Keyboard Shortcuts in the UI

| Shortcut | Action |
|---------|--------|
| `g d` | Go to Dashboard |
| `g g` | Go to Goals |
| `g a` | Go to Agents |
| `g t` | Go to Templates |
| `g k` | Go to Knowledge |
| `g r` | Go to Analytics |
| `g o` | Go to Observability |
| `Cmd+K` | Open Command Palette |
| `shift+?` | Show keyboard shortcuts overlay |

### 18.3 Useful API Shortcuts

```bash
# Set these once:
export AV_URL=http://localhost:8000
export AV_KEY=av_...  # Your API key

# Goal submission
alias av-goal='curl -s -X POST $AV_URL/goals -H "X-API-Key: $AV_KEY" -H "Content-Type: application/json"'

# Examples:
av-goal -d '{"goal": "Your goal here"}' | python3 -m json.tool
av-goal -d '{"goal": "Your goal", "dry_run": true}' | python3 -m json.tool

# Stream goal
av-stream() { curl -N $AV_URL/goals/$1/stream -H "X-API-Key: $AV_KEY"; }
# Usage: av-stream g-12345678

# Create tenant
av-tenant() { curl -s -X POST $AV_URL/tenants/signup -H "Content-Type: application/json" -d "{\"name\":\"$1\",\"email\":\"$2\",\"plan\":\"free\"}" | python3 -m json.tool; }
# Usage: av-tenant "My Org" "admin@myorg.com"
```

---

## Summary

| What you want | Command |
|--------------|---------|
| Start everything (Docker) | `colima start && docker-compose -f infra/docker-compose.yml up -d` |
| Run migrations | `docker-compose exec backend uv run alembic upgrade head` |
| Run backend locally | `uv run uvicorn app.main:app --reload` |
| Run frontend locally | `npm run dev` |
| Run Celery worker | `uv run celery -A app.scaling.celery_app worker --loglevel=info -Q goals,...` |
| Run Celery beat | `uv run celery -A app.scaling.celery_app beat --scheduler=redbeat.RedBeatScheduler` |
| Run all tests | `uv run pytest -m "not integration"` + `npm run test` |
| View API docs | http://localhost:8000/docs |
| View frontend | http://localhost:5173 |
| View Grafana | http://localhost:3001 (admin/agentverse) |
| View traces | http://localhost:16686 |
| Stop everything | `docker-compose -f infra/docker-compose.yml down` |
