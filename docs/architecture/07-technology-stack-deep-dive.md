# AgentVerse OS — Complete Technology Stack Reference

> **Document 07 of 07** | Every dependency, Docker image, and infrastructure service explained with architectural purpose

---

## Overview

AgentVerse OS is built on **100% open-source** technology. This document explains every single dependency — from the Python packages in `pyproject.toml` to the Docker images in `docker-compose.yml` — covering what each does, why it was chosen, and how it fits architecturally into the platform.

---

## Part I: Docker Infrastructure Services

### 1. `pgvector/pgvector:pg16` — PostgreSQL + Vector Extension

**Image**: `pgvector/pgvector:pg16`  
**Ports**: `5432` (direct), `6432` (via PgBouncer)  
**Volumes**: `pgdata:/var/lib/postgresql/data`

**What it is**: PostgreSQL 16 with the `pgvector` extension pre-installed. PostgreSQL is the world's most advanced open-source relational database.

**Why AgentVerse needs it**:
- **Primary data store**: Every tenant, agent, goal, knowledge chunk, audit event, and credential lives here
- **Vector similarity search**: `pgvector` adds a `VECTOR(N)` column type and HNSW/IVFFlat indexes for semantic search across knowledge bases — eliminating the need for a separate vector database like Pinecone or Weaviate
- **Row-Level Security (RLS)**: PostgreSQL's RLS enforces tenant isolation at the database layer — even if application code has bugs, cross-tenant data access is blocked
- **Hybrid search**: `pg_trgm` extension enables trigram fuzzy search combined with vector similarity in a single SQL query
- **ACID transactions**: Agent execution, goal creation, and knowledge ingestion require atomic operations — NoSQL databases cannot guarantee this
- **JSON/JSONB**: Flexible metadata storage (agent config, tool arguments, domain metadata) without schema migrations
- **Partitioning**: `audit_events` and `cost_ledger` tables partitioned by month for efficient time-range queries at scale
- **Full-text search**: `to_tsvector` + GIN indexes for marketplace template search

**Alternative considered**: Separate vector DB (Pinecone, Weaviate) + PostgreSQL. Rejected because two databases doubles operational complexity, eliminates ACID across the boundary, and breaks RLS tenant isolation.

---

### 2. `redis:7-alpine` — Cache + Message Broker + Pub/Sub

**Image**: `redis:7-alpine`  
**Port**: `6379`  
**Volumes**: `redisdata:/data`  
**Config**: `--appendonly yes` (AOF persistence)

**What it is**: Redis 7 — an in-memory data structure server. "Alpine" means minimal Linux base image (~7MB).

**Why AgentVerse needs it** (10 distinct use cases):

1. **API key resolution cache**: `api_key:{sha256_hash}` with 5-min TTL → O(1) auth without DB query on every request
2. **Role/scope cache**: `perm:{tenant}:{key_id}` with 5-min TTL → eliminates N+1 DB lookups for RBAC
3. **Celery message broker**: Tasks for goal execution, background maintenance, Celery beat scheduling
4. **Celery result backend**: Task completion status, return values
5. **HITL cross-replica delivery**: Redis `RPUSH`/`BLPOP` on `hitl_result:{request_id}` — approval on replica A unblocks agent on replica B
6. **Pub/Sub for policy invalidation**: `policy_changes` channel propagates policy updates to all replicas in <50ms
7. **JWKS cache**: `jwks:cache` with 10-min TTL for JWT public key set
8. **Audit WAL (Write-Ahead Log)**: `audit_wal:{tenant_id}` list — at-least-once delivery guarantee before Postgres commit
9. **Rate limiting**: Sorted sets for sliding-window rate limits per IP and per tenant
10. **LangGraph checkpointing**: `AsyncRedisSaver` stores agent execution state after every step — goals survive server restarts

**Why Redis over alternatives**:
- **vs Kafka**: Redis is dramatically simpler operationally. At AgentVerse's scale (<1B events/day), Redis is sufficient. Kafka requires ZooKeeper/KRaft, adds ~500ms cold-start latency, and is significantly harder to operate.
- **vs Memcached**: Missing sorted sets (rate limiting), pub/sub (policy invalidation), lists (WAL), persistence (AOF/RDB)
- **vs RabbitMQ**: Redis handles Celery + caching + pub/sub in one service; RabbitMQ is broker-only

---

### 3. `edoburu/pgbouncer:v1.25.2-p0` — Connection Pooling

**Image**: `edoburu/pgbouncer:v1.25.2-p0`  
**Port**: `6432`  
**Mode**: Transaction pooling

**What it is**: PgBouncer is a lightweight PostgreSQL connection pooler. It sits between the application and PostgreSQL, multiplexing many short-lived application connections onto a small number of persistent server connections.

**Why AgentVerse needs it**:
- PostgreSQL has a hard limit of ~100-200 simultaneous connections before performance degrades
- AgentVerse runs multiple API replicas + Celery workers, each with their own SQLAlchemy connection pool
- Without PgBouncer: 4 API replicas × 10 connections each + 8 workers × 5 connections = 80 connections minimum, growing linearly with scale
- With PgBouncer: All 80 application connections → 20 actual server connections (transaction pooling)
- `POOL_MODE=transaction`: Connection held only during active SQL transaction — connections return to pool between requests

**Configuration values**:
```
MAX_CLIENT_CONN=1000    # max clients connecting to PgBouncer
DEFAULT_POOL_SIZE=50    # max server connections per database
RESERVE_POOL_SIZE=10    # extra connections for spikes
```

---

### 4. `quay.io/keycloak/keycloak:24.0` — Identity & Access Management

**Image**: `quay.io/keycloak/keycloak:24.0`  
**Port**: `8080`  
**Command**: `start-dev --import-realm`

