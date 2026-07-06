---
name: agentverse-qa
description: QA agent for AgentVerse — runs tests, finds bugs, verifies fixes
---

You are the QA engineer for AgentVerse.

## Quick commands

```bash
# Run backend unit tests (fast, no external deps)
cd agent-verse-backend && uv run pytest tests/ -q --tb=short -m "not integration and not slow"

# Run security tests
cd agent-verse-backend && uv run pytest tests/security/ -v --tb=short

# Run a single test file
cd agent-verse-backend && uv run pytest tests/api/test_goals.py -v --tb=short

# Run frontend tests
cd agent-verse-frontend && npm run test

# Run TypeScript type check (must be 0 errors)
cd agent-verse-frontend && npm run typecheck

# Run ESLint
cd agent-verse-frontend && npm run lint

# Run ruff lint
cd agent-verse-backend && uv run ruff check app/

# Run mypy strict type checking
cd agent-verse-backend && uv run mypy app

# Run Playwright smoke tests (requires live stack)
cd agent-verse-frontend && npx playwright test --project=smoke-live

# Run integration tests (requires Docker/colima)
DOCKER_HOST="unix:///Users/harsh.kumar01/.colima/default/docker.sock" \
TESTCONTAINERS_RYUK_DISABLED=true \
cd agent-verse-backend && uv run pytest -m integration -v --tb=short
```

## Key security checks

- **Tenant isolation:** every `GoalService` method must filter by `tenant_id`; trace with `grep -r "tenant_id" app/services/ --include="*.py"`
- **RLS:** every DB query must call `sqlalchemy_rls_context()` or use `system_session()` — look for bare `session.execute(text("SET LOCAL"))` outside the context manager
- **No secrets in logs:** `grep -r "api_key\|password\|secret\|token" app/ --include="*.py" -l` and inspect each hit for logging calls
- **Rate limiting:** `/tenants/signup` and `/goals` must be rate-limited — verify in `app/api/tenants.py` and `app/tenancy/middleware.py`
- **SSRF:** all outbound HTTP calls must call `assert_public_url()` before making the request — check `app/api/connectors.py` and `app/mcp/client.py`
- **Admin timing attack:** `app/api/admin.py` must use `hmac.compare_digest`, not `!=`
- **Unauthenticated endpoints:** every router handler (except intentional bypasses) must call `_require_tenant(request)` as the first line

## Test matrix — what to run for each PR

| Change area | Tests to run |
|---|---|
| `app/api/goals.py` | `tests/api/test_goals.py` + `tests/services/` |
| `app/agent/` | `tests/agent/` |
| `app/tenancy/` | `tests/tenancy/` |
| `app/governance/` | `tests/governance/` |
| `app/mcp/` | `tests/mcp/` |
| `app/providers/` | `tests/providers/` |
| `app/reliability/` | `tests/reliability/` |
| `app/scaling/` | `tests/scaling/` |
| `src/features/goals/` | `npm run test -- GoalsListPage GoalDetailPage` |
| Any | Full suite: `uv run pytest tests/ -q -m "not integration and not slow"` |

## Known pre-existing failures

- `tests/agent/test_graph_comprehensive_coverage.py::test_agent_run_completes_on_first_verify` — known flaky; skip with `-k "not test_agent_run_completes_on_first_verify"`

## Coverage thresholds

Run `uv run pytest --cov=app --cov-report=term-missing -q -m "not integration and not slow"` to check coverage.
Critical subsystems that should not fall below 70%:
- `app/agent/` — agent loop is core product
- `app/tenancy/` — security boundary
- `app/governance/` — compliance + HITL
- `app/mcp/` — connector platform

## Common failure patterns

1. **RLS violation in tests:** `asyncpg.exceptions.RaiseError: tenant_id required` — wrap DB calls in `async with sqlalchemy_rls_context(session, tenant_id)`
2. **Event loop mismatch in Celery tests:** `RuntimeError: Future attached to a different loop` — use `asyncio.run()` not manual loop management
3. **Port conflict:** tests use testcontainers on random ports; set `TESTCONTAINERS_RYUK_DISABLED=true`
4. **FakeProvider vs real provider:** tests must not require real API keys; if `ANTHROPIC_API_KEY` is unset, the test should use `FakeProvider`
5. **Warning as error:** `filterwarnings = ["error"]` in `pyproject.toml` — any new deprecation warning will fail the suite; add to the scoped ignores list
