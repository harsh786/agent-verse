/**
 * E2E tests — Admin Page (/admin)
 *
 * All HTTP calls are intercepted via page.route().
 * Tests:
 *   - Page load: title, metrics row, health badge
 *   - Platform usage metrics displayed
 *   - Tenant table renders rows
 *   - Search filters rows
 *   - Plan filter chips filter rows
 *   - Plan upgrade via dropdown calls API
 *   - System health panel shows service statuses
 *   - Quick action links present
 *   - Refresh button triggers refetch
 *   - Empty state when no tenants
 *   - Degraded health badge when API reports error
 *   - Accessibility: landmark roles, aria-labels
 */

import { test, expect, type Page } from '@playwright/test';

// ── Fixtures ──────────────────────────────────────────────────────────────────

const MOCK_USAGE = {
  active_goals: 42,
  total_tenants: 150,
  goals_today: 1280,
  avg_latency_ms: 340,
};

const MOCK_TENANTS = {
  tenants: [
    { tenant_id: 'acme-corp',     name: 'Acme Corp',        plan: 'enterprise' },
    { tenant_id: 'startup-xyz',   name: 'Startup XYZ',      plan: 'professional' },
    { tenant_id: 'free-user-001', name: undefined,           plan: 'free' },
    { tenant_id: 'small-biz',     name: 'Small Business Co', plan: 'starter' },
  ],
  total: 4,
};

const MOCK_HEALTH = { status: 'ok', db: 'ok', redis: 'ok', celery: 'ok' };

// ── Auth + route setup ────────────────────────────────────────────────────────

async function setupAuth(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('av-auth', JSON.stringify({
      state: { apiKey: 'test-key', tenantId: 'test-tenant', plan: 'enterprise', isAuthenticated: true },
      version: 0,
    }));
    localStorage.setItem('av_api_key', 'test-key');
    sessionStorage.setItem('av_api_key', 'test-key');
  });
  await page.route('**/tenants/me', (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ tenant_id: 'test-tenant', name: 'Test Org', plan: 'enterprise' }),
    }),
  );
}

async function mockAdminApis(
  page: Page,
  opts: { usage?: object; tenants?: object; health?: object; healthStatus?: number } = {},
) {
  await page.route('**/admin/usage', (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify(opts.usage ?? MOCK_USAGE),
    }),
  );
  await page.route(/admin\/tenants/, (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify(opts.tenants ?? MOCK_TENANTS),
    }),
  );
  await page.route('**/health', (route) =>
    route.fulfill({
      status: opts.healthStatus ?? 200, contentType: 'application/json',
      body: JSON.stringify(opts.health ?? MOCK_HEALTH),
    }),
  );
}

async function navigateToAdmin(page: Page) {
  await page.goto('/admin');
  await expect(page.getByTestId('admin-page')).toBeVisible({ timeout: 10_000 });
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test.describe('Admin Page — page load', () => {
  test('shows title and subtitle', async ({ page }) => {
    await setupAuth(page);
    await mockAdminApis(page);
    await navigateToAdmin(page);

    await expect(page.getByText('Platform Administration')).toBeVisible();
    await expect(page.getByText(/Manage tenants/i)).toBeVisible();
  });

  test('shows healthy badge when API is ok', async ({ page }) => {
    await setupAuth(page);
    await mockAdminApis(page);
    await navigateToAdmin(page);

    await expect(page.getByTestId('health-badge')).toContainText('Healthy');
  });

  test('shows degraded badge when health returns error', async ({ page }) => {
    await setupAuth(page);
    await mockAdminApis(page, { health: { status: 'error', db: 'error', redis: 'ok', celery: 'ok' } });
    await navigateToAdmin(page);

    await expect(page.getByTestId('health-badge')).toContainText('Degraded', { timeout: 6_000 });
  });
});

test.describe('Admin Page — metrics', () => {
  test('displays platform usage metrics', async ({ page }) => {
    await setupAuth(page);
    await mockAdminApis(page);
    await navigateToAdmin(page);

    await expect(page.getByTestId('metrics-row')).toBeVisible();
    await expect(page.getByTestId('metric-tenants')).toBeVisible();
    await expect(page.getByTestId('metric-active-goals')).toBeVisible();
  });

  test('shows active goals count from API', async ({ page }) => {
    await setupAuth(page);
    await mockAdminApis(page, { usage: { ...MOCK_USAGE, active_goals: 77 } });
    await navigateToAdmin(page);

    await expect(page.getByTestId('metric-active-goals')).toContainText('77', { timeout: 5_000 });
  });

  test('refresh button triggers usage refetch', async ({ page }) => {
    await setupAuth(page);
    let callCount = 0;
    await page.route('**/admin/usage', (route) => {
      callCount++;
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_USAGE) });
    });
    await page.route(/admin\/tenants/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_TENANTS) }),
    );
    await page.route('**/health', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOCK_HEALTH) }),
    );
    await navigateToAdmin(page);

    const before = callCount;
    await page.getByTestId('refresh-btn').click();
    await page.waitForTimeout(500);
    expect(callCount).toBeGreaterThan(before);
  });
});

