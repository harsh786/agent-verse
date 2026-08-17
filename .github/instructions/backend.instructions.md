---
applyTo: "agent-verse-backend/**/*.py"
---

# Backend Coding Instructions — AgentVerse

## Module Structure (Enforce Strictly)

Every domain module under `app/<domain>/` follows this exact layout:

```
app/<domain>/
  __init__.py          # Exports public API only (service, schemas, router)
  router.py            # FastAPI router — thin, only HTTP concerns
  service.py           # Business logic — orchestrates repository calls
  repository.py        # All SQLAlchemy ORM queries (async only)
  schemas.py           # Pydantic v2 in/out models
  models.py            # SQLAlchemy ORM models (if domain owns tables)
  exceptions.py        # Typed domain exceptions
  tasks.py             # Celery tasks for this domain (if any)
  tests/               # Co-located unit tests
```

## FastAPI Router Pattern

```python
# app/<domain>/router.py
from __future__ import annotations
import uuid
from fastapi import APIRouter, Depends, Header, Query, status
from app.tenancy.context import TenantContext
from app.tenancy.deps import get_tenant
from app.core.pagination import CursorPage
from .service import DomainService
from .schemas import CreateRequest, DomainResponse

router = APIRouter(prefix="/v1/domain", tags=["domain"])

def get_service(tenant: TenantContext = Depends(get_tenant)) -> DomainService:
    return DomainService(tenant=tenant)

@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DomainResponse,
    operation_id="domain_create",           # ALWAYS set operation_id
    summary="Create a domain entity",
)
async def create(
    body: CreateRequest,
    service: DomainService = Depends(get_service),
    x_request_id: str = Header(default_factory=lambda: str(uuid.uuid4())),
    x_idempotency_key: str | None = Header(default=None),
) -> DomainResponse:
    return await service.create(body, idempotency_key=x_idempotency_key)

@router.get(
    "",
    response_model=CursorPage[DomainResponse],
    operation_id="domain_list",
)
async def list_items(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    service: DomainService = Depends(get_service),
) -> CursorPage[DomainResponse]:
    return await service.list(cursor=cursor, limit=limit)
```

## Service Layer Pattern

```python
# app/<domain>/service.py
from __future__ import annotations
import structlog
from opentelemetry import trace
from app.tenancy.context import TenantContext
from .repository import DomainRepository
from .schemas import CreateRequest, DomainResponse
from .exceptions import DomainNotFoundError

log = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

class DomainService:
    def __init__(self, tenant: TenantContext) -> None:
        self._tenant = tenant
        self._repo = DomainRepository(tenant=tenant)

    async def create(self, req: CreateRequest, *, idempotency_key: str | None = None) -> DomainResponse:
        with tracer.start_as_current_span("domain.create") as span:
            span.set_attribute("tenant_id", str(self._tenant.id))
            span.set_attribute("idempotency_key", idempotency_key or "")

            log.info("domain.create.start", tenant_id=str(self._tenant.id))
            result = await self._repo.create(req, idempotency_key=idempotency_key)
            log.info("domain.create.done", entity_id=str(result.id))
            span.set_attribute("result.id", str(result.id))
            return DomainResponse.model_validate(result)
```

## Repository Pattern

```python
# app/<domain>/repository.py
from __future__ import annotations
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_session
from app.tenancy.context import TenantContext
from .models import DomainModel

class DomainRepository:
    def __init__(self, tenant: TenantContext) -> None:
        self._tenant = tenant

    async def create(self, data: dict, *, idempotency_key: str | None = None) -> DomainModel:
        async with get_session() as session:
            async with session.begin():
                entity = DomainModel(
                    tenant_id=self._tenant.id,
                    **data,
                )
                session.add(entity)
                return entity

    async def get_by_id(self, entity_id: str) -> DomainModel | None:
        async with get_session() as session:
            result = await session.execute(
                select(DomainModel)
                .where(
                    DomainModel.id == entity_id,
                    DomainModel.tenant_id == self._tenant.id,   # ALWAYS filter by tenant
                )
            )
            return result.scalar_one_or_none()

    async def list_cursor(
        self, cursor: str | None, limit: int
    ) -> tuple[list[DomainModel], str | None]:
        async with get_session() as session:
            q = (
                select(DomainModel)
                .where(DomainModel.tenant_id == self._tenant.id)
                .order_by(DomainModel.created_at.desc(), DomainModel.id.desc())
                .limit(limit + 1)
            )
            if cursor:
                # decode cursor → (created_at, id) tuple
                q = q.where(DomainModel.id < cursor)
            rows = list((await session.scalars(q)).all())
            has_more = len(rows) > limit
            return rows[:limit], (str(rows[limit - 1].id) if has_more else None)
```

