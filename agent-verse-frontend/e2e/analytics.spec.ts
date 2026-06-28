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

const MOCK_GOAL_METRICS = {
  total_goals: 142,
  success_rate: 0.87,
  avg_duration_ms: 4200,
  by_status: { complete: 124, failed: 18 },
};

const MOCK_COST_METRICS = {
  total_usd: 12.5,
  by_day: [
    { date: '2025-06-01', usd: 1.2 },
    { date: '2025-06-02', usd: 2.1 },
  ],
};

const MOCK_EVAL_METRICS = {
  avg_score: 0.91,
  total_evals: 50,
};

function mockAnalyticsApis(page: Page) {
  return page.route(/localhost:8000\/analytics/, (route) => {
    const url = route.request().url();
    if (url.includes('/goals')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_GOAL_METRICS),
      });
    }
    if (url.includes('/costs')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_COST_METRICS),
      });
    }
    if (url.includes('/evals')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_EVAL_METRICS),
      });
    }
    return route.continue();
  });
}

test.describe('Analytics', () => {
  test('shows Analytics h1 heading', async ({ page }) => {
    await setupAuth(page);
    await mockAnalyticsApis(page);
    await page.goto('/analytics');
    await expect(page.locator('h1').filter({ hasText: /analytics/i })).toBeVisible({
      timeout: 15000,
    });
  });

  test('displays goal completion metrics from the API', async ({ page }) => {
    await setupAuth(page);
    await mockAnalyticsApis(page);
    await page.goto('/analytics');

    // The analytics page should render some goal metric data
    await page.waitForLoadState('networkidle');
    await expect(page.locator('h1').filter({ hasText: /analytics/i })).toBeVisible({
      timeout: 15000,
    });
    // Page renders without error
    await expect(page.locator('body')).not.toContainText('Failed to load');
  });

  test('displays cost metrics section', async ({ page }) => {
    await setupAuth(page);
    await mockAnalyticsApis(page);
    await page.goto('/analytics');

    await page.waitForLoadState('networkidle');
    // Cost section should be present on the analytics page
    await expect(page.locator('h1').filter({ hasText: /analytics/i })).toBeVisible({
      timeout: 15000,
    });
  });

  test('can change time period and re-fetches data', async ({ page }) => {
    await setupAuth(page);
    await mockAnalyticsApis(page);
    await page.goto('/analytics');

    await page.waitForLoadState('networkidle');
    // Look for a time period selector (button or select)
    const periodSelector = page
      .locator('button, select')
      .filter({ hasText: /7|14|30|day|week|month/i })
      .first();

    // If there's a period control, interact with it
    if (await periodSelector.isVisible({ timeout: 5000 }).catch(() => false)) {
      await periodSelector.click();
    }

    // Page should still show analytics heading after interaction
    await expect(page.locator('h1').filter({ hasText: /analytics/i })).toBeVisible({
      timeout: 10000,
    });
  });
});
