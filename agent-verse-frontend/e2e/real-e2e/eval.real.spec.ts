/**
 * Real E2E tests for Eval — NO HTTP mocking.
 *
 * Route: /eval — src/features/eval/EvalPage.tsx ("Eval & Testing")
 * Tabs inside the page hit, among others:
 *   GET  /intelligence/eval/dimensions   — the 7 scoring dimension names
 *   GET  /goals                         — goal list for the scorecard picker
 *   GET  /goals/{goalId}/eval           — a single goal's eval scorecard
 *   POST /enterprise/red-team           — red-team attack simulation
 *
 * NOTE: this file creates a single shared tenant in `beforeAll` (instead of
 * one tenant per test) because /tenants/signup is IP-rate-limited to 10/hr,
 * a budget shared with other real-e2e suites and other agents running
 * against the same local backend.
 *
 * Run:
 *   npx playwright test --config=playwright.real-e2e.config.ts e2e/real-e2e/eval.real.spec.ts
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
  tenant = await createE2ETenant(requestContext, '-eval');
  api = apiClient(requestContext, tenant);
});

test.afterAll(async () => {
  await requestContext.dispose();
});

test.describe('Eval — API', () => {
  test('eval dimensions API returns the 7 scoring dimensions', async () => {
    const resp = await api.get('/intelligence/eval/dimensions');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body.dimensions)).toBe(true);
    expect(body.dimensions.length).toBe(7);
    expect(body.count).toBe(7);
  });

  test('goal eval scorecard 404s gracefully for an unknown goal', async () => {
    const resp = await api.get('/goals/nonexistent-goal-id/eval');
    expect(resp.status()).toBe(404);
  });

  test('goals list API used by the scorecard picker returns an array', async () => {
    const resp = await api.get('/goals');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body.goals)).toBe(true);
  });
});

test.describe('Eval — page', () => {
  test('eval page renders without a critical error', async ({ page }) => {
    await loginFrontend(page, tenant);
    await page.goto(`${FRONTEND_BASE}/eval`, { waitUntil: 'networkidle' });
    await expect(page.locator('body')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Eval & Testing' })).toBeVisible();
    const text = (await page.locator('body').textContent()) ?? '';
    expect(text.toLowerCase()).not.toContain('internal server error');
  });
});
