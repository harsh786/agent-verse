---
description: "Generate a complete TDD test suite for a backend service/router or frontend component — tests written FIRST, implementation follows"
---

# Generate TDD Test Suite

Generate a complete, world-class test suite following RED → GREEN → REFACTOR.
Tests are written BEFORE implementation. No production code until tests are written.

## Target
- **Module/Component**: {{target}}
- **Layer**: {{layer}}  <!-- backend-service | backend-router | frontend-component | frontend-hook -->
- **Behaviour to test**: {{description}}

## What to Generate

### Backend Service Tests (`tests/{{domain}}/test_{{name}}_service.py`)

Generate tests that cover:
1. **Happy path**: successful operation returns expected result
2. **Idempotency**: same idempotency key returns cached result (no DB hit)
3. **Not found**: raises typed domain exception (not generic Exception)
4. **Validation**: invalid input raises appropriate error
5. **Tenant isolation**: cannot access other tenant's data
6. **OTel emitted**: span created with correct name and attributes
7. **Structlog emitted**: info log on success, error log on failure

Pattern:
```python
class Test{{Name}}Service:
    async def test_{{verb}}_success(self, mock_tenant, mock_repo): ...
    async def test_{{verb}}_idempotent(self, mock_tenant, mock_repo, mock_cache): ...
    async def test_{{verb}}_not_found_raises_typed_error(self, mock_tenant, mock_repo): ...
    async def test_{{verb}}_emits_otel_span(self, mock_tenant, mock_tracer): ...
```

### Backend Router Tests (`tests/{{domain}}/test_{{name}}_router.py`)

Generate tests that cover:
1. **201/200/204**: correct status codes
2. **Location header**: on 201 responses
3. **422 + RFC 7807**: validation failure with correct error shape
4. **401**: unauthenticated returns 401
5. **403**: authenticated but wrong tenant returns 404 (don't reveal existence)
6. **Pagination**: list returns cursor, hasMore, data fields
7. **Idempotency**: X-Idempotency-Key header honoured

### Frontend Component Tests (`src/features/{{domain}}/__tests__/{{Name}}.test.tsx`)

Generate tests that cover:
1. **Renders**: base component renders without errors
2. **Loading state**: skeleton shown while data fetches
3. **Success state**: data from API shown correctly
4. **Empty state**: empty state UI shown when no data
5. **Error state**: ErrorBoundary triggered on API failure
6. **Interaction**: user can trigger main action (click, type, submit)
7. **Optimistic update**: mutation shows result immediately
8. **Accessibility**: no axe-core violations (call checkA11y)
9. **Keyboard**: interactive elements accessible via Tab + Enter

### Frontend Hook Tests (`src/features/{{domain}}/hooks/__tests__/use{{Name}}.test.ts`)

Generate tests that cover:
1. **Fetches data**: returns correct data on success
2. **Handles error**: returns error state on API failure
3. **Optimistic mutation**: cache updated immediately on mutate
4. **Rollback**: cache restored on mutation error
5. **Invalidation**: related queries invalidated on success

## Constraints
- Use `FakeProvider` for LLM — NEVER real API in tests
- Use MSW for frontend API mocking — NEVER fetch directly
- Use `testcontainers` for integration tests that need real DB/Redis
- Every test must be independent — no shared state between tests
- Tests must FAIL before implementation (TDD: RED first)
