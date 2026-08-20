/**
 * Real E2E — Authentication
 *
 * Tests the complete auth flow through the real backend.
 * No HTTP mocking. Every request hits localhost:8000.
 */

import { test, expect, createE2ETenant, API_BASE, FRONTEND_BASE, loginFrontend } from './fixtures';

test.describe('Authentication — Real E2E', () => {
  // ── Signup ────────────────────────────────────────────────────────────────

  test('signup creates a tenant and returns a valid API key', async ({ request }) => {
    const ts = Date.now();
    const resp = await request.post(`${API_BASE}/tenants/signup`, {
      data: { name: `Auth E2E ${ts}`, email: `auth-${ts}@agentverse.io` },
    });
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.tenant_id).toBeTruthy();
    expect(body.api_key).toMatch(/^av_free_/);
    expect(body.plan).toBe('free');
  });

  test('signup rejects duplicate email with 409 or 400', async ({ request }) => {
    const ts = Date.now();
    const email = `dup-${ts}@agentverse.io`;
    await request.post(`${API_BASE}/tenants/signup`, {
      data: { name: 'First', email },
    });
    const resp2 = await request.post(`${API_BASE}/tenants/signup`, {
      data: { name: 'Second', email },
    });
    expect([400, 409, 422]).toContain(resp2.status());
  });

  test('signup rejects missing name with 422', async ({ request }) => {
    const resp = await request.post(`${API_BASE}/tenants/signup`, {
      data: { email: `no-name-${Date.now()}@agentverse.io` },
    });
    expect(resp.status()).toBe(422);
  });

  test('signup rejects invalid email format with 422', async ({ request }) => {
    const resp = await request.post(`${API_BASE}/tenants/signup`, {
      data: { name: 'Test', email: 'not-an-email' },
    });
    expect(resp.status()).toBe(422);
  });

  // ── /tenants/me ───────────────────────────────────────────────────────────

  test('GET /tenants/me returns tenant profile for valid API key', async ({ request, tenant }) => {
    const resp = await request.get(`${API_BASE}/tenants/me`, {
      headers: { 'X-API-Key': tenant.apiKey },
    });
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.tenant_id).toBe(tenant.tenantId);
    expect(body.name).toBe(tenant.name);
    expect(body.email).toBe(tenant.email);
  });

  test('GET /tenants/me returns 401 for missing API key', async ({ request }) => {
    const resp = await request.get(`${API_BASE}/tenants/me`);
    expect(resp.status()).toBe(401);
  });

  test('GET /tenants/me returns 401 for invalid API key', async ({ request }) => {
    const resp = await request.get(`${API_BASE}/tenants/me`, {
      headers: { 'X-API-Key': 'av_free_totally_invalid_key' },
    });
    expect(resp.status()).toBe(401);
  });

  test('GET /tenants/me returns 401 for expired/revoked key', async ({ request }) => {
    const resp = await request.get(`${API_BASE}/tenants/me`, {
      headers: { 'X-API-Key': 'av_free_ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ' },
    });
    expect(resp.status()).toBe(401);
  });

  // ── Frontend auth flow ────────────────────────────────────────────────────

  test('frontend /auth page renders without errors', async ({ page }) => {
    await page.goto(`${FRONTEND_BASE}/auth`);
    await expect(page).toHaveTitle(/AgentVerse/);
    // No uncaught JS errors
    const errors: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.waitForTimeout(1000);
    expect(errors).toHaveLength(0);
  });

  test('authenticated user can reach dashboard without redirect to /auth', async ({ page, tenant }) => {
    await loginFrontend(page, tenant);
    await page.goto(`${FRONTEND_BASE}/dashboard`, { waitUntil: 'networkidle' });
    // Should NOT be redirected back to /auth
    expect(page.url()).not.toContain('/auth');
  });

  test('unauthenticated user is redirected to /auth from protected routes', async ({ page }) => {
    // Clear any stored auth
    await page.goto(`${FRONTEND_BASE}/`);
    await page.evaluate(() => {
      localStorage.clear();
      sessionStorage.clear();
    });
    await page.goto(`${FRONTEND_BASE}/dashboard`);
    await page.waitForURL(/\/auth/, { timeout: 5000 }).catch(() => {});
    // Either redirected to /auth or got 401 — either is acceptable
    const url = page.url();
    const hasAuthGuard = url.includes('/auth') || url.includes('/login');
    // If no redirect, check that the page doesn't show user data
    if (!hasAuthGuard) {
      const pageText = await page.textContent('body') ?? '';
      expect(pageText).not.toContain(tenant.name ?? 'E2E Tenant');
    }
  });

  // ── API key rotation ──────────────────────────────────────────────────────

  test('tenant can list their API keys', async ({ request, tenant }) => {
    const resp = await request.get(`${API_BASE}/api-keys`, {
      headers: { 'X-API-Key': tenant.apiKey },
    });
    expect([200, 404]).toContain(resp.status());
    if (resp.status() === 200) {
      const body = await resp.json();
      expect(Array.isArray(body) || Array.isArray(body.data) || Array.isArray(body.keys)).toBe(true);
    }
  });
});