test.describe('Admin Page — tenant table', () => {
  test('renders tenant rows', async ({ page }) => {
    await setupAuth(page);
    await mockAdminApis(page);
    await navigateToAdmin(page);

    await expect(page.getByTestId('tenant-table')).toBeVisible({ timeout: 8_000 });
    const rows = page.getByTestId('tenant-row');
    await expect(rows).toHaveCount(4);
  });

  test('enterprise tenants appear first (sorted by plan rank)', async ({ page }) => {
    await setupAuth(page);
    await mockAdminApis(page);
    await navigateToAdmin(page);

    await expect(page.getByTestId('tenant-table')).toBeVisible({ timeout: 8_000 });
    const firstRow = page.getByTestId('tenant-row').first();
    await expect(firstRow).toContainText('acme-corp');
  });

  test('search filters tenant rows', async ({ page }) => {
    await setupAuth(page);
    await mockAdminApis(page);
    await navigateToAdmin(page);

    await expect(page.getByTestId('tenant-table')).toBeVisible({ timeout: 8_000 });
    await page.getByTestId('tenant-search').fill('acme');

    await expect(page.getByTestId('tenant-row')).toHaveCount(1);
    await expect(page.getByTestId('tenant-row')).toContainText('acme-corp');
  });

  test('plan filter chip filters to matching tenants', async ({ page }) => {
    await setupAuth(page);
    await mockAdminApis(page);
    await navigateToAdmin(page);

    await expect(page.getByTestId('tenant-table')).toBeVisible({ timeout: 8_000 });
    await page.getByTestId('plan-filter-enterprise').click();

    await expect(page.getByTestId('tenant-row')).toHaveCount(1);
    await expect(page.getByTestId('tenant-row')).toContainText('acme-corp');
  });

  test('clearing plan filter shows all tenants again', async ({ page }) => {
    await setupAuth(page);
    await mockAdminApis(page);
    await navigateToAdmin(page);

    await expect(page.getByTestId('tenant-table')).toBeVisible({ timeout: 8_000 });
    await page.getByTestId('plan-filter-free').click();
    await expect(page.getByTestId('tenant-row')).toHaveCount(1);
    await page.getByText('Clear ×').click();
    await expect(page.getByTestId('tenant-row')).toHaveCount(4);
  });

  test('empty state when no tenants match search', async ({ page }) => {
    await setupAuth(page);
    await mockAdminApis(page);
    await navigateToAdmin(page);

    await expect(page.getByTestId('tenant-table')).toBeVisible({ timeout: 8_000 });
    await page.getByTestId('tenant-search').fill('xxxxxxxx-nonexistent');
    await expect(page.getByTestId('tenants-empty')).toBeVisible();
  });

  test('empty state when API returns no tenants', async ({ page }) => {
    await setupAuth(page);
    await mockAdminApis(page, { tenants: { tenants: [], total: 0 } });
    await navigateToAdmin(page);

    await expect(page.getByTestId('tenants-empty')).toBeVisible({ timeout: 8_000 });
  });

  test('plan select calls update API on change', async ({ page }) => {
    await setupAuth(page);
    await mockAdminApis(page);
    let updateCalled = false;
    await page.route(/admin\/tenants\/.*\/plan/, (route) => {
      updateCalled = true;
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true }) });
    });
    await navigateToAdmin(page);

    await expect(page.getByTestId('tenant-table')).toBeVisible({ timeout: 8_000 });
    await page.getByTestId('plan-select-free-user-001').selectOption('starter');
    await page.waitForTimeout(500);
    expect(updateCalled).toBe(true);
  });
});

test.describe('Admin Page — system health panel', () => {
  test('shows all four service indicators', async ({ page }) => {
    await setupAuth(page);
    await mockAdminApis(page);
    await navigateToAdmin(page);

    await expect(page.getByTestId('system-health')).toBeVisible();
    await expect(page.getByTestId('health-api')).toBeVisible();
    await expect(page.getByTestId('health-database')).toBeVisible();
    await expect(page.getByTestId('health-redis')).toBeVisible();
    await expect(page.getByTestId('health-workers')).toBeVisible();
  });

  test('all services show ok when health endpoint returns ok', async ({ page }) => {
    await setupAuth(page);
    await mockAdminApis(page, { health: { status: 'ok', db: 'ok', redis: 'ok', celery: 'ok' } });
    await navigateToAdmin(page);

    const apiRow = page.getByTestId('health-api');
    await expect(apiRow).toContainText('ok', { timeout: 6_000 });
  });
});

test.describe('Admin Page — quick action links', () => {
  test('shows audit log link', async ({ page }) => {
    await setupAuth(page);
    await mockAdminApis(page);
    await navigateToAdmin(page);

    await expect(page.getByTestId('quick-link-view-audit-log')).toBeVisible();
  });

  test('shows governance link', async ({ page }) => {
    await setupAuth(page);
    await mockAdminApis(page);
    await navigateToAdmin(page);

    await expect(page.getByTestId('quick-link-governance')).toBeVisible();
    await expect(page.getByTestId('quick-link-governance')).toHaveAttribute('href', '/governance');
  });
});

test.describe('Admin Page — accessibility', () => {
  test('search input has accessible label', async ({ page }) => {
    await setupAuth(page);
    await mockAdminApis(page);
    await navigateToAdmin(page);

    await expect(page.getByLabel('Search tenants')).toBeVisible();
  });

  test('refresh button has aria-label', async ({ page }) => {
    await setupAuth(page);
    await mockAdminApis(page);
    await navigateToAdmin(page);

    await expect(page.getByRole('button', { name: 'Refresh metrics' })).toBeVisible();
  });

  test('plan selects have aria-label for each tenant', async ({ page }) => {
    await setupAuth(page);
    await mockAdminApis(page);
    await navigateToAdmin(page);

    await expect(page.getByTestId('tenant-table')).toBeVisible({ timeout: 8_000 });
    await expect(page.getByLabel('Change plan for acme-corp')).toBeVisible();
  });
});
