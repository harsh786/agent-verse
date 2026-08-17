# Test-Driven Development Skill — AgentVerse

## When to Invoke
**MANDATORY**: Invoke BEFORE writing ANY implementation code for a feature or bugfix.
No implementation code should exist before tests are written.

## Global Skill Reference
This skill extends: `~/.agents/skills/test-driven-development/SKILL.md`
Read that file for the full TDD methodology, then apply AgentVerse-specific patterns below.

---

## TDD Cycle for AgentVerse

### RED → GREEN → REFACTOR

```
RED:    Write a failing test that describes the desired behaviour
GREEN:  Write the minimum code to make it pass
REFACTOR: Clean up, no new behaviour
REPEAT: One cycle per behaviour (not per function)
```

---

## Backend TDD Pattern (Python/FastAPI)

### Step 1 — Write the test FIRST (before any implementation)

```python
# tests/missions/test_mission_service.py
import pytest
from unittest.mock import AsyncMock
from app.missions.service import MissionService
from app.missions.schemas import CreateMissionRequest
from app.missions.exceptions import MissionNotFoundError

class TestMissionService:
    """Write ALL tests before touching service.py"""

    # RED: This test fails because MissionService doesn't exist yet
    async def test_create_mission_returns_mission_with_id(
        self, mock_tenant, mock_repo
    ):
        service = MissionService(tenant=mock_tenant)
        service._repo = mock_repo
        mock_repo.create.return_value = FakeMission(title="Deploy AI")

        result = await service.create(CreateMissionRequest(
            title="Deploy AI", priority="high"
        ))

        assert result.title == "Deploy AI"
        assert result.id is not None
        mock_repo.create.assert_awaited_once()

    async def test_create_with_idempotency_key_returns_cached(
        self, mock_tenant, mock_repo, mock_cache
    ):
        """Same idempotency key must return same result without hitting DB again."""
        cached = FakeMission(id="existing-id", title="Deploy AI")
        mock_cache.get.return_value = cached

        service = MissionService(tenant=mock_tenant)
        service._repo = mock_repo
        result = await service.create(
            CreateMissionRequest(title="Deploy AI"),
            idempotency_key="key-abc"
        )

        assert str(result.id) == "existing-id"
        mock_repo.create.assert_not_awaited()  # DB not hit

    async def test_create_records_otel_span(self, mock_tenant, mock_repo, mock_tracer):
        """OTel span emitted with correct attributes."""
        service = MissionService(tenant=mock_tenant)
        await service.create(CreateMissionRequest(title="Test"))

        span = mock_tracer.last_span
        assert span.name == "mission.create"
        assert span.attributes["tenant_id"] == str(mock_tenant.id)

    async def test_get_nonexistent_mission_raises_not_found(self, mock_tenant, mock_repo):
        mock_repo.get_by_id.return_value = None
        service = MissionService(tenant=mock_tenant)

        with pytest.raises(MissionNotFoundError):
            await service.get("nonexistent")
```

### Step 2 — Write minimum implementation to make tests pass

```python
# app/missions/service.py  (written AFTER tests above)
# Only implements what the tests require — nothing more
```

### Step 3 — Write router test BEFORE router implementation

```python
# tests/missions/test_mission_router.py
@pytest.mark.integration
class TestMissionRouter:
    async def test_post_mission_returns_201(self, client, auth_headers):
        resp = await client.post("/v1/missions",
            json={"title": "Deploy AI", "priority": "high"},
            headers=auth_headers)
        assert resp.status_code == 201
        assert resp.json()["title"] == "Deploy AI"
        assert "id" in resp.json()
        assert "Location" in resp.headers

    async def test_post_mission_missing_title_returns_422_rfc7807(self, client, auth_headers):
        resp = await client.post("/v1/missions", json={}, headers=auth_headers)
        assert resp.status_code == 422
        err = resp.json()
        assert "type" in err      # RFC 7807
        assert "title" in err
        assert "status" in err
        assert "request_id" in err

    async def test_post_mission_unauthenticated_returns_401(self, client):
        resp = await client.post("/v1/missions", json={"title": "x"})
        assert resp.status_code == 401
```

