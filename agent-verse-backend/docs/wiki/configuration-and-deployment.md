---
title: Configuration & Deployment
description: Environment variables, Docker Compose service topology, production deployment checklist, Kubernetes architecture, health checks, and monitoring setup for AgentVerse.
outline: deep
---

# Configuration & Deployment

AgentVerse follows strict 12-factor app principles. Every tunable behavior is exposed as an environment variable, the same Docker image runs in development and production, and the application refuses to start with insecure defaults in production mode.

## Docker Compose Service Topology

The `infra/docker-compose.yml` orchestrates the complete local development stack. The same services map 1:1 to Kubernetes resources in production.

```mermaid
graph TB
    subgraph Storage ["💾 Storage Layer"]
        PG["🐘 postgres:pg16<br>+ pgvector extension<br>port 5432"]:::success
        PGB["🔄 pgbackup<br>daily/weekly/monthly dumps<br>7d / 4w / 6m retention"]:::neutral
        PGBouncer["⚡ pgbouncer:1.25.2<br>transaction pooling<br>50 pool / 1000 max clients<br>port 6432"]:::warning
        RD["🔴 redis:7-alpine<br>AOF persistence<br>port 6379"]:::success
        RDS1["🔴 redis-sentinel-1<br>HA failover<br>port 26379"]:::neutral
        RDS2["🔴 redis-sentinel-2<br>port 26380"]:::neutral
        RDS3["🔴 redis-sentinel-3<br>port 26381"]:::neutral
        MINIO["📦 minio<br>S3-compatible object store<br>port 9000/9001"]:::neutral
    end

    subgraph Auth ["🔐 Auth Layer"]
        KC["🔐 keycloak<br>SSO / OIDC<br>port 8080"]:::warning
    end

    subgraph App ["🏗 Application Layer"]
        BE["🏗 backend<br>FastAPI + uvicorn<br>port 8000"]:::primary
        CW["⚙ celery-worker<br>goal execution<br>per-plan queues"]:::primary
        CB["⏱ celery-beat<br>cron scheduler<br>NLScheduler triggers"]:::neutral
    end

    subgraph Observability ["📊 Observability"]
        OTEL["📊 otel-collector<br>OTLP gRPC port 4317<br>HTTP port 4318"]:::neutral
        JAE["🔍 jaeger<br>distributed tracing<br>port 16686"]:::neutral
        PROM["📈 prometheus<br>metrics scrape<br>port 9090"]:::neutral
        GRAF["📉 grafana<br>dashboards<br>port 3000"]:::neutral
        LOKI["📋 loki<br>log aggregation"]:::neutral
    end

    subgraph Comms ["📨 Communication"]
        MAIL["📧 mailpit<br>SMTP catch-all dev<br>port 1025/8025"]:::neutral
        SEARCH["🔍 searxng<br>web search tool<br>port 8080"]:::neutral
    end

    FE["🖥 frontend<br>React + Vite<br>port 5173"]:::primary

    PG --> PGB
    PG --> PGBouncer
    PGBouncer --> BE
    RD --> RDS1
    RD --> RDS2
    RD --> RDS3
    RD --> BE
    RD --> CW
    KC --> BE
    MINIO --> BE
    OTEL --> JAE
    OTEL --> PROM
    PROM --> GRAF
    LOKI --> GRAF
    MAIL --> BE
    SEARCH --> BE
    BE --> CW
    BE --> CB
    FE --> BE

    style PG fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style RD fill:#4a2e2e,stroke:#d45b5b,color:#e0e0e0
    style BE fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style CW fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style CB fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style FE fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style KC fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style PGBouncer fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style OTEL fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style JAE fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style PROM fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style GRAF fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style LOKI fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style MINIO fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style MAIL fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style SEARCH fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style PGB fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style RDS1 fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style RDS2 fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style RDS3 fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
```

<!-- Sources:
  agent-verse-backend/infra/docker-compose.yml:1-200
  agent-verse-backend/Dockerfile:1-55
-->

### Starting the Stack

