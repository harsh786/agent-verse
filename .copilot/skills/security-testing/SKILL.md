# Security & Vulnerability Testing Skill — AgentVerse

## When to Invoke
- Before any PR touching auth, permissions, or data access
- Before any new API endpoint is added
- Weekly automated scans in CI
- Before every production release

---

## OWASP Top 10 Test Coverage

### A01: Broken Access Control

```python
# tests/security/test_access_control.py
class TestAccessControl:
    """Every data endpoint must enforce tenant isolation."""

    async def test_cannot_access_other_tenant_mission(
        self, client, tenant_a_headers, tenant_b_mission_id
    ):
        """Tenant A cannot read Tenant B's mission — even with valid auth."""
        resp = await client.get(
            f"/v1/missions/{tenant_b_mission_id}",
            headers=tenant_a_headers  # valid auth, wrong tenant
        )
        assert resp.status_code == 404  # not 403 — don't reveal existence

    async def test_cannot_list_other_tenant_missions(
        self, client, tenant_a_headers, create_missions_for_tenant_b
    ):
        """List must only return current tenant's missions."""
        resp = await client.get("/v1/missions", headers=tenant_a_headers)
        data = resp.json()["data"]
        # All returned missions must belong to tenant A
        for mission in data:
            assert mission["tenant_id"] == str(TENANT_A_ID)

    async def test_rls_prevents_raw_sql_bypass(self, db_session):
        """RLS policies active — raw query without GUC returns nothing."""
        # Without SET app.tenant_id, RLS blocks all rows
        result = await db_session.execute(
            text("SELECT COUNT(*) FROM org_missions")
        )
        count = result.scalar()
        assert count == 0   # RLS blocks without tenant context
```

### A03: Injection Prevention

```python
class TestInjectionPrevention:
    @pytest.mark.parametrize("malicious_input", [
        "'; DROP TABLE missions; --",
        "' OR '1'='1",
        "<script>alert('xss')</script>",
        "{{7*7}}",            # template injection
        "../../etc/passwd",   # path traversal
        "\x00null",           # null byte
    ])
    async def test_sql_injection_rejected(self, client, auth_headers, malicious_input):
        resp = await client.post("/v1/missions",
            json={"title": malicious_input, "priority": "low"},
            headers=auth_headers)
        # Either 400/422 (validation rejected) or 201 (safely stored as text)
        assert resp.status_code in (201, 400, 422)
        # If stored, it must be stored as plain text — not executed
        if resp.status_code == 201:
            stored = resp.json()["title"]
            assert "DROP TABLE" not in stored.upper() or stored == malicious_input

    async def test_xss_prevented_in_api_response(self, client, auth_headers):
        resp = await client.post("/v1/missions",
            json={"title": "<img src=x onerror=alert(1)>", "priority": "low"},
            headers=auth_headers)
        if resp.status_code == 201:
            title = resp.json()["title"]
            # API returns text — no encoding needed (that's the frontend's job)
            # But the stored value must equal what was sent (not executed)
            assert resp.json()["title"] == "<img src=x onerror=alert(1)>"
```

### A07: Authentication Failures

```python
class TestAuthentication:
    async def test_missing_api_key_returns_401(self, client):
        resp = await client.get("/v1/missions")
        assert resp.status_code == 401

    async def test_invalid_api_key_returns_401(self, client):
        resp = await client.get("/v1/missions",
            headers={"X-API-Key": "invalid-key-that-does-not-exist"})
        assert resp.status_code == 401

    async def test_rate_limiting_enforced(self, client, auth_headers):
        """429 after exceeding rate limit."""
        responses = []
        for _ in range(200):   # exceed free tier 60 RPM
            responses.append(await client.get("/v1/missions",
                headers=auth_headers))
        assert any(r.status_code == 429 for r in responses)

    async def test_rate_limit_has_retry_after_header(self, client, rate_limited_headers):
        resp = await client.get("/v1/missions", headers=rate_limited_headers)
        if resp.status_code == 429:
            assert "retry-after" in resp.headers or "Retry-After" in resp.headers
```

---

## Dependency Vulnerability Scanning

