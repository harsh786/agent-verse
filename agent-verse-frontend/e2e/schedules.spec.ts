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

test.describe('Schedules', () => {
  test('shows Schedules h1 heading', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/schedules/, (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([]),
        });
      }
      return route.continue();
    });
    await page.goto('/schedules');
    await expect(page.locator('h1').filter({ hasText: /schedules/i })).toBeVisible({
      timeout: 15000,
    });
  });

  test('shows empty state when no schedules exist', async ({ page }) => {
    await setupAuth(page);
    await page.route(/localhost:8000\/schedules/, (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([]),
        });
      }
      return route.continue();
    });
    await page.goto('/schedules');
    await expect(
      page.getByText('No schedules yet. Create one to automate your agents.')
    ).toBeVisible({ timeout: 15000 });
  });

  test('can create a schedule with a cron expression', async ({ page }) => {
    const created = {
      schedule_id: 'sched-001',
      goal_template: 'Check all open PRs',
      trigger_type: 'cron',
      cron_expression: '0 * * * *',
      is_active: true,
      created_at: new Date().toISOString(),
    };
    let schedules: typeof created[] = [];

    await setupAuth(page);
    await page.route(/localhost:8000\/schedules/, async (route) => {
      const method = route.request().method();
      if (method === 'GET' && !route.request().url().includes('/sched-')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(schedules),
        });
      }
      if (method === 'POST') {
        schedules = [created];
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify(created),
        });
      }
      return route.continue();
    });

    await page.goto('/schedules');
    await page.getByRole('button', { name: /new schedule/i }).click();
    await page
      .locator('input[placeholder="Check all open PRs and post a summary"], textarea[placeholder="Check all open PRs and post a summary"]')
      .fill('Check all open PRs');
    await page.locator('input[placeholder="0 * * * *"]').fill('0 * * * *');
    await page.getByRole('button', { name: /create|save/i }).click();

    await expect(page.getByText('Check all open PRs')).toBeVisible({ timeout: 15000 });
  });

  test('shows schedule list with existing schedules', async ({ page }) => {
    const schedules = [
      {
        schedule_id: 'sched-abc',
        goal_template: 'Daily standup summary',
        trigger_type: 'cron',
        cron_expression: '0 9 * * *',
        is_active: true,
        created_at: new Date().toISOString(),
      },
    ];

    await setupAuth(page);
    await page.route(/localhost:8000\/schedules/, (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(schedules),
        });
      }
      return route.continue();
    });

    await page.goto('/schedules');
    await expect(page.getByText('Daily standup summary')).toBeVisible({ timeout: 15000 });
  });

  test('can delete a schedule', async ({ page }) => {
    const s = {
      schedule_id: 'sched-del',
      goal_template: 'Delete me',
      trigger_type: 'cron',
      cron_expression: '* * * * *',
      is_active: true,
      created_at: new Date().toISOString(),
    };
    let schedules = [s];

    await setupAuth(page);
    await page.route(/localhost:8000\/schedules/, async (route) => {
      const method = route.request().method();
      const url = route.request().url();
      if (method === 'DELETE') {
        schedules = [];
        return route.fulfill({ status: 204, body: '' });
      }
      if (method === 'GET' && !url.includes('/sched-del')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(schedules),
        });
      }
      return route.continue();
    });

    await page.goto('/schedules');
    await expect(page.getByText('Delete me')).toBeVisible({ timeout: 15000 });
    await page.getByRole('button', { name: /delete/i }).first().click();

    await expect(
      page.getByText('No schedules yet. Create one to automate your agents.')
    ).toBeVisible({ timeout: 10000 });
  });
});
