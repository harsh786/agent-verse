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

const MOCK_SIMULATION_RESULT = {
  goal: 'Refund all failed payments from last week',
  summary: {
    total_steps: 3,
    estimated_cost_usd: 0.05,
    risk_level: 'low',
  },
  policy_checks: [
    { tool: 'payments:list', result: 'allowed' },
    { tool: 'payments:refund', result: 'requires_approval' },
  ],
  plan: {
    steps: [
      'Fetch failed payments from last 7 days',
      'Validate refund eligibility for each payment',
      'Process refunds in batch',
    ],
  },
};

test.describe('Simulation', () => {
  test('shows Simulation Studio h1 heading', async ({ page }) => {
    await setupAuth(page);
    await page.goto('/simulation');
    await expect(page.locator('h1').filter({ hasText: /simulation studio/i })).toBeVisible({
      timeout: 15000,
    });
  });

  test('shows Run Simulation button', async ({ page }) => {
    await setupAuth(page);
    await page.goto('/simulation');
    await expect(page.getByRole('button', { name: /run simulation/i })).toBeVisible({
      timeout: 15000,
    });
  });

  test('shows goal input placeholder', async ({ page }) => {
    await setupAuth(page);
    await page.goto('/simulation');
    await expect(
      page.locator('textarea[placeholder*="Refund all failed payments"]')
    ).toBeVisible({ timeout: 15000 });
  });

  test('can run a goal simulation and see policy check results', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/governance/simulate', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          summary: MOCK_SIMULATION_RESULT.summary,
          policy_checks: MOCK_SIMULATION_RESULT.policy_checks,
        }),
      })
    );
    await page.route('**/goals', (route) => {
      if (route.request().method() === 'POST') {
        return route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({ steps: MOCK_SIMULATION_RESULT.plan.steps }),
        });
      }
      return route.continue();
    });

    await page.goto('/simulation');
    await page
      .locator('textarea[placeholder*="Refund all failed payments"]')
      .fill('Refund all failed payments from last week');
    await page.getByRole('button', { name: /run simulation/i }).click();

    await expect(page.getByText('Governance Policy Check')).toBeVisible({ timeout: 15000 });
  });

  test('shows policy check results after simulation', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/governance/simulate', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          summary: MOCK_SIMULATION_RESULT.summary,
          policy_checks: MOCK_SIMULATION_RESULT.policy_checks,
        }),
      })
    );
    await page.route('**/goals', (route) => {
      if (route.request().method() === 'POST') {
        return route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({ steps: MOCK_SIMULATION_RESULT.plan.steps }),
        });
      }
      return route.continue();
    });

    await page.goto('/simulation');
    await page
      .locator('textarea[placeholder*="Refund all failed payments"]')
      .fill('Refund failed payments');
    await page.getByRole('button', { name: /run simulation/i }).click();

    await expect(page.getByText('payments:list')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('allowed')).toBeVisible();
  });

  test('shows planned execution steps after simulation', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/governance/simulate', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          summary: MOCK_SIMULATION_RESULT.summary,
          policy_checks: [],
        }),
      })
    );
    await page.route('**/goals', (route) => {
      if (route.request().method() === 'POST') {
        return route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({ plan: { steps: MOCK_SIMULATION_RESULT.plan.steps } }),
        });
      }
      return route.continue();
    });

    await page.goto('/simulation');
    await page
      .locator('textarea[placeholder*="Refund all failed payments"]')
      .fill('Refund failed payments and notify merchants');
    await page.getByRole('button', { name: /run simulation/i }).click();

    await expect(page.getByText('Planned Execution Steps')).toBeVisible({ timeout: 15000 });
  });
});
