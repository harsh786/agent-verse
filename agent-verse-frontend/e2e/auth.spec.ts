import { test, expect, type Page } from '@playwright/test';

const TENANT_RESPONSE = {
  tenant_id: 'test-tenant',
  name: 'Test Org',
  plan: 'free',
};

/** Mock the /tenants/me endpoint used by RequireAuth for session validation. */
async function mockTenantValidation(page: Page) {
  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(TENANT_RESPONSE),
    })
  );
}

/** Mock /goals and /goals/metrics so the Dashboard page renders cleanly. */
async function mockDashboardApis(page: Page) {
  await page.route(/localhost:8000\/goals/, (route) => {
    const url = route.request().url();
    if (url.includes('/metrics')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          active_goals: 0,
          total_goals: 0,
          success_rate: 0,
          avg_latency_ms: 0,
          cost_today_usd: 0,
          goals_today: 0,
        }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ goals: [] }),
    });
  });
}

test.describe('Authentication', () => {
  // ── Unauthenticated redirects ────────────────────────────────────────────────

  test('redirects unauthenticated user from / to /auth', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/(auth|login)/);
  });

  // ── Auth form structure ──────────────────────────────────────────────────────

  test('shows AgentVerse branding, Tenant ID field, API Key field, and Sign in button', async ({
    page,
  }) => {
    await page.goto('/auth');
    await expect(page.getByText('AgentVerse')).toBeVisible();
    await expect(page.getByText('Sign in to your tenant')).toBeVisible();
    await expect(page.locator('#tenantId')).toBeVisible();
    await expect(page.locator('#apiKey')).toBeVisible();
    await expect(page.getByRole('button', { name: /sign in/i })).toBeVisible();
  });

  test('/login route also renders the auth form', async ({ page }) => {
    await page.goto('/login');
    await expect(page.locator('#tenantId')).toBeVisible({ timeout: 10000 });
    await expect(page.locator('#apiKey')).toBeVisible();
  });

  // ── Validation ───────────────────────────────────────────────────────────────

  test('shows "required" error when submitting with empty fields', async ({ page }) => {
    await page.goto('/auth');
    await page.fill('#tenantId', '   ');
    await page.fill('#apiKey', '   ');
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page.getByRole('alert')).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('alert')).toContainText(/required/i);
  });

  test('shows error when API key is rejected (401 from /tenants/me)', async ({ page }) => {
    await page.route('**/tenants/me', (route) =>
      route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ error: { message: 'Unauthorized' } }),
      })
    );
    await page.goto('/auth');
    await page.fill('#tenantId', 'test-tenant');
    await page.fill('#apiKey', 'bad-key-xyz');
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page.getByRole('alert')).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('alert')).toContainText(/invalid/i);
  });

  test('shows error when tenant ID does not match the API key response', async ({ page }) => {
    await page.route('**/tenants/me', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        // Returns a DIFFERENT tenant_id than what user typed
        body: JSON.stringify({ tenant_id: 'some-other-org', name: 'Other Org', plan: 'free' }),
      })
    );
    await page.goto('/auth');
    await page.fill('#tenantId', 'test-tenant');
    await page.fill('#apiKey', 'some-valid-key');
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page.getByRole('alert')).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('alert')).toContainText(/invalid/i);
  });

  test('Sign in button shows "Signing in…" text while submitting', async ({ page }) => {
    // Slow the response so we can catch the intermediate state
    await page.route('**/tenants/me', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 500));
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(TENANT_RESPONSE),
      });
    });
    await mockDashboardApis(page);
    await page.goto('/auth');
    await page.fill('#tenantId', 'test-tenant');
    await page.fill('#apiKey', 'valid-key');
    // Click and immediately check the button text
    const submitBtn = page.getByRole('button', { name: /sign in/i });
    await submitBtn.click();
    // Button should transition to "Signing in…" during the request
    await expect(page.getByRole('button', { name: /signing in/i })).toBeVisible({ timeout: 3000 });
  });

  // ── Successful login ─────────────────────────────────────────────────────────

  test('redirects to /dashboard after successful login', async ({ page }) => {
    await mockTenantValidation(page);
    await mockDashboardApis(page);
    await page.goto('/auth');
    await page.fill('#tenantId', 'test-tenant');
    await page.fill('#apiKey', 'valid-api-key');
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 15000 });
  });

  test('dashboard renders after successful login (no redirect back to auth)', async ({ page }) => {
    await mockTenantValidation(page);
    await mockDashboardApis(page);
    await page.goto('/auth');
    await page.fill('#tenantId', 'test-tenant');
    await page.fill('#apiKey', 'valid-api-key');
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page.locator('h1').filter({ hasText: /mission control/i })).toBeVisible({
      timeout: 15000,
    });
  });

  // ── Logout ───────────────────────────────────────────────────────────────────

  test('logout button clears session and redirects to /auth', async ({ page }) => {
    // Inject auth state into localStorage before the page loads
    await page.addInitScript(() => {
      localStorage.setItem(
        'av-auth',
        JSON.stringify({
          state: {
            apiKey: 'test-key',
            tenantId: 'test-tenant',
            plan: 'free',
            isAuthenticated: true,
          },
          version: 0,
        })
      );
      localStorage.setItem('av_api_key', 'test-key');
    });
    await mockTenantValidation(page);
    await mockDashboardApis(page);
    await page.goto('/dashboard');
    // Confirm we are on the dashboard
    await expect(page.locator('h1').filter({ hasText: /mission control/i })).toBeVisible({
      timeout: 15000,
    });
    // Click the Sign out button in the TopBar (aria-label="Sign out")
    await page.getByRole('button', { name: /sign out/i }).click();
    // Should redirect to /auth
    await expect(page).toHaveURL(/\/(auth|login)/, { timeout: 10000 });
  });

  test('after logout, visiting / redirects to /auth again', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem(
        'av-auth',
        JSON.stringify({
          state: {
            apiKey: 'test-key',
            tenantId: 'test-tenant',
            plan: 'free',
            isAuthenticated: true,
          },
          version: 0,
        })
      );
      localStorage.setItem('av_api_key', 'test-key');
    });
    await mockTenantValidation(page);
    await mockDashboardApis(page);
    await page.goto('/dashboard');
    await expect(page.locator('h1').filter({ hasText: /mission control/i })).toBeVisible({
      timeout: 15000,
    });
    await page.getByRole('button', { name: /sign out/i }).click();
    await expect(page).toHaveURL(/\/(auth|login)/, { timeout: 10000 });
    // Navigate to / and confirm redirect to /auth
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/(auth|login)/);
  });
});
