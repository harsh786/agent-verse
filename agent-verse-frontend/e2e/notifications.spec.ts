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

  // Mock SSE endpoint so it doesn't hang
  await page.route('**/governance/approvals/stream', (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
  );
  await page.route('**/governance/approvals', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
  );
}

test('create and delete a notification channel', async ({ page }) => {
  await setupAuth(page);
  let channels: Array<{ channel_id: string; type: string; enabled: boolean }> = [];
  await page.route('**/governance/notifications', async (route) => {
    if (route.request().method() === 'POST') {
      channels = [{ channel_id: 'c1', type: 'webhook', enabled: true }];
      await route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ channel_id: 'c1', type: 'webhook', status: 'created' }) });
    } else {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(channels) });
    }
  });
  await page.goto('/notifications');
  await page.getByRole('button', { name: /add channel/i }).click();
  await expect(page.getByText('webhook')).toBeVisible();
});

test('shows error toast on server failure', async ({ page }) => {
  await setupAuth(page);
  await page.route('**/governance/notifications', async (route) => {
    if (route.request().method() === 'POST')
      await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: { message: 'boom' } }) });
    else await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
  });
  await page.goto('/notifications');
  await page.getByRole('button', { name: /add channel/i }).click();
  await expect(page.getByRole('status')).toContainText(/boom|error/i);
});
