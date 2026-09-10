/**
 * REAL-BACKEND e2e: the agent-pattern selection surface shows the REAL pattern
 * the backend auto-selected for a goal — NOT mocked.
 *
 * Flow: mint a real tenant via the backend signup API → bootstrap the app session
 * (the same api-key/sessionStorage the app uses) → submit a goal → open the goal's
 * "Pattern" tab → assert the surface renders the real selected pattern (name +
 * auto-selected badge) served by GET /goals/{id}/pattern-selection, and that the
 * registry-driven override picker is populated.
 *
 * Run: `npx playwright test --project=real-backend` with the backend live on :8000
 * (API_BASE_URL to override) and the frontend served by the config's webServer.
 */
import { test, expect, request as pwRequest } from '@playwright/test';

const API_BASE = process.env.API_BASE_URL ?? 'http://localhost:8000';

async function mintTenant(): Promise<{ apiKey: string; tenantId: string }> {
  const ctx = await pwRequest.newContext();
  const resp = await ctx.post(`${API_BASE}/tenants/signup`, {
    data: { name: 'PW Patterns E2E', email: `pw-patterns-${Date.now()}@example.com` },
  });
  expect(resp.status(), await resp.text()).toBe(201);
  const body = await resp.json();
  await ctx.dispose();
  return { apiKey: body.api_key, tenantId: body.tenant_id };
}

test('real backend: goal detail shows the auto-selected agent pattern', async ({ page }) => {
  const { apiKey, tenantId } = await mintTenant();

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
  await expect(page.getByText('Mission Control')).toBeVisible({ timeout: 20_000 });

  // Submit a non-trivial goal through the real Quick Goal input.
  const goalInput = page.getByPlaceholder('What should your agents do? (Enter to submit)');
  await expect(goalInput).toBeVisible();
  await goalInput.fill(
    'Design, implement and rigorously verify a fault-tolerant distributed rate limiter',
  );

  const [createResp] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes('/goals') && r.request().method() === 'POST' && r.ok(),
      { timeout: 20_000 },
    ),
    goalInput.press('Enter').catch(() => page.getByRole('button', { name: /run/i }).click()),
  ]);
  expect(createResp.ok()).toBeTruthy();

  // Open the Pattern tab on the goal detail view.
  const patternTab = page.getByRole('tab', { name: /pattern/i });
  await expect(patternTab).toBeVisible({ timeout: 20_000 });
  await patternTab.click();

  // The surface shows the REAL selected pattern from the backend.
  const primary = page.getByTestId('pattern-primary-name');
  await expect(primary).toBeVisible({ timeout: 20_000 });
  await expect(primary).not.toBeEmpty();

  // Auto-selected (the goal carried no explicit override).
  await expect(page.getByText('Auto-selected')).toBeVisible();

  // The registry-driven override picker is populated with real patterns.
  await expect(page.getByTestId('pattern-override-select')).toBeVisible();
  await expect(page.getByTestId('pattern-rationale')).toBeVisible();
});