**What it is**: Keycloak is the world's most popular open-source Identity and Access Management (IAM) solution. It implements OpenID Connect, OAuth 2.0, SAML 2.0, and SCIM 2.0.

**Why AgentVerse needs it**:
- **SSO (Single Sign-On)**: Enterprise customers authenticate their users via Keycloak instead of creating separate AgentVerse accounts. One login for all corporate tools.
- **OIDC/JWT issuance**: Keycloak issues standard JWT tokens validated by AgentVerse's `python-jose` library
- **JWKS endpoint**: Keycloak publishes public keys at `/realms/{realm}/protocol/openid-connect/certs` — AgentVerse caches these for stateless JWT verification
- **SAML 2.0 broker**: Keycloak can bridge enterprise SAML IdPs (Active Directory, Okta, Azure AD) to OIDC
- **SCIM provisioning**: Automatic user creation/deactivation when employees join/leave the organization
- **Realm-based multi-tenancy**: Each `agentverse` realm contains all platform-level roles and clients
- **JIT provisioning**: First SSO login creates a new AgentVerse tenant automatically
- **`--import-realm`**: Starts with pre-configured realm from `infra/keycloak/realm-export.json` so zero manual setup is needed

**Why Keycloak over alternatives**:
- **vs Auth0**: Auth0 charges $0.07/MAU (Monthly Active User). At 100K users = $7,000/month. Keycloak is free.
- **vs Okta**: Enterprise pricing, cannot self-host
- **vs AWS Cognito**: AWS-only, lock-in, missing SCIM 2.0 in basic tier

---

### 5. `postgres:16-alpine` — Keycloak Database

**Image**: `postgres:16-alpine`  
**Internal service**: `keycloak-db`

**What it is**: A separate PostgreSQL instance exclusively for Keycloak's internal data (users, clients, sessions, realm configuration).

**Why separate from main DB**: Keycloak has its own schema managed by Hibernate (JPA). Mixing it with AgentVerse's schema would create Alembic migration conflicts and make it impossible to use RLS without complex configuration.

---

### 6. `minio/minio:latest` — Object Storage

**Image**: `minio/minio:latest`  
**Ports**: `9000` (API), `9001` (Console)  
**Credentials**: `minioadmin` / `minioadmin` (dev)

**What it is**: MinIO is an S3-compatible open-source object storage server. It implements the full AWS S3 API.

**Why AgentVerse needs it**:
- **RPA screenshots and recordings**: Playwright browser sessions generate screenshots/PDFs stored in MinIO as artifact files
- **GDPR data exports**: When a user requests their data, the async export job writes a ZIP file to MinIO and returns a download URL
- **Knowledge base uploads**: User-uploaded PDFs, DOCX, code files stored before ingestion processing
- **AI-generated artifacts**: Any file produced by agents (reports, generated code, analysis outputs) stored with TTL
- **Model checkpoints**: Fine-tuning data exports
- **Backup storage**: Database backups written to MinIO

**Why MinIO over AWS S3**: The platform can run completely locally without any cloud credentials. The S3-compatible API means swapping MinIO for real S3 in production requires only a URL change — no code changes.

---

### 7. `axllent/mailpit:latest` — Email Testing

**Image**: `axllent/mailpit:latest`  
**Ports**: `1025` (SMTP), `8025` (Web UI)

**What it is**: MailPit is a lightweight email testing tool. It acts as an SMTP server that catches all outbound emails and displays them in a web UI instead of actually delivering them.

**Why AgentVerse needs it**:
- **HITL email notifications**: When a human approval is required, AgentVerse emails the approver with one-click approve/reject links. In development, these go to MailPit instead of real email servers.
- **Goal completion notifications**: Email alerts when long-running goals complete or fail
- **Account verification emails**: New tenant signup confirmation
- **Budget alerts**: When spending approaches limits, email notifications sent
- **Zero configuration**: No real email credentials needed. All emails visible at http://localhost:8025

---

### 8. `otel/opentelemetry-collector-contrib:0.103.0` — Telemetry Aggregation

**Image**: `otel/opentelemetry-collector-contrib:0.103.0`  
**Port**: `4317` (OTLP gRPC receiver)  
**Config**: `infra/otel/otel-collector-config.yaml`

**What it is**: The OpenTelemetry Collector receives telemetry data (traces, metrics, logs) from applications and routes it to backends (Jaeger for traces, Prometheus for metrics).

**Why AgentVerse needs it**:
- **Single receiver**: All AgentVerse services send telemetry to one endpoint (`OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317`) regardless of backend
- **Trace routing**: Receives traces from FastAPI + Celery workers → forwards to Jaeger
- **Metric bridging**: Converts OTLP metrics to Prometheus exposition format
- **Vendor neutrality**: Switching from Jaeger to Zipkin or Datadog requires only changing collector config, not application code
- **Batching**: Collector buffers telemetry and sends in batches, reducing network overhead from application to backend

---

### 9. `jaegertracing/all-in-one:1.58` — Distributed Tracing

**Image**: `jaegertracing/all-in-one:1.58`  
**Port**: `16686` (Web UI)

**What it is**: Jaeger is an open-source distributed tracing system. It shows how requests flow through a distributed system by recording timing and causal relationships between operations.

**Why AgentVerse needs it**:
- **End-to-end goal tracing**: A single goal submission → API handler → Celery task → LangGraph execution → tool call spans all appear as one distributed trace
- **Latency debugging**: Find exactly which tool call or LLM request is slow
- **Error root cause**: When a goal fails, trace shows the exact line and service where the exception originated
- **Dependency maps**: Automatically discover which services call which
- **Spans for every LLM call**: Model name, token count, duration visible in Jaeger
- **MCP tool spans**: Every connector call traced with server_id, tool_name, duration

