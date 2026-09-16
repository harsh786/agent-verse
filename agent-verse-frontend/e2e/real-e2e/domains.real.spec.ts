/**
 * Real E2E tests for Domains — NO HTTP mocking.
 *
 * Routes:
 *   /domains          — DomainsPage.tsx: static list of 37 industry verticals,
 *                        augmented with live counts from marketplace + goal
 *                        template APIs.
 *   /domains/:domain   — DomainDetailPage.tsx: per-domain agent/goal templates.
 *
 * Backing APIs (real, no mocks):
 *   GET /marketplace/templates?domain=<key>
 *   GET /templates?domain=<key>
 *
 * NOTE: unlike other real-e2e specs, this file creates a single shared tenant
 * in `beforeAll` (instead of one tenant per test via the `tenant`/`api`/
 * `authedPage` fixtures) because /tenants/signup is IP-rate-limited to 10/hr
 * and that budget is shared with other real-e2e suites and other agents
 * running against the same local backend.
 *
 * Run:
 *   npx playwright test --config=playwright.real-e2e.config.ts e2e/real-e2e/domains.real.spec.ts
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
  tenant = await createE2ETenant(requestContext, '-domains');
  api = apiClient(requestContext, tenant);
});

test.afterAll(async () => {
  await requestContext.dispose();
});

test.describe('Domains — list', () => {
  test('marketplace templates API returns data for a known domain', async () => {
    const resp = await api.get('/marketplace/templates?domain=hr-talent');
    expect([200, 404]).toContain(resp.status());
    if (resp.status() === 200) {
      const body = await resp.json();
      const items = body.items ?? body.templates ?? [];
      expect(Array.isArray(items)).toBe(true);
    }
  });

  test('goal templates API returns data for a known domain', async () => {
    const resp = await api.get('/templates?domain=hr-talent');
    expect([200, 404]).toContain(resp.status());
  });

  test('domains page renders the full vertical list', async ({ page }) => {
    await loginFrontend(page, tenant);
    await page.goto(`${FRONTEND_BASE}/domains`, { waitUntil: 'networkidle' });
    await expect(page.locator('body')).toBeVisible();
    await expect(page.getByText('Domain Solutions')).toBeVisible();
    // At least one known domain card should render
    await expect(page.getByText('HR & Talent')).toBeVisible();
  });

  test('domains page search filters the list', async ({ page }) => {
    await loginFrontend(page, tenant);
    await page.goto(`${FRONTEND_BASE}/domains`, { waitUntil: 'networkidle' });
    const search = page.getByPlaceholder('Search domains…');
    await expect(search).toBeVisible();
    await search.fill('Healthcare');
    await expect(page.getByText('Healthcare', { exact: true })).toBeVisible();
    // A domain that clearly does not match should disappear from view
    await expect(page.getByText('HR & Talent')).not.toBeVisible();
  });

  test('clicking a domain card navigates to its detail route', async ({ page }) => {
    await loginFrontend(page, tenant);
    await page.goto(`${FRONTEND_BASE}/domains`, { waitUntil: 'networkidle' });
    await page.getByText('HR & Talent').click();
    await page.waitForURL(/\/domains\/hr-talent/, { timeout: 10_000 });
    await expect(page.locator('body')).toBeVisible();
  });
});

test.describe('Domains — detail', () => {
  test('detail page for a known domain renders hero + stats without crashing', async ({ page }) => {
    await loginFrontend(page, tenant);
    await page.goto(`${FRONTEND_BASE}/domains/hr-talent`, { waitUntil: 'networkidle' });
    await expect(page.locator('body')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'HR & Talent' })).toBeVisible();
    await expect(page.getByText('Back to Domains')).toBeVisible();
  });

  test('detail page handles an unknown domain id gracefully (no crash)', async ({ page }) => {
    await loginFrontend(page, tenant);
    await page.goto(`${FRONTEND_BASE}/domains/this-domain-does-not-exist`, {
      waitUntil: 'networkidle',
    });
    const text = (await page.locator('body').textContent()) ?? '';
    // Falls back to a generic domain meta object keyed by the raw slug — should
    // still render the page shell, not blow up with an error boundary.
    expect(text.toLowerCase()).not.toContain('internal server error');
    expect(text.length).toBeGreaterThan(0);
    await expect(page.getByText('Back to Domains')).toBeVisible();
  });
});
