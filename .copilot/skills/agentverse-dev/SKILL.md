# AgentVerse Development Skill — SKILL.md

## Purpose

This skill activates world-class code generation guardrails for the AgentVerse monorepo.
It enforces all engineering standards when generating backend (Python/FastAPI), frontend (React/TypeScript), database (PostgreSQL), and infrastructure (Kubernetes/Helm) code.

## When to Invoke

Invoke this skill when:
- Creating any new backend module or API endpoint
- Creating any new frontend feature or component
- Designing a new database schema or migration
- Adding observability, resilience, or security patterns
- Reviewing existing code for compliance
- Setting up infrastructure or DevOps configurations

## Stack Reference

```
Backend:   Python 3.12 · FastAPI 0.115 · LangGraph 0.2 · Celery 5 · SQLAlchemy 2 async
Database:  PostgreSQL 16 + pgvector · Redis 7 · Alembic migrations
Frontend:  React 19 · TypeScript · Vite 6 · TanStack Query 5 · Zustand 5 · Tailwind 3
Infra:     Docker · Kubernetes · Helm 3 · GitHub Actions · OpenTelemetry → Jaeger + Prometheus
```

## Instruction Files Active

| File | Applies To |
|------|-----------|
| `.github/instructions/backend.instructions.md` | `app/**/*.py` |
| `.github/instructions/database.instructions.md` | `app/db/**` |
| `.github/instructions/observability.instructions.md` | All Python |
| `.github/instructions/resilience.instructions.md` | All Python |
| `.github/instructions/security.instructions.md` | All Python |
| `.github/instructions/frontend.instructions.md` | `src/**/*.{ts,tsx}` |
| `.github/instructions/testing.instructions.md` | Test files |
| `.github/instructions/api-design.instructions.md` | Router files |

## Prompt Templates Available

| Prompt | Use For |
|--------|---------|
| `new-backend-module.prompt.md` | Full module: router+service+repo+schemas+models+tests |
| `new-db-migration.prompt.md` | Production-safe Alembic migration |
| `new-frontend-feature.prompt.md` | Full feature: page+components+hooks+tests |
| `new-agent-node.prompt.md` | LangGraph node with OTel + error handling |

## Agent Modes Available

| Agent | Use For |
|-------|---------|
| `backend-engineer.agent.md` | All backend Python code generation |
| `frontend-engineer.agent.md` | All frontend React/TypeScript code |
| `db-architect.agent.md` | Schema design, migrations, query optimization |

## Non-Negotiable Rules Summary

### Backend
1. Module structure: `router → service → repository → models → schemas → exceptions`
2. All ORM queries: async SQLAlchemy, always filter by `tenant_id`
3. All service methods: OTel span + structlog event
4. All external calls: circuit breaker + timeout + retry
5. All errors: RFC 7807 Problem Details format
6. All lists: cursor-based pagination (not offset)
7. All writes: idempotency key support
8. All tables: `id` (UUIDv7) + `tenant_id` + `created_at` + `updated_at` + RLS

### Frontend
1. Server state: TanStack Query ONLY (no useEffect fetching)
2. Client/UI state: Zustand (not useState for global state)
3. Writes: optimistic updates with rollback
4. Lists >50 items: useVirtualizer MANDATORY
5. Heavy routes: lazy() + Suspense + ErrorBoundary MANDATORY
6. Auth tokens: sessionStorage or HttpOnly cookie (NOT localStorage)
7. Styling: Tailwind only (no inline styles)
8. Types: no `any` — TypeScript strict mode

### Database
1. Every FK column: index (CONCURRENTLY)
2. All composite queries: composite index
3. All tenant tables: RLS policy
4. All new indexes: `CREATE INDEX CONCURRENTLY IF NOT EXISTS`
5. All DDL changes: zero-downtime (expand-contract for column changes)
6. Migrations: sequential NNNN_, reversible downgrade()

### Observability
1. Every service method: OTel span with tenant_id + entity_id attributes
2. Every Celery task: OTel span + structlog with task_id
3. Every LangGraph node: OTel span
4. All logging: structlog (never print())
5. Log event naming: `{domain}.{verb}.{state}` (e.g., `mission.create.done`)

### Resilience
1. All LLM calls: circuit breaker
2. All MCP calls: circuit breaker + timeout (30s)
3. Per-tenant agent execution: bulkhead (max 5 concurrent)
4. All state-changing operations: idempotency key
5. Singleton Celery jobs: distributed lock
6. Multi-step operations: saga with compensation

### Security
1. All endpoints: `Depends(get_tenant)` — never bypass auth
2. All DB queries: parameterized (never f-strings)
3. All secrets: from `settings.*` (never hardcoded)
4. All file uploads: MIME validation + size limit + path traversal check
5. Security events: always written to audit trail

## Verification Checklist (Before Completing Any Task)

```bash
# Backend:
cd agent-verse-backend
uv run ruff check app/<module>/     # 0 errors
uv run mypy app/<module>/           # 0 errors
uv run pytest tests/<module>/ -v   # all pass

# Frontend:
cd agent-verse-frontend
npm run typecheck                   # 0 errors
npm run lint                        # 0 errors
npm run test                        # all pass
```