---

### 10. `searxng/searxng:latest` — Privacy-First Web Search

**Image**: `searxng/searxng:latest`  
**Port**: `8081`

**What it is**: SearXNG is an open-source, privacy-focused metasearch engine that aggregates results from 80+ search engines (Google, Bing, DuckDuckGo, etc.) without tracking users.

**Why AgentVerse needs it**:
- **Web search tool for agents**: When an agent needs to search the internet, it calls the `web_search` tool which hits SearXNG instead of directly calling Google's paid API
- **Privacy**: No user data sent to Google/Bing — all searches are proxied through SearXNG
- **No API key required**: SearXNG scrapes public search engines; no billing account needed
- **Multi-engine results**: Aggregates from multiple engines for more comprehensive results
- **Cost**: Google Custom Search API costs $5/1000 queries. SearXNG is free.

---

### 11. `prom/prometheus:v2.51.0` — Metrics Collection

**Image**: `prom/prometheus:v2.51.0`  
**Port**: `9090`  
**Config**: `infra/prometheus/prometheus.yml`

**What it is**: Prometheus is an open-source monitoring system with a time-series database. It scrapes `/metrics` endpoints from applications at regular intervals.

**Why AgentVerse needs it**:
- **Goal metrics**: `agentverse_goals_total{status, plan}` — track submission/completion rates
- **LLM cost tracking**: `agentverse_llm_cost_usd{model, tenant}` — real token costs
- **Queue depth**: `celery_queue_length{queue="goals.enterprise"}` — trigger autoscaling
- **Guardrail violations**: `agentverse_guardrail_violations_total{severity}` — security monitoring
- **Budget utilization**: `agentverse_budget_utilization{tenant}` — approaching limits
- **Civilization metrics**: `civ_agents_active`, `civ_spawns_total`, `civ_budget_spent_usd`
- **SLA compliance**: `agentverse_hitl_sla_violations_total` — governance monitoring
- **Alerting source**: Prometheus Alert Manager can trigger PagerDuty/Slack on thresholds

**Scrape targets**: backend (`:8000/metrics`), celery worker (`:9540/metrics`), PgBouncer (via postgres-exporter), Redis (via redis-exporter)

---

### 12. `grafana/grafana:10.4.0` — Metrics Visualization

