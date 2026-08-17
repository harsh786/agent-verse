---
description: "Generate a complete backend module: router + service + repository + schemas + models + migration + tests"
---

# Generate New Backend Module

I need you to generate a complete, world-class backend module for AgentVerse following ALL project conventions.

## Domain Details
- **Module name**: {{module_name}}
- **Domain description**: {{description}}
- **Parent app module**: `app/{{module_name}}/`

## What to Generate

### 1. SQLAlchemy Model (`app/{{module_name}}/models.py`)
- UUID v7 primary key
- `tenant_id` FK with index
- `created_at` + `updated_at` timestamps
- All domain-specific columns with proper types
- `__table_args__` with all necessary indexes (FK, status, composite)
- RLS-compatible (tenant_id always filterable)

### 2. Pydantic Schemas (`app/{{module_name}}/schemas.py`)
- `CreateRequest` with `model_config = {"extra": "forbid"}`
- `UpdateRequest` with all optional fields
- `Response` with `model_config = {"from_attributes": True}`
- Field validation (min_length, max_length, pattern)
- Cross-field validators where needed

### 3. Repository (`app/{{module_name}}/repository.py`)
- All queries use `async with get_session() as session`
- Always filter by `tenant_id`
- Cursor-based pagination
- Eager loading to prevent N+1
- `session.begin()` for writes

### 4. Service (`app/{{module_name}}/service.py`)
- OTel span on every method: `tracer.start_as_current_span("{{module_name}}.{verb}")`
- Structlog event on every method: `log.info("{{module_name}}.{verb}.done", ...)`
- Idempotency key support on create
- RFC 7807 error propagation
- No direct DB calls — uses repository

### 5. Router (`app/{{module_name}}/router.py`)
- Prefix: `/v1/{{module_name}}s`
- All endpoints have `operation_id`
- `x_request_id` header on state-changing endpoints
- Cursor pagination on list endpoint
- Proper HTTP status codes (201 for create, 204 for delete)
- No business logic — delegates to service

### 6. Exceptions (`app/{{module_name}}/exceptions.py`)
- `{{ModuleName}}NotFoundError`
- `{{ModuleName}}ConflictError` (for duplicates)
- Exception handlers that produce RFC 7807 responses

### 7. Alembic Migration (`app/db/migrations/versions/XXXX_{{module_name}}.py`)
- Next sequential revision number
- All columns with proper types
- All indexes (use `CREATE INDEX CONCURRENTLY` for production-safe)
- RLS policies
- Reversible `downgrade()`

### 8. Tests
- `tests/{{module_name}}/test_service.py`: unit tests with mocked repo
- `tests/{{module_name}}/test_router.py`: integration tests with TestClient
- Cover: create success, validation error (422), not found (404), unauthenticated (401), pagination

### 9. `__init__.py`
- Export: `router`, `{{ModuleName}}Service`, schemas

## Constraints
- NEVER use sync SQLAlchemy
- NEVER use `import *`
- NEVER use `print()` — always structlog
- NEVER skip tenant_id filter in queries
- ALWAYS add OTel spans
- ALWAYS add RFC 7807 error format
- Follow ruff line-length=100
