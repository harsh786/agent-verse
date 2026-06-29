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

test.describe('Workflow Builder', () => {
  test('shows workflow builder page with toolbar controls', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/workflows/, (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([]),
        });
      }
      return route.continue();
    });
    await page.goto('/workflow-builder');

    // The page should render with save and run buttons
    await expect(page.getByRole('button', { name: /save/i })).toBeVisible({ timeout: 30000 });
    await expect(page.getByRole('button', { name: /run/i })).toBeVisible({ timeout: 30000 });
  });

  test('shows workflow name input', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/workflows/, (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([]),
        });
      }
      return route.continue();
    });
    await page.goto('/workflow-builder');

    // The workflow name input should default to "My Workflow"
    await expect(page.locator('input').filter({ hasValue: /my workflow/i })).toBeVisible({
      timeout: 15000,
    });
  });

  test('can save a workflow', async ({ page }) => {
    const saved = {
      id: 'wf-001',
      name: 'My Workflow',
      nodes: [],
      edges: [],
      created_at: new Date().toISOString(),
    };
    let savedWorkflow: typeof saved | null = null;

    await setupAuth(page);
    await page.route(/localhost:8000\/workflows/, async (route) => {
      const method = route.request().method();
      if (method === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(savedWorkflow ? [savedWorkflow] : []),
        });
      }
      if (method === 'POST') {
        savedWorkflow = saved;
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify(saved),
        });
      }
      if (method === 'PUT') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(saved),
        });
      }
      return route.continue();
    });

    await page.goto('/workflow-builder');
    await page.getByRole('button', { name: /save/i }).click();

    // Should not crash or show error
    await expect(page.getByRole('button', { name: /save/i })).toBeVisible({ timeout: 10000 });
  });

  test('can load a saved workflow', async ({ page }) => {
    const workflows = [
      {
        id: 'wf-saved',
        name: 'Deploy Pipeline',
        nodes: [],
        edges: [],
        created_at: new Date().toISOString(),
      },
    ];

    await setupAuth(page);
    await page.route(/localhost:8000\/workflows/, (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(workflows),
        });
      }
      return route.continue();
    });

    await page.goto('/workflow-builder');
    // Saved workflow name should be listed
    await expect(page.getByText('Deploy Pipeline')).toBeVisible({ timeout: 15000 });
  });

  test('Dry Run button is visible and enabled', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/workflows/, (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([]),
        });
      }
      return route.continue();
    });
    await page.route('**/goals', (route) => {
      if (route.request().method() === 'POST') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ steps: ['Step 1', 'Step 2'], plan: { steps: ['Step 1', 'Step 2'] } }),
        });
      }
      return route.continue();
    });

    await page.goto('/workflow-builder');
    await expect(page.getByRole('button', { name: /dry run/i })).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: /dry run/i })).toBeEnabled();
  });
});
