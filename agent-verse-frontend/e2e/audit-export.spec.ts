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

  // Mock SSE endpoints so they don't hang
  await page.route('**/governance/approvals/stream', (route) =>
    route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
  );
  await page.route('**/governance/approvals', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
  );
}

test('audit explorer renders rows and exposes export buttons', async ({ page }) => {
  await setupAuth(page);
  await page.route('**/governance/audit*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([{ event_id: 'e1', goal_id: 'g1', tool_name: 'jira.delete', action_level: 'deny', outcome: 'denied' }]),
    });
  });
  await page.goto('/audit');
  await expect(page.getByText('jira.delete')).toBeVisible();
  await expect(page.getByRole('button', { name: /export csv/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /export json/i })).toBeVisible();
});
