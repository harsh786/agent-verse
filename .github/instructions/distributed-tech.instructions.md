---
applyTo: "agent-verse-backend/**/*.py,agent-verse-frontend/src/**/*.{ts,tsx},agent-verse-backend/helm/**"
---

# Distributed Technology Stack — Latest Proven Choices

## Technology Decision Matrix (Approved Stack)

### Backend Runtime & Framework
| Concern | Technology | Version | Why |
|---------|-----------|---------|-----|
| Runtime | Python | 3.12 | Latest stable, asyncio native |
| Web framework | FastAPI | ≥0.115 | Async-first, OpenAPI, Pydantic v2 |
| Validation | Pydantic | v2 | 10x faster than v1, model_config |
| AI orchestration | LangGraph | ≥0.2 | Stateful agent loops, checkpointing |
| Background jobs | Celery 5 + Redis | ≥5.4 | Proven, per-plan queue routing |
| Async HTTP client | httpx | ≥0.28 | Async-first, retry, connection pools |
| Package manager | uv | latest | 10–100x faster than pip |

### Database Layer
| Concern | Technology | Why |
|---------|-----------|-----|
| Primary store | **PostgreSQL 16** | ACID, JSONB, pgvector, RLS, partitioning |
| Vector search | **pgvector** (Postgres extension) | Collocated, no separate service |
| Cache + pub/sub | **Redis 7 Cluster** | Sub-ms, Streams, auto-expiry |
| Object storage | **S3 / MinIO** | Unlimited scale, presigned URLs |
| Full-text search | **Postgres GIN + pg_trgm** | Avoids Elasticsearch at this scale |
| Connection pool | **PgBouncer** (transaction mode) | 1000+ clients → 25 DB connections |
| Time-series (future) | **TimescaleDB** | When metrics need complex retention |
| Distributed cache (future) | **Redis Cluster** | When single Redis reaches limit |
| Graph queries (future) | **Apache AGE** (Postgres ext) | SQL + graph queries, same DB |
| High-volume events (future) | **Apache Kafka** | When Redis Streams reaches limit |

> **Scale trigger for Kafka**: Redis Streams handles well up to ~10K events/sec.
> Beyond that, evaluate Kafka with Schema Registry.

### AI/ML Stack
| Concern | Technology | Why |
|---------|-----------|-----|
| LLM provider | Anthropic Claude 3.5 Sonnet / OpenAI GPT-4o | Via LLMProvider abstraction |
| Embeddings | Voyage-3 (primary) / text-embedding-3-large | High quality, 1536 dim |
| LLM framework | **LangGraph 0.2+** | Stateful, checkpointable agent loops |
| Vector index | pgvector HNSW | ANN search, Postgres-native |
| Semantic cache | In-house SemanticCache (Redis + pgvector) | Saves 40%+ on LLM costs |

### Frontend
| Concern | Technology | Version | Why |
|---------|-----------|---------|-----|
| Framework | React | 19 | Concurrent features, Server Actions |
| Build | Vite | 6 | Sub-second HMR, ESM-native |
| State (server) | TanStack Query | 5 | Best-in-class server state |
| State (client) | Zustand | 5 | Lightweight, devtools |
| Styling | Tailwind CSS | 3 | Utility-first, design tokens |
| Animation | Framer Motion | 13 | JARVIS-style UI |
| Graph viz | @xyflow/react | 12 | Knowledge graph |
| Physics | d3-force | 3 | Force-directed layout |
| Collaboration | YJS + y-websocket | latest | CRDT real-time |
| i18n | i18next + react-i18next | latest | 20+ languages |
| Testing | Vitest 3 + Testing Library | latest | Fast, ESM-native |
| E2E | Playwright | latest | Cross-browser, visual regression |

### Infrastructure & DevOps
| Concern | Technology | Why |
|---------|-----------|-----|
| Container | Docker (multi-stage) | Minimal runtime image |
| Orchestration | Kubernetes + Helm 3 | Industry standard |
| Service mesh (future) | Istio / Linkerd | mTLS, traffic management |
| CI/CD | GitHub Actions | Integrated, fast |
| IaC | Terraform + AWS ECS | Reproducible infra |
| Secrets | AWS Secrets Manager / Vault | Never env vars for secrets |
| Registry | ghcr.io (GitHub Container Registry) | Free with GitHub |
| Image scanning | Grype + Cosign | SBOM, signed images |
| APM | OpenTelemetry → Jaeger + Prometheus | Vendor-neutral |
| Logging | structlog → Loki / ELK | Structured, searchable |
| Dashboards | Grafana | Unified metrics + logs + traces |

---

## Technology Decision Rules

### When to Add a New Technology

```
RULE: Before adding ANY new library or service, ask:
  1. Can Postgres + Redis solve this? (Answer is YES 90% of the time)
  2. Is there an existing library in pyproject.toml/package.json?
  3. Does the team have operational experience with this?
  4. Is it actively maintained (< 6 months since last release)?
  5. Does it have a clear migration path if we need to swap it?

If you must add something new:
  - Write an ADR (Architecture Decision Record) in docs/adr/
  - Get team approval before adding to pyproject.toml
  - Add to the approved list above
```

### Approved vs Prohibited Libraries

```python
# APPROVED Python libraries (already in pyproject.toml):
fastapi, pydantic, sqlalchemy, asyncpg, alembic, redis, celery
langgraph, anthropic, openai, structlog, opentelemetry-*
httpx, cryptography, python-jose, boto3, pgvector

# PROHIBITED (better alternatives exist in project):
requests          # use httpx (async-native)
django, flask     # use fastapi
pymongo, motor    # no MongoDB in this stack
elasticsearch     # use Postgres full-text search
aiofiles          # use anyio.open_file
loguru, logging   # use structlog
typing_extensions # use Python 3.12 native typing
```

```typescript
// APPROVED frontend libraries:
react@19, @tanstack/react-query@5, zustand@5
framer-motion@13, @xyflow/react@12, d3-force@3
tailwindcss@3, lucide-react, react-i18next
vitest@3, @testing-library/react, @playwright/test

// PROHIBITED (better alternatives exist):
axios           // use fetch or TanStack Query
redux, mobx     // use Zustand
styled-components, emotion  // use Tailwind
moment, date-fns // use Temporal API (native) or date-fns
lodash          // use native JS + lodash-es for tree-shaking
```
