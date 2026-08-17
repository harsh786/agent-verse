# AgentVerse — Global Copilot Instructions

## Project Identity
AgentVerse is a **vendor-agnostic, multi-tenant AI Organization Operating System**.
Agents receive natural-language goals, plan autonomously, call real-world tools via MCP,
verify results, and replan on failure — with **zero hardcoded workflows**.

## Monorepo Layout (in scope)
| Directory | Stack | Purpose |
|-----------|-------|---------|
| `agent-verse-backend/` | Python 3.12 · FastAPI · LangGraph · Celery · Postgres+pgvector · Redis | API + AI engine |
| `agent-verse-frontend/` | React 19 · TypeScript · Vite · TanStack Query 5 · Zustand 5 · Tailwind | UI |
| `agent-verse-backend/helm/` | Helm 3 | Kubernetes deployment |
| `.github/workflows/` | GitHub Actions | CI/CD |

> **Out of scope**: `agent-verse-sdk-python/`, `agent-verse-sdk-typescript/`, `agent-verse-github-action/`

---

## ABSOLUTE CODING RULES (Never Violate)

### 1. Backend Module Contract
Every new backend module under `app/<domain>/` MUST have:
```
app/<domain>/
  __init__.py          # re-exports public surface only
  router.py            # FastAPI APIRouter — no business logic here
  service.py           # Business logic — NO direct DB calls, uses repository
  repository.py        # ALL SQLAlchemy queries live here
  schemas.py           # Pydantic v2 request/response models
  models.py            # SQLAlchemy ORM models (if domain owns tables)
  exceptions.py        # Domain-specific exceptions
```
- **Never** put SQLAlchemy queries in `router.py` or `service.py`
- **Never** put HTTP-level logic in `service.py`
- **Always** use `async def` for all I/O operations

### 2. Database: All Tables MUST Have
```python
class MyModel(Base):
    __tablename__ = "my_models"
    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    tenant_id   = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    # RLS: always via app.db.rls.rls_context()
```
- **Every** table needs `id`, `tenant_id`, `created_at`, `updated_at`
- **Every** FK column needs an index
- **Every** `status` / `type` column needs an index
- **Composite indexes** for all multi-column WHERE patterns
- **RLS** enforced on all tenant-scoped tables

### 3. OpenTelemetry — Always Instrument
```python
from opentelemetry import trace
tracer = trace.get_tracer(__name__)

async def my_operation():
    with tracer.start_as_current_span("domain.operation") as span:
        span.set_attribute("tenant_id", tenant_id)
        span.set_attribute("entity_id", entity_id)
        # ... work ...
        span.set_attribute("result.count", len(results))
```
- **Every** service method gets a span
- **Every** Celery task gets a span
- **Every** LangGraph node gets a span
- Span names: `{domain}.{operation}` e.g. `mission.create`, `graphify.build`

### 4. Error Responses — RFC 7807 Always
```python
from app.core.errors import problem_detail
# Returns: {"type": "...", "title": "...", "status": 4xx, "detail": "...", "request_id": "..."}
raise HTTPException(status_code=422, detail=problem_detail("validation-error", "Field required"))
```

### 5. API Endpoints — Always Follow
- Path: `/v1/{resource_plural}` (plural nouns, lowercase, hyphens)
- Method semantics: GET (read), POST (create), PUT (full replace), PATCH (partial), DELETE
- Always: `x_request_id: str = Header(default_factory=lambda: str(uuid4()))`
- Always: cursor-based pagination `?cursor=...&limit=20` (max 100)
- Always: `operation_id` on every route

### 6. Resilience — Always Apply to External Calls
```python
from app.reliability.circuit_breaker import CircuitBreaker
from app.reliability.bulkhead import Bulkhead
# Wrap every external I/O: LLM calls, MCP tool calls, webhook delivery
```

### 7. Testing Requirements
- **New service** → unit tests with `FakeProvider` or mocks
- **New endpoint** → integration test via `TestClient`
- **New migration** → migration smoke test
- Minimum: 1 happy path + 1 error case + 1 edge case per public method

