---
applyTo: "agent-verse-backend/tests/**/*.py,agent-verse-frontend/src/**/*.test.*,agent-verse-frontend/e2e/**"
---

# Testing Instructions — AgentVerse

## Backend Testing Structure

```
tests/
  <domain>/
    test_service.py           # Unit tests (mocked dependencies)
    test_router.py            # Integration tests (TestClient + real DB)
    test_repository.py        # DB-level tests (testcontainers)
  conftest.py                 # Shared fixtures
  contracts/
    test_<domain>_contract.py # API contract tests
```

## Unit Test Pattern (Service Layer)

```python
# tests/<domain>/test_service.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.<domain>.service import DomainService
from app.<domain>.schemas import CreateRequest

@pytest.fixture
def mock_repo():
    return AsyncMock()

@pytest.fixture
def service(mock_repo, mock_tenant):
    svc = DomainService(tenant=mock_tenant)
    svc._repo = mock_repo   # inject mock
    return svc

class TestDomainService:
    async def test_create_success(self, service, mock_repo):
        """Happy path: entity created and returned."""
        mock_repo.create.return_value = FakeEntity(title="test")
        result = await service.create(CreateRequest(title="test"))
        assert result.title == "test"
        mock_repo.create.assert_awaited_once()

    async def test_create_idempotent(self, service, mock_repo, mock_cache):
        """Same idempotency key returns cached result."""
        mock_cache.get.return_value = FakeEntity(id="existing")
        result = await service.create(
            CreateRequest(title="test"),
            idempotency_key="key-123"
        )
        assert str(result.id) == "existing"
        mock_repo.create.assert_not_awaited()    # DB not hit again

    async def test_create_not_found_raises(self, service, mock_repo):
        """Domain exception raised, not generic exception."""
        mock_repo.get_by_id.return_value = None
        with pytest.raises(DomainNotFoundError):
            await service.get("nonexistent-id")
```

## Integration Test Pattern (Router + DB)

```python
# tests/<domain>/test_router.py
import pytest
from httpx import AsyncClient
from app.main import create_app

@pytest.mark.integration
class TestDomainRouter:
    async def test_create_returns_201(self, client: AsyncClient, auth_headers):
        response = await client.post(
            "/v1/domain",
            json={"title": "Test Entity", "priority": "high"},
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Test Entity"
        assert "id" in data
        assert "created_at" in data

    async def test_missing_title_returns_422(self, client: AsyncClient, auth_headers):
        response = await client.post(
            "/v1/domain",
            json={"priority": "high"},   # missing required title
            headers=auth_headers,
        )
        assert response.status_code == 422
        error = response.json()
        assert error["type"].endswith("validation-error")  # RFC 7807

    async def test_list_is_paginated(self, client: AsyncClient, auth_headers):
        response = await client.get(
            "/v1/domain?limit=2",
            headers=auth_headers,
        )
        assert response.status_code == 200
        page = response.json()
        assert "data" in page
        assert "cursor" in page
        assert "hasMore" in page

    async def test_unauthenticated_returns_401(self, client: AsyncClient):
        response = await client.get("/v1/domain")   # No auth headers
        assert response.status_code == 401
```

## Contract Tests (API Shape Verification)

```python
# tests/contracts/test_mission_contract.py
class TestMissionAPIContract:
    """Verifies API responses match frontend expectations."""

    async def test_mission_response_has_all_required_fields(self, client, auth_headers):
        resp = await client.post("/v1/missions", json={...}, headers=auth_headers)
        data = resp.json()
        required = ["id", "tenant_id", "title", "status", "priority", "created_at"]
        for field in required:
            assert field in data, f"Contract broken: missing field '{field}'"

    async def test_error_response_is_rfc7807(self, client, auth_headers):
        resp = await client.post("/v1/missions", json={}, headers=auth_headers)
        assert resp.status_code == 422
        error = resp.json()
        for field in ["type", "title", "status", "detail"]:
            assert field in error, f"RFC 7807 violated: missing '{field}'"
```