---

## Frontend TDD Pattern (React/TypeScript)

### Step 1 — Write component test FIRST

```tsx
// src/features/missions/__tests__/MissionsPage.test.tsx
// Written BEFORE MissionsPage.tsx exists

describe('MissionsPage', () => {
  // RED: fails because component doesn't exist
  it('renders page heading', async () => {
    render(<MissionsPage orgId="org-1" />, { wrapper: Providers });
    expect(await screen.findByRole('heading', { name: /missions/i })).toBeInTheDocument();
  });

  it('shows list of missions fetched from API', async () => {
    server.use(
      http.get('*/missions', () =>
        HttpResponse.json({ data: [buildMission({ title: 'Launch ML pipeline' })], hasMore: false })
      )
    );
    render(<MissionsPage orgId="org-1" />, { wrapper: Providers });
    expect(await screen.findByText('Launch ML pipeline')).toBeInTheDocument();
  });

  it('shows empty state when no missions', async () => {
    server.use(
      http.get('*/missions', () => HttpResponse.json({ data: [], hasMore: false }))
    );
    render(<MissionsPage orgId="org-1" />, { wrapper: Providers });
    expect(await screen.findByText(/no missions yet/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /create/i })).toBeInTheDocument();
  });

  it('shows error boundary on API failure', async () => {
    server.use(
      http.get('*/missions', () => HttpResponse.json({}, { status: 500 }))
    );
    render(
      <ErrorBoundary name="test"><MissionsPage orgId="org-1" /></ErrorBoundary>,
      { wrapper: Providers }
    );
    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });

  it('keyboard: can create mission via Enter on button', async () => {
    const user = userEvent.setup();
    render(<MissionsPage orgId="org-1" />, { wrapper: Providers });
    const btn = await screen.findByRole('button', { name: /create/i });
    await user.tab(); // navigate to button
    await user.keyboard('{Enter}');
    expect(screen.getByRole('dialog')).toBeInTheDocument(); // form opens
  });
});
```

### Step 2 — Write hook test BEFORE hook implementation

```typescript
// src/features/missions/hooks/__tests__/useMissions.test.ts
describe('useMissions', () => {
  it('fetches missions for orgId', async () => {
    const { result } = renderHook(
      () => useMissions('org-1'),
      { wrapper: createQueryWrapper() }
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.pages[0].data).toHaveLength(2);
  });

  it('mutation adds optimistic mission immediately', async () => {
    const { result } = renderHook(
      () => useCreateMission('org-1'),
      { wrapper: createQueryWrapper() }
    );
    act(() => result.current.mutate({ title: 'New mission', priority: 'high' }));
    // Optimistic: appears before API confirms
    const qc = result.current;
    // Check TanStack Query cache updated optimistically
  });
});
```

---

## TDD Checklist (Run Before Any PR)

```
BACKEND:
  □ Unit tests written BEFORE service.py implementation
  □ Router tests written BEFORE router.py implementation
  □ Minimum 3 tests per public method: happy + error + edge
  □ All tests pass: uv run pytest tests/<module>/ -v
  □ Coverage ≥ 90%: uv run pytest --cov=app/<module> --cov-fail-under=90
  □ No test uses real LLM (use FakeProvider)
  □ No test uses real DB (use testcontainers or mocked repo)

FRONTEND:
  □ Component test written BEFORE component implementation
  □ Hook test written BEFORE hook implementation
  □ Tests cover: render, loading state, empty state, error state, interaction
  □ All tests pass: npm run test
  □ Coverage ≥ 90%: npm run test -- --coverage --coverage.thresholds.lines=90
  □ No test uses real API (use MSW handlers)
  □ Accessibility tested: axe-core on every page component
```

## Coverage Enforcement

```bash
# Backend (blocks CI if < 90%):
uv run pytest --cov=app --cov-fail-under=90 --cov-report=term-missing

# Frontend (blocks CI if < 90%):
npx vitest run --coverage --coverage.thresholds.lines=90

# Check coverage report:
# Backend:  open htmlcov/index.html
# Frontend: open coverage/index.html
```
