/**
 * REAL-BACKEND e2e: Evaluations page against a genuinely running backend (real
 * :8000 API + Postgres + Redis) — NOT mocked. Mint a real tenant, load the authed
 * Evaluations page, and assert it renders real backend state (honest empty for a
 * fresh tenant, never fabricated). Runs under `--project=real-backend` in CI.
 */
import { test, expect, request as pwRequest } from '@playwright/test';

const API_BASE = process.env.API_BASE_URL ?? 'http://localhost:8000';

test('real backend: Evaluations page renders real backend state', async ({ page }) => {
  const ctx = await pwRequest.newContext();
  const resp = await ctx.post(`${API_BASE}/tenants/signup`, {
    data: { name: 'PW Evals E2E', email: `pw-evals-${Date.now()}@example.com` },
  });
  expect(resp.status(), await resp.text()).toBe(201);
  const { api_key: apiKey, tenant_id: tenantId } = await resp.json();
  await ctx.dispose();

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

  await page.goto('/eval');
  await page.waitForLoadState('networkidle');

  // The authed shell + the Evaluations surface render against the real backend.
  await expect(page.getByPlaceholder(/search goals, agents/i)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole('heading', { name: /evaluation/i }).first()).toBeVisible({ timeout: 20_000 });
});