### 8. Celery Tasks
```python
@celery_app.task(bind=True, name="domain.task_name", max_retries=3, default_retry_delay=60)
async def my_task(self, tenant_id: str, entity_id: str) -> dict:
    # Always idempotent — safe to retry
    # Always structured logs with task_id, tenant_id
```

### 9. Frontend Component Rules
- **Every** feature in `src/features/<domain>/`
- **Every** heavy route: `lazy(() => import(...))`  wrapped in `<Suspense>`
- **Every** section: `<ErrorBoundary name="...">`
- **Never** call API directly in component — use TanStack Query hooks
- **All** lists ≥ 50 items: `useVirtualizer` from `@tanstack/react-virtual`

### 10. No Code Duplication
- Check `app/core/`, `app/reliability/`, `app/tenancy/` before writing new utilities
- Check `src/components/ui/`, `src/lib/`, `src/hooks/` before writing frontend utils
- Shared types go in `app/core/types.py` (backend) or `src/lib/types.ts` (frontend)

---

## Tech Stack Reference

### Backend
- **Runtime**: Python 3.12, `uv` package manager
- **Framework**: FastAPI ≥ 0.115, Pydantic v2
- **ORM**: SQLAlchemy 2 async + asyncpg (NEVER use sync SQLAlchemy)
- **Migrations**: Alembic (naming: `NNNN_description.py`, sequential)
- **AI**: LangGraph ≥ 0.2, Anthropic, OpenAI (via `app/providers/`)
- **Queue**: Celery 5 + Redis, `celery-redbeat` for Beat
- **Cache**: Redis (via `app.tenancy.store.TenantScopedStore`)
- **Vector DB**: pgvector (via SQLAlchemy Column with `Vector(1536)`)
- **OTel**: opentelemetry-api/sdk + structlog + Prometheus
- **Linting**: ruff (line-length=100, target=py312)
- **Types**: mypy strict mode

### Frontend
- **Framework**: React 19, TypeScript strict
- **Build**: Vite 6
- **State**: TanStack Query 5 (server state) + Zustand 5 (client state)
- **Styling**: Tailwind CSS 3 + CSS custom properties (design tokens)
- **Animation**: Framer Motion 13
- **Graph**: @xyflow/react 12, d3-force 3
- **Collaboration**: YJS + y-websocket
- **i18n**: i18next + react-i18next
- **Testing**: Vitest 3 + @testing-library/react 16 + MSW + Playwright
- **Icons**: lucide-react (tree-shaken imports only)

### Infrastructure
- **Container**: Docker (multi-stage)
- **Orchestration**: Kubernetes + Helm 3 (charts in `helm/agentverse/`)
- **CI/CD**: GitHub Actions (`.github/workflows/`)
- **Monitoring**: OpenTelemetry → Jaeger (traces) + Prometheus (metrics) + structlog (logs)
- **Secrets**: Environment variables (12-factor) + Vault (production)

---

## Import Conventions

### Backend
```python
# CORRECT order:
from __future__ import annotations          # always first

import asyncio                               # stdlib
import uuid

from fastapi import APIRouter, Depends       # third-party
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings        # internal absolute
from app.tenancy.context import TenantContext
from .service import MyService              # relative (same domain)
```

### Frontend
```typescript
// CORRECT order:
import { useState, useCallback } from 'react';        // react
import { useQuery } from '@tanstack/react-query';     // third-party
import { Button } from '@/components/ui/button';      // internal shared
import { useMissions } from './hooks/useMissions';    // feature-local
import type { Mission } from './types';               // types last
```

---

## What Copilot Must NEVER Generate
1. Sync SQLAlchemy (`Session`) — always use `AsyncSession`
2. `import *` from any module
3. `print()` for logging — always `structlog.get_logger()`
4. Raw f-string SQL — always parameterized via SQLAlchemy ORM or `text()`
5. Secrets in code — always `settings.SECRET_NAME`
6. `localStorage` for auth tokens — always `sessionStorage` or HttpOnly cookie
7. Inline `<style>` in React — always Tailwind classes
8. `useEffect` for data fetching — always TanStack Query
9. Missing `tenant_id` on any DB write
10. Missing error boundary on any new page component
