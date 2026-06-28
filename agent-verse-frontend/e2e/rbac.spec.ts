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

test.describe('RBAC', () => {
  test('shows Access Control h1 heading', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/tenants/me/roles', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.route('**/tenants/me/ip-allowlist', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.goto('/rbac');
    await expect(page.locator('h1').filter({ hasText: /access control/i })).toBeVisible({
      timeout: 15000,
    });
  });

  test('shows empty state when no roles assigned', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/tenants/me/roles', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.route('**/tenants/me/ip-allowlist', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.goto('/rbac');

    await expect(page.getByText('No role assignments')).toBeVisible({ timeout: 15000 });
  });

  test('shows IP allowlist section', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/tenants/me/roles', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.route('**/tenants/me/ip-allowlist', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.goto('/rbac');

    await expect(page.getByText('IP allowlist')).toBeVisible({ timeout: 15000 });
  });

  test('shows empty allowlist state when no IPs configured', async ({ page }) => {
    await setupAuth(page);
    await page.route('**/tenants/me/roles', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.route('**/tenants/me/ip-allowlist', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.goto('/rbac');

    await expect(page.getByText('No allowlist entries')).toBeVisible({ timeout: 15000 });
  });

  test('shows assigned roles in the list', async ({ page }) => {
    const roles = [
      {
        id: 'role-001',
        user_id: 'user-alice',
        role: 'admin',
        created_at: new Date().toISOString(),
      },
    ];
    await setupAuth(page);
    await page.route('**/tenants/me/roles', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(roles),
      })
    );
    await page.route('**/tenants/me/ip-allowlist', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.goto('/rbac');

    await expect(page.getByText('user-alice')).toBeVisible({ timeout: 15000 });
    await expect(page.getByText(/admin/i)).toBeVisible();
  });

  test('can add an IP to the allowlist', async ({ page }) => {
    const newEntry = { id: 'ip-001', cidr: '10.0.0.1/32', description: 'Office IP', created_at: new Date().toISOString() };
    let allowlist: typeof newEntry[] = [];

    await setupAuth(page);
    await page.route('**/tenants/me/roles', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    );
    await page.route(/localhost:8000\/tenants\/me\/ip-allowlist/, async (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(allowlist),
        });
      }
      if (route.request().method() === 'POST') {
        allowlist = [newEntry];
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify(newEntry),
        });
      }
      return route.continue();
    });

    await page.goto('/rbac');
    // Fill in IP CIDR field
    await page.locator('input[placeholder*="CIDR"], input[placeholder*="192.168"], input[placeholder*="10.0"]').fill('10.0.0.1/32');
    await page.getByRole('button', { name: /add|allow/i }).last().click();

    await expect(page.getByText('10.0.0.1/32')).toBeVisible({ timeout: 15000 });
  });
});