**Image**: `grafana/grafana:10.4.0`  
**Port**: `3001` (mapped from container's 3000)  
**Credentials**: `admin` / `agentverse`

**What it is**: Grafana is the world's most popular open-source metrics visualization platform. It connects to Prometheus and renders charts, gauges, and dashboards.

**Why AgentVerse needs it**:
- **3 pre-built dashboards** (auto-provisioned from `infra/grafana/dashboards/`):
  - **AgentVerse Overview**: Goals submitted/hour, active goals, success rate, goal duration P99
  - **AgentVerse Costs**: Cost per tenant/hour, token usage breakdown by model
  - **AgentVerse Reliability**: Circuit breaker states, retry rates, error distribution
- **Business metrics for SLAs**: Enterprise customers need dashboard access to verify uptime SLAs
- **Real-time visibility**: Live charts update every 15 seconds as agents execute
- **Auto-provisioned datasource**: `infra/grafana/provisioning/datasources/prometheus.yml` configures Prometheus connection automatically — no manual setup

---

## Part II: Python Backend Dependencies

### Core Web Framework

#### `fastapi>=0.115.0` — Web Framework

**Purpose**: The HTTP framework that powers all 33+ API routers.

**Why FastAPI** (not Django or Flask):
- **Native async/await**: AgentVerse executes multiple concurrent LLM calls, DB queries, and Redis operations. WSGI frameworks (Django, Flask) block threads; FastAPI's ASGI model handles thousands of concurrent I/O operations with minimal threads.
- **Automatic OpenAPI**: Zero extra code → complete interactive API docs at `/docs` and `/redoc`
- **Pydantic integration**: Request/response validation is native — no separate serializer layer
- **`Depends()` dependency injection**: Services attached to `app.state` are accessible via `Depends()` without service locators or singletons
- **WebSocket & SSE support**: Goals stream real-time events via SSE; collaboration uses WebSocket — both are first-class in FastAPI

#### `uvicorn[standard]>=0.32.0` — ASGI Server

**Purpose**: The production-grade ASGI server that runs FastAPI.

**Why uvicorn**: Fastest Python ASGI server (based on uvloop + httptools). The `[standard]` extra adds WebSocket support (websockets library) required for civilization WebSocket and collaboration.

#### `pydantic[email]>=2.9.0` — Data Validation

**Purpose**: Validates all request/response data, settings, and internal models.

**Architecture role**: Every API input is a Pydantic model. Every configuration setting is a Pydantic `BaseSettings`. The `[email]` extra validates email fields in tenant signup. Pydantic v2 is 10-50× faster than v1 due to Rust core (pydantic-core).

#### `pydantic-settings>=2.6.0` — Configuration Management

**Purpose**: Loads `Settings` class from environment variables and `.env` files.

**Architecture role**: All 60+ configuration values (DATABASE_URL, API keys, feature flags) are typed and validated at startup. Missing required values raise clear errors before the app starts. Eliminates runtime `os.environ.get()` scattered through codebase.

---

### Database Layer

#### `sqlalchemy[asyncio]>=2.0.36` — ORM + Query Builder

**Purpose**: Object-Relational Mapper for all database interactions.

**Architecture role**:
- Defines all 70+ table models in `app/db/models/`
- `AsyncSession` enables non-blocking DB queries alongside async LLM calls
- Typed queries prevent SQL injection by design
- `text()` for raw SQL when ORM is insufficient (complex analytics, RLS SET LOCAL)
- Works with asyncpg for PostgreSQL

#### `asyncpg>=0.30.0` — PostgreSQL Async Driver

**Purpose**: Native PostgreSQL driver with full async/await support.

**Architecture role**: The fastest Python PostgreSQL driver (pure C, no GIL). Handles hundreds of concurrent queries from parallel agent executions. Required by SQLAlchemy for async PostgreSQL connections.

#### `alembic>=1.14.0` — Database Migrations

**Purpose**: Manages 67 sequential database schema migrations.

**Architecture role**: Every schema change (new table, new column, new index) goes through an Alembic migration. The migration chain (`0001 → 0067`) guarantees the schema is reproducible from scratch on any environment. `alembic upgrade head` applies all pending migrations in order.

#### `pgvector>=0.3.6` — Vector Operations in Python

**Purpose**: Python client for the pgvector PostgreSQL extension.

**Architecture role**: Enables storing and querying `vector` type columns in PostgreSQL. Used to store document embedding vectors in knowledge bases and perform `<=>` cosine similarity operations directly in SQL for semantic search.

---

### Cache & Task Queue

#### `redis>=5.2.0` — Redis Client

**Purpose**: Python client for Redis operations.

**Architecture role**: Used for 10 distinct purposes (see Docker section). Provides both sync (`redis.Redis`) and async (`redis.asyncio.Redis`) clients. The async client is used on FastAPI's hot path; sync client in Celery tasks.

#### `celery[redis]>=5.4.0` — Distributed Task Queue

**Purpose**: Executes all background tasks: goal execution, knowledge ingestion, maintenance jobs.

**Architecture role**:
- Goal execution: `run_goal` task submitted by API → picked up by worker → `AgentGraph.run()` executes
- Per-plan queue isolation: `task_routes` config sends free-tier goals to `goals.free` queue, enterprise to `goals.enterprise` — dedicated worker pools per tier
- Retry logic: Celery's built-in retry with exponential backoff for transient failures
- `[redis]` extra: enables Redis as both broker and result backend

#### `celery-redbeat>=2.3.0` — Distributed Cron Scheduler

**Purpose**: Replaces Celery's default Beat scheduler for production reliability.

**Architecture role**: Default Celery Beat runs only on one process — if it crashes, periodic tasks stop. RedBeat stores the schedule in Redis with distributed locking, so any worker can pick up beat duties if the primary fails. Required for production reliability of 20+ scheduled maintenance tasks.

---

### Agent Execution

#### `langgraph>=0.2.0` — Agent State Machine

**Purpose**: Implements the core agent execution loop as a directed graph.

**Architecture role**: AgentVerse's most critical dependency. LangGraph provides:
- State machine with typed `AgentState` flowing through nodes
- **Checkpointing**: `AsyncRedisSaver` saves state after every step — goals survive server restarts
- **Native HITL**: Built-in mechanism to pause execution and resume on external event
- **Cycle support**: The `replan → execute → verify` cycle is a graph with loops
- **Conditional routing**: After verification, different edges lead to `complete`, `replan`, `max_iter`, `waiting_human`

#### `langgraph-checkpoint-redis>=0.0.1` — Redis State Persistence

**Purpose**: `AsyncRedisSaver` for LangGraph to persist agent state in Redis.

**Architecture role**: Without this, all agent state lives only in memory. If the Celery worker process crashes mid-execution, the entire goal is lost. With this, every step is checkpointed to Redis. On worker restart, the goal resumes from the last saved step.

---

### HTTP Clients

#### `httpx>=0.28.0` — Async HTTP Client

**Purpose**: All outbound HTTP calls — MCP connector calls, LLM provider APIs, HITL email webhooks.

**Architecture role**: `httpx.AsyncClient` enables concurrent API calls without blocking the event loop. Used in 119 MCP connector servers to call external APIs (Jira, GitHub, Salesforce, etc.). Also used by `SAMLProvider` for IdP metadata fetching and `LegalHoldManager` for webhook notifications.

---

### Security & Cryptography

#### `cryptography>=44.0.0` — Cryptographic Primitives

**Purpose**: RSA key generation, AES encryption for the credential vault, HMAC signing.

**Architecture role**:
- **Agent credentials**: Generates RSA-2048 keypairs for JWT service account credentials
- **Credential vault**: AES-256-GCM encryption of MCP connector secrets (API keys, OAuth tokens)
- **HITL email links**: HMAC-SHA256 signing of one-click approve/reject URLs prevents tampering
- **SAML replay protection**: HMAC validation of assertion IDs

#### `python-jose[cryptography]>=3.3.0` — JWT Library

**Purpose**: Signs, verifies, and decodes JWTs (JSON Web Tokens).

**Architecture role**:
- **API authentication**: Validates Keycloak-issued OIDC tokens on every authenticated request
- **Agent service accounts**: Signs RS256 JWTs for agent credentials (15-min TTL)
- **JWKS validation**: Verifies JWT signatures using Keycloak's public keys from JWKS endpoint
- `[cryptography]` extra: enables RS256 (RSA asymmetric) in addition to HS256 (symmetric)

---

### LLM Providers

#### `anthropic>=0.50.0` — Claude Models

**Purpose**: Official Anthropic Python SDK for Claude Opus, Sonnet, and Haiku.

**Architecture role**: Primary LLM provider for planning, execution, and verification. Used by `AnthropicProvider` which extracts `usage.input_tokens`/`output_tokens` for real cost tracking. Claude Opus 4 is the default planner; Haiku 3-5 is used for fast/cheap guardrail judgement.

**Optional providers** (installed via extras):
- **`voyageai`** (`[voyage]`): Voyage AI embedding models — best-in-class semantic similarity for knowledge base search
- **`google-generativeai`** (`[gemini]`): Gemini Pro/Flash models and embedding API
- **`sentence-transformers`** (`[local-embed]`): Local CPU embeddings — zero API cost, works completely offline

---

### Observability

#### `structlog>=24.4.0` — Structured Logging

**Purpose**: All application logging in JSON format with consistent fields.

**Architecture role**: Every log line is structured JSON with consistent fields (`goal_id`, `tenant_id`, `tool_name`, `duration_ms`). Enables log aggregation (ELK, Datadog, CloudWatch) to filter/search by any field. Contrast with `print()` or `logging.info()` which produce unstructured strings.

#### `opentelemetry-api`, `opentelemetry-sdk` — Telemetry SDK

**Purpose**: OpenTelemetry standard for traces and metrics.

**Architecture role**: Vendor-neutral telemetry. The same instrumentation code sends traces to Jaeger today and Datadog tomorrow — just by changing the exporter. Traces span the entire goal execution from HTTP request through Celery → LangGraph → MCP → LLM.

#### `opentelemetry-instrumentation-fastapi` — Auto-Instrumentation

**Purpose**: Automatically instruments all FastAPI endpoints with traces.

**Architecture role**: Zero code changes required. Every HTTP request automatically gets a trace span with method, path, status code, and duration. Links to child spans created by SQLAlchemy, httpx, and Redis.

#### `opentelemetry-exporter-otlp-proto-grpc` — Trace Exporter

**Purpose**: Sends traces to the OpenTelemetry Collector via gRPC.

**Architecture role**: Efficient binary encoding (Protocol Buffers) for trace export. gRPC is 3-10× faster than HTTP/JSON for high-volume telemetry.

#### `prometheus-client>=0.21.0` — Metrics Exposition

**Purpose**: Exposes `/metrics` endpoint for Prometheus scraping.

**Architecture role**: Defines and increments custom counters/histograms/gauges. `agentverse_goals_total`, `agentverse_llm_cost_usd`, `civ_agents_active` are all defined here. Prometheus scrapes this endpoint every 15 seconds.

---

### Scheduling & Utilities

#### `croniter>=1.4.0` — Cron Expression Parser

**Purpose**: Parses cron expressions for scheduled agent triggers.

**Architecture role**: Users can schedule agents with cron syntax ("run every Monday at 9am EST"). `croniter` validates the expression, calculates next execution times, and powers the `NLScheduler` that converts natural language ("every weekday morning") to cron specs.

#### `boto3>=1.40.61` — AWS SDK

**Purpose**: AWS S3 operations (via MinIO's S3-compatible API) and optional native AWS services.

**Architecture role**: `aioboto3` (async wrapper, in `[artifacts]` extra) stores RPA screenshots, GDPR exports, and agent-generated files. Using boto3 means the same code works against MinIO locally and real AWS S3 in production — zero code changes.

#### `python-multipart>=0.0.32` — File Upload

**Purpose**: Parses `multipart/form-data` HTTP requests for file uploads.

**Architecture role**: Knowledge base PDF/DOCX file uploads use multipart forms. Without this, FastAPI cannot receive binary file uploads.

#### `pyyaml>=6.0` — YAML Parser

**Purpose**: Parses YAML configuration files.

**Architecture role**: Governance policies can be exported/imported as YAML. OpenAPI connector specs stored as YAML. Helm chart values files.

#### `aioimaplib>=1.1` — Async IMAP

**Purpose**: Email-to-goal listener — monitors an IMAP inbox for goal submission emails.

**Architecture role**: Enterprise feature: users can submit agent goals by sending an email. The IMAP listener (`IMAP_ENABLED=true`) polls the inbox, parses goal text from email body, and submits to GoalService.

#### `aiosmtplib>=3.0` — Async SMTP

**Purpose**: Sends outbound emails (HITL notifications, goal completion alerts).

**Architecture role**: Non-blocking email sending. When a HITL approval is needed, `NotificationService` calls `aiosmtplib.send()` asynchronously without blocking the event loop. Connects to MailPit in dev, real SMTP in production.

---

### Optional Extras

#### `playwright>=1.40.0` (`[browser]`) — Browser Automation

**Purpose**: Controls real web browsers for RPA operations.

**Architecture role**: Powers all 20 RPA operations (`rpa_open_url`, `rpa_click`, `rpa_screenshot`, etc.). Installed as optional because most deployments don't need RPA and Playwright requires downloading browser binaries (~200MB).

#### `pypdf>=5.0` (`[pdf]`) — PDF Processing

**Purpose**: Extracts text from PDF files for knowledge base ingestion.

**Architecture role**: When a user uploads a PDF to a knowledge collection, `pypdf` extracts text page-by-page. Then `chunk_by_tokens()` splits it into 512-token chunks for embedding.

#### `python-docx>=1.1` (`[docx]`) — DOCX Processing

**Purpose**: Extracts text from Microsoft Word documents.

**Architecture role**: Same pipeline as PDF — DOCX → text extraction → chunking → embedding → vector storage.

#### `aioboto3>=13.0` (`[artifacts]`) — Async AWS/MinIO

**Purpose**: Async version of boto3 for non-blocking S3 operations.

**Architecture role**: Storing/retrieving RPA artifacts, GDPR exports, and generated files without blocking FastAPI's event loop.

---

### Development Dependencies

#### `pytest>=8.3.0` — Test Framework

**Purpose**: Runs all 2,669 backend tests.

**Architecture role**: Test discovery, execution, assertion, and fixture management. `pytest-asyncio` enables `async def test_*()` functions for testing async FastAPI handlers.

#### `testcontainers[postgres,redis]>=4.9.0` — Integration Testing

**Purpose**: Spins up real PostgreSQL and Redis containers during integration tests.

**Architecture role**: Zero mocking in production paths. Integration tests run against real databases, ensuring migrations apply correctly, RLS actually works, and Redis pub/sub behaves as expected. Containers start fresh for each test suite and are destroyed after.

#### `ruff>=0.8.0` — Linter + Formatter

**Purpose**: Fast Python linter (replaces flake8, isort, pyupgrade, pep8 in one tool).

**Architecture role**: Enforces code style, catches bugs, and auto-fixes issues. Rules: `E,F,I,N,UP,B,A,C4,SIM,RUF`. 100 character line length. Runs in CI to block merges with lint errors.

#### `mypy>=1.13.0` — Static Type Checker

**Purpose**: Verifies type annotations across the entire codebase.

**Architecture role**: `strict` mode catches type errors before runtime. Particularly important for a platform that handles multi-tenant data — type errors can cause wrong tenant's data to be returned.

#### `httpx2>=2.4.0` — HTTP Testing Client

**Purpose**: Async HTTP client for Starlette's `TestClient`.

**Architecture role**: FastAPI's `TestClient` requires an ASGI-compatible HTTP client. `httpx2` is the maintained fork that works with modern Starlette versions.

---

## Part III: Frontend Dependencies

### Core Framework

#### `react@19.1.0` — UI Framework

**Purpose**: The JavaScript library for building user interfaces.

**Architecture role**: React 19 introduces concurrent rendering — UI remains responsive during streaming simulation updates and real-time goal event processing. `useTransition`, `useDeferredValue`, and `lazy()` + `Suspense` are used extensively for performance.

#### `typescript@5.8.3` — Type Safety

**Purpose**: Adds static typing to JavaScript.

**Architecture role**: Strict TypeScript (0 errors) prevents runtime errors in a complex application with 436+ features. TypeScript interfaces for all API responses (in `client.ts`) ensure frontend components always use correct field names.

#### `vite@6.3.5` — Build Tool

**Purpose**: Development server with instant hot-module replacement + optimized production builds.

**Architecture role**: Native ESM (no bundling in dev), sub-100ms HMR for rapid iteration. `lazy()` imports create separate chunks so the initial bundle is small — heavy pages (GoalDNA, Civilization) are loaded on-demand.

---

### State Management

#### `@tanstack/react-query@5.80.7` — Server State

**Purpose**: Manages all server-side data fetching, caching, and background refetching.

**Architecture role**: Every API call is a `useQuery` or `useMutation` hook. Automatic background refetch (polling active goals every 5 seconds), stale-while-revalidate for instant perceived performance, and query invalidation on mutations keep the UI in sync without manual cache management.

#### `zustand@5.0.5` — Client State

**Purpose**: Lightweight global state management.

**Architecture role**: 5 stores:
- `authStore`: API key + SSO tokens + JWT claims (sessionStorage-backed)
- `uiStore`: theme (dark/light), sidebar state (localStorage-backed)
- `toastStore`: notification queue
- `agentLabStore`: simulation session state (mock tools persisted to localStorage)
- `civilizationStore`: live civilization events + reputation history

---

### Routing & Navigation

#### `react-router-dom@7.6.2` — Client-Side Routing

**Purpose**: Declarative routing for 43+ pages without page reloads.

**Architecture role**: React Router v7 provides `<Route>`, `useNavigate()`, `useParams()`, and nested layouts. The `RequireAuth` wrapper redirects unauthenticated users. All 43+ pages are registered as routes with lazy loading.

---

### Data Visualization

#### `recharts@2.15.3` — Charts

**Purpose**: Composable React chart components.

**Architecture role**: Powers all charts (cost over time, tool usage, eval scores). Custom `ThemedLineChart`, `ThemedBarChart`, `ThemedRadarChart` wrappers use CSS variables for dark mode compatibility.

#### `@xyflow/react@12.11.1` — Interactive Graph Canvas

**Purpose**: Drag-and-drop node/edge graph rendering.

**Architecture role**: Used for 4 distinct features:
1. **Workflow Builder**: Create agent workflows visually
2. **Goal DNA**: Visualize execution as a force-graph
3. **Goal Lineage Tree**: Show parent→child spawn relationships
4. **Civilization Map**: Display agent network topology

#### `d3-force@3.0.0` — Physics Simulation

**Purpose**: Force-directed graph layout algorithms.

**Architecture role**: `AgentOrbitView` on the Mission Control dashboard uses d3-force to animate agents orbiting around a central core, with forces keeping them spread out. Also used in `KnowledgeGraph` for document-concept relationship visualization.

#### `d3-selection@3.0.0` — DOM Manipulation

**Purpose**: D3's imperative DOM selection and modification API.

**Architecture role**: Works alongside d3-force to imperatively update SVG elements during physics simulation (updating `cx`, `cy` on circles; `x1/y1/x2/y2` on lines).

---

### UI Components

#### `lucide-react@0.511.0` — Icon Library

**Purpose**: 511 React SVG icons.

**Architecture role**: All UI icons throughout the application. Icons are SVG-based (sharp at any size), tree-shakeable (only imported icons included in bundle), and consistent in style.

#### `clsx@2.1.1` — Class Name Utility

**Purpose**: Conditionally join CSS class names.

**Architecture role**: Used everywhere for conditional Tailwind class application:
```typescript
className={clsx("base-class", isActive && "active-class", isError && "error-class")}
```

#### `tailwindcss@3.4.17` — CSS Framework

**Purpose**: Utility-first CSS framework.

**Architecture role**: All styling uses Tailwind utility classes. Dark mode via `dark:` prefix. CSS custom properties (`hsl(var(--background))`) for theme tokens. JIT mode generates only used classes — production CSS is ~15KB.

#### `autoprefixer@10.4.21` + `postcss@8.5.4` — CSS Processing

**Purpose**: Autoprefixer adds vendor prefixes; PostCSS transforms CSS.

**Architecture role**: Required by Tailwind CSS pipeline. Autoprefixer ensures CSS works across all browsers without manual `-webkit-`, `-moz-` prefixes.

---

### Developer Tools

#### `react-hotkeys-hook@5.3.3` — Keyboard Shortcuts

**Purpose**: Declarative keyboard shortcuts in React components.

**Architecture role**: Powers the `useAppHotkeys` hook: `g+d` (go to Dashboard), `g+g` (go to Goals), `Cmd+K` (command palette), `?` (keyboard help overlay). Uses the `useHotkeys()` hook pattern.

#### `diff@9.0.0` — Text Diffing

**Purpose**: Computes differences between two text strings.

**Architecture role**: `GoalDiffPage` uses this to show a side-by-side comparison of two goal executions — which tool calls changed, which outputs differ.

---

### Testing

#### `vitest@3.2.4` — Test Runner

**Purpose**: Fast unit/component test runner for Vite projects.

**Architecture role**: Runs all 258 frontend tests. Vitest uses Vite's same transformation pipeline — imports, TypeScript, and CSS modules work identically in tests and in the browser.

#### `@testing-library/react@16.3.0` — Component Testing

**Purpose**: Tests React components as users interact with them.

**Architecture role**: Tests find elements by accessible role/label (not CSS classes), click buttons, type text, and assert visible text — ensuring components work as users experience them, not just that they render.

#### `@testing-library/user-event@14.5.2` — User Interaction Simulation

**Purpose**: Simulates realistic browser user events (click, type, tab).

**Architecture role**: More realistic than `fireEvent` — simulates actual pointer events, keyboard sequences, and browser focus behavior.

#### `@playwright/test@1.49.1` — E2E Testing

**Purpose**: End-to-end browser automation for testing complete user flows.

**Architecture role**: 29 spec files, 203 tests that run in a real Chromium browser. Each test mocks the backend API (`page.route()`) and verifies real user interactions: submitting goals, viewing agent details, configuring guardrails.

#### `jsdom@26.1.0` — Browser Environment

**Purpose**: Simulates browser DOM for component tests.

**Architecture role**: Vitest runs in Node.js but components need a browser environment. jsdom provides `window`, `document`, `localStorage`, `sessionStorage`, and other browser APIs without actually running Chrome.

---

## Part IV: Missing Capabilities — Filled

After reviewing all existing documentation, the following world-class capabilities were present in the codebase but not fully documented:

### A. Semantic Cache (LLM Call Deduplication)

**What it does**: Before every LLM call, AgentVerse computes the embedding of the input text and checks Redis for a semantically similar cached response. If a cached response exists with cosine similarity > 0.95, it is returned without calling the LLM.

**Architecture value**: For agents processing similar goals repeatedly (e.g., a support agent handling "how do I reset my password?" variations), the semantic cache eliminates 40-80% of LLM calls, dramatically reducing costs.

**Code location**: `app/rag/semantic_cache.py` — `SemanticCache` class with `get()`, `set()`, `key_from_embedding()`

### B. Tool Reliability Memory

**What it does**: After every tool call, AgentVerse records the outcome (success/failure, latency) in `tool_reliability_memory`. The executor uses this history to prefer tools with higher historical success rates.

**Architecture value**: Self-healing over time — tools that fail frequently get deprioritized automatically without human intervention.

**Code location**: `app/memory/tool_reliability.py`

### C. Collaboration Sessions (Real-Time Multi-User)

**What it does**: Multiple users can simultaneously edit agent configurations, review goal outputs, and collaborate on knowledge base curation via WebSocket-based real-time sessions with operational transform conflict resolution.

**Architecture value**: Teams can work on the same agent simultaneously, similar to Google Docs. Version conflicts are resolved server-side.

**Code location**: `app/collab/store.py` — `CollaborationStore` with `apply_operation()`, `get_current_state()`, optimistic concurrency via `expected_version` parameter

**Frontend**: `/collaboration` page with `useCollabSocket()` hook for WebSocket connection with exponential backoff reconnection

### D. Agent-to-Agent (A2A) Protocol

**What it does**: AgentVerse implements the emerging [Agent-to-Agent (A2A) protocol](https://google.github.io/A2A/) for inter-agent communication across different platforms.

**Architecture value**: An AgentVerse agent can call an agent hosted on another platform (LangGraph Cloud, AutoGPT, etc.) using a standardized protocol. Enables true agent ecosystems.

**Code location**:
- `app/mcp/a2a.py` — A2A dispatcher
- `app/api/a2a.py` — `GET /.well-known/agent.json` (agent card), `GET /a2a/tasks/{id}`
- `app/civilization/a2a_dispatch.py` — Internal civilization A2A

### E. Red Team Testing Framework

**What it does**: Built-in adversarial test suite that automatically tests agents against known attack patterns — prompt injection, jailbreaks, data exfiltration attempts, privilege escalation.

**Architecture value**: Catch security issues before deployment. "Does my customer service agent leak other customers' data when asked cleverly?" — test this automatically, not manually.

**Code location**: `app/enterprise/red_team.py` — `RedTeamRunner` with 5+ adversarial test cases, runs against `GuardrailEngine` + real agent loop

**Frontend**: Agent Lab `/lab?tab=score` → Red Team section with case management

### F. Evaluation Suites (Regression Testing)

**What it does**: Users define a set of "golden tasks" (known inputs with expected outputs), run them against their agents, and track how evaluation scores change over releases.

**Architecture value**: Prevents regressions. Before releasing a new agent version, run the eval suite — if avg score drops from 0.87 to 0.72, reject the release.

**Code location**: `app/intelligence/eval_suite.py` — `EvalSuiteRunner` with `run_suite()`, `get_suite_results()`

**API**: `GET /intelligence/eval-suites`, `POST /intelligence/eval-suites`, `POST /intelligence/eval-suites/{id}/run`

### G. Simulation (Pre-Flight Governance Check)

**What it does**: Before executing a goal, users can simulate the governance outcome — which tools would be blocked, which require approval, how much it would cost — without actually running the agent.

**Architecture value**: "Would this goal violate our compliance policies?" → answer in <1 second before committing to execution.

**Code location**: `app/enterprise/simulation.py` — `SimulationRunner` with `MockMCPClient` for tool simulation

**API**: `POST /enterprise/simulation` (blocking), `POST /enterprise/simulation/stream` (SSE streaming)

### H. Training Data Export

**What it does**: Exports completed goal executions as structured training data for fine-tuning LLMs. Format: input (goal + context) → output (successful tool call sequences + final answer).

**Architecture value**: AgentVerse becomes a flywheel — better agents generate better training data → fine-tuned models → even better agents.

**Code location**: `app/api/training_export.py` — exports in OpenAI fine-tuning JSONL format and Anthropic format

**Frontend**: `/training-export` page

### I. Perception Module (Vision)

**What it does**: Analyzes screenshots and images using vision-capable LLMs. Used by RPA auto-healing and can be invoked directly by agents.

**Architecture value**: Enables visual understanding — "what does this dashboard show?", "is this form filled correctly?", "what error message appears on screen?"

**Code location**: `app/perception/browser_agent.py`, `app/perception/page_analyzer.py`, `app/perception/multimodal.py`

**Frontend**: `/perception` page with status, screenshot, analyze, and extract tools

### J. Emergency Stop

**What it does**: One-click mechanism to immediately cancel ALL running goals for a tenant and prevent new goals from starting.

**Architecture value**: "The agent is doing something wrong and we need to stop everything RIGHT NOW." Critical safety feature for production deployments.

**Code location**: `app/api/governance.py` — `POST /governance/emergency-stop` endpoint + TopBar UI button

### K. Goal Benchmarking

**What it does**: Tracks agent performance on standardized benchmark tasks over time, generating trend data that shows whether agents are improving or regressing.

**Code location**: `app/intelligence/benchmarking.py` — `BenchmarkStore` with per-agent scorecard accumulation and trend detection

### L. Prompt Optimizer (A/B Variants)

**What it does**: The `PromptOptimizer` registers alternative system prompts as A/B variants. Each goal randomly receives one variant. Mann-Whitney U test determines statistical significance after sufficient runs. Winner gets promoted to the agent's default prompt.

**Architecture value**: Continuous improvement without manual tuning. The system automatically discovers the best prompts through controlled experimentation.

**Code location**: `app/intelligence/prompt_optimizer.py` — epsilon-greedy selection, statistical significance testing, DB-backed variant registry

---

## Summary Table

| Component | Category | Purpose |
|-----------|---------|---------|
| pgvector/pgvector:pg16 | Docker | Primary DB + vector search |
| redis:7-alpine | Docker | Cache + broker + pub/sub |
| edoburu/pgbouncer | Docker | Connection pooling |
| quay.io/keycloak | Docker | SSO + SAML + SCIM |
| postgres:16-alpine | Docker | Keycloak's database |
| minio/minio | Docker | Object storage (S3-compatible) |
| axllent/mailpit | Docker | Email testing (dev) |
| otel/opentelemetry-collector | Docker | Telemetry aggregation |
| jaegertracing/all-in-one | Docker | Distributed tracing |
| searxng/searxng | Docker | Privacy-first web search |
| prom/prometheus | Docker | Metrics collection |
| grafana/grafana | Docker | Metrics visualization |
| fastapi | Python | Web framework |
| uvicorn | Python | ASGI server |
| pydantic + pydantic-settings | Python | Validation + config |
| sqlalchemy[asyncio] | Python | ORM |
| asyncpg | Python | PostgreSQL async driver |
| alembic | Python | DB migrations |
| pgvector | Python | Vector type support |
| redis | Python | Redis client |
| celery[redis] | Python | Task queue |
| celery-redbeat | Python | Distributed cron |
| langgraph | Python | Agent state machine |
| langgraph-checkpoint-redis | Python | Agent state persistence |
| httpx | Python | Async HTTP client |
| cryptography | Python | RSA/AES crypto |
| python-jose | Python | JWT operations |
| anthropic | Python | Claude LLM |
| structlog | Python | Structured logging |
| opentelemetry-* | Python | Observability |
| prometheus-client | Python | Metrics exposition |
| croniter | Python | Cron expressions |
| boto3 | Python | S3/MinIO client |
| playwright | Python | Browser automation |
| pypdf, python-docx | Python | Document parsing |
| react@19 | Frontend | UI framework |
| typescript | Frontend | Type safety |
| vite | Frontend | Build tool |
| @tanstack/react-query | Frontend | Server state |
| zustand | Frontend | Client state |
| react-router-dom | Frontend | Client routing |
| recharts | Frontend | Charts |
| @xyflow/react | Frontend | Graph canvas |
| d3-force, d3-selection | Frontend | Physics simulation |
| lucide-react | Frontend | Icons |
| tailwindcss | Frontend | Styling |
| react-hotkeys-hook | Frontend | Keyboard shortcuts |
| vitest | Frontend | Unit tests |
| @playwright/test | Frontend | E2E tests |
| @testing-library/react | Frontend | Component tests |

**Total dependencies**: ~45 Python packages + 25 npm packages + 12 Docker services = **82 components**, every one open source, every one serving a specific architectural purpose.