```bash
# Minimum viable stack (Postgres + Redis only — fastest for development)
colima start
docker-compose -f infra/docker-compose.yml up -d postgres redis

# Full stack including observability, Keycloak, Minio
docker-compose -f infra/docker-compose.yml up -d

# Redis Sentinel HA (activate the sentinel profile)
docker-compose -f infra/docker-compose.yml --profile sentinel up -d

# Apply migrations and start the backend
cd agent-verse-backend
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

## Environment Variable Reference

All variables are loaded by [`app/core/config.py`](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/app/core/config.py) via `pydantic-settings`. The `Settings` class resolves secrets through `app.core.secrets.read_secret()` so the same image works with env vars in development and mounted secret files in production.

### Core Application

| Variable | Type | Default | Required | Description |
|---|---|---|---|---|
| `ENVIRONMENT` | `development\|staging\|production` | `development` | ✅ | Enables production safety checks |
| `APP_NAME` | `str` | `AgentVerse` | — | Service display name |
| `DEBUG` | `bool` | `false` | — | FastAPI debug mode (never use in production) |
| `LOG_LEVEL` | `str` | `INFO` | — | `DEBUG\|INFO\|WARNING\|ERROR` |
| `CORS_ORIGINS` | `list[str]` | `["http://localhost:5173"]` | ✅ prod | Comma-separated allowed origins |
| `FRONTEND_URL` | `str` | `http://localhost:5173` | — | For email links and redirects |

### Database

| Variable | Type | Default | Required | Description |
|---|---|---|---|---|
| `DATABASE_URL` | `str` | `postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse` | ✅ | asyncpg DSN. Production rejects the default credentials. |
| `DB_POOL_SIZE` | `int` | `10` | — | SQLAlchemy pool size |
| `DB_MAX_OVERFLOW` | `int` | `5` | — | Extra connections above pool_size |
| `DB_POOL_TIMEOUT` | `float` | `30.0` | — | Seconds to wait for a connection |
| `DB_POOL_MAX` | `int` | `20` | — | Max asyncpg pool connections |
| `DB_POOL_MIN` | `int` | `5` | — | Min asyncpg pool connections |
| `DB_POOL_PRE_PING` | `bool` | `true` | — | Test connections before use |
| `DB_POOL_RECYCLE` | `int` | `1800` | — | Recycle connections after N seconds |

### Redis

| Variable | Type | Default | Required | Description |
|---|---|---|---|---|
| `REDIS_URL` | `str` | `redis://localhost:6379/0` | ✅ | Single-node Redis DSN |
| `REDIS_PASSWORD` | `str` | `""` | ✅ prod | Shared Redis password (single, Sentinel, Cluster) |
| `REDIS_MAX_CONNECTIONS` | `int` | `50` | — | Connection pool size |
| `REDIS_SENTINEL_URLS` | `str` | `""` | HA | Comma-separated `host:port` for Sentinel HA |
| `REDIS_SENTINEL_MASTER` | `str` | `mymaster` | HA | Sentinel master name |
| `REDIS_CLUSTER_NODES` | `str` | `""` | Cluster | Comma-separated cluster nodes for horizontal sharding |

### LLM Providers

At least one API key must be set for real goal execution. Without any key, `FakeProvider` returns deterministic dummy responses (useful for tests, dangerous in production).

| Variable | Provider | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic Claude | Primary recommended provider |
| `OPENAI_API_KEY` | OpenAI GPT-4o | Compatible with any OpenAI-compatible endpoint |
| `GOOGLE_API_KEY` | Google Gemini | |
| `VOYAGE_API_KEY` | Voyage AI | Embedding provider for RAG |
| `DEFAULT_LLM_PROVIDER` | `str` (`anthropic`) | Which provider to use by default |
| `DEFAULT_MODEL` | `str` (`""`) | Specific model slug; empty = provider default |

### Observability

| Variable | Type | Default | Description |
|---|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `str` | `null` | OTLP gRPC endpoint (e.g. `http://otel-collector:4317`) |
| `SERVICE_NAME` | `str` | `agentverse-backend` | Reported to tracing/metrics backends |
| `METRICS_ENABLED` | `bool` | `true` | Expose `/metrics` Prometheus endpoint |

### SSO / Keycloak

| Variable | Type | Default | Description |
|---|---|---|---|
| `SSO_ENABLED` | `bool` | `false` | Enable Keycloak OIDC authentication |
| `KEYCLOAK_URL` | `str` | `http://keycloak:8080` | Keycloak base URL |
| `KEYCLOAK_REALM` | `str` | `agentverse` | Realm name |
| `KEYCLOAK_CLIENT_ID` | `str` | `agentverse-backend` | Client ID |
| `KEYCLOAK_CLIENT_SECRET` | `str` | `""` | Required in production when `SSO_ENABLED=true` |

### Agent Civilization

