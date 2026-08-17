# Test Coverage Skill — AgentVerse (90%+ Mandatory)

## Coverage Targets

| Layer | Minimum | Target | Failure Action |
|-------|---------|--------|----------------|
| Backend unit (service + repo) | 90% | 95% | Block CI |
| Backend integration | 85% | 90% | Block CI |
| Frontend unit (components + hooks) | 90% | 95% | Block CI |
| Frontend E2E | critical paths | all features | Block release |
| API contract | all endpoints | all endpoints | Block merge |

---

## Backend Coverage Configuration

```toml
# agent-verse-backend/pyproject.toml  [tool.pytest.ini_options]
addopts = [
    "--cov=app",
    "--cov-fail-under=90",          # blocks if < 90%
    "--cov-report=term-missing",
    "--cov-report=html:htmlcov",
    "--cov-report=xml:coverage.xml", # for CI artifact
]
# Exclude from coverage (generated, migrations, config):
[tool.coverage.run]
omit = [
    "app/db/migrations/*",
    "app/core/config.py",
    "app/cli/*",
    "app/main.py",        # wiring code — hard to test
    "*/tests/*",
]
```

## Frontend Coverage Configuration

```typescript
// agent-verse-frontend/vite.config.ts
export default defineConfig({
  test: {
    coverage: {
      provider:  'v8',
      reporter:  ['text', 'html', 'lcov'],
      thresholds: {
        lines:      90,    // blocks if < 90%
        functions:  90,
        branches:   85,
        statements: 90,
      },
      exclude: [
        '**/node_modules/**',
        '**/*.d.ts',
        '**/*.config.*',
        '**/coverage/**',
        'src/main.tsx',     // entry point
        'src/app/routes.tsx', // just routing config
      ],
    },
  },
});
```

---

## Integration Test Patterns

### Backend: TestContainers (Real Postgres + Redis)

```python
# tests/conftest.py — shared integration fixtures
import pytest
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

@pytest.fixture(scope="session")
def postgres_url():
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        # Apply migrations to real DB
        subprocess.run(["alembic", "upgrade", "head"],
            env={"DATABASE_URL": pg.get_connection_url()})
        yield pg.get_connection_url()

@pytest.fixture(scope="session")
def redis_url():
    with RedisContainer("redis:7-alpine") as redis:
        yield redis.get_connection_url()

# Mark integration tests:
# @pytest.mark.integration  → needs Docker
# @pytest.mark.slow         → hits real LLM (opt-in)
```

### Backend: Mission Lifecycle Integration Test

```python
@pytest.mark.integration
class TestMissionLifecycle:
    """Full mission lifecycle: create → plan → execute → verify → complete."""

    async def test_full_mission_cycle(self, client, auth_headers, fake_provider):
        # 1. Create mission
        resp = await client.post("/v1/missions",
            json={"title": "Analyse market trends", "priority": "high"},
            headers=auth_headers)
        assert resp.status_code == 201
        mission_id = resp.json()["id"]

        # 2. Submit goal
        resp = await client.post(f"/v1/goals",
            json={"mission_id": mission_id, "objective": "Analyse market"},
            headers=auth_headers)
        assert resp.status_code == 202
        goal_id = resp.json()["goal_id"]

        # 3. Stream SSE until complete
        events = []
        async with client.stream("GET", f"/v1/goals/{goal_id}/stream",
                headers=auth_headers) as stream:
            async for line in stream.aiter_lines():
                if line.startswith("data:"):
                    event = json.loads(line[5:])
                    events.append(event)
                    if event.get("status") in ("completed", "failed"):
                        break

        # 4. Verify outcome
        assert any(e["status"] == "completed" for e in events)
        resp = await client.get(f"/v1/goals/{goal_id}", headers=auth_headers)
        assert resp.json()["status"] == "completed"
```

### Frontend: Playwright E2E Integration Tests

