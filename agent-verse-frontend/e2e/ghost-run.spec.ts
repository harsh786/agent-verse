import { test, expect, type Page } from '@playwright/test';

async function setupAuth(page: Page) {
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
  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'Test Org', plan: 'free' }),
    })
  );
}

let goalCounter = 0;

async function mockGoalsApi(page: Page) {
  goalCounter = 0;
  await page.route(/localhost:8000\/goals/, async (route) => {
    if (route.request().method() === 'POST') {
      goalCounter++;
      const suffix = goalCounter;
      return route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({
          id: `ghost-goal-${suffix}`,
          goal_id: `ghost-goal-${suffix}`,
          goal: 'Fix all JIRA prod-down bugs',
          status: suffix === 3 ? 'dry_run' : 'planning',
          created_at: new Date().toISOString(),
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

test.describe('Ghost Run', () => {
  test('page renders "Ghost Run" heading', async ({ page }) => {
    await setupAuth(page);
    await page.goto('/goals/ghost-run');

    await expect(
      page.locator('h1').filter({ hasText: /ghost run/i })
    ).toBeVisible({ timeout: 15000 });
  });

  test('goal textarea is present', async ({ page }) => {
    await setupAuth(page);
    await page.goto('/goals/ghost-run');

    await expect(
      page.locator('textarea').or(page.locator('#ghost-goal'))
    ).toBeVisible({ timeout: 15000 });
  });

  test('Launch Ghost Run button is visible', async ({ page }) => {
    await setupAuth(page);
    await page.goto('/goals/ghost-run');

    await expect(
      page.getByRole('button', { name: /launch ghost run/i })
    ).toBeVisible({ timeout: 15000 });
  });

  test('after submit, shows 3 strategy cards each with a Track link', async ({ page }) => {
    await setupAuth(page);
    await mockGoalsApi(page);
    await page.goto('/goals/ghost-run');

    // Fill in the goal text
    const textarea = page.locator('textarea').first();
    await expect(textarea).toBeVisible({ timeout: 15000 });
    await textarea.fill('Fix all JIRA prod-down bugs');

    // Click launch
    await page.getByRole('button', { name: /launch ghost run/i }).click();

    // Wait for the 3 strategy cards to appear
    await expect(page.locator('span.font-medium', { hasText: 'Standard' }).first()).toBeVisible({ timeout: 15000 });
    await expect(page.locator('span.font-medium', { hasText: 'High Priority' }).first()).toBeVisible();
    await expect(page.locator('span.font-medium', { hasText: 'Dry Run' }).first()).toBeVisible();

    // Each strategy card should have a Track button
    const trackButtons = page.locator('button', { hasText: 'Track' });
    await expect(trackButtons.first()).toBeVisible();
  });

  test('Launch Ghost Run button is disabled when textarea is empty', async ({ page }) => {
    await setupAuth(page);
    await page.goto('/goals/ghost-run');

    const launchBtn = page.getByRole('button', { name: /launch ghost run/i });
    await expect(launchBtn).toBeVisible({ timeout: 15000 });
    await expect(launchBtn).toBeDisabled();
  });
});