| Variable | Type | Default | Description |
|---|---|---|---|
| `CIVILIZATION_ENABLED` | `bool` | `false` | Master switch for civilization features |
| `CIVILIZATION_MAX_AGENTS_PER_TENANT` | `int` | `50` | Hard cap enforced by Constitution |
| `CIVILIZATION_MAX_SPAWN_DEPTH` | `int` | `5` | Maximum spawn tree depth |
| `CIVILIZATION_DEFAULT_BUDGET_USD` | `float` | `10.0` | Default budget per civilization |
| `CIVILIZATION_TICK_INTERVAL_SECONDS` | `int` | `30` | Governor health check interval |

### Feature Flags (Advanced RAG)

| Variable | Default | Description |
|---|---|---|
| `ENABLE_RAPTOR` | `true` | Hierarchical RAG clustering |
| `ENABLE_FLARE` | `true` | Forward-looking active retrieval |
| `ENABLE_SELF_RAG` | `true` | Self-reflective retrieval |
| `ENABLE_COLBERT` | `true` | Late-interaction reranking |
| `ENABLE_TREE_OF_THOUGHTS` | `true` | Tree-of-thought reasoning |
| `ISOLATED_AGENT_EXECUTION` | `false` | Route execution to isolated subprocess/Kubernetes runner |

## Docker: Multi-Stage Build

