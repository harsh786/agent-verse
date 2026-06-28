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

test.describe('Approvals', () => {
  test('shows Approval Inbox heading', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/governance/approvals', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([]),
        });
      }
      return route.continue();
    });
    await page.route('**/governance/approvals/stream', (route) =>
      route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
    );
    await page.goto('/approvals');
    await expect(page.getByText('Approval Inbox')).toBeVisible({ timeout: 15000 });
  });

  test('shows page subtitle', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/governance/approvals', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([]),
        });
      }
      return route.continue();
    });
    await page.route('**/governance/approvals/stream', (route) =>
      route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
    );
    await page.goto('/approvals');
    await expect(
      page.getByText('Human-in-the-loop requests awaiting your decision')
    ).toBeVisible({ timeout: 15000 });
  });

  test('shows empty state when no pending approvals', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/governance/approvals', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([]),
        });
      }
      return route.continue();
    });
    await page.route('**/governance/approvals/stream', (route) =>
      route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
    );
    await page.goto('/approvals');

    await expect(page.getByText('All clear')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText('No pending approval requests.')).toBeVisible();
  });

  test('shows Approve and Reject buttons for pending approval', async ({ page }) => {
    const approvals = [
      {
        request_id: 'req-001',
        goal_id: 'goal-001',
        action: 'Deploy to production environment',
        status: 'pending',
        created_at: new Date().toISOString(),
      },
    ];

    await setupAuth(page);
    await page.route('**/governance/approvals', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(approvals),
        });
      }
      return route.continue();
    });
    await page.route('**/governance/approvals/stream', (route) =>
      route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
    );
    await page.goto('/approvals');

    await expect(page.getByText('Deploy to production environment')).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: /approve/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /reject/i })).toBeVisible();
  });

  test('can approve a request', async ({ page }) => {
    let approved = false;
    const approvals = [
      {
        request_id: 'req-approve',
        goal_id: 'goal-002',
        action: 'Scale up database replicas',
        status: 'pending',
        created_at: new Date().toISOString(),
      },
    ];

    await setupAuth(page);
    await page.route(/localhost:8000\/governance\/approvals/, async (route) => {
      const method = route.request().method();
      const url = route.request().url();
      if (method === 'GET' && !url.includes('/stream')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(approved ? [] : approvals),
        });
      }
      if (method === 'POST' && url.includes('/approve')) {
        approved = true;
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ status: 'approved' }),
        });
      }
      if (url.includes('/stream')) {
        return route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' });
      }
      return route.continue();
    });

    await page.goto('/approvals');
    await expect(page.getByText('Scale up database replicas')).toBeVisible({ timeout: 15000 });
    await page.getByRole('button', { name: /approve/i }).click();

    expect(approved).toBe(true);
  });

  test('can reject a request', async ({ page }) => {
    let rejected = false;
    const approvals = [
      {
        request_id: 'req-reject',
        goal_id: 'goal-003',
        action: 'Delete all backups',
        status: 'pending',
        created_at: new Date().toISOString(),
      },
    ];

    await setupAuth(page);
    await page.route(/localhost:8000\/governance\/approvals/, async (route) => {
      const method = route.request().method();
      const url = route.request().url();
      if (method === 'GET' && !url.includes('/stream')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(rejected ? [] : approvals),
        });
      }
      if (method === 'POST' && url.includes('/reject')) {
        rejected = true;
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ status: 'rejected' }),
        });
      }
      if (url.includes('/stream')) {
        return route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' });
      }
      return route.continue();
    });

    await page.goto('/approvals');
    await expect(page.getByText('Delete all backups')).toBeVisible({ timeout: 15000 });
    await page.getByRole('button', { name: /reject/i }).click();

    expect(rejected).toBe(true);
  });
});
