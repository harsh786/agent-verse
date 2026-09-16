/**
 * Real E2E tests for Gateway settings — NO HTTP mocking.
 *
 * Routes (both render the same component):
 *   /settings/gateway        — src/features/gateway/GatewaySettingsPage.tsx
 *   /org/:orgId/gateway
 *
 * Backing API:
 *   GET /v1/gateway/{org_id}/config — per-org channel config (rate limits,
 *     2FA requirements, channel list). The page itself calls the org-less
 *     `/v1/gateway/config`, which 404s; it swallows that error and falls
 *     back to static channel definitions, so the page still renders fine.
 *
 * NOTE: this file creates a single shared tenant in `beforeAll` (instead of
 * one tenant per test) because /tenants/signup is IP-rate-limited to 10/hr,
 * a budget shared with other real-e2e suites and other agents running
 * against the same local backend.
 *
 * Run:
 *   npx playwright test --config=playwright.real-e2e.config.ts e2e/real-e2e/gateway.real.spec.ts
 */

import { test, expect, request as pwRequest, type APIRequestContext } from '@playwright/test';
import { createE2ETenant, apiClient, loginFrontend, FRONTEND_BASE, type E2ETenant } from './fixtures';

let tenant: E2ETenant;
let api: ReturnType<typeof apiClient>;
let requestContext: APIRequestContext;

test.beforeAll(async () => {
  // A manually-created APIRequestContext (rather than the test-scoped `request`
  // fixture) so it can be reused across every test in this file — Playwright
  // forbids reusing a beforeAll-scoped `request` fixture inside a test.
  requestContext = await pwRequest.newContext();
  tenant = await createE2ETenant(requestContext, '-gateway');
  api = apiClient(requestContext, tenant);
});

test.afterAll(async () => {
  await requestContext.dispose();
});

test.describe('Gateway — API', () => {
  test('org-scoped gateway config API returns real channel config', async () => {
    const resp = await api.get('/v1/gateway/e2e-test-org/config');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(typeof body.max_commands_per_hour).toBe('number');
    expect(body).toHaveProperty('mcp_enabled');
    expect(body).toHaveProperty('webhook_enabled');
  });

  test('org-less gateway config path used by the frontend 404s (page has a fallback)', async () => {
    const resp = await api.get('/v1/gateway/config');
    expect(resp.status()).toBe(404);
  });
});

test.describe('Gateway — settings page', () => {
  test('/settings/gateway renders the Command Gateway UI', async ({ page }) => {
    await loginFrontend(page, tenant);
    await page.goto(`${FRONTEND_BASE}/settings/gateway`, { waitUntil: 'networkidle' });
    await expect(page.locator('body')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Command Gateway' })).toBeVisible();
    // Falls back to the static channel list (REST API is always-on)
    await expect(page.getByText('REST API')).toBeVisible();
    const text = (await page.locator('body').textContent()) ?? '';
    expect(text.toLowerCase()).not.toContain('internal server error');
  });
});

test.describe('Gateway — org-scoped page', () => {
  test('/org/:orgId/gateway renders the same Command Gateway UI', async ({ page }) => {
    await loginFrontend(page, tenant);
    await page.goto(`${FRONTEND_BASE}/org/some-org-id/gateway`, { waitUntil: 'networkidle' });
    await expect(page.locator('body')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Command Gateway' })).toBeVisible();
    const text = (await page.locator('body').textContent()) ?? '';
    expect(text.toLowerCase()).not.toContain('internal server error');
  });
});
