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

test.describe('Memory Explorer', () => {
  test('shows Memory Explorer h1 heading', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/memory/, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      })
    );
    await page.goto('/memory');
    await expect(page.locator('h1').filter({ hasText: /memory explorer/i })).toBeVisible({
      timeout: 15000,
    });
  });

  test('shows memory list with scores', async ({ page }) => {
    const memories = [
      {
        id: 'mem-001',
        content: 'Use async functions for all API calls',
        memory_type: 'long_term',
        score: 0.92,
        created_at: new Date().toISOString(),
      },
      {
        id: 'mem-002',
        content: 'Prefer TypeScript over JavaScript',
        memory_type: 'long_term',
        score: 0.87,
        created_at: new Date().toISOString(),
      },
    ];

    await setupAuth(page);
    await page.route(/localhost:8000\/memory/, (route) => {
      const url = route.request().url();
      if (!url.includes('/recall') && !url.includes('/tool-reliability')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(memories),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
    });

    await page.goto('/memory');
    await expect(page.getByText('Use async functions for all API calls')).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByText('Prefer TypeScript over JavaScript')).toBeVisible();
  });

  test('recall search shows results', async ({ page }) => {
    const recallResults = [
      {
        id: 'mem-recall-1',
        content: 'Always validate user input before processing',
        memory_type: 'long_term',
        score: 0.95,
        created_at: new Date().toISOString(),
      },
    ];

    await setupAuth(page);
    await page.route(/localhost:8000\/memory/, async (route) => {
      const url = route.request().url();
      if (url.includes('/recall')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(recallResults),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
    });

    await page.goto('/memory');
    await page
      .locator('input[placeholder="Recall memories relevant to…"]')
      .fill('validation');
    await page.keyboard.press('Enter');

    await expect(page.getByText('Always validate user input before processing')).toBeVisible({
      timeout: 15000,
    });
  });

  test('can delete a memory entry', async ({ page }) => {
    const mem = {
      id: 'mem-del',
      content: 'Memory to delete',
      memory_type: 'long_term',
      score: 0.5,
      created_at: new Date().toISOString(),
    };
    let memories = [mem];

    await setupAuth(page);
    await page.route(/localhost:8000\/memory/, async (route) => {
      const method = route.request().method();
      const url = route.request().url();
      if (method === 'DELETE') {
        memories = [];
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ deleted: 'mem-del', status: 'ok' }) });
      }
      if (method === 'GET' && !url.includes('/recall') && !url.includes('/tool-reliability')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(memories),
        });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
    });

    await page.goto('/memory');
    await expect(page.getByText('Memory to delete')).toBeVisible({ timeout: 15000 });
    await page.getByRole('button', { name: /delete/i }).first().click();

    await expect(page.getByText('Memory to delete')).not.toBeVisible({ timeout: 10000 });
  });
});
