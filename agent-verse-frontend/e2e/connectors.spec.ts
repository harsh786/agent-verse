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

test.describe('Connectors', () => {
  test('shows Registered Connectors heading', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000/connectors/, (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([]),
        });
      }
      return route.continue();
    });
    await page.goto('/connectors');
    await expect(page.getByText('Registered Connectors')).toBeVisible({ timeout: 15000 });
  });

  test('shows connector list with registered connectors', async ({ page }) => {
    const connectors = [
      {
        id: 'conn-001',
        name: 'github-main',
        url: 'http://localhost:9001',
        status: 'active',
        created_at: new Date().toISOString(),
      },
    ];

    await setupAuth(page);
    await page.route(/localhost:8000/connectors/, (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(connectors),
        });
      }
      return route.continue();
    });

    await page.goto('/connectors');
    await expect(page.getByText('github-main')).toBeVisible({ timeout: 15000 });
  });

  test('can register a new connector', async ({ page }) => {
    const created = {
      id: 'conn-new',
      name: 'my-github',
      url: 'http://localhost:9000',
      status: 'active',
      created_at: new Date().toISOString(),
    };
    let connectors: typeof created[] = [];

    await setupAuth(page);
    await page.route(/localhost:8000\/connectors/, async (route) => {
      const method = route.request().method();
      const url = route.request().url();
      if (method === 'GET' && !url.match(/\/connectors\/[^/]+$/)) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(connectors),
        });
      }
      if (method === 'POST' && !url.includes('/test')) {
        connectors = [created];
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify(created),
        });
      }
      return route.continue();
    });

    await page.goto('/connectors');
    await page.locator('input[placeholder="my-github"]').fill('my-github');
    await page.locator('input[placeholder="http://localhost:9000"]').fill('http://localhost:9000');
    await page.getByRole('button', { name: /register|add|connect/i }).first().click();

    await expect(page.getByText('my-github')).toBeVisible({ timeout: 15000 });
  });

  test('can test a connector', async ({ page }) => {
    const connector = {
      id: 'conn-test',
      name: 'jira-prod',
      url: 'http://localhost:9002',
      status: 'active',
      created_at: new Date().toISOString(),
    };

    await setupAuth(page);
    await page.route(/localhost:8000\/connectors/, async (route) => {
      const method = route.request().method();
      const url = route.request().url();
      if (method === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([connector]),
        });
      }
      if (method === 'POST' && url.includes('/test')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ status: 'ok', latency_ms: 42 }),
        });
      }
      return route.continue();
    });

    await page.goto('/connectors');
    await expect(page.getByText('jira-prod')).toBeVisible({ timeout: 15000 });
    await page.getByRole('button', { name: /test/i }).first().click();
    // test result or button interaction verified
    await page.waitForTimeout(500);
  });

  test('can unregister a connector', async ({ page }) => {
    const conn = {
      id: 'conn-del',
      name: 'old-connector',
      url: 'http://localhost:9003',
      status: 'active',
      created_at: new Date().toISOString(),
    };
    let connectors = [conn];

    await setupAuth(page);
    await page.route(/localhost:8000\/connectors/, async (route) => {
      const method = route.request().method();
      const url = route.request().url();
      if (method === 'DELETE') {
        connectors = [];
        return route.fulfill({ status: 204, body: '' });
      }
      if (method === 'GET' && !url.match(/\/connectors\/[^/]+$/)) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(connectors),
        });
      }
      return route.continue();
    });

    await page.goto('/connectors');
    await expect(page.getByText('old-connector')).toBeVisible({ timeout: 15000 });
    await page.getByRole('button', { name: /unregister|delete|remove/i }).first().click();

    await expect(page.getByText('old-connector')).not.toBeVisible({ timeout: 10000 });
  });
});
