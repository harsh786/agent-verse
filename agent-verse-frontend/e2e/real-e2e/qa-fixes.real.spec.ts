/**
 * Real E2E (no mocking): UI behaviour behind the QA fixes, against the live stack.
 *
 *   QA-rotate  rotating an API key shows the NEW key (not "undefined")
 *   QA-6       /admin as a tenant admin does not sign the user out
 *   QA-17      billing shows the enforced plan limits
 *   QA-8       the policy form says the policy is active only inside the window
 *   QA-3       a key can be minted with a role and that role can read costs
 */
import { readFileSync } from 'node:fs';
import { test, expect, type Page } from '@playwright/test';
import {
  API_BASE,
  FRONTEND_BASE,
  createE2ETenant,
  loginFrontend,
  type E2ETenant,
} from './fixtures';

let tenant: E2ETenant;

test.beforeAll(async ({ request }) => {
  // QA_STATE_FILE: reuse a tenant ({tenant_id, admin}) instead of signing up (signup
  // is limited to 10/hour/IP). The key stays in that private file.
  const stateFile = process.env.QA_STATE_FILE;
  if (stateFile) {
    const st = JSON.parse(readFileSync(stateFile, 'utf8')) as { tenant_id: string; admin: string };
    tenant = { tenantId: st.tenant_id, apiKey: st.admin, name: 'reused', email: '', plan: 'enterprise' };
  } else {
    tenant = await createE2ETenant(request, '-qafix');
  }
  // The free plan caps API keys and requests/min; use enterprise. This also
  // exercises QA-6: a tenant admin (no X-Admin-Key) may call /admin.
  const up = await request.put(`${API_BASE}/admin/tenants/${tenant.tenantId}/plan`, {
    headers: { 'X-API-Key': tenant.apiKey },
    data: { plan: 'enterprise' },
  });
  expect(up.status(), 'tenant admin can use /admin').toBe(200);
});

async function open(page: Page, path: string) {
  await loginFrontend(page, tenant);
  // 'domcontentloaded': pages with a live event stream never reach network-idle.
  await page.goto(`${FRONTEND_BASE}${path}`, { waitUntil: 'domcontentloaded' });
}

test('rotating an API key shows the new key in the banner, not "undefined"', async ({ page }) => {
  await open(page, '/settings?tab=apikeys');
  // Create a key to rotate.
  await page.getByRole('button', { name: /new key|create key|add key/i }).first().click();
  await page.getByPlaceholder(/name/i).first().fill('rotate-me');
  await page.getByRole('button', { name: /^create$/i }).click();
  await expect(page.getByText(/Key created/)).toBeVisible();
  await page.getByRole('button', { name: /dismiss/i }).click();

  await page.getByText('rotate-me').waitFor();
  const row = page.locator('tr, li, div').filter({ hasText: 'rotate-me' }).last();
  await row.getByTitle('Rotate').click();

  const banner = page.getByText(/Key rotated/);
  await expect(banner).toBeVisible();
  const code = page.locator('code').filter({ hasText: /^av_/ }).first();
  await expect(code).toBeVisible();
  await expect(code).not.toHaveText(/undefined/);
  expect((await code.innerText()).length).toBeGreaterThan(20);
  await expect(page.getByText('undefined', { exact: true })).toHaveCount(0);
  await page.screenshot({ path: 'test-results/qa-rotate-banner.png' });
});

test('/admin as a tenant admin stays signed in', async ({ page }) => {
  await open(page, '/admin');
  await page.waitForTimeout(3000); // the old code logged the user out within seconds
  expect(page.url()).not.toMatch(/login/);
  const stored = await page.evaluate(() => localStorage.getItem('av_api_key'));
  expect(stored).toBeTruthy();
  await expect(page.getByText(/session expired/i)).toHaveCount(0);
  await page.screenshot({ path: 'test-results/qa-admin-page.png' });
});

test('billing shows the limits the platform enforces', async ({ page, request }) => {
  const plans = (await (await request.get(`${API_BASE}/billing/plans`, {
    headers: { 'X-API-Key': tenant.apiKey },
  })).json()) as { plans?: Array<{ plan_id: string; limits: Record<string, number> }> };
  const list = Array.isArray(plans) ? plans : (plans.plans ?? []);
  const starter = list.find((p) => p.plan_id === 'starter');
  expect(starter?.limits.agents).toBe(10);
  expect(starter?.limits.goals_per_day).toBe(100);

  await open(page, '/settings/billing');
  await expect(page.getByText(/starter/i).first()).toBeVisible();
  // The page shows the enforced numbers from the API (100 goals/day, 10 agents).
  await expect(page.getByText(/100/).first()).toBeVisible();
  await page.screenshot({ path: 'test-results/qa-billing.png', fullPage: true });
});

test('the policy form labels the time window as active hours', async ({ page }) => {
  await open(page, '/governance');
  await page.getByRole('button', { name: /new policy|add policy|create policy/i }).first().click();
  await expect(page.getByText(/Active only during a time window/i)).toBeVisible();
  await page.getByText(/Active only during a time window/i).click();
  await expect(page.getByText(/Active hours \(UTC\)/i)).toBeVisible();
  await page.screenshot({ path: 'test-results/qa-policy-form.png' });
});

test('a key minted with the viewer role can read costs (role hierarchy)', async ({ request }) => {
  const call = (fn: () => Promise<import('@playwright/test').APIResponse>) => fn();
  const minted = await call(() =>
    request.post(`${API_BASE}/tenants/me/keys`, {
      headers: { 'X-API-Key': tenant.apiKey },
      data: { name: 'viewer-key', roles: ['viewer'] },
    }),
  );
  expect(minted.status()).toBe(201);
  const viewerKey = (await minted.json()).raw_key as string;
  for (const path of ['/costs/summary', '/governance/policies', '/governance/audit']) {
    const r = await call(() =>
      request.get(`${API_BASE}${path}`, { headers: { 'X-API-Key': viewerKey } }),
    );
    expect(r.status(), path).not.toBe(403);
  }
});
