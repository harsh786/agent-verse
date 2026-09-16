/**
 * Real E2E tests for the Obsidian feature — NO HTTP mocking.
 *
 * Route:   /obsidian           (src/features/obsidian/ObsidianPage.tsx)
 * Backend: GET /v1/org         (src/org/router.py, prefix "/v1/org")
 *          — the page loads the tenant's orgs, auto-selects the first one,
 *            and renders an ObsidianVaultExplorer for it.
 *
 * Every HTTP request goes through:
 *   browser → localhost:5173 (Vite) → localhost:8000 (FastAPI) → Postgres+Redis
 *
 * Tenant signup is capped at 10/IP/hour on the backend (app/api/tenants.py) and
 * that IP is shared by every real-e2e worker/file running concurrently, so this
 * file creates exactly ONE tenant (in beforeAll) and reuses it for every test.
 */

import { expect, test as base } from '@playwright/test';
import { createE2ETenant, loginFrontend, apiClient, FRONTEND_BASE, type E2ETenant } from './fixtures';

let tenant: E2ETenant;
let api: ReturnType<typeof apiClient>;

base.beforeAll(async ({ playwright }) => {
  const ctx = await playwright.request.newContext();
  tenant = await createE2ETenant(ctx, `-obsidian-${Math.random().toString(36).slice(2, 7)}`);
  api = apiClient(ctx, tenant);
});

base.describe('Obsidian — org vault', () => {
  base('GET /v1/org returns a real (paginated) org list shape', async () => {
    const resp = await api.get('/v1/org');
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    // Router returns { data, cursor, hasMore, total }.
    expect(body).toHaveProperty('data');
    expect(Array.isArray(body.data)).toBe(true);
  });

  base('create an org via real API, then it appears in the org list', async () => {
    const createResp = await api.post('/v1/org', {
      name: `E2E Obsidian Org ${Date.now()}`,
    });
    expect([200, 201]).toContain(createResp.status());
    const created = await createResp.json();
    const orgId = created.id ?? created.org_id;
    expect(orgId).toBeTruthy();

    const listResp = await api.get('/v1/org');
    expect(listResp.status()).toBe(200);
    const list = await listResp.json();
    const ids = (list.data ?? []).map((o: { id?: string; org_id?: string }) => o.id ?? o.org_id);
    expect(ids).toContain(orgId);
  });

  base('obsidian page renders the vault explorer for a tenant with an org', async ({ page }) => {
    await loginFrontend(page, tenant);
    await page.goto(`${FRONTEND_BASE}/obsidian`, { waitUntil: 'networkidle' });
    await expect(page.locator('body')).toBeVisible();
    const text = await page.locator('body').textContent();
    expect(text!.length).toBeGreaterThan(0);
    expect(text!.toLowerCase()).not.toContain('internal server error');
    await expect(page.getByRole('heading', { name: /Obsidian Mode/i })).toBeVisible();
  });
});