The [Dockerfile](https://github.com/harsh786/agent-verse/blob/main/agent-verse-backend/Dockerfile) uses a two-stage build to keep the runtime image lean:

```mermaid
graph LR
    B1["🏗 Stage 1: builder<br>ghcr.io/astral-sh/uv:python3.12<br>UV_COMPILE_BYTECODE=1<br>uv sync --frozen --no-dev"]:::warning
    B2["🚀 Stage 2: runtime<br>python:3.12-slim-bookworm<br>non-root user: app<br>PATH=/app/.venv/bin"]:::success
    PLAY["🎭 Playwright Chromium<br>installed as app user<br>ARG INSTALL_PLAYWRIGHT=true"]:::neutral
    CMD["🎬 CMD<br>alembic upgrade head &&<br>uvicorn app.main:app<br>--host 0.0.0.0 --port 8000"]:::primary

    B1 -->|"COPY --chown=app:app /app"| B2
    B2 --> PLAY
    B2 --> CMD

    style B1 fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style B2 fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style PLAY fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style CMD fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
```

<!-- Sources: agent-verse-backend/Dockerfile:1-55 -->

**Key design decisions:**
- **`UV_LINK_MODE=copy`**: avoids hardlink issues with Docker layer caching across filesystems
- **`--frozen`**: fails the build if `uv.lock` is out of sync with `pyproject.toml` — no surprise dep updates in CI
- **Playwright installed as `app` user**: browser binary lives at `/home/app/.cache/ms-playwright`; skip with `ARG INSTALL_PLAYWRIGHT=false` for non-RPA builds to save ~500MB
- **`alembic upgrade head` in CMD**: migrations run at container startup, ensuring the DB schema is always current before the first request

## Health Check Endpoints

| Endpoint | Method | Purpose | Response |
|---|---|---|---|
| `/health` | `GET` | **Liveness**: is the process running? Used by load balancers. | `200 {"status": "ok"}` always (never checks dependencies) |
| `/ready` | `GET` | **Readiness**: can the process serve traffic? Checks DB + Redis. | `200` if all deps healthy; `503` if any dep is down |
| `/metrics` | `GET` | Prometheus scrape endpoint | OpenMetrics text format |

```bash
# Kubernetes liveness probe — restart container if this fails
livenessProbe:
  httpGet:
    path: /health
    port: 8000
  initialDelaySeconds: 10
  periodSeconds: 30

# Kubernetes readiness probe — stop routing traffic if this fails
readinessProbe:
  httpGet:
    path: /ready
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 10
  failureThreshold: 3
```

**Why separate probes?** `/health` never checks external dependencies — a slow database should not cause a Kubernetes restart loop. `/ready` gates traffic to ensure new pods don't receive requests before their connection pools are warmed.

## Production Deployment Checklist

```mermaid
flowchart TD
    S1["1️⃣ Infrastructure<br>Postgres 16+ with pgvector<br>Redis 7+ with AOF<br>PgBouncer for connection pooling"]:::warning
    S2["2️⃣ Secrets<br>Rotate DB password from default<br>Set REDIS_PASSWORD<br>Generate KEYCLOAK_CLIENT_SECRET<br>Generate A2A_SHARED_SECRET"]:::danger
    S3["3️⃣ LLM Keys<br>Set ANTHROPIC_API_KEY or OPENAI_API_KEY<br>Set VOYAGE_API_KEY for embeddings<br>Verify FakeProvider is NOT active"]:::warning
    S4["4️⃣ Security<br>ENVIRONMENT=production<br>CORS_ORIGINS=https://yourdomain.com<br>SSO_ENABLED=true (recommended)<br>DEBUG=false"]:::danger
    S5["5️⃣ Observability<br>OTEL_EXPORTER_OTLP_ENDPOINT set<br>Prometheus scraping /metrics<br>Grafana dashboards imported<br>Alert rules configured"]:::neutral
    S6["6️⃣ Database<br>uv run alembic upgrade head<br>Verify pgvector extension installed<br>RLS policies active"]:::neutral
    S7["7️⃣ Workers<br>≥16 Celery workers (4 per plan tier)<br>Celery beat running for schedules<br>Worker health monitoring"]:::neutral
    S8["8️⃣ Validation<br>GET /health → 200<br>GET /ready → 200<br>Submit test goal → COMPLETED<br>Verify trace in Jaeger"]:::success

    S1 --> S2 --> S3 --> S4 --> S5 --> S6 --> S7 --> S8

    style S1 fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style S2 fill:#4a2e2e,stroke:#d45b5b,color:#e0e0e0
    style S3 fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style S4 fill:#4a2e2e,stroke:#d45b5b,color:#e0e0e0
    style S5 fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style S6 fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style S7 fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style S8 fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
```

### Minimum Production Requirements

| Component | Minimum | Recommended |
|---|---|---|
| PostgreSQL | 16+ with pgvector | 16+ with pgvector, PgBouncer, daily backups |
| Redis | 7+ | 7+ with AOF, Sentinel HA (3 nodes) |
| Python | 3.12 via uv | 3.12, pinned uv.lock |
| Backend replicas | 2 | 4+ behind load balancer |
| Celery workers | 16 (4 per plan tier) | 4×8 = 32 for high-throughput |
| CPU per worker | 1 vCPU | 2 vCPU (LLM calls are I/O heavy) |
| RAM per worker | 512 MB | 1 GB (embedding + context buffers) |
| Disk (Postgres) | 50 GB | 500 GB SSD with WAL archiving |

## Kubernetes Deployment Architecture

```mermaid
graph TB
    ING["🌐 Ingress / Load Balancer<br>TLS termination<br>nginx / Traefik"]:::primary
    SVC_BE["Service: backend<br>ClusterIP :8000"]:::neutral
    SVC_CEL["Service: celery-worker<br>no inbound — outbound only"]:::neutral
    DEP_BE["Deployment: backend<br>replicas: 4<br>resources: 0.5 CPU / 512Mi"]:::primary
    DEP_CW["Deployment: celery-worker<br>replicas: 16+<br>resources: 1 CPU / 1Gi"]:::primary
    DEP_CB["Deployment: celery-beat<br>replicas: 1 (singleton!)"]:::warning
    CM["ConfigMap: agentverse-config<br>non-sensitive settings"]:::neutral
    SEC["Secret: agentverse-secrets<br>DB_URL, REDIS_PASSWORD,<br>LLM keys, signing secrets"]:::danger
    PG_STS["StatefulSet: postgres<br>PVC: 200Gi SSD"]:::success
    RD_STS["StatefulSet: redis<br>PVC: 20Gi SSD"]:::success
    HPA["HPA: backend<br>min=2 max=20<br>CPU target: 70%"]:::neutral

    ING --> SVC_BE
    SVC_BE --> DEP_BE
    DEP_BE --> CM
    DEP_BE --> SEC
    DEP_CW --> CM
    DEP_CW --> SEC
    DEP_CB --> CM
    DEP_CB --> SEC
    DEP_BE --> PG_STS
    DEP_BE --> RD_STS
    DEP_CW --> PG_STS
    DEP_CW --> RD_STS
    HPA -.->|"scales"| DEP_BE

    style ING fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style DEP_BE fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style DEP_CW fill:#1e3a5f,stroke:#4a9eed,color:#e0e0e0
    style DEP_CB fill:#5a4a2e,stroke:#d4a84b,color:#e0e0e0
    style PG_STS fill:#2d4a3e,stroke:#4aba8a,color:#e0e0e0
    style RD_STS fill:#4a2e2e,stroke:#d45b5b,color:#e0e0e0
    style SEC fill:#4a2e2e,stroke:#d45b5b,color:#e0e0e0
    style CM fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style SVC_BE fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style SVC_CEL fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
    style HPA fill:#2d2d3d,stroke:#7a7a8a,color:#e0e0e0
```

<!-- Sources:
  agent-verse-backend/infra/helm/
  agent-verse-backend/infra/k8s/
-->

**Critical**: Celery Beat **must run as a singleton** (`replicas: 1`). Running multiple beat instances causes duplicate scheduled tasks. Use a leader election sidecar or a Kubernetes `Job` for cron-style scheduling if you need HA.

## Monitoring Dashboard Setup

### Key Metrics to Track

| Metric | Alert Threshold | Meaning |
|---|---|---|
| `agentverse_goals_submitted_total` | — | Goal throughput counter |
| `agentverse_goal_duration_seconds` (p95) | > 120s | Goal taking too long |
| `agentverse_goal_error_rate` | > 2% | Too many failures |
| `agentverse_llm_tokens_total` | budget-defined | Token consumption |
| `agentverse_tool_call_duration_seconds` (p99) | > 10s | Slow tool |
| `cb_{tenant_id}_{tool}:state` | `open` | Circuit breaker tripped |
| `agentverse_celery_queue_length` | > 100 per queue | Worker backlog |
| `agentverse_active_goals_gauge` | > tenant limit | Concurrency ceiling |
| `pg_stat_activity_count` | > 80% of pool | DB connection saturation |
| `redis_connected_clients` | > 80% of max | Redis connection saturation |

### Grafana Dashboard Setup

```bash
# Import community dashboards
# 1. Postgres: grafana.com/dashboards/9628
# 2. Redis: grafana.com/dashboards/11835
# 3. Celery: grafana.com/dashboards/10741
# 4. FastAPI: grafana.com/dashboards/14928

# Custom AgentVerse dashboard panels
docker-compose -f infra/docker-compose.yml up -d grafana
# Then navigate to http://localhost:3000 (admin/admin)
# Import infra/grafana/agentverse-dashboard.json
```

### Recommended Alert Rules (Prometheus)

```yaml
# infra/prometheus/alerts.yml
groups:
  - name: agentverse
    rules:
      - alert: HighGoalErrorRate
        expr: rate(agentverse_goal_errors_total[5m]) / rate(agentverse_goals_submitted_total[5m]) > 0.02
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Goal error rate > 2% for 5 minutes"

      - alert: CircuitBreakerOpen
        expr: agentverse_circuit_breaker_state{state="open"} == 1
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "Circuit breaker open for {{ $labels.tool_name }}"

      - alert: CeleryQueueBacklog
        expr: agentverse_celery_queue_length > 100
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Celery queue {{ $labels.queue }} has {{ $value }} pending tasks"

      - alert: DatabaseConnectionPoolSaturated
        expr: pg_stat_activity_count / pg_settings_max_connections > 0.8
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "PostgreSQL connection pool > 80% utilized"
```

## Common Operations

### Running Migrations

```bash
# Apply all pending migrations (runs automatically at container startup)
uv run alembic upgrade head

# Generate a new migration after model changes
uv run alembic revision --autogenerate -m "add_civilization_budget_column"

# Downgrade one revision (emergency rollback)
uv run alembic downgrade -1

# Check current migration state
uv run alembic current
uv run alembic history --verbose
```

### Scaling Workers

```bash
# Scale Celery workers horizontally (Kubernetes)
kubectl scale deployment celery-worker --replicas=32

# Check queue depths
celery -A app.scaling.celery_app inspect active_queues

# Purge a queue (emergency — data loss warning)
celery -A app.scaling.celery_app purge --queues goals.free
```

### Diagnosing a Stuck Goal

```bash
# Check goal status via API
curl -H "X-API-Key: av-..." https://{host}/goals/{goal_id}

# Check if a distributed lock is held
redis-cli GET "goal_lock:{goal_id}"

# Check circuit breaker state for a tool
redis-cli GET "cb:{tenant_id}:{tool_name}:state"

# Check pause/cancel signals
redis-cli GET "goal_paused:{goal_id}"
redis-cli GET "goal_cancelled:{goal_id}"

# View goal events in Jaeger
# Navigate to http://localhost:16686 → search by tag: goal_id={goal_id}
```

## Related Pages

| Page | Description |
|---|---|
| [Reliability & Infrastructure](reliability-and-infrastructure.md) | Circuit breakers, bulkheads, distributed locks — what the infrastructure supports |
| [SDKs & Integrations](sdk-and-integrations.md) | How to talk to the API from any language |
| [Agent Loop & LangGraph](agent-loop.md) | What runs inside the Celery workers |
| [Multi-Agent Civilization](multi-agent-civilization.md) | Infrastructure requirements for civilization features |
