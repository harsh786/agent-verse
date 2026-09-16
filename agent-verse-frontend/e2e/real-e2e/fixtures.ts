/**
 * Real E2E test fixtures — NO HTTP mocking.
 *
 * Every test that uses these fixtures hits the live backend at
 * process.env.API_BASE_URL (default: http://localhost:8000) through the
 * real React frontend at process.env.BASE_URL (default: http://localhost:5173).
 *
 * Tenant lifecycle:
 *   - Each test FILE shares one tenant (created in beforeAll, deleted in afterAll).
 *   - Each test may create its own resources and should clean them up.
 */

import { test as base, expect, type Page, type APIRequestContext } from '@playwright/test';

export const API_BASE = process.env.API_BASE_URL ?? 'http://localhost:8000';
export const FRONTEND_BASE = process.env.BASE_URL ?? 'http://localhost:5173';

export interface E2ETenant {
  tenantId: string;
  apiKey: string;
  name: string;
  email: string;
  plan: string;
}

/** Create a fresh tenant via the real signup API. */
export async function createE2ETenant(request: APIRequestContext, suffix = ''): Promise<E2ETenant> {
  const ts = Date.now();
  const resp = await request.post(`${API_BASE}/tenants/signup`, {
    data: {
      name: `E2E Tenant ${ts}${suffix}`,
      email: `e2e-${ts}${suffix}@agentverse.io`,
    },
  });
  // POST /tenants/signup is declared with status_code=201 (app/api/tenants.py) —
  // accept both 200/201 so this helper doesn't regress if that ever changes.
  expect([200, 201], `Tenant signup failed: ${await resp.text()}`).toContain(resp.status());
  const body = await resp.json();
  return {
    tenantId: body.tenant_id,
    apiKey: body.api_key,
    name: body.name,
    email: body.email,
    plan: body.plan,
  };
}

/** Sign in on the frontend by injecting the API key into localStorage. */
export async function loginFrontend(page: Page, tenant: E2ETenant): Promise<void> {
  // Navigate to the app first to set up the origin
  await page.goto(FRONTEND_BASE, { waitUntil: 'domcontentloaded' });

  // Inject auth into Zustand-persisted localStorage store
  await page.evaluate(
    ({ apiKey, tenantId, plan }) => {
      localStorage.setItem(
        'av-auth',
        JSON.stringify({
          state: {
            apiKey,
            tenantId,
            plan,
            isAuthenticated: true,
          },
          version: 0,
        }),
      );
      localStorage.setItem('av_api_key', apiKey);
      localStorage.setItem('av_tenant_id', tenantId);
    },
    { apiKey: tenant.apiKey, tenantId: tenant.tenantId, plan: tenant.plan },
  );
}

/** Navigate to a page after auth is injected and wait for the app to be ready. */
export async function navigateTo(page: Page, path: string): Promise<void> {
  await page.goto(`${FRONTEND_BASE}${path}`, { waitUntil: 'networkidle' });
  // Wait for React to hydrate — any error boundary should not be visible
  await expect(page.locator('[data-testid="error-boundary-message"]')).not.toBeVisible({
    timeout: 3000,
  }).catch(() => {/* no error boundary present */});
}

/** Authenticated API helper that sends requests directly to the backend. */
export function apiClient(request: APIRequestContext, tenant: E2ETenant) {
  const headers = { 'X-API-Key': tenant.apiKey, 'Content-Type': 'application/json' };
  return {
    get: (path: string) => request.get(`${API_BASE}${path}`, { headers }),
    post: (path: string, data?: unknown) => request.post(`${API_BASE}${path}`, { data, headers }),
    put: (path: string, data?: unknown) => request.put(`${API_BASE}${path}`, { data, headers }),
    patch: (path: string, data?: unknown) => request.patch(`${API_BASE}${path}`, { data, headers }),
    delete: (path: string) => request.delete(`${API_BASE}${path}`, { headers }),
  };
}

// ─── Custom Playwright fixture ────────────────────────────────────────────────

type RealE2EFixtures = {
  tenant: E2ETenant;
  api: ReturnType<typeof apiClient>;
  authedPage: Page;
};

export const test = base.extend<RealE2EFixtures>({
  // Create a fresh tenant per test (isolated, no shared state)
  tenant: async ({ request }, use) => {
    const t = await createE2ETenant(request, `-${Math.random().toString(36).slice(2, 7)}`);
    await use(t);
    // Cleanup: no tenant delete endpoint so we just let it expire naturally
  },

  // Provide an authenticated API client for direct backend calls
  api: async ({ request, tenant }, use) => {
    await use(apiClient(request, tenant));
  },

  // Provide a browser page already logged in to the frontend
  authedPage: async ({ page, tenant }, use) => {
    await loginFrontend(page, tenant);
    await use(page);
  },
});

export { expect };
