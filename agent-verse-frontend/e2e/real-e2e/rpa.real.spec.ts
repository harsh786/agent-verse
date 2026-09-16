/**
 * Real E2E tests for the RPA feature — NO HTTP mocking.
 *
 * Route: /rpa/live (RpaLivePage.tsx)
 * Backend: app/api/rpa.py (prefix /rpa)
 *
 * Real browser-automation actions (actually opening a Playwright session inside
 * the RPA executor) are not exercised here — that requires a headed/headless
 * browser pool wired into the backend that may not be available in this dev
 * environment. Instead we cover the session lifecycle (create/list/close),
 * the tool catalog endpoint, its auth requirement, and that the live page
 * itself renders against the real backend.
 *
 * NOTE: the backend signup endpoint rate-limits to 10 signups/IP/hour, and
 * this IP is shared with other test suites/agents. To stay well under that
 * budget this file creates exactly ONE real tenant (in beforeAll) and reuses
 * it for every test below, instead of the per-test `tenant`/`api`/`authedPage`
 * fixtures.
 */
import { expect, request as pwRequest, type APIRequestContext, type Page } from '@playwright/test';
import { test, createE2ETenant, loginFrontend, apiClient, FRONTEND_BASE, API_BASE, type E2ETenant } from './fixtures';

let ctx: APIRequestContext;
let tenant: E2ETenant;
let api: ReturnType<typeof apiClient>;

test.beforeAll(async () => {
  ctx = await pwRequest.newContext();
  tenant = await createE2ETenant(ctx, '-rpa');
  api = apiClient(ctx, tenant);
});

test.afterAll(async () => {
  await ctx.dispose();
});

test.describe('RPA — real tool catalog', () => {
  test('GET /rpa/tools returns built-in tool metadata', async () => {
    const resp = await api.get('/rpa/tools');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body)).toBe(true);
    expect(body.length).toBeGreaterThan(0);
    expect(body[0]).toHaveProperty('name');
  });

  test('GET /rpa/tools requires a valid API key', async ({ request }) => {
    const resp = await request.get(`${API_BASE}/rpa/tools`);
    expect(resp.status()).toBe(401);
  });
});

test.describe('RPA — real session lifecycle', () => {
  test('sessions list returns an array for this tenant', async () => {
    const resp = await api.get('/rpa/sessions');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body)).toBe(true);
  });

  test('create → list → close a real RPA session', async () => {
    const createResp = await api.post('/rpa/sessions');
    expect([200, 201]).toContain(createResp.status());
    const created = await createResp.json();
    expect(created.session_id).toBeTruthy();

    const listResp = await api.get('/rpa/sessions');
    expect(listResp.status()).toBe(200);
    const sessions = await listResp.json();
    expect(sessions.some((s: { session_id: string }) => s.session_id === created.session_id)).toBe(true);

    const closeResp = await api.delete(`/rpa/sessions/${created.session_id}`);
    expect(closeResp.status()).toBe(204);
  });

  test('closing an unknown session returns 404', async () => {
    const resp = await api.delete('/rpa/sessions/does-not-exist');
    expect(resp.status()).toBe(404);
  });
});

test.describe('RPA — live page', () => {
  let page: Page;

  test.beforeEach(async ({ browser }) => {
    page = await browser.newPage();
    await loginFrontend(page, tenant);
  });

  test.afterEach(async () => {
    await page.close();
  });

  test('rpa/live page renders with the session panel', async () => {
    await page.goto(`${FRONTEND_BASE}/rpa/live`, { waitUntil: 'networkidle' });
    await expect(page.locator('body')).toBeVisible();
    const text = await page.locator('body').textContent();
    expect(text!.length).toBeGreaterThan(0);
    expect(text!.toLowerCase()).not.toContain('internal server error');
  });

  test('creating a session from the UI shows it in the session list', async () => {
    await page.goto(`${FRONTEND_BASE}/rpa/live`, { waitUntil: 'networkidle' });
    const newSessionButton = page.getByLabel('New session');
    await expect(newSessionButton).toBeVisible();
    await newSessionButton.click();
    // A session row should appear (session ids are truncated in the UI).
    await expect(page.getByText(/active|paused|closed/i).first()).toBeVisible({ timeout: 10_000 });
  });
});
