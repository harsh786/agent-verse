/**
 * Real E2E tests for Eval Suites — NO HTTP mocking.
 *
 * Route: /eval-suites — src/features/eval-suites/EvalSuitesPage.tsx
 * Backing API:
 *   GET  /intelligence/eval-suites             — list suites
 *   POST /intelligence/eval-suites             — create a suite
 *   GET  /intelligence/eval-suites/{id}/results — historical run results
 *
 * NOTE: this file creates a single shared tenant in `beforeAll` (instead of
 * one tenant per test) because /tenants/signup is IP-rate-limited to 10/hr,
 * a budget shared with other real-e2e suites and other agents running
 * against the same local backend.
 *
 * Run:
 *   npx playwright test --config=playwright.real-e2e.config.ts e2e/real-e2e/eval-suites.real.spec.ts
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
  tenant = await createE2ETenant(requestContext, '-eval-suites');
  api = apiClient(requestContext, tenant);
});

test.afterAll(async () => {
  await requestContext.dispose();
});

test.describe('Eval Suites — API', () => {
  test('eval-suites list returns an array for a new tenant', async () => {
    const resp = await api.get('/intelligence/eval-suites');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body)).toBe(true);
  });

  test('create a suite via the real API, then see it in the list and its results', async () => {
    const createResp = await api.post('/intelligence/eval-suites', {
      name: `E2E Suite ${Date.now()}`,
      description: 'Created by real-e2e test',
    });
    expect(createResp.status()).toBe(201);
    const created = await createResp.json();
    expect(created.suite_id).toBeTruthy();

    const listResp = await api.get('/intelligence/eval-suites');
    expect(listResp.status()).toBe(200);
    const suites = await listResp.json();
    expect(suites.some((s: { suite_id: string }) => s.suite_id === created.suite_id)).toBe(true);

    const resultsResp = await api.get(`/intelligence/eval-suites/${created.suite_id}/results`);
    expect(resultsResp.status()).toBe(200);
    const results = await resultsResp.json();
    expect(Array.isArray(results)).toBe(true);
  });
});

test.describe('Eval Suites — page', () => {
  test('eval suites page renders without a critical error', async ({ page }) => {
    await loginFrontend(page, tenant);
    await page.goto(`${FRONTEND_BASE}/eval-suites`, { waitUntil: 'networkidle' });
    await expect(page.locator('body')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Eval Suites' })).toBeVisible();
    const text = (await page.locator('body').textContent()) ?? '';
    expect(text.toLowerCase()).not.toContain('internal server error');
  });

  test('the New Suite modal opens and accepts input', async ({ page }) => {
    await loginFrontend(page, tenant);
    await page.goto(`${FRONTEND_BASE}/eval-suites`, { waitUntil: 'networkidle' });
    const suiteName = `UI Suite ${Date.now()}`;

    await page.getByRole('button', { name: /New Suite/i }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();

    const nameInput = page.getByPlaceholder('Q1 Quality Benchmarks');
    await nameInput.fill(suiteName);
    await expect(nameInput).toHaveValue(suiteName);

    const submit = page.getByRole('button', { name: /Create Suite/i });
    await expect(submit).toBeEnabled();
  });
});
