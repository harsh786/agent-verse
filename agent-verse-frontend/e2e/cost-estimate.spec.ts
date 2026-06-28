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

async function mockBaseApis(page: Page) {
  await page.route(/localhost:8000\/goals/, (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ goals: [] }),
      });
    }
    return route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({ id: 'g-new', goal_id: 'g-new', status: 'planning', goal: 'test' }),
    });
  });
  await page.route(/localhost:8000\/agents/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  );
}

const MOCK_ESTIMATE = {
  estimated_cost_usd: { min: 0.002, mean: 0.008, max: 0.015 },
  estimated_duration_s: { min: 8, mean: 22, max: 45 },
  estimated_iterations: { min: 3, max: 7 },
  success_probability: 0.87,
  confidence: 'medium' as const,
  similar_goals_count: 12,
};

async function mockEstimateApi(page: Page) {
  await page.route('**/insights/estimate', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_ESTIMATE),
    })
  );
}

test.describe('CostEstimateWidget', () => {
  test('widget is hidden for short goal text (< 10 chars)', async ({ page }) => {
    await setupAuth(page);
    await mockBaseApis(page);
    await mockEstimateApi(page);
    await page.goto('/goals');

    const textarea = page.locator('textarea[aria-label="Goal text"]');
    await expect(textarea).toBeVisible({ timeout: 15000 });

    await textarea.fill('Fix bug');
    await page.waitForTimeout(300);

    // Widget should not be visible for short text
    await expect(page.getByText('Estimated run')).not.toBeVisible();
  });

  test('widget appears when goal text is >= 10 characters', async ({ page }) => {
    await setupAuth(page);
    await mockBaseApis(page);
    await mockEstimateApi(page);
    await page.goto('/goals');

    const textarea = page.locator('textarea[aria-label="Goal text"]');
    await expect(textarea).toBeVisible({ timeout: 15000 });

    await textarea.fill('Fix all JIRA bugs labelled prod-down');

    // Widget should appear once text is long enough
    await expect(page.getByText('Estimated run')).toBeVisible({ timeout: 10000 });
  });

  test('shows cost, time, and success probability after data loads', async ({ page }) => {
    await setupAuth(page);
    await mockBaseApis(page);
    await mockEstimateApi(page);
    await page.goto('/goals');

    const textarea = page.locator('textarea[aria-label="Goal text"]');
    await expect(textarea).toBeVisible({ timeout: 15000 });
    await textarea.fill('Fix all JIRA bugs labelled prod-down');

    await expect(page.getByText('Estimated run')).toBeVisible({ timeout: 10000 });

    // Cost: $0.008
    await expect(page.getByText('$0.008')).toBeVisible();
    // Time: ~22s
    await expect(page.getByText('~22s')).toBeVisible();
    // Success probability: 87%
    await expect(page.getByText('87%')).toBeVisible();
  });

  test('confidence level is shown', async ({ page }) => {
    await setupAuth(page);
    await mockBaseApis(page);
    await mockEstimateApi(page);
    await page.goto('/goals');

    const textarea = page.locator('textarea[aria-label="Goal text"]');
    await expect(textarea).toBeVisible({ timeout: 15000 });
    await textarea.fill('Fix all JIRA bugs labelled prod-down');

    await expect(page.getByText('Estimated run')).toBeVisible({ timeout: 10000 });

    // Confidence level from MOCK_ESTIMATE
    await expect(page.getByText(/medium confidence/i)).toBeVisible();
  });

  test('shows similar goals count when available', async ({ page }) => {
    await setupAuth(page);
    await mockBaseApis(page);
    await mockEstimateApi(page);
    await page.goto('/goals');

    const textarea = page.locator('textarea[aria-label="Goal text"]');
    await expect(textarea).toBeVisible({ timeout: 15000 });
    await textarea.fill('Fix all JIRA bugs labelled prod-down');

    await expect(page.getByText('Estimated run')).toBeVisible({ timeout: 10000 });

    // "12 similar" from MOCK_ESTIMATE.similar_goals_count
    await expect(page.getByText(/12 similar/i)).toBeVisible();
  });
});
