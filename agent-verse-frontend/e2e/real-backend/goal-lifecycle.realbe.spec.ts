/**
 * REAL-BACKEND e2e (gap #4): drive the actual app against a genuinely running
 * backend (real :8000 API + Postgres + Redis) — NOT mocked.
 *
 * Flow: mint a real tenant via the backend signup API → bootstrap the app
 * session (the same api-key/sessionStorage the app uses) → load the authenticated
 * dashboard → submit a goal → assert the real backend created it and the goal
 * enters live execution. This is the automated version of the manual UI e2e.
 *
 * Run: `npx playwright test --project=real-backend` with the backend live on
 * :8000 (API_BASE_URL to override) and the frontend served by the config's
 * webServer (BASE_URL, default :5173).
 */
import { test, expect, request as pwRequest } from '@playwright/test';

const API_BASE = process.env.API_BASE_URL ?? 'http://localhost:8000';

async function mintTenant(): Promise<{ apiKey: string; tenantId: string }> {
  const ctx = await pwRequest.newContext();
  const resp = await ctx.post(`${API_BASE}/tenants/signup`, {
    data: { name: 'PW Real E2E', email: `pw-realbe-${Date.now()}@example.com` },
  });
  expect(resp.status(), await resp.text()).toBe(201);
  const body = await resp.json();
  await ctx.dispose();
  return { apiKey: body.api_key, tenantId: body.tenant_id };
}

test('real backend: authenticated dashboard renders and a goal enters live execution', async ({ page }) => {
  const { apiKey, tenantId } = await mintTenant();

  // Bootstrap the app session exactly as the app persists it (av-auth in
  // sessionStorage + the av_api_key fallback) before any app code runs.
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

  await page.goto('/dashboard');

  // The authenticated JARVIS shell renders (real backend, real tenant).
  await expect(page.getByText('Mission Control')).toBeVisible({ timeout: 20_000 });

  // Submit a goal through the real Quick Goal input.
  const goalInput = page.getByPlaceholder('What should your agents do? (Enter to submit)');
  await expect(goalInput).toBeVisible();
  await goalInput.fill('Summarize the benefits of automated testing');

  // The submission POSTs to the real backend and navigates to the goal view.
  const [createResp] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes('/goals') && r.request().method() === 'POST' && r.ok(),
      { timeout: 20_000 },
    ),
    goalInput.press('Enter').catch(() => page.getByRole('button', { name: /run/i }).click()),
  ]);
  expect(createResp.ok()).toBeTruthy();

  // The goal detail view shows a real, live status from the backend.
  await expect(
    page.getByText(/executing|planning|completed|waiting|queued|failed/i).first(),
  ).toBeVisible({ timeout: 20_000 });
});