## Pydantic v2 Schemas

```python
# app/<domain>/schemas.py
from __future__ import annotations
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field, model_validator

class CreateRequest(BaseModel):
    model_config = {"extra": "forbid"}    # Reject unknown fields

    title: str = Field(min_length=1, max_length=500)
    priority: str = Field(default="medium", pattern="^(low|medium|high|critical)$")

    @model_validator(mode="after")
    def validate_fields(self) -> "CreateRequest":
        # Cross-field validation here
        return self

class DomainResponse(BaseModel):
    model_config = {"from_attributes": True}    # Allow ORM object validation

    id: UUID
    tenant_id: UUID
    title: str
    priority: str
    created_at: datetime
    updated_at: datetime
```

## SQLAlchemy Models

```python
# app/<domain>/models.py
from __future__ import annotations
from sqlalchemy import Column, String, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.sql import func
from app.db.base import Base
from app.core.uuid7 import uuid7

class DomainModel(Base):
    __tablename__ = "domain_entities"
    __table_args__ = (
        Index("idx_domain_tenant_status", "tenant_id", "status"),      # composite
        Index("idx_domain_tenant_created", "tenant_id", "created_at"), # for list
        {"schema": None},
    )

    id          = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid7)
    tenant_id   = Column(PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    title       = Column(String(500), nullable=False)
    status      = Column(String(50), nullable=False, default="active", index=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
```

## Celery Tasks

```python
# app/<domain>/tasks.py
from __future__ import annotations
import structlog
from opentelemetry import trace
from app.scaling.celery_app import celery_app

log = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

@celery_app.task(
    bind=True,
    name="domain.process_entity",
    max_retries=3,
    default_retry_delay=60,
    queue="domain",
)
def process_entity(self, tenant_id: str, entity_id: str) -> dict:
    """Idempotent — safe to retry. Always logs tenant_id + task_id."""
    with tracer.start_as_current_span("domain.process_entity") as span:
        span.set_attribute("tenant_id", tenant_id)
        span.set_attribute("entity_id", entity_id)
        span.set_attribute("celery.task_id", self.request.id)

        log.info("domain.process.start", tenant_id=tenant_id, entity_id=entity_id)
        try:
            # ... do work ...
            log.info("domain.process.done", entity_id=entity_id)
            return {"status": "completed", "entity_id": entity_id}
        except Exception as exc:
            log.error("domain.process.failed", error=str(exc), entity_id=entity_id)
            raise self.retry(exc=exc)
```

## Logging Rules

```python
import structlog
log = structlog.get_logger(__name__)

# ALWAYS use structured logging — NEVER print()
log.info("event.name", tenant_id=tenant_id, key="value")
log.error("event.failed", error=str(exc), tenant_id=tenant_id)

# Log at these levels:
# info  → business events (created, updated, completed)
# warning → recoverable issues (retrying, fallback used)
# error → failures requiring attention
# debug → only in dev (never in prod without sampling)
```

## Exception Handling

```python
# app/<domain>/exceptions.py
class DomainNotFoundError(Exception):
    """Raised when a domain entity does not exist for this tenant."""
    def __init__(self, entity_id: str) -> None:
        self.entity_id = entity_id
        super().__init__(f"Entity {entity_id} not found")

# In router.py — convert to RFC 7807:
@router.exception_handler(DomainNotFoundError)
async def not_found_handler(request, exc):
    return JSONResponse(
        status_code=404,
        content={"type": "not-found", "title": "Not Found",
                 "status": 404, "detail": str(exc)},
    )
```

## Anti-Patterns (Never Generate)

```python
# ❌ WRONG — sync SQLAlchemy
from sqlalchemy.orm import Session
def get_data(db: Session): ...

# ❌ WRONG — SQL injection
query = f"SELECT * FROM users WHERE id = '{user_id}'"

# ❌ WRONG — business logic in router
@router.post("/")
async def create(body: CreateReq, session: AsyncSession = Depends(get_db)):
    entity = MyModel(**body.dict())  # NO — goes in service/repository
    session.add(entity)

# ❌ WRONG — missing tenant filter
await session.execute(select(MyModel))  # ALWAYS add .where(tenant_id == ...)

# ❌ WRONG — print logging
print(f"Processing {entity_id}")

# ✅ CORRECT — everything above done properly
```