## Frontend Unit Test Pattern

```tsx
// src/features/goals/__tests__/MissionsPage.test.tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MissionsList } from '../components/MissionsList';
import { server } from '@/test/server';
import { http, HttpResponse } from 'msw';

describe('MissionsList', () => {
  it('renders missions from API', async () => {
    render(<MissionsList orgId="org-001" />, { wrapper: Providers });
    // Wait for data to load
    await screen.findByText('Research AI market trends');
    expect(screen.getAllByRole('listitem')).toHaveLength(3);
  });

  it('shows empty state when no missions', async () => {
    server.use(
      http.get('*/missions', () => HttpResponse.json({ data: [], hasMore: false }))
    );
    render(<MissionsList orgId="org-empty" />, { wrapper: Providers });
    await screen.findByText('No missions yet');
    expect(screen.getByRole('button', { name: /create/i })).toBeInTheDocument();
  });

  it('shows error boundary on API failure', async () => {
    server.use(
      http.get('*/missions', () => HttpResponse.json({}, { status: 500 }))
    );
    render(
      <ErrorBoundary name="test">
        <MissionsList orgId="org-001" />
      </ErrorBoundary>,
      { wrapper: Providers }
    );
    await screen.findByRole('alert');
  });

  it('creates mission on form submit', async () => {
    const user = userEvent.setup();
    render(<MissionCreate orgId="org-001" />, { wrapper: Providers });
    await user.type(screen.getByLabelText(/title/i), 'New mission');
    await user.click(screen.getByRole('button', { name: /create/i }));
    // Optimistic update — shows immediately
    expect(await screen.findByText('New mission')).toBeInTheDocument();
  });
});
```

## Playwright E2E Pattern

```typescript
// e2e/missions.spec.ts
import { test, expect } from '@playwright/test';

test.describe('Mission lifecycle', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/org/command-center');
    await page.waitForSelector('[data-testid="command-center-loaded"]');
  });

  test('creates a mission end-to-end', async ({ page }) => {
    await page.getByRole('button', { name: 'New Mission' }).click();
    await page.getByLabel('Mission title').fill('Research competitors');
    await page.getByLabel('Priority').selectOption('high');
    await page.getByRole('button', { name: 'Create' }).click();

    // SSE stream shows progress
    await expect(page.getByText('Research competitors')).toBeVisible();
    await expect(page.getByTestId('mission-status')).toHaveText('running', {
      timeout: 30_000,
    });
  });

  test('command bar accessible via keyboard', async ({ page }) => {
    await page.keyboard.press('Meta+K');
    await expect(page.getByRole('dialog', { name: 'Command bar' })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).not.toBeVisible();
  });
});

// Visual regression:
test('command center matches snapshot', async ({ page }) => {
  await page.goto('/org/command-center');
  await page.waitForSelector('[data-testid="command-center-loaded"]');
  await expect(page).toHaveScreenshot('command-center.png', { threshold: 0.02 });
});
```

## Conftest Fixtures

```python
# tests/conftest.py
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app

@pytest.fixture
def mock_tenant():
    from app.tenancy.context import TenantContext
    return TenantContext(
        id="00000000-0000-0000-0000-000000000001",
        plan="pro",
        rpm_limit=600,
    )

@pytest.fixture
async def client():
    app = create_app(manage_pools=False)  # In-memory services
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c

@pytest.fixture
def auth_headers():
    return {"X-API-Key": "test-api-key-for-tests"}
```

## Test Rules

1. **Minimum per feature**: happy path + error case + edge case
2. **No real LLM calls** in unit/integration tests — always use `FakeProvider`
3. **No real external HTTP** — always use `respx` or MSW mocking
4. **No shared state** — each test is independent (use transactions that rollback)
5. **Mark slow tests**: `@pytest.mark.slow` (LLM calls), `@pytest.mark.integration` (needs Docker)
6. **Coverage**: minimum 80% on new code (enforced by CI)
