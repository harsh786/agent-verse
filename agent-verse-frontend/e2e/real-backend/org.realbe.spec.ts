/**
 * REAL-BACKEND e2e: AI Org against a genuinely running backend (real :8000 API +
 * Postgres + Redis) — NOT mocked. Mint a real tenant, load the authed Organizations
 * page, create an org, and assert the real backend persisted it and the org list
 * reflects it. Runs under `--project=real-backend` in the nightly CI job.
 */
import { test, expect, request as pwRequest } from '@playwright/test';

const API_BASE = process.env.API_BASE_URL ?? 'http://localhost:8000';

async function mintTenant(): Promise<{ apiKey: string; tenantId: string }> {
  const ctx = await pwRequest.newContext();
  const resp = await ctx.post(`${API_BASE}/tenants/signup`, {
    data: { name: 'PW Org E2E', email: `pw-org-${Date.now()}@example.com` },
  });
  expect(resp.status(), await resp.text()).toBe(201);
  const body = await resp.json();
  await ctx.dispose();
  return { apiKey: body.api_key, tenantId: body.tenant_id };
}

async function bootstrapAuth(page: import('@playwright/test').Page, apiKey: string, tenantId: string) {
  await page.addInitScript(
    ({ apiKey, tenantId }) => {
      const blob = {
        state: {
          apiKey, tenantId, plan: 'free', isAuthenticated: true, ssoMode: false,
          accessToken: '', refreshToken: '', tokenExpiresAt: 0,
          sessionValidated: false, mfaRequired: false, mfaToken: null,
        },
        version: 0,
      };
      sessionStorage.setItem('av-auth', JSON.stringify(blob));
      sessionStorage.setItem('av_api_key', apiKey);
    },
    { apiKey, tenantId },
  );
}

test('real backend: Organizations page renders and an org can be created', async ({ page }) => {
  const { apiKey, tenantId } = await mintTenant();
  await bootstrapAuth(page, apiKey, tenantId);

  await page.goto('/org');
  // Honest empty state for a fresh tenant (real data, not fabricated).
  await expect(page.getByText(/AI Organizations/i)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(/no organizations yet/i)).toBeVisible({ timeout: 20_000 });

  // Create an org straight against the real backend, then confirm the list reflects it.
  const ctx = await pwRequest.newContext({ extraHTTPHeaders: { 'X-API-Key': apiKey } });
  const created = await ctx.post(`${API_BASE}/v1/org`, { data: { name: 'PW Live Org' } });
  expect(created.status(), await created.text()).toBe(201);
  await ctx.dispose();

  await page.reload();
  await expect(page.getByText('PW Live Org')).toBeVisible({ timeout: 20_000 });
});
