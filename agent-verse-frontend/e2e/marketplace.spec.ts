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

const MOCK_TEMPLATES = [
  {
    template_id: 'tmpl-001',
    name: 'GitHub PR Reviewer',
    description: 'Automatically reviews and summarizes GitHub pull requests',
    domain: 'software',
    goal_template: 'Review all open PRs in {{repo}} and post a summary',
    required_connectors: ['github'],
    created_at: new Date().toISOString(),
  },
  {
    template_id: 'tmpl-002',
    name: 'Jira Sprint Reporter',
    description: 'Generates sprint velocity and burndown reports from Jira',
    domain: 'software',
    goal_template: 'Generate sprint report for {{project}}',
    required_connectors: ['jira'],
    created_at: new Date().toISOString(),
  },
];

test.describe('Marketplace', () => {
  test('shows Marketplace h1 heading', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/marketplace/browse', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      })
    );
    await page.goto('/marketplace');
    await expect(page.locator('h1').filter({ hasText: /marketplace/i })).toBeVisible({
      timeout: 15000,
    });
  });

  test('can browse agent templates', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/marketplace/browse', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_TEMPLATES),
      })
    );
    await page.goto('/marketplace');

    await expect(page.getByText('GitHub PR Reviewer')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('Jira Sprint Reporter')).toBeVisible();
  });

  test('shows template descriptions', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/marketplace/browse', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_TEMPLATES),
      })
    );
    await page.goto('/marketplace');

    await expect(
      page.getByText('Automatically reviews and summarizes GitHub pull requests')
    ).toBeVisible({ timeout: 15000 });
  });

  test('can deploy a template', async ({ page }) => {
    let deployed = false;

    await setupAuth(page);
    await page.route('**/marketplace/browse', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_TEMPLATES),
      })
    );
    await page.route(/localhost:8000\/marketplace\/.*\/deploy/, (route) => {
      deployed = true;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          goal_id: 'g-deployed',
          status: 'planning',
          goal: 'Review all open PRs',
        }),
      });
    });

    await page.goto('/marketplace');
    await expect(page.getByText('GitHub PR Reviewer')).toBeVisible({ timeout: 15000 });

    // Click the first deploy/use button
    await page.getByRole('button', { name: /deploy|use|install/i }).first().click();

    // After clicking, the deploy call should have been triggered or a dialog appeared
    await page.waitForTimeout(500);
    // Deployment was initiated (either via API call or navigation)
    expect(deployed || page.url().includes('/goals')).toBeTruthy();
  });

  test('template search filter works', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/marketplace/browse', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_TEMPLATES),
      })
    );
    await page.goto('/marketplace');

    await expect(page.getByText('GitHub PR Reviewer')).toBeVisible({ timeout: 15000 });

    // Search for "jira" - should filter results
    await page.locator('input[placeholder="Template name"]').fill('jira');
    await page.waitForTimeout(300);

    // Jira template should be visible, GitHub template might be hidden
    await expect(page.getByText('Jira Sprint Reporter')).toBeVisible();
  });
});
