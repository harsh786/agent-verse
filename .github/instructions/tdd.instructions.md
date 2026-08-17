---
applyTo: "agent-verse-backend/tests/**/*.py,agent-verse-frontend/src/**/*.test.*,agent-verse-frontend/e2e/**"
---

# TDD — Test-Driven Development (Mandatory)

## THE RULE: Tests Written BEFORE Implementation. Always.

```
1. RED    → Write a failing test describing the desired behaviour
2. GREEN  → Write minimum code to make ONLY that test pass
3. REFACTOR → Clean up, same tests must still pass
4. REPEAT → One cycle per behaviour (not per function, not per class)
```

**If you are writing implementation code without a failing test → STOP. Write the test first.**

---

## Minimum Test Coverage Per Method (Non-Negotiable)

Every public method must have at minimum:

| Test # | What It Tests |
|--------|--------------|
| Test 1 | **Happy path** — correct input → expected output |
| Test 2 | **Error/exception** — invalid input → typed domain error |
| Test 3 | **Edge case** — boundary, empty, null, concurrent |

Plus for service methods:
| Test 4 | **Idempotency** — same key/input → same result, no side effects |
| Test 5 | **Tenant isolation** — other tenant's data is inaccessible |

---

## Backend Test Structure (Mandatory)

```python
# tests/<domain>/test_<name>_service.py
class Test<Name>Service:
    # ── WRITE THESE BEFORE service.py EXISTS ──────────────────────────────

    async def test_<verb>_success(self, mock_tenant, mock_repo):
        """Happy path: entity created and returned."""
        mock_repo.create.return_value = Fake<Entity>(title="test")
        service = <Name>Service(tenant=mock_tenant)
        service._repo = mock_repo
        result = await service.<verb>(Create<Entity>Request(title="test"))
        assert result.title == "test"

    async def test_<verb>_idempotent(self, mock_tenant, mock_repo, mock_cache):
        """Same idempotency key → cached result, DB not hit."""
        mock_cache.get.return_value = Fake<Entity>(id="cached-id")
        service = <Name>Service(tenant=mock_tenant)
        result = await service.<verb>(
            Create<Entity>Request(title="test"),
            idempotency_key="key-123"
        )
        assert str(result.id) == "cached-id"
        mock_repo.create.assert_not_awaited()  # DB NOT called

    async def test_<verb>_not_found_raises_typed_error(self, mock_tenant, mock_repo):
        """Missing entity raises domain exception, not generic Exception."""
        mock_repo.get_by_id.return_value = None
        with pytest.raises(<Entity>NotFoundError):  # typed — not Exception!
            await service.get("nonexistent")

    async def test_<verb>_emits_otel_span(self, mock_tenant, mock_tracer):
        """OTel span emitted with correct name and tenant_id attribute."""
        ...
        assert mock_tracer.last_span.name == "<domain>.<verb>"
        assert mock_tracer.last_span.attributes["tenant_id"] == str(mock_tenant.id)

    async def test_<verb>_tenant_isolation(self, client, tenant_a, tenant_b_entity):
        """Tenant A cannot access Tenant B's data."""
        resp = await client.get(f"/v1/<entities>/{tenant_b_entity.id}",
            headers=tenant_a_headers)
        assert resp.status_code == 404  # not 403 — don't reveal existence
```

---

## Frontend Test Structure (Mandatory)

```tsx
// src/features/<domain>/__tests__/<Name>.test.tsx
// WRITE THESE BEFORE <Name>.tsx EXISTS

describe('<Name>', () => {
  it('renders in default state', async () => {
    render(<Name />, { wrapper: Providers });
    expect(await screen.findByRole('main')).toBeInTheDocument();
  });

  it('shows loading skeleton while fetching', () => {
    server.use(http.get('*', () => new Promise(() => {})));  // never resolves
    render(<Name />, { wrapper: Providers });
    expect(screen.getByTestId('skeleton')).toBeInTheDocument();
  });

  it('shows empty state when no data', async () => {
    server.use(http.get('*', () => HttpResponse.json({ data: [] })));
    render(<Name />, { wrapper: Providers });
    expect(await screen.findByText(/no .* yet/i)).toBeInTheDocument();
  });

  it('shows error boundary on API failure', async () => {
    server.use(http.get('*', () => HttpResponse.json({}, { status: 500 })));
    render(
      <ErrorBoundary name="test"><Name /></ErrorBoundary>,
      { wrapper: Providers }
    );
    expect(await screen.findByRole('alert')).toBeInTheDocument();
  });

  it('interactive: keyboard accessible', async () => {
    const user = userEvent.setup();
    render(<Name />, { wrapper: Providers });
    await user.tab();
    expect(document.activeElement).toBeVisible();
  });

  it('has no accessibility violations', async () => {
    const { container } = render(<Name />, { wrapper: Providers });
    const violations = await axe(container);
    expect(violations).toHaveLength(0);
  });
});
```

---

## Coverage Thresholds (Enforced by CI — Will Block Merge)

```
Backend:   ≥ 90% line coverage  (pytest --cov-fail-under=90)
Frontend:  ≥ 90% line coverage  (vitest --coverage.thresholds.lines=90)
```

**If your new code drops coverage below 90%: add tests before committing.**

---

## Anti-Patterns (Never Do in Tests)

```python
# ❌ WRONG — writing implementation before tests
def my_feature():
    return result   # written before any test

# ❌ WRONG — testing implementation details
assert service._internal_cache  # tests private state

# ❌ WRONG — using real LLM in unit tests
response = await anthropic_client.complete(...)  # slow, costs money, flaky

# ❌ WRONG — shared mutable state between tests
class TestService:
    service = MissionService()  # module-level — shared between ALL tests!

# ✅ CORRECT — fresh instance per test via fixture
@pytest.fixture
def service(mock_tenant): return MissionService(tenant=mock_tenant)
```
