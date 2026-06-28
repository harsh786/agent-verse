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

test.describe('Knowledge', () => {
  test('shows Knowledge h1 heading', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/knowledge/collections', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.goto('/knowledge');
    await expect(page.locator('h1').filter({ hasText: /knowledge/i })).toBeVisible({
      timeout: 15000,
    });
  });

  test('shows empty state when no collections exist', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/knowledge/collections', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.goto('/knowledge');
    await expect(
      page.getByText('No collections yet. Create one to start ingesting documents.')
    ).toBeVisible({ timeout: 15000 });
  });

  test('"+ New Collection" button toggles the creation form', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/knowledge/collections', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.goto('/knowledge');

    await page.getByRole('button', { name: '+ New Collection' }).click();
    await expect(page.getByText('New Collection')).toBeVisible({ timeout: 10000 });
    await expect(page.locator('input[placeholder="my-knowledge-base"]')).toBeVisible();
  });

  test('can create a new collection and see it in the list', async ({ page }) => {
    const created = {
      collection_id: 'col-001',
      name: 'my-docs',
      doc_count: 0,
      created_at: new Date().toISOString(),
    };
    let collections: typeof created[] = [];

    await setupAuth(page);
    await page.route('**/knowledge/collections', async (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(collections),
        });
      }
      if (route.request().method() === 'POST') {
        collections = [created];
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify(created),
        });
      }
      return route.continue();
    });

    await page.goto('/knowledge');
    await page.getByRole('button', { name: '+ New Collection' }).click();
    await page.locator('input[placeholder="my-knowledge-base"]').fill('my-docs');
    await page.getByRole('button', { name: 'Create' }).click();

    await expect(page.getByText('my-docs')).toBeVisible({ timeout: 15000 });
  });

  test('can delete a knowledge collection', async ({ page }) => {
    const col = {
      collection_id: 'col-del',
      name: 'to-delete',
      doc_count: 5,
      created_at: new Date().toISOString(),
    };
    let collections = [col];

    await setupAuth(page);
    await page.route(/localhost:8000\/knowledge\/collections/, async (route) => {
      const method = route.request().method();
      const url = route.request().url();

      if (method === 'DELETE') {
        collections = [];
        return route.fulfill({ status: 204, body: '' });
      }
      if (method === 'GET' && !url.includes('/col-')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(collections),
        });
      }
      return route.continue();
    });

    await page.goto('/knowledge');
    await expect(page.getByText('to-delete')).toBeVisible({ timeout: 15000 });
    await page.getByRole('button', { name: /delete/i }).first().click();

    await expect(
      page.getByText('No collections yet. Create one to start ingesting documents.')
    ).toBeVisible({ timeout: 10000 });
  });
});