```typescript
// e2e/missions-lifecycle.spec.ts
test.describe('Mission Lifecycle E2E', () => {
  test('create → execute → view result', async ({ page, context }) => {
    // Login
    await page.goto('/login');
    await page.fill('[name=api_key]', process.env.TEST_API_KEY!);
    await page.click('button[type=submit]');
    await page.waitForURL('/org/command-center');

    // Create mission
    await page.click('[data-testid=new-mission-btn]');
    await page.fill('[name=title]', 'E2E: Analyse AI trends');
    await page.selectOption('[name=priority]', 'high');
    await page.click('[data-testid=submit-mission]');

    // Wait for SSE progress
    await expect(page.locator('[data-testid=mission-status]'))
      .toHaveText('running', { timeout: 10_000 });

    // Wait for completion (uses FakeProvider in test env)
    await expect(page.locator('[data-testid=mission-status]'))
      .toHaveText('completed', { timeout: 30_000 });

    // Verify output displayed
    await expect(page.locator('[data-testid=mission-output]')).toBeVisible();
  });

  test('approval flow: HITL works end-to-end', async ({ page }) => {
    // Submit mission requiring approval
    // Verify approval queue shows item
    // Approve item
    // Verify mission continues
  });
});
```

---

## Functional Test Patterns

### API Contract Tests (All Endpoints)

```python
# tests/functional/test_all_endpoints_contract.py
class TestAllEndpointsContract:
    """Smoke test: every endpoint returns expected shape and auth."""

    @pytest.mark.parametrize("method,path,body", [
        ("GET",    "/v1/missions",        None),
        ("POST",   "/v1/missions",        {"title": "t", "priority": "low"}),
        ("GET",    "/v1/missions/{id}",   None),
        ("PATCH",  "/v1/missions/{id}",   {"status": "paused"}),
        ("DELETE", "/v1/missions/{id}",   None),
    ])
    async def test_endpoint_requires_auth(self, client, method, path, body):
        resp = await client.request(method, path, json=body)
        assert resp.status_code == 401, f"{method} {path} must require auth"

    async def test_all_errors_are_rfc7807(self, client, auth_headers):
        """No endpoint returns raw exceptions."""
        resp = await client.post("/v1/missions",
            json={},  # missing required fields
            headers=auth_headers)
        err = resp.json()
        for field in ("type", "title", "status", "detail", "request_id"):
            assert field in err, f"RFC 7807 violation: missing {field}"

    async def test_all_lists_are_cursor_paginated(self, client, auth_headers):
        for path in ["/v1/missions", "/v1/agents", "/v1/knowledge"]:
            resp = await client.get(path, headers=auth_headers)
            body = resp.json()
            assert "data" in body,    f"{path}: missing 'data'"
            assert "cursor" in body,  f"{path}: missing 'cursor'"
            assert "hasMore" in body, f"{path}: missing 'hasMore'"
```

### Frontend Functional Tests (User Flows)

```typescript
// src/features/missions/__tests__/MissionsUserFlow.test.tsx
describe('Missions user flow', () => {
  it('complete create → list → view → delete flow', async () => {
    const user = userEvent.setup();

    render(<MissionsPage orgId="org-1" />, { wrapper: Providers });

    // Create
    await user.click(await screen.findByRole('button', { name: /new mission/i }));
    await user.type(screen.getByLabelText(/title/i), 'Functional test mission');
    await user.selectOptions(screen.getByLabelText(/priority/i), 'high');
    await user.click(screen.getByRole('button', { name: /create/i }));

    // Optimistic: appears immediately
    expect(screen.getByText('Functional test mission')).toBeInTheDocument();

    // View detail
    await user.click(screen.getByText('Functional test mission'));
    expect(await screen.findByTestId('mission-detail')).toBeInTheDocument();

    // Delete
    await user.click(screen.getByRole('button', { name: /delete/i }));
    await user.click(screen.getByRole('button', { name: /confirm/i }));
    expect(screen.queryByText('Functional test mission')).not.toBeInTheDocument();
  });
});
```

---

## CI Coverage Gates

```yaml
# .github/workflows/ci.yml — coverage gates
- name: Backend coverage check
  run: |
    cd agent-verse-backend
    uv run pytest --cov=app --cov-fail-under=90 \
      --cov-report=xml:coverage.xml
  # Fails build if < 90%

- name: Frontend coverage check
  run: |
    cd agent-verse-frontend
    npx vitest run --coverage
  # Fails build if thresholds not met (defined in vite.config.ts)

- name: Upload coverage reports
  uses: codecov/codecov-action@v4
  with:
    files: |
      agent-verse-backend/coverage.xml
      agent-verse-frontend/coverage/lcov.info
```