```yaml
# .github/workflows/security.yml
name: Security Scan
on:
  push:
    branches: [main]
  schedule:
    - cron: "0 2 * * 1"   # Weekly Monday 2am

jobs:
  backend-vulns:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: pip-audit (Python dependencies)
        run: |
          cd agent-verse-backend
          uv run pip-audit --strict       # fails on any known CVE
          uv run pip-audit --fix --dry-run # shows what would be fixed

  frontend-vulns:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: npm audit (Node dependencies)
        run: |
          cd agent-verse-frontend
          npm audit --audit-level=high    # fails on high/critical CVEs

  container-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build image
        run: docker build -t agentverse-backend ./agent-verse-backend
      - name: Grype container scan
        uses: anchore/scan-action@v3
        with:
          image: agentverse-backend
          fail-build: true
          severity-cutoff: high

  secret-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0    # full history
      - name: Gitleaks scan
        uses: gitleaks/gitleaks-action@v2
        env:
          GITLEAKS_LICENSE: ${{ secrets.GITLEAKS_LICENSE }}
```

---

## Security Test Patterns

### JWT / Token Security

```python
class TestTokenSecurity:
    async def test_expired_jwt_rejected(self, client):
        expired_token = create_jwt(expiry=datetime.utcnow() - timedelta(hours=1))
        resp = await client.get("/v1/missions",
            headers={"Authorization": f"Bearer {expired_token}"})
        assert resp.status_code == 401

    async def test_tampered_jwt_rejected(self, client, valid_jwt):
        tampered = valid_jwt[:-10] + "tampered!!"
        resp = await client.get("/v1/missions",
            headers={"Authorization": f"Bearer {tampered}"})
        assert resp.status_code == 401
```

### File Upload Security

```python
class TestFileUploadSecurity:
    async def test_oversized_file_rejected(self, client, auth_headers):
        large_file = b"x" * (51 * 1024 * 1024)  # 51MB > 50MB limit
        resp = await client.post("/v1/knowledge/upload",
            files={"file": ("large.pdf", large_file, "application/pdf")},
            headers=auth_headers)
        assert resp.status_code == 413

    async def test_executable_file_rejected(self, client, auth_headers):
        resp = await client.post("/v1/knowledge/upload",
            files={"file": ("evil.exe", b"MZ...", "application/octet-stream")},
            headers=auth_headers)
        assert resp.status_code == 422

    async def test_path_traversal_prevented(self, client, auth_headers):
        resp = await client.post("/v1/knowledge/upload",
            files={"file": ("../../etc/passwd", b"root:x:0:0", "text/plain")},
            headers=auth_headers)
        # Either rejected or filename sanitised
        if resp.status_code == 201:
            assert ".." not in resp.json()["filename"]
            assert "etc/passwd" not in resp.json()["filename"]
```

### Frontend XSS Tests

```typescript
// e2e/security/xss.spec.ts
test('XSS payload in mission title does not execute', async ({ page }) => {
  await page.goto('/org/command-center');

  // Simulate API returning XSS payload
  await page.route('**/missions', (route) =>
    route.fulfill({
      json: {
        data: [{ id: '1', title: '<img src=x onerror=window.__xss=true>', status: 'active' }],
        hasMore: false,
      },
    })
  );

  await page.reload();
  await page.waitForSelector('[data-testid=missions-list]');

  // XSS must not have executed
  const xssExecuted = await page.evaluate(() => (window as any).__xss === true);
  expect(xssExecuted).toBe(false);

  // But the text content is displayed (safely escaped)
  await expect(page.locator('[data-testid=mission-title]').first())
    .toContainText('<img');  // displayed as text, not rendered as tag
});
```

---

## Penetration Testing Checklist

Run before every major release:

```
□ OWASP ZAP automated scan against staging environment
□ Manual IDOR testing: try accessing IDs from other tenants
□ Mass assignment testing: send unexpected fields in request bodies
□ Rate limiting bypass testing: try rotating IPs, headers
□ Authentication bypass: test JWT algorithm confusion (RS256/HS256)
□ Business logic testing: negative prices, zero quantities, future dates
□ File upload: zip bombs, polyglot files, SVG with embedded scripts
□ SSRF: test webhook URLs pointing to internal services (169.254.x.x, 10.x.x.x)
```
